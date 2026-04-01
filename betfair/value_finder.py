"""
Value finder — compares model probabilities against Betfair market odds.

Same concept as poker equity vs pot odds:
  - Model says Team A wins 55% of the time
  - Market back odds are 2.10 (implied prob = 47.6%)
  - Edge = 55% - 47.6% = 7.4% → value bet

Uses Kelly criterion for bet sizing (same bankroll math as poker).
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ValueBet:
    event_name: str
    market_id: str
    selection_id: int
    selection_name: str
    side: str  # BACK or LAY
    market_odds: float
    model_prob: float
    implied_prob: float
    edge: float
    kelly_fraction: float
    suggested_stake: float


def implied_probability(decimal_odds):
    """Convert decimal odds to implied probability."""
    return 1.0 / decimal_odds


def decimal_to_implied(decimal_odds):
    """Convert decimal odds to implied probability (percentage)."""
    return 100.0 / decimal_odds


def kelly_criterion(prob, odds, fraction=0.25):
    """
    Kelly criterion for optimal bet sizing.

    Args:
        prob: Model's estimated probability of outcome
        odds: Decimal odds offered
        fraction: Fractional Kelly (default 1/4 Kelly for safety)

    Returns:
        Fraction of bankroll to bet (0 if no edge)
    """
    q = 1 - prob
    b = odds - 1  # net odds (profit per unit staked)
    if b <= 0:
        return 0.0
    kelly = (b * prob - q) / b
    return max(0.0, kelly * fraction)


def find_value(predictions, market_book, bankroll, min_edge=0.02, max_stake_pct=0.05):
    """
    Find value bets by comparing model predictions to market odds.

    Args:
        predictions: dict mapping selection_id -> model probability
        market_book: Betfair market book response
        bankroll: Current bankroll
        min_edge: Minimum edge to consider (default 2%)
        max_stake_pct: Maximum stake as fraction of bankroll (default 5%)

    Returns:
        List of ValueBet objects sorted by edge descending
    """
    value_bets = []

    for market in market_book:
        market_id = market["marketId"]

        for runner in market.get("runners", []):
            sel_id = runner["selectionId"]
            sel_name = runner.get("runnerName", str(sel_id))

            if sel_id not in predictions:
                continue

            model_prob = predictions[sel_id]

            # Check back odds (betting FOR the outcome)
            back_prices = runner.get("ex", {}).get("availableToBack", [])
            if back_prices:
                best_back = back_prices[0]["price"]
                imp_prob = implied_probability(best_back)
                edge = model_prob - imp_prob

                if edge >= min_edge:
                    kelly = kelly_criterion(model_prob, best_back)
                    stake = min(kelly * bankroll, max_stake_pct * bankroll)

                    value_bets.append(ValueBet(
                        event_name=market.get("eventName", ""),
                        market_id=market_id,
                        selection_id=sel_id,
                        selection_name=sel_name,
                        side="BACK",
                        market_odds=best_back,
                        model_prob=model_prob,
                        implied_prob=imp_prob,
                        edge=edge,
                        kelly_fraction=kelly,
                        suggested_stake=round(stake, 2),
                    ))

            # Check lay odds (betting AGAINST the outcome)
            lay_prices = runner.get("ex", {}).get("availableToLay", [])
            if lay_prices:
                best_lay = lay_prices[0]["price"]
                lay_imp_prob = implied_probability(best_lay)
                # For a lay, we profit when the outcome doesn't happen
                lay_edge = (1 - model_prob) - (1 - lay_imp_prob)

                if lay_edge >= min_edge:
                    kelly = kelly_criterion(1 - model_prob, best_lay)
                    stake = min(kelly * bankroll, max_stake_pct * bankroll)

                    value_bets.append(ValueBet(
                        event_name=market.get("eventName", ""),
                        market_id=market_id,
                        selection_id=sel_id,
                        selection_name=sel_name,
                        side="LAY",
                        market_odds=best_lay,
                        model_prob=model_prob,
                        implied_prob=lay_imp_prob,
                        edge=lay_edge,
                        kelly_fraction=kelly,
                        suggested_stake=round(stake, 2),
                    ))

    value_bets.sort(key=lambda x: x.edge, reverse=True)
    return value_bets


def format_value_bets(bets):
    """Pretty print value bets."""
    if not bets:
        print("No value bets found.")
        return

    print(f"\n{'='*80}")
    print(f"{'SELECTION':<25} {'SIDE':<5} {'ODDS':>6} {'MODEL':>7} {'IMPLIED':>7} {'EDGE':>6} {'STAKE':>8}")
    print(f"{'='*80}")

    for bet in bets:
        print(
            f"{bet.selection_name:<25} "
            f"{bet.side:<5} "
            f"{bet.market_odds:>6.2f} "
            f"{bet.model_prob:>6.1%} "
            f"{bet.implied_prob:>6.1%} "
            f"{bet.edge:>5.1%} "
            f"  {bet.suggested_stake:>6.2f}"
        )
