from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path


LAB_PATH = Path(__file__).resolve().parents[1] / "scripts" / "lab.py"


def _load_lab_module():
    spec = importlib.util.spec_from_file_location("lab_under_test", LAB_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/lab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spearman_correlation_preserves_sign() -> None:
    lab = _load_lab_module()
    rho = lab._spearman_correlation([1.0, 2.0, 3.0], [3.0, 2.0, 1.0])
    assert rho is not None
    assert rho == -1.0


def test_audit_markdown_keeps_ct40_scope_explicit(tmp_path: Path) -> None:
    lab = _load_lab_module()
    results_path = tmp_path / "results.jsonl"
    results_path.write_text(
        "\n".join(
            [
                '{"solver":"single-stage","equilibrium":"iota15","status":"crash","score":0.0,"params":{"curvature_threshold":40,"curvature_weight":0.1}}',
                '{"solver":"single-stage","equilibrium":"iota15","status":"crash","score":0.0,"params":{"curvature_threshold":40,"curvature_weight":0.1}}',
                '{"solver":"single-stage","equilibrium":"iota15","status":"pass","score":0.99,"field_error":0.0003,"max_curvature":19.9,"params":{"curvature_threshold":40,"curvature_weight":0.1}}',
                '{"solver":"stage2","equilibrium":"iota15","status":"pass","score":0.70,"field_error":0.01,"max_curvature":30.0,"params":{"curvature_threshold":40,"curvature_weight":0.0001}}',
                '{"solver":"single-stage","equilibrium":"iota16","status":"pass","score":0.80,"field_error":0.002,"max_curvature":20.0,"params":{"curvature_threshold":20,"curvature_weight":0.2}}',
                '{"solver":"stage2","equilibrium":"iota16","status":"pass","params":{"curvature_threshold":20,"curvature_weight":0.0001}}',
            ]
        )
        + "\n"
    )

    lab.RESULTS_PATH = results_path
    lab._DB = None

    markdown = lab.build_audit_markdown()

    assert "- Total rows: 6" in markdown
    assert "- Missing `score` rows: 1" in markdown
    assert "| single-stage / crash | 2 |" in markdown
    assert "| single-stage / pass | 1 |" in markdown
    assert "| stage2 / pass | 1 |" in markdown
    assert "| 40 | 3 | 1 | 0 | 2 | 0.3300 | 0.9900 | 0.9900 |" in markdown


def test_apply_score_filters_are_mutually_exclusive() -> None:
    lab = _load_lab_module()
    args = Namespace(scored_only=True, pass_only_scored=True)
    try:
        lab._apply_score_filters("1=1", {}, args)
    except SystemExit as exc:
        assert "mutually exclusive" in str(exc)
    else:
        raise AssertionError("expected mutually exclusive score filters to fail")


def test_apply_score_filters_add_expected_sql_clauses() -> None:
    lab = _load_lab_module()

    where, _ = lab._apply_score_filters(
        "solver='single-stage'",
        {},
        Namespace(scored_only=True, pass_only_scored=False),
    )
    assert "score IS NOT NULL" in where
    assert "status='pass'" not in where

    where, _ = lab._apply_score_filters(
        "solver='single-stage'",
        {},
        Namespace(scored_only=False, pass_only_scored=True),
    )
    assert "score IS NOT NULL" in where
    assert "status='pass'" in where


def test_pareto_frontier_keeps_only_non_dominated_rows() -> None:
    lab = _load_lab_module()

    class FakeRow(dict):
        pass

    rows = [
        FakeRow(name="a", field_error=1.0, max_curvature=2.0, coil_length=3.0),
        FakeRow(name="b", field_error=1.0, max_curvature=3.0, coil_length=4.0),
        FakeRow(name="c", field_error=0.8, max_curvature=2.5, coil_length=3.5),
    ]
    frontier = lab._pareto_frontier(rows, ("field_error", "max_curvature", "coil_length"))
    names = {row["name"] for row in frontier}
    assert names == {"a", "c"}


def test_crash_model_markdown_learns_interpretable_threshold(tmp_path: Path) -> None:
    lab = _load_lab_module()
    results_path = tmp_path / "results.jsonl"
    results_path.write_text(
        "\n".join(
            [
                '{"solver":"single-stage","equilibrium":"iota15","status":"pass","params":{"maxiter":150,"curvature_threshold":20,"order":2}}',
                '{"solver":"single-stage","equilibrium":"iota15","status":"pass","params":{"maxiter":150,"curvature_threshold":20,"order":2}}',
                '{"solver":"single-stage","equilibrium":"iota16","status":"pass","params":{"maxiter":200,"curvature_threshold":20,"order":4}}',
                '{"solver":"single-stage","equilibrium":"iota17","status":"crash","params":{"maxiter":400,"curvature_threshold":40,"order":2}}',
                '{"solver":"single-stage","equilibrium":"iota18","status":"crash","params":{"maxiter":400,"curvature_threshold":40,"order":2}}',
                '{"solver":"single-stage","equilibrium":"iota19","status":"crash","params":{"maxiter":500,"curvature_threshold":40,"order":2}}',
            ]
        )
        + "\n"
    )

    lab.RESULTS_PATH = results_path
    lab._DB = None

    markdown = lab.build_crash_model_markdown(
        Namespace(
            solver="single-stage",
            eq=None,
            order=None,
            target_status="crash",
            max_depth=2,
            min_leaf=2,
            min_gain=0.01,
        )
    )

    assert "- Rows in cohort: 6" in markdown
    assert "- `crash` rows: 3" in markdown
    assert "| maxiter |" in markdown or "| curvature_threshold |" in markdown
    assert "| crash | maxiter >" in markdown or "| crash | curvature_threshold >" in markdown
    assert "Training accuracy: 1.0000" in markdown
