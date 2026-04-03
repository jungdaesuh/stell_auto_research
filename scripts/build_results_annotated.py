#!/usr/bin/env python3
"""Build a combined derived ledger from results.jsonl.

The output preserves every original row and appends:
- reclassified_* fields using the current run_one.py status contract
- pp_* fields that mirror post_process.py-style physical columns when they can
  be recovered exactly or via safe aliases from surviving artifacts / row data
- provenance for what was and was not recoverable
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "results.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "results_annotated.jsonl"
RUN_ONE_PATH = Path(__file__).resolve().parent / "run_one.py"

PP_FIELDS = (
    "pp_mpol",
    "pp_ntor",
    "pp_nfp",
    "pp_constraint_weight",
    "pp_boozer_residual",
    "pp_volume",
    "pp_iota",
    "pp_nonqs_ratio",
    "pp_norm_squared_flux",
    "pp_coil_length",
    "pp_coil_max_curvature",
    "pp_coil_opp_end_curvature",
    "pp_banana_coils_min_cc_distance",
    "pp_all_coils_min_cc_distance",
    "pp_banana_coils_min_cs_distance",
    "pp_boozer_surface_file",
)


def _load_run_one_module():
    spec = importlib.util.spec_from_file_location("run_one_for_annotation", RUN_ONE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/run_one.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUN_ONE = _load_run_one_module()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _row_to_metrics(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "FIELD_ERROR": row.get("field_error"),
        "MAX_CURVATURE": row.get("max_curvature"),
        "FINAL_IOTA": row.get("final_iota"),
        "FINAL_VOLUME": row.get("final_volume"),
        "SELF_INTERSECTING": row.get("self_intersecting", False),
        "OPTIMIZER_SUCCESS": row.get("optimizer_success"),
    }


def reclassify_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = _row_to_metrics(row)
    score = float(row.get("score", 0.0) or 0.0)
    status, reason, missing = RUN_ONE._classify_completed_run(metrics, row["solver"], score)
    original_reason = row.get("status_reason")
    score_formula = row.get("score_formula") or row["solver"]
    return {
        "legacy_contract": not {
            "status_reason",
            "score_formula",
        }.issubset(row.keys()),
        "reclassified_status": status,
        "reclassified_status_reason": reason,
        "reclassified_missing_metrics": missing,
        "status_changed": row.get("status") != status,
        "status_reason_changed": original_reason != reason if original_reason is not None else None,
        "reclassified_score_formula": score_formula,
    }


def _find_existing_path(path_str: Any) -> Path | None:
    if not isinstance(path_str, str) or not path_str:
        return None
    path = Path(path_str)
    return path if path.exists() else None


def _find_artifact_context(row: dict[str, Any]) -> tuple[Path | None, Path | None, str | None]:
    artifact_dir = _find_existing_path(row.get("single_stage_artifact_dir"))
    if artifact_dir is not None and artifact_dir.is_dir():
        results_path = artifact_dir / "results.json"
        return artifact_dir, results_path if results_path.exists() else None, "single_stage_artifact_dir"

    stage2_seed_path = _find_existing_path(row.get("stage2_seed_path"))
    if stage2_seed_path is not None:
        artifact_dir = stage2_seed_path.parent
        results_path = artifact_dir / "results.json"
        return artifact_dir, results_path if results_path.exists() else None, "stage2_seed_path"

    run_dir = _find_existing_path(row.get("run_dir"))
    if run_dir is not None and run_dir.is_dir():
        direct = run_dir / "results.json"
        if direct.exists():
            return run_dir, direct, "run_dir"
        matches = sorted(run_dir.rglob("results.json"))
        if matches:
            return matches[0].parent, matches[0], "run_dir"

    return None, None, None


def _load_artifact_results(results_path: Path | None) -> dict[str, Any]:
    if results_path is None:
        return {}
    with results_path.open() as handle:
        return json.load(handle)


def _detect_boozer_surface_file(artifact_dir: Path | None) -> str | None:
    if artifact_dir is None:
        return None
    for candidate in sorted(artifact_dir.iterdir()):
        if "boozer" in candidate.name.lower():
            return str(candidate)
    return None


def _assign_if_present(
    target: dict[str, Any],
    field_sources: dict[str, str],
    field: str,
    value: Any,
    source: str,
) -> None:
    if value is None or field in field_sources:
        return
    target[field] = value
    field_sources[field] = source


def enrich_post_process_fields(row: dict[str, Any]) -> dict[str, Any]:
    artifact_dir, results_path, source_kind = _find_artifact_context(row)
    artifact = _load_artifact_results(results_path)
    params = row.get("params") or {}

    derived = {field: None for field in PP_FIELDS}
    field_sources: dict[str, str] = {}

    _assign_if_present(derived, field_sources, "pp_mpol", artifact.get("mpol"), "artifact:mpol")
    _assign_if_present(derived, field_sources, "pp_mpol", params.get("mpol"), "params:mpol")

    _assign_if_present(derived, field_sources, "pp_ntor", artifact.get("ntor"), "artifact:ntor")
    _assign_if_present(derived, field_sources, "pp_ntor", params.get("ntor"), "params:ntor")

    _assign_if_present(derived, field_sources, "pp_nfp", artifact.get("nfp"), "artifact:nfp")
    _assign_if_present(derived, field_sources, "pp_nfp", artifact.get("NFP"), "artifact:NFP")

    _assign_if_present(
        derived,
        field_sources,
        "pp_constraint_weight",
        artifact.get("CONSTRAINT_WEIGHT"),
        "artifact:CONSTRAINT_WEIGHT",
    )
    _assign_if_present(
        derived,
        field_sources,
        "pp_constraint_weight",
        params.get("constraint_weight"),
        "params:constraint_weight",
    )

    _assign_if_present(
        derived,
        field_sources,
        "pp_boozer_residual",
        artifact.get("BOOZER_RESIDUAL"),
        "artifact:BOOZER_RESIDUAL",
    )
    _assign_if_present(
        derived,
        field_sources,
        "pp_boozer_residual",
        row.get("boozer_residual"),
        "row:boozer_residual",
    )

    _assign_if_present(derived, field_sources, "pp_volume", artifact.get("FINAL_VOLUME"), "artifact:FINAL_VOLUME")
    _assign_if_present(derived, field_sources, "pp_volume", row.get("final_volume"), "row:final_volume")

    _assign_if_present(derived, field_sources, "pp_iota", artifact.get("FINAL_IOTA"), "artifact:FINAL_IOTA")
    _assign_if_present(derived, field_sources, "pp_iota", row.get("final_iota"), "row:final_iota")

    _assign_if_present(
        derived,
        field_sources,
        "pp_nonqs_ratio",
        artifact.get("NONQS_RATIO"),
        "artifact:NONQS_RATIO",
    )
    _assign_if_present(
        derived,
        field_sources,
        "pp_nonqs_ratio",
        row.get("nonqs_ratio"),
        "row:nonqs_ratio",
    )

    _assign_if_present(derived, field_sources, "pp_coil_length", artifact.get("COIL_LENGTH"), "artifact:COIL_LENGTH")
    _assign_if_present(derived, field_sources, "pp_coil_length", row.get("coil_length"), "row:coil_length")

    _assign_if_present(
        derived,
        field_sources,
        "pp_coil_max_curvature",
        artifact.get("MAX_CURVATURE"),
        "artifact:MAX_CURVATURE",
    )
    _assign_if_present(
        derived,
        field_sources,
        "pp_coil_max_curvature",
        row.get("max_curvature"),
        "row:max_curvature",
    )

    _assign_if_present(
        derived,
        field_sources,
        "pp_coil_opp_end_curvature",
        artifact.get("COIL_OPP_END_CURVATURE"),
        "artifact:COIL_OPP_END_CURVATURE",
    )

    _assign_if_present(
        derived,
        field_sources,
        "pp_banana_coils_min_cc_distance",
        artifact.get("BANANA_COILS_MIN_CC_DISTANCE"),
        "artifact:BANANA_COILS_MIN_CC_DISTANCE",
    )

    _assign_if_present(
        derived,
        field_sources,
        "pp_all_coils_min_cc_distance",
        artifact.get("CURVE_CURVE_MIN_DIST"),
        "artifact:CURVE_CURVE_MIN_DIST",
    )
    _assign_if_present(
        derived,
        field_sources,
        "pp_all_coils_min_cc_distance",
        row.get("curve_curve_min_dist"),
        "row:curve_curve_min_dist",
    )

    _assign_if_present(
        derived,
        field_sources,
        "pp_banana_coils_min_cs_distance",
        artifact.get("BANANA_COILS_MIN_CS_DISTANCE"),
        "artifact:BANANA_COILS_MIN_CS_DISTANCE",
    )

    _assign_if_present(
        derived,
        field_sources,
        "pp_norm_squared_flux",
        artifact.get("NORM_SQUARED_FLUX"),
        "artifact:NORM_SQUARED_FLUX",
    )

    _assign_if_present(
        derived,
        field_sources,
        "pp_boozer_surface_file",
        artifact.get("BOOZER_SURFACE_FILE"),
        "artifact:BOOZER_SURFACE_FILE",
    )
    _assign_if_present(
        derived,
        field_sources,
        "pp_boozer_surface_file",
        _detect_boozer_surface_file(artifact_dir),
        "artifact_scan:boozer*",
    )

    if results_path is not None:
        enrichment_status = "artifact_results_partial"
    elif field_sources:
        enrichment_status = "row_partial"
    else:
        enrichment_status = "unavailable"

    missing_fields = [field for field in PP_FIELDS if derived[field] is None]
    return {
        **derived,
        "pp_enrichment_status": enrichment_status,
        "pp_enrichment_source_kind": source_kind,
        "pp_enrichment_source_path": str(results_path) if results_path is not None else None,
        "pp_field_sources": field_sources,
        "pp_missing_fields": missing_fields,
    }


def annotate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for row in rows:
        new_row = dict(row)
        new_row["annotation_version"] = 1
        new_row.update(reclassify_row(row))
        new_row.update(enrich_post_process_fields(row))
        annotated.append(new_row)
    return annotated


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "status_changed": sum(1 for row in rows if row.get("status_changed")),
        "artifact_results_partial": sum(
            1 for row in rows if row.get("pp_enrichment_status") == "artifact_results_partial"
        ),
        "row_partial": sum(1 for row in rows if row.get("pp_enrichment_status") == "row_partial"),
        "unavailable": sum(1 for row in rows if row.get("pp_enrichment_status") == "unavailable"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build combined annotated results JSONL.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Source results.jsonl path.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output annotated JSONL path.",
    )
    args = parser.parse_args()

    rows = _read_jsonl(args.input)
    annotated = annotate_rows(rows)
    _write_jsonl(args.output, annotated)
    summary = build_summary(annotated)
    print(json.dumps({"input": str(args.input), "output": str(args.output), **summary}, indent=2))


if __name__ == "__main__":
    main()
