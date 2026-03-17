#!/usr/bin/env python3
"""CLI entry point — Stage 2 autoresearch search loop.

Usage:
    # Tier 1 only (init-only screening, ~5s per candidate)
    python scripts/run_search.py \\
        --solver-root /path/to/candidate-fixed \\
        --candidates 10 --strategy random

    # Two-stage: Tier 1 screen → Tier 2 optimize (maxiter=100, ~22s per candidate)
    python scripts/run_search.py \\
        --solver-root /path/to/candidate-fixed \\
        --candidates 50 --strategy perturb \\
        --mode two-stage --tier2-maxiter 100 --omp-threads 10
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from search.candidate import Stage2Candidate, PARAM_RANGES, INT_PARAMS  # noqa: E402
from search.config import CampaignConfig  # noqa: E402
from search.probe import tier0_validate, tier1_probe, tier2_probe  # noqa: E402
from search.results_log import ensure_results_tsv, append_result  # noqa: E402
from search.state import SearchDB  # noqa: E402


WEIGHT_PARAMS = {name for name in PARAM_RANGES if "weight" in name}


def random_candidate(parent_id: str | None = None) -> Stage2Candidate:
    """Generate a random candidate by sampling within parameter ranges."""
    params: dict = {}
    for name, (lo, hi) in PARAM_RANGES.items():
        if name in INT_PARAMS:
            params[name] = random.randint(int(lo), int(hi))
        elif name in WEIGHT_PARAMS:
            params[name] = 10 ** random.uniform(math.log10(lo), math.log10(hi))
        else:
            params[name] = random.uniform(lo, hi)
    params["parent_id"] = parent_id
    return Stage2Candidate.from_dict(params)


def perturb_candidate(
    base: Stage2Candidate, scale: float = 0.2, parent_id: str | None = None
) -> Stage2Candidate:
    """Perturb an existing candidate. Log-scale for weights, linear for others."""
    params = base.params_dict.copy()
    for name, (lo, hi) in PARAM_RANGES.items():
        val: float = float(params[name])
        if name in INT_PARAMS:
            int_delta = max(1, int(scale * (hi - lo)))
            val = round(val) + random.randint(-int_delta, int_delta)
            val = max(lo, min(hi, val))
        elif name in WEIGHT_PARAMS:
            log_val = math.log10(max(val, lo))
            log_lo, log_hi = math.log10(lo), math.log10(hi)
            log_span = log_hi - log_lo
            log_delta = scale * log_span * random.uniform(-1, 1)
            val = 10 ** max(log_lo, min(log_hi, log_val + log_delta))
        else:
            span = hi - lo
            flt_delta = scale * span * random.uniform(-1, 1)
            val = max(lo, min(hi, val + flt_delta))
        params[name] = val
    params["parent_id"] = parent_id
    return Stage2Candidate.from_dict(params)


def evaluate_candidate(
    candidate: Stage2Candidate,
    config: CampaignConfig,
    db: SearchDB,
    mode: str,
) -> tuple[str, float, float | None]:
    """Run Tier 0 → Tier 1 → [Tier 2] pipeline. Returns (status, score, field_error)."""
    # Tier 0
    t0 = tier0_validate(candidate, config)
    db.insert_run(t0)
    db.update_candidate_tier(candidate.candidate_id, 0, t0.status, None)
    if t0.status == "fail":
        print(f"  Tier 0 FAIL: {t0.failure_reason}")
        return "crash", 0.0, None

    # Tier 1
    print(
        f"  Tier 1 (init-only, {config.tier1_nphi}x{config.tier1_ntheta})...",
        end="",
        flush=True,
    )
    t1 = tier1_probe(candidate, config)
    db.insert_run(t1)
    db.update_candidate_tier(candidate.candidate_id, 1, t1.status, t1.combined_score)
    fe1 = t1.metrics.get("FIELD_ERROR")
    print(
        f" {t1.status} score={t1.combined_score:.4f} FE={fe1 or '-'} ({t1.elapsed_seconds:.1f}s)"
    )

    if t1.status in ("fail", "crash"):
        return t1.status, t1.combined_score, fe1

    # Tier 2 (only in two-stage mode)
    if mode == "two-stage":
        print(
            f"  Tier 2 (maxiter={config.tier2_maxiter}, "
            f"{config.tier2_nphi}x{config.tier2_ntheta})...",
            end="",
            flush=True,
        )
        t2 = tier2_probe(
            candidate,
            config,
            maxiter=config.tier2_maxiter,
            nphi=config.tier2_nphi,
            ntheta=config.tier2_ntheta,
            timeout=config.tier2_timeout,
        )
        db.insert_run(t2)
        db.update_candidate_tier(
            candidate.candidate_id, 2, t2.status, t2.combined_score
        )
        fe2 = t2.metrics.get("FIELD_ERROR")
        si = t2.metrics.get("SELF_INTERSECTING", False)
        print(
            f" {t2.status} score={t2.combined_score:.4f} FE={fe2 or '-'} SI={si} ({t2.elapsed_seconds:.1f}s)"
        )
        return t2.status, t2.combined_score, fe2

    return t1.status, t1.combined_score, fe1


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2 autoresearch search loop")
    parser.add_argument(
        "--solver-root",
        type=Path,
        required=True,
        help="Path to the SIMSOPT candidate-fixed checkout",
    )
    parser.add_argument(
        "--plasma-surf",
        default="wout_nfp22ginsburg_000_014417_iota15.nc",
        help="Equilibrium filename",
    )
    parser.add_argument(
        "--candidates", type=int, default=10, help="Number of candidates to evaluate"
    )
    parser.add_argument(
        "--strategy",
        choices=["random", "perturb", "baseline"],
        default="random",
        help="Candidate generation strategy",
    )
    parser.add_argument(
        "--mode",
        choices=["tier1-only", "two-stage"],
        default="tier1-only",
        help="tier1-only: init-only screening. two-stage: Tier 1 + Tier 2 optimization.",
    )
    parser.add_argument(
        "--tier2-maxiter",
        type=int,
        default=100,
        help="Max optimizer iterations for Tier 2 (default: 100)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs"),
        help="Output directory for run artifacts",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("search.sqlite"),
        help="SQLite database path",
    )
    parser.add_argument(
        "--omp-threads", type=int, default=4, help="OMP_NUM_THREADS for solver"
    )
    parser.add_argument(
        "--python",
        type=str,
        default=None,
        help="Python executable for solver (default: sys.executable)",
    )
    args = parser.parse_args()

    config = CampaignConfig(
        solver_root=args.solver_root.resolve(),
        plasma_surf_filename=args.plasma_surf,
        python_executable=args.python,
        output_root=args.output_root.resolve(),
        db_path=args.db_path.resolve(),
        omp_num_threads=args.omp_threads,
        mkl_num_threads=args.omp_threads,
        tier2_maxiter=args.tier2_maxiter,
    )

    errors = config.validate()
    if errors:
        print("Campaign config errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    campaign_id = f"s2_{int(time.time())}"
    db = SearchDB(config.db_path)
    db.create_campaign(
        campaign_id, str(config.solver_root), config.plasma_surf_filename, {}
    )

    results_tsv = config.output_root / "results.tsv"
    ensure_results_tsv(results_tsv)

    print(f"Campaign: {campaign_id}")
    print(f"Solver: {config.solver_script}")
    print(f"Equilibrium: {config.equilibrium_path}")
    print(f"Mode: {args.mode}")
    if args.mode == "two-stage":
        print(
            f"Tier 2: maxiter={config.tier2_maxiter}, "
            f"{config.tier2_nphi}x{config.tier2_ntheta}"
        )
    print(f"Strategy: {args.strategy}, Candidates: {args.candidates}")
    print(f"Threads: {args.omp_threads}, Output: {config.output_root}")
    print()

    baseline = Stage2Candidate.baseline()
    frontier_best_id: str | None = None
    frontier_best_score = 0.0

    for i in range(args.candidates):
        # Propose
        if i == 0 and args.strategy != "random":
            candidate = baseline
            desc = "baseline"
        elif args.strategy == "random":
            candidate = random_candidate(parent_id=frontier_best_id)
            desc = "random"
        elif args.strategy == "perturb":
            base = baseline
            if frontier_best_id:
                best_row = db.get_candidate(frontier_best_id)
                if best_row:
                    base = Stage2Candidate.from_dict(
                        json.loads(best_row["params_json"])
                    )
            candidate = perturb_candidate(base, parent_id=frontier_best_id)
            desc = f"perturb from {frontier_best_id or 'baseline'}"
        else:
            candidate = baseline
            desc = "baseline"

        # Skip duplicates
        if db.get_candidate(candidate.candidate_id) is not None:
            print(
                f"[{i + 1}/{args.candidates}] {candidate.candidate_id} — {desc} (duplicate, skip)"
            )
            continue

        print(f"[{i + 1}/{args.candidates}] {candidate.candidate_id} — {desc}")

        db.insert_candidate(
            candidate.candidate_id,
            campaign_id,
            candidate.candidate_hash,
            candidate.parent_id,
            candidate.params_dict,
            desc,
        )

        # Evaluate
        status, score, field_error = evaluate_candidate(
            candidate, config, db, args.mode
        )

        # Keep/discard
        if status in ("fail", "crash"):
            decision = "discard"
        elif score > frontier_best_score:
            decision = "keep"
            frontier_best_id = candidate.candidate_id
            frontier_best_score = score
            db.update_frontier(campaign_id, candidate.candidate_id, score, field_error)
            print(f"  KEEP — new frontier best: {frontier_best_score:.4f}")
        else:
            decision = "discard"
            print(
                f"  discard — score {score:.4f} <= frontier {frontier_best_score:.4f}"
            )

        # Final tier for the decision column
        final_tier = (
            2 if args.mode == "two-stage" and status not in ("fail", "crash") else 1
        )
        db.update_candidate_tier(
            candidate.candidate_id, final_tier, status, score, decision=decision
        )
        append_result(
            results_tsv, candidate.candidate_id, score, field_error, decision, desc
        )

    # Summary
    print()
    print("=" * 60)
    print(f"Campaign {campaign_id} complete.")
    print(f"Candidates evaluated: {db.count_candidates(campaign_id)}")
    best_id, best_score = db.frontier_best(campaign_id)
    if best_id:
        print(f"Frontier best: {best_id} (score={best_score:.4f})")
    print(f"Results: {results_tsv}")
    print(f"Database: {config.db_path}")

    db.close()


if __name__ == "__main__":
    main()
