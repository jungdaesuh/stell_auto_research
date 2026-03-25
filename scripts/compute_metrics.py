#!/usr/bin/env python3
"""Compute comparison metrics from single-stage or Stage 2 output artifacts.

Usage:
    # Compute metrics for one output dir
    python scripts/compute_metrics.py /path/to/output-dir

    # Backfill all single-stage results
    python scripts/compute_metrics.py --backfill single_stage_results/

    # Backfill Stage 2 seeds (coil-only metrics)
    python scripts/compute_metrics.py --backfill stage2_seeds/

Merges computed metrics into results.json in each output dir.
Idempotent: re-running produces the same values.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

# HBT vacuum vessel — physical constant, not an optimization parameter.
# Source: single_stage_banana_example.py lines 768-773
VV_NFP = 5
VV_R0 = 0.976
VV_A = 0.222

# Default TF coil count for HBT (banana coils start at this index)
DEFAULT_NUM_TF_COILS = 20


def _build_vv_surface():
    """Construct the HBT vacuum vessel surface."""
    from simsopt.geo import SurfaceRZFourier
    vv = SurfaceRZFourier(nfp=VV_NFP, stellsym=True)
    vv.set_rc(0, 0, VV_R0)
    vv.set_rc(1, 0, VV_A)
    vv.set_zs(1, 0, VV_A)
    return vv


def _get_banana_curve(bs, num_tf_coils=DEFAULT_NUM_TF_COILS):
    """Extract the base banana curve (first non-TF coil) from BiotSavart."""
    coils = bs.coils
    if len(coils) <= num_tf_coils:
        return None
    return coils[num_tf_coils].curve


def compute_metrics(artifact_dir: Path, num_tf_coils: int = DEFAULT_NUM_TF_COILS) -> dict:
    """Compute all available metrics from artifacts in the given directory.

    Returns a dict of metric_name -> float | None. None means the metric
    could not be computed (missing artifact or inapplicable solver type).
    """
    from scipy.spatial.distance import cdist
    from simsopt._core.optimizable import load
    from simsopt.geo import CurveLength
    from simsopt.geo.curveobjectives import CurveSurfaceDistance

    metrics = {
        "COIL_LENGTH": None,
        "CURVE_SURFACE_MIN_DIST": None,
        "SURFACE_VESSEL_MIN_DIST": None,
        "MAX_FORCE": None,              # blocked: needs conductor cross-section
        "LEAD_END_CURVATURE": None,     # blocked: needs "lead" convention from team
        "NON_LEAD_END_CURVATURE": None, # blocked: needs "lead" convention from team
    }

    bs_path = artifact_dir / "biot_savart_opt.json"
    surf_path = artifact_dir / "surf_opt.json"

    if not bs_path.exists():
        return metrics

    # Load BiotSavart (coils)
    bs = load(str(bs_path))
    coils = bs.coils
    curves = [c.curve for c in coils]

    # --- Coil length (banana curve) ---
    banana_curve = _get_banana_curve(bs, num_tf_coils)
    if banana_curve is not None:
        metrics["COIL_LENGTH"] = float(CurveLength(banana_curve).J())

    # --- Metrics requiring the optimized Boozer surface ---
    if surf_path.exists():
        surf = load(str(surf_path))
        surf_xyz = surf.gamma().reshape((-1, 3))

        # Coil-plasma min distance (all curves to Boozer surface)
        cs_dists = []
        for curve in curves:
            d = np.min(cdist(curve.gamma(), surf_xyz))
            cs_dists.append(d)
        metrics["CURVE_SURFACE_MIN_DIST"] = float(min(cs_dists))

        # Plasma-vessel min distance (Boozer surface to VV)
        vv = _build_vv_surface()
        vv_xyz = vv.gamma().reshape((-1, 3))
        metrics["SURFACE_VESSEL_MIN_DIST"] = float(np.min(cdist(surf_xyz, vv_xyz)))

    return metrics


def process_dir(artifact_dir: Path, num_tf_coils: int = DEFAULT_NUM_TF_COILS) -> bool:
    """Compute metrics and merge into results.json. Returns True on success."""
    if not (artifact_dir / "biot_savart_opt.json").exists():
        return False

    t0 = time.time()
    try:
        new_metrics = compute_metrics(artifact_dir, num_tf_coils)
    except Exception as e:
        print(f"  ERROR: {artifact_dir}: {e}", file=sys.stderr)
        return False

    # Merge into existing results.json (or create if missing)
    results_path = artifact_dir / "results.json"
    if results_path.exists():
        with open(results_path) as f:
            existing = json.load(f)
    else:
        existing = {}
    existing.update({k: v for k, v in new_metrics.items() if v is not None})
    with open(results_path, "w") as f:
        json.dump(existing, f, indent=2)

    elapsed = time.time() - t0
    non_null = sum(1 for v in new_metrics.values() if v is not None)
    print(f"  {artifact_dir.name}: {non_null}/{len(new_metrics)} metrics in {elapsed:.1f}s")
    return True


def backfill(root: Path, num_tf_coils: int = DEFAULT_NUM_TF_COILS) -> None:
    """Walk a directory tree and compute metrics for every dir with biot_savart_opt.json."""
    dirs = sorted(root.rglob("biot_savart_opt.json"))
    print(f"Found {len(dirs)} artifact dirs under {root}")

    success = 0
    for bs_path in dirs:
        artifact_dir = bs_path.parent
        if process_dir(artifact_dir, num_tf_coils):
            success += 1

    print(f"\nDone: {success}/{len(dirs)} dirs processed successfully.")


def main():
    parser = argparse.ArgumentParser(description="Compute comparison metrics from artifacts.")
    parser.add_argument("path", type=Path, help="Output dir or --backfill root dir.")
    parser.add_argument("--backfill", action="store_true",
                        help="Walk the given directory tree and process all artifact dirs.")
    parser.add_argument("--num-tf-coils", type=int, default=DEFAULT_NUM_TF_COILS)
    args = parser.parse_args()

    if args.backfill:
        backfill(args.path, args.num_tf_coils)
    else:
        if not process_dir(args.path, args.num_tf_coils):
            print(f"No biot_savart_opt.json found in {args.path}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
