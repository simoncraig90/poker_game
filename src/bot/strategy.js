"use strict";

/**
 * Tight-Aggressive (TAG) poker bot strategy.
 *
 * Decides an action given game state + legal actions.
 * Designed to run both server-side (Node) and client-side (browser).
 */

const { preflopScore, evaluateHand, countDraws, HAND_CATEGORY } = require("./hand-strength");

// ── Position categories ───────────────────────────────────────────────────

function getPosition(seatIdx, buttonSeat, numPlayers, maxSeats) {
  // Distance from button clockwise (0 = button)
  const dist = (seatIdx - buttonSeat + maxSeats) % maxSeats;
  if (dist === 0) return "BTN";
  if (numPlayers <= 3) return dist === 1 ? "SB" : "BB";
  if (dist <= 1) return "SB";
  if (dist <= 2) return "BB";
  if (dist <= Math.floor(numPlayers / 3)) return "EP"; // early position
  if (dist <= Math.floor((numPlayers * 2) / 3)) return "MP"; // middle position
  return "LP"; // late position (cutoff, etc.)
}

// ── Opening thresholds by position ────────────────────────────────────────

const OPEN_THRESHOLDS = {
  EP: 0.72,   // tight: premium hands only
  MP: 0.60,   // medium pairs+, strong broadway
  LP: 0.45,   // wider: suited connectors, medium aces
  BTN: 0.35,  // widest: most playable hands
  SB: 0.50,   // tighter from SB (out of position post-flop)
  BB: 0.30,   // defend BB wide
};

// ── Main decision function ────────────────────────────────────────────────

/**
 * @param {object} params
 * @param {object} params.hand       - hand state (phase, board, pot, actionSeat, actions, etc.)
 * @param {object} params.seat       - seat state (stack, holeCards, bet, totalInvested, etc.)
 * @param {object} params.legalActions - { actions[], callAmount, minBet, minRaise, maxRaise }
 * @param {number} params.bb         - big blind amount
 * @param {number} params.button     - button seat index
 * @param {number} params.numPlayers - number of occupied seats
 * @param {number} params.maxSeats   - table max seats
 * @returns {{ action: string, amount?: number }}
 */
function decide(params) {
  const { hand, seat, legalActions, bb, button, numPlayers, maxSeats } = params;
  const { actions: legal, callAmount, minBet, minRaise, maxRaise } = legalActions;

  if (legal.length === 0) return { action: "FOLD" };
  if (legal.length === 1) return { action: legal[0] };

  const phase = hand.phase;
  const position = getPosition(seat.seat, button, numPlayers, maxSeats);
  const potSize = hand.pot;

  if (phase === "PREFLOP") {
    return decidePreflop({ seat, legal, callAmount, minBet, minRaise, maxRaise, bb, position, potSize, hand });
  }
  return decidePostflop({ seat, legal, callAmount, minBet, minRaise, maxRaise, bb, position, potSize, hand });
}

// ── Preflop Strategy ──────────────────────────────────────────────────────

function decidePreflop({ seat, legal, callAmount, minBet, minRaise, maxRaise, bb, position, potSize, hand }) {
  const cards = seat.holeCards;
  if (!cards || cards.length < 2) {
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }

  const score = preflopScore(cards[0], cards[1]);
  const threshold = OPEN_THRESHOLDS[position] || 0.50;

  // Count raises before us this street
  const preflopActions = (hand.actions || []).filter(
    (a) => a.street === "PREFLOP" && a.type !== "BLIND_SB" && a.type !== "BLIND_BB"
  );
  const raises = preflopActions.filter((a) => a.type === "RAISE" || a.type === "BET").length;
  const facingRaise = callAmount > bb;
  const facingThreebet = raises >= 2;

  // ── Premium hands (AA-JJ, AKs): always raise/re-raise
  if (score >= 0.85) {
    if (legal.includes("RAISE")) {
      const raiseSize = facingRaise
        ? Math.min(Math.round(callAmount * 3), maxRaise)
        : Math.min(bb * 3, maxRaise);
      return { action: "RAISE", amount: Math.max(raiseSize, minRaise) };
    }
    if (legal.includes("BET")) {
      return { action: "BET", amount: Math.min(bb * 3, seat.stack) };
    }
    if (legal.includes("CALL")) return { action: "CALL" };
    return { action: "CHECK" };
  }

  // ── Strong hands (TT-88, AQ-AJ, KQ): raise or call raises
  if (score >= 0.65) {
    if (facingThreebet) {
      // Call 3-bets with strong hands, fold medium ones
      if (score >= 0.75 && legal.includes("CALL")) return { action: "CALL" };
      return legal.includes("FOLD") ? { action: "FOLD" } : { action: "CALL" };
    }
    if (!facingRaise) {
      if (legal.includes("RAISE")) {
        const raiseSize = Math.min(bb * 3, maxRaise);
        return { action: "RAISE", amount: Math.max(raiseSize, minRaise) };
      }
      if (legal.includes("BET")) {
        return { action: "BET", amount: Math.min(bb * 3, seat.stack) };
      }
    }
    if (legal.includes("CALL")) return { action: "CALL" };
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }

  // ── Playable hands above position threshold: open or call small raises
  if (score >= threshold) {
    if (!facingRaise) {
      if (legal.includes("RAISE")) {
        const raiseSize = Math.min(bb * 3, maxRaise);
        return { action: "RAISE", amount: Math.max(raiseSize, minRaise) };
      }
      if (legal.includes("BET")) {
        return { action: "BET", amount: Math.min(bb * 3, seat.stack) };
      }
      if (legal.includes("CHECK")) return { action: "CHECK" };
    }
    // Call a single raise if getting good odds
    if (facingRaise && !facingThreebet && callAmount <= bb * 4) {
      if (legal.includes("CALL")) return { action: "CALL" };
    }
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }

  // ── Junk hands: check or fold
  if (legal.includes("CHECK")) return { action: "CHECK" };
  return { action: "FOLD" };
}

// ── Post-flop Strategy ────────────────────────────────────────────────────

function decidePostflop({ seat, legal, callAmount, minBet, minRaise, maxRaise, bb, position, potSize, hand }) {
  const cards = seat.holeCards;
  const board = hand.board || [];

  if (!cards || cards.length < 2) {
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }

  const eval_ = evaluateHand(cards, board);
  const draws = countDraws(cards, board);
  const strength = eval_.strength;
  const category = eval_.category;

  // Pot odds for calling decisions
  const potOdds = callAmount > 0 ? callAmount / (potSize + callAmount) : 0;

  // ── Monster hands (full house+): bet/raise for value
  if (category >= HAND_CATEGORY.FULL_HOUSE) {
    return valueAction({ legal, potSize, minBet, minRaise, maxRaise, bb, sizeFraction: 0.75 });
  }

  // ── Strong made hands (flush, straight, trips, top two pair)
  if (category >= HAND_CATEGORY.STRAIGHT || (category === HAND_CATEGORY.THREE_OF_A_KIND && strength > 0.68)) {
    return valueAction({ legal, potSize, minBet, minRaise, maxRaise, bb, sizeFraction: 0.65 });
  }

  // ── Good hands (two pair, overpair, top pair good kicker)
  if (category >= HAND_CATEGORY.TWO_PAIR || strength >= 0.45) {
    if (callAmount > 0) {
      // Facing a bet — call if pot odds are reasonable
      if (potOdds < 0.40 && legal.includes("CALL")) return { action: "CALL" };
      // Raise strong two pair+
      if (category >= HAND_CATEGORY.TWO_PAIR && strength >= 0.55 && legal.includes("RAISE")) {
        const raiseSize = Math.min(Math.round(potSize * 0.7) + seat.bet, maxRaise);
        return { action: "RAISE", amount: Math.max(raiseSize, minRaise) };
      }
      if (legal.includes("CALL")) return { action: "CALL" };
    }
    // No bet facing — bet for value
    return valueAction({ legal, potSize, minBet, minRaise, maxRaise, bb, sizeFraction: 0.55 });
  }

  // ── Medium hands (middle pair, weak top pair)
  if (category >= HAND_CATEGORY.PAIR && strength >= 0.30) {
    if (callAmount > 0) {
      // Call small bets
      if (potOdds < 0.30 && legal.includes("CALL")) return { action: "CALL" };
      return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
    }
    // Bet small for thin value / protection on early streets
    if (hand.phase === "FLOP" || hand.phase === "TURN") {
      return valueAction({ legal, potSize, minBet, minRaise, maxRaise, bb, sizeFraction: 0.40 });
    }
    if (legal.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  // ── Drawing hands (flush draw, straight draw)
  if (draws.flushDraw || draws.straightDraw) {
    const drawStrength = draws.flushDraw ? 0.35 : 0.30;
    if (callAmount > 0) {
      // Call with draws if odds are right (roughly 2:1 or better)
      if (potOdds < drawStrength && legal.includes("CALL")) return { action: "CALL" };
      return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
    }
    // Semi-bluff with draws
    if (hand.phase !== "RIVER") {
      return valueAction({ legal, potSize, minBet, minRaise, maxRaise, bb, sizeFraction: 0.50 });
    }
    if (legal.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  // ── Weak hands: check/fold
  if (callAmount > 0) {
    // Occasionally bluff-call tiny bets on the river
    if (hand.phase === "RIVER" && potOdds < 0.15 && legal.includes("CALL")) {
      return { action: "CALL" };
    }
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }

  if (legal.includes("CHECK")) return { action: "CHECK" };
  return { action: "FOLD" };
}

// ── Bet/Raise sizing helper ───────────────────────────────────────────────

function valueAction({ legal, potSize, minBet, minRaise, maxRaise, bb, sizeFraction }) {
  const betAmount = Math.max(Math.round(potSize * sizeFraction), bb);

  if (legal.includes("BET")) {
    const size = Math.max(Math.min(betAmount, maxRaise || betAmount), minBet);
    return { action: "BET", amount: size };
  }
  if (legal.includes("RAISE") && minRaise > 0) {
    const raiseSize = Math.min(Math.max(betAmount, minRaise), maxRaise);
    return { action: "RAISE", amount: raiseSize };
  }
  if (legal.includes("CHECK")) return { action: "CHECK" };
  if (legal.includes("CALL")) return { action: "CALL" };
  return { action: "FOLD" };
}

module.exports = { decide, getPosition, OPEN_THRESHOLDS };
