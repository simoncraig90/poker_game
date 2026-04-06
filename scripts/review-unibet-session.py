"""
Review tool for poker advisor session logs.

Loads session JSONL files produced by vision/session_logger.py and prints
a formatted report with stats, per-position breakdown, leak detection,
and recommendation accuracy.

Usage:
    python scripts/review-unibet-session.py                   # latest session
    python scripts/review-unibet-session.py path/to/file.jsonl # specific file
"""

import json
import os
import sys
import glob
from collections import defaultdict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BB_CENTS = 10  # $0.05/$0.10 game -> BB = 10 cents
POSITIONS = ["UTG", "MP", "CO", "BTN", "SB", "BB"]

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def find_latest_session():
    """Find the most recently modified session_*.jsonl file."""
    data_dir = os.path.join(os.path.dirname(__file__), "..", "vision", "data")
    data_dir = os.path.normpath(data_dir)
    pattern = os.path.join(data_dir, "session_*.jsonl")
    files = glob.glob(pattern)
    if not files:
        print(f"No session files found in {data_dir}")
        sys.exit(1)
    return max(files, key=os.path.getmtime)


def load_session(path):
    """Load JSONL file, return list of hand dicts.

    Handles two formats:
      1. session_logger.py format: one JSON object per hand with hand_id,
         hero, position, streets[], profit_cents, starting_stack, timestamp.
      2. Legacy review format: one JSON object per decision with hero, board,
         phase, recommended_action, action_probs, equity. These are grouped
         into synthetic hands.
    """
    hands = []
    with open(path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        print("Session file is empty.")
        sys.exit(1)

    # Detect format from first line
    first = json.loads(lines[0])
    is_structured = "hand_id" in first or "streets" in first

    if is_structured:
        for line in lines:
            hands.append(json.loads(line))
    else:
        # Legacy per-decision format: group by hero cards
        hands = _group_legacy_decisions(lines)

    return hands


def _group_legacy_decisions(lines):
    """Group legacy per-decision rows into synthetic hand objects."""
    hands = []
    current_hero = None
    current_streets = []
    hand_counter = 0

    for line in lines:
        row = json.loads(line)
        hero = tuple(sorted(row.get("hero", [])))
        phase = row.get("phase", "PREFLOP")
        board = row.get("board", [])

        # New hand when hero cards change
        if hero != current_hero:
            if current_hero and current_streets:
                hand_counter += 1
                hands.append(_build_legacy_hand(hand_counter, current_streets))
            current_hero = hero
            current_streets = []

        # Deduplicate: only keep if phase or board length changed
        if current_streets:
            prev = current_streets[-1]
            if prev["phase"] == phase and len(prev.get("board", [])) == len(board):
                # Update in place with latest recommendation
                current_streets[-1] = row
                continue

        current_streets.append(row)

    # Flush last hand
    if current_hero and current_streets:
        hand_counter += 1
        hands.append(_build_legacy_hand(hand_counter, current_streets))

    return hands


def _build_legacy_hand(hand_id, decisions):
    """Build a structured hand dict from legacy decision rows."""
    first = decisions[0]
    streets = []
    for d in decisions:
        rec_action = d.get("recommended_action", "")
        # Normalize: "RAISE pot-size" -> "RAISE"
        action_word = rec_action.split()[0].upper() if rec_action else ""
        probs = d.get("action_probs", {})
        streets.append({
            "phase": d.get("phase", "PREFLOP"),
            "board": d.get("board", []),
            "pot": d.get("pot", 0),
            "facing_bet": d.get("facing_bet", False),
            "call_amount": d.get("call_amount", 0),
            "stack": d.get("stack", 0),
            "rec_action": action_word,
            "rec_equity": d.get("equity", 0),
            "cfr_probs": probs,
        })
    return {
        "hand_id": str(hand_id),
        "time": first.get("time", ""),
        "timestamp": first.get("timestamp", 0),
        "hero": list(first.get("hero", [])),
        "position": first.get("position", "?"),
        "starting_stack": first.get("stack", 0),
        "profit_cents": 0,  # unknown in legacy format
        "streets": streets,
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(hands):
    """Run all analyses and return a results dict."""
    results = {}

    # --- Session stats ---
    total = len(hands)
    timestamps = [h.get("timestamp", 0) for h in hands if h.get("timestamp")]
    if len(timestamps) >= 2:
        duration_sec = max(timestamps) - min(timestamps)
    else:
        duration_sec = 0
    duration_min = duration_sec / 60

    profits = [h.get("profit_cents", 0) or 0 for h in hands]
    net_cents = sum(profits)
    net_bb = net_cents / BB_CENTS
    bb_per_hr = (net_bb / (duration_min / 60)) if duration_min > 0 else 0
    bb_per_100 = (net_bb / total * 100) if total > 0 else 0

    results["session"] = {
        "total_hands": total,
        "duration_min": duration_min,
        "net_cents": net_cents,
        "net_bb": net_bb,
        "bb_per_hr": bb_per_hr,
        "bb_per_100": bb_per_100,
    }

    # --- Per-position stats ---
    pos_hands = defaultdict(list)
    for h in hands:
        pos = h.get("position", "?").upper()
        pos_hands[pos].append(h)

    pos_stats = {}
    for pos in POSITIONS:
        ph = pos_hands.get(pos, [])
        if not ph:
            continue
        count = len(ph)
        played = sum(1 for h in ph if _hero_played(h))
        vpip = played / count if count else 0
        pos_profit = sum(h.get("profit_cents", 0) or 0 for h in ph)
        pos_bb = pos_profit / BB_CENTS
        wr = (pos_bb / count * 100) if count else 0

        # Average pot: use max pot seen across streets
        pots = []
        for h in ph:
            max_pot = max((s.get("pot", 0) for s in h.get("streets", [])), default=0)
            if max_pot > 0:
                pots.append(max_pot)
        avg_pot = (sum(pots) / len(pots)) if pots else 0

        pos_stats[pos] = {
            "hands": count,
            "vpip": vpip,
            "win_rate_bb100": wr,
            "avg_pot_cents": avg_pot,
        }

    results["positions"] = pos_stats

    # --- Leak detection ---
    calling_station = []  # advisor said FOLD, hero continued, lost
    passive = []          # advisor said RAISE, hero just called
    chasing = []          # equity <30% but hero put money in
    biggest_losses = []

    for h in hands:
        streets = h.get("streets", [])
        profit = h.get("profit_cents", 0) or 0

        for i, s in enumerate(streets):
            rec = (s.get("rec_action") or "").upper()
            equity = s.get("rec_equity", 0) or 0

            # Calling station: FOLD recommended, hand continued past this street, lost
            if rec == "FOLD" and i < len(streets) - 1 and profit < 0:
                calling_station.append(_hand_summary(h, s, "FOLD ignored"))

            # Passive: RAISE recommended but no raise observed
            # We approximate: if RAISE was recommended and next street exists
            # without pot increasing significantly, hero likely just called
            if rec == "RAISE" and i < len(streets) - 1:
                passive.append(_hand_summary(h, s, "RAISE->flat"))

            # Chasing: equity <30% but hero continued
            if equity < 0.30 and equity > 0 and i < len(streets) - 1:
                chasing.append(_hand_summary(h, s, f"equity={equity:.0%}"))

        if profit < 0:
            biggest_losses.append((profit, h))

    biggest_losses.sort(key=lambda x: x[0])

    results["leaks"] = {
        "calling_station": calling_station,
        "passive": passive,
        "chasing": chasing,
        "biggest_losses": biggest_losses[:10],
    }

    # --- Recommendation accuracy ---
    fold_correct = 0
    fold_total = 0
    action_correct = 0
    action_total = 0

    for h in hands:
        streets = h.get("streets", [])
        profit = h.get("profit_cents", 0) or 0
        if not streets:
            continue

        # Use first street recommendation as primary advice
        first_rec = (streets[0].get("rec_action") or "").upper()

        if first_rec == "FOLD":
            fold_total += 1
            # If hero would have lost, fold was correct
            if profit <= 0:
                fold_correct += 1
        elif first_rec in ("CALL", "RAISE"):
            action_total += 1
            if profit > 0:
                action_correct += 1

    results["accuracy"] = {
        "fold_correct": fold_correct,
        "fold_total": fold_total,
        "action_correct": action_correct,
        "action_total": action_total,
    }

    return results


def _hero_played(hand):
    """Did hero voluntarily put money in (beyond blinds)?"""
    streets = hand.get("streets", [])
    # If hand has more than just preflop, hero played
    if len(streets) > 1:
        return True
    # If only preflop and rec was not FOLD, assume hero played
    if streets:
        rec = (streets[0].get("rec_action") or "").upper()
        if rec in ("CALL", "RAISE"):
            return True
    return False


def _hand_summary(hand, street, tag):
    """Create a short summary dict for leak reporting."""
    return {
        "hand_id": hand.get("hand_id", "?"),
        "hero": hand.get("hero", []),
        "position": hand.get("position", "?"),
        "phase": street.get("phase", "?"),
        "board": street.get("board", []),
        "pot": street.get("pot", 0),
        "rec_action": street.get("rec_action", "?"),
        "equity": street.get("rec_equity", 0),
        "profit": hand.get("profit_cents", 0),
        "tag": tag,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def card_str(cards):
    """Format card list as string."""
    return " ".join(cards) if cards else "-"


def print_report(results, path):
    """Print formatted report to terminal."""
    s = results["session"]
    sep = "=" * 64

    print()
    print(sep)
    print(f"  SESSION REVIEW: {os.path.basename(path)}")
    print(sep)
    print()

    # Session stats
    print("  SESSION STATS")
    print("  " + "-" * 40)
    print(f"  Hands:        {s['total_hands']}")
    print(f"  Duration:     {s['duration_min']:.1f} min")
    net_sign = "+" if s["net_cents"] >= 0 else ""
    print(f"  Net P/L:      {net_sign}${s['net_cents']/100:.2f} ({net_sign}{s['net_bb']:.1f} BB)")
    print(f"  BB/hr:        {s['bb_per_hr']:+.1f}")
    print(f"  bb/100:       {s['bb_per_100']:+.1f}")
    print()

    # Position stats
    pos = results["positions"]
    if pos:
        print("  PER-POSITION STATS")
        print("  " + "-" * 56)
        print(f"  {'Pos':<6} {'Hands':>6} {'VPIP':>7} {'bb/100':>8} {'Avg Pot':>10}")
        print("  " + "-" * 56)
        for p in POSITIONS:
            if p in pos:
                ps = pos[p]
                print(f"  {p:<6} {ps['hands']:>6} {ps['vpip']:>6.0%} {ps['win_rate_bb100']:>+7.1f} {ps['avg_pot_cents']/100:>9.2f}")
        print()

    # Leak detection
    leaks = results["leaks"]
    print("  LEAK DETECTION")
    print("  " + "-" * 40)

    cs = leaks["calling_station"]
    print(f"  Calling station (FOLD ignored, lost):  {len(cs)}")
    for item in cs[:5]:
        print(f"    Hand {item['hand_id']}: {card_str(item['hero'])} @ {item['position']}"
              f" | {item['phase']} | board {card_str(item['board'])}"
              f" | equity {item['equity']:.0%} | P/L {item['profit']}c")

    pv = leaks["passive"]
    print(f"  Passive (RAISE->flat):                 {len(pv)}")
    for item in pv[:5]:
        print(f"    Hand {item['hand_id']}: {card_str(item['hero'])} @ {item['position']}"
              f" | {item['phase']} | equity {item['equity']:.0%}")

    ch = leaks["chasing"]
    print(f"  Chasing (equity <30%, continued):      {len(ch)}")
    for item in ch[:5]:
        print(f"    Hand {item['hand_id']}: {card_str(item['hero'])} @ {item['position']}"
              f" | {item['phase']} | {item['tag']}")

    print()

    # Biggest losses
    bl = leaks["biggest_losses"]
    if bl:
        print("  BIGGEST LOSSES")
        print("  " + "-" * 56)
        for profit, h in bl[:5]:
            streets = h.get("streets", [])
            last = streets[-1] if streets else {}
            rec_actions = " -> ".join(
                (s.get("rec_action") or "?") for s in streets
            )
            print(f"  Hand {h.get('hand_id','?')}: {card_str(h.get('hero',[]))} @ {h.get('position','?')}")
            print(f"    Board: {card_str(last.get('board', []))}")
            print(f"    Advisor: {rec_actions}")
            print(f"    Loss: {profit}c ({profit/BB_CENTS:.1f} BB)")
            print()

    # Recommendation accuracy
    acc = results["accuracy"]
    print("  RECOMMENDATION ACCURACY")
    print("  " + "-" * 40)

    if acc["fold_total"] > 0:
        fold_pct = acc["fold_correct"] / acc["fold_total"]
        print(f"  FOLD advice correct:  {acc['fold_correct']}/{acc['fold_total']} ({fold_pct:.0%})")
        print(f"    (hand would have lost -> fold was right)")
    else:
        print("  FOLD advice: no data")

    if acc["action_total"] > 0:
        act_pct = acc["action_correct"] / acc["action_total"]
        print(f"  CALL/RAISE correct:   {acc['action_correct']}/{acc['action_total']} ({act_pct:.0%})")
        print(f"    (hand was profitable -> action was right)")
    else:
        print("  CALL/RAISE advice: no data")

    print()
    print(sep)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if not os.path.exists(path):
            print(f"File not found: {path}")
            sys.exit(1)
    else:
        path = find_latest_session()

    print(f"Loading: {path}")
    hands = load_session(path)
    print(f"Loaded {len(hands)} hands")

    results = analyze(hands)
    print_report(results, path)


if __name__ == "__main__":
    main()
