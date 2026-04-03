from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_results_annotated.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_results_annotated_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/build_results_annotated.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reclassify_row_marks_legacy_pass_as_incomplete() -> None:
    mod = _load_module()
    result = mod.reclassify_row(
        {
            "solver": "single-stage",
            "status": "pass",
            "score": 0.91,
            "field_error": 0.001,
            "max_curvature": 20.0,
            "final_iota": 0.15,
            "final_volume": None,
            "self_intersecting": False,
        }
    )
    assert result["legacy_contract"] is True
    assert result["reclassified_status"] == "fail"
    assert result["reclassified_status_reason"] == "incomplete_metrics"
    assert result["reclassified_missing_metrics"] == ["FINAL_VOLUME"]
    assert result["status_changed"] is True


def test_enrich_post_process_fields_uses_artifact_results_json(tmp_path: Path) -> None:
    mod = _load_module()
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    (artifact_dir / "results.json").write_text(
        json.dumps(
            {
                "mpol": 10,
                "ntor": 6,
                "CONSTRAINT_WEIGHT": 123.0,
                "FINAL_IOTA": 0.16,
                "FINAL_VOLUME": 0.11,
                "COIL_LENGTH": 2.8,
                "MAX_CURVATURE": 18.5,
                "CURVE_CURVE_MIN_DIST": 0.09,
                "NONQS_RATIO": 1.0e-4,
            }
        )
    )
    row = {
        "solver": "single-stage",
        "status": "pass",
        "score": 0.98,
        "single_stage_artifact_dir": str(artifact_dir),
        "params": {"mpol": 8, "ntor": 4, "constraint_weight": 44.0},
    }
    enriched = mod.enrich_post_process_fields(row)
    assert enriched["pp_enrichment_status"] == "artifact_results_partial"
    assert enriched["pp_enrichment_source_kind"] == "single_stage_artifact_dir"
    assert enriched["pp_mpol"] == 10
    assert enriched["pp_ntor"] == 6
    assert enriched["pp_constraint_weight"] == 123.0
    assert enriched["pp_iota"] == 0.16
    assert enriched["pp_volume"] == 0.11
    assert enriched["pp_all_coils_min_cc_distance"] == 0.09
    assert enriched["pp_field_sources"]["pp_all_coils_min_cc_distance"] == "artifact:CURVE_CURVE_MIN_DIST"


def test_enrich_post_process_fields_falls_back_to_row_and_params() -> None:
    mod = _load_module()
    row = {
        "solver": "single-stage",
        "status": "pass",
        "score": 0.98,
        "final_iota": 0.15,
        "final_volume": 0.1,
        "boozer_residual": 3.2e-6,
        "nonqs_ratio": 1.1e-4,
        "coil_length": 2.7,
        "max_curvature": 19.2,
        "curve_curve_min_dist": 0.08,
        "params": {"mpol": 10, "ntor": 6, "constraint_weight": 50.0},
    }
    enriched = mod.enrich_post_process_fields(row)
    assert enriched["pp_enrichment_status"] == "row_partial"
    assert enriched["pp_mpol"] == 10
    assert enriched["pp_constraint_weight"] == 50.0
    assert enriched["pp_iota"] == 0.15
    assert enriched["pp_volume"] == 0.1
    assert enriched["pp_all_coils_min_cc_distance"] == 0.08
    assert enriched["pp_norm_squared_flux"] is None
    assert "pp_norm_squared_flux" in enriched["pp_missing_fields"]
