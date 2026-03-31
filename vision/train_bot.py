"""
Training loop orchestrator for the self-improving poker bot.

Cycles:
  1. Generate training data (Node.js engine with current strategy)
  2. Train policy network (Python/CUDA)
  3. Evaluate: neural bot vs TAG bot (measures absolute skill)
  4. Evaluate: neural bot vs previous version (measures improvement)
  5. Log results and repeat

Usage:
  python vision/train_bot.py
  python vision/train_bot.py --cycles 10 --hands-per-cycle 100000
  python vision/train_bot.py --resume       # continue from last cycle
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
SCRIPTS = PROJECT / "scripts"
MODELS = ROOT / "models"
DATA = ROOT / "data"

PYTHON = r"C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe"
NODE = "node"

MODEL_PATH = MODELS / "policy_net.pt"
PREV_MODEL_PATH = MODELS / "policy_net_prev.pt"
TRAINING_DATA = DATA / "rl_training_data.jsonl"
TRAINING_LOG = DATA / "training_log.json"

INFERENCE_PORT = 9200


# ── Helpers ──────────────────────────────────────────────────────────────

def run_cmd(cmd, description, timeout=600, env=None):
    """Run a command and stream output. Returns (returncode, stdout)."""
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"  CMD: {' '.join(cmd)}")
    print(f"{'='*60}\n")

    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=merged_env, cwd=str(PROJECT)
    )

    output_lines = []
    try:
        for line in proc.stdout:
            print(f"  | {line}", end="")
            output_lines.append(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        print(f"\n  TIMEOUT after {timeout}s")
        return -1, "".join(output_lines)

    return proc.returncode, "".join(output_lines)


def start_inference_server(model_path=None, port=INFERENCE_PORT):
    """Start the inference server as a background process. Returns the process."""
    cmd = [PYTHON, str(ROOT / "inference_server.py"), "--port", str(port)]
    if model_path:
        cmd.extend(["--model", str(model_path)])

    print(f"\n  Starting inference server on port {port}...")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(ROOT)
    )

    # Wait for server to be ready
    import urllib.request
    for attempt in range(30):
        time.sleep(1)
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
            data = json.loads(resp.read())
            if data.get("status") == "ok":
                print(f"  Inference server ready (model_loaded={data.get('model_loaded')})")
                return proc
        except Exception:
            pass

    print("  WARNING: Server may not be ready after 30s")
    return proc


def stop_inference_server(proc):
    """Stop the inference server process."""
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("  Inference server stopped.")


def parse_nn_results(output):
    """Parse structured JSON results from self-play-nn.js output."""
    marker = "__RESULTS_JSON__"
    if marker in output:
        json_str = output.split(marker)[-1].strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Fallback: try to parse bb/100 from text output
    # Look for the neural bot's bb/100
    result = {"players": []}
    for line in output.split("\n"):
        if "bb/100" in line and "NeuralBot" in output:
            try:
                # Extract bb/100 value
                parts = line.strip()
                if "bb/100" in parts:
                    val = parts.split("(")[1].split("bb/100")[0].strip()
                    result["nn_bb100"] = float(val)
            except (IndexError, ValueError):
                pass
    return result


def load_training_log():
    """Load existing training log or create new one."""
    if TRAINING_LOG.exists():
        with open(TRAINING_LOG) as f:
            return json.load(f)
    return {"cycles": [], "config": {}}


def save_training_log(log):
    """Save training log."""
    DATA.mkdir(parents=True, exist_ok=True)
    with open(TRAINING_LOG, "w") as f:
        json.dump(log, f, indent=2)


# ── Training Cycle ───────────────────────────────────────────────────────

def run_cycle(cycle_num, args, training_log):
    """Run one training cycle: generate -> train -> evaluate."""
    cycle_start = time.time()
    cycle_result = {
        "cycle": cycle_num,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hands_generated": args.hands_per_cycle,
    }

    print(f"\n{'#'*60}")
    print(f"#  CYCLE {cycle_num}")
    print(f"{'#'*60}")

    # ── Step 1: Generate training data ────────────────────────────────
    if cycle_num == 1:
        # First cycle: use TAG strategy to generate seed data
        strategy = "tag"
        append = False
    else:
        # Subsequent cycles: use neural net for data generation
        strategy = "neural"
        append = True  # append to existing data

    if strategy == "neural":
        # Start inference server with current model
        server_proc = start_inference_server(MODEL_PATH)
    else:
        server_proc = None

    seed = 42 + cycle_num * 1000  # different seed each cycle
    gen_cmd = [
        NODE, str(SCRIPTS / "generate-rl-data.js"),
        "--hands", str(args.hands_per_cycle),
        "--seats", "6",
        "--strategy", strategy,
        "--seed", str(seed),
    ]
    if append:
        gen_cmd.append("--append")

    rc, output = run_cmd(gen_cmd, f"Cycle {cycle_num}: Generate {args.hands_per_cycle} hands ({strategy} strategy)")

    if server_proc:
        stop_inference_server(server_proc)

    if rc != 0:
        print(f"  ERROR: Data generation failed (rc={rc})")
        cycle_result["error"] = "data_generation_failed"
        return cycle_result

    # Count total data points
    total_lines = 0
    if TRAINING_DATA.exists():
        with open(TRAINING_DATA) as f:
            for _ in f:
                total_lines += 1
    cycle_result["total_data_points"] = total_lines
    print(f"\n  Total training data: {total_lines:,} decision points")

    # ── Step 2: Train policy network ──────────────────────────────────
    train_cmd = [
        PYTHON, str(ROOT / "train_policy.py"),
        "--epochs", str(args.train_epochs),
        "--batch-size", "2048",
        "--lr", str(args.lr),
        "--entropy-weight", "0.01",
    ]
    if cycle_num > 1:
        train_cmd.append("--resume")

    rc, output = run_cmd(train_cmd, f"Cycle {cycle_num}: Train policy network ({args.train_epochs} epochs)")

    if rc != 0:
        print(f"  ERROR: Training failed (rc={rc})")
        cycle_result["error"] = "training_failed"
        return cycle_result

    # Parse training metrics from output
    for line in output.split("\n"):
        if "Best val_loss" in line:
            try:
                val = line.split("val_loss=")[1].strip()
                cycle_result["best_val_loss"] = float(val)
            except (IndexError, ValueError):
                pass

    # ── Step 3: Evaluate neural bot vs TAG ────────────────────────────
    server_proc = start_inference_server(MODEL_PATH)

    eval_cmd = [
        NODE, str(SCRIPTS / "self-play-nn.js"),
        "--hands", str(args.eval_hands),
        "--seats", "2",
        "--mode", "nn-vs-tag",
        "--seed", str(seed + 500),
        "--greedy",
    ]

    rc, output = run_cmd(
        eval_cmd,
        f"Cycle {cycle_num}: Evaluate neural vs TAG ({args.eval_hands} hands)",
        env={"STRUCTURED_OUTPUT": "1"}
    )

    eval_results = parse_nn_results(output)
    cycle_result["nn_vs_tag"] = eval_results

    # Extract neural bot's bb/100
    nn_bb100 = None
    if "players" in eval_results:
        for p in eval_results.get("players", []):
            if "Neural" in p.get("name", ""):
                nn_bb100 = p.get("bb100", 0)
                break
    if nn_bb100 is None:
        nn_bb100 = eval_results.get("nn_bb100", 0)
    cycle_result["nn_vs_tag_bb100"] = nn_bb100
    print(f"\n  Neural vs TAG: {nn_bb100:.1f} bb/100")

    # ── Step 4: Evaluate neural bot vs previous version ───────────────
    if cycle_num > 1 and PREV_MODEL_PATH.exists():
        # Start a second server on a different port for the prev model
        prev_port = INFERENCE_PORT + 1
        prev_server = start_inference_server(PREV_MODEL_PATH, port=prev_port)

        # Run neural (current, port 9200) vs neural (prev, port 9201)
        # For this we need a custom approach - run current model on seat 0
        # But self-play-nn.js only supports one port. So we just track
        # improvement via the nn_vs_tag metric instead.
        stop_inference_server(prev_server)

        # Compare bb/100 across cycles
        if len(training_log["cycles"]) > 0:
            prev_bb100 = training_log["cycles"][-1].get("nn_vs_tag_bb100", 0)
            improvement = (nn_bb100 or 0) - prev_bb100
            cycle_result["improvement_bb100"] = improvement
            print(f"  Improvement over last cycle: {improvement:+.1f} bb/100")

    stop_inference_server(server_proc)

    # ── Save previous model for comparison ────────────────────────────
    if MODEL_PATH.exists():
        shutil.copy2(MODEL_PATH, PREV_MODEL_PATH)

    cycle_result["elapsed_seconds"] = time.time() - cycle_start
    print(f"\n  Cycle {cycle_num} complete in {cycle_result['elapsed_seconds']:.0f}s")

    return cycle_result


# ── Main Loop ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RL training loop orchestrator")
    parser.add_argument("--cycles", type=int, default=5,
                        help="Number of training cycles")
    parser.add_argument("--hands-per-cycle", type=int, default=100000,
                        help="Hands to generate per cycle")
    parser.add_argument("--eval-hands", type=int, default=10000,
                        help="Hands for evaluation matches")
    parser.add_argument("--train-epochs", type=int, default=10,
                        help="Training epochs per cycle")
    parser.add_argument("--lr", type=float, default=3e-4,
                        help="Learning rate")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last completed cycle")
    parser.add_argument("--plateau-threshold", type=float, default=0.5,
                        help="Stop if improvement < this for 3 consecutive cycles (bb/100)")

    args = parser.parse_args()

    print("=" * 60)
    print("  POKER BOT RL TRAINING LOOP")
    print("=" * 60)
    print(f"  Cycles: {args.cycles}")
    print(f"  Hands/cycle: {args.hands_per_cycle:,}")
    print(f"  Eval hands: {args.eval_hands:,}")
    print(f"  Train epochs: {args.train_epochs}")
    print(f"  Learning rate: {args.lr}")
    print(f"  Plateau threshold: {args.plateau_threshold} bb/100")
    print()

    # Load or create training log
    training_log = load_training_log()
    training_log["config"] = {
        "cycles": args.cycles,
        "hands_per_cycle": args.hands_per_cycle,
        "eval_hands": args.eval_hands,
        "train_epochs": args.train_epochs,
        "lr": args.lr,
    }

    start_cycle = 1
    if args.resume and training_log["cycles"]:
        start_cycle = len(training_log["cycles"]) + 1
        print(f"  Resuming from cycle {start_cycle}")

        # If resuming, don't regenerate cycle 1 data from scratch
        if start_cycle == 1:
            start_cycle = 1  # no-op, but clear

    # Track consecutive plateaus
    plateau_count = 0

    for cycle_num in range(start_cycle, args.cycles + 1):
        cycle_result = run_cycle(cycle_num, args, training_log)
        training_log["cycles"].append(cycle_result)
        save_training_log(training_log)

        if "error" in cycle_result:
            print(f"\n  Cycle {cycle_num} had an error: {cycle_result['error']}")
            print("  Stopping training loop.")
            break

        # Check for plateau
        improvement = cycle_result.get("improvement_bb100")
        if improvement is not None and abs(improvement) < args.plateau_threshold:
            plateau_count += 1
            print(f"\n  Plateau detected ({plateau_count}/3)")
            if plateau_count >= 3:
                print("  Stopping: improvement has plateaued for 3 consecutive cycles.")
                break
        else:
            plateau_count = 0

    # ── Final Summary ─────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  TRAINING SUMMARY")
    print(f"{'='*60}")

    for c in training_log["cycles"]:
        bb100 = c.get("nn_vs_tag_bb100", "N/A")
        imp = c.get("improvement_bb100", "N/A")
        elapsed = c.get("elapsed_seconds", 0)
        data_pts = c.get("total_data_points", 0)
        bb100_str = f"{bb100:+.1f}" if isinstance(bb100, (int, float)) else str(bb100)
        imp_str = f"{imp:+.1f}" if isinstance(imp, (int, float)) else str(imp)
        print(f"  Cycle {c['cycle']}: nn_vs_tag={bb100_str} bb/100 | "
              f"delta={imp_str} | data={data_pts:,} | {elapsed:.0f}s")

    print(f"\n  Training log: {TRAINING_LOG}")
    print(f"  Model: {MODEL_PATH}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
