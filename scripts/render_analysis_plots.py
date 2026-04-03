#!/usr/bin/env python3
"""Render reproducible SVG analysis plots from results.jsonl."""

from __future__ import annotations

import argparse
import math
from html import escape
from pathlib import Path

import lab


STATUS_COLORS = {
    "pass": "#2a9d8f",
    "fail": "#f4a261",
    "crash": "#e76f51",
}
SOLVER_COLORS = {
    "single-stage": "#264653",
    "stage2": "#577590",
}
GRID_COLOR = "#d8dee4"
TEXT_COLOR = "#1f2933"


class SvgCanvas:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.parts: list[str] = [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                f'height="{height}" viewBox="0 0 {width} {height}">'
            ),
            f'<rect width="{width}" height="{height}" fill="#f8fafc" />',
        ]

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    def rect(self, x: float, y: float, width: float, height: float, **attrs: str) -> None:
        self.add(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" '
            f'{_attrs(attrs)} />'
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, **attrs: str) -> None:
        self.add(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'{_attrs(attrs)} />'
        )

    def circle(self, cx: float, cy: float, r: float, **attrs: str) -> None:
        self.add(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" {_attrs(attrs)} />'
        )

    def polyline(self, points: list[tuple[float, float]], **attrs: str) -> None:
        point_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        self.add(f'<polyline points="{point_str}" {_attrs(attrs)} />')

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        anchor: str = "start",
        size: int = 13,
        fill: str = TEXT_COLOR,
        weight: str = "400",
        rotate: float | None = None,
    ) -> None:
        transform = ""
        if rotate is not None:
            transform = f' transform="rotate({rotate:.2f} {x:.2f} {y:.2f})"'
        self.add(
            f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
            f'font-family="SFMono-Regular, Menlo, monospace" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}"{transform}>{escape(value)}</text>'
        )

    def save(self, path: Path) -> None:
        path.write_text("\n".join(self.parts + ["</svg>"]) + "\n")


def _attrs(attrs: dict[str, str]) -> str:
    return " ".join(f'{key.replace("_", "-")}="{escape(str(value))}"' for key, value in attrs.items())


def _ensure_outdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _linear_scale(domain_min: float, domain_max: float, range_min: float, range_max: float, value: float) -> float:
    if domain_max <= domain_min:
        return (range_min + range_max) / 2.0
    fraction = (value - domain_min) / (domain_max - domain_min)
    return range_min + fraction * (range_max - range_min)


def _log_scale(domain_min: float, domain_max: float, range_min: float, range_max: float, value: float) -> float:
    safe_min = max(domain_min, 1e-9)
    safe_value = max(value, safe_min)
    return _linear_scale(
        math.log10(safe_min),
        math.log10(max(domain_max, safe_min * 10)),
        range_min,
        range_max,
        math.log10(safe_value),
    )


def _draw_plot_frame(
    canvas: SvgCanvas,
    title: str,
    xlabel: str,
    ylabel: str,
    *,
    width: int = 920,
    height: int = 560,
) -> tuple[float, float, float, float]:
    left = 90.0
    right = width - 50.0
    top = 70.0
    bottom = height - 85.0
    canvas.text(width / 2, 34, title, anchor="middle", size=22, weight="700")
    canvas.line(left, top, left, bottom, stroke="#7b8794", stroke_width="1.2")
    canvas.line(left, bottom, right, bottom, stroke="#7b8794", stroke_width="1.2")
    canvas.text((left + right) / 2, height - 28, xlabel, anchor="middle", size=14)
    canvas.text(24, (top + bottom) / 2, ylabel, anchor="middle", size=14, rotate=-90)
    return left, top, right, bottom


def _draw_y_grid(
    canvas: SvgCanvas,
    left: float,
    right: float,
    top: float,
    bottom: float,
    ticks: list[float],
    formatter,
) -> None:
    for tick in ticks:
        y = _linear_scale(ticks[0], ticks[-1], bottom, top, tick)
        canvas.line(left, y, right, y, stroke=GRID_COLOR, stroke_width="1")
        canvas.text(left - 10, y + 4, formatter(tick), anchor="end", size=12, fill="#52606d")


def _draw_legend(canvas: SvgCanvas, items: list[tuple[str, str]], x: float, y: float) -> None:
    offset = 0.0
    for label, color in items:
        canvas.rect(x + offset, y - 12, 14, 14, fill=color, stroke=color)
        canvas.text(x + offset + 22, y, label, size=12)
        offset += 118


def plot_solver_status_breakdown(outdir: Path) -> Path:
    db = lab._get_db()
    rows = db.execute(
        """
        SELECT solver, status, COUNT(*) AS cnt
        FROM runs
        GROUP BY solver, status
        ORDER BY solver, status
        """
    ).fetchall()
    solvers = sorted({row["solver"] for row in rows if row["solver"]})
    statuses = [status for status in ("pass", "fail", "crash") if any(row["status"] == status for row in rows)]
    by_solver_status = {solver: {status: 0 for status in statuses} for solver in solvers}
    for row in rows:
        by_solver_status[row["solver"]][row["status"]] = row["cnt"]

    canvas = SvgCanvas(920, 560)
    left, top, right, bottom = _draw_plot_frame(
        canvas,
        "Solver Outcome Breakdown",
        "solver",
        "runs",
    )
    totals = [sum(by_solver_status[solver].values()) for solver in solvers]
    max_total = max(totals)
    y_ticks = [0, 200, 400, 600]
    if max_total > 600:
        y_ticks.append(800)
    _draw_y_grid(canvas, left, right, top, bottom, y_ticks, lambda v: str(int(v)))
    _draw_legend(canvas, [(status, STATUS_COLORS[status]) for status in statuses], left, 56)

    slot = (right - left) / max(len(solvers), 1)
    bar_width = slot * 0.52
    for idx, solver in enumerate(solvers):
        x = left + slot * idx + (slot - bar_width) / 2
        base_y = bottom
        for status in statuses:
            count = by_solver_status[solver][status]
            height = (bottom - top) * (count / y_ticks[-1])
            y = base_y - height
            canvas.rect(
                x,
                y,
                bar_width,
                height,
                fill=STATUS_COLORS[status],
                stroke="none",
            )
            base_y = y
        canvas.text(x + bar_width / 2, bottom + 22, solver, anchor="middle", size=13)
        canvas.text(x + bar_width / 2, top - 8, str(totals[idx]), anchor="middle", size=12, fill="#52606d")

    path = outdir / "solver_status_breakdown.svg"
    canvas.save(path)
    return path


def plot_single_stage_ct_tradeoff(outdir: Path) -> Path:
    db = lab._get_db()
    rows = db.execute(
        """
        SELECT
            p_curvature_threshold AS ct,
            SUM(status='pass') AS pass_runs,
            SUM(status='crash') AS crash_runs,
            AVG(CASE WHEN status='pass' AND score IS NOT NULL THEN score END) AS pass_mean_score
        FROM runs
        WHERE solver='single-stage'
          AND p_curvature_threshold IS NOT NULL
          AND status IN ('pass', 'crash')
        GROUP BY p_curvature_threshold
        ORDER BY p_curvature_threshold
        """
    ).fetchall()

    canvas = SvgCanvas(920, 560)
    left, top, right, bottom = _draw_plot_frame(
        canvas,
        "Single-Stage CT Tradeoff",
        "curvature_threshold",
        "runs",
    )
    canvas.text(892, (top + bottom) / 2, "pass-only mean score", anchor="middle", size=14, rotate=90)
    _draw_legend(
        canvas,
        [("pass", STATUS_COLORS["pass"]), ("crash", STATUS_COLORS["crash"]), ("mean score", "#1d3557")],
        left,
        56,
    )
    max_runs = max((row["pass_runs"] + row["crash_runs"] for row in rows), default=1)
    y_ticks = [0, 50, 100, 150, 200, 250]
    _draw_y_grid(canvas, left, right, top, bottom, y_ticks, lambda v: str(int(v)))

    slot = (right - left) / max(len(rows), 1)
    bar_width = slot * 0.46
    line_points: list[tuple[float, float]] = []
    for idx, row in enumerate(rows):
        x = left + slot * idx + (slot - bar_width) / 2
        pass_height = (bottom - top) * (row["pass_runs"] / y_ticks[-1])
        crash_height = (bottom - top) * (row["crash_runs"] / y_ticks[-1])
        y_pass = bottom - pass_height
        y_crash = y_pass - crash_height
        canvas.rect(x, y_pass, bar_width, pass_height, fill=STATUS_COLORS["pass"], stroke="none")
        canvas.rect(x, y_crash, bar_width, crash_height, fill=STATUS_COLORS["crash"], stroke="none")
        label_x = x + bar_width / 2
        canvas.text(label_x, bottom + 22, str(int(row["ct"])), anchor="middle", size=13)
        score_y = _linear_scale(0.0, 1.0, bottom, top, row["pass_mean_score"] or 0.0)
        line_points.append((label_x, score_y))
        canvas.circle(label_x, score_y, 5, fill="#1d3557", stroke="white", stroke_width="1")
        canvas.text(label_x, score_y - 10, f"{(row['pass_mean_score'] or 0.0):.3f}", anchor="middle", size=11, fill="#1d3557")

    canvas.polyline(line_points, fill="none", stroke="#1d3557", stroke_width="2.2")
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = _linear_scale(0.0, 1.0, bottom, top, tick)
        canvas.text(right + 10, y + 4, f"{tick:.2f}", size=12, fill="#52606d")

    path = outdir / "single_stage_ct_tradeoff.svg"
    canvas.save(path)
    return path


def plot_scored_pareto_scatter(outdir: Path) -> Path:
    db = lab._get_db()
    rows = db.execute(
        """
        SELECT *
        FROM runs
        WHERE status='pass'
          AND score IS NOT NULL
          AND field_error IS NOT NULL
          AND max_curvature IS NOT NULL
          AND coil_length IS NOT NULL
        ORDER BY field_error ASC, max_curvature ASC, coil_length ASC
        """
    ).fetchall()
    rows = lab._dedupe_rows(
        rows,
        (
            "solver",
            "equilibrium",
            "score",
            "field_error",
            "max_curvature",
            "coil_length",
            "params_json",
        ),
    )
    frontier = sorted(
        lab._pareto_frontier(rows, ("field_error", "max_curvature", "coil_length")),
        key=lambda row: row["field_error"],
    )
    x_min = min(row["field_error"] for row in rows)
    x_max = max(row["field_error"] for row in rows)
    y_min = 0.0
    y_max = max(row["max_curvature"] for row in rows) * 1.05

    canvas = SvgCanvas(960, 580)
    left, top, right, bottom = _draw_plot_frame(
        canvas,
        "Scored Physical Pareto Frontier",
        "field_error (log scale)",
        "max_curvature",
        width=960,
        height=580,
    )
    _draw_legend(
        canvas,
        [
            ("single-stage", SOLVER_COLORS["single-stage"]),
            ("stage2", SOLVER_COLORS["stage2"]),
            ("Pareto frontier", "#d62828"),
        ],
        left,
        56,
    )
    for tick in [10, 30, 50, 70, 90, 110, 130]:
        y = _linear_scale(y_min, y_max, bottom, top, tick)
        canvas.line(left, y, right, y, stroke=GRID_COLOR, stroke_width="1")
        canvas.text(left - 10, y + 4, str(tick), anchor="end", size=12, fill="#52606d")
    log_ticks = [1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 3e-2]
    visible_ticks = [tick for tick in log_ticks if x_min <= tick <= x_max * 1.1]
    for tick in visible_ticks:
        x = _log_scale(x_min, x_max, left, right, tick)
        canvas.line(x, top, x, bottom, stroke=GRID_COLOR, stroke_width="1")
        canvas.text(x, bottom + 22, f"{tick:.0e}", anchor="middle", size=12, fill="#52606d")

    frontier_points: list[tuple[float, float]] = []
    for row in rows:
        x = _log_scale(x_min, x_max, left, right, row["field_error"])
        y = _linear_scale(y_min, y_max, bottom, top, row["max_curvature"])
        radius = _linear_scale(1.9, 3.0, 5.0, 12.0, row["coil_length"])
        canvas.circle(
            x,
            y,
            radius,
            fill=SOLVER_COLORS.get(row["solver"], "#999999"),
            fill_opacity="0.45",
            stroke="none",
        )
    for row in frontier:
        x = _log_scale(x_min, x_max, left, right, row["field_error"])
        y = _linear_scale(y_min, y_max, bottom, top, row["max_curvature"])
        frontier_points.append((x, y))
        canvas.circle(x, y, 7.4, fill="none", stroke="#d62828", stroke_width="1.8")
    canvas.polyline(frontier_points, fill="none", stroke="#d62828", stroke_width="1.2")

    path = outdir / "scored_pareto_scatter.svg"
    canvas.save(path)
    return path


def plot_single_stage_crash_rule_map(outdir: Path) -> Path:
    db = lab._get_db()
    rows = db.execute(
        """
        SELECT *
        FROM runs
        WHERE solver='single-stage'
          AND status IN ('pass', 'crash')
          AND p_maxiter IS NOT NULL
          AND p_curvature_threshold IS NOT NULL
          AND p_banana_surf_radius IS NOT NULL
        """
    ).fetchall()
    x_min = min(row["p_maxiter"] for row in rows)
    x_max = max(row["p_maxiter"] for row in rows)
    y_min = min(row["p_curvature_threshold"] for row in rows)
    y_max = max(row["p_curvature_threshold"] for row in rows)

    canvas = SvgCanvas(940, 580)
    left, top, right, bottom = _draw_plot_frame(
        canvas,
        "Single-Stage Crash Rule Map",
        "maxiter",
        "curvature_threshold",
        width=940,
        height=580,
    )
    _draw_legend(
        canvas,
        [("pass", STATUS_COLORS["pass"]), ("crash", STATUS_COLORS["crash"])],
        left,
        56,
    )
    for tick in [100, 200, 300, 400, 500]:
        x = _linear_scale(x_min, x_max, left, right, tick)
        canvas.line(x, top, x, bottom, stroke=GRID_COLOR, stroke_width="1")
        canvas.text(x, bottom + 22, str(tick), anchor="middle", size=12, fill="#52606d")
    for tick in [20, 25, 30, 35, 40]:
        y = _linear_scale(y_min, y_max, bottom, top, tick)
        canvas.line(left, y, right, y, stroke=GRID_COLOR, stroke_width="1")
        canvas.text(left - 10, y + 4, str(tick), anchor="end", size=12, fill="#52606d")

    x_rule = _linear_scale(x_min, x_max, left, right, 350)
    y_rule = _linear_scale(y_min, y_max, bottom, top, 25)
    canvas.line(x_rule, top, x_rule, bottom, stroke="#1d3557", stroke_width="1.8", stroke_dasharray="8 6")
    canvas.line(left, y_rule, right, y_rule, stroke="#6a4c93", stroke_width="1.8", stroke_dasharray="8 6")
    canvas.text(x_rule + 8, top + 18, "maxiter = 350", size=11, fill="#1d3557")
    canvas.text(right - 8, y_rule - 8, "CT = 25", anchor="end", size=11, fill="#6a4c93")

    for row in rows:
        x = _linear_scale(x_min, x_max, left, right, row["p_maxiter"])
        y = _linear_scale(y_min, y_max, bottom, top, row["p_curvature_threshold"])
        radius = _linear_scale(0.205, 0.22, 4.5, 8.5, row["p_banana_surf_radius"])
        canvas.circle(
            x,
            y,
            radius,
            fill=STATUS_COLORS[row["status"]],
            fill_opacity="0.66",
            stroke="white",
            stroke_width="0.7",
        )

    path = outdir / "single_stage_crash_rule_map.svg"
    canvas.save(path)
    return path


def plot_single_stage_equilibrium_scores(outdir: Path) -> Path:
    db = lab._get_db()
    rows = db.execute(
        """
        SELECT
            equilibrium,
            COUNT(*) AS pass_runs,
            AVG(score) AS mean_score,
            MAX(score) AS best_score
        FROM runs
        WHERE solver='single-stage'
          AND status='pass'
          AND score IS NOT NULL
          AND equilibrium IS NOT NULL
        GROUP BY equilibrium
        HAVING COUNT(*) >= 3
        ORDER BY mean_score DESC, best_score DESC
        LIMIT 10
        """
    ).fetchall()

    canvas = SvgCanvas(980, 580)
    left, top, right, bottom = _draw_plot_frame(
        canvas,
        "Single-Stage Equilibrium Quality",
        "equilibrium",
        "score",
        width=980,
        height=580,
    )
    _draw_legend(
        canvas,
        [("mean pass score", "#457b9d"), ("best score", "#d62828")],
        left,
        56,
    )
    for tick in [0.0, 0.25, 0.5, 0.75, 1.0]:
        y = _linear_scale(0.0, 1.0, bottom, top, tick)
        canvas.line(left, y, right, y, stroke=GRID_COLOR, stroke_width="1")
        canvas.text(left - 10, y + 4, f"{tick:.2f}", anchor="end", size=12, fill="#52606d")

    slot = (right - left) / max(len(rows), 1)
    bar_width = slot * 0.56
    for idx, row in enumerate(rows):
        x = left + slot * idx + (slot - bar_width) / 2
        mean_height = bottom - _linear_scale(0.0, 1.0, bottom, top, row["mean_score"])
        canvas.rect(x, bottom - mean_height, bar_width, mean_height, fill="#457b9d", stroke="none")
        best_y = _linear_scale(0.0, 1.0, bottom, top, row["best_score"])
        canvas.circle(x + bar_width / 2, best_y, 5.4, fill="#d62828", stroke="white", stroke_width="1")
        canvas.text(x + bar_width / 2, bottom + 34, row["equilibrium"], anchor="end", size=12, rotate=-35)
        canvas.text(x + bar_width / 2, bottom - mean_height - 8, f"{row['mean_score']:.3f}", anchor="middle", size=11, fill="#315c73")

    path = outdir / "single_stage_equilibrium_scores.svg"
    canvas.save(path)
    return path


def plot_score_vs_physics(outdir: Path) -> Path:
    db = lab._get_db()
    rows = db.execute(
        """
        SELECT score, field_error, max_curvature, solver
        FROM runs
        WHERE score IS NOT NULL
          AND field_error IS NOT NULL
          AND max_curvature IS NOT NULL
        """
    ).fetchall()
    fe_min = min(row["field_error"] for row in rows)
    fe_max = max(row["field_error"] for row in rows)
    curv_min = min(row["max_curvature"] for row in rows)
    curv_max = max(row["max_curvature"] for row in rows)

    canvas = SvgCanvas(980, 580)
    canvas.text(490, 34, "Score vs Physics Metrics", anchor="middle", size=22, weight="700")
    _draw_legend(
        canvas,
        [("single-stage", SOLVER_COLORS["single-stage"]), ("stage2", SOLVER_COLORS["stage2"])],
        110,
        56,
    )

    panels = [
        (80.0, 90.0, 450.0, 500.0, "field_error (log scale)", "score", "field_error"),
        (530.0, 90.0, 900.0, 500.0, "max_curvature", "score", "max_curvature"),
    ]
    for left, top, right, bottom, xlabel, ylabel, metric in panels:
        canvas.line(left, top, left, bottom, stroke="#7b8794", stroke_width="1.2")
        canvas.line(left, bottom, right, bottom, stroke="#7b8794", stroke_width="1.2")
        canvas.text((left + right) / 2, 536, xlabel, anchor="middle", size=14)
        canvas.text(left - 52, (top + bottom) / 2, ylabel, anchor="middle", size=14, rotate=-90)
        for tick in [0.0, 0.25, 0.5, 0.75, 1.0]:
            y = _linear_scale(0.0, 1.0, bottom, top, tick)
            canvas.line(left, y, right, y, stroke=GRID_COLOR, stroke_width="1")
            canvas.text(left - 10, y + 4, f"{tick:.2f}", anchor="end", size=12, fill="#52606d")
        if metric == "field_error":
            for tick in [1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 3e-2]:
                if fe_min <= tick <= fe_max * 1.05:
                    x = _log_scale(fe_min, fe_max, left, right, tick)
                    canvas.line(x, top, x, bottom, stroke=GRID_COLOR, stroke_width="1")
                    canvas.text(x, bottom + 22, f"{tick:.0e}", anchor="middle", size=12, fill="#52606d")
        else:
            for tick in [20, 40, 60, 80, 100, 120]:
                if curv_min <= tick <= curv_max * 1.05:
                    x = _linear_scale(curv_min, curv_max, left, right, tick)
                    canvas.line(x, top, x, bottom, stroke=GRID_COLOR, stroke_width="1")
                    canvas.text(x, bottom + 22, str(tick), anchor="middle", size=12, fill="#52606d")

    for row in rows:
        score_y_left = _linear_scale(0.0, 1.0, 500.0, 90.0, row["score"])
        x_fe = _log_scale(fe_min, fe_max, 80.0, 450.0, row["field_error"])
        x_curv = _linear_scale(curv_min, curv_max, 530.0, 900.0, row["max_curvature"])
        color = SOLVER_COLORS.get(row["solver"], "#999999")
        canvas.circle(x_fe, score_y_left, 4.2, fill=color, fill_opacity="0.45", stroke="none")
        canvas.circle(x_curv, score_y_left, 4.2, fill=color, fill_opacity="0.45", stroke="none")

    path = outdir / "score_vs_physics.svg"
    canvas.save(path)
    return path


def plot_crash_parameter_sensitivity(outdir: Path) -> Path:
    db = lab._get_db()
    rows = db.execute(
        """
        SELECT *
        FROM runs
        WHERE solver='single-stage'
          AND status IN ('crash', 'pass')
        """
    ).fetchall()
    splits = lab._best_feature_splits(rows, "crash", 12)[:8]

    canvas = SvgCanvas(980, 580)
    left = 270.0
    right = 920.0
    top = 70.0
    bottom = 495.0
    canvas.text(490, 34, "Single-Stage Crash Parameter Sensitivity", anchor="middle", size=22, weight="700")
    canvas.line(left, top, left, bottom, stroke="#7b8794", stroke_width="1.2")
    canvas.line(left, bottom, right, bottom, stroke="#7b8794", stroke_width="1.2")
    canvas.text((left + right) / 2, 548, "best univariate crash split gain", anchor="middle", size=14)
    canvas.text(38, (top + bottom) / 2, "parameter", anchor="middle", size=14, rotate=-90)
    canvas.text(955, (top + bottom) / 2, "crash-rate split", anchor="middle", size=13, rotate=90)
    max_gain = max(float(split["gain"]) for split in splits) if splits else 1.0
    for tick in [0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3]:
        if tick <= max_gain * 1.1:
            x = _linear_scale(0.0, max_gain * 1.1, left, right, tick)
            canvas.line(x, top, x, bottom, stroke=GRID_COLOR, stroke_width="1")
            canvas.text(x, bottom + 22, f"{tick:.2f}", anchor="middle", size=12, fill="#52606d")

    row_slot = (bottom - top) / max(len(splits), 1)
    bar_height = row_slot * 0.56
    for idx, split in enumerate(splits):
        y = top + idx * row_slot + (row_slot - bar_height) / 2
        bar_width = _linear_scale(0.0, max_gain * 1.1, 0.0, right - left, float(split["gain"]))
        canvas.rect(left, y, bar_width, bar_height, fill="#457b9d", stroke="none")
        canvas.text(left - 12, y + bar_height / 2 + 4, str(split["feature"]), anchor="end", size=12)
        canvas.text(
            left + bar_width + 8,
            y + bar_height / 2 + 4,
            (
                f"thr={float(split['threshold']):g}, "
                f"L={int(split['left_positive'])}/{int(split['left_count'])}, "
                f"R={int(split['right_positive'])}/{int(split['right_count'])}"
            ),
            size=11,
            fill="#52606d",
        )

    path = outdir / "crash_parameter_sensitivity.svg"
    canvas.save(path)
    return path


def write_report(report_path: Path, image_paths: list[Path]) -> None:
    captions = {
        "solver_status_breakdown.svg": "Solver-level pass/fail/crash split.",
        "single_stage_ct_tradeoff.svg": "Single-stage crash volume versus pass-only mean score by curvature threshold.",
        "scored_pareto_scatter.svg": "Scored pass runs in field-error / curvature space with the Pareto frontier highlighted.",
        "single_stage_crash_rule_map.svg": "Single-stage crash/pass scatter with learned `maxiter=350` and `CT=25` rule boundaries.",
        "single_stage_equilibrium_scores.svg": "Top single-stage equilibria by mean pass score, with best-score overlay.",
        "score_vs_physics.svg": "Two-panel scatter of score against field error and max curvature.",
        "crash_parameter_sensitivity.svg": "Best single-feature crash-separation gains for the single-stage cohort.",
    }
    lines = [
        "# Analysis Plots",
        "",
        f"- Source: `{lab.RESULTS_PATH}`",
        f"- Output directory: `{image_paths[0].parent}`",
        "",
        "## Files",
        "",
    ]
    for path in image_paths:
        lines.append(f"- `{path.name}`: {captions[path.name]}")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render reproducible SVG plots for the current autoresearch analysis.",
    )
    parser.add_argument(
        "--outdir",
        default=str(lab.REPO_ROOT / "docs" / "analysis_plots_2026-04-03"),
        help="Directory to write plot images into.",
    )
    parser.add_argument(
        "--report",
        default=str(lab.REPO_ROOT / "docs" / "analysis_plots_2026-04-03.md"),
        help="Markdown index file to write.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outdir = _ensure_outdir(Path(args.outdir))
    image_paths = [
        plot_solver_status_breakdown(outdir),
        plot_single_stage_ct_tradeoff(outdir),
        plot_scored_pareto_scatter(outdir),
        plot_single_stage_crash_rule_map(outdir),
        plot_single_stage_equilibrium_scores(outdir),
        plot_score_vs_physics(outdir),
        plot_crash_parameter_sensitivity(outdir),
    ]
    write_report(Path(args.report), image_paths)
    print("Generated plots:")
    for path in image_paths:
        print(path)
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
