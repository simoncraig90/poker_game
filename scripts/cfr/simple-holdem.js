"use strict";

/**
 * Simplified Hold'em for CFR training.
 *
 * This is a self-contained game model that CFR traverses directly
 * (no engine dependency for the game tree walk). The real engine
 * is used only for showdown evaluation.
 *
 * Variants (progressive complexity):
 *   1. PREFLOP_ONLY  - 2 players, preflop only, limit betting (1 raise max)
 *   2. FLOP          - adds flop street
 *   3. FULL          - all four streets
 *
 * We start with PREFLOP_ONLY to verify CFR convergence.
 *
 * Game tree for PREFLOP_ONLY with limit betting:
 *   - SB posts 0.5 BB, BB posts 1 BB
 *   - SB acts first: FOLD, CALL (limp to 1 BB), RAISE (to 2 BB)
 *   - If SB calls: BB can CHECK or RAISE (to 2 BB)
 *   - If SB raises: BB can FOLD, CALL, or RAISE (to 3 BB, cap)
 *   - If BB raises after SB limp: SB can FOLD, CALL, or RAISE (to 3 BB, cap)
 *   - Max 1 re-raise (cap at 3 BB)
 *   - Showdown: best hand wins by heuristic strength
 */

const { evaluateHandStrength, strengthToBucket, makeInfoSetKey, encodeAction } = require("./abstraction");

const NUM_BUCKETS = 50;

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
 * Immutable game state for CFR tree traversal.
 * Using plain objects for speed (no class overhead).
 */
function createInitialState(p0Cards, p1Cards, board) {
  return {
    // Cards
    p0Cards,       // player 0 (SB) hole cards
    p1Cards,       // player 1 (BB) hole cards
    board: board || [],

    // Betting state
    pot: 1.5,      // SB=0.5 + BB=1.0  (in BB units, so SB=0.5, BB=1.0)
    p0Invested: 0.5,
    p1Invested: 1.0,
    currentBet: 1.0, // BB amount
    numRaises: 0,
    maxRaises: 2,  // cap: SB can raise to 2, BB can re-raise to 3

    // Action tracking
    history: "",   // encoded action string
    activePlayer: 0, // 0 = SB (acts first preflop)
    phase: "PREFLOP",

    // Terminal
    isTerminal: false,
    winner: -1,    // -1 = not decided, 0 = p0, 1 = p1, 2 = tie
    payoff: [0, 0], // net payoff to each player
  };
}

// ── Legal Actions ────────────────────────────────────────────────────────

function getLegalActions(state) {
  if (state.isTerminal) return [];

  const actions = ["FOLD"];
  const player = state.activePlayer;
  const myInvested = player === 0 ? state.p0Invested : state.p1Invested;
  const toCall = state.currentBet - myInvested;

  if (toCall === 0) {
    actions.length = 0; // remove FOLD when nothing to call (can just check)
    actions.push("CHECK");
  } else {
    actions.push("CALL");
  }

  // Can raise if under the cap
  if (state.numRaises < state.maxRaises) {
    actions.push("RAISE");
  }

  return actions;
}

// ── State Transitions ────────────────────────────────────────────────────

/**
 * Apply an action and return a NEW state (immutable).
 * For preflop-only limit game, bet sizes are fixed:
 *   - CALL: match current bet
 *   - RAISE: increase by 1 BB
 */
function applyAction(state, action) {
  const s = { ...state };
  const player = s.activePlayer;
  const opponent = 1 - player;

  s.history = s.history + encodeAction(action);

  switch (action) {
    case "FOLD": {
      s.isTerminal = true;
      s.winner = opponent;
      // Payoff: folding player loses what they invested
      s.payoff = [0, 0];
      s.payoff[player] = -(player === 0 ? s.p0Invested : s.p1Invested);
      s.payoff[opponent] = -s.payoff[player];
      return s;
    }

    case "CHECK": {
      // Check: no money added. If both have checked (BB checks after SB limp),
      // go to showdown (in preflop-only game).
      // BB checks after SB calls = showdown
      if (player === 1 && s.history.length >= 2) {
        // BB checked; both have acted and bets are equal -> showdown
        return resolveShowdown(s);
      }
      // SB's check... but SB can't check preflop (must call BB or fold)
      // This shouldn't happen in normal preflop; keep for safety
      s.activePlayer = opponent;
      return s;
    }

    case "CALL": {
      const myInvested = player === 0 ? s.p0Invested : s.p1Invested;
      const callAmount = s.currentBet - myInvested;
      s.pot += callAmount;
      if (player === 0) s.p0Invested += callAmount;
      else s.p1Invested += callAmount;

      // After a call, if this is responding to a raise -> showdown
      // OR if SB limps (calls BB), BB gets option
      if (s.numRaises > 0) {
        // Calling a raise -> showdown
        return resolveShowdown(s);
      } else {
        // SB limps to BB level. BB gets option to check or raise.
        s.activePlayer = opponent;
        return s;
      }
    }

    case "RAISE": {
      const myInvested = player === 0 ? s.p0Invested : s.p1Invested;
      const newBet = s.currentBet + 1.0; // raise by 1 BB
      const raiseAmount = newBet - myInvested;
      s.pot += raiseAmount;
      if (player === 0) s.p0Invested += raiseAmount;
      else s.p1Invested += raiseAmount;
      s.currentBet = newBet;
      s.numRaises++;
      s.activePlayer = opponent;
      return s;
    }

    default:
      throw new Error(`Unknown action: ${action}`);
  }
}

/**
 * Resolve showdown: compare hands, set terminal state and payoffs.
 */
function resolveShowdown(state) {
  const s = { ...state };
  s.isTerminal = true;

  const str0 = evaluateHandStrength(s.p0Cards, s.board, s.phase);
  const str1 = evaluateHandStrength(s.p1Cards, s.board, s.phase);

  // Use a small epsilon for ties (pure heuristic can produce exact matches)
  const eps = 0.001;
  if (str0 > str1 + eps) {
    s.winner = 0;
    s.payoff = [s.p1Invested, -s.p1Invested];
  } else if (str1 > str0 + eps) {
    s.winner = 1;
    s.payoff = [-s.p0Invested, s.p0Invested];
  } else {
    // Tie: split pot, net zero
    s.winner = 2;
    s.payoff = [0, 0];
  }

  return s;
}

// ── Information Set Key ──────────────────────────────────────────────────

/**
 * Get the information set key for the current player.
 * This is what the player "knows": their card bucket + action history.
 */
function getInfoSetKey(state) {
  const player = state.activePlayer;
  const cards = player === 0 ? state.p0Cards : state.p1Cards;
  const strength = evaluateHandStrength(cards, state.board, state.phase);
  const bucket = strengthToBucket(strength, NUM_BUCKETS);
  return makeInfoSetKey(bucket, state.history);
}

// ── Deal random cards for one CFR iteration ──────────────────────────────

function dealForIteration(rng) {
  const deck = shuffleDeck(buildDeck(), rng);
  const p0Cards = [deck[0], deck[1]];
  const p1Cards = [deck[2], deck[3]];
  // For preflop-only, no board cards needed
  return { p0Cards, p1Cards, board: [] };
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
};
