#!/usr/bin/env python3
"""Run a single coil-optimization experiment and record it.

The harness core is solver-agnostic: it owns the experiment database, the
scratch/artifact lifecycle, and the agent-facing CLI skeleton. The active
solver adapter (see adapter.py / contract.py) owns everything solver-specific —
which flags exist, how to invoke the solver, what its outputs mean.

Usage (flags come from the active adapter; this shows the banana adapter):
    # Stage 2 (default mode)
    python run.py --equilibrium nfp5_iota15 --cc-weight 44

    # Single-stage
    python run.py --solver single-stage --equilibrium nfp5_iota20 \
        --iota-target 0.20 --vol-target 0.10 --mpol 8

Writes to both results.jsonl (append-only) and results.db (queryable), and
prints a single-line JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import adapter
from contract import ExperimentOutcome, clean

REPO_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = REPO_ROOT / "schema.sql"
DB_PATH = REPO_ROOT / "results.db"
JSONL_PATH = REPO_ROOT / "results.jsonl"

# Canonical metric keys with a dedicated DB column (must match schema.sql and
# the INSERT in _insert_db). Any other key an adapter emits is preserved in the
# row's `metrics` JSON blob.
COLUMN_METRIC_KEYS = (
    "iterations",
    "optimizer_success",
    "termination_message",
    "field_error",
    "qs_error",
    "boozer_residual",
    "iota_actual",
    "volume_actual",
    "max_curvature",
    "coil_length",
    "coil_coil_dist",
    "coil_surface_dist",
    "surface_vessel_dist",
    "max_force",
    "self_intersecting",
    "objective_J",
)

# --- Artifact & directory layout (optional environment overrides) ---
# OUTPUT_BASE: scratch dir for live solver runs. Crashed runs always leave their
#   dir + run.log here for debugging.
# KEEP_ARTIFACTS: what to do with a completed run's output dir after ingest —
#   "none" discards it, "pass" keeps passing runs, "all" keeps every completed
#   run. Kept dirs are moved to ARTIFACTS_DIR/<run-id>.
OUTPUT_BASE = Path(os.environ.get("OUTPUT_BASE", "/tmp/stellarator_harness"))
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", str(REPO_ROOT / "artifacts")))
KEEP_ARTIFACTS = os.environ.get("KEEP_ARTIFACTS", "none")
if KEEP_ARTIFACTS not in ("none", "pass", "all"):
    print(
        f"WARNING: unknown KEEP_ARTIFACTS '{KEEP_ARTIFACTS}', using 'none'",
        file=sys.stderr,
    )
    KEEP_ARTIFACTS = "none"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid7() -> str:
    ts_ms = int(time.time() * 1000)
    rand_bits = uuid.uuid4().int & ((1 << 74) - 1)
    u = (ts_ms << 80) | (0x7 << 76) | rand_bits
    u = (u & ~(0x3 << 62)) | (0x2 << 62)
    return str(uuid.UUID(int=u))


def _bool_to_int(v: object) -> int | None:
    if v is True:
        return 1
    if v is False:
        return 0
    return None


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------

def _ensure_db() -> sqlite3.Connection:
    """Open DB, create schema if needed. Safe for concurrent first-run."""
    db = sqlite3.connect(str(DB_PATH))
    db.executescript(SCHEMA_PATH.read_text())  # IF NOT EXISTS + PRAGMA WAL in schema.sql
    return db


def _insert_db(db: sqlite3.Connection, record: dict) -> None:
    """Insert one run record into the DB."""
    db.execute(
        """INSERT INTO runs (
            id, coil_type, solver, equilibrium, experiment_group,
            status, status_reason, validated,
            iterations, elapsed, created_at,
            optimizer_success, termination_message,
            field_error, qs_error, boozer_residual,
            iota_actual, volume_actual,
            max_curvature,
            coil_length, coil_coil_dist, coil_surface_dist, surface_vessel_dist,
            max_force, self_intersecting, objective_J,
            metrics, params
        ) VALUES (
            :id, :coil_type, :solver, :equilibrium, :experiment_group,
            :status, :status_reason, :validated,
            :iterations, :elapsed, :created_at,
            :optimizer_success, :termination_message,
            :field_error, :qs_error, :boozer_residual,
            :iota_actual, :volume_actual,
            :max_curvature,
            :coil_length, :coil_coil_dist, :coil_surface_dist, :surface_vessel_dist,
            :max_force, :self_intersecting, :objective_J,
            :metrics, :params
        )""",
        {
            "id": record["id"],
            "coil_type": record["coil_type"],
            "solver": record["solver"],
            "equilibrium": record["equilibrium"],
            "experiment_group": record.get("experiment_group"),
            "status": record["status"],
            "status_reason": record.get("status_reason"),
            "validated": record.get("validated"),
            "iterations": record.get("iterations"),
            "elapsed": record.get("elapsed"),
            "created_at": record.get("created_at"),
            "optimizer_success": _bool_to_int(record.get("optimizer_success")),
            "termination_message": record.get("termination_message"),
            "field_error": record.get("field_error"),
            "qs_error": record.get("qs_error"),
            "boozer_residual": record.get("boozer_residual"),
            "iota_actual": record.get("iota_actual"),
            "volume_actual": record.get("volume_actual"),
            "max_curvature": record.get("max_curvature"),
            "coil_length": record.get("coil_length"),
            "coil_coil_dist": record.get("coil_coil_dist"),
            "coil_surface_dist": record.get("coil_surface_dist"),
            "surface_vessel_dist": record.get("surface_vessel_dist"),
            "max_force": record.get("max_force"),
            "self_intersecting": _bool_to_int(record.get("self_intersecting")),
            "objective_J": record.get("objective_J"),
            "metrics": json.dumps(record.get("metrics", {})),
            "params": json.dumps(record.get("params", {})),
        },
    )
    db.commit()


def _append_jsonl(record: dict) -> None:
    """Append one JSON record to results.jsonl with file locking."""
    line = json.dumps(record) + "\n"
    with open(JSONL_PATH, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
        f.flush()


def _emit_result(record: dict) -> None:
    """Write to JSONL + DB + stdout. Persistence failures don't block stdout."""
    try:
        _append_jsonl(record)
    except Exception as e:
        print(f"WARNING: JSONL write failed: {e}", file=sys.stderr)
    try:
        with contextlib.closing(_ensure_db()) as db:
            _insert_db(db, record)
    except Exception as e:
        print(f"WARNING: DB write failed: {e}", file=sys.stderr)
    print(json.dumps(record))


# ---------------------------------------------------------------------------
# Record construction (single place where NaN cleaning happens)
# ---------------------------------------------------------------------------

def _build_record(args: argparse.Namespace, outcome: ExperimentOutcome, elapsed: float) -> dict:
    """Assemble a result record from an adapter outcome. NaN cleaning happens here.

    Canonical metric keys with a column are projected to top-level fields; every
    other emitted key is preserved (cleaned) in `metrics` for JSON storage.
    """
    metrics = outcome.metrics
    record = {
        "id": _uuid7(),
        "coil_type": adapter.NAME,
        "solver": args.solver,
        "equilibrium": args.equilibrium,  # contract requires adapters to register --equilibrium
        "experiment_group": outcome.experiment_group,
        "status": outcome.status,
        "status_reason": outcome.status_reason,
        "validated": outcome.validated,
        "elapsed": round(elapsed, 1),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "params": dict(outcome.params),
    }
    for key in COLUMN_METRIC_KEYS:
        record[key] = clean(metrics.get(key))
    record["metrics"] = {
        k: clean(v) for k, v in metrics.items() if k not in COLUMN_METRIC_KEYS
    }
    return record


# ---------------------------------------------------------------------------
# Core experiment
# ---------------------------------------------------------------------------

def _run_experiment(args: argparse.Namespace) -> None:
    """Run one experiment via the active adapter and record the outcome."""
    run_dir = OUTPUT_BASE / f"run_{int(time.time() * 1000)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    try:
        outcome = adapter.run_experiment(args, run_dir)
    except Exception as e:
        # Truly-unexpected adapter failure (the adapter never returned an
        # outcome). Record the full parsed CLI as params so the experiment
        # stays reproducible despite the missing adapter-curated params.
        print(f"WARNING: adapter raised: {e}", file=sys.stderr)
        outcome = ExperimentOutcome("crash", f"adapter_error: {e}", params=vars(args))
    # Total experiment wall time: includes any validation (e.g. Poincaré) or
    # chained sub-steps the adapter runs internally, not just one solver call.
    elapsed = time.monotonic() - t0

    record = _build_record(args, outcome, elapsed)
    _emit_result(record)
    _finalize_run_dir(run_dir, outcome.status, record["id"])


def _finalize_run_dir(run_dir: Path, status: str, run_id: str) -> None:
    """Apply KEEP_ARTIFACTS: move the run dir to ARTIFACTS_DIR/<run-id> or discard it."""
    keep = KEEP_ARTIFACTS == "all" or (KEEP_ARTIFACTS == "pass" and status == "pass")
    if not keep:
        shutil.rmtree(run_dir, ignore_errors=True)
        return
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARTIFACTS_DIR / run_id
    shutil.move(str(run_dir), str(dest))
    print(f"Artifacts kept: {dest}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Run one coil-optimization experiment")
    p.add_argument(
        "--solver",
        choices=list(adapter.SOLVER_MODES),
        default=adapter.SOLVER_MODES[0],
        help="solver mode exposed by the active adapter",
    )
    adapter.add_arguments(p)

    args = p.parse_args()
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    _run_experiment(args)


if __name__ == "__main__":
    main()
