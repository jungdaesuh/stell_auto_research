#!/bin/bash
# Progressive mpol ramp-up: 8 → 10 → 12 → 14 → 16 → 18
# Each step uses the previous step's optimized coils as the seed.
# Runs on EC2 in a screen session with auto-shutdown.
#
# Usage (on EC2, from ~/simsopt):
#   screen -dmS mpol_ramp bash ~/progressive_mpol.sh
#
# Or from local:
#   scp scripts/progressive_mpol.sh ubuntu@<IP>:~/
#   ssh ubuntu@<IP> 'screen -dmS mpol_ramp bash ~/progressive_mpol.sh'

set -euo pipefail

cd ~/simsopt
source .venv/bin/activate

NCPU=$(nproc)
THREADS=$((NCPU - 2))
export OMP_NUM_THREADS=$THREADS
export MKL_NUM_THREADS=$THREADS
export OPENBLAS_NUM_THREADS=$THREADS

LOG=~/mpol_ramp.log
RESULTS_DIR=~/mpol_ramp_results
SOLVER=examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py

# Best seed from our search (order=4 iota20 Stage 2 seed, used by best single-stage run)
INITIAL_SEED=~/seed_mpol18/biot_savart_opt.json

# Common params (from best single-stage config)
COMMON_ARGS=(
    --plasma-surf-filename wout_nfp22ginsburg_000_014417_iota15.nc
    --equilibria-dir ~/equilibria
    --iota-target 0.15
    --vol-target 0.10
    --ntor 6
    --nphi 255
    --ntheta 64
    --cc-weight 50
    --curvature-weight 0.2
    --curvature-threshold 20
    --cc-dist 0.05
    --constraint-weight 1.0
    --res-weight 1000
    --iotas-weight 200
    --cs-weight 1.0
    --cs-dist 0.02
    --surf-dist-weight 1000
    --ss-dist 0.04
    --maxcor 300
    --boozer-stage initial
)

echo "=== Progressive mpol ramp started at $(date) ===" | tee $LOG
echo "Threads: $THREADS" | tee -a $LOG
echo "Initial seed: $INITIAL_SEED" | tee -a $LOG
echo "" | tee -a $LOG

CURRENT_SEED=$INITIAL_SEED

for MPOL in 8 10 12 14 16 18; do
    STEP_DIR=$RESULTS_DIR/mpol=$MPOL-ntor=6
    mkdir -p $STEP_DIR

    # Scale maxiter with mpol (higher mpol needs more iterations)
    if [ $MPOL -le 8 ]; then
        MAXITER=200
    elif [ $MPOL -le 12 ]; then
        MAXITER=150
    else
        MAXITER=100
    fi

    echo "=== mpol=$MPOL: maxiter=$MAXITER, seed=$(basename $(dirname $CURRENT_SEED)) ===" | tee -a $LOG
    echo "Started at $(date)" | tee -a $LOG

    # Run single-stage
    python $SOLVER \
        "${COMMON_ARGS[@]}" \
        --mpol $MPOL \
        --maxiter $MAXITER \
        --stage2-bs-path $CURRENT_SEED \
        --output-root $STEP_DIR \
        >> $LOG 2>&1
    EXIT_CODE=$?

    echo "Exit code: $EXIT_CODE" | tee -a $LOG

    # Check for results
    RESULTS_JSON=$(find $STEP_DIR -name results.json -type f | head -1)
    if [ -n "$RESULTS_JSON" ]; then
        FE=$(python3 -c "import json; print(json.load(open('$RESULTS_JSON')).get('FIELD_ERROR', '?'))")
        SI=$(python3 -c "import json; print(json.load(open('$RESULTS_JSON')).get('SELF_INTERSECTING', '?'))")
        echo "  FE=$FE SI=$SI" | tee -a $LOG

        # Use this step's optimized coils as seed for next step
        NEXT_SEED=$(find $STEP_DIR -name biot_savart_opt.json -type f | head -1)
        if [ -n "$NEXT_SEED" ]; then
            CURRENT_SEED=$NEXT_SEED
            echo "  Next seed: $CURRENT_SEED" | tee -a $LOG
        else
            echo "  WARNING: no biot_savart_opt.json found, keeping previous seed" | tee -a $LOG
        fi
    else
        echo "  FAILED: no results.json produced" | tee -a $LOG
        echo "  Stopping ramp-up — cannot continue without results" | tee -a $LOG
        break
    fi

    echo "Completed at $(date)" | tee -a $LOG
    echo "" | tee -a $LOG
done

echo "=== Progressive mpol ramp finished at $(date) ===" | tee -a $LOG

# Summary
echo "" | tee -a $LOG
echo "=== SUMMARY ===" | tee -a $LOG
for MPOL in 8 10 12 14 16 18; do
    RJ=$(find $RESULTS_DIR/mpol=$MPOL-ntor=6 -name results.json -type f 2>/dev/null | head -1)
    if [ -n "$RJ" ]; then
        python3 -c "
import json
r = json.load(open('$RJ'))
print(f'  mpol=$MPOL: FE={r.get(\"FIELD_ERROR\",\"?\"):.6f} SI={r.get(\"SELF_INTERSECTING\",\"?\")} iota={r.get(\"FINAL_IOTA\",\"?\"):.4f} vol={r.get(\"FINAL_VOLUME\",\"?\"):.6f}')
" 2>/dev/null || echo "  mpol=$MPOL: parse error"
    else
        echo "  mpol=$MPOL: not completed"
    fi
done | tee -a $LOG

# Auto-shutdown
echo "" | tee -a $LOG
echo "Auto-shutting down instance..." | tee -a $LOG
sudo shutdown -h now
