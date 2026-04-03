#!/bin/bash
# Run a long single-stage experiment on EC2 with auto-shutdown on completion.
# The instance stops itself when the run finishes (success or crash).
#
# Usage:
#   scripts/aws_run_long.sh --mpol 18 --iota-target 0.15 --equilibrium iota15 [extra solver args]
#
# Prerequisites:
#   - EC2 instance running (python scripts/aws_run.py launch)
#   - SIMSOPT installed (scripts/setup_ami.sh)
#   - Equilibria and seeds uploaded

set -euo pipefail

# Get instance IP from aws_run.py state
STATE_FILE="/tmp/hbt_autoresearch/aws_instance.json"
if [ ! -f "$STATE_FILE" ]; then
    echo "No instance running. Launch first: python scripts/aws_run.py launch"
    exit 1
fi
IP=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['ip'])")
KEY="$HOME/.ssh/columbia-profile.pem"

echo "Instance: $IP"
echo "Args: $@"

# Parse --equilibrium to determine plasma_surf_filename
EQUILIBRIUM="iota15"
for i in "$@"; do
    if [ "$prev" = "--equilibrium" ]; then
        EQUILIBRIUM="$i"
    fi
    prev="$i"
done

# Map equilibrium shorthand — supports nfp{N}_iota{XX} format
if [[ "$EQUILIBRIUM" =~ ^nfp([0-9]+)_iota([0-9]+)$ ]]; then
    _NFP="${BASH_REMATCH[1]}"
    _IOTA="${BASH_REMATCH[2]}"
    PLASMA_SURF="wout_nfp${_NFP}ginsburg_desc_iota${_IOTA}.nc"
else
    declare -A EQ_MAP=(
        ["iota15"]="wout_nfp5ginsburg_000_014417_iota15.nc"
        ["iota16"]="wout_nfp5ginsburg_desc_iota16.nc"
        ["iota17"]="wout_nfp5ginsburg_desc_iota17.nc"
        ["iota20"]="wout_nfp5ginsburg_000_002084_iota20.nc"
    )
    PLASMA_SURF="${EQ_MAP[$EQUILIBRIUM]:-$EQUILIBRIUM}"
fi

# Find best matching seed on the remote instance
echo "Finding seed on remote..."
SEED_PATH=$(ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@"$IP" "
    find ~/stage2_seeds -name biot_savart_opt.json -path '*${PLASMA_SURF}*' | head -1
" 2>/dev/null)

if [ -z "$SEED_PATH" ]; then
    echo "No seed found for $PLASMA_SURF. Upload seeds first."
    exit 1
fi
echo "Seed: $SEED_PATH"

# Get number of CPUs on the instance
NCPU=$(ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@"$IP" 'nproc')
THREADS=$((NCPU - 2))  # Leave 2 cores for OS
echo "Threads: $THREADS (of $NCPU)"

# Build the solver command — pass all args through
SOLVER_CMD="python examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py \
  --plasma-surf-filename $PLASMA_SURF \
  --equilibria-dir ~/equilibria \
  --stage2-bs-path $SEED_PATH \
  --output-root ~/long_run_results \
  $@"

# Launch in screen with auto-shutdown
echo "Launching in screen session 'long_run'..."
ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@"$IP" "
    mkdir -p ~/long_run_results

    screen -dmS long_run bash -c '
        cd ~/simsopt && source .venv/bin/activate
        export OMP_NUM_THREADS=$THREADS
        export MKL_NUM_THREADS=$THREADS
        export OPENBLAS_NUM_THREADS=$THREADS

        echo \"=== Run started at \$(date) ===\" > ~/long_run.log
        echo \"Command: $SOLVER_CMD\" >> ~/long_run.log
        echo \"Threads: $THREADS\" >> ~/long_run.log
        echo \"\" >> ~/long_run.log

        $SOLVER_CMD >> ~/long_run.log 2>&1
        EXIT_CODE=\$?

        echo \"\" >> ~/long_run.log
        echo \"=== Run finished at \$(date), exit code \$EXIT_CODE ===\" >> ~/long_run.log

        # Save results summary
        RESULTS=\$(find ~/long_run_results -name results.json | head -1)
        if [ -n \"\$RESULTS\" ]; then
            echo \"Results:\" >> ~/long_run.log
            cat \"\$RESULTS\" >> ~/long_run.log
        else
            echo \"No results.json found\" >> ~/long_run.log
        fi

        # AUTO-SHUTDOWN: stop the instance to prevent idle charges
        echo \"Auto-stopping instance...\" >> ~/long_run.log
        sudo shutdown -h now
    '

    sleep 2
    screen -ls
    echo '---'
    echo 'Run launched with auto-shutdown. Instance will stop when run completes.'
    echo 'Monitor: ssh -i $KEY ubuntu@$IP \"tail -20 ~/long_run.log\"'
    echo 'Results: ssh -i $KEY ubuntu@$IP \"cat \\\$(find ~/long_run_results -name results.json | head -1)\"'
"

echo ""
echo "Instance will auto-stop when run finishes. No idle charges."
echo "To check: ssh -i $KEY ubuntu@$IP 'tail -20 ~/long_run.log'"
