from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path


RUN_ONE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_one.py"


def _load_run_one_module():
    spec = importlib.util.spec_from_file_location("run_one_under_test", RUN_ONE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/run_one.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classify_completed_stage2_requires_core_metrics() -> None:
    run_one = _load_run_one_module()
    status, reason, missing = run_one._classify_completed_run(
        {"FIELD_ERROR": 0.01, "MAX_CURVATURE": None, "SELF_INTERSECTING": False},
        "stage2",
        0.75,
    )
    assert status == "fail"
    assert reason == "incomplete_metrics"
    assert missing == ["MAX_CURVATURE"]


def test_classify_completed_single_stage_requires_iota_and_volume() -> None:
    run_one = _load_run_one_module()
    status, reason, missing = run_one._classify_completed_run(
        {"FIELD_ERROR": 0.001, "MAX_CURVATURE": 20.0, "FINAL_IOTA": 0.15},
        "single-stage",
        0.9,
    )
    assert status == "fail"
    assert reason == "incomplete_metrics"
    assert missing == ["FINAL_VOLUME"]


def test_classify_completed_run_respects_optimizer_failure() -> None:
    run_one = _load_run_one_module()
    status, reason, missing = run_one._classify_completed_run(
        {
            "FIELD_ERROR": 0.001,
            "MAX_CURVATURE": 20.0,
            "FINAL_IOTA": 0.15,
            "FINAL_VOLUME": 0.10,
            "OPTIMIZER_SUCCESS": False,
        },
        "single-stage",
        0.9,
    )
    assert status == "fail"
    assert reason == "optimizer_unsuccessful"
    assert missing == []


def test_classify_completed_run_passes_when_complete_and_nonzero() -> None:
    run_one = _load_run_one_module()
    status, reason, missing = run_one._classify_completed_run(
        {
            "FIELD_ERROR": 0.001,
            "MAX_CURVATURE": 20.0,
            "FINAL_IOTA": 0.15,
            "FINAL_VOLUME": 0.10,
            "SELF_INTERSECTING": False,
            "OPTIMIZER_SUCCESS": None,
        },
        "single-stage",
        0.9,
    )
    assert status == "pass"
    assert reason == "ok"
    assert missing == []


def test_resolve_stage2_seed_refuses_ambiguous_legacy_matches(tmp_path: Path) -> None:
    run_one = _load_run_one_module()
    plasma_dir = tmp_path / "seed_store" / "outputs-test.nc"
    plasma_dir.mkdir(parents=True)
    for name, fe in (("seed_a", 0.02), ("seed_b", 0.01)):
        seed_dir = plasma_dir / name
        seed_dir.mkdir()
        (seed_dir / "biot_savart_opt.json").write_text("{}")
        (seed_dir / "results.json").write_text(
            f'{{"MAJOR_RADIUS": 0.915, "order": 2, "SELF_INTERSECTING": false, "FIELD_ERROR": {fe}}}'
        )

    run_one.STAGE2_SEED_STORE = tmp_path / "seed_store"
    run_one.COLUMBIA_DATABASE = tmp_path / "database"
    args = Namespace(
        stage2_bs_path=None,
        equilibrium="iota15",
        major_radius=0.915,
        order=2,
        allow_ambiguous_seed_match=False,
    )

    seed = run_one._resolve_stage2_seed(args, "test.nc")
    assert seed is None
    assert args._seed_resolution_mode == "ambiguous_refused"
    assert args._seed_candidate_count == 2


def test_resolve_stage2_seed_can_pick_lowest_fe_with_override(tmp_path: Path) -> None:
    run_one = _load_run_one_module()
    plasma_dir = tmp_path / "seed_store" / "outputs-test.nc"
    plasma_dir.mkdir(parents=True)
    expected = None
    for name, fe in (("seed_a", 0.02), ("seed_b", 0.01)):
        seed_dir = plasma_dir / name
        seed_dir.mkdir()
        (seed_dir / "biot_savart_opt.json").write_text("{}")
        (seed_dir / "results.json").write_text(
            f'{{"MAJOR_RADIUS": 0.915, "order": 2, "SELF_INTERSECTING": false, "FIELD_ERROR": {fe}}}'
        )
        if fe == 0.01:
            expected = str(seed_dir / "biot_savart_opt.json")

    run_one.STAGE2_SEED_STORE = tmp_path / "seed_store"
    run_one.COLUMBIA_DATABASE = tmp_path / "database"
    args = Namespace(
        stage2_bs_path=None,
        equilibrium="iota15",
        major_radius=0.915,
        order=2,
        allow_ambiguous_seed_match=True,
    )

    seed = run_one._resolve_stage2_seed(args, "test.nc")
    assert seed == expected
    assert args._seed_resolution_mode == "ambiguous_best_fe"
    assert args._seed_candidate_count == 2
