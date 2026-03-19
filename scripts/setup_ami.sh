#!/bin/bash
# Run this ON the EC2 instance to set up SIMSOPT environment.
# After running, create an AMI from the instance for future launches.
#
# Usage: ssh -i ~/.ssh/columbia-profile.pem ubuntu@<IP> 'bash -s' < scripts/setup_ami.sh

set -euo pipefail

echo "=== Installing system dependencies ==="
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential gfortran libopenblas-dev \
  liblapack-dev pkg-config cmake git python3-dev python3-pip python3-venv curl screen

echo "=== Cloning SIMSOPT (candidate-fixed branch) ==="
cd ~
if [ ! -d simsopt ]; then
  git clone -b hho-runner-parameterization https://github.com/jungdaesuh/simsopt.git
fi
cd simsopt

echo "=== Creating venv and installing SIMSOPT ==="
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip setuptools wheel -q
pip install -e . 2>&1 | tail -5
pip install numba "ground==9.0.0" "bentley-ottmann==8.0.0" -q 2>&1 | tail -3

echo "=== Staging directories ==="
mkdir -p ~/equilibria ~/stage2_seeds

echo "=== Verifying installation ==="
python -c "import simsopt; print('simsopt OK:', simsopt.__file__)"
python -c "import numba; print('numba OK')"
python -c "
from simsopt.geo import SurfaceRZFourier
s = SurfaceRZFourier()
try:
    s.is_self_intersecting()
    print('ground OK: is_self_intersecting works')
except RuntimeError as e:
    print(f'ground MISSING: {e}')
"

echo ""
echo "=== SETUP COMPLETE ==="
