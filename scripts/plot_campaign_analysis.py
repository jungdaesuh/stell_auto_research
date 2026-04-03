#!/usr/bin/env python3
"""Generate multi-objective Pareto and parallel coordinates analysis plots
from results.jsonl.

Usage:
    python scripts/plot_campaign_analysis.py [--input results.jsonl] [--outdir plots/]

Produces:
    1. pareto_analysis.png        — 4-panel overview (Pareto front, elite zoom, score vs FE, box plots)
    2. parameter_sensitivity.png  — 4-panel (curvature weight, CT=20 vs CT=40, diminishing returns, violin)
    3. parallel_coords.png        — 3-axis parallel coordinates (364 runs)
    4. parallel_coords_split.png  — 4-axis parallel coordinates, split by solver
    5. pareto_projections.png     — 3D Pareto front projected onto 2D pairs
    6. six_objective_parallel.png — 6-objective parallel coordinates (full-metric single-stage runs)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import numpy as np
from matplotlib.colors import Normalize


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_results(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def passing_runs(rows: list[dict], require: list[str] | None = None) -> list[dict]:
    """Filter to passing runs with score and optionally required fields."""
    out = []
    for r in rows:
        if r["status"] != "pass" or r.get("score") is None:
            continue
        if require and not all(r.get(k) is not None for k in require):
            continue
        out.append(r)
    return out


def _save_figure(fig: plt.Figure, outdir: Path, filename: str) -> None:
    path = outdir / filename
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path}")


# ---------------------------------------------------------------------------
# Pareto front computation
# ---------------------------------------------------------------------------

def pareto_mask(costs: np.ndarray) -> np.ndarray:
    """Return boolean mask of Pareto-optimal rows (all objectives minimized)."""
    n = costs.shape[0]
    is_eff = np.ones(n, dtype=bool)
    for i in range(n):
        if not is_eff[i]:
            continue
        others = np.where(is_eff)[0]
        others = others[others != i]
        if len(others) == 0:
            continue
        dominated = np.all(costs[others] <= costs[i], axis=1) & np.any(
            costs[others] < costs[i], axis=1
        )
        if np.any(dominated):
            is_eff[i] = False
    return is_eff


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SCORE_NORM = Normalize(vmin=0.15, vmax=1.0)
CMAP = cm.plasma


# ---------------------------------------------------------------------------
# Plot 1: pareto_analysis.png — 4-panel overview
# ---------------------------------------------------------------------------

def plot_pareto_analysis(rows: list[dict], outdir: Path) -> None:
    data = passing_runs(rows, require=["field_error", "max_curvature"])
    fe = np.array([r["field_error"] for r in data])
    curv = np.array([r["max_curvature"] for r in data])
    scores = np.array([r["score"] for r in data])
    solvers = np.array([r["solver"] for r in data])
    ss = solvers == "single-stage"
    s2 = ~ss

    fig, axes = plt.subplots(2, 2, figsize=(16, 14))

    # --- A: Full scatter with Pareto front ---
    ax = axes[0, 0]
    sc = ax.scatter(fe[s2], curv[s2], c=scores[s2], cmap="viridis", s=30,
                    alpha=0.6, marker="o", edgecolors="none", label="stage2",
                    vmin=0.15, vmax=1.0)
    ax.scatter(fe[ss], curv[ss], c=scores[ss], cmap="viridis", s=60, alpha=0.8,
               marker="D", edgecolors="black", linewidths=0.5, label="single-stage",
               vmin=0.15, vmax=1.0)

    # Pareto front (minimize fe, minimize curv)
    costs_2d = np.column_stack([fe, curv])
    pmask = pareto_mask(costs_2d)
    pfe, pcurv = fe[pmask], curv[pmask]
    order = np.argsort(pfe)
    ax.plot(pfe[order], pcurv[order], "r-", linewidth=2, alpha=0.8, label="Pareto front")
    ax.scatter(pfe, pcurv, c="red", s=100, zorder=5, marker="*", edgecolors="black")

    ax.set_xlabel("Field Error", fontsize=12)
    ax.set_ylabel("Max Curvature", fontsize=12)
    ax.set_title("A) Pareto Front: Field Error vs Max Curvature",
                 fontsize=13, fontweight="bold")
    ax.legend(loc="upper right")
    plt.colorbar(sc, ax=ax, label="Score")
    ax.set_xlim(-0.002, 0.07)

    # --- B: Elite zoom ---
    ax = axes[0, 1]
    elite = scores > 0.9
    ax.scatter(fe[elite & s2], curv[elite & s2], c=scores[elite & s2], cmap="plasma",
               s=50, alpha=0.7, marker="o", edgecolors="gray", linewidths=0.3,
               label="stage2", vmin=0.9, vmax=1.0)
    sc2 = ax.scatter(fe[elite & ss], curv[elite & ss], c=scores[elite & ss],
                     cmap="plasma", s=80, alpha=0.9, marker="D", edgecolors="black",
                     linewidths=0.5, label="single-stage", vmin=0.9, vmax=1.0)

    best_idx = int(np.argmax(scores))
    ax.annotate(f"BEST: {scores[best_idx]:.4f}", xy=(fe[best_idx], curv[best_idx]),
                xytext=(fe[best_idx] + 0.001, curv[best_idx] + 3),
                arrowprops={"arrowstyle": "->", "color": "red"},
                fontsize=10, color="red", fontweight="bold")

    ax.set_xlabel("Field Error", fontsize=12)
    ax.set_ylabel("Max Curvature", fontsize=12)
    ax.set_title("B) Elite Region (score > 0.9)", fontsize=13, fontweight="bold")
    ax.legend(loc="upper right")
    plt.colorbar(sc2, ax=ax, label="Score")

    # --- C: Score vs field error by solver ---
    ax = axes[1, 0]
    ax.scatter(fe[s2], scores[s2], c="steelblue", s=25, alpha=0.5, label="stage2")
    ax.scatter(fe[ss], scores[ss], c="orangered", s=40, alpha=0.7, label="single-stage")
    s2_best = float(scores[s2].max()) if s2.any() else 0
    ss_best = float(scores[ss].max()) if ss.any() else 0
    ax.axhline(y=s2_best, color="steelblue", linestyle="--", alpha=0.5,
               label=f"stage2 ceiling ({s2_best:.3f})")
    ax.axhline(y=ss_best, color="orangered", linestyle="--", alpha=0.5,
               label=f"SS best ({ss_best:.3f})")
    ax.set_xlabel("Field Error", fontsize=12)
    ax.set_ylabel("Score", fontsize=12)
    ax.set_title("C) Score vs Field Error by Solver\n"
                 "(scores use different formulas per solver — not directly comparable)",
                 fontsize=11, fontweight="bold")
    ax.legend(loc="lower left", fontsize=9)

    # --- D: Score distribution by equilibrium, split by solver ---
    # Show only single-stage to avoid confounding with solver allocation.
    # Annotate sample size so the reader knows coverage.
    ax = axes[1, 1]
    ss_data_d = [r for r in data if r["solver"] == "single-stage"]
    equil_scores_ss: dict[str, list[float]] = defaultdict(list)
    for r in ss_data_d:
        equil_scores_ss[r.get("equilibrium", "unknown")].append(r["score"])

    top_equils = sorted(
        [eq for eq, s in equil_scores_ss.items() if len(s) >= 3],
        key=lambda eq: -max(equil_scores_ss[eq]),
    )[:8]
    box_data = [equil_scores_ss[eq] for eq in top_equils]
    tick_labels = [f"{eq}\n(n={len(equil_scores_ss[eq])})" for eq in top_equils]
    bp = ax.boxplot(box_data, tick_labels=tick_labels, patch_artist=True, showfliers=True)
    colors_bp = plt.cm.Set2(np.linspace(0, 1, len(top_equils)))
    for patch, color in zip(bp["boxes"], colors_bp):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_xlabel("Equilibrium", fontsize=12)
    ax.set_ylabel("Score", fontsize=12)
    ax.set_title("D) Score by Equilibrium (single-stage only)",
                 fontsize=13, fontweight="bold")
    ax.tick_params(axis="x", rotation=45)

    plt.suptitle(
        "Stellarator Coil Optimization Campaign\n"
        "NFP=5 Ginsburg Equilibria, SIMSOPT Framework",
        fontsize=15, fontweight="bold", y=1.01,
    )
    plt.tight_layout()
    _save_figure(fig, outdir, "pareto_analysis.png")


# ---------------------------------------------------------------------------
# Plot 2: parameter_sensitivity.png — 4-panel
# ---------------------------------------------------------------------------

def plot_parameter_sensitivity(rows: list[dict], outdir: Path) -> None:
    data = passing_runs(rows, require=["field_error", "max_curvature"])
    scores = np.array([r["score"] for r in data])
    solvers = np.array([r["solver"] for r in data])

    fig, axes = plt.subplots(2, 2, figsize=(16, 14))

    # --- A: curvature_weight vs score (single-stage) ---
    ax = axes[0, 0]
    ss_pass = [r for r in data if r["solver"] == "single-stage" and r.get("params")]
    cw_vals = [r["params"].get("curvature_weight", 0) for r in ss_pass]
    ss_scores = [r["score"] for r in ss_pass]
    ax.scatter(cw_vals, ss_scores, c="orangered", s=50, alpha=0.7,
               edgecolors="black", linewidths=0.3)
    ax.set_xlabel("curvature_weight", fontsize=12)
    ax.set_ylabel("Score", fontsize=12)
    ax.set_title("A) Curvature Weight vs Score (single-stage)",
                 fontsize=13, fontweight="bold")
    ax.axvspan(0.08, 0.3, alpha=0.1, color="green", label="Sweet spot")
    ax.legend()

    # --- B: CT=20 vs CT=40 ---
    ax = axes[0, 1]
    ss_all = [r for r in rows if r["solver"] == "single-stage" and r.get("params")]
    ct20 = [r for r in ss_all if r["params"].get("curvature_threshold") == 20.0]
    ct40 = [r for r in ss_all if r["params"].get("curvature_threshold") == 40.0]

    ct20_pass = sum(1 for r in ct20 if r["status"] == "pass")
    ct20_crash = sum(1 for r in ct20 if r["status"] == "crash")
    ct40_pass = sum(1 for r in ct40 if r["status"] == "pass")
    ct40_crash = sum(1 for r in ct40 if r["status"] == "crash")

    ct20_scores = [r["score"] for r in ct20 if r["status"] == "pass" and r.get("score") is not None]
    ct40_scores = [r["score"] for r in ct40 if r["status"] == "pass" and r.get("score") is not None]
    ct20_mean = sum(ct20_scores) / len(ct20_scores) if ct20_scores else 0
    ct40_mean = sum(ct40_scores) / len(ct40_scores) if ct40_scores else 0

    x_pos = [0, 1]
    width = 0.35
    ax.bar([x - width / 2 for x in x_pos], [ct20_pass, ct40_pass], width,
           label="Pass", color="forestgreen", alpha=0.8)
    ax.bar([x + width / 2 for x in x_pos], [ct20_crash, ct40_crash], width,
           label="Crash", color="crimson", alpha=0.8)
    ax.set_xticks(x_pos)
    ax.set_xticklabels([f"CT=20\n(n={len(ct20)})", f"CT=40\n(n={len(ct40)})"])
    ax.set_ylabel("Number of Runs", fontsize=12)
    ax.set_title("B) CT=20 vs CT=40: High-Risk/High-Reward",
                 fontsize=13, fontweight="bold")
    ax.legend()

    for i, (mean_s, counts) in enumerate(
        [(ct20_mean, [ct20_pass, ct20_crash]), (ct40_mean, [ct40_pass, ct40_crash])]
    ):
        ax.annotate(f"Mean score: {mean_s:.3f}", xy=(i, max(counts) + 5),
                    ha="center", fontsize=11, fontweight="bold", color="navy")

    # --- C: Diminishing returns ---
    ax = axes[1, 0]
    milestones_h, milestones_s = [], []
    cum_time, best_so_far = 0.0, 0.0
    for r in rows:
        if r.get("elapsed"):
            cum_time += r["elapsed"]
        if r["status"] == "pass" and r.get("score") is not None and r["score"] > best_so_far:
            best_so_far = r["score"]
            milestones_h.append(cum_time / 3600)
            milestones_s.append(best_so_far)

    ax.plot(milestones_h, milestones_s, "ko-", markersize=6)
    ax.fill_between(milestones_h, milestones_s, alpha=0.1, color="blue")

    s2_ceil = max(
        (r["score"] for r in rows if r["solver"] == "stage2" and r["status"] == "pass"
         and r.get("score") is not None),
        default=0,
    )
    ax.axhline(y=s2_ceil, color="steelblue", linestyle="--", alpha=0.5,
               label="stage2 ceiling")
    ax.axhline(y=0.99, color="orangered", linestyle="--", alpha=0.5,
               label="0.99 threshold")
    ax.set_xlabel("Cumulative Compute (hours)", fontsize=12)
    ax.set_ylabel("Best Score Achieved", fontsize=12)
    ax.set_title("C) Diminishing Returns Curve", fontsize=13, fontweight="bold")
    ax.legend()

    # --- D: Solver violin ---
    ax = axes[1, 1]
    ss_scores_all = [r["score"] for r in data if r["solver"] == "single-stage"]
    s2_scores_all = [r["score"] for r in data if r["solver"] == "stage2"]

    vp = ax.violinplot([ss_scores_all, s2_scores_all], positions=[0, 1],
                       showmedians=True, showextrema=True)
    colors_v = ["orangered", "steelblue"]
    for i, body in enumerate(vp["bodies"]):
        body.set_facecolor(colors_v[i])
        body.set_alpha(0.6)

    ax.set_xticks([0, 1])
    ax.set_xticklabels([f"Single-stage\n(n={len(ss_scores_all)})",
                        f"Stage2\n(n={len(s2_scores_all)})"])
    ax.set_ylabel("Score", fontsize=12)
    ax.set_title("D) Score Distribution by Solver\n"
                 "(different scoring formulas — cross-solver comparison is qualitative)",
                 fontsize=11, fontweight="bold")

    if ss_scores_all:
        ax.annotate(f"max={max(ss_scores_all):.4f}", xy=(0, max(ss_scores_all)),
                    xytext=(0.3, max(ss_scores_all)), fontsize=10, color="orangered")
    if s2_scores_all:
        ax.annotate(f"max={max(s2_scores_all):.4f}", xy=(1, max(s2_scores_all)),
                    xytext=(1.2, max(s2_scores_all)), fontsize=10, color="steelblue")

    plt.suptitle("Stellarator Optimization — Parameter Sensitivity & Efficiency",
                 fontsize=15, fontweight="bold", y=1.01)
    plt.tight_layout()
    _save_figure(fig, outdir, "parameter_sensitivity.png")


# ---------------------------------------------------------------------------
# Plot 3: parallel_coords.png — 3-axis (all runs)
# ---------------------------------------------------------------------------

# Axis direction: "minimize" means lower raw value = better = plots high (1.0).
# "maximize" means higher raw value = better = plots high (1.0).
MINIMIZE = "minimize"
MAXIMIZE = "maximize"

# Default direction for known metrics.  Unlisted keys default to MAXIMIZE.
_METRIC_DIRECTION: dict[str, str] = {
    "field_error": MINIMIZE,
    "max_curvature": MINIMIZE,
    "coil_length": MINIMIZE,
    "nonqs_ratio": MINIMIZE,
    "boozer_residual": MINIMIZE,
    # maximize: higher is better
    "score": MAXIMIZE,
    "curve_curve_min_dist": MAXIMIZE,
    "curve_surface_min_dist": MAXIMIZE,
    "surface_vessel_min_dist": MAXIMIZE,
}


def _parallel_coords_panel(
    ax: plt.Axes,
    data: list[dict],
    axes_keys: list[tuple[str, str]],
    cmap_name: str = "plasma",
    norm: Normalize = SCORE_NORM,
    style_fn=None,
    global_ranges: dict[str, tuple[float, float]] | None = None,
) -> None:
    """Draw parallel coordinates on a single axes.

    axes_keys:     list of (display_label, jsonl_key) tuples.
    style_fn:      callable(run, score) -> dict with alpha, linewidth, linestyle, marker.
    global_ranges: optional {key: (min, max)} to override per-panel normalization,
                   ensuring multiple panels share the same physical scale.

    Axis direction is determined by ``_METRIC_DIRECTION``.  "minimize" axes are
    flipped so that *better* (lower raw) values always appear *higher* on the
    plot.  Annotations show the raw value that maps to y=0 (worst) at the bottom
    and y=1 (best) at the top.
    """
    labels = [label for label, _ in axes_keys]
    keys = [key for _, key in axes_keys]
    raw = {k: np.array([r[k] if k != "score" else r.get("score", 0) for r in data])
           for _, k in axes_keys}

    # Compute and cache ranges, then normalize to [0,1] respecting axis direction.
    ranges: dict[str, tuple[float, float]] = {}
    normed = {}
    for k, vals in raw.items():
        if global_ranges and k in global_ranges:
            vmin, vmax = global_ranges[k]
        else:
            vmin, vmax = float(vals.min()), float(vals.max())
        ranges[k] = (vmin, vmax)

        direction = _METRIC_DIRECTION.get(k, MAXIMIZE)
        if vmax > vmin:
            n01 = (vals - vmin) / (vmax - vmin)
            if direction == MINIMIZE:
                n01 = 1.0 - n01  # flip: low raw value → high plot position
        else:
            n01 = np.zeros_like(vals)
        normed[k] = n01

    x_pos = np.arange(len(labels))
    scores = raw.get("score", np.array([r.get("score", 0) for r in data]))
    cmap_obj = matplotlib.colormaps[cmap_name]

    for idx in np.argsort(scores):
        y = [normed[k][idx] for k in keys]
        color = cmap_obj(norm(scores[idx]))
        if style_fn:
            style = style_fn(data[idx], scores[idx])
        else:
            s = scores[idx]
            style = {
                "alpha": 0.05 if s < 0.5 else (0.25 if s < 0.9 else 0.85),
                "linewidth": 0.4 if s < 0.9 else (2.0 if s < 0.95 else 3.0),
            }
        ax.plot(x_pos, y, color=color, **style)

    # Annotate: bottom (y=0) = worst, top (y=1) = best.
    for i, k in enumerate(keys):
        vmin, vmax = ranges[k]
        direction = _METRIC_DIRECTION.get(k, MAXIMIZE)
        bottom_val, top_val = (vmax, vmin) if direction == MINIMIZE else (vmin, vmax)

        ax.annotate(f"{bottom_val:.4f}", xy=(i, -0.07), ha="center",
                    fontsize=9, color="gray")
        ax.annotate(f"{top_val:.4f}", xy=(i, 1.07), ha="center",
                    fontsize=9, color="gray")

    ax.set_xticks(x_pos)
    ax.set_xticklabels(labels, fontsize=13, fontweight="bold")
    ax.set_ylim(-0.12, 1.15)
    ax.set_ylabel("Normalized (up = better)", fontsize=12)


def plot_parallel_coords(rows: list[dict], outdir: Path) -> None:
    data3 = passing_runs(rows, require=["field_error", "max_curvature"])
    data4 = [r for r in data3 if r.get("curve_curve_min_dist") is not None]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(18, 14),
                                    gridspec_kw={"hspace": 0.4})

    # Top: 3-axis
    _parallel_coords_panel(ax1, data3, [
        ("Field Error", "field_error"),
        ("Max Curvature", "max_curvature"),
        ("Score", "score"),
    ])
    ax1.set_title(f"Parallel Coordinates — {len(data3)} Passing Runs (colored by Score)",
                  fontsize=14, fontweight="bold")
    sm = cm.ScalarMappable(cmap=CMAP, norm=SCORE_NORM)
    sm.set_array([])
    plt.colorbar(sm, ax=ax1, shrink=0.7, pad=0.02).set_label("Score")

    # Bottom: 4-axis with solver distinction
    def style_4axis(r, s):
        ls = "-" if r["solver"] == "single-stage" else "--"
        return {
            "alpha": 0.12 if s < 0.5 else (0.35 if s < 0.9 else 0.9),
            "linewidth": 0.6 if s < 0.9 else 2.5,
            "linestyle": ls,
        }

    _parallel_coords_panel(ax2, data4, [
        ("Field Error", "field_error"),
        ("Max Curvature", "max_curvature"),
        ("Coil-Coil Dist", "curve_curve_min_dist"),
        ("Score", "score"),
    ], style_fn=style_4axis)
    ax2.set_title(
        f"4-Objective Parallel Coordinates — {len(data4)} Runs "
        "(solid=single-stage, dashed=stage2)",
        fontsize=14, fontweight="bold",
    )
    sm2 = cm.ScalarMappable(cmap=CMAP, norm=SCORE_NORM)
    sm2.set_array([])
    plt.colorbar(sm2, ax=ax2, shrink=0.7, pad=0.02).set_label("Score")

    _save_figure(fig, outdir, "parallel_coords.png")


# ---------------------------------------------------------------------------
# Plot 4: parallel_coords_split.png — separate panels per solver
# ---------------------------------------------------------------------------

def plot_parallel_coords_split(rows: list[dict], outdir: Path) -> None:
    data4 = passing_runs(rows, require=["field_error", "max_curvature"])
    data4 = [r for r in data4 if r.get("curve_curve_min_dist") is not None]

    ss_data = [r for r in data4 if r["solver"] == "single-stage"]
    s2_data = [r for r in data4 if r["solver"] == "stage2"]

    axes_spec = [
        ("Field Error", "field_error"),
        ("Max Curvature", "max_curvature"),
        ("Coil-Coil Dist", "curve_curve_min_dist"),
        ("Score", "score"),
    ]

    # Compute global min/max across BOTH solvers so panels are directly comparable.
    global_ranges: dict[str, tuple[float, float]] = {}
    for _, key in axes_spec:
        all_vals = [r[key] if key != "score" else r.get("score", 0) for r in data4]
        global_ranges[key] = (min(all_vals), max(all_vals))

    fig, (ax_ss, ax_s2) = plt.subplots(2, 1, figsize=(18, 14),
                                        gridspec_kw={"hspace": 0.4})

    def _solver_style(marker: str):
        def style(r, s):
            return {
                "alpha": 0.3 if s < 0.5 else 0.85,
                "linewidth": 1.0 if s < 0.9 else 3.0,
                "marker": marker,
                "markersize": 4,
            }
        return style

    _parallel_coords_panel(ax_ss, ss_data, axes_spec, cmap_name="YlOrRd",
                           norm=Normalize(0.15, 1.0), style_fn=_solver_style("D"),
                           global_ranges=global_ranges)
    ax_ss.set_title(f"Single-Stage Only — {len(ss_data)} Runs (orange-red palette)",
                    fontsize=14, fontweight="bold", color="orangered")

    _parallel_coords_panel(ax_s2, s2_data, axes_spec, cmap_name="Blues",
                           norm=Normalize(0.15, 1.0), style_fn=_solver_style("o"),
                           global_ranges=global_ranges)
    ax_s2.set_title(f"Stage2 Only — {len(s2_data)} Runs (blue palette)",
                    fontsize=14, fontweight="bold", color="steelblue")

    _save_figure(fig, outdir, "parallel_coords_split.png")


# ---------------------------------------------------------------------------
# Plot 5: pareto_projections.png — 3D Pareto projected onto 2D pairs
# ---------------------------------------------------------------------------

def plot_pareto_projections(rows: list[dict], outdir: Path) -> None:
    data = passing_runs(rows, require=["field_error", "max_curvature"])
    data = [r for r in data if r.get("curve_curve_min_dist") is not None]

    fe = np.array([r["field_error"] for r in data])
    curv = np.array([r["max_curvature"] for r in data])
    ccdist = np.array([r["curve_curve_min_dist"] for r in data])
    scores = np.array([r["score"] for r in data])
    solvers = np.array([r["solver"] for r in data])

    ss_mask = solvers == "single-stage"
    s2_mask = ~ss_mask

    # 3D Pareto: minimize fe, minimize curv, maximize ccdist
    obj = np.column_stack([fe, curv, -ccdist])
    pmask = pareto_mask(obj)
    n_pareto_runs = int(np.sum(pmask))
    n_pareto_unique = len(set(tuple(row) for row in obj[pmask]))

    pairs = [
        ("Field Error", fe, "Max Curvature", curv),
        ("Field Error", fe, "Coil-Coil Dist", ccdist),
        ("Max Curvature", curv, "Coil-Coil Dist", ccdist),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(22, 7))

    for pidx, (xn, xd, yn, yd) in enumerate(pairs):
        ax = axes[pidx]

        # Non-Pareto stage2
        m = s2_mask & ~pmask
        ax.scatter(xd[m], yd[m], c="steelblue", s=50, alpha=0.6,
                   edgecolors="white", linewidths=0.5, marker="o",
                   label="stage2", zorder=2)
        # Non-Pareto single-stage
        m = ss_mask & ~pmask
        ax.scatter(xd[m], yd[m], c="orangered", s=60, alpha=0.6,
                   edgecolors="white", linewidths=0.5, marker="D",
                   label="single-stage", zorder=3)
        # Pareto stage2
        m = s2_mask & pmask
        ax.scatter(xd[m], yd[m], c="steelblue", s=200, alpha=0.95,
                   edgecolors="red", linewidths=2.5, marker="*",
                   label="Pareto (stage2)", zorder=5)
        # Pareto single-stage
        m = ss_mask & pmask
        ax.scatter(xd[m], yd[m], c="orangered", s=200, alpha=0.95,
                   edgecolors="red", linewidths=2.5, marker="*",
                   label="Pareto (single-stage)", zorder=5)

        ax.set_xlabel(xn, fontsize=13)
        ax.set_ylabel(yn, fontsize=13)
        ax.set_title(f"{xn} vs {yn}", fontsize=13, fontweight="bold")
        if pidx == 0:
            ax.legend(fontsize=9, loc="upper right")

    pareto_label = (f"{n_pareto_unique} Pareto-Optimal Points"
                    if n_pareto_unique == n_pareto_runs
                    else f"{n_pareto_unique} Pareto-Optimal Points "
                         f"({n_pareto_runs} runs, {n_pareto_unique} unique)")
    fig.suptitle(
        f"3D Pareto Front — {pareto_label} (red-edged stars)\n"
        f"Orange diamonds = single-stage ({int(ss_mask.sum())}), "
        f"Blue circles = stage2 ({int(s2_mask.sum())})",
        fontsize=14, fontweight="bold",
    )
    plt.tight_layout()
    _save_figure(fig, outdir, "pareto_projections.png")


# ---------------------------------------------------------------------------
# Plot 6: six_objective_parallel.png
# ---------------------------------------------------------------------------

def plot_six_objective(rows: list[dict], outdir: Path) -> None:
    required = ["field_error", "max_curvature", "coil_length",
                "curve_curve_min_dist", "curve_surface_min_dist", "nonqs_ratio"]
    data = passing_runs(rows, require=required)
    if len(data) < 3:
        print("  Skipping six_objective_parallel.png — fewer than 3 runs with all 6 metrics")
        return

    scores = np.array([r["score"] for r in data])

    axes_spec = [
        ("Field Error", "field_error"),
        ("Max Curvature", "max_curvature"),
        ("Coil Length", "coil_length"),
        ("CC Dist", "curve_curve_min_dist"),
        ("CS Dist", "curve_surface_min_dist"),
        ("nonQS Ratio", "nonqs_ratio"),
    ]

    fig, ax = plt.subplots(figsize=(18, 7))

    def style_6(r, s):
        return {
            "alpha": 0.3 if s < 0.5 else 0.9,
            "linewidth": 1.0 if s < 0.9 else 3.0,
            "marker": "o",
            "markersize": 5,
        }

    _parallel_coords_panel(ax, data, axes_spec, style_fn=style_6)
    ax.set_title(
        f"6-Objective Parallel Coordinates — {len(data)} Single-Stage Runs (Full Metrics)",
        fontsize=14, fontweight="bold",
    )
    sm = cm.ScalarMappable(cmap=CMAP, norm=SCORE_NORM)
    sm.set_array([])
    plt.colorbar(sm, ax=ax, shrink=0.7, pad=0.02).set_label("Score")
    plt.tight_layout()
    _save_figure(fig, outdir, "six_objective_parallel.png")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Campaign analysis plots from results.jsonl")
    parser.add_argument("--input", type=Path, default=Path("results.jsonl"),
                        help="Path to results.jsonl")
    parser.add_argument("--outdir", type=Path, default=Path("plots"),
                        help="Output directory for PNGs")
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"Error: {args.input} not found")

    args.outdir.mkdir(parents=True, exist_ok=True)
    rows = load_results(args.input)
    print(f"Loaded {len(rows)} runs from {args.input}\n")

    plot_pareto_analysis(rows, args.outdir)
    plot_parameter_sensitivity(rows, args.outdir)
    plot_parallel_coords(rows, args.outdir)
    plot_parallel_coords_split(rows, args.outdir)
    plot_pareto_projections(rows, args.outdir)
    plot_six_objective(rows, args.outdir)

    print(f"\nAll plots saved to {args.outdir}/")


if __name__ == "__main__":
    main()
