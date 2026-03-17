"""Probe runner — Tier 0/1/2 probes for Stage 2 banana coil optimization."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from search.candidate import Stage2Candidate
from search.config import CampaignConfig
from search.scorer import combined_score, feedback_string


@dataclass
class ProbeResult:
    """Result of a probe tier evaluation."""

    candidate_id: str
    tier: int
    status: str  # "pass" | "fail" | "crash"
    metrics: dict[str, Any]
    combined_score: float
    feedback: str
    failure_reason: str | None = None
    run_dir: str | None = None
    elapsed_seconds: float = 0.0


def tier0_validate(candidate: Stage2Candidate, config: CampaignConfig) -> ProbeResult:
    """Tier 0: schema validation + equilibrium file existence. Instant, zero cost."""
    errors = candidate.validate() + config.validate()

    if errors:
        return ProbeResult(
            candidate_id=candidate.candidate_id,
            tier=0,
            status="fail",
            metrics={},
            combined_score=0.0,
            feedback="Tier 0 validation failed: " + "; ".join(errors),
            failure_reason="; ".join(errors),
        )

    return ProbeResult(
        candidate_id=candidate.candidate_id,
        tier=0,
        status="pass",
        metrics={},
        combined_score=0.0,
        feedback="Tier 0 validation passed.",
    )


def tier1_probe(candidate: Stage2Candidate, config: CampaignConfig) -> ProbeResult:
    """Tier 1: init-only probe. Builds geometry without optimizing.

    Uses low resolution (nphi=63, ntheta=32) and --init-only flag.
    Typical runtime: ~5 seconds.
    """
    return _run_probe(
        candidate=candidate,
        config=config,
        tier=1,
        nphi=config.tier1_nphi,
        ntheta=config.tier1_ntheta,
        maxiter=0,
        init_only=True,
        timeout=120,
    )


def tier2_probe(
    candidate: Stage2Candidate,
    config: CampaignConfig,
    *,
    maxiter: int = 100,
    nphi: int = 127,
    ntheta: int = 32,
    timeout: int = 600,
) -> ProbeResult:
    """Tier 2: short optimization probe. Runs L-BFGS-B with real iterations.

    Default: maxiter=100, 127x32 resolution. ~20s per candidate on M3 Max (10 cores).
    """
    return _run_probe(
        candidate=candidate,
        config=config,
        tier=2,
        nphi=nphi,
        ntheta=ntheta,
        maxiter=maxiter,
        init_only=False,
        timeout=timeout,
    )


def _run_probe(
    *,
    candidate: Stage2Candidate,
    config: CampaignConfig,
    tier: int,
    nphi: int,
    ntheta: int,
    maxiter: int,
    init_only: bool,
    timeout: int,
) -> ProbeResult:
    """Shared probe runner for all tiers."""
    t0 = time.monotonic()
    tier_label = f"tier{tier}"

    # Build run directory — include timestamp to prevent stale collisions
    ts = int(time.time())
    run_dir = (
        config.output_root / "probes" / f"{tier_label}_{candidate.candidate_id}_{ts}"
    )
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Build CLI args
    cli_args = candidate.to_cli_args(
        plasma_surf_filename=config.plasma_surf_filename,
        equilibria_dir=config.equilibria_dir,
        output_root=run_dir,
        nphi=nphi,
        ntheta=ntheta,
        maxiter=maxiter,
        init_only=init_only,
    )

    python = config.python_executable or sys.executable
    cmd = [python, str(config.solver_script)] + cli_args

    # Set thread environment
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = str(config.omp_num_threads)
    env["MKL_NUM_THREADS"] = str(config.mkl_num_threads)
    env["OPENBLAS_NUM_THREADS"] = str(config.omp_num_threads)

    # Run solver
    log_path = run_dir / "probe.log"
    try:
        with open(log_path, "w") as log_file:
            result = subprocess.run(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=timeout,
            )
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return ProbeResult(
            candidate_id=candidate.candidate_id,
            tier=tier,
            status="crash",
            metrics={},
            combined_score=0.0,
            feedback=f"{tier_label} timed out after {timeout}s.",
            failure_reason="timeout",
            run_dir=str(run_dir),
            elapsed_seconds=elapsed,
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ProbeResult(
            candidate_id=candidate.candidate_id,
            tier=tier,
            status="crash",
            metrics={},
            combined_score=0.0,
            feedback=f"{tier_label} subprocess error: {exc}",
            failure_reason=str(exc),
            run_dir=str(run_dir),
            elapsed_seconds=elapsed,
        )

    elapsed = time.monotonic() - t0

    if returncode != 0:
        tail = ""
        if log_path.exists():
            lines = log_path.read_text().splitlines()
            tail = "\n".join(lines[-20:])
        return ProbeResult(
            candidate_id=candidate.candidate_id,
            tier=tier,
            status="crash",
            metrics={"returncode": returncode},
            combined_score=0.0,
            feedback=f"{tier_label} exited with code {returncode}. Tail: {tail[:500]}",
            failure_reason=f"exit code {returncode}",
            run_dir=str(run_dir),
            elapsed_seconds=elapsed,
        )

    # Find results.json
    metrics = _find_and_parse_results(run_dir)
    if metrics is None:
        return ProbeResult(
            candidate_id=candidate.candidate_id,
            tier=tier,
            status="fail",
            metrics={},
            combined_score=0.0,
            feedback=f"{tier_label} completed but no results.json found.",
            failure_reason="no results.json",
            run_dir=str(run_dir),
            elapsed_seconds=elapsed,
        )

    score = combined_score(metrics, candidate.params_dict)
    fb = feedback_string(metrics, candidate.params_dict, score)

    # Determine pass/fail
    field_error = metrics.get("FIELD_ERROR")
    self_intersecting = metrics.get("SELF_INTERSECTING", False)

    status = "pass"
    failure_reason = None

    if self_intersecting:
        status = "fail"
        failure_reason = "self-intersecting"
    elif field_error is not None and field_error > 1.0:
        status = "fail"
        failure_reason = f"field_error={field_error:.4f} too high"

    return ProbeResult(
        candidate_id=candidate.candidate_id,
        tier=tier,
        status=status,
        metrics=metrics,
        combined_score=score,
        feedback=fb,
        failure_reason=failure_reason,
        run_dir=str(run_dir),
        elapsed_seconds=elapsed,
    )


def _find_and_parse_results(run_dir: Path) -> dict[str, Any] | None:
    """Recursively find results.json under run_dir and parse it."""
    for results_path in run_dir.rglob("results.json"):
        try:
            with open(results_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
    return None
