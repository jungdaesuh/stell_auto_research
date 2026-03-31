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

# Paths — defaults (used when --solver-root is not specified)
_DEFAULT_PYTHON = "/opt/homebrew/Caskroom/miniforge/base/envs/columbia-jax-0.9.2/bin/python"
_DEFAULT_SIMSOPT_ROOT = Path("/Users/suhjungdae/code/hbt-compare/wt/candidate-fixed")
EQUILIBRIA = Path("/Users/suhjungdae/code/columbia/DATABASE/EQUILIBRIA")
OUTPUT_BASE = Path("/tmp/hbt_autoresearch")
STAGE2_SEED_STORE = REPO_ROOT / "stage2_seeds"
SINGLE_STAGE_STORE = REPO_ROOT / "single_stage_results"
SOLVER_CONFIG_PATH = REPO_ROOT / ".solver_config.json"

# Relative paths from any SIMSOPT root to solver scripts
_SOLVER_REL_PATHS = {
    "stage2": Path("examples/single_stage_optimization/STAGE_2/banana_coil_solver.py"),
    "single-stage": Path("examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py"),
}


def _load_solver_config() -> dict:
    """Load saved solver configurations from .solver_config.json."""
    if SOLVER_CONFIG_PATH.exists():
        try:
            with open(SOLVER_CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: corrupt .solver_config.json: {e}", file=sys.stderr)
    return {}


def _save_solver_config(config: dict) -> None:
    with open(SOLVER_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _get_git_metadata(solver_root: Path) -> dict:
    """Extract git commit, branch, and dirty status from a solver root."""
    meta = {}
    try:
        meta["solver_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=solver_root,
            stderr=subprocess.DEVNULL, text=True,
        ).strip()[:12]
        meta["solver_branch"] = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=solver_root,
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "diff", "--stat", "HEAD"], cwd=solver_root,
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        meta["solver_dirty"] = bool(dirty)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    meta["solver_root"] = str(solver_root)
    return meta


def _resolve_solver(args) -> tuple[list[str], Path, dict]:
    """Resolve the Python command, solver script path, and git metadata.

    Returns (cmd_prefix, solver_script, git_meta).
    cmd_prefix is the command prefix for subprocess (e.g., [python] or [python, -S, launcher, ...]).
    """
    solver_root = getattr(args, "solver_root", None)

    if solver_root is None:
        # Default: use hardcoded candidate-fixed paths (today's behavior)
        solver_script = _DEFAULT_SIMSOPT_ROOT / _SOLVER_REL_PATHS[args.solver]
        return [_DEFAULT_PYTHON], solver_script, _get_git_metadata(_DEFAULT_SIMSOPT_ROOT)

    solver_root = Path(solver_root).resolve()

    # Look up python from config or CLI
    solver_python = getattr(args, "solver_python", None)
    if solver_python is None:
        config = _load_solver_config()
        solver_python = config.get(str(solver_root), {}).get("python")
    if solver_python is None:
        print(f"ERROR: No --solver-python specified and no saved config for {solver_root}.", file=sys.stderr)
        print(f"Run: python scripts/run_one.py --register --solver-root {solver_root} --solver-python /path/to/python", file=sys.stderr)
        sys.exit(1)

    solver_script = solver_root / _SOLVER_REL_PATHS[args.solver]
    if not solver_script.exists():
        print(f"ERROR: Solver script not found: {solver_script}", file=sys.stderr)
        sys.exit(1)

    # Detect site-packages from the python interpreter
    try:
        site_packages = subprocess.check_output(
            [solver_python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        print(f"ERROR: Cannot detect site-packages from {solver_python}: {e}", file=sys.stderr)
        print(f"Re-register with: python scripts/run_one.py --register --solver-root {solver_root} --solver-python /path/to/python", file=sys.stderr)
        sys.exit(1)

    # Use the -S launcher pattern: bypass editable installs, prepend solver_root/src
    cmd_prefix = [
        solver_python, "-S",
        str(REPO_ROOT / "scripts" / "_solver_launcher.py"),
        "--live-repo", str(solver_root),
        "--site-packages", site_packages,
    ]

    return cmd_prefix, solver_script, _get_git_metadata(solver_root)

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

# Axis iota for each equilibrium — used to validate --iota-target matches.
EQUILIBRIUM_AXIS_IOTA = {
    "iota15": 0.1466, "iota15p": 0.15,
    "iota16": 0.16, "iota17": 0.1697, "iota18": 0.18, "iota19": 0.19,
    "iota20": 0.1980, "iota20p": 0.20,
    "iota21": 0.21, "iota22": 0.22, "iota23": 0.23, "iota24": 0.24,
    "iota25": 0.25, "iota26": 0.26, "iota27": 0.27, "iota28": 0.28,
    "iota29": 0.29, "iota30": 0.30,
    "001490": 0.2973,
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
    parser.add_argument("--ftol", type=float, default=None)
    parser.add_argument("--gtol", type=float, default=None)
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

    # --- Checkpoint & topology scoring (single-stage only) ---
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=0,
        help="Single-stage: save checkpoint every N accepted iterations (0 = disabled).",
    )
    parser.add_argument(
        "--topology-scorer-every",
        type=int,
        default=0,
        help="Single-stage: run topology confinement scoring every N accepted iterations (0 = disabled). Writes topology_archive.jsonl.",
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

    # --- Solver variant ---
    parser.add_argument(
        "--solver-root", type=str, default=None,
        help="Path to a SIMSOPT repo root. Auto-detects solver scripts and git metadata. "
             "When omitted, uses the default candidate-fixed worktree.",
    )
    parser.add_argument(
        "--solver-python", type=str, default=None,
        help="Path to the Python interpreter for this solver root. "
             "Saved by --register so you only need to specify it once.",
    )
    parser.add_argument(
        "--register", action="store_true",
        help="Register a solver root: validate paths, save python interpreter, exit.",
    )

    # --- Execution ---
    parser.add_argument("--omp-threads", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=600)

    # Use parse_known_args only when --solver-root is specified (to forward extra solver args).
    # Otherwise use strict parse_args to catch typos.
    preliminary, remaining = parser.parse_known_args()
    if preliminary.solver_root and remaining:
        args = preliminary
        args._extra_solver_args = remaining
    else:
        args = parser.parse_args()
        args._extra_solver_args = []

    # Handle --register
    if args.register:
        if not args.solver_root:
            parser.error("--register requires --solver-root")
        if not args.solver_python:
            parser.error("--register requires --solver-python")
        root = Path(args.solver_root).resolve()
        py = Path(args.solver_python).resolve()
        if not py.exists():
            parser.error(f"Python not found: {py}")
        for name, rel in _SOLVER_REL_PATHS.items():
            script = root / rel
            if not script.exists():
                parser.error(f"Solver script not found: {script}")
        # Verify imports resolve correctly (without running any solver)
        site_packages = subprocess.check_output(
            [str(py), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        check = subprocess.run(
            [str(py), "-S", "-c",
             f"import sys; sys.path.insert(0, '{root / 'src'}'); sys.path.append('{site_packages}'); "
             f"import simsopt; print('simsopt:', simsopt.__file__); "
             f"from simsopt.geo.boozersurface import BoozerSurface; print('boozersurface: OK')"],
            capture_output=True, text=True,
        )
        if check.returncode != 0:
            parser.error(f"Import validation failed:\n{check.stderr}")
        # Verify imports resolve to the requested root
        for line in check.stdout.strip().splitlines():
            print(f"  {line}", file=sys.stderr)
            if "simsopt:" in line and str(root) not in line:
                parser.error(f"simsopt resolves to wrong location: {line}\nExpected: {root}/src/simsopt/")
        git_meta = _get_git_metadata(root)
        config = _load_solver_config()
        config[str(root)] = {"python": str(py), **git_meta}
        _save_solver_config(config)
        print(f"Registered {root} → {py}", file=sys.stderr)
        print(f"  commit: {git_meta.get('solver_commit', '?')}, branch: {git_meta.get('solver_branch', '?')}")
        sys.exit(0)

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

    # Resolve solver variant (handles --solver-root, ALM, and default)
    # For ALM without --solver-root, use the ALM worktree with the default Python (direct invocation)
    _alm_default_root = Path("/Users/suhjungdae/code/hbt-compare/wt/alm")
    _using_alm_default = args.alm and args.solver == "single-stage" and not args.solver_root

    if _using_alm_default:
        # ALM default: use candidate-fixed Python with ALM worktree's solver script (direct, no launcher)
        solver_script = _alm_default_root / _SOLVER_REL_PATHS[args.solver]
        cmd_prefix = [_DEFAULT_PYTHON]
        git_meta = _get_git_metadata(_alm_default_root)
    else:
        cmd_prefix, solver_script, git_meta = _resolve_solver(args)
    plasma_surf = EQUILIBRIUM_FILES.get(args.equilibrium, args.equilibrium)

    # Validate iota-target against equilibrium axis iota
    if args.solver == "single-stage":
        expected_iota = EQUILIBRIUM_AXIS_IOTA.get(args.equilibrium)
        if expected_iota is not None and abs(args.iota_target - expected_iota) > 0.02:
            print(
                f"WARNING: --iota-target {args.iota_target} does not match "
                f"equilibrium {args.equilibrium} axis iota {expected_iota}. "
                f"This will produce a nonsensical run.",
                file=sys.stderr,
            )

    # SSOT: curvature_threshold <= 40 for HBT coil fabrication
    if args.curvature_threshold > 40:
        print(
            f"ERROR: --curvature-threshold {args.curvature_threshold} exceeds "
            f"HBT fabrication limit of 40. Clamping to 40.",
            file=sys.stderr,
        )
        args.curvature_threshold = 40

    # Build CLI args for the solver
    cli_args = _build_cli_args(args, plasma_surf)
    if not cli_args:
        _emit_error("failed to resolve Stage 2 seed for single-stage", 0.0, args, git_meta)
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
        precheck_cmd = cmd_prefix + [str(solver_script)] + precheck_args
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
                _emit_error(feedback, 0.0, args, git_meta)
                shutil.rmtree(precheck_dir, ignore_errors=True)
                return
        except subprocess.TimeoutExpired:
            _emit_error("Boozer init pre-check timed out after 180s", 0.0, args, git_meta)
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

    cmd = cmd_prefix + [str(solver_script)] + cli_args

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
        # Salvage partial topology artifacts before reporting timeout
        _salvage_partial_artifacts(run_dir, plasma_surf, args)
        _emit_error(
            f"timeout after {args.timeout}s. Partial artifacts may exist in run_dir. "
            f"Run dir: {run_dir}",
            time.monotonic() - t0, args, git_meta,
        )
        return
    except Exception as exc:
        _emit_error(str(exc), time.monotonic() - t0, args, git_meta)
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
        _emit_error(feedback, elapsed, args, git_meta)
        return

    # Parse results.json
    results_files = list(run_dir.rglob("results.json"))
    if not results_files:
        _emit_error("no results.json found", elapsed, args, git_meta)
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
        "solver_commit": git_meta.get("solver_commit"),
        "solver_branch": git_meta.get("solver_branch"),
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
            ts = int(time.time() * 1000)
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
            # Millisecond timestamp avoids collisions between concurrent runs
            ts = int(time.time() * 1000)
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
            # Preserve topology subdirectories (best_topology/, checkpoint_iter*/)
            for subdir in src_dir.iterdir():
                if subdir.is_dir() and (
                    subdir.name == "best_topology"
                    or subdir.name.startswith("checkpoint_iter")
                ):
                    shutil.copytree(subdir, artifact_dir / subdir.name, dirs_exist_ok=True)

            output["single_stage_artifact_dir"] = str(artifact_dir)

            # Verify topology archive was written when topology scoring was enabled
            if args.topology_scorer_every > 0:
                topo_archive = artifact_dir / "topology_archive.jsonl"
                if topo_archive.exists():
                    output["topology_archive_path"] = str(topo_archive)
                else:
                    print(
                        f"WARNING: --topology-scorer-every {args.topology_scorer_every} "
                        f"was set but topology_archive.jsonl was not produced. "
                        f"The solver may not have run enough iterations.",
                        file=sys.stderr,
                    )

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

    extra = getattr(args, '_extra_solver_args', [])

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
        ] + extra
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
            "--basin-hops",
            str(args.basin_hops),
            "--basin-stepsize",
            str(args.basin_stepsize),
            "--basin-seed",
            str(args.basin_seed),
        ]
        if args.ftol is not None:
            cli += ["--ftol", str(args.ftol)]
        if args.gtol is not None:
            cli += ["--gtol", str(args.gtol)]
        if args.checkpoint_every > 0:
            cli += ["--checkpoint-every", str(args.checkpoint_every)]
        if args.topology_scorer_every > 0:
            cli += ["--topology-scorer-every", str(args.topology_scorer_every)]
        if args.alm:
            cli += [
                "--alm",
                "--alm-outer-iters", str(args.alm_outer_iters),
                "--alm-mu-init", str(args.alm_mu_init),
                "--alm-mu-max", str(args.alm_mu_max),
                "--alm-mu-increase", str(args.alm_mu_increase),
                "--alm-tol", str(args.alm_tol),
            ]
        return cli + extra


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
                "checkpoint_every": args.checkpoint_every,
                "topology_scorer_every": args.topology_scorer_every,
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
    # Record solver variant and any extra args forwarded to the solver
    if getattr(args, 'solver_root', None):
        shared["solver_root"] = args.solver_root
    extra = getattr(args, '_extra_solver_args', [])
    if extra:
        shared["extra_solver_args"] = extra
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


def _salvage_partial_artifacts(run_dir: Path, plasma_surf: str, args: argparse.Namespace) -> None:
    """On timeout, copy whatever solver artifacts exist — archive, checkpoints, or both."""
    try:
        # Find the solver output directory: look for any solver artifact
        src_dir = None
        for marker in ("topology_archive.jsonl", "results.json", "biot_savart_opt.json"):
            hits = list(run_dir.rglob(marker))
            if hits:
                src_dir = hits[0].parent
                break
        if src_dir is None:
            # Fall back: check for checkpoint dirs directly under run_dir subdirs
            for subdir in run_dir.iterdir():
                if subdir.is_dir():
                    for item in subdir.iterdir():
                        if item.is_dir() and (
                            item.name == "best_topology"
                            or item.name.startswith("checkpoint_iter")
                        ):
                            src_dir = subdir
                            break
                if src_dir:
                    break
        if src_dir is None:
            return
        ts = int(time.time() * 1000)
        artifact_dir = (
            SINGLE_STAGE_STORE
            / f"outputs-{plasma_surf}"
            / f"timeout-{ts}"
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            if item.is_file() and item.name in (
                "topology_archive.jsonl", "results.json",
                "biot_savart_opt.json", "biot_savart_init.json",
            ):
                shutil.copy2(item, artifact_dir / item.name)
            elif item.is_dir() and (
                item.name == "best_topology"
                or item.name.startswith("checkpoint_iter")
            ):
                shutil.copytree(item, artifact_dir / item.name, dirs_exist_ok=True)
        print(f"Salvaged partial artifacts to {artifact_dir}", file=sys.stderr)
    except Exception as exc:
        print(f"Failed to salvage artifacts: {exc}", file=sys.stderr)


def _emit_error(reason: str, elapsed: float, args: argparse.Namespace,
                git_meta: dict | None = None) -> None:
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
        "solver_commit": (git_meta or {}).get("solver_commit"),
        "solver_branch": (git_meta or {}).get("solver_branch"),
        "params": _extract_params(args),
    }
    print(json.dumps(output))
    _append_jsonl(output)


if __name__ == "__main__":
    main()
