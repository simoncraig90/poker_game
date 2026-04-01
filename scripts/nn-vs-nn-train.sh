#!/bin/bash
# 6 neural bots play each other, learn, repeat
# Runs for 2 hours or until killed
#
# Usage: bash scripts/nn-vs-nn-train.sh

PY="/c/Users/Simon/AppData/Local/Programs/Python/Python312/python.exe"
HANDS_PER_CYCLE=1000000
DURATION_SECONDS=7200  # 2 hours
START_TIME=$(date +%s)
CYCLE=0

echo "=========================================="
echo "  6-Bot Neural Self-Play Training Loop"
echo "  $HANDS_PER_CYCLE hands per cycle"
echo "  Running for $((DURATION_SECONDS / 3600)) hours"
echo "  Start: $(date)"
echo "=========================================="

while true; do
    ELAPSED=$(( $(date +%s) - START_TIME ))
    if [ $ELAPSED -ge $DURATION_SECONDS ]; then
        echo ""
        echo "Time limit reached ($((ELAPSED / 60)) minutes). Stopping."
        break
    fi

    CYCLE=$((CYCLE + 1))
    REMAINING=$(( (DURATION_SECONDS - ELAPSED) / 60 ))
    echo ""
    echo "==================== CYCLE $CYCLE ($REMAINING min remaining) ===================="

    # Step 1: Generate 1M hands of self-play data using TAG
    # All 6 seats play TAG, recording every decision with outcome
    echo "[Cycle $CYCLE] Generating $HANDS_PER_CYCLE hands..."
    SEED=$((5000 + CYCLE * 137))
    node scripts/generate-rl-data.js --hands $HANDS_PER_CYCLE --seats 6 --strategy tag --seed $SEED 2>&1 | grep -E "Decisions:|Speed:|Hands:"

    # Step 2: Train policy net (imitation learning)
    echo "[Cycle $CYCLE] Training policy net..."
    $PY -u vision/train_policy.py --epochs 8 --batch-size 8192 --lr 0.0008 2>&1 | grep -E "Epoch|complete|Loaded|Action"

    # Step 3: Quick eval — neural bot vs 5 TAG bots
    echo "[Cycle $CYCLE] Evaluating..."
    $PY -u vision/fast_selfplay.py --hands 3000 --seats 6 --nn-seats 1 --seed $((3000 + CYCLE)) 2>&1 | grep -E "NeuralBot|Profit|Win rate|VPIP|Throughput"

    # Step 4: Also eval — 6 neural bots vs each other (nn-vs-nn mode)
    echo "[Cycle $CYCLE] 6 neural bots playing each other..."
    $PY -u vision/fast_selfplay.py --hands 3000 --seats 6 --nn-seats 6 --seed $((4000 + CYCLE)) 2>&1 | grep -E "NN_|Profit|Win rate|VPIP|Throughput"

    # Log cycle result
    echo "[Cycle $CYCLE] Complete at $(date) ($((ELAPSED / 60))m elapsed)"

    # Check for memory issues — kill zombie python processes
    PYCOUNT=$(tasklist //FI "IMAGENAME eq python.exe" 2>/dev/null | grep -c python)
    if [ "$PYCOUNT" -gt 2 ]; then
        echo "[WARNING] $PYCOUNT Python processes detected. Cleaning up..."
        taskkill //F //IM python.exe 2>/dev/null
        sleep 2
    fi
done

echo ""
echo "=========================================="
echo "  Training complete: $CYCLE cycles in $((ELAPSED / 60)) minutes"
echo "  End: $(date)"
echo "=========================================="
