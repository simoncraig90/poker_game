"""
Python bridge to the postflop-solver Rust binary.

Usage:
    from solver_bridge import solve_spot, SolverResult

    result = solve_spot(
        oop_range="QQ+,AKs,AKo",
        ip_range="TT+,AQs+,AKo",
        board="Kh 7d 2c",
        pot=200, stack=900,
    )
    print(result.actions)           # ['Check', 'Bet(66)', 'Bet(150)', 'AllIn(900)']
    print(result.exploitability_pct) # 0.51
    for h in result.oop_hands[:5]:
        print(h)                    # {'hand': 'AdAc', 'eq': 0.82, 'ev': 171.7, 'freq': [0.01, 0.99, 0.0, 0.0]}

    # Query a specific hand
    strat = result.strategy_for("AhKd")  # {'Check': 0.018, 'Bet(66)': 0.981, 'Bet(150)': 0.001, 'AllIn(900)': 0.0}
"""
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SOLVER_BIN = Path(__file__).parent / "postflop-solver" / "target" / "release" / "examples" / "solve_json.exe"


@dataclass
class SolverResult:
    raw: dict
    actions: list[str] = field(default_factory=list)
    exploitability: float = 0.0
    exploitability_pct: float = 0.0
    pot: int = 0
    stack: int = 0
    memory_mb: float = 0.0
    oop_avg_equity: float = 0.0
    oop_avg_ev: float = 0.0
    oop_hands: list[dict] = field(default_factory=list)
    ip_avg_equity: float = 0.0
    ip_avg_ev: float = 0.0

    def strategy_for(self, hand: str) -> Optional[dict]:
        """Get action frequencies for a specific hand (OOP).
        Returns dict mapping action name -> frequency."""
        for h in self.oop_hands:
            if h["hand"] == hand:
                return dict(zip(self.actions, h["freq"]))
        # Try reverse (e.g., "KdAh" -> "AhKd")
        if len(hand) == 4:
            rev = hand[2:4] + hand[0:2]
            for h in self.oop_hands:
                if h["hand"] == rev:
                    return dict(zip(self.actions, h["freq"]))
        return None

    def best_action_for(self, hand: str) -> Optional[tuple[str, float]]:
        """Return (action_name, frequency) of the highest-frequency action."""
        strat = self.strategy_for(hand)
        if not strat:
            return None
        best = max(strat.items(), key=lambda x: x[1])
        return best


def solve_spot(
    oop_range: str,
    ip_range: str,
    board: str,
    pot: int = 200,
    stack: int = 900,
    iterations: int = 300,
    target_exploitability_pct: float = 0.5,
    bet_sizes: str = "33%, 75%, a",
    raise_sizes: str = "2.5x",
    solver_bin: Optional[str] = None,
) -> SolverResult:
    """Solve a postflop spot and return structured results."""
    bin_path = solver_bin or str(SOLVER_BIN)
    if not os.path.exists(bin_path):
        raise FileNotFoundError(f"Solver binary not found: {bin_path}")

    payload = json.dumps({
        "oop_range": oop_range,
        "ip_range": ip_range,
        "board": board,
        "pot": pot,
        "stack": stack,
        "iterations": iterations,
        "target_exploitability_pct": target_exploitability_pct,
        "bet_sizes": bet_sizes,
        "raise_sizes": raise_sizes,
    })

    result = subprocess.run(
        [bin_path],
        input=payload,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Solver failed: {result.stderr}")

    data = json.loads(result.stdout)

    return SolverResult(
        raw=data,
        actions=data.get("actions", []),
        exploitability=data.get("exploitability", 0),
        exploitability_pct=data.get("exploitability_pct", 0),
        pot=data.get("pot", 0),
        stack=data.get("stack", 0),
        memory_mb=data.get("memory_mb", 0),
        oop_avg_equity=data.get("oop", {}).get("avg_equity", 0),
        oop_avg_ev=data.get("oop", {}).get("avg_ev", 0),
        oop_hands=data.get("oop", {}).get("hands", []),
        ip_avg_equity=data.get("ip", {}).get("avg_equity", 0),
        ip_avg_ev=data.get("ip", {}).get("avg_ev", 0),
    )


if __name__ == "__main__":
    print("Solving K72r flop spot...")
    r = solve_spot(
        oop_range="QQ+,AKs,AKo",
        ip_range="TT+,AQs+,AKo",
        board="Kh 7d 2c",
        pot=200, stack=900,
        iterations=300,
    )
    print(f"Exploitability: {r.exploitability_pct:.2f}% of pot")
    print(f"Actions: {r.actions}")
    print(f"OOP avg equity: {r.oop_avg_equity:.1%}, avg EV: {r.oop_avg_ev:.0f}")
    print(f"IP  avg equity: {r.ip_avg_equity:.1%}, avg EV: {r.ip_avg_ev:.0f}")
    print()

    # Show strategy for key hands
    for hand in ["AsAh", "KdKc", "AhKd", "QsQh"]:
        strat = r.strategy_for(hand)
        if strat:
            best, freq = r.best_action_for(hand)
            print(f"  {hand}: {best} ({freq:.0%})", end="")
            # Show full breakdown
            parts = [f"{a}:{f:.0%}" for a, f in strat.items() if f > 0.01]
            print(f"  [{', '.join(parts)}]")
        else:
            print(f"  {hand}: not in OOP range")

    print("\n--- Wider range test: J87tt flop ---")
    r2 = solve_spot(
        oop_range="22+,A2s+,K9s+,Q9s+,J9s+,T8s+,97s+,86s+,75s+,64s+,54s,A8o+,KTo+,QTo+,JTo",
        ip_range="22+,A2s+,K5s+,Q8s+,J8s+,T7s+,96s+,85s+,75s+,64s+,53s+,A8o+,KTo+,QJo",
        board="Jh 8d 7d",
        pot=120, stack=940,
        iterations=200,
    )
    print(f"Exploitability: {r2.exploitability_pct:.2f}%")
    print(f"Actions: {r2.actions}")
    print(f"OOP avg equity: {r2.oop_avg_equity:.1%}")
    for hand in ["9s6s", "TsTc", "AhKh", "8s8c"]:
        strat = r2.strategy_for(hand)
        if strat:
            best, freq = r2.best_action_for(hand)
            parts = [f"{a}:{f:.0%}" for a, f in strat.items() if f > 0.01]
            print(f"  {hand}: {best} ({freq:.0%})  [{', '.join(parts)}]")
