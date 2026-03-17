#!/usr/bin/env python3
"""Run a single HBT experiment (Stage 2 or single-stage) with isolated output.

Usage:
    # Stage 2 (default)
    python scripts/run_one.py --cc-weight 44 --curvature-weight 0.00085

    # Single-stage
    python scripts/run_one.py --solver single-stage --equilibrium iota20 \
        --iota-target 0.20 --vol-target 0.10 --mpol 8

    # Different equilibrium
    python scripts/run_one.py --equilibrium iota20

Prints a single-line JSON result to stdout. Handles output isolation, result
parsing, scoring, and cleanup so the agent never touches the filesystem.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Paths
PYTHON = "/Users/suhjungdae/code/hbt-compare/envs/candidate-fixed/bin/python"
SIMSOPT_ROOT = Path("/Users/suhjungdae/code/hbt-compare/wt/candidate-fixed")
EQUILIBRIA = Path("/Users/suhjungdae/code/columbia/DATABASE/EQUILIBRIA")
OUTPUT_BASE = Path("/tmp/hbt_autoresearch")

SOLVERS = {
    "stage2": SIMSOPT_ROOT
    / "examples"
    / "single_stage_optimization"
    / "STAGE_2"
    / "banana_coil_solver.py",
    "single-stage": SIMSOPT_ROOT
    / "examples"
    / "single_stage_optimization"
    / "SINGLE_STAGE"
    / "single_stage_banana_example.py",
}

EQUILIBRIUM_FILES = {
    "iota15": "wout_nfp22ginsburg_000_014417_iota15.nc",
    "iota20": "wout_nfp22ginsburg_000_002084_iota20.nc",
    "001490": "wout_nfp22ginsburg_000_001490.nc",
}


def score_stage2(metrics: dict, args: argparse.Namespace) -> float:
    """Stage 2 scoring: field error + curvature excess + SI penalty."""
    fe = metrics.get("FIELD_ERROR")
    if fe is None or (isinstance(fe, float) and math.isnan(fe)):
        return 0.0
    penalty = 25.0 * fe
    ct = args.curvature_threshold
    mc = metrics.get("MAX_CURVATURE")
    if mc is not None and mc > ct:
        penalty += (mc - ct) / max(ct, 1.0)
    if metrics.get("SELF_INTERSECTING", False):
        penalty += 5.0
    return 1.0 / (1.0 + penalty)


def score_single_stage(metrics: dict, args: argparse.Namespace) -> float:
    """Single-stage scoring: field error + iota/volume miss + Boozer + curvature + SI."""
    fe = metrics.get("FIELD_ERROR")
    if fe is None or (isinstance(fe, float) and math.isnan(fe)):
        return 0.0
    penalty = 25.0 * fe

    # Iota miss
    final_iota = metrics.get("FINAL_IOTA")
    target_iota = metrics.get("TARGET_IOTA", args.iota_target)
    if final_iota is not None:
        penalty += 4.0 * abs(final_iota - target_iota)
    else:
        penalty += 1.0

    # Volume miss
    final_vol = metrics.get("FINAL_VOLUME")
    target_vol = metrics.get("TARGET_VOLUME", args.vol_target)
    if final_vol is not None:
        penalty += 8.0 * abs(final_vol - target_vol)
    else:
        penalty += 1.0

    # Curvature excess
    ct = args.curvature_threshold
    mc = metrics.get("MAX_CURVATURE")
    if mc is not None and mc > ct:
        penalty += (mc - ct) / max(ct, 1.0)

    if metrics.get("SELF_INTERSECTING", False):
        penalty += 5.0

    return 1.0 / (1.0 + penalty)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one HBT experiment (stage2 or single-stage)"
    )

    # Solver and equilibrium selection
    parser.add_argument(
        "--solver",
        choices=["stage2", "single-stage"],
        default="stage2",
        help="Which solver to run (default: stage2).",
    )
    parser.add_argument(
        "--equilibrium",
        default="iota15",
        help="Equilibrium to use. Shorthand: iota15, iota20, 001490. Or a direct .nc filename/path.",
    )

    # --- Shared params (both solvers) ---
    parser.add_argument("--cc-weight", type=float, default=44.0)
    parser.add_argument("--cc-threshold", type=float, default=0.05)
    parser.add_argument("--curvature-weight", type=float, default=0.00085)
    parser.add_argument("--curvature-threshold", type=float, default=30.0)
    parser.add_argument("--banana-surf-radius", type=float, default=0.22)
    parser.add_argument("--major-radius", type=float, default=0.915)
    parser.add_argument("--toroidal-flux", type=float, default=0.215)
    parser.add_argument("--order", type=int, default=2)
    parser.add_argument("--maxiter", type=int, default=400)
    parser.add_argument("--nphi", type=int, default=127)
    parser.add_argument("--ntheta", type=int, default=32)

    # --- Stage 2 only ---
    parser.add_argument(
        "--length-weight",
        type=float,
        default=0.0001,
        help="Stage 2: curve length penalty weight.",
    )
    parser.add_argument("--length-target", type=float, default=1.75)
    parser.add_argument("--theta-center", type=float, default=0.5)
    parser.add_argument("--phi-center", type=float, default=0.06)
    parser.add_argument("--theta-width", type=float, default=0.1)
    parser.add_argument("--phi-width", type=float, default=0.03)
    parser.add_argument("--ftol", type=float, default=1e-15)
    parser.add_argument("--gtol", type=float, default=1e-15)
    parser.add_argument(
        "--squared-flux-weight",
        type=float,
        default=1.0,
        help="Stage 2: weight on SquaredFlux term.",
    )
    parser.add_argument(
        "--curvature-p-norm",
        type=int,
        default=4,
        help="Stage 2: Lp norm exponent for curvature penalty.",
    )
    parser.add_argument(
        "--num-quadpoints",
        type=int,
        default=128,
        help="Stage 2: coil discretization points.",
    )

    # --- Single-stage only ---
    parser.add_argument("--iota-target", type=float, default=0.15)
    parser.add_argument("--vol-target", type=float, default=0.10)
    parser.add_argument("--mpol", type=int, default=8)
    parser.add_argument("--ntor", type=int, default=6)
    parser.add_argument("--constraint-weight", type=float, default=1.0)
    parser.add_argument(
        "--cc-dist",
        type=float,
        default=0.05,
        help="Single-stage: coil-coil min distance.",
    )
    parser.add_argument(
        "--res-weight",
        type=float,
        default=1000.0,
        help="Single-stage: Boozer residual weight.",
    )
    parser.add_argument(
        "--iotas-weight",
        type=float,
        default=100.0,
        help="Single-stage: iota tracking weight.",
    )
    parser.add_argument(
        "--cs-weight",
        type=float,
        default=1.0,
        help="Single-stage: coil-surface distance weight.",
    )
    parser.add_argument(
        "--cs-dist",
        type=float,
        default=0.02,
        help="Single-stage: coil-surface min distance.",
    )
    parser.add_argument(
        "--surf-dist-weight",
        type=float,
        default=1000.0,
        help="Single-stage: surface-vessel distance weight.",
    )
    parser.add_argument(
        "--ss-dist",
        type=float,
        default=0.04,
        help="Single-stage: surface-vessel min distance.",
    )
    parser.add_argument(
        "--ss-length-weight",
        type=float,
        default=1.0,
        help="Single-stage: curve length weight.",
    )
    parser.add_argument(
        "--maxcor", type=int, default=300, help="Single-stage: L-BFGS-B memory."
    )
    parser.add_argument(
        "--boozer-stage", choices=["initial", "final"], default="initial"
    )
    parser.add_argument("--num-tf-coils", type=int, default=20)
    # Stage 2 seed params for single-stage
    parser.add_argument(
        "--stage2-source", choices=["database", "local"], default="database"
    )
    parser.add_argument("--stage2-bs-path", type=str, default=None)
    parser.add_argument("--database-stage2-root", type=str, default=None)

    # --- Execution ---
    parser.add_argument("--omp-threads", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=600)

    args = parser.parse_args()

    # Resolve solver and equilibrium
    solver_script = SOLVERS[args.solver]
    plasma_surf = EQUILIBRIUM_FILES.get(args.equilibrium, args.equilibrium)

    # Build CLI args for the solver
    cli_args = _build_cli_args(args, plasma_surf)

    # Unique output dir
    run_id = f"run_{int(time.time() * 1000)}"
    run_dir = OUTPUT_BASE / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    cli_args.extend(["--output-root", str(run_dir)])

    cmd = [PYTHON, str(solver_script)] + cli_args

    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = str(args.omp_threads)
    env["MKL_NUM_THREADS"] = str(args.omp_threads)
    env["OPENBLAS_NUM_THREADS"] = str(args.omp_threads)

    log_path = run_dir / "run.log"
    t0 = time.monotonic()

    try:
        with open(log_path, "w") as log_file:
            result = subprocess.run(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=args.timeout,
            )
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        _emit_error(f"timeout after {args.timeout}s", time.monotonic() - t0, args)
        return
    except Exception as exc:
        _emit_error(str(exc), time.monotonic() - t0, args)
        return

    elapsed = time.monotonic() - t0

    if returncode != 0:
        tail = ""
        if log_path.exists():
            tail = "\n".join(log_path.read_text().splitlines()[-10:])
        _emit_error(f"exit code {returncode}: {tail[:300]}", elapsed, args)
        return

    # Parse results.json
    results_files = list(run_dir.rglob("results.json"))
    if not results_files:
        _emit_error("no results.json found", elapsed, args)
        return

    with open(results_files[0]) as f:
        metrics = json.load(f)

    # Score based on solver
    if args.solver == "stage2":
        score = score_stage2(metrics, args)
    else:
        score = score_single_stage(metrics, args)

    output = {
        "solver": args.solver,
        "equilibrium": args.equilibrium,
        "status": "fail" if metrics.get("SELF_INTERSECTING", False) else "pass",
        "field_error": metrics.get("FIELD_ERROR"),
        "self_intersecting": metrics.get("SELF_INTERSECTING", False),
        "max_curvature": metrics.get("MAX_CURVATURE"),
        "score": round(score, 6),
        "iterations": metrics.get("iterations"),
        "elapsed": round(elapsed, 1),
    }

    # Single-stage extra fields
    if args.solver == "single-stage":
        output["final_iota"] = metrics.get("FINAL_IOTA")
        output["final_volume"] = metrics.get("FINAL_VOLUME")
        output["target_iota"] = metrics.get("TARGET_IOTA")
        output["target_volume"] = metrics.get("TARGET_VOLUME")

    print(json.dumps(output))
    shutil.rmtree(run_dir, ignore_errors=True)


def _build_cli_args(args: argparse.Namespace, plasma_surf: str) -> list[str]:
    """Build solver CLI args based on --solver type."""
    common = [
        "--plasma-surf-filename",
        plasma_surf,
        "--equilibria-dir",
        str(EQUILIBRIA),
        "--nphi",
        str(args.nphi),
        "--ntheta",
        str(args.ntheta),
        "--maxiter",
        str(args.maxiter),
        "--cc-weight",
        str(args.cc_weight),
        "--curvature-weight",
        str(args.curvature_weight),
        "--curvature-threshold",
        str(args.curvature_threshold),
        "--banana-surf-radius",
        str(args.banana_surf_radius),
        "--major-radius",
        str(args.major_radius),
        "--toroidal-flux",
        str(args.toroidal_flux),
        "--order",
        str(args.order),
    ]

    if args.solver == "stage2":
        return common + [
            "--cc-threshold",
            str(args.cc_threshold),
            "--length-weight",
            str(args.length_weight),
            "--length-target",
            str(args.length_target),
            "--theta-center",
            str(args.theta_center),
            "--phi-center",
            str(args.phi_center),
            "--theta-width",
            str(args.theta_width),
            "--phi-width",
            str(args.phi_width),
            "--ftol",
            str(args.ftol),
            "--gtol",
            str(args.gtol),
            "--squared-flux-weight",
            str(args.squared_flux_weight),
            "--curvature-p-norm",
            str(args.curvature_p_norm),
            "--num-quadpoints",
            str(args.num_quadpoints),
        ]
    else:
        cli = common + [
            "--cc-dist",
            str(args.cc_dist),
            "--iota-target",
            str(args.iota_target),
            "--vol-target",
            str(args.vol_target),
            "--mpol",
            str(args.mpol),
            "--ntor",
            str(args.ntor),
            "--constraint-weight",
            str(args.constraint_weight),
            "--boozer-stage",
            args.boozer_stage,
            "--num-tf-coils",
            str(args.num_tf_coils),
            "--length-weight",
            str(args.ss_length_weight),
            "--res-weight",
            str(args.res_weight),
            "--iotas-weight",
            str(args.iotas_weight),
            "--cs-weight",
            str(args.cs_weight),
            "--cs-dist",
            str(args.cs_dist),
            "--surf-dist-weight",
            str(args.surf_dist_weight),
            "--ss-dist",
            str(args.ss_dist),
            "--maxcor",
            str(args.maxcor),
            "--stage2-source",
            args.stage2_source,
        ]
        if args.stage2_bs_path:
            cli.extend(["--stage2-bs-path", args.stage2_bs_path])
        if args.database_stage2_root:
            cli.extend(["--database-stage2-root", args.database_stage2_root])
        return cli


def _emit_error(reason: str, elapsed: float, args: argparse.Namespace) -> None:
    output = {
        "solver": args.solver,
        "equilibrium": args.equilibrium,
        "status": "crash",
        "field_error": None,
        "self_intersecting": None,
        "max_curvature": None,
        "score": 0.0,
        "iterations": None,
        "elapsed": round(elapsed, 1),
        "feedback": reason,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
