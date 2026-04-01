"use strict";

/**
 * Full Hold'em game model for CFR training.
 *
 * Covers all 4 streets (preflop, flop, turn, river) with:
 *   - Heads-up only (2 players)
 *   - Bet abstraction: half-pot, pot, all-in (plus check/fold/call)
 *   - Card abstraction: 10 buckets per street via hand strength
 *   - Max 3 raises per street to limit tree depth
 *   - Monte Carlo sampling of board cards (external sampling MCCFR)
 *
 * Uses the engine's hand evaluator for showdown.
 */

const { evaluateHand, compareHands } = require("../../src/engine/evaluate");
const {
  evaluateHandStrength,
  strengthToBucket,
  encodeAction,
} = require("./abstraction");

const NUM_BUCKETS = 20;

// Starting stack in BB. Effective stack for the game.
const STARTING_STACK = 100;

// Small blind / big blind in BB units.
const SB_AMOUNT = 0.5;
const BB_AMOUNT = 1.0;

// Maximum raises per street.
const MAX_RAISES_PER_STREET = 3;

// Street progression.
const STREETS = ["PREFLOP", "FLOP", "TURN", "RIVER"];
const NEXT_STREET = { PREFLOP: "FLOP", FLOP: "TURN", TURN: "RIVER" };
const BOARD_CARDS_PER_STREET = { FLOP: 3, TURN: 1, RIVER: 1 };

// ── Card utilities ───────────────────────────────────────────────────────

function makeCard(rank, suit) {
  return { rank, suit };
}

function buildDeck() {
  const cards = [];
  for (let suit = 1; suit <= 4; suit++) {
    for (let rank = 2; rank <= 14; rank++) {
      cards.push(makeCard(rank, suit));
    }
  }
  return cards;
}

function shuffleDeck(deck, rng) {
  const rand = rng || Math.random;
  const a = deck.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// ── Game State ───────────────────────────────────────────────────────────

/**
 * Create the initial game state after dealing cards and posting blinds.
 *
 * @param {Array} p0Cards - Player 0 (SB) hole cards, 2 elements
 * @param {Array} p1Cards - Player 1 (BB) hole cards, 2 elements
 * @param {Array} fullBoard - Pre-sampled board cards (up to 5)
 * @returns {Object} Initial game state
 */
function createInitialState(p0Cards, p1Cards, fullBoard) {
  return {
    // Cards
    p0Cards,
    p1Cards,
    fullBoard: fullBoard || [],  // all 5 board cards, dealt progressively
    board: [],                    // currently visible board cards

    // Street
    street: "PREFLOP",

    // Betting state (in BB units)
    pot: SB_AMOUNT + BB_AMOUNT,
    p0Invested: SB_AMOUNT,        // SB posted
    p1Invested: BB_AMOUNT,        // BB posted
    p0Stack: STARTING_STACK - SB_AMOUNT,
    p1Stack: STARTING_STACK - BB_AMOUNT,
    currentBet: BB_AMOUNT,        // current bet to match
    raisesThisStreet: 0,

    // Action tracking — per-street history separated by "-"
    streetHistory: "",            // actions within current street
    previousStreets: "",          // encoded actions from completed streets

    // Turn
    activePlayer: 0,              // SB acts first preflop
    actionsThisStreet: 0,         // count of actions taken this street

    // Terminal
    isTerminal: false,
    winner: -1,                   // -1 = undecided, 0 = p0, 1 = p1, 2 = tie
    payoff: [0, 0],
  };
}

// ── Legal Actions ────────────────────────────────────────────────────────

/**
 * Get the list of legal actions for the active player.
 */
function getLegalActions(state) {
  if (state.isTerminal) return [];

  const player = state.activePlayer;
  const myInvested = player === 0 ? state.p0Invested : state.p1Invested;
  const myStack = player === 0 ? state.p0Stack : state.p1Stack;
  const toCall = state.currentBet - myInvested;

  const actions = [];

  if (toCall > 0) {
    // Facing a bet/raise
    actions.push("FOLD");

    if (myStack >= toCall) {
      actions.push("CALL");
    } else {
      // Can only call all-in for less
      actions.push("CALL");
    }

    // Can raise if under the cap and have chips beyond calling
    if (state.raisesThisStreet < MAX_RAISES_PER_STREET && myStack > toCall) {
      const remainingAfterCall = myStack - toCall;
      // Only offer raise sizes the player can afford
      const potAfterCall = state.pot + toCall;
      const halfPotRaise = Math.max(BB_AMOUNT, Math.floor(potAfterCall * 0.5 * 100) / 100);
      const potRaise = Math.max(BB_AMOUNT, potAfterCall);

      if (remainingAfterCall >= halfPotRaise) {
        actions.push("RAISE_HALF");
      }
      if (remainingAfterCall >= potRaise) {
        actions.push("RAISE_POT");
      }
      // All-in is always available if we have chips beyond calling
      actions.push("RAISE_ALLIN");
    }
  } else {
    // No bet to face — can check or bet
    actions.push("CHECK");

    if (myStack > 0) {
      const halfPot = Math.max(BB_AMOUNT, Math.floor(state.pot * 0.5 * 100) / 100);
      const fullPot = Math.max(BB_AMOUNT, state.pot);

      if (myStack >= halfPot) {
        actions.push("BET_HALF");
      }
      if (myStack >= fullPot) {
        actions.push("BET_POT");
      }
      // All-in always available
      actions.push("BET_ALLIN");
    }
  }

  return actions;
}

// ── Bet sizing helpers ───────────────────────────────────────────────────

/**
 * Calculate the actual chip amount for a bet/raise action.
 */
function getBetAmount(state, action) {
  const player = state.activePlayer;
  const myInvested = player === 0 ? state.p0Invested : state.p1Invested;
  const myStack = player === 0 ? state.p0Stack : state.p1Stack;
  const toCall = state.currentBet - myInvested;

  switch (action) {
    case "BET_HALF":
      return Math.min(myStack, Math.max(BB_AMOUNT, Math.floor(state.pot * 0.5 * 100) / 100));
    case "BET_POT":
      return Math.min(myStack, Math.max(BB_AMOUNT, state.pot));
    case "BET_ALLIN":
      return myStack;
    case "RAISE_HALF": {
      const potAfterCall = state.pot + toCall;
      const raiseSize = Math.max(BB_AMOUNT, Math.floor(potAfterCall * 0.5 * 100) / 100);
      return Math.min(myStack, toCall + raiseSize);
    }
    case "RAISE_POT": {
      const potAfterCall = state.pot + toCall;
      const raiseSize = Math.max(BB_AMOUNT, potAfterCall);
      return Math.min(myStack, toCall + raiseSize);
    }
    case "RAISE_ALLIN":
      return myStack;
    case "CALL":
      return Math.min(myStack, toCall);
    default:
      return 0;
  }
}

// ── State Transitions ────────────────────────────────────────────────────

/**
 * Apply an action and return a NEW state (immutable pattern).
 */
function applyAction(state, action) {
  const s = { ...state };
  const player = s.activePlayer;
  const opponent = 1 - player;

  s.streetHistory = s.streetHistory + encodeAction(action);
  s.actionsThisStreet++;

  switch (action) {
    case "FOLD": {
      s.isTerminal = true;
      s.winner = opponent;
      // Payoff: net gain/loss relative to starting stack.
      // Total invested = STARTING_STACK - remaining stack.
      const p0TotalInv = STARTING_STACK - s.p0Stack;
      const p1TotalInv = STARTING_STACK - s.p1Stack;
      if (opponent === 0) {
        s.payoff = [p1TotalInv, -p1TotalInv];
      } else {
        s.payoff = [-p0TotalInv, p0TotalInv];
      }
      return s;
    }

    case "CHECK": {
      // Check: no money changes
      return checkStreetEnd(s, player);
    }

    case "CALL": {
      const amount = getBetAmount(state, action);
      s.pot += amount;
      if (player === 0) {
        s.p0Invested += amount;
        s.p0Stack -= amount;
      } else {
        s.p1Invested += amount;
        s.p1Stack -= amount;
      }

      // A call always ends the action for this street (or is terminal if all-in)
      // If someone is all-in, run out remaining streets
      if (s.p0Stack <= 0 || s.p1Stack <= 0) {
        return runOutBoard(s);
      }

      // After a call: if this is the first action of a preflop round (BB option after limp)
      // the BB still gets to act. Otherwise, street is over.
      if (s.street === "PREFLOP" && s.raisesThisStreet === 0 && s.actionsThisStreet === 1) {
        // SB limped (called BB). BB gets option.
        s.activePlayer = opponent;
        return s;
      }

      // Street is over — advance
      return advanceStreet(s);
    }

    case "BET_HALF":
    case "BET_POT":
    case "BET_ALLIN": {
      const amount = getBetAmount(state, action);
      s.pot += amount;
      if (player === 0) {
        s.p0Invested += amount;
        s.p0Stack -= amount;
      } else {
        s.p1Invested += amount;
        s.p1Stack -= amount;
      }
      s.currentBet = player === 0 ? s.p0Invested : s.p1Invested;
      s.raisesThisStreet++;

      // If all-in and opponent can't act, run out
      if ((player === 0 && s.p0Stack <= 0) || (player === 1 && s.p1Stack <= 0)) {
        // Opponent still needs to respond
        const oppStack = opponent === 0 ? s.p0Stack : s.p1Stack;
        if (oppStack <= 0) {
          return runOutBoard(s);
        }
      }

      s.activePlayer = opponent;
      return s;
    }

    case "RAISE_HALF":
    case "RAISE_POT":
    case "RAISE_ALLIN": {
      const amount = getBetAmount(state, action);
      s.pot += amount;
      if (player === 0) {
        s.p0Invested += amount;
        s.p0Stack -= amount;
      } else {
        s.p1Invested += amount;
        s.p1Stack -= amount;
      }
      s.currentBet = player === 0 ? s.p0Invested : s.p1Invested;
      s.raisesThisStreet++;

      s.activePlayer = opponent;
      return s;
    }

    default:
      throw new Error(`Unknown action: ${action}`);
  }
}

/**
 * Check if the street should end after a check.
 */
function checkStreetEnd(state, player) {
  const s = state;
  const opponent = 1 - player;

  // Preflop: BB checks (option) after SB limps => street over
  if (s.street === "PREFLOP") {
    if (player === 1 && s.actionsThisStreet >= 2) {
      return advanceStreet(s);
    }
    // SB can't normally check preflop (must call or fold), but handle it
    s.activePlayer = opponent;
    return s;
  }

  // Postflop: if both players have checked (second check ends street)
  if (s.actionsThisStreet >= 2) {
    return advanceStreet(s);
  }

  s.activePlayer = opponent;
  return s;
}

/**
 * Advance to the next street or showdown.
 */
function advanceStreet(state) {
  const s = { ...state };
  const nextStreet = NEXT_STREET[s.street];

  // Record this street's history
  s.previousStreets = s.previousStreets
    ? s.previousStreets + "-" + s.streetHistory
    : s.streetHistory;

  if (!nextStreet) {
    // We're on the river — go to showdown
    return resolveShowdown(s);
  }

  // Deal board cards for next street
  s.street = nextStreet;
  const numCards = BOARD_CARDS_PER_STREET[nextStreet];
  const boardStart = s.board.length;
  s.board = s.fullBoard.slice(0, boardStart + numCards);

  // Reset street-level state
  s.streetHistory = "";
  s.actionsThisStreet = 0;
  s.raisesThisStreet = 0;
  // Reset current bet — new street starts fresh
  s.currentBet = 0;
  s.p0Invested = 0;
  s.p1Invested = 0;

  // Postflop: BB (player 1) acts first... actually in heads-up,
  // SB is the button and acts LAST postflop. So BB (player 1) acts first postflop.
  // Wait — in heads-up, SB=Button acts first preflop, BB acts first postflop.
  s.activePlayer = 1; // BB acts first postflop

  return s;
}

/**
 * When someone is all-in, deal remaining board cards and resolve showdown.
 */
function runOutBoard(state) {
  const s = { ...state };
  // Deal all remaining board cards
  s.board = s.fullBoard.slice(0, 5);
  s.street = "SHOWDOWN";
  return resolveShowdown(s);
}

/**
 * Resolve showdown using the engine's hand evaluator.
 */
function resolveShowdown(state) {
  const s = { ...state };
  s.isTerminal = true;
  s.street = "SHOWDOWN";

  // Ensure we have 5 board cards for proper evaluation
  const board = s.board.length >= 5 ? s.board.slice(0, 5) : s.fullBoard.slice(0, 5);

  if (board.length < 5) {
    // Fallback: use heuristic if not enough board cards
    return resolveShowdownHeuristic(s);
  }

  try {
    const hand0 = evaluateHand([...s.p0Cards, ...board]);
    const hand1 = evaluateHand([...s.p1Cards, ...board]);
    const cmp = compareHands(hand0, hand1);

    // Payoffs: what each player gains relative to what they put in.
    // Total invested by each player: STARTING_STACK - remaining stack.
    const p0TotalInvested = STARTING_STACK - s.p0Stack;
    const p1TotalInvested = STARTING_STACK - s.p1Stack;

    if (cmp > 0) {
      // Player 0 wins
      s.winner = 0;
      s.payoff = [p1TotalInvested, -p1TotalInvested];
    } else if (cmp < 0) {
      // Player 1 wins
      s.winner = 1;
      s.payoff = [-p0TotalInvested, p0TotalInvested];
    } else {
      // Tie
      s.winner = 2;
      s.payoff = [0, 0];
    }
  } catch (e) {
    // If evaluation fails, fall back to heuristic
    return resolveShowdownHeuristic(s);
  }

  return s;
}

/**
 * Heuristic showdown (fallback if board is incomplete).
 */
function resolveShowdownHeuristic(state) {
  const s = { ...state };
  s.isTerminal = true;

  const str0 = evaluateHandStrength(s.p0Cards, s.board, s.street);
  const str1 = evaluateHandStrength(s.p1Cards, s.board, s.street);

  const p0TotalInvested = STARTING_STACK - s.p0Stack;
  const p1TotalInvested = STARTING_STACK - s.p1Stack;

  const eps = 0.001;
  if (str0 > str1 + eps) {
    s.winner = 0;
    s.payoff = [p1TotalInvested, -p1TotalInvested];
  } else if (str1 > str0 + eps) {
    s.winner = 1;
    s.payoff = [-p0TotalInvested, p0TotalInvested];
  } else {
    s.winner = 2;
    s.payoff = [0, 0];
  }

  return s;
}

// ── Information Set Key ──────────────────────────────────────────────────

/**
 * Get the information set key for the current player.
 * Format: "STREET:bucket:full_action_history"
 * The full action history encodes all streets separated by dashes.
 */
function getStackBucket(stack, bb) {
  const bbs = stack / bb;
  if (bbs < 30) return 0;  // short
  if (bbs < 80) return 1;  // medium
  return 2;                 // deep
}

function getInfoSetKey(state) {
  const player = state.activePlayer;
  const cards = player === 0 ? state.p0Cards : state.p1Cards;
  const stack = player === 0 ? state.p0Stack : state.p1Stack;
  const strength = evaluateHandStrength(cards, state.board, state.street);
  const bucket = strengthToBucket(strength, NUM_BUCKETS);
  const stackBucket = getStackBucket(stack, state.bb || 10);

  // Build full history: previous streets + current street
  let fullHistory = state.previousStreets || "";
  if (state.streetHistory) {
    fullHistory = fullHistory ? fullHistory + "-" + state.streetHistory : state.streetHistory;
  }

  return `${state.street}:${bucket}:s${stackBucket}:${fullHistory}`;
}

// ── Deal random cards for one MCCFR iteration ───────────────────────────

/**
 * Deal a complete set of cards for one MCCFR iteration.
 * Pre-samples all 5 board cards (Monte Carlo external sampling).
 */
function dealForIteration(rng) {
  const deck = shuffleDeck(buildDeck(), rng);
  const p0Cards = [deck[0], deck[1]];
  const p1Cards = [deck[2], deck[3]];
  // Pre-deal all 5 board cards
  const board = [deck[4], deck[5], deck[6], deck[7], deck[8]];
  return { p0Cards, p1Cards, board };
}

module.exports = {
  createInitialState,
  getLegalActions,
  applyAction,
  getInfoSetKey,
  dealForIteration,
  resolveShowdown,
  NUM_BUCKETS,
  buildDeck,
  shuffleDeck,
  STARTING_STACK,
  MAX_RAISES_PER_STREET,
};
