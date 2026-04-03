#!/bin/bash
# Progressive mpol ramp-up: 8 → 10 → 12 → 14 → 16 → 18
# Each step uses the previous step's optimized coils as the seed.
# Runs on EC2 in a screen session.
#
# Usage (on EC2, from ~/simsopt):
#   screen -dmS mpol_ramp bash ~/progressive_mpol.sh
#
# Or from local:
#   scp scripts/progressive_mpol.sh ubuntu@<IP>:~/
#   ssh ubuntu@<IP> 'screen -dmS mpol_ramp bash ~/progressive_mpol.sh'
#
# Results are saved to RESULTS_DIR with checkpoint markers after each step.
# Use `python scripts/aws_run.py download` from local to pull results.
# The idle watchdog (30 min) handles shutdown after the ramp completes.

set -euo pipefail

cd ~/simsopt
source .venv/bin/activate

NCPU=$(nproc)
THREADS=$((NCPU - 2))
export OMP_NUM_THREADS=$THREADS
export MKL_NUM_THREADS=$THREADS
export OPENBLAS_NUM_THREADS=$THREADS

# Configurable via env vars for reuse across different ramps
LOG=${RAMP_LOG:-~/mpol_ramp.log}
RESULTS_DIR=${RAMP_RESULTS_DIR:-~/mpol_ramp_results}
CHECKPOINT_DIR=${RESULTS_DIR}/checkpoints
SOLVER=examples/single_stage_optimization/SINGLE_STAGE/single_stage_banana_example.py
NTOR=${RAMP_NTOR:-12}
CC_WEIGHT=${RAMP_CC_WEIGHT:-50}
CURVATURE_WEIGHT=${RAMP_CURVATURE_WEIGHT:-0.1}
CURVATURE_THRESHOLD=${RAMP_CURVATURE_THRESHOLD:-40}
RES_WEIGHT=${RAMP_RES_WEIGHT:-1000}
IOTAS_WEIGHT=${RAMP_IOTAS_WEIGHT:-200}
BOOZER_STAGE=${RAMP_BOOZER_STAGE:-initial}
FTOL=${RAMP_FTOL:-}
GTOL=${RAMP_GTOL:-}

# Best seed from our search (order=4 iota20 Stage 2 seed, used by best single-stage run)
INITIAL_SEED=${RAMP_INITIAL_SEED:-~/seed_mpol18/biot_savart_opt.json}

# Common params (from best single-stage config)
COMMON_ARGS=(
    --plasma-surf-filename ${RAMP_PLASMA_SURF:-wout_nfp5ginsburg_000_014417_iota15.nc}
    --equilibria-dir ~/equilibria
    --iota-target ${RAMP_IOTA_TARGET:-0.15}
    --vol-target ${RAMP_VOL_TARGET:-0.10}
    --ntor ${NTOR}
    --nphi 255
    --ntheta 64
    --cc-weight ${CC_WEIGHT}
    --curvature-weight ${CURVATURE_WEIGHT}
    --curvature-threshold ${CURVATURE_THRESHOLD}
    --cc-dist 0.05
    --constraint-weight 1.0
    --res-weight ${RES_WEIGHT}
    --iotas-weight ${IOTAS_WEIGHT}
    --cs-weight 1.0
    --cs-dist 0.02
    --surf-dist-weight 1000
    --ss-dist 0.04
    --maxcor 300
    --boozer-stage ${BOOZER_STAGE}
)

if [ -n "$FTOL" ]; then
    COMMON_ARGS+=(--ftol "$FTOL")
fi

if [ -n "$GTOL" ]; then
    COMMON_ARGS+=(--gtol "$GTOL")
fi

mkdir -p $CHECKPOINT_DIR

echo "=== Progressive mpol ramp started at $(date) ===" | tee $LOG
echo "Threads: $THREADS" | tee -a $LOG
echo "Initial seed: $INITIAL_SEED" | tee -a $LOG
echo "" | tee -a $LOG

CURRENT_SEED=$INITIAL_SEED

for MPOL in 8 10 12 14 16 18; do
    STEP_DIR=$RESULTS_DIR/mpol=$MPOL-ntor=${NTOR}
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

        # Write checkpoint after each completed step
        python3 -c "
import json, time
r = json.load(open('$RESULTS_JSON'))
cp = {
    'mpol': $MPOL, 'ntor': ${NTOR}, 'maxiter': $MAXITER,
    'exit_code': $EXIT_CODE,
    'field_error': r.get('FIELD_ERROR'),
    'self_intersecting': r.get('SELF_INTERSECTING'),
    'final_iota': r.get('FINAL_IOTA'),
    'final_volume': r.get('FINAL_VOLUME'),
    'objective_J': r.get('OBJECTIVE_J'),
    'completed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'results_dir': '$STEP_DIR',
}
json.dump(cp, open('$CHECKPOINT_DIR/mpol_$MPOL.json', 'w'), indent=2)
print('  Checkpoint written: $CHECKPOINT_DIR/mpol_$MPOL.json')
" | tee -a $LOG

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
        # Write failure checkpoint
        python3 -c "
import json, time
cp = {
    'mpol': $MPOL, 'ntor': ${NTOR}, 'maxiter': $MAXITER,
    'exit_code': $EXIT_CODE, 'status': 'failed',
    'completed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
}
json.dump(cp, open('$CHECKPOINT_DIR/mpol_$MPOL.json', 'w'), indent=2)
"
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
    RJ=$(find $RESULTS_DIR/mpol=$MPOL-ntor=${NTOR} -name results.json -type f 2>/dev/null | head -1)
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

# Write completion marker (used by aws_run.py download to detect finished ramps)
touch ${RESULTS_DIR}/RAMP_COMPLETE
echo "" | tee -a $LOG
echo "Ramp complete. Results in $RESULTS_DIR" | tee -a $LOG
echo "Idle watchdog will shut down instance in ~30 min if no other work is running." | tee -a $LOG
