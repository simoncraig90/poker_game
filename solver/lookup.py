"""
Runtime solver lookup engine.

Loads precomputed solutions from disk and provides instant (<1ms) strategy
lookups during live play. This is the interface the advisor calls.

Usage:
    from lookup import SolverLookup

    engine = SolverLookup()          # loads all solutions into memory
    engine.load()

    decision = engine.query(
        preflop_scenario="BTN_open_BB_call",
        board=["Kh", "7d", "2c"],
        pot=200,
        stack=900,
        hero_hand="AhKd",
        hero_is_oop=True,
    )
    print(decision)
    # SolverDecision(
    #   action="Bet(66)",
    #   frequency=0.98,
    #   all_actions={"Check": 0.02, "Bet(66)": 0.98, "Bet(150)": 0.00, "AllIn(900)": 0.00},
    #   equity=0.73,
    #   ev=115.5,
    #   confidence="high",
    #   is_value=True,
    #   range_position=0.22,  # top 22% of range
    # )
"""
import json
import os
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from board_clusters import classify_flop, RANK_VAL

SOLUTIONS_DIR = Path(__file__).parent / "solutions"

SPR_BUCKETS = [
    ("spr_1", 0, 1.5),
    ("spr_2", 1.5, 3.0),
    ("spr_3", 3.0, 6.0),
    ("spr_4", 6.0, 10.0),
    ("spr_5", 10.0, 15.0),
    ("spr_6", 15.0, 999.0),
]


def _spr_bucket(pot: int, stack: int) -> str:
    if pot <= 0:
        return "spr_3"
    spr = stack / pot
    for name, lo, hi in SPR_BUCKETS:
        if lo <= spr < hi:
            return name
    return "spr_6"


@dataclass
class SolverDecision:
    """The advisor's output for a single hand at a single decision point."""
    action: str                          # recommended action: "Bet(66)", "Check", "Fold", etc
    size: Optional[int] = None           # bet/raise size in chips, None for check/fold/call
    frequency: float = 0.0              # how often GTO takes this action with this hand
    all_actions: dict = field(default_factory=dict)  # {action: frequency} for all actions
    equity: float = 0.0
    ev: float = 0.0
    confidence: str = "unknown"          # "high", "medium", "low", "unknown"
    is_value: bool = False               # True if this hand is in the top half of betting range
    range_position: float = 0.0          # 0.0 = bottom, 1.0 = top of range by EV
    scenario: str = ""
    cluster: str = ""
    spr_bucket: str = ""
    solver_exploitability: float = 0.0
    from_precomputed: bool = True


class SolverLookup:
    """Loads and queries precomputed solver solutions."""

    def __init__(self, solutions_dir: Optional[str] = None):
        self.solutions_dir = Path(solutions_dir) if solutions_dir else SOLUTIONS_DIR
        self._cache: dict[str, dict] = {}  # key -> solution data
        self._loaded_scenarios: set[str] = set()

    def load(self, scenarios: Optional[list[str]] = None):
        """Load solutions from disk. If scenarios is None, load all available."""
        if not self.solutions_dir.exists():
            print(f"WARNING: solutions dir not found: {self.solutions_dir}")
            return

        available = [d.name for d in self.solutions_dir.iterdir() if d.is_dir()]
        to_load = scenarios if scenarios else available

        for scenario in to_load:
            scenario_dir = self.solutions_dir / scenario
            if not scenario_dir.exists():
                continue
            count = 0
            for f in scenario_dir.glob("*.json.zlib"):
                try:
                    raw = zlib.decompress(f.read_bytes())
                    data = json.loads(raw)
                    # Key: scenario/cluster_spr
                    key = f"{scenario}/{f.stem}"  # stem removes .json.zlib -> keeps cluster_spr
                    # Actually stem only removes last suffix, so "x.json.zlib" -> "x.json"
                    key = f"{scenario}/{f.name.replace('.json.zlib', '')}"
                    self._cache[key] = data
                    count += 1
                except Exception as e:
                    print(f"WARNING: failed to load {f}: {e}")
            self._loaded_scenarios.add(scenario)
            print(f"Loaded {count} solutions for {scenario}")

        total_mb = sum(len(json.dumps(v)) for v in self._cache.values()) / 1024 / 1024
        print(f"Total: {len(self._cache)} solutions loaded (~{total_mb:.0f}MB uncompressed)")

    def _find_solution(self, scenario: str, cluster_key: tuple, spr_bucket: str) -> Optional[dict]:
        """Find a precomputed solution matching the query."""
        cluster_str = "_".join(str(x).replace("/", "-") for x in cluster_key)
        key = f"{scenario}/{cluster_str}_{spr_bucket}"
        return self._cache.get(key)

    def query(
        self,
        preflop_scenario: str,
        board: list[str],
        pot: int,
        stack: int,
        hero_hand: str,
        hero_is_oop: bool = True,
        action_history: list[str] = None,
    ) -> SolverDecision:
        """Query the solver for a specific hand in a specific spot.

        Args:
            preflop_scenario: e.g. "BTN_open_BB_call"
            board: e.g. ["Kh", "7d", "2c"] (3-5 cards)
            pot: current pot in chips
            stack: effective stack in chips
            hero_hand: e.g. "AhKd" (4 chars, two cards)
            hero_is_oop: True if hero is out of position (OOP)

        Returns:
            SolverDecision with the recommended action and metadata.
        """
        # Classify flop (first 3 cards)
        flop_cards = tuple(board[:3])
        cluster_key = classify_flop(flop_cards)
        spr_bucket = _spr_bucket(pot, stack)

        solution = self._find_solution(preflop_scenario, cluster_key, spr_bucket)
        if solution is None:
            return SolverDecision(
                action="CHECK" if hero_is_oop else "CALL",
                confidence="unknown",
                scenario=preflop_scenario,
                cluster=str(cluster_key),
                spr_bucket=spr_bucket,
                from_precomputed=False,
            )

        # Find the right node in the tree based on action history
        nodes = solution.get("nodes", [])
        # Legacy format: flat oop_hands at root
        legacy_hands = solution.get("oop_hands", solution.get("oop", {}).get("hands", []))

        if nodes:
            # Tree format: find node matching the action history
            actions, hands = self._find_node_in_tree(nodes, action_history, hero_is_oop)
        elif legacy_hands:
            # Legacy flat format (root OOP strategy only)
            if not hero_is_oop:
                return SolverDecision(
                    action="CALL", confidence="unknown",
                    scenario=preflop_scenario, cluster=str(cluster_key),
                    spr_bucket=spr_bucket, from_precomputed=False,
                )
            actions = solution.get("actions", [])
            hands = legacy_hands
        else:
            return SolverDecision(
                action="CHECK" if hero_is_oop else "CALL", confidence="unknown",
                scenario=preflop_scenario, cluster=str(cluster_key),
                spr_bucket=spr_bucket, from_precomputed=False,
            )

        # Find hero's hand in the solution
        hand_data = self._find_hand(hands, hero_hand)
        if hand_data is None:
            return SolverDecision(
                action="FOLD",
                confidence="low",
                scenario=preflop_scenario,
                cluster=str(cluster_key),
                spr_bucket=spr_bucket,
                from_precomputed=True,
            )

        # Build action frequency map (tree format uses "f", legacy uses "freq")
        freqs = hand_data.get("freq", hand_data.get("f", []))
        action_freq = dict(zip(actions, freqs))

        # Pick the highest-frequency action
        best_action = max(action_freq, key=action_freq.get)
        best_freq = action_freq[best_action]

        # Parse size from action string like "Bet(66)" or "Raise(300)"
        size = None
        if "(" in best_action:
            try:
                size = int(best_action.split("(")[1].rstrip(")"))
            except (ValueError, IndexError):
                pass

        # Compute range position (where does this hand rank by EV?)
        # Tree nodes don't have per-hand EV, only freqs. Use freq-weighted position.
        hero_ev = hand_data.get("ev", 0)  # may be 0 for tree nodes
        all_evs = sorted([h.get("ev", 0) for h in hands])
        if all_evs:
            rank = sum(1 for e in all_evs if e <= hero_ev)
            range_position = rank / len(all_evs)
        else:
            range_position = 0.5

        # Determine if this is a value bet or bluff
        # Value = top half of hands that take this action
        betting_hands = [(h.get("ev", 0), i) for i, h in enumerate(hands)
                         if h.get("freq", [0])[actions.index(best_action)] > 0.3]
        if betting_hands:
            betting_evs = sorted([ev for ev, _ in betting_hands])
            median_ev = betting_evs[len(betting_evs) // 2]
            is_value = hero_ev >= median_ev
        else:
            is_value = hero_ev > solution.get("oop_avg_ev", solution.get("oop", {}).get("avg_ev", 0))

        # Confidence based on frequency
        if best_freq >= 0.85:
            confidence = "high"
        elif best_freq >= 0.6:
            confidence = "medium"
        else:
            confidence = "low"

        return SolverDecision(
            action=best_action,
            size=size,
            frequency=best_freq,
            all_actions=action_freq,
            equity=hand_data.get("eq", 0),
            ev=hero_ev,
            confidence=confidence,
            is_value=is_value,
            range_position=range_position,
            scenario=preflop_scenario,
            cluster=str(cluster_key),
            spr_bucket=spr_bucket,
            solver_exploitability=solution.get("exploitability_pct", 0),
            from_precomputed=True,
        )

    def _find_node_in_tree(self, nodes: list[dict], action_history: list[str],
                           hero_is_oop: bool) -> tuple[list[str], list[dict]]:
        """Find the correct node in the strategy tree for the given action history.

        Args:
            nodes: list of node dicts from the precomputed solution, each with
                   "path" (comma-separated action history), "player" ("oop"/"ip"),
                   "actions" (available actions), "hands" (per-hand frequencies)
            action_history: list of actions taken so far, e.g. ["Check", "Bet(66)"]
            hero_is_oop: whether hero is OOP

        Returns:
            (actions, hands) where actions is the list of available action strings
            and hands is the list of {h: hand, f: [freqs]} dicts.
        """
        target_path = ",".join(action_history) if action_history else ""
        hero_player = "oop" if hero_is_oop else "ip"

        for node in nodes:
            if node.get("path", "") == target_path and node.get("player", "") == hero_player:
                actions = node.get("actions", [])
                hands = [{"hand": h["h"], "freq": h["f"]} for h in node.get("hands", [])]
                return actions, hands

        # Fallback: try matching just the path (regardless of player)
        for node in nodes:
            if node.get("path", "") == target_path:
                actions = node.get("actions", [])
                hands = [{"hand": h["h"], "freq": h["f"]} for h in node.get("hands", [])]
                return actions, hands

        return [], []

    def _find_hand(self, hands: list[dict], hero_hand: str) -> Optional[dict]:
        """Find a hand in the solver output. Handles card order and key variations."""
        for h in hands:
            name = h.get("hand") or h.get("h", "")
            if name == hero_hand:
                return h
        # Try reversed card order
        if len(hero_hand) == 4:
            rev = hero_hand[2:4] + hero_hand[0:2]
            for h in hands:
                name = h.get("hand") or h.get("h", "")
                if name == rev:
                    return h
        return None

    @property
    def loaded_count(self) -> int:
        return len(self._cache)

    @property
    def loaded_scenarios(self) -> list[str]:
        return sorted(self._loaded_scenarios)


# ============================================================
# Human-bot display: one-line output for the table switcher
# ============================================================

def format_for_display(decision: SolverDecision) -> str:
    """Format a solver decision as a single bold action line.
    This is what the human-bot sees on the overlay."""
    if "Fold" in decision.action:
        return "FOLD"
    elif "Check" in decision.action:
        return "CHECK"
    elif "Call" in decision.action:
        return "CALL"
    elif "AllIn" in decision.action:
        return f"ALL-IN"
    elif "Bet" in decision.action and decision.size:
        return f"BET {decision.size}"
    elif "Raise" in decision.action and decision.size:
        return f"RAISE {decision.size}"
    else:
        return decision.action.upper()


def format_confidence_color(decision: SolverDecision) -> str:
    """Return a color indicator for the overlay."""
    if decision.confidence == "high":
        return "GREEN"   # slam dunk, don't think
    elif decision.confidence == "medium":
        return "YELLOW"  # clear action but mixed spot
    else:
        return "RED"     # close decision, consider overriding


if __name__ == "__main__":
    print("Loading precomputed solutions...")
    engine = SolverLookup()
    engine.load()
    print()

    if engine.loaded_count == 0:
        print("No solutions loaded. Run precompute.py first.")
    else:
        # Test queries — root (BB acts first)
        print("--- ROOT (BB acts) ---")
        tests = [
            ("BTN_open_BB_call", ["3c", "2d", "2h"], 200, 900, "7s6s", True, []),
            ("BTN_open_BB_call", ["3c", "2d", "2h"], 200, 900, "Ac2c", True, []),
        ]
        for scenario, board, pot, stack, hand, oop, history in tests:
            d = engine.query(scenario, board, pot, stack, hand, oop, history)
            display = format_for_display(d)
            color = format_confidence_color(d)
            print(f"  {hand} on {' '.join(board)}: {display} [{color}] (freq={d.frequency:.0%})")

        # Test with action history: BB checked, BTN bet 33%
        print("\n--- After BB Check, BTN Bet(66) — BB responds ---")
        tests2 = [
            ("BTN_open_BB_call", ["3c", "2d", "2h"], 200, 900, "Ac2c", True, ["Check", "Bet(66)"]),
            ("BTN_open_BB_call", ["3c", "2d", "2h"], 200, 900, "7s6s", True, ["Check", "Bet(66)"]),
        ]
        for scenario, board, pot, stack, hand, oop, history in tests2:
            d = engine.query(scenario, board, pot, stack, hand, oop, history)
            display = format_for_display(d)
            color = format_confidence_color(d)
            print(f"  {hand}: {display} [{color}] (freq={d.frequency:.0%}, actions={d.all_actions})")
