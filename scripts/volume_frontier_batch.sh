#!/bin/bash
# Volume frontier experiments: optimize at larger target volumes + multi-surface.
#
# Answers Prof. Paul's question: can we push the usable plasma volume further
# by optimizing at larger vol_target and/or with multi-surface optimization?
#
# Prerequisite: EC2 instance running with seeds uploaded.
#   python scripts/aws_run.py launch
#
# Usage:
#   scripts/volume_frontier_batch.sh [--equilibrium iota15|iota16]
#
# Runs 5 experiments sequentially (each ~30-60 min at mpol=12):
#   1. vol=0.12 single-surface  (midpoint of good regime)
#   2. vol=0.13 single-surface  (just below current breakdown)
#   3. vol=0.15 single-surface  (at the breakdown edge)
#   4. vol=0.12 dual-surface    (inner + outer Boozer surfaces)
#   5. vol=0.13 dual-surface    (inner + outer Boozer surfaces)
#
# After each run completes, a volume_sweep is run to measure the new breakdown point.

set -euo pipefail

EQUILIBRIUM="${1:-nfp5_iota15}"

# Map equilibrium shorthand — supports nfp{N}_iota{XX} format
# If the key matches the pattern, generate the filename directly.
# Otherwise fall back to legacy aliases.
if [[ "$1" =~ ^nfp([0-9]+)_iota([0-9]+)$ ]]; then
    _NFP="${BASH_REMATCH[1]}"
    _IOTA="${BASH_REMATCH[2]}"
    PLASMA_SURF="wout_nfp${_NFP}ginsburg_desc_iota${_IOTA}.nc"
else
    declare -A EQ_MAP=(
        ["iota15"]="wout_nfp5ginsburg_000_014417_iota15.nc"
        ["iota16"]="wout_nfp5ginsburg_desc_iota16.nc"
        ["iota20"]="wout_nfp5ginsburg_000_002084_iota20.nc"
    )
    PLASMA_SURF="${EQ_MAP[$EQUILIBRIUM]:-$EQUILIBRIUM}"
fi

STATE_FILE="/tmp/hbt_autoresearch/aws_instance.json"
if [ ! -f "$STATE_FILE" ]; then
    echo "No instance running. Launch first: python scripts/aws_run.py launch"
    exit 1
fi
IP=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['ip'])")
KEY="$HOME/.ssh/columbia-profile.pem"

echo "Instance: $IP"
echo "Equilibrium: $EQUILIBRIUM ($PLASMA_SURF)"

# Find seed on remote
SEED_PATH=$(ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@"$IP" "
    find ~/stage2_seeds -name biot_savart_opt.json -path '*${PLASMA_SURF}*' | head -1
" 2>/dev/null)

if [ -z "$SEED_PATH" ]; then
    echo "No seed found for $PLASMA_SURF. Upload seeds first."
    exit 1
fi
echo "Seed: $SEED_PATH"

NCPU=$(ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@"$IP" 'nproc')
THREADS=$((NCPU - 2))

# Common args
COMMON="--plasma-surf-filename $PLASMA_SURF \
  --equilibria-dir ~/equilibria \
  --stage2-bs-path $SEED_PATH \
  --mpol 12 --ntor 6 \
  --iota-target 0.15 \
  --curvature-threshold 40 \
  --maxiter 300"

# Define experiments
declare -a NAMES=(
    "vol012_single"
    "vol013_single"
    "vol015_single"
    "vol012_dual"
    "vol013_dual"
)

declare -a EXTRA_ARGS=(
    "--vol-target 0.12"
    "--vol-target 0.13"
    "--vol-target 0.15"
    "--vol-target 0.12 --num-surfaces 2 --inner-surface-ratio 0.8 --topology-scorer-every 10"
    "--vol-target 0.13 --num-surfaces 2 --inner-surface-ratio 0.8 --topology-scorer-every 10"
)

OUTPUT_ROOT="~/volume_frontier_${EQUILIBRIUM}"

echo ""
echo "=== Volume Frontier Batch ==="
echo "5 experiments, output: $OUTPUT_ROOT"
echo ""

# Launch all experiments in a single screen session, sequentially
BATCH_CMD=""
for i in "${!NAMES[@]}"; do
    NAME="${NAMES[$i]}"
    ARGS="${EXTRA_ARGS[$i]}"
    OUT="$OUTPUT_ROOT/$NAME"

    BATCH_CMD+="
echo '========================================' >> ~/volume_frontier.log
echo \"[$((i+1))/5] $NAME started at \$(date)\" >> ~/volume_frontier.log
echo '========================================' >> ~/volume_frontier.log

python examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py \
  $COMMON \
  $ARGS \
  --output-root $OUT \
  >> ~/volume_frontier.log 2>&1 || echo \"$NAME FAILED with exit \$?\" >> ~/volume_frontier.log

echo \"$NAME finished at \$(date)\" >> ~/volume_frontier.log

# Run volume sweep on the result
RESULT_DIR=\$(find $OUT -name results.json -exec dirname {} \\; | head -1)
if [ -n \"\$RESULT_DIR\" ]; then
    echo \"Running volume sweep on \$RESULT_DIR\" >> ~/volume_frontier.log
    python examples/single_stage_optimization/volume_sweep.py \
      --run-dir \"\$RESULT_DIR\" \
      --vol-min 0.04 --vol-max 0.25 --vol-steps 22 \
      >> ~/volume_frontier.log 2>&1 || echo \"volume_sweep FAILED\" >> ~/volume_frontier.log
fi

echo '' >> ~/volume_frontier.log
"
done

# Auto-shutdown after all experiments
BATCH_CMD+="
echo '=== All experiments finished at \$(date) ===' >> ~/volume_frontier.log
sudo shutdown -h now
"

echo "Launching batch in screen session 'volume_frontier'..."
ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@"$IP" "
    mkdir -p $OUTPUT_ROOT

    screen -dmS volume_frontier bash -c '
        cd ~/simsopt && source .venv/bin/activate
        export OMP_NUM_THREADS=$THREADS
        export MKL_NUM_THREADS=$THREADS
        export OPENBLAS_NUM_THREADS=$THREADS

        echo \"=== Volume Frontier Batch started at \$(date) ===\" > ~/volume_frontier.log
        echo \"Equilibrium: $PLASMA_SURF\" >> ~/volume_frontier.log
        echo \"Seed: $SEED_PATH\" >> ~/volume_frontier.log
        echo \"Threads: $THREADS\" >> ~/volume_frontier.log
        echo \"\" >> ~/volume_frontier.log

        $BATCH_CMD
    '

    sleep 2
    screen -ls
    echo '---'
    echo 'Batch launched with auto-shutdown.'
"

echo ""
echo "5 experiments + volume sweeps queued. Instance auto-stops when done."
echo "Monitor: ssh -i $KEY ubuntu@$IP 'tail -30 ~/volume_frontier.log'"
echo "Check:   ssh -i $KEY ubuntu@$IP 'ls ~/volume_frontier_${EQUILIBRIUM}/*/results.json 2>/dev/null'"
