#!/bin/bash
# 6 neural bot self-play training loop
# Play 100k hands → learn → repeat 10 times
#
# Usage: bash scripts/train-loop.sh

PY="/c/Users/Simon/AppData/Local/Programs/Python/Python312/python.exe"
HANDS=100000
CYCLES=10
SEATS=6

echo "=========================================="
echo "  6-Bot Neural Self-Play Training Loop"
echo "  $HANDS hands × $CYCLES cycles = $(($HANDS * $CYCLES)) total"
echo "=========================================="

for i in $(seq 1 $CYCLES); do
    echo ""
    echo "==================== CYCLE $i/$CYCLES ===================="

    # Step 1: Generate data — all 6 bots play using TAG strategy
    # (First cycle uses TAG, later cycles could use the trained neural bot)
    echo "[Cycle $i] Generating $HANDS hands of self-play data..."
    SEED=$((1000 + $i * 100))
    node scripts/generate-rl-data.js --hands $HANDS --seats $SEATS --strategy tag --seed $SEED 2>&1 | grep -E "Hands:|Decisions:|Speed:"

    # Step 2: Train policy net on the accumulated data
    echo "[Cycle $i] Training policy net..."
    $PY -u vision/train_policy.py --epochs 10 --batch-size 8192 --lr 0.0005 2>&1 | grep -E "Epoch|Training complete|Loaded|Action"

    # Step 3: Evaluate — neural bot vs TAG at 6-max
    echo "[Cycle $i] Evaluating neural bot vs TAG..."
    $PY -u vision/fast_selfplay.py --hands 2000 --seats 6 --nn-seats 1 --seed $((2000 + $i)) 2>&1 | grep -E "NeuralBot|Profit|Win rate|VPIP|Throughput"

    echo "[Cycle $i] Complete"
    echo ""
done

echo "=========================================="
echo "  Training loop complete: $CYCLES cycles"
echo "=========================================="
