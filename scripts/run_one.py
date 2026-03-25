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
import datetime
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
SIMSOPT_ROOT_ALM = Path("/Users/suhjungdae/code/hbt-compare/wt/alm")
EQUILIBRIA = Path("/Users/suhjungdae/code/columbia/DATABASE/EQUILIBRIA")
OUTPUT_BASE = Path("/tmp/hbt_autoresearch")
STAGE2_SEED_STORE = REPO_ROOT / "stage2_seeds"
SINGLE_STAGE_STORE = REPO_ROOT / "single_stage_results"

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
    "iota15p": "wout_nfp22ginsburg_desc_iota15.nc",
    "iota16": "wout_nfp22ginsburg_desc_iota16.nc",
    "iota17": "wout_nfp22ginsburg_desc_iota17.nc",
    "iota18": "wout_nfp22ginsburg_desc_iota18.nc",
    "iota19": "wout_nfp22ginsburg_desc_iota19.nc",
    "iota20": "wout_nfp22ginsburg_000_002084_iota20.nc",
    "iota20p": "wout_nfp22ginsburg_desc_iota20.nc",
    "iota21": "wout_nfp22ginsburg_desc_iota21.nc",
    "iota22": "wout_nfp22ginsburg_desc_iota22.nc",
    "iota23": "wout_nfp22ginsburg_desc_iota23.nc",
    "iota24": "wout_nfp22ginsburg_desc_iota24.nc",
    "iota25": "wout_nfp22ginsburg_desc_iota25.nc",
    "iota26": "wout_nfp22ginsburg_desc_iota26.nc",
    "iota27": "wout_nfp22ginsburg_desc_iota27.nc",
    "iota28": "wout_nfp22ginsburg_desc_iota28.nc",
    "iota29": "wout_nfp22ginsburg_desc_iota29.nc",
    "iota30": "wout_nfp22ginsburg_desc_iota30.nc",
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


def _count_concurrent_runs() -> int:
    """Count other run_one.py processes currently running (excluding this one).

    Uses /tmp lockfiles instead of pgrep to avoid false matches from editors/grep.
    """
    lock_dir = OUTPUT_BASE / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    own_lock = lock_dir / f"{os.getpid()}.lock"
    own_lock.write_text(str(os.getpid()))
    # Count other lockfiles whose PIDs are still alive
    count = 0
    for lock_file in lock_dir.glob("*.lock"):
        try:
            pid = int(lock_file.read_text().strip())
            if pid == os.getpid():
                continue
            os.kill(pid, 0)  # Check if process exists (signal 0 = no-op)
            count += 1
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            lock_file.unlink(missing_ok=True)  # Stale lock, clean up
    return count


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
    parser.add_argument("--cc-weight", type=float, default=100.0)
    parser.add_argument("--cc-threshold", type=float, default=0.05)
    parser.add_argument("--curvature-weight", type=float, default=0.0001)
    parser.add_argument("--curvature-threshold", type=float, default=40.0)
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
        default=0.0005,
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

    # --- Basin-hopping (Stage 2 only) ---
    parser.add_argument(
        "--basin-hops",
        type=int,
        default=0,
        help="Stage 2: number of basin-hopping restarts (0 = single L-BFGS-B, default).",
    )
    parser.add_argument(
        "--basin-stepsize",
        type=float,
        default=0.01,
        help="Stage 2: perturbation scale for basin-hopping (default 0.01).",
    )
    parser.add_argument(
        "--basin-seed",
        type=int,
        default=-1,
        help="Stage 2: RNG seed for basin-hopping (-1 = random). Set for reproducibility.",
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
    # Stage 2 seed for single-stage
    parser.add_argument(
        "--stage2-bs-path",
        type=str,
        default=None,
        help="Explicit path to biot_savart_opt.json. Recommended: use stage2_seed_path from a Stage 2 run's JSON output.",
    )

    # --- ALM (single-stage only) ---
    parser.add_argument(
        "--alm", action="store_true",
        help="Single-stage: use Augmented Lagrangian Method for constraint handling.",
    )
    parser.add_argument("--alm-outer-iters", type=int, default=20)
    parser.add_argument("--alm-mu-init", type=float, default=1.0)
    parser.add_argument("--alm-mu-max", type=float, default=1e6)
    parser.add_argument("--alm-mu-increase", type=float, default=5.0)
    parser.add_argument("--alm-tol", type=float, default=1e-6)

    # --- Execution ---
    parser.add_argument("--omp-threads", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=600)

    args = parser.parse_args()

    # Validate ALM constraints
    if args.alm and args.solver == "stage2":
        parser.error("--alm is only supported with --solver single-stage")
    if args.alm and args.basin_hops > 0:
        parser.error("--alm and --basin-hops are mutually exclusive")
    # Auto-bump timeout for ALM (multiple outer iterations need more time)
    if args.alm and args.timeout <= 600:
        args.timeout = 3600
        print("Auto-set --timeout 3600 for ALM mode.", file=sys.stderr)

    # Auto-manage threads: count concurrent run_one.py processes, divide cores fairly.
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    concurrent = _count_concurrent_runs()
    if concurrent > 0:
        total_cores = 10  # Reserve 4 cores for system + other work
        threads_per_run = max(2, total_cores // (concurrent + 1))
        if args.omp_threads > threads_per_run:
            args.omp_threads = threads_per_run
            print(
                f"Auto-reduced to {threads_per_run} threads ({concurrent + 1} concurrent runs on {total_cores} cores).",
                file=sys.stderr,
            )

    try:
        _run_experiment(args)
    finally:
        own_lock = OUTPUT_BASE / ".locks" / f"{os.getpid()}.lock"
        own_lock.unlink(missing_ok=True)


def _run_experiment(args: argparse.Namespace) -> None:
    """Core experiment logic, separated so main() can wrap with try/finally for lockfile cleanup.

    Hardware constraint minimums (cc_threshold >= 0.05, curvature_threshold >= 20,
    length_target >= 1.75) are enforced in the solver code itself, not here.
    """

    # Resolve solver and equilibrium
    # ALM mode uses the alm worktree's single-stage solver
    if args.alm and args.solver == "single-stage":
        solver_script = (
            SIMSOPT_ROOT_ALM / "examples" / "single_stage_optimization"
            / "SINGLE_STAGE" / "single_stage_banana_example.py"
        )
    else:
        solver_script = SOLVERS[args.solver]
    plasma_surf = EQUILIBRIUM_FILES.get(args.equilibrium, args.equilibrium)

    # Build CLI args for the solver
    cli_args = _build_cli_args(args, plasma_surf)
    if not cli_args:
        _emit_error("failed to resolve Stage 2 seed for single-stage", 0.0, args)
        return

    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = str(args.omp_threads)
    env["MKL_NUM_THREADS"] = str(args.omp_threads)
    env["OPENBLAS_NUM_THREADS"] = str(args.omp_threads)

    # Single-stage pre-check: run --init-only first to verify Boozer init works.
    # Skip pre-check when explicit seed path is given (user knows what they're doing).
    if args.solver == "single-stage" and not args.stage2_bs_path:
        precheck_dir = OUTPUT_BASE / f"precheck_{int(time.time() * 1000)}"
        precheck_dir.mkdir(parents=True, exist_ok=True)
        precheck_args = cli_args + ["--init-only", "--output-root", str(precheck_dir)]
        precheck_cmd = [PYTHON, str(solver_script)] + precheck_args
        precheck_log = precheck_dir / "precheck.log"
        try:
            with open(precheck_log, "w") as lf:
                precheck = subprocess.run(
                    precheck_cmd,
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=180,
                )
            if precheck.returncode != 0:
                tail = ""
                if precheck_log.exists():
                    tail = "\n".join(precheck_log.read_text().splitlines()[-15:])
                feedback = "BOOZER INIT PRE-CHECK FAILED (saved ~10-30 min). "
                if "goes back" in tail:
                    feedback += "Surface folds back on itself — seed geometry incompatible. Try a different seed."
                elif "self_intersecting" in tail.lower() or "Self-intersecting" in tail:
                    feedback += "Boozer surface self-intersects. Try a different seed."
                else:
                    feedback += f"Run dir: {precheck_dir}\n{tail[:500]}"
                _emit_error(feedback, 0.0, args)
                shutil.rmtree(precheck_dir, ignore_errors=True)
                return
        except subprocess.TimeoutExpired:
            _emit_error("Boozer init pre-check timed out after 180s", 0.0, args)
            shutil.rmtree(precheck_dir, ignore_errors=True)
            return
        finally:
            shutil.rmtree(precheck_dir, ignore_errors=True)
        print("Boozer init pre-check passed.", file=sys.stderr)

    # Unique output dir
    run_id = f"run_{int(time.time() * 1000)}"
    run_dir = OUTPUT_BASE / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    cli_args.extend(["--output-root", str(run_dir)])

    cmd = [PYTHON, str(solver_script)] + cli_args

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
            tail = "\n".join(log_path.read_text().splitlines()[-30:])
        # Detect the common Boozer surface crash for single-stage
        feedback = f"exit code {returncode}. Run dir: {run_dir}\n{tail[:1000]}"
        if "goes back" in tail and args.solver == "single-stage":
            feedback = (
                f"BOOZER SURFACE CRASH: The Stage 2 seed produces a surface that folds back on itself. "
                f"This is a geometry issue with the seed coil, not the single-stage weights. "
                f"Try a different seed (different Stage 2 params or equilibrium). "
                f"Run dir: {run_dir}"
            )
        _emit_error(feedback, elapsed, args)
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
        "source": "local",
        "solver": args.solver,
        "equilibrium": args.equilibrium,
        "status": "fail" if metrics.get("SELF_INTERSECTING", False) else "pass",
        "score": round(score, 6),
        "field_error": metrics.get("FIELD_ERROR"),
        "self_intersecting": metrics.get("SELF_INTERSECTING", False),
        "max_curvature": metrics.get("MAX_CURVATURE"),
        "iterations": metrics.get("iterations"),
        "elapsed": round(elapsed, 1),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "params": _extract_params(args),
    }

    # Solver objective and physics metrics (new — available when solver writes them)
    output["objective_J"] = metrics.get("OBJECTIVE_J")
    output["termination_message"] = metrics.get("TERMINATION_MESSAGE")
    output["optimizer_success"] = metrics.get("OPTIMIZER_SUCCESS")
    output["ftol"] = metrics.get("FTOL")
    output["gtol"] = metrics.get("GTOL")
    output["curve_curve_min_dist"] = metrics.get("CURVE_CURVE_MIN_DIST")

    # Additional comparison metrics (written by solver to results.json)
    output["coil_length"] = metrics.get("COIL_LENGTH")
    output["curve_surface_min_dist"] = metrics.get("CURVE_SURFACE_MIN_DIST")
    output["surface_vessel_min_dist"] = metrics.get("SURFACE_VESSEL_MIN_DIST")
    output["max_force"] = metrics.get("MAX_FORCE")
    output["lead_end_curvature"] = metrics.get("LEAD_END_CURVATURE")
    output["non_lead_end_curvature"] = metrics.get("NON_LEAD_END_CURVATURE")

    # Single-stage extra fields
    if args.solver == "single-stage":
        output["final_iota"] = metrics.get("FINAL_IOTA")
        output["final_volume"] = metrics.get("FINAL_VOLUME")
        output["target_iota"] = metrics.get("TARGET_IOTA")
        output["target_volume"] = metrics.get("TARGET_VOLUME")
        output["nonqs_ratio"] = metrics.get("NONQS_RATIO")
        output["boozer_residual"] = metrics.get("BOOZER_RESIDUAL")
        # Log which Stage 2 seed was used (resolved or explicit)
        seed = getattr(args, "_resolved_seed_path", None) or args.stage2_bs_path
        if seed:
            output["stage2_seed_path"] = seed

    # For Stage 2: persist all runs as seeds for single-stage
    if args.solver == "stage2":
        bs_files = list(run_dir.rglob("biot_savart_opt.json"))
        results_files = list(run_dir.rglob("results.json"))
        if bs_files:
            ts = int(time.time())
            seed_dir = (
                STAGE2_SEED_STORE
                / f"outputs-{plasma_surf}"
                / f"{bs_files[0].parent.name}-{ts}"
            )
            seed_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bs_files[0], seed_dir / "biot_savart_opt.json")
            if results_files:
                shutil.copy2(results_files[0], seed_dir / "results.json")

            output["stage2_seed_path"] = str(seed_dir / "biot_savart_opt.json")

    # For single-stage: persist all passing run artifacts
    if args.solver == "single-stage" and output.get("status") == "pass":
        ss_files = list(run_dir.rglob("results.json"))
        if ss_files:
            # Append timestamp to directory name so no run overwrites another
            ts = int(time.time())
            artifact_dir = (
                SINGLE_STAGE_STORE
                / f"outputs-{plasma_surf}"
                / f"{ss_files[0].parent.name}-{ts}"
            )
            artifact_dir.mkdir(parents=True, exist_ok=True)
            src_dir = ss_files[0].parent
            for artifact in src_dir.iterdir():
                if artifact.is_file():
                    shutil.copy2(artifact, artifact_dir / artifact.name)

            output["single_stage_artifact_dir"] = str(artifact_dir)

    # Keep crash logs for debugging, clean up successful runs
    if output.get("status") in ("crash", "fail"):
        output["run_dir"] = str(run_dir)
    else:
        shutil.rmtree(run_dir, ignore_errors=True)

    print(json.dumps(output))
    _append_jsonl(output)


COLUMBIA_DATABASE = Path(
    "/Users/suhjungdae/code/columbia/DATABASE/COIL_OPTIMIZATION/outputs"
)


def _resolve_stage2_seed(args: argparse.Namespace, plasma_surf: str) -> str | None:
    """Find a Stage 2 biot_savart_opt.json matching the seed params.

    Searches: explicit --stage2-bs-path, then stage2_seeds/, then Columbia DATABASE.
    Matches on equilibrium (directory) + major_radius + order, picks lowest field error.
    Returns None with a stderr diagnostic if no seed is found.
    """
    if args.stage2_bs_path:
        if Path(args.stage2_bs_path).is_file():
            return args.stage2_bs_path
        print(
            f"stage2-bs-path not found: {args.stage2_bs_path}",
            file=sys.stderr,
        )
        return None

    plasma_dir = f"outputs-{plasma_surf}"

    # Search stage2_seeds/ (autoresearch runs) then Columbia DATABASE
    # Collect all matching seeds and pick the one with lowest field error
    best_seed = None
    best_fe = float("inf")
    for search_root in [STAGE2_SEED_STORE, COLUMBIA_DATABASE]:
        seeds_parent = search_root / plasma_dir
        if not seeds_parent.is_dir():
            continue
        for seed_dir in seeds_parent.iterdir():
            bs_file = seed_dir / "biot_savart_opt.json"
            results_file = seed_dir / "results.json"
            if not bs_file.is_file():
                continue
            # Match on geometry essentials only: equilibrium (implicit from
            # directory), major_radius, and order. The single-stage optimizer
            # re-optimizes coil weights anyway — the seed just provides
            # starting coil geometry. Pick the lowest-FE match.
            if results_file.is_file():
                with open(results_file) as f:
                    seed_meta = json.load(f)
                if (
                    abs(seed_meta.get("MAJOR_RADIUS", 0) - args.major_radius) < 0.001
                    and seed_meta.get("order", 0) == args.order
                    and not seed_meta.get("SELF_INTERSECTING", False)
                ):
                    fe = seed_meta.get("FIELD_ERROR", 999.0)
                    if isinstance(fe, float) and math.isnan(fe):
                        fe = 999.0
                    if fe < best_fe:
                        best_fe = fe
                        best_seed = str(bs_file)

    if best_seed is not None:
        print(
            f"Seed matched: FE={best_fe:.6f} path={best_seed}",
            file=sys.stderr,
        )
        return best_seed

    # No seed found — tell the agent to run Stage 2 first
    print(
        f"No Stage 2 seed found for eq={args.equilibrium} MR={args.major_radius} Order={args.order}. "
        f"Run Stage 2 first, or use: python scripts/lab.py seeds --eq {args.equilibrium}",
        file=sys.stderr,
    )
    return None


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
    ]

    if args.solver == "stage2":
        return common + [
            "--major-radius",
            str(args.major_radius),
            "--toroidal-flux",
            str(args.toroidal_flux),
            "--order",
            str(args.order),
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
            "--basin-hops",
            str(args.basin_hops),
            "--basin-stepsize",
            str(args.basin_stepsize),
            "--basin-seed",
            str(args.basin_seed),
        ]
    else:
        # Resolve Stage 2 seed — searches stage2_seeds/ and Columbia DATABASE
        seed_path = _resolve_stage2_seed(args, plasma_surf)
        if seed_path is None:
            return []  # Will be caught as empty args → crash
        # Stash on args so the output dict can log which seed was used
        args._resolved_seed_path = seed_path

        cli = common + [
            "--stage2-bs-path",
            seed_path,
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
            "--ftol",
            str(args.ftol),
            "--gtol",
            str(args.gtol),
            "--basin-hops",
            str(args.basin_hops),
            "--basin-stepsize",
            str(args.basin_stepsize),
            "--basin-seed",
            str(args.basin_seed),
        ]
        if args.alm:
            cli += [
                "--alm",
                "--alm-outer-iters", str(args.alm_outer_iters),
                "--alm-mu-init", str(args.alm_mu_init),
                "--alm-mu-max", str(args.alm_mu_max),
                "--alm-mu-increase", str(args.alm_mu_increase),
                "--alm-tol", str(args.alm_tol),
            ]
        return cli


def _extract_params(args: argparse.Namespace) -> dict:
    """Extract all physics input params from args into a flat dict."""
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
        shared.update(
            {
                "cc_threshold": args.cc_threshold,
                "length_weight": args.length_weight,
                "length_target": args.length_target,
                "squared_flux_weight": args.squared_flux_weight,
                "curvature_p_norm": args.curvature_p_norm,
                "num_quadpoints": args.num_quadpoints,
                "theta_center": args.theta_center,
                "phi_center": args.phi_center,
                "theta_width": args.theta_width,
                "phi_width": args.phi_width,
                "basin_hops": args.basin_hops,
                "basin_stepsize": args.basin_stepsize,
            }
        )
    else:
        shared.update(
            {
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
                "alm": args.alm,
            }
        )
        if args.alm:
            shared.update({
                "alm_outer_iters": args.alm_outer_iters,
                "alm_mu_init": args.alm_mu_init,
                "alm_mu_max": args.alm_mu_max,
                "alm_mu_increase": args.alm_mu_increase,
                "alm_tol": args.alm_tol,
            })
    return shared


JSONL_PATH = REPO_ROOT / "results.jsonl"


def _append_jsonl(record: dict) -> None:
    """Append one JSON record to results.jsonl. File-locked for concurrent access."""
    import fcntl

    line = json.dumps(record) + "\n"
    with open(JSONL_PATH, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)


def _emit_error(reason: str, elapsed: float, args: argparse.Namespace) -> None:
    output = {
        "source": "local",
        "solver": args.solver,
        "equilibrium": args.equilibrium,
        "status": "crash",
        "score": 0.0,
        "field_error": None,
        "self_intersecting": None,
        "max_curvature": None,
        "iterations": None,
        "elapsed": round(elapsed, 1),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "feedback": reason,
        "params": _extract_params(args),
    }
    print(json.dumps(output))
    _append_jsonl(output)


if __name__ == "__main__":
    main()
