#!/bin/bash
# Neural bot training loop — runs until 11:00 AM UK time
# Each cycle: generate self-play data → train policy net → evaluate
#
# Usage: bash scripts/train-loop.sh

PY="/c/Users/Simon/AppData/Local/Programs/Python/Python312/python.exe"
HANDS=100000
SEATS=6
STOP_HOUR=11
ITERATION=0

echo "=========================================="
echo "  Neural Bot Training Loop"
echo "  $HANDS hands per cycle, running until ${STOP_HOUR}:00"
echo "  Started at $(date '+%H:%M:%S')"
echo "=========================================="

while true; do
    HOUR=$(date +%H)
    if [ "$HOUR" -ge "$STOP_HOUR" ] 2>/dev/null; then
        echo ""
        echo "=== ${STOP_HOUR}:00 reached — stopping training loop ==="
        echo "Completed $ITERATION iterations"
        break
    fi

    ITERATION=$((ITERATION + 1))
    echo ""
    echo "==================== CYCLE $ITERATION ($(date '+%H:%M:%S')) ===================="

    # Step 1: Generate self-play data
    echo "[Cycle $ITERATION] Generating $HANDS hands of self-play data..."
    SEED=$((1000 + ITERATION * 100))
    node scripts/generate-rl-data.js --hands $HANDS --seats $SEATS --strategy tag --seed $SEED 2>&1 | grep -E "Hands:|Decisions:|Speed:"

    # Step 2: Train policy net
    echo "[Cycle $ITERATION] Training policy net..."
    $PY -u vision/train_policy.py --epochs 10 --batch-size 8192 --lr 0.0005 2>&1 | grep -E "Epoch|Training complete|Loaded|Action"

    # Step 3: Evaluate
    echo "[Cycle $ITERATION] Evaluating neural bot vs TAG..."
    $PY -u vision/fast_selfplay.py --hands 2000 --seats 6 --nn-seats 1 --seed $((2000 + ITERATION)) 2>&1 | grep -E "NeuralBot|Profit|Win rate|VPIP|Throughput"

    echo "[Cycle $ITERATION] Complete at $(date '+%H:%M:%S')"
done
