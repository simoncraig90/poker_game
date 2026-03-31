"""
Inference server for the policy network.

Runs a Flask HTTP server on localhost:9200 that accepts game state JSON
and returns the action + amount for the neural net bot.

Usage:
  python vision/inference_server.py
  python vision/inference_server.py --port 9200
  python vision/inference_server.py --model vision/models/policy_net.pt

Endpoint:
  POST /predict
  Body: { features: {...}, legal_actions: [...], min_bet, min_raise, max_raise, call_amount }
  Response: { action: "CALL", amount: 0, action_probs: [...], sizing: 1.5 }

  GET /health
  Response: { status: "ok", model_loaded: true }
"""

import argparse
import json
import sys
from pathlib import Path

import torch
import numpy as np
from flask import Flask, request, jsonify

from policy_net import PolicyNet, ACTION_NAMES, NUM_ACTIONS, build_feature_tensors

# ── Paths ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / "models" / "policy_net.pt"

# ── Server ───────────────────────────────────────────────────────────────

app = Flask(__name__)

# Global model reference
_model = None
_device = None


def load_model(model_path):
    global _model, _device
    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    if not Path(model_path).exists():
        print(f"WARNING: Model not found at {model_path}. Server will use random actions.")
        _model = None
        return

    checkpoint = torch.load(model_path, map_location=_device, weights_only=True)
    _model = PolicyNet().to(_device)
    _model.load_state_dict(checkpoint["model_state"])
    _model.eval()
    print(f"Loaded model from {model_path} (epoch {checkpoint.get('epoch', '?')})")
    print(f"  Device: {_device}")


# Action name mapping (index -> engine action string)
ACTION_INDEX_TO_NAME = {0: "FOLD", 1: "CHECK", 2: "CALL", 3: "BET", 4: "RAISE"}
ACTION_NAME_TO_INDEX = {v: k for k, v in ACTION_INDEX_TO_NAME.items()}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "model_loaded": _model is not None})


@app.route("/predict", methods=["POST"])
def predict():
    data = request.get_json()
    features = data["features"]
    legal_actions = data["legal_actions"]  # list of action name strings
    min_bet = data.get("min_bet", 0)
    min_raise = data.get("min_raise", 0)
    max_raise = data.get("max_raise", 0)
    call_amount = data.get("call_amount", 0)

    # Convert legal action names to indices
    legal_indices = []
    for a in legal_actions:
        if isinstance(a, str):
            idx = ACTION_NAME_TO_INDEX.get(a, -1)
        else:
            idx = a
        if 0 <= idx < NUM_ACTIONS:
            legal_indices.append(idx)

    if not legal_indices:
        return jsonify({"action": "FOLD", "amount": 0})

    if _model is None:
        # Random fallback
        idx = legal_indices[np.random.randint(len(legal_indices))]
        action_name = ACTION_INDEX_TO_NAME[idx]
        amount = 0
        if action_name == "BET":
            amount = min_bet
        elif action_name == "RAISE":
            amount = min_raise
        return jsonify({"action": action_name, "amount": amount})

    # Build tensors
    cards, extra = build_feature_tensors(features, device=_device)

    # Legal mask
    legal_mask = torch.zeros(1, NUM_ACTIONS, dtype=torch.bool, device=_device)
    for idx in legal_indices:
        legal_mask[0, idx] = True

    # Inference
    with torch.no_grad():
        action_probs, sizing = _model.get_action_probs(cards, extra, legal_mask)

    probs = action_probs[0].cpu().numpy()
    size_frac = sizing[0, 0].item()  # fraction of pot (0-3)

    # Sample action from distribution (with temperature for exploration during data gen)
    # For evaluation, use argmax; for data gen, sample
    chosen_idx = np.random.choice(NUM_ACTIONS, p=probs)
    action_name = ACTION_INDEX_TO_NAME[chosen_idx]

    # Compute amount for bet/raise
    amount = 0
    pot_size = features.get("potNorm", 0) * 1000  # denormalize (100bb = 1000 chips)
    if action_name == "BET":
        # Size as fraction of pot
        raw_amount = int(pot_size * size_frac)
        amount = max(min_bet, min(raw_amount, features.get("stackNorm", 10) * 1000))
        if amount < min_bet:
            amount = min_bet
    elif action_name == "RAISE":
        raw_amount = int(pot_size * size_frac) + call_amount
        # Raise amount is total raise-to
        amount = max(min_raise, min(raw_amount, max_raise))
        if amount < min_raise:
            amount = min_raise
        if amount > max_raise and max_raise > 0:
            amount = max_raise

    return jsonify({
        "action": action_name,
        "amount": amount,
        "action_probs": probs.tolist(),
        "sizing": size_frac,
    })


@app.route("/predict_greedy", methods=["POST"])
def predict_greedy():
    """Same as /predict but uses argmax instead of sampling."""
    data = request.get_json()
    features = data["features"]
    legal_actions = data["legal_actions"]
    min_bet = data.get("min_bet", 0)
    min_raise = data.get("min_raise", 0)
    max_raise = data.get("max_raise", 0)
    call_amount = data.get("call_amount", 0)

    legal_indices = []
    for a in legal_actions:
        if isinstance(a, str):
            idx = ACTION_NAME_TO_INDEX.get(a, -1)
        else:
            idx = a
        if 0 <= idx < NUM_ACTIONS:
            legal_indices.append(idx)

    if not legal_indices:
        return jsonify({"action": "FOLD", "amount": 0})

    if _model is None:
        idx = legal_indices[0]
        action_name = ACTION_INDEX_TO_NAME[idx]
        return jsonify({"action": action_name, "amount": 0})

    cards, extra = build_feature_tensors(features, device=_device)
    legal_mask = torch.zeros(1, NUM_ACTIONS, dtype=torch.bool, device=_device)
    for idx in legal_indices:
        legal_mask[0, idx] = True

    with torch.no_grad():
        action_probs, sizing = _model.get_action_probs(cards, extra, legal_mask)

    probs = action_probs[0].cpu().numpy()
    size_frac = sizing[0, 0].item()

    chosen_idx = int(np.argmax(probs))
    action_name = ACTION_INDEX_TO_NAME[chosen_idx]

    amount = 0
    pot_size = features.get("potNorm", 0) * 1000
    if action_name == "BET":
        raw_amount = int(pot_size * size_frac)
        amount = max(min_bet, min(raw_amount, features.get("stackNorm", 10) * 1000))
    elif action_name == "RAISE":
        raw_amount = int(pot_size * size_frac) + call_amount
        amount = max(min_raise, min(raw_amount, max_raise))
        if max_raise > 0:
            amount = min(amount, max_raise)

    return jsonify({
        "action": action_name,
        "amount": amount,
        "action_probs": probs.tolist(),
        "sizing": size_frac,
    })


def main():
    parser = argparse.ArgumentParser(description="Policy net inference server")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--model", type=str, default=str(DEFAULT_MODEL))
    args = parser.parse_args()

    load_model(args.model)

    print(f"\nStarting inference server on http://localhost:{args.port}")
    print(f"  POST /predict - sample action from policy")
    print(f"  POST /predict_greedy - argmax action from policy")
    print(f"  GET  /health - server status\n")

    # Disable Flask banner noise
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.WARNING)

    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
