"""Stage 2 scoring — combined_score and feedback string from results.json."""

from __future__ import annotations

import math
from typing import Any


def combined_score(metrics: dict[str, Any], candidate_params: dict[str, Any]) -> float:
    """Score a Stage 2 result. Higher is better. Range (0, 1].

    Stage 2 objective: minimize field error (B·n on plasma surface)
    under engineering constraints (self-intersection, curvature, coil spacing).

    Penalty components (lower total penalty = higher score):
        field_error × 25.0          dominant term — quasi-symmetry
        curvature excess / threshold soft penalty above threshold
        +5.0 if self-intersecting   hard engineering failure
    """
    status = metrics.get("status", "done")
    if status != "done":
        return 0.0

    field_error = metrics.get("FIELD_ERROR")
    if field_error is None or (
        isinstance(field_error, float) and math.isnan(field_error)
    ):
        return 0.0

    penalty = 0.0

    # Field error dominates (weight 25)
    penalty += 25.0 * field_error

    # Curvature excess
    curvature_threshold = candidate_params.get("curvature_threshold", 40.0)
    max_curvature = metrics.get("MAX_CURVATURE")
    if max_curvature is not None and max_curvature > curvature_threshold:
        penalty += (max_curvature - curvature_threshold) / max(curvature_threshold, 1.0)

    # Self-intersection: hard failure
    if metrics.get("SELF_INTERSECTING", False):
        penalty += 5.0

    return 1.0 / (1.0 + penalty)


def feedback_string(
    metrics: dict[str, Any],
    candidate_params: dict[str, Any],
    score: float,
    frontier_best: float | None = None,
    rank: int | None = None,
    total: int | None = None,
) -> str:
    """Build a diagnostic feedback string for logging and LLM context."""
    parts: list[str] = []

    # Score line
    score_str = f"score={score:.4f}"
    if frontier_best is not None:
        score_str += f" (frontier best: {frontier_best:.4f})"
    if rank is not None and total is not None:
        score_str += f" rank: {rank}/{total}"
    parts.append(score_str)

    # Config summary
    cfg_keys = [
        "cc_weight",
        "cc_threshold",
        "curvature_threshold",
        "curvature_weight",
        "length_weight",
        "banana_surf_radius",
        "major_radius",
    ]
    cfg_parts = [
        f"{k}={candidate_params.get(k)}" for k in cfg_keys if k in candidate_params
    ]
    if cfg_parts:
        parts.append("config: " + ", ".join(cfg_parts))

    # Result metrics
    field_error = metrics.get("FIELD_ERROR")
    if field_error is not None:
        parts.append(f"field_error={field_error:.6f}")

    max_curvature = metrics.get("MAX_CURVATURE")
    if max_curvature is not None:
        threshold = candidate_params.get("curvature_threshold", 40.0)
        status = (
            "OK" if max_curvature <= threshold else f"EXCEEDS threshold {threshold}"
        )
        parts.append(f"max_curvature={max_curvature:.1f} ({status})")

    if metrics.get("SELF_INTERSECTING", False):
        parts.append("SELF-INTERSECTING")

    iterations = metrics.get("iterations")
    if iterations is not None:
        maxiter = metrics.get("max_iterations", "?")
        parts.append(f"iterations={iterations}/{maxiter}")

    volume = metrics.get("FINAL_VOLUME")
    if volume is not None:
        parts.append(f"volume={volume:.6f}")

    return ". ".join(parts)
