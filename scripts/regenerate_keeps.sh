#!/usr/bin/env bash
# Regenerate seeds for all 27 "keep" runs from results.tsv.
# Each run uses the current run_one.py which persists biot_savart_opt.json to stage2_seeds/.
# Order=3 runs are non-deterministic; FE may differ but seeds will still be valid.

set -euo pipefail
cd "$(dirname "$0")/.."

RUN="python scripts/run_one.py"

echo "=== Regenerating 27 keep seeds ==="

# Run 3: CT=30, CW=0.001 (all other defaults)
echo "[1/27] Run 3: CT=30, CW=0.001"
$RUN --cc-weight 100 --curvature-weight 0.001 --curvature-threshold 30 --length-weight 0.0005 --toroidal-flux 0.24 --maxiter 400

# Run 9: CCW=50, LW=0.0001, CT=30, CW=0.001
echo "[2/27] Run 9: CCW=50, LW=0.0001, CT=30, CW=0.001"
$RUN --cc-weight 50 --curvature-weight 0.001 --curvature-threshold 30 --length-weight 0.0001 --toroidal-flux 0.24 --maxiter 400

# Run 37: CCW=45, CW=0.001, CT=30, LW=0.0001, maxiter=400
echo "[3/27] Run 37: CCW=45, maxiter=400"
$RUN --cc-weight 45 --curvature-weight 0.001 --curvature-threshold 30 --length-weight 0.0001 --toroidal-flux 0.24 --maxiter 400

# Run 45: CCW=45, CW=0.0008, CT=30, LW=0.0001, maxiter=400
echo "[4/27] Run 45: CCW=45, CW=0.0008"
$RUN --cc-weight 45 --curvature-weight 0.0008 --curvature-threshold 30 --length-weight 0.0001 --toroidal-flux 0.24 --maxiter 400

# Run 50: TF=0.22 at frontier (CCW=45, CW=0.0008, CT=30, LW=0.0001)
echo "[5/27] Run 50: TF=0.22"
$RUN --cc-weight 45 --curvature-weight 0.0008 --curvature-threshold 30 --length-weight 0.0001 --toroidal-flux 0.22 --maxiter 400

# Run 56: CCW=44, CW=0.00085, TF=0.22
echo "[6/27] Run 56: CCW=44, CW=0.00085, TF=0.22"
$RUN --cc-weight 44 --curvature-weight 0.00085 --curvature-threshold 30 --length-weight 0.0001 --toroidal-flux 0.22 --maxiter 400

# Run 68: TF=0.215 (CCW=44, CW=0.00085, CT=30, LW=0.0001)
echo "[7/27] Run 68: TF=0.215"
$RUN --cc-weight 44 --curvature-weight 0.00085 --curvature-threshold 30 --length-weight 0.0001 --toroidal-flux 0.215 --maxiter 400

# Run 78: CCT=0.04, TF=0.215, maxiter=400
echo "[8/27] Run 78: CCT=0.04"
$RUN --cc-weight 44 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.04 --length-weight 0.0001 --toroidal-flux 0.215 --maxiter 400

# Run 79: CCT=0.04, maxiter=500
echo "[9/27] Run 79: CCT=0.04, maxiter=500"
$RUN --cc-weight 44 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.04 --length-weight 0.0001 --toroidal-flux 0.215 --maxiter 500

# Run 80: CCT=0.035, maxiter=500
echo "[10/27] Run 80: CCT=0.035"
$RUN --cc-weight 44 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.035 --length-weight 0.0001 --toroidal-flux 0.215 --maxiter 500

# Run 82: CCT=0.032, maxiter=500
echo "[11/27] Run 82: CCT=0.032"
$RUN --cc-weight 44 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.032 --length-weight 0.0001 --toroidal-flux 0.215 --maxiter 500

# Run 83: CCT=0.031, maxiter=500
echo "[12/27] Run 83: CCT=0.031"
$RUN --cc-weight 44 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.031 --length-weight 0.0001 --toroidal-flux 0.215 --maxiter 500

# Run 84: CCW=42, CCT=0.031, maxiter=500
echo "[13/27] Run 84: CCW=42, CCT=0.031"
$RUN --cc-weight 42 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.031 --length-weight 0.0001 --toroidal-flux 0.215 --maxiter 500

# Run 86: LW=5e-5, CCW=42, CCT=0.031
echo "[14/27] Run 86: LW=5e-5"
$RUN --cc-weight 42 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.031 --length-weight 5e-5 --toroidal-flux 0.215 --maxiter 500

# Run 89: LW=4e-5
echo "[15/27] Run 89: LW=4e-5"
$RUN --cc-weight 42 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.031 --length-weight 4e-5 --toroidal-flux 0.215 --maxiter 500

# Run 102: MR=0.92
echo "[16/27] Run 102: MR=0.92"
$RUN --cc-weight 42 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.031 --length-weight 4e-5 --toroidal-flux 0.215 --major-radius 0.92 --maxiter 500

# Run 103: MR=0.925 (best order=2)
echo "[17/27] Run 103: MR=0.925 (best order=2)"
$RUN --cc-weight 42 --curvature-weight 0.00085 --curvature-threshold 30 --cc-threshold 0.031 --length-weight 4e-5 --toroidal-flux 0.215 --major-radius 0.925 --maxiter 500

# Run 137: order=3 breakthrough (CCW=50, CW=0.005, CT=20, MR=0.925)
echo "[18/27] Run 137: order=3 breakthrough"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.031 --length-weight 4e-5 --toroidal-flux 0.215 --major-radius 0.925 --order 3 --maxiter 800

# Run 139: replication of 137
echo "[19/27] Run 139: order=3 replication"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.031 --length-weight 4e-5 --toroidal-flux 0.215 --major-radius 0.925 --order 3 --maxiter 800

# Run 145: MR=0.92, order=3
echo "[20/27] Run 145: MR=0.92, order=3"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.031 --length-weight 4e-5 --toroidal-flux 0.215 --major-radius 0.92 --order 3 --maxiter 800

# Run 147: MR=0.915, order=3
echo "[21/27] Run 147: MR=0.915, order=3"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.031 --length-weight 4e-5 --toroidal-flux 0.215 --major-radius 0.915 --order 3 --maxiter 800

# Run 150: CCT=0.025, order=3, MR=0.915
echo "[22/27] Run 150: CCT=0.025, order=3"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.025 --length-weight 4e-5 --toroidal-flux 0.215 --major-radius 0.915 --order 3 --maxiter 800

# Run 152: CCT=0.022, order=3
echo "[23/27] Run 152: CCT=0.022"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.022 --length-weight 4e-5 --toroidal-flux 0.215 --major-radius 0.915 --order 3 --maxiter 800

# Run 153: CCT=0.021, order=3
echo "[24/27] Run 153: CCT=0.021"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.021 --length-weight 4e-5 --toroidal-flux 0.215 --major-radius 0.915 --order 3 --maxiter 800

# Run 154: LW=1e-5, CCT=0.021, order=3 (best reliable)
echo "[25/27] Run 154: LW=1e-5 (best reliable)"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.021 --length-weight 1e-5 --toroidal-flux 0.215 --major-radius 0.915 --order 3 --maxiter 800

# Run 156: LW=8e-6 (best ever FE=0.00419)
echo "[26/27] Run 156: LW=8e-6 (best ever)"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.021 --length-weight 8e-6 --toroidal-flux 0.215 --major-radius 0.915 --order 3 --maxiter 800

# Run 163: LW=1e-5 replication
echo "[27/27] Run 163: LW=1e-5 replication"
$RUN --cc-weight 50 --curvature-weight 0.005 --curvature-threshold 20 --cc-threshold 0.021 --length-weight 1e-5 --toroidal-flux 0.215 --major-radius 0.915 --order 3 --maxiter 800

echo "=== Done. Check stage2_seeds/ for persisted seeds. ==="
