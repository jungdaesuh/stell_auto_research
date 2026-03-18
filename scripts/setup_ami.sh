#!/bin/bash
# Run this ON the EC2 instance to set up SIMSOPT environment.
# After running, create an AMI from the instance for future launches.
#
# Usage: ssh -i ~/.ssh/columbia-profile.pem ubuntu@<IP> < scripts/setup_ami.sh

set -euo pipefail

echo "=== Installing system dependencies ==="
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential gfortran libopenblas-dev \
  liblapack-dev pkg-config cmake git python3-dev python3-pip python3-venv curl

echo "=== Installing uv ==="
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

echo "=== Cloning SIMSOPT (candidate-fixed branch) ==="
cd ~
git clone -b codex/hho-runner-parameterization git@github.com:jungdaesuh/simsopt.git
cd simsopt

echo "=== Creating venv and installing SIMSOPT ==="
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e ".[spec]" 2>&1 | tail -5 || {
  echo "Full install failed, trying without [spec]..."
  pip install -e . 2>&1 | tail -5
}
pip install numba

echo "=== Staging equilibria data ==="
mkdir -p ~/equilibria
# These will be uploaded separately via scp
echo "Equilibria dir created at ~/equilibria — upload .nc files here."

echo "=== Verifying installation ==="
python -c "import simsopt; print('simsopt OK:', simsopt.__file__)"
python -c "import numba; print('numba OK')"

echo ""
echo "=== SETUP COMPLETE ==="
echo "Next steps:"
echo "  1. Upload equilibria: scp -i ~/.ssh/columbia-profile.pem /path/to/*.nc ubuntu@<IP>:~/equilibria/"
echo "  2. Test a run: cd ~/simsopt && source .venv/bin/activate && python examples/single_stage_optimization/STAGE_2/banana_coil_solver.py --equilibria-dir ~/equilibria --maxiter 5"
echo "  3. If it works, create AMI from this instance"
