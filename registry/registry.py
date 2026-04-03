#!/usr/bin/env python3
"""Stellarator coil optimization registry: DB operations and JSONL ingest.

Usage:
    # Initialize a new registry database
    python registry/registry.py init

    # Ingest existing results.jsonl into the registry
    python registry/registry.py ingest

    # Validate DB integrity (artifacts exist, hashes match)
    python registry/registry.py check

    # Show runs pending Poincare validation
    python registry/registry.py pending

    # Mark a run as validated/failed after Poincare analysis
    python registry/registry.py validate <run_id> --status validated \
        --survival 0.98 --phi-completeness 0.95 --by jungdae

    # Show the validated frontier
    python registry/registry.py frontier

    # Export validated frontier to CSV (for sharing)
    python registry/registry.py export
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import uuid
from pathlib import Path

REGISTRY_DIR = Path(__file__).resolve().parent
DB_PATH = REGISTRY_DIR / "registry.db"
MIGRATIONS_DIR = REGISTRY_DIR / "migrations"
ARTIFACT_ROOT = REGISTRY_DIR / "artifacts"
REPO_ROOT = REGISTRY_DIR.parent
RESULTS_PATH = REPO_ROOT / "results.jsonl"


# ---------------------------------------------------------------------------
# UUID v7 (time-sortable)
# ---------------------------------------------------------------------------

def _uuid7() -> str:
    """Generate a UUID v7 (timestamp-based, sortable, no coordination)."""
    import time
    ts_ms = int(time.time() * 1000)
    rand_bits = uuid.uuid4().int & ((1 << 74) - 1)
    u = (ts_ms << 80) | (0x7 << 76) | rand_bits
    # Set variant bits
    u = (u & ~(0x3 << 62)) | (0x2 << 62)
    return str(uuid.UUID(int=u))


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

def _run_migrations(db: sqlite3.Connection) -> int:
    """Apply pending migrations. Returns number applied."""
    # Ensure schema_versions exists (bootstrap)
    db.execute("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            description TEXT NOT NULL
        )
    """)
    db.commit()

    current = db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_versions"
    ).fetchone()[0]

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied = 0

    for mf in migration_files:
        version = int(mf.stem.split("_")[0])
        if version <= current:
            continue
        print(f"  Applying migration {mf.name} ...", end=" ")
        sql = mf.read_text()
        # Strip the INSERT INTO schema_versions from the migration file itself
        # (the initial schema includes it) -- we handle versioning here.
        db.executescript(sql)
        db.execute(
            "INSERT OR REPLACE INTO schema_versions (version, description) VALUES (?, ?)",
            (version, mf.stem),
        )
        db.commit()
        applied += 1
        print("ok")

    return applied


def get_db() -> sqlite3.Connection:
    """Open (and migrate) the registry database."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA foreign_keys = ON")
    _run_migrations(db)
    return db


# ---------------------------------------------------------------------------
# JSONL -> Registry ingest
# ---------------------------------------------------------------------------

def _nan_to_none(v: object) -> object:
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _ingest_one(db: sqlite3.Connection, record: dict, contributor: str, machine: str) -> str:
    """Ingest a single JSONL record into the registry. Returns the run_id."""
    run_id = _uuid7()
    p = record.get("params", {})

    # -- runs --
    db.execute(
        """INSERT INTO runs
           (run_id, contributor, machine, runtime_s, solver, method,
            equilibrium, equilibrium_file, optimizer_converged,
            termination_msg, iterations, objective_J, validation_status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            contributor,
            machine,
            record.get("elapsed"),
            record.get("solver", "stage2"),
            "alm" if p.get("alm") else "weighted-sum",
            record.get("equilibrium", "unknown"),
            None,
            1 if record.get("optimizer_success") else (0 if record.get("status") == "crash" else None),
            record.get("termination_message") or record.get("feedback"),
            record.get("iterations"),
            _nan_to_none(record.get("objective_J")),
            "pending",
            None,
        ),
    )

    # -- metrics --
    db.execute(
        """INSERT INTO metrics
           (run_id, iota_actual, iota_target, iota_error, qs_error,
            boozer_residual, max_curvature_m_inv, coil_coil_dist_m,
            field_error, self_intersecting, volume_actual)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            _nan_to_none(record.get("final_iota")),
            _nan_to_none(record.get("target_iota") or record.get("params", {}).get("iota_target")),
            (abs(record["final_iota"] - _target_iota)
             if record.get("final_iota") is not None
             and (_target_iota := record.get("target_iota") or record.get("params", {}).get("iota_target")) is not None
             else None),
            _nan_to_none(record.get("nonqs_ratio")),
            _nan_to_none(record.get("boozer_residual")),
            _nan_to_none(record.get("max_curvature")),
            _nan_to_none(record.get("curve_curve_min_dist")),
            _nan_to_none(record.get("field_error")),
            1 if record.get("self_intersecting") else (0 if record.get("self_intersecting") is False else None),
            _nan_to_none(record.get("final_volume")),
        ),
    )

    # -- params --
    # Identify params that map to columns vs extra_json overflow
    known_params = {
        "mpol", "ntor", "nphi", "ntheta", "maxiter", "maxcor", "ftol", "gtol",
        "major_radius", "toroidal_flux", "banana_surf_radius", "order",
        "cc_weight", "curvature_weight", "length_weight",
        "cc_threshold", "curvature_threshold",
        "squared_flux_weight", "curvature_p_norm", "num_quadpoints",
        "length_target", "theta_center", "phi_center", "theta_width", "phi_width",
        "basin_hops", "basin_stepsize", "basin_seed",
        "iota_target", "vol_target", "constraint_weight",
        "res_weight", "iotas_weight", "cs_weight", "cs_dist", "cc_dist",
        "surf_dist_weight", "ss_dist", "ss_length_weight",
        "boozer_stage", "num_tf_coils",
        "alm", "alm_outer_iters", "alm_mu_init", "alm_mu_max",
        "alm_mu_increase", "alm_tol",
    }
    extra = {k: v for k, v in p.items() if k not in known_params}

    db.execute(
        """INSERT INTO params
           (run_id, mpol, ntor, nphi, ntheta, maxiter, maxcor, ftol, gtol,
            major_radius, toroidal_flux, banana_surf_radius, order_param,
            cc_weight, curvature_weight, length_weight,
            cc_threshold, curvature_threshold,
            squared_flux_weight, curvature_p_norm, num_quadpoints,
            length_target, theta_center, phi_center, theta_width, phi_width,
            basin_hops, basin_stepsize, basin_seed,
            iota_target, vol_target, constraint_weight,
            res_weight, iotas_weight, cs_weight, cs_dist, cc_dist,
            surf_dist_weight, ss_dist, ss_length_weight,
            boozer_stage, num_tf_coils,
            alm_enabled, alm_outer_iters, alm_mu_init, alm_mu_max,
            alm_mu_increase, alm_tol, extra_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?,
                   ?, ?, ?,
                   ?, ?,
                   ?, ?, ?,
                   ?, ?, ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?,
                   ?, ?, ?, ?, ?,
                   ?, ?, ?,
                   ?, ?,
                   ?, ?, ?, ?,
                   ?, ?, ?)""",
        (
            run_id,
            p.get("mpol"), p.get("ntor"), p.get("nphi"), p.get("ntheta"),
            p.get("maxiter"), p.get("maxcor"),
            p.get("ftol"), p.get("gtol"),
            p.get("major_radius"), p.get("toroidal_flux"),
            p.get("banana_surf_radius"), p.get("order"),
            p.get("cc_weight"), p.get("curvature_weight"), p.get("length_weight"),
            p.get("cc_threshold"), p.get("curvature_threshold"),
            p.get("squared_flux_weight"), p.get("curvature_p_norm"),
            p.get("num_quadpoints"),
            p.get("length_target"), p.get("theta_center"), p.get("phi_center"),
            p.get("theta_width"), p.get("phi_width"),
            p.get("basin_hops"), p.get("basin_stepsize"), p.get("basin_seed"),
            p.get("iota_target"), p.get("vol_target"), p.get("constraint_weight"),
            p.get("res_weight"), p.get("iotas_weight"),
            p.get("cs_weight"), p.get("cs_dist"), p.get("cc_dist"),
            p.get("surf_dist_weight"), p.get("ss_dist"), p.get("ss_length_weight"),
            p.get("boozer_stage"), p.get("num_tf_coils"),
            1 if p.get("alm") else 0,
            p.get("alm_outer_iters"), p.get("alm_mu_init"), p.get("alm_mu_max"),
            p.get("alm_mu_increase"), p.get("alm_tol"),
            json.dumps(extra) if extra else None,
        ),
    )

    # -- seeds (single-stage only) --
    seed_path = record.get("stage2_seed_path")
    if seed_path:
        db.execute(
            """INSERT INTO seeds (run_id, seed_source, seed_path)
               VALUES (?, ?, ?)""",
            (run_id, "autoresearch" if "autoresearch" in str(seed_path) else "columbia_database", seed_path),
        )

    return run_id


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    """Initialize the registry database."""
    if DB_PATH.exists() and not args.force:
        print(f"Registry already exists at {DB_PATH}. Use --force to reinitialize.")
        sys.exit(1)
    if DB_PATH.exists():
        DB_PATH.unlink()
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM schema_versions").fetchone()[0]
    print(f"Registry initialized at {DB_PATH} (schema version {count}).")
    db.close()


def cmd_ingest(args: argparse.Namespace) -> None:
    """Ingest results.jsonl into the registry."""
    if not RESULTS_PATH.exists():
        print(f"No {RESULTS_PATH} found.")
        sys.exit(1)

    db = get_db()
    contributor = args.contributor or "autoresearch"
    machine = args.machine or "unknown"

    existing = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    if existing > 0 and not args.force:
        print(f"Registry already has {existing} runs. Use --force to add more.")
        sys.exit(1)

    ingested = 0
    skipped = 0
    with open(RESULTS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            source = record.get("source", "local")
            m = "M3 Max" if source == "local" else "EC2 c5ad.8xlarge"
            _ingest_one(db, record, contributor, m)
            ingested += 1

    db.commit()
    print(f"Ingested {ingested} runs (skipped {skipped}). Total: {existing + ingested}.")
    db.close()


def cmd_check(args: argparse.Namespace) -> None:
    """Validate artifact integrity."""
    db = get_db()
    rows = db.execute("SELECT storage_key, sha256 FROM artifacts").fetchall()
    if not rows:
        print("No artifacts registered.")
        return

    missing = 0
    corrupt = 0
    for row in rows:
        path = ARTIFACT_ROOT / row["storage_key"]
        if not path.exists():
            print(f"  MISSING: {row['storage_key']}")
            missing += 1
        elif row["sha256"]:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != row["sha256"]:
                print(f"  CORRUPT: {row['storage_key']} (expected {row['sha256'][:12]}..., got {actual[:12]}...)")
                corrupt += 1

    total = len(rows)
    ok = total - missing - corrupt
    print(f"Checked {total} artifacts: {ok} ok, {missing} missing, {corrupt} corrupt.")
    db.close()


def cmd_pending(args: argparse.Namespace) -> None:
    """Show runs pending Poincare validation."""
    db = get_db()
    rows = db.execute("""
        SELECT run_id, contributor, equilibrium, created_at,
               field_error, iota_actual, qs_error, max_curvature_m_inv,
               mpol, order_param
        FROM v_needs_poincare
        LIMIT ?
    """, (args.limit or 20,)).fetchall()

    if not rows:
        print("No runs pending validation.")
        return

    print(f"{'run_id':>12s}  {'eq':>8s}  {'FE':>10s}  {'iota':>6s}  {'QS':>10s}  {'mpol':>4s}  {'by':>12s}")
    print("-" * 80)
    for r in rows:
        fe = f"{r['field_error']:.6f}" if r["field_error"] is not None else "N/A"
        iota = f"{r['iota_actual']:.4f}" if r["iota_actual"] is not None else "N/A"
        qs = f"{r['qs_error']:.2e}" if r["qs_error"] is not None else "N/A"
        mpol = str(r["mpol"]) if r["mpol"] is not None else "?"
        print(f"{r['run_id'][:12]:>12s}  {r['equilibrium']:>8s}  {fe:>10s}  {iota:>6s}  {qs:>10s}  {mpol:>4s}  {r['contributor']:>12s}")

    db.close()


def cmd_validate(args: argparse.Namespace) -> None:
    """Update a run's validation status after Poincare analysis."""
    db = get_db()

    # Verify run exists
    run = db.execute("SELECT run_id FROM runs WHERE run_id LIKE ?", (args.run_id + "%",)).fetchone()
    if not run:
        print(f"No run found matching '{args.run_id}'.")
        sys.exit(1)
    full_id = run["run_id"]

    db.execute(
        "UPDATE runs SET validation_status = ? WHERE run_id = ?",
        (args.status, full_id),
    )

    if args.status == "validated" or args.status == "fails_validation":
        db.execute(
            """INSERT OR REPLACE INTO poincare
               (run_id, validated_by, survival_fraction, phi_completeness, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (
                full_id,
                args.by,
                args.survival,
                args.phi_completeness,
                args.notes,
            ),
        )

    db.commit()
    print(f"Run {full_id[:12]}... -> validation_status = {args.status}")
    db.close()


def cmd_frontier(args: argparse.Namespace) -> None:
    """Show the validated frontier."""
    db = get_db()

    # Use validated frontier if any validated runs exist, otherwise fall back to all passing
    validated_count = db.execute(
        "SELECT COUNT(*) FROM runs WHERE validation_status = 'validated'"
    ).fetchone()[0]

    if validated_count > 0:
        rows = db.execute("SELECT * FROM v_validated_frontier LIMIT ?", (args.limit or 20,)).fetchall()
        print(f"=== VALIDATED FRONTIER ({validated_count} runs) ===\n")
    else:
        rows = db.execute("""
            SELECT r.run_id, r.contributor, r.equilibrium, r.solver, r.method,
                   m.max_volume_m3, m.iota_actual, m.iota_error, m.qs_error,
                   m.boozer_residual, m.coil_coil_dist_m, m.max_curvature_m_inv,
                   m.coil_length_m, m.plasma_vessel_dist_m,
                   p.mpol, p.order_param, p.curvature_weight
            FROM runs r
            JOIN metrics m ON r.run_id = m.run_id
            JOIN params  p ON r.run_id = p.run_id
            WHERE m.self_intersecting = 0
              AND m.field_error IS NOT NULL
            ORDER BY m.field_error ASC
            LIMIT ?
        """, (args.limit or 20,)).fetchall()
        print(f"=== FRONTIER (no validated runs yet, showing by field_error) ===\n")

    if not rows:
        print("No runs in frontier.")
        return

    for i, r in enumerate(rows, 1):
        vol = f"{r['max_volume_m3']:.6f}" if r["max_volume_m3"] is not None else "N/A"
        iota = f"{r['iota_actual']:.4f}" if r["iota_actual"] is not None else "N/A"
        qs = f"{r['qs_error']:.2e}" if r["qs_error"] is not None else "N/A"
        cc = f"{r['coil_coil_dist_m']:.4f}" if r["coil_coil_dist_m"] is not None else "N/A"
        mc = f"{r['max_curvature_m_inv']:.1f}" if r["max_curvature_m_inv"] is not None else "N/A"
        print(
            f"  {i:2d}. [{r['equilibrium']:>8s}] vol={vol}  iota={iota}  QS={qs}"
            f"  CC={cc}  MC={mc}  mpol={r['mpol'] or '?'}"
        )

    db.close()


def cmd_export(args: argparse.Namespace) -> None:
    """Export the validated frontier to CSV."""
    import csv

    db = get_db()
    rows = db.execute("SELECT * FROM v_validated_frontier").fetchall()

    if not rows:
        print("No validated runs to export.")
        return

    out_path = args.output or (REGISTRY_DIR / "frontier_export.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([desc[0] for desc in rows[0].keys()])
        for row in rows:
            writer.writerow(list(row))

    print(f"Exported {len(rows)} runs to {out_path}")
    db.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stellarator coil optimization registry"
    )
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Initialize the registry database")
    p_init.add_argument("--force", action="store_true", help="Reinitialize if exists")

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest results.jsonl into registry")
    p_ingest.add_argument("--contributor", default="autoresearch")
    p_ingest.add_argument("--machine", default=None)
    p_ingest.add_argument("--force", action="store_true", help="Allow adding to non-empty DB")

    # check
    sub.add_parser("check", help="Validate artifact integrity")

    # pending
    p_pending = sub.add_parser("pending", help="Show runs pending Poincare validation")
    p_pending.add_argument("--limit", type=int, default=20)

    # validate
    p_val = sub.add_parser("validate", help="Set validation status for a run")
    p_val.add_argument("run_id", help="Run ID (prefix match supported)")
    p_val.add_argument("--status", required=True,
                       choices=["validated", "fails_validation", "diagnostic_only"])
    p_val.add_argument("--survival", type=float, help="Poincare survival fraction")
    p_val.add_argument("--phi-completeness", type=float)
    p_val.add_argument("--by", default="unknown", help="Who validated this run")
    p_val.add_argument("--notes", default=None)

    # frontier
    p_front = sub.add_parser("frontier", help="Show the validated frontier")
    p_front.add_argument("--limit", type=int, default=20)

    # export
    p_export = sub.add_parser("export", help="Export frontier to CSV")
    p_export.add_argument("--output", type=str, default=None)

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "ingest": cmd_ingest,
        "check": cmd_check,
        "pending": cmd_pending,
        "validate": cmd_validate,
        "frontier": cmd_frontier,
        "export": cmd_export,
    }

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands[args.command](args)


if __name__ == "__main__":
    main()
