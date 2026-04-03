#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Precursor Stage 2 runs to regenerate usable order=2 seeds for the current
# single-stage frontier follow-up candidates.

python3 scripts/run_one.py --solver stage2 --equilibrium iota15 --order 2 --curvature-weight 0.01 --curvature-threshold 20 --cc-weight 100.0 --toroidal-flux 0.215 --length-weight 0.0005
python3 scripts/run_one.py --solver stage2 --equilibrium iota15p --order 2 --curvature-weight 0.0001 --curvature-threshold 20 --cc-weight 100.0 --toroidal-flux 0.215 --length-weight 0.0005
python3 scripts/run_one.py --solver stage2 --equilibrium iota20p --order 2 --curvature-weight 0.0001 --curvature-threshold 40 --cc-weight 100.0 --toroidal-flux 0.215 --length-weight 0.0005
