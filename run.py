#!/usr/bin/env python3
"""Run a single stellarator coil optimization experiment.

Usage:
    # Stage 2 (default)
    python run.py --cc-weight 44 --curvature-weight 0.00085

    # Single-stage
    python run.py --solver single-stage --equilibrium nfp5_iota20 \
        --iota-target 0.20 --vol-target 0.10 --mpol 8

Writes to both results.jsonl (append-only) and results.db (queryable).
Prints a single-line JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = REPO_ROOT / "schema.sql"
DB_PATH = REPO_ROOT / "results.db"
JSONL_PATH = REPO_ROOT / "results.jsonl"
OUTPUT_BASE = Path("/tmp/stellarator_harness")

# ---------------------------------------------------------------------------
# Configuration — edit these paths for your environment
# ---------------------------------------------------------------------------

EQUILIBRIA_DIR = Path(
    os.environ.get("EQUILIBRIA_DIR", "/Users/suhjungdae/code/columbia/DATABASE/EQUILIBRIA")
)
STAGE2_SEED_STORE = REPO_ROOT / "stage2_seeds"
POINCARE_FIELD_ERROR_THRESHOLD = 0.1
POINCARE_SURVIVAL_THRESHOLD = 0.9

SOLVERS = {
    "banana": {
        "stage2": "examples/single_stage_optimization/STAGE_2/banana_coil_solver.py",
        "single-stage": "examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py",
        "default_root": Path(os.environ.get(
            "SIMSOPT_ROOT", "/Users/suhjungdae/code/hbt-compare/wt/candidate-fixed"
        )),
        "default_python": os.environ.get(
            "SIMSOPT_PYTHON",
            "/opt/homebrew/Caskroom/miniforge/base/envs/columbia-jax-0.9.2/bin/python",
        ),
    },
}

# ---------------------------------------------------------------------------
# Equilibrium registry: nfp{N}_iota{XX} -> wout filename
# ---------------------------------------------------------------------------

EQUILIBRIUM_FILES: dict[str, str] = {}
for _nfp in (5, 10, 15):
    for _iota_int in range(10, 51):
        _key = f"nfp{_nfp}_iota{_iota_int}"
        EQUILIBRIUM_FILES[_key] = f"wout_nfp{_nfp}ginsburg_desc_iota{_iota_int:02d}.nc"

# Legacy aliases (NFP=5 only)
EQUILIBRIUM_FILES.update({
    "iota15": "wout_nfp5ginsburg_000_014417_iota15.nc",
    "iota20": "wout_nfp5ginsburg_000_002084_iota20.nc",
    "iota15p": "wout_nfp5ginsburg_desc_iota15.nc",
    "iota20p": "wout_nfp5ginsburg_desc_iota20.nc",
    "001490": "wout_nfp5ginsburg_000_001490.nc",
})
for _i in range(15, 31):
    _legacy = f"iota{_i}"
    if _legacy not in EQUILIBRIUM_FILES:
        _mapped = EQUILIBRIUM_FILES.get(f"nfp5_iota{_i}")
        if _mapped:
            EQUILIBRIUM_FILES[_legacy] = _mapped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid7() -> str:
    ts_ms = int(time.time() * 1000)
    rand_bits = uuid.uuid4().int & ((1 << 74) - 1)
    u = (ts_ms << 80) | (0x7 << 76) | rand_bits
    u = (u & ~(0x3 << 62)) | (0x2 << 62)
    return str(uuid.UUID(int=u))


def _clean(v: object) -> object:
    """Convert NaN/Inf floats to None for JSON and SQLite safety."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


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
            id, coil_type, solver, equilibrium,
            status, status_reason, validated,
            iterations, elapsed, created_at,
            optimizer_success, termination_message,
            field_error, qs_error, boozer_residual,
            iota_actual, volume_actual,
            max_curvature, lead_end_curvature, non_lead_end_curvature,
            coil_length, coil_coil_dist, coil_surface_dist, surface_vessel_dist,
            max_force, self_intersecting, objective_J,
            params
        ) VALUES (
            :id, :coil_type, :solver, :equilibrium,
            :status, :status_reason, :validated,
            :iterations, :elapsed, :created_at,
            :optimizer_success, :termination_message,
            :field_error, :qs_error, :boozer_residual,
            :iota_actual, :volume_actual,
            :max_curvature, :lead_end_curvature, :non_lead_end_curvature,
            :coil_length, :coil_coil_dist, :coil_surface_dist, :surface_vessel_dist,
            :max_force, :self_intersecting, :objective_J,
            :params
        )""",
        {
            "id": record["id"],
            "coil_type": record.get("coil_type", "banana"),
            "solver": record["solver"],
            "equilibrium": record["equilibrium"],
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
            "lead_end_curvature": record.get("lead_end_curvature"),
            "non_lead_end_curvature": record.get("non_lead_end_curvature"),
            "coil_length": record.get("coil_length"),
            "coil_coil_dist": record.get("coil_coil_dist"),
            "coil_surface_dist": record.get("coil_surface_dist"),
            "surface_vessel_dist": record.get("surface_vessel_dist"),
            "max_force": record.get("max_force"),
            "self_intersecting": _bool_to_int(record.get("self_intersecting")),
            "objective_J": record.get("objective_J"),
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
# Equilibrium + seed resolution
# ---------------------------------------------------------------------------

def _resolve_equilibrium(eq_key: str) -> str:
    filename = EQUILIBRIUM_FILES.get(eq_key)
    if filename:
        return filename
    if (EQUILIBRIA_DIR / eq_key).exists():
        return eq_key
    print(f"ERROR: unknown equilibrium '{eq_key}'.", file=sys.stderr)
    sys.exit(1)


def _resolve_stage2_seed(args: argparse.Namespace, plasma_surf: str) -> str | None:
    """Find best Stage 2 seed matching equilibrium + geometry."""
    if args.stage2_bs_path:
        if Path(args.stage2_bs_path).is_file():
            return args.stage2_bs_path
        print(f"ERROR: seed not found: {args.stage2_bs_path}", file=sys.stderr)
        return None

    seeds_parent = STAGE2_SEED_STORE / f"outputs-{plasma_surf}"
    if not seeds_parent.is_dir():
        print(f"No seeds for {args.equilibrium}. Run Stage 2 first.", file=sys.stderr)
        return None

    best_seed = None
    best_fe = float("inf")
    for seed_dir in seeds_parent.iterdir():
        bs_file = seed_dir / "biot_savart_opt.json"
        results_file = seed_dir / "results.json"
        if not bs_file.is_file():
            continue
        if results_file.is_file():
            try:
                with open(results_file) as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if (
                abs(meta.get("MAJOR_RADIUS", 0) - args.major_radius) < 0.001
                and meta.get("order", 0) == args.order
                and not meta.get("SELF_INTERSECTING", False)
            ):
                fe = meta.get("FIELD_ERROR", 999.0)
                if isinstance(fe, float) and math.isnan(fe):
                    fe = 999.0
                if fe < best_fe:
                    best_fe = fe
                    best_seed = str(bs_file)

    if best_seed:
        print(f"Seed: FE={best_fe:.6f} {best_seed}", file=sys.stderr)
        return best_seed

    print(
        f"No Stage 2 seed for eq={args.equilibrium} R={args.major_radius} order={args.order}. "
        f"Run Stage 2 first.",
        file=sys.stderr,
    )
    return None


# ---------------------------------------------------------------------------
# CLI building
# ---------------------------------------------------------------------------

def _build_cli(args: argparse.Namespace, plasma_surf: str) -> list[str] | None:
    """Build solver CLI args. Returns None if seed resolution fails."""
    common = [
        "--plasma-surf-filename", plasma_surf,
        "--equilibria-dir", str(EQUILIBRIA_DIR),
        "--nphi", str(args.nphi),
        "--ntheta", str(args.ntheta),
        "--maxiter", str(args.maxiter),
        "--cc-weight", str(args.cc_weight),
        "--curvature-weight", str(args.curvature_weight),
        "--curvature-threshold", str(args.curvature_threshold),
        "--banana-surf-radius", str(args.banana_surf_radius),
    ]

    if args.solver == "stage2":
        return common + [
            "--major-radius", str(args.major_radius),
            "--toroidal-flux", str(args.toroidal_flux),
            "--order", str(args.order),
            "--cc-threshold", str(args.cc_threshold),
            "--length-weight", str(args.length_weight),
            "--length-target", str(args.length_target),
            "--squared-flux-weight", str(args.squared_flux_weight),
            "--curvature-p-norm", str(args.curvature_p_norm),
            "--num-quadpoints", str(args.num_quadpoints),
            "--basin-hops", str(args.basin_hops),
            "--basin-stepsize", str(args.basin_stepsize),
        ]

    # Single-stage: resolve seed
    seed = _resolve_stage2_seed(args, plasma_surf)
    if seed is None:
        return None

    return common + [
        "--stage2-bs-path", seed,
        "--cc-dist", str(args.cc_dist),
        "--iota-target", str(args.iota_target),
        "--vol-target", str(args.vol_target),
        "--mpol", str(args.mpol),
        "--ntor", str(args.ntor),
        "--constraint-weight", str(args.constraint_weight),
        "--boozer-stage", args.boozer_stage,
        "--num-tf-coils", str(args.num_tf_coils),
        "--length-weight", str(args.ss_length_weight),
        "--res-weight", str(args.res_weight),
        "--iotas-weight", str(args.iotas_weight),
        "--cs-weight", str(args.cs_weight),
        "--cs-dist", str(args.cs_dist),
        "--surf-dist-weight", str(args.surf_dist_weight),
        "--ss-dist", str(args.ss_dist),
        "--maxcor", str(args.maxcor),
    ]


def _extract_params(args: argparse.Namespace) -> dict:
    """Collect all solver params into a flat dict."""
    shared = {
        "cc_weight": args.cc_weight,
        "curvature_weight": args.curvature_weight,
        "curvature_threshold": args.curvature_threshold,
        "banana_surf_radius": args.banana_surf_radius,
        "major_radius": args.major_radius,
        "toroidal_flux": args.toroidal_flux,
        "order": args.order,
        "maxiter": args.maxiter,
        "nphi": args.nphi,
        "ntheta": args.ntheta,
    }
    if args.solver == "stage2":
        shared.update({
            "cc_threshold": args.cc_threshold,
            "length_weight": args.length_weight,
            "length_target": args.length_target,
            "squared_flux_weight": args.squared_flux_weight,
            "curvature_p_norm": args.curvature_p_norm,
            "num_quadpoints": args.num_quadpoints,
        })
    else:
        shared.update({
            "iota_target": args.iota_target,
            "vol_target": args.vol_target,
            "mpol": args.mpol,
            "ntor": args.ntor,
            "cc_dist": args.cc_dist,
            "constraint_weight": args.constraint_weight,
            "res_weight": args.res_weight,
            "iotas_weight": args.iotas_weight,
            "cs_weight": args.cs_weight,
            "cs_dist": args.cs_dist,
            "surf_dist_weight": args.surf_dist_weight,
            "ss_dist": args.ss_dist,
            "ss_length_weight": args.ss_length_weight,
            "maxcor": args.maxcor,
            "boozer_stage": args.boozer_stage,
        })
    return shared


# ---------------------------------------------------------------------------
# Result classification
# ---------------------------------------------------------------------------

def _classify(metrics: dict, solver: str) -> tuple[str, str]:
    """Classify a completed run. NaN metrics count as missing."""
    if metrics.get("SELF_INTERSECTING", False):
        return "fail", "self_intersecting"
    required = ["FIELD_ERROR", "MAX_CURVATURE"]
    if solver == "single-stage":
        required += ["FINAL_IOTA", "FINAL_VOLUME"]
    missing = [m for m in required if _clean(metrics.get(m)) is None]
    if missing:
        return "fail", "incomplete_metrics"
    if metrics.get("OPTIMIZER_SUCCESS") is False:
        return "fail", "optimizer_unsuccessful"
    return "pass", "ok"


# ---------------------------------------------------------------------------
# Record construction (single place where NaN cleaning happens)
# ---------------------------------------------------------------------------

def _build_record(
    args: argparse.Namespace,
    status: str,
    status_reason: str,
    elapsed: float,
    metrics: dict | None = None,
) -> dict:
    """Build a result record. All NaN cleaning happens here (SSOT)."""
    m = metrics or {}
    return {
        "id": _uuid7(),
        "coil_type": "banana",
        "solver": args.solver,
        "equilibrium": args.equilibrium,
        "status": status,
        "status_reason": status_reason,
        "iterations": m.get("iterations"),
        "elapsed": round(elapsed, 1),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "optimizer_success": m.get("OPTIMIZER_SUCCESS"),
        "termination_message": m.get("TERMINATION_MESSAGE"),
        "field_error": _clean(m.get("FIELD_ERROR")),
        "qs_error": _clean(m.get("NONQS_RATIO")),
        "boozer_residual": _clean(m.get("BOOZER_RESIDUAL")),
        "iota_actual": _clean(m.get("FINAL_IOTA")),
        "volume_actual": _clean(m.get("FINAL_VOLUME")),
        "max_curvature": _clean(m.get("MAX_CURVATURE")),
        "lead_end_curvature": _clean(m.get("LEAD_END_CURVATURE")),
        "non_lead_end_curvature": _clean(m.get("NON_LEAD_END_CURVATURE")),
        "coil_length": _clean(m.get("COIL_LENGTH")),
        "coil_coil_dist": _clean(m.get("CURVE_CURVE_MIN_DIST")),
        "coil_surface_dist": _clean(m.get("CURVE_SURFACE_MIN_DIST")),
        "surface_vessel_dist": _clean(m.get("SURFACE_VESSEL_MIN_DIST")),
        "max_force": _clean(m.get("MAX_FORCE")),
        "self_intersecting": m.get("SELF_INTERSECTING", False),
        "objective_J": _clean(m.get("OBJECTIVE_J")),
        "params": _extract_params(args),
    }


# ---------------------------------------------------------------------------
# Poincare validation
# ---------------------------------------------------------------------------

def _run_poincare(run_dir: Path, solver_python: str, solver_root: Path) -> str | None:
    """Run Poincare field-line tracing. Returns 'pass', 'fail', or None on error.

    The Poincare script traces field lines and prints phi hit counts to stdout.
    Field lines that exit the surface have fewer hits. We parse the hit counts
    to compute a survival fraction.
    """
    poincare_script = (
        solver_root
        / "examples/single_stage_optimization/POINCARE_PLOTTING/poincare_surfaces.py"
    )
    if not poincare_script.exists():
        print(f"Poincare script not found: {poincare_script}", file=sys.stderr)
        return None

    # Find the solver output dir (contains biot_savart_opt.json)
    bs_files = list(run_dir.rglob("biot_savart_opt.json"))
    if not bs_files:
        print("No biot_savart_opt.json for Poincare", file=sys.stderr)
        return None
    out_dir = str(bs_files[0].parent)

    env = os.environ.copy()
    env["POINCARE_OUT_DIR"] = out_dir

    try:
        result = subprocess.run(
            [solver_python, str(poincare_script)],
            capture_output=True, text=True, env=env, timeout=600,
        )
    except subprocess.TimeoutExpired:
        print("Poincare timed out after 600s", file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"Poincare failed (exit {result.returncode})", file=sys.stderr)
        return None

    # Parse hit counts from stdout: "phi hit counts=[N, N, N, N]"
    # The validation trace stops field lines at the Boozer surface exit.
    # Surviving lines produce many hits; lost lines produce few.
    # Uniformity across phi slices (min/max) indicates confinement quality.
    for line in result.stdout.splitlines():
        if "phi hit counts=" not in line:
            continue
        try:
            counts_str = line.split("phi hit counts=")[1].strip()
            counts = json.loads(counts_str)
            if not isinstance(counts, list) or not counts:
                continue
            counts = [c for c in counts if isinstance(c, (int, float)) and c is not None]
            if not counts or max(counts) == 0:
                return "fail"
            uniformity = min(counts) / max(counts)
            return "pass" if uniformity > POINCARE_SURVIVAL_THRESHOLD else "fail"
        except Exception:
            continue

    print("Could not parse Poincare output", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Core experiment
# ---------------------------------------------------------------------------

def _run_experiment(args: argparse.Namespace) -> None:
    coil_cfg = SOLVERS["banana"]
    solver_root = Path(args.solver_root) if args.solver_root else coil_cfg["default_root"]
    solver_python = args.solver_python or coil_cfg["default_python"]
    solver_script = solver_root / coil_cfg[args.solver]

    plasma_surf = _resolve_equilibrium(args.equilibrium)
    cli_args = _build_cli(args, plasma_surf)
    if cli_args is None:
        _emit_result(_build_record(args, "crash", "no_seed", 0.0))
        return

    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = str(args.omp_threads)
    env["MKL_NUM_THREADS"] = str(args.omp_threads)

    run_dir = OUTPUT_BASE / f"run_{int(time.time() * 1000)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    cli_args += ["--output-root", str(run_dir)]

    cmd = [solver_python, str(solver_script)] + cli_args
    log_path = run_dir / "run.log"
    t0 = time.monotonic()

    try:
        with open(log_path, "w") as lf:
            result = subprocess.run(
                cmd, stdout=lf, stderr=subprocess.STDOUT,
                env=env, timeout=args.timeout,
            )
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        _emit_result(_build_record(args, "crash", "timeout", time.monotonic() - t0))
        return

    elapsed = time.monotonic() - t0

    if returncode != 0:
        _emit_result(_build_record(args, "crash", f"exit_{returncode}", elapsed))
        return

    # Parse solver output
    results_files = list(run_dir.rglob("results.json"))
    if not results_files:
        _emit_result(_build_record(args, "crash", "no_results_json", elapsed))
        return

    try:
        with open(results_files[0]) as f:
            metrics = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _emit_result(_build_record(args, "crash", f"bad_results_json: {e}", elapsed))
        return

    status, status_reason = _classify(metrics, args.solver)
    record = _build_record(args, status, status_reason, elapsed, metrics)

    # Emit to JSONL + stdout first (without validated — JSONL is append-only)
    _emit_result(record)

    # Poincare validation for passing single-stage runs (DB-only update)
    if (
        args.solver == "single-stage"
        and status == "pass"
        and record.get("field_error") is not None
        and record["field_error"] < POINCARE_FIELD_ERROR_THRESHOLD
    ):
        try:
            validated = _run_poincare(run_dir, solver_python, solver_root)
            if validated:
                with contextlib.closing(_ensure_db()) as db:
                    db.execute(
                        "UPDATE runs SET validated = ? WHERE id = ?",
                        (validated, record["id"]),
                    )
                    db.commit()
        except Exception as e:
            print(f"WARNING: Poincare validation failed: {e}", file=sys.stderr)

    # Archive Stage 2 seeds for single-stage to use later
    if args.solver == "stage2":
        try:
            _archive_stage2_seed(run_dir, plasma_surf)
        except Exception as e:
            print(f"WARNING: seed archival failed: {e}", file=sys.stderr)

    # Cleanup tmpdir
    shutil.rmtree(run_dir, ignore_errors=True)


def _archive_stage2_seed(run_dir: Path, plasma_surf: str) -> None:
    """Copy Stage 2 biot_savart_opt.json to seed store."""
    bs_files = list(run_dir.rglob("biot_savart_opt.json"))
    if not bs_files:
        return
    ts = int(time.time() * 1000)
    seed_dir = STAGE2_SEED_STORE / f"outputs-{plasma_surf}" / f"{bs_files[0].parent.name}-{ts}"
    seed_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bs_files[0], seed_dir / "biot_savart_opt.json")
    results_files = list(run_dir.rglob("results.json"))
    if results_files:
        shutil.copy2(results_files[0], seed_dir / "results.json")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Run one stellarator coil optimization experiment")

    p.add_argument("--solver", choices=["stage2", "single-stage"], default="stage2")
    p.add_argument("--equilibrium", default="nfp5_iota15")

    # Shared
    p.add_argument("--cc-weight", type=float, default=100.0)
    p.add_argument("--cc-threshold", type=float, default=0.05)
    p.add_argument("--curvature-weight", type=float, default=0.1)
    p.add_argument("--curvature-threshold", type=float, default=40.0)
    p.add_argument("--banana-surf-radius", type=float, default=0.22)
    p.add_argument("--major-radius", type=float, default=0.915)
    p.add_argument("--toroidal-flux", type=float, default=0.215)
    p.add_argument("--order", type=int, default=2)
    p.add_argument("--maxiter", type=int, default=400)
    p.add_argument("--nphi", type=int, default=255)
    p.add_argument("--ntheta", type=int, default=64)

    # Stage 2 only
    p.add_argument("--length-weight", type=float, default=1.0)
    p.add_argument("--length-target", type=float, default=1.75)
    p.add_argument("--squared-flux-weight", type=float, default=1.0)
    p.add_argument("--curvature-p-norm", type=int, default=4)
    p.add_argument("--num-quadpoints", type=int, default=128)
    p.add_argument("--basin-hops", type=int, default=0)
    p.add_argument("--basin-stepsize", type=float, default=0.01)

    # Single-stage only
    p.add_argument("--iota-target", type=float, default=0.15)
    p.add_argument("--vol-target", type=float, default=0.10)
    p.add_argument("--mpol", type=int, default=8)
    p.add_argument("--ntor", type=int, default=6)
    p.add_argument("--constraint-weight", type=float, default=1.0)
    p.add_argument("--cc-dist", type=float, default=0.05)
    p.add_argument("--res-weight", type=float, default=1000.0)
    p.add_argument("--iotas-weight", type=float, default=100.0)
    p.add_argument("--cs-weight", type=float, default=1.0)
    p.add_argument("--cs-dist", type=float, default=0.02)
    p.add_argument("--surf-dist-weight", type=float, default=1000.0)
    p.add_argument("--ss-dist", type=float, default=0.04)
    p.add_argument("--ss-length-weight", type=float, default=1.0)
    p.add_argument("--num-tf-coils", type=int, default=20)
    p.add_argument("--maxcor", type=int, default=300)
    p.add_argument("--boozer-stage", choices=["initial", "final"], default="initial")
    p.add_argument("--stage2-bs-path", type=str, default=None)

    # Solver variant
    p.add_argument("--solver-root", type=str, default=None)
    p.add_argument("--solver-python", type=str, default=None)

    # Execution
    p.add_argument("--omp-threads", type=int, default=10)
    p.add_argument("--timeout", type=int, default=600)

    args = p.parse_args()
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    _run_experiment(args)


if __name__ == "__main__":
    main()
