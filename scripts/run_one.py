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
STAGE2_SEED_STORE = REPO_ROOT / "stage2_seeds"

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
    # Stage 2 seed for single-stage
    parser.add_argument(
        "--stage2-source", choices=["database", "local"], default="database"
    )
    parser.add_argument(
        "--stage2-bs-path",
        type=str,
        default=None,
        help="Explicit path to biot_savart_opt.json. Recommended: use stage2_seed_path from a Stage 2 run's JSON output.",
    )
    parser.add_argument("--database-stage2-root", type=str, default=None)

    # --- Execution ---
    parser.add_argument("--omp-threads", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=600)

    args = parser.parse_args()

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

    # Resolve solver and equilibrium
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

    # Single-stage pre-check: run --init-only first (seconds) to verify Boozer init works.
    # Saves 10-30 minutes on runs that would crash during init.
    if args.solver == "single-stage":
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
                    timeout=120,
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
            _emit_error("Boozer init pre-check timed out after 120s", 0.0, args)
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
        "params": _extract_params(args),
    }

    # Single-stage extra fields
    if args.solver == "single-stage":
        output["final_iota"] = metrics.get("FINAL_IOTA")
        output["final_volume"] = metrics.get("FINAL_VOLUME")
        output["target_iota"] = metrics.get("TARGET_IOTA")
        output["target_volume"] = metrics.get("TARGET_VOLUME")

    # For Stage 2: persist biot_savart_opt.json so single-stage can use it as a seed
    # Only overwrite if the new result has lower field error than the existing seed
    if args.solver == "stage2":
        bs_files = list(run_dir.rglob("biot_savart_opt.json"))
        if bs_files:
            seed_dir = (
                STAGE2_SEED_STORE / f"outputs-{plasma_surf}" / bs_files[0].parent.name
            )
            existing_results = seed_dir / "results.json"
            new_fe = metrics.get("FIELD_ERROR", 999.0)
            if isinstance(new_fe, float) and math.isnan(new_fe):
                new_fe = 999.0
            should_write = True
            if existing_results.is_file():
                with open(existing_results) as f:
                    old = json.load(f)
                old_fe = old.get("FIELD_ERROR", 999.0)
                if isinstance(old_fe, float) and math.isnan(old_fe):
                    old_fe = 999.0
                if new_fe >= old_fe:
                    should_write = False

            if should_write:
                seed_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bs_files[0], seed_dir / "biot_savart_opt.json")
                if results_files:
                    shutil.copy2(results_files[0], seed_dir / "results.json")

            output["stage2_seed_path"] = str(seed_dir / "biot_savart_opt.json")

    # Keep crash logs for debugging, clean up successful runs
    if output.get("status") in ("crash", "fail"):
        output["run_dir"] = str(run_dir)
    else:
        shutil.rmtree(run_dir, ignore_errors=True)

    print(json.dumps(output))
    _append_jsonl(output)

    # Clean up process lockfile
    own_lock = OUTPUT_BASE / ".locks" / f"{os.getpid()}.lock"
    own_lock.unlink(missing_ok=True)


COLUMBIA_DATABASE = Path(
    "/Users/suhjungdae/code/columbia/DATABASE/COIL_OPTIMIZATION/outputs"
)


def _resolve_stage2_seed(args: argparse.Namespace, plasma_surf: str) -> str | None:
    """Find a Stage 2 biot_savart_opt.json matching the seed params.

    Searches: explicit --stage2-bs-path, then stage2_seeds/, then Columbia DATABASE.
    If no seed exists, runs Stage 2 automatically and returns the new seed path.
    """
    if args.stage2_bs_path:
        if Path(args.stage2_bs_path).is_file():
            return args.stage2_bs_path
        print(
            json.dumps(
                {
                    "status": "crash",
                    "score": 0.0,
                    "feedback": f"stage2-bs-path not found: {args.stage2_bs_path}",
                }
            ),
        )
        return None

    plasma_dir = f"outputs-{plasma_surf}"

    # Search stage2_seeds/ (autoresearch runs) — solver uses R0/s/CCT/CT format
    for search_root in [STAGE2_SEED_STORE, COLUMBIA_DATABASE]:
        seeds_parent = search_root / plasma_dir
        if not seeds_parent.is_dir():
            continue
        for seed_dir in seeds_parent.iterdir():
            bs_file = seed_dir / "biot_savart_opt.json"
            results_file = seed_dir / "results.json"
            if not bs_file.is_file():
                continue
            # Match by checking results.json params
            if results_file.is_file():
                with open(results_file) as f:
                    seed_meta = json.load(f)
                if (
                    abs(seed_meta.get("MAJOR_RADIUS", 0) - args.major_radius) < 0.001
                    and abs(seed_meta.get("TOROIDAL_FLUX", 0) - args.toroidal_flux)
                    < 0.001
                    and abs(seed_meta.get("CC_WEIGHT", 0) - args.cc_weight) < 0.1
                    and abs(
                        seed_meta.get("CURVATURE_WEIGHT", 0) - args.curvature_weight
                    )
                    < 1e-7
                    and seed_meta.get("order", 0) == args.order
                    and abs(
                        seed_meta.get("banana_surf_radius", 0) - args.banana_surf_radius
                    )
                    < 0.001
                ):
                    return str(bs_file)

    # No seed found — auto-run Stage 2 to generate one
    print(
        f"No Stage 2 seed found for MR={args.major_radius} CCW={args.cc_weight} Order={args.order}. Running Stage 2 first...",
        file=sys.stderr,
    )
    stage2_cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--solver",
        "stage2",
        "--equilibrium",
        args.equilibrium,
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
        "--length-weight",
        str(args.length_weight),
        "--cc-threshold",
        str(getattr(args, "cc_threshold", 0.05)),
        "--maxiter",
        "400",
        "--omp-threads",
        str(args.omp_threads),
    ]
    result = subprocess.run(stage2_cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        return None

    # Parse the Stage 2 output for seed path
    try:
        stage2_output = json.loads(result.stdout.strip())
        seed_path = stage2_output.get("stage2_seed_path")
        if seed_path and Path(seed_path).is_file():
            print(f"Stage 2 seed generated: {seed_path}", file=sys.stderr)
            return seed_path
    except json.JSONDecodeError:
        pass

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
        ]
    else:
        # Resolve Stage 2 seed — auto-searches stage2_seeds/, Columbia DATABASE, or runs Stage 2
        seed_path = _resolve_stage2_seed(args, plasma_surf)
        if seed_path is None:
            return []  # Will be caught as empty args → crash

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
            }
        )
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
        "feedback": reason,
        "params": _extract_params(args),
    }
    print(json.dumps(output))
    _append_jsonl(output)


if __name__ == "__main__":
    main()
