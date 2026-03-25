#!/usr/bin/env python3
"""Lab notebook: SQLite-backed query engine for the stellarator experiment space.

JSONL is the append-only source of truth. SQLite is a derived in-memory index
rebuilt on each invocation (<100ms for 500 rows). The agent never touches SQL
directly — it uses subcommands.

Usage:
    python scripts/lab.py check --eq iota15 --cw 0.005 --order 4  # Has this been tried?
    python scripts/lab.py coverage                                  # What's been explored?
    python scripts/lab.py frontier                                  # Best results so far
    python scripts/lab.py history --eq iota15                       # What combos tried for eq?
    python scripts/lab.py nearby --eq iota15 --cw 0.005             # What's near this CW?
    python scripts/lab.py crashes --eq iota15                       # Crash patterns
    python scripts/lab.py suggest --budget 3                        # Next best experiments
    python scripts/lab.py diff --eq iota15 --eq2 iota20             # Compare two equilibria
    python scripts/lab.py param-effect --param order                # How does param affect outcome?
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "results.jsonl"
STAGE2_SEED_STORE = REPO_ROOT / "stage2_seeds"
COLUMBIA_DATABASE = Path(
    "/Users/suhjungdae/code/columbia/DATABASE/COIL_OPTIMIZATION/outputs"
)

ALL_EQS = [
    "iota15", "iota15p", "iota16", "iota17", "iota18", "iota19",
    "iota20", "iota20p", "iota21", "iota22", "iota23", "iota24",
    "iota25", "iota26", "iota27", "iota28", "iota29", "iota30",
    "001490",
]


# ---------------------------------------------------------------------------
# SQLite index — rebuilt from JSONL each invocation
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    source TEXT, solver TEXT, equilibrium TEXT, status TEXT,
    score REAL, field_error REAL, self_intersecting INTEGER,
    max_curvature REAL, iterations INTEGER, elapsed REAL,
    timestamp TEXT, feedback TEXT,
    stage2_seed_path TEXT, single_stage_artifact_dir TEXT, run_dir TEXT,
    objective_J REAL, curve_curve_min_dist REAL,
    final_iota REAL, final_volume REAL,
    nonqs_ratio REAL, boozer_residual REAL, note TEXT,
    termination_message TEXT, optimizer_success INTEGER, ftol REAL, gtol REAL,
    coil_length REAL, curve_surface_min_dist REAL, surface_vessel_min_dist REAL,
    max_force REAL, lead_end_curvature REAL, non_lead_end_curvature REAL,
    p_order INTEGER, p_curvature_weight REAL, p_curvature_threshold REAL,
    p_cc_weight REAL, p_banana_surf_radius REAL, p_major_radius REAL,
    p_toroidal_flux REAL, p_length_weight REAL, p_cc_threshold REAL,
    p_maxiter INTEGER, p_res_weight REAL, p_iotas_weight REAL,
    p_mpol INTEGER, p_iota_target REAL, p_vol_target REAL, p_alm INTEGER,
    params_json TEXT
)"""

# Maps JSONL top-level keys → DB column names (order matters for INSERT)
_TOP_FIELDS = [
    "source", "solver", "equilibrium", "status", "score", "field_error",
    "self_intersecting", "max_curvature", "iterations", "elapsed",
    "timestamp", "feedback", "stage2_seed_path", "single_stage_artifact_dir",
    "run_dir", "objective_J", "curve_curve_min_dist", "final_iota",
    "final_volume", "nonqs_ratio",
    "boozer_residual", "note",
    "termination_message", "optimizer_success", "ftol", "gtol",
    "coil_length", "curve_surface_min_dist", "surface_vessel_min_dist",
    "max_force", "lead_end_curvature", "non_lead_end_curvature",
]

# Maps params dict keys → DB column names (p_ prefix)
_PARAM_FIELDS = [
    "order", "curvature_weight", "curvature_threshold", "cc_weight",
    "banana_surf_radius", "major_radius", "toroidal_flux", "length_weight",
    "cc_threshold", "maxiter", "res_weight", "iotas_weight", "mpol",
    "iota_target", "vol_target", "alm",
]

_SI_MAP = {True: 1, False: 0}

_DB: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    """Build (or return cached) in-memory SQLite index from results.jsonl."""
    global _DB
    if _DB is not None:
        return _DB

    if not RESULTS_PATH.exists():
        print("No results.jsonl found.", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(_SCHEMA)
    db.execute("CREATE INDEX idx_eq ON runs(equilibrium)")
    db.execute("CREATE INDEX idx_solver_status ON runs(solver, status)")
    db.execute("CREATE INDEX idx_eq_order ON runs(equilibrium, p_order)")
    db.execute("CREATE INDEX idx_score ON runs(score DESC)")

    all_cols = ["id"] + _TOP_FIELDS + [f"p_{f}" for f in _PARAM_FIELDS] + ["params_json"]
    placeholders = ", ".join(f":{c}" for c in all_cols)
    insert_sql = f"INSERT INTO runs ({', '.join(all_cols)}) VALUES ({placeholders})"

    with open(RESULTS_PATH) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            p = r.get("params", {})
            row = {"id": i}
            for field in _TOP_FIELDS:
                val = r.get(field)
                if field in ("score", "field_error"):
                    val = _nan_to_none(val)
                elif field == "self_intersecting":
                    val = _SI_MAP.get(val)
                row[field] = val
            for field in _PARAM_FIELDS:
                row[f"p_{field}"] = p.get(field)
            row["params_json"] = json.dumps(p)
            db.execute(insert_sql, row)

    db.commit()
    _DB = db
    return db


def _nan_to_none(v):
    """Convert NaN to None for SQLite compatibility."""
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _where(solver=None, eq=None, order=None, status=None):
    """Build WHERE clause and params dict from common CLI filters."""
    clauses: list[str] = []
    params: dict = {}
    if solver is not None:
        clauses.append("solver = :solver")
        params["solver"] = solver
    if eq is not None:
        clauses.append("equilibrium = :eq")
        params["eq"] = eq
    if order is not None:
        clauses.append("p_order = :order")
        params["order"] = order
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    return (" AND ".join(clauses) or "1=1"), params


def _row_params(row) -> dict:
    """Extract the params dict from a database row."""
    raw = row["params_json"]
    return json.loads(raw) if raw else {}


def _exists(
    db: sqlite3.Connection, eq: str, order: int, cw: float,
    solver: str | None = None,
) -> bool:
    """Check if a run with these exact key params exists."""
    sql = (
        "SELECT 1 FROM runs WHERE equilibrium=:eq AND p_order=:order"
        " AND ABS(p_curvature_weight - :cw) < 1e-7"
    )
    params: dict = {"eq": eq, "order": order, "cw": cw}
    if solver is not None:
        sql += " AND solver=:solver"
        params["solver"] = solver
    return db.execute(sql + " LIMIT 1", params).fetchone() is not None


def _fmt_fe(fe) -> str:
    if fe is None:
        return "N/A"
    return f"{fe:.6f}"


def _fmt_score(s) -> str:
    if s is None:
        return "N/A"
    return f"{s:.4f}"


def _compact_params(p: dict) -> str:
    """One-line param summary showing only interesting (non-default) params."""
    defaults = {
        "cc_weight": 50.0, "curvature_threshold": 20.0,
        "banana_surf_radius": 0.22, "major_radius": 0.915,
        "toroidal_flux": 0.215, "length_weight": 1e-05, "cc_threshold": 0.05,
    }
    abbrev = {
        "curvature_threshold": "CT", "cc_weight": "CCW",
        "banana_surf_radius": "SR", "major_radius": "MR",
        "toroidal_flux": "TF", "length_weight": "LW", "cc_threshold": "CCT",
    }
    parts = [f"o={p.get('order', '?')}", f"CW={p.get('curvature_weight', '?')}"]
    for k, default_v in defaults.items():
        v = p.get(k)
        if v is not None and abs(v - default_v) > 1e-9:
            parts.append(f"{abbrev.get(k, k)}={v}")
    return " ".join(parts)


# Canonical experiment key columns (for GROUP BY / dedup)
_KEY_COLS = (
    "solver", "equilibrium", "p_order", "p_curvature_weight",
    "p_curvature_threshold", "p_cc_weight", "p_banana_surf_radius",
    "p_major_radius", "p_toroidal_flux", "p_length_weight",
)
_KEY_GROUP = ", ".join(_KEY_COLS)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> None:
    """Has this exact (eq, order, CW) combo been tried?"""
    db = _get_db()
    w, p = _where(eq=args.eq, order=args.order)
    if args.cw is not None:
        w += " AND ABS(p_curvature_weight - :cw) < 1e-7"
        p["cw"] = args.cw

    rows = db.execute(
        f"SELECT * FROM runs WHERE {w} ORDER BY score DESC", p,
    ).fetchall()

    parts = []
    if args.eq:
        parts.append(f"eq={args.eq}")
    if args.order is not None:
        parts.append(f"order={args.order}")
    if args.cw is not None:
        parts.append(f"CW={args.cw}")
    header = " ".join(parts) or "all"

    if rows:
        print(f"=== CHECK: {header} — FOUND {len(rows)} runs ===")
        print()
        for i, r in enumerate(rows[:10], 1):
            fe = _fmt_fe(r["field_error"])
            sc = _fmt_score(r["score"])
            si = " SI!" if r["self_intersecting"] else ""
            elapsed = f"{r['elapsed']:.0f}s" if r["elapsed"] is not None else "?s"
            print(
                f"  {i}. [{r['status']:5s}] score={sc} FE={fe}"
                f" ({r['solver']}, {elapsed}){si}"
            )
        if len(rows) > 10:
            print(f"  ... and {len(rows) - 10} more")
        best = rows[0]
        print(
            f"\nVerdict: Already explored. Best score={_fmt_score(best['score'])},"
            f" FE={_fmt_fe(best['field_error'])}"
        )
    else:
        print(f"=== CHECK: {header} — NOT TRIED ===")
        nw, np = _where(eq=args.eq, order=args.order, status="pass")
        near = db.execute(
            f"SELECT * FROM runs WHERE {nw}"
            " AND p_curvature_weight IS NOT NULL"
            " ORDER BY ABS(p_curvature_weight - :tcw) LIMIT 3",
            {**np, "tcw": args.cw or 0},
        ).fetchall()
        if near:
            print("\nNearest passing runs:")
            for r in near:
                print(
                    f"  {_compact_params(_row_params(r))} →"
                    f" score={_fmt_score(r['score'])} FE={_fmt_fe(r['field_error'])}"
                )
        print("\nVerdict: Unexplored. Run it.")


def cmd_coverage(args: argparse.Namespace) -> None:
    """What has been explored? Coverage heatmap across key dimensions."""
    db = _get_db()
    w, p = _where(solver=args.solver)
    total = db.execute(f"SELECT COUNT(*) FROM runs WHERE {w}", p).fetchone()[0]

    rows = db.execute(
        f"""
        SELECT equilibrium, p_order,
            COUNT(*) as total,
            SUM(status='pass') as passes,
            SUM(status='fail') as fails,
            SUM(status='crash') as crashes,
            MIN(field_error) as best_fe,
            MAX(score) as best_score
        FROM runs WHERE {w}
        GROUP BY equilibrium, p_order
        ORDER BY equilibrium, p_order
        """,
        p,
    ).fetchall()

    grid = {(r["equilibrium"], r["p_order"]): r for r in rows}
    orders = sorted(set(r["p_order"] for r in rows))
    eqs = sorted(set(r["equilibrium"] for r in rows))

    print(f"=== COVERAGE ({args.solver or 'all'}, {total} runs) ===")
    print()
    header = "equilibrium".ljust(14) + "".join(
        f"order={o}".rjust(22) for o in orders
    )
    print(header)
    print("-" * len(header))
    for eq in eqs:
        line = eq.ljust(14)
        for o in orders:
            c = grid.get((eq, o))
            if c is None:
                line += "---".rjust(22)
            else:
                fe = _fmt_fe(c["best_fe"]) if c["best_fe"] is not None else "N/A"
                line += f"{c['passes']}p/{c['fails']}f/{c['crashes']}c FE={fe}".rjust(22)
        print(line)

    print()
    cw_vals = [
        r[0]
        for r in db.execute(
            f"SELECT DISTINCT p_curvature_weight FROM runs WHERE {w} ORDER BY 1", p
        )
    ]
    print(
        f"CW values explored ({len(cw_vals)}):"
        f" {cw_vals[:8]}{'...' if len(cw_vals) > 8 else ''}"
    )
    ct_vals = [
        r[0]
        for r in db.execute(
            f"SELECT DISTINCT p_curvature_threshold FROM runs WHERE {w} ORDER BY 1", p
        )
    ]
    print(f"CT values explored ({len(ct_vals)}): {ct_vals}")

    ss_w = f"{w} AND solver='single-stage'"
    ss = db.execute(f"SELECT COUNT(*) FROM runs WHERE {ss_w}", p).fetchone()[0]
    if ss:
        print(f"\nSingle-stage runs: {ss}")
        rw = [
            r[0]
            for r in db.execute(
                f"SELECT DISTINCT p_res_weight FROM runs WHERE {ss_w} ORDER BY 1", p
            )
        ]
        print(f"  res_weight values: {rw[:8]}{'...' if len(rw) > 8 else ''}")
        iw = [
            r[0]
            for r in db.execute(
                f"SELECT DISTINCT p_iotas_weight FROM runs WHERE {ss_w} ORDER BY 1", p
            )
        ]
        print(f"  iotas_weight values: {iw[:8]}{'...' if len(iw) > 8 else ''}")


def cmd_frontier(args: argparse.Namespace) -> None:
    """Show the Pareto frontier: best results across all dimensions."""
    db = _get_db()
    w, p = _where(solver=args.solver, eq=args.eq, order=args.order)
    w += " AND status='pass' AND score > 0"
    limit = args.top or 15
    p["lim"] = limit

    rows = db.execute(
        f"SELECT * FROM runs WHERE {w} ORDER BY score DESC LIMIT :lim", p,
    ).fetchall()
    if not rows:
        print("No passing runs found with these filters.")
        return

    print(f"=== FRONTIER (top {limit} by score) ===")
    if args.eq:
        print(f"Equilibrium: {args.eq}")
    if args.order:
        print(f"Order: {args.order}")
    print()

    for i, r in enumerate(rows, 1):
        params = _row_params(r)
        fe = _fmt_fe(r["field_error"])
        sc = _fmt_score(r["score"])
        mc = f"{r['max_curvature']:.1f}" if r["max_curvature"] is not None else "?"
        obj = f"J={r['objective_J']:.6f}" if r["objective_J"] is not None else ""
        si = "SI!" if r["self_intersecting"] else ""
        eq = r["equilibrium"] or "?"
        print(f"  {i:2d}. [{eq:8s}] score={sc} FE={fe} MC={mc} {obj} {si}")
        print(f"      {_compact_params(params)}")

    if len(rows) >= 5:
        print()
        print("--- Pattern in top 5 ---")
        top5 = rows[:5]
        print(f"  Orders: {[r['p_order'] for r in top5]}")
        print(f"  CW:     {[r['p_curvature_weight'] for r in top5]}")
        print(f"  CT:     {[r['p_curvature_threshold'] for r in top5]}")
        print(f"  Eq:     {[r['equilibrium'] for r in top5]}")


def cmd_history(args: argparse.Namespace) -> None:
    """What exact (seed, param) combos have been tried for a given equilibrium?"""
    db = _get_db()
    w, p = _where(solver=args.solver, eq=args.eq, order=args.order)

    rows = db.execute(
        f"""
        SELECT {_KEY_GROUP},
            COUNT(*) as cnt,
            SUM(status='pass') as passes,
            SUM(status='fail') as fails,
            SUM(status='crash') as crashes,
            MAX(score) as best_score,
            MIN(field_error) as best_fe,
            params_json
        FROM runs WHERE {w}
        GROUP BY {_KEY_GROUP}
        ORDER BY best_score DESC
        """,
        p,
    ).fetchall()

    if not rows:
        print(f"No runs found for eq={args.eq} order={args.order}")
        return

    total = sum(r["cnt"] for r in rows)
    eq_label = args.eq or "all"
    order_label = f" order={args.order}" if args.order else ""
    print(
        f"=== HISTORY for {eq_label}{order_label}"
        f" ({total} runs, {len(rows)} unique combos) ==="
    )
    print()

    for i, r in enumerate(rows, 1):
        n_pass, n_fail, n_crash = r["passes"], r["fails"], r["crashes"]
        if r["cnt"] <= 3:
            segs = []
            if n_pass:
                segs.append(f"{n_pass}p")
            if n_fail:
                segs.append(f"{n_fail}f")
            if n_crash:
                segs.append(f"{n_crash}c")
            status_str = "/".join(segs) or "0"
        else:
            status_str = f"{n_pass}p/{n_fail}f/{n_crash}c"
        params = _row_params(r)
        print(
            f"  {i:3d}. [{status_str:12s}] score={_fmt_score(r['best_score'])}"
            f" FE={_fmt_fe(r['best_fe'])}  |  {_compact_params(params)}"
        )
        if i >= 50:
            remaining = len(rows) - 50
            if remaining > 0:
                print(f"  ... and {remaining} more combos")
            break


def cmd_nearby(args: argparse.Namespace) -> None:
    """What's been tried near a specific param value? Finds neighboring experiments."""
    db = _get_db()
    w, p = _where(solver=args.solver, eq=args.eq, order=args.order)
    rows = db.execute(f"SELECT * FROM runs WHERE {w}", p).fetchall()

    if not rows:
        print("No matching runs.")
        return

    def distance(r: sqlite3.Row) -> float:
        d = 0.0
        if args.cw is not None:
            cw = r["p_curvature_weight"] or 0
            if cw > 0 and args.cw > 0:
                d += (math.log10(cw) - math.log10(args.cw)) ** 2
        if args.ct is not None:
            ct = r["p_curvature_threshold"] or 0
            d += ((ct - args.ct) / 10.0) ** 2
        if args.ccw is not None:
            ccw = r["p_cc_weight"] or 0
            d += ((ccw - args.ccw) / 20.0) ** 2
        return math.sqrt(d)

    scored = sorted(((distance(r), r) for r in rows), key=lambda x: x[0])
    limit = args.top or 20

    query_parts = []
    if args.cw is not None:
        query_parts.append(f"CW={args.cw}")
    if args.ct is not None:
        query_parts.append(f"CT={args.ct}")
    if args.ccw is not None:
        query_parts.append(f"CCW={args.ccw}")

    print(
        f"=== NEARBY {' '.join(query_parts)}"
        f" (eq={args.eq or 'all'}, order={args.order or 'all'}) ==="
    )
    print()

    for i, (dist, r) in enumerate(scored[:limit], 1):
        params = _row_params(r)
        eq = r["equilibrium"] or "?"
        print(
            f"  {i:2d}. d={dist:.3f} [{r['status']:5s}]"
            f" score={_fmt_score(r['score'])} FE={_fmt_fe(r['field_error'])}"
            f"  [{eq}] {_compact_params(params)}"
        )


def cmd_crashes(args: argparse.Namespace) -> None:
    """Analyze crash patterns: what param regions crash and why?"""
    db = _get_db()
    w, p = _where(eq=args.eq)

    counts = {
        r["status"]: r["cnt"]
        for r in db.execute(
            f"SELECT status, COUNT(*) as cnt FROM runs WHERE {w} GROUP BY status", p
        )
    }

    print(f"=== CRASH ANALYSIS (eq={args.eq or 'all'}) ===")
    print(
        f"Total: {counts.get('pass', 0)} pass,"
        f" {counts.get('fail', 0)} fail (SI),"
        f" {counts.get('crash', 0)} crash"
    )
    print()

    if not counts.get("crash") and not counts.get("fail"):
        print("No crashes or fails to analyze.")
        return

    # Crash reason clustering
    crashes = db.execute(
        f"SELECT * FROM runs WHERE {w} AND status='crash'", p
    ).fetchall()
    if crashes:
        reason_groups: dict[str, list[sqlite3.Row]] = {}
        for r in crashes:
            fb = (r["feedback"] or "").lower()
            if "boozer" in fb or "goes back" in fb:
                reason = "boozer_surface"
            elif "timeout" in fb:
                reason = "timeout"
            elif "pre-check" in fb:
                reason = "boozer_precheck"
            elif "exit code" in fb:
                reason = "solver_error"
            else:
                reason = "other"
            reason_groups.setdefault(reason, []).append(r)

        print("Crash reasons:")
        for reason, group in sorted(reason_groups.items(), key=lambda x: -len(x[1])):
            orders = sorted(set(r["p_order"] for r in group))
            cws = sorted(set(r["p_curvature_weight"] for r in group))[:5]
            eqs = sorted(set(r["equilibrium"] for r in group))[:5]
            print(f"  {reason}: {len(group)} crashes")
            print(f"    orders={orders}, CW={cws}, eq={eqs}")
        print()

    # Self-intersection analysis
    fails = db.execute(
        f"SELECT * FROM runs WHERE {w} AND status='fail'", p
    ).fetchall()
    if fails:
        print(f"Self-intersection failures ({len(fails)}):")
        si_orders: dict[int, int] = {}
        si_cw: dict[str, int] = {}
        for r in fails:
            o = r["p_order"] or 0
            si_orders[o] = si_orders.get(o, 0) + 1
            cw = r["p_curvature_weight"] or 0
            bucket = (
                "CW<=0.001" if cw <= 0.001
                else ("CW 0.001-0.005" if cw <= 0.005 else "CW>0.005")
            )
            si_cw[bucket] = si_cw.get(bucket, 0) + 1
        print(f"  By order: {dict(sorted(si_orders.items()))}")
        print(f"  By CW range: {si_cw}")

    # Crash-prone vs safe regions
    passes = db.execute(
        f"SELECT * FROM runs WHERE {w} AND status='pass'", p
    ).fetchall()
    bad = list(crashes) + list(fails)
    if passes and bad:
        print()
        print("Crash-prone vs safe param regions:")
        for col, label in [
            ("p_curvature_weight", "CW"),
            ("p_order", "order"),
            ("p_curvature_threshold", "CT"),
        ]:
            pass_vals = [r[col] for r in passes if r[col] is not None]
            bad_vals = [r[col] for r in bad if r[col] is not None]
            if pass_vals and bad_vals:
                pm = sum(pass_vals) / len(pass_vals)
                bm = sum(bad_vals) / len(bad_vals)
                print(f"  {label}: pass_mean={pm:.5f}, crash_mean={bm:.5f}")


def cmd_suggest(args: argparse.Namespace) -> None:
    """Suggest next experiments based on exploration gaps and promising regions.

    Strategy (inspired by acquisition functions in Bayesian optimization):
    1. EXPLOIT: Perturb best-scoring params -- highest value, always slot 1
    2. TRANSFER: Port winning params to under-explored equilibria
    3. BOUNDARY: Probe the pass/fail frontier to map safe regions
    4. EXPLORE: Fill empty (eq, order) cells with a baseline run
    """
    db = _get_db()
    budget = args.budget or 3
    solver_filter = args.solver
    w, p = _where(solver=solver_filter)

    suggestions: list[tuple[float, str, str]] = []

    # Top distinct passing runs (deduplicated by key params)
    all_passes = db.execute(
        f"SELECT * FROM runs WHERE {w} AND status='pass' AND score > 0"
        " ORDER BY score DESC",
        p,
    ).fetchall()
    seen_keys: set[tuple] = set()
    top_distinct: list[sqlite3.Row] = []
    for r in all_passes:
        key = tuple(r[c] for c in _KEY_COLS)
        if key not in seen_keys:
            seen_keys.add(key)
            top_distinct.append(r)
        if len(top_distinct) >= 3:
            break

    # --- Strategy 1: EXPLOIT ---
    for rank, best in enumerate(top_distinct):
        cw = best["p_curvature_weight"] or 0.005
        order = best["p_order"] or 4
        eq = best["equilibrium"] or "iota15"
        ccw = best["p_cc_weight"] or 50.0

        for mult in [0.7, 1.4]:
            new_cw = round(cw * mult, 6)
            if not _exists(db, eq, order, new_cw, solver_filter):
                pri = 5.0 - rank * 0.3
                suggestions.append((
                    pri,
                    "EXPLOIT",
                    f"Perturb rank-{rank+1} best: eq={eq} o={order}"
                    f" CW={new_cw} CCW={ccw}"
                    f" (was CW={cw}, score={best['score']:.4f})."
                    f" Run: --equilibrium {eq} --order {order}"
                    f" --curvature-weight {new_cw} --cc-weight {ccw}",
                ))

    # --- Strategy 2: TRANSFER ---
    if top_distinct:
        best = top_distinct[0]
        best_cw = best["p_curvature_weight"]
        best_order = best["p_order"]
        best_eq = best["equilibrium"]

        for eq in ALL_EQS:
            if eq == best_eq:
                continue
            if not _exists(db, eq, best_order, best_cw, solver_filter):
                eq_count = db.execute(
                    "SELECT COUNT(*) FROM runs WHERE equilibrium=?", (eq,),
                ).fetchone()[0]
                pri = 4.0 + (0.5 if eq_count < 5 else 0)
                suggestions.append((
                    pri,
                    "TRANSFER",
                    f"Port best params to eq={eq} ({eq_count} runs so far):"
                    f" --equilibrium {eq} --order {best_order}"
                    f" --curvature-weight {best_cw}",
                ))

    # --- Strategy 3: BOUNDARY ---
    fail_rows = db.execute(
        f"SELECT * FROM runs WHERE {w} AND status='fail' LIMIT 8", p,
    ).fetchall()
    for fr in fail_rows:
        f_cw = fr["p_curvature_weight"] or 0
        f_order = fr["p_order"] or 0
        f_eq = fr["equilibrium"] or ""
        nearest = db.execute(
            "SELECT p_curvature_weight FROM runs"
            " WHERE equilibrium=:eq AND p_order=:order AND status='pass'"
            " AND p_curvature_weight IS NOT NULL"
            " ORDER BY ABS(p_curvature_weight - :cw) LIMIT 1",
            {"eq": f_eq, "order": f_order, "cw": f_cw},
        ).fetchone()
        if nearest:
            np_cw = nearest[0]
            mid_cw = round((f_cw + np_cw) / 2, 6)
            if not _exists(db, f_eq, f_order, mid_cw, solver_filter):
                suggestions.append((
                    3.5,
                    "BOUNDARY",
                    f"Pass/fail bisect: eq={f_eq} o={f_order} CW={mid_cw}"
                    f" (pass@CW={np_cw} vs fail@CW={f_cw})",
                ))

    # --- Strategy 4: EXPLORE ---
    explore_solver = solver_filter or "stage2"
    for eq in ALL_EQS:
        for order in [3, 4, 5]:
            count = db.execute(
                "SELECT COUNT(*) FROM runs"
                " WHERE solver=? AND equilibrium=? AND p_order=?",
                (explore_solver, eq, order),
            ).fetchone()[0]
            if count == 0:
                suggestions.append((
                    2.5,
                    "EXPLORE",
                    f"eq={eq} o={order} -- zero {explore_solver} runs. Try:"
                    f" --equilibrium {eq} --order {order}"
                    f" --curvature-weight 0.005 --curvature-threshold 20",
                ))
            elif count <= 2:
                suggestions.append((
                    1.5,
                    "EXPLORE",
                    f"eq={eq} o={order} -- only {count} {explore_solver} runs,"
                    f" low confidence.",
                ))

    # Deduplicate and print
    suggestions.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    deduped: list[tuple[float, str, str]] = []
    for s in suggestions:
        short = s[2][:60]
        if short not in seen:
            seen.add(short)
            deduped.append(s)

    print(f"=== SUGGESTED EXPERIMENTS (budget={budget}) ===")
    print()
    for i, (pri, label, desc) in enumerate(deduped[:budget], 1):
        print(f"  {i}. [{label:10s}] (pri {pri:.1f}) {desc}")
    if len(deduped) > budget:
        print(
            f"\n  ({len(deduped) - budget} more suggestions available,"
            f" increase --budget)"
        )


def cmd_diff(args: argparse.Namespace) -> None:
    """Compare results between two equilibria."""
    db = _get_db()

    print(f"=== DIFF: {args.eq} vs {args.eq2} ===")
    print()

    for eq in [args.eq, args.eq2]:
        row = db.execute(
            """
            SELECT
                SUM(status='pass') as passes,
                SUM(status='fail') as fails,
                SUM(status='crash') as crashes,
                MIN(CASE WHEN status='pass' THEN score END) as min_score,
                MAX(CASE WHEN status='pass' THEN score END) as max_score,
                MIN(CASE WHEN status='pass' THEN field_error END) as min_fe,
                MAX(CASE WHEN status='pass' THEN field_error END) as max_fe
            FROM runs WHERE equilibrium=?
            """,
            (eq,),
        ).fetchone()
        if not row or (row["passes"] or 0) + (row["fails"] or 0) + (row["crashes"] or 0) == 0:
            print(f"  {eq:10s}: no runs")
            continue
        min_sc = _fmt_score(row["min_score"])
        max_sc = _fmt_score(row["max_score"])
        min_fe = _fmt_fe(row["min_fe"])
        max_fe = _fmt_fe(row["max_fe"])
        print(
            f"  {eq:10s}: {row['passes'] or 0}p/{row['fails'] or 0}f/{row['crashes'] or 0}c"
            f" | score {min_sc}-{max_sc} | FE {min_fe}-{max_fe}"
        )

    # Set operations on (order, CW, CT) combos
    combo_sql = (
        "SELECT p_order, p_curvature_weight, p_curvature_threshold"
        " FROM runs WHERE equilibrium=?"
    )
    k1 = {tuple(r) for r in db.execute(combo_sql, (args.eq,))}
    k2 = {tuple(r) for r in db.execute(combo_sql, (args.eq2,))}

    only1 = k1 - k2
    only2 = k2 - k1
    both = k1 & k2

    print(f"\n  Shared (order, CW, CT) combos: {len(both)}")
    print(f"  Only in {args.eq}: {len(only1)}")
    print(f"  Only in {args.eq2}: {len(only2)}")

    if only1:
        print(f"\n  Combos in {args.eq} not tried in {args.eq2}:")
        for o, cw, ct in sorted(only1)[:10]:
            print(f"    order={o} CW={cw} CT={ct}")
        if len(only1) > 10:
            print(f"    ... and {len(only1) - 10} more")


def cmd_seeds(args: argparse.Namespace) -> None:
    """List available Stage 2 seeds by scanning the filesystem."""
    seeds: list[dict] = []
    for search_root, source in [
        (STAGE2_SEED_STORE, "autoresearch"),
        (COLUMBIA_DATABASE, "columbia"),
    ]:
        if not search_root.is_dir():
            continue
        for eq_dir in search_root.iterdir():
            if not eq_dir.is_dir():
                continue
            eq_name = eq_dir.name  # "outputs-wout_...nc"
            for seed_dir in eq_dir.iterdir():
                if not seed_dir.is_dir():
                    continue
                bs_file = seed_dir / "biot_savart_opt.json"
                results_file = seed_dir / "results.json"
                if not bs_file.is_file():
                    continue
                meta: dict = {}
                if results_file.is_file():
                    try:
                        meta = json.loads(results_file.read_text())
                    except json.JSONDecodeError:
                        continue
                seeds.append({
                    "path": str(bs_file),
                    "eq_dir": eq_name,
                    "source": source,
                    "fe": meta.get("FIELD_ERROR"),
                    "order": meta.get("order"),
                    "mr": meta.get("MAJOR_RADIUS"),
                    "si": meta.get("SELF_INTERSECTING", False),
                })

    # Filter
    if args.eq:
        seeds = [s for s in seeds if args.eq in s["eq_dir"]]
    if args.order is not None:
        seeds = [s for s in seeds if s["order"] == args.order]

    # Remove self-intersecting
    seeds = [s for s in seeds if not s["si"]]

    # Sort by field error (best first), None last
    seeds.sort(key=lambda s: s["fe"] if s["fe"] is not None else float("inf"))

    if args.best:
        seeds = seeds[:1]

    if not seeds:
        print("No seeds found.")
        return

    print(f"=== SEEDS ({len(seeds)} available) ===")
    print()
    for i, s in enumerate(seeds[:30], 1):
        fe = _fmt_fe(s["fe"])
        print(
            f"  {i:3d}. FE={fe} o={s['order']} MR={s['mr']}"
            f" [{s['source']}]"
        )
        print(f"       {s['path']}")
    if len(seeds) > 30:
        print(f"  ... and {len(seeds) - 30} more")


def cmd_sql(args: argparse.Namespace) -> None:
    """Run an arbitrary SQL query against the runs table."""
    db = _get_db()
    sql = args.query
    if not sql.strip().upper().startswith("SELECT"):
        print("Only SELECT queries are allowed.", file=sys.stderr)
        sys.exit(1)
    try:
        rows = db.execute(sql).fetchall()
    except sqlite3.OperationalError as e:
        print(f"SQL error: {e}", file=sys.stderr)
        sys.exit(1)
    if not rows:
        print("(no results)")
        return
    cols = rows[0].keys()
    print("\t".join(cols))
    for r in rows:
        print("\t".join(str(r[c]) for c in cols))


def cmd_schema(args: argparse.Namespace) -> None:
    """Print the runs table schema for reference."""
    _get_db()
    print(_SCHEMA.strip())


def cmd_param_effect(args: argparse.Namespace) -> None:
    """How does a single parameter affect outcomes? Bucket analysis."""
    db = _get_db()
    w, p = _where(solver=args.solver, eq=args.eq)
    param = args.param

    col_map = {f: f"p_{f}" for f in _PARAM_FIELDS}

    rows = db.execute(f"SELECT * FROM runs WHERE {w}", p).fetchall()

    # Extract (value, row) pairs
    col = col_map.get(param)
    pairs: list[tuple[float, sqlite3.Row]] = []
    for r in rows:
        if col:
            v = r[col]
        else:
            v = _row_params(r).get(param)
        if v is not None:
            pairs.append((v, r))

    if not pairs:
        print(f"No results with param '{param}'")
        return

    pairs.sort(key=lambda x: x[0])
    values = sorted(set(v for v, _ in pairs))

    print(f"=== EFFECT OF {param} (eq={args.eq or 'all'}, {len(pairs)} runs) ===")
    print()

    def _bucket_stats(bucket: list) -> tuple[int, float, float, str]:
        passes = [r for r in bucket if r["status"] == "pass"]
        pass_rate = len(passes) / len(bucket) * 100
        avg_score = sum(r["score"] or 0 for r in passes) / max(len(passes), 1)
        fes = [r["field_error"] for r in passes if r["field_error"] is not None]
        return len(bucket), pass_rate, avg_score, _fmt_fe(min(fes) if fes else None)

    is_numeric = isinstance(values[0], (int, float))

    if not is_numeric or len(values) <= 15:
        print(f"{'value':>12s}  {'runs':>5s}  {'pass%':>6s}  {'avg_score':>10s}  {'best_FE':>10s}")
        print("-" * 55)
        for v in values:
            bucket = [r for val, r in pairs if val == v]
            n, pr, avg, fe = _bucket_stats(bucket)
            v_str = f"{v:>12g}" if is_numeric else f"{v!s:>12s}"
            print(f"{v_str}  {n:>5d}  {pr:>5.0f}%  {avg:>10.4f}  {fe:>10s}")
    else:
        bucket_size = max(1, len(pairs) // 8)
        print(
            f"{'value_range':>20s}  {'runs':>5s}  {'pass%':>6s}"
            f"  {'avg_score':>10s}  {'best_FE':>10s}"
        )
        print("-" * 65)
        for bi in range(0, len(pairs), bucket_size):
            chunk = pairs[bi : bi + bucket_size]
            n, pr, avg, fe = _bucket_stats([r for _, r in chunk])
            range_str = f"{chunk[0][0]:.5f}-{chunk[-1][0]:.5f}"
            print(f"{range_str:>20s}  {n:>5d}  {pr:>5.0f}%  {avg:>10.4f}  {fe:>10s}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lab notebook: query the stellarator experiment space",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- check ---
    p = sub.add_parser("check", help="Has this exact combo been tried?")
    p.add_argument("--eq", help="Equilibrium name")
    p.add_argument("--order", type=int, help="Fourier order")
    p.add_argument("--cw", type=float, help="Curvature weight (exact match)")

    # --- coverage ---
    p = sub.add_parser("coverage", help="Coverage heatmap: what has been explored?")
    p.add_argument("--solver", choices=["stage2", "single-stage"])

    # --- frontier ---
    p = sub.add_parser("frontier", help="Pareto frontier: best results")
    p.add_argument("--solver", choices=["stage2", "single-stage"])
    p.add_argument("--eq", help="Filter by equilibrium")
    p.add_argument("--order", type=int, help="Filter by order")
    p.add_argument("--top", type=int, default=15, help="How many to show")

    # --- history ---
    p = sub.add_parser("history", help="Full history for an equilibrium")
    p.add_argument("--eq", help="Equilibrium name (e.g., iota15)")
    p.add_argument("--solver", choices=["stage2", "single-stage"])
    p.add_argument("--order", type=int, help="Filter by order")

    # --- nearby ---
    p = sub.add_parser("nearby", help="Find experiments near a param value")
    p.add_argument("--eq", help="Filter by equilibrium")
    p.add_argument("--solver", choices=["stage2", "single-stage"])
    p.add_argument("--order", type=int, help="Filter by order")
    p.add_argument("--cw", type=float, help="Curvature weight to search near")
    p.add_argument("--ct", type=float, help="Curvature threshold to search near")
    p.add_argument("--ccw", type=float, help="CC weight to search near")
    p.add_argument("--top", type=int, default=20)

    # --- crashes ---
    p = sub.add_parser("crashes", help="Analyze crash/fail patterns")
    p.add_argument("--eq", help="Filter by equilibrium")

    # --- suggest ---
    p = sub.add_parser("suggest", help="Suggest next experiments")
    p.add_argument("--budget", type=int, default=3, help="How many suggestions")
    p.add_argument("--solver", choices=["stage2", "single-stage"])

    # --- diff ---
    p = sub.add_parser("diff", help="Compare two equilibria")
    p.add_argument("--eq", required=True, help="First equilibrium")
    p.add_argument("--eq2", required=True, help="Second equilibrium")

    # --- param-effect ---
    p = sub.add_parser(
        "param-effect", help="How does a parameter affect outcomes?"
    )
    p.add_argument(
        "--param", required=True,
        help="Parameter name (e.g., order, curvature_weight)",
    )
    p.add_argument("--eq", help="Filter by equilibrium")
    p.add_argument("--solver", choices=["stage2", "single-stage"])

    # --- seeds ---
    p = sub.add_parser("seeds", help="List available Stage 2 seeds (filesystem scan)")
    p.add_argument("--eq", help="Filter by equilibrium name (substring match)")
    p.add_argument("--order", type=int, help="Filter by Fourier order")
    p.add_argument("--best", action="store_true", help="Show only the single lowest-FE seed")

    # --- sql ---
    p = sub.add_parser("sql", help="Run arbitrary SELECT on the runs table")
    p.add_argument("query", help="SQL query (SELECT only)")

    # --- schema ---
    sub.add_parser("schema", help="Print the runs table schema")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "check": cmd_check,
        "coverage": cmd_coverage,
        "frontier": cmd_frontier,
        "history": cmd_history,
        "nearby": cmd_nearby,
        "crashes": cmd_crashes,
        "suggest": cmd_suggest,
        "diff": cmd_diff,
        "param-effect": cmd_param_effect,
        "seeds": cmd_seeds,
        "sql": cmd_sql,
        "schema": cmd_schema,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
