"use strict";

/**
 * Flop-Only Hold'em game model for CFR training.
 *
 * 2 players (IP vs OOP), flop street only. Assumes both players already
 * reached the flop via the preflop chart — we don't model preflop at all.
 *
 * Design rationale:
 *   - By the flop, most 6-max hands are heads-up (1 raiser + 1 caller)
 *   - Full 4-street 6-max CFR at 50 buckets = 50-200M info sets = OOM
 *   - Flop-only 2-player at 50 buckets = ~500K-2M info sets = 80-150MB
 *   - Trainable on any machine in 1-2 hours
 *
 * Bet sizes (5 options when betting, vs 3 in the HU model):
 *   BET_33  — 1/3 pot  (probe bet / thin value)
 *   BET_66  — 2/3 pot  (standard c-bet / value)
 *   BET_POT — full pot (polar / protection)
 *   BET_ALLIN — shove
 *
 * Raise sizes:
 *   RAISE_HALF — min-raise area (half pot on top)
 *   RAISE_POT  — pot-sized raise
 *   RAISE_ALLIN — shove
 *
 * Max 3 raises per street.
 *
 * Pot sizes: the model accepts a configurable initial pot (in BB) to
 * represent different preflop action (limp pot, single-raised, 3-bet).
 * Default: single-raised pot (6.5 BB: open 2.5x + call from BB).
 *
 * Info set key format:
 *   FLOP:{bucket}:s{stack}:{pos}:{pot_class}:{history}
 *
 *   bucket:    0-49 hand strength bucket
 *   stack:     0-2 (short/medium/deep)
 *   pos:       IP or OOP
 *   pot_class: SRP (single raised), 3BP (3-bet pot), LP (limp pot)
 *   history:   encoded actions (k=check, bx=bet, rx=raise, c=call, f=fold)
 */

const { evaluateHand, compareHands } = require("../../src/engine/evaluate");
const { evaluateHandStrength, strengthToBucket, encodeAction } = require("./abstraction");

// ── Configuration ───────────────────────────────────────────────────────

const NUM_BUCKETS = parseInt(process.env.FLOP_BUCKETS || "50", 10);
const NUM_PLAYERS = 2;
const MAX_RAISES = 3;

// Stack and pot defaults (in BB)
const DEFAULT_STACK = 100; // 100bb effective
const BB = 1.0;

// Preflop pot scenarios (BB units, after preflop action, before flop)
const POT_SCENARIOS = {
  SRP:  { pot: 6.5, invested: 2.5 },   // open 2.5x, BB calls: 2.5+2.5+0.5(SB dead)=5.5, +1BB = 6.5
  "3BP": { pot: 20,  invested: 7.5 },   // 3-bet pot: ~20BB
  LP:   { pot: 2.5, invested: 1.0 },    // limp pot: SB completes + BB checks
};

// ── Card utilities ──────────────────────────────────────────────────────

function makeCard(rank, suit) { return { rank, suit }; }

function buildDeck() {
  const cards = [];
  for (let suit = 1; suit <= 4; suit++)
    for (let rank = 2; rank <= 14; rank++)
      cards.push(makeCard(rank, suit));
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

// ── Game State ──────────────────────────────────────────────────────────

/**
 * Create flop game state. Both players are already in the pot.
 *
 * @param {Array} p0Cards - OOP player hole cards (BB / early position caller)
 * @param {Array} p1Cards - IP player hole cards (BTN / CO opener)
 * @param {Array} board - 3 flop cards
 * @param {Object} [opts] - { potClass: 'SRP'|'3BP'|'LP', effectiveStack: 100 }
 */
function createInitialState(p0Cards, p1Cards, board, opts) {
  const options = opts || {};
  const potClass = options.potClass || "SRP";
  const scenario = POT_SCENARIOS[potClass] || POT_SCENARIOS.SRP;
  const effectiveStack = options.effectiveStack || DEFAULT_STACK;

  // Both players have already invested `scenario.invested` BB preflop
  const remainingStack = effectiveStack - scenario.invested;

  return {
    p0Cards,           // OOP (BB/EP caller)
    p1Cards,           // IP (BTN/CO opener)
    board,             // 3 flop cards (fixed, no more dealing)

    pot: scenario.pot,
    potClass,

    p0Stack: remainingStack,
    p1Stack: remainingStack,
    p0Invested: 0,     // flop street investment (reset from preflop)
    p1Invested: 0,
    currentBet: 0,
    raisesThisStreet: 0,

    actionHistory: "",
    actionsThisStreet: 0,

    // OOP acts first on flop
    activePlayer: 0,

    isTerminal: false,
    payoff: [0, 0],
  };
}

// ── Legal Actions ───────────────────────────────────────────────────────

function getLegalActions(state) {
  if (state.isTerminal) return [];

  const p = state.activePlayer;
  const myStack = p === 0 ? state.p0Stack : state.p1Stack;
  const myInvested = p === 0 ? state.p0Invested : state.p1Invested;
  const toCall = state.currentBet - myInvested;

  const actions = [];

  if (toCall > 0) {
    // Facing a bet or raise
    actions.push("FOLD");
    actions.push("CALL");

    if (state.raisesThisStreet < MAX_RAISES && myStack > toCall) {
      const remaining = myStack - toCall;
      const potAfterCall = state.pot + toCall;

      const halfRaise = Math.max(BB, Math.round(potAfterCall * 0.5 * 100) / 100);
      const potRaise = Math.max(BB, potAfterCall);

      if (remaining >= halfRaise) actions.push("RAISE_HALF");
      if (remaining >= potRaise) actions.push("RAISE_POT");
      actions.push("RAISE_ALLIN");
    }
  } else {
    // First to act or checked to
    actions.push("CHECK");

    if (myStack > 0) {
      const third = Math.max(BB, Math.round(state.pot * 0.33 * 100) / 100);
      const twoThird = Math.max(BB, Math.round(state.pot * 0.66 * 100) / 100);
      const full = Math.max(BB, state.pot);

      if (myStack >= third) actions.push("BET_33");
      if (myStack >= twoThird) actions.push("BET_66");
      if (myStack >= full) actions.push("BET_POT");
      actions.push("BET_ALLIN");
    }
  }

  return actions;
}

// ── Bet Sizing ──────────────────────────────────────────────────────────

function getBetAmount(state, action) {
  const p = state.activePlayer;
  const myStack = p === 0 ? state.p0Stack : state.p1Stack;
  const myInvested = p === 0 ? state.p0Invested : state.p1Invested;
  const toCall = state.currentBet - myInvested;

  switch (action) {
    case "BET_33":
      return Math.min(myStack, Math.max(BB, Math.round(state.pot * 0.33 * 100) / 100));
    case "BET_66":
      return Math.min(myStack, Math.max(BB, Math.round(state.pot * 0.66 * 100) / 100));
    case "BET_POT":
      return Math.min(myStack, Math.max(BB, state.pot));
    case "BET_ALLIN":
      return myStack;
    case "RAISE_HALF": {
      const potAfterCall = state.pot + toCall;
      return Math.min(myStack, toCall + Math.max(BB, Math.round(potAfterCall * 0.5 * 100) / 100));
    }
    case "RAISE_POT": {
      const potAfterCall = state.pot + toCall;
      return Math.min(myStack, toCall + Math.max(BB, potAfterCall));
    }
    case "RAISE_ALLIN":
      return myStack;
    case "CALL":
      return Math.min(myStack, toCall);
    default:
      return 0;
  }
}

// ── Action Encoding ─────────────────────────────────────────────────────

function encodeFlopAction(action) {
  switch (action) {
    case "FOLD": return "f";
    case "CHECK": return "k";
    case "CALL": return "c";
    case "BET_33": return "bt";    // t = third
    case "BET_66": return "bs";    // s = standard (2/3)
    case "BET_POT": return "bp";
    case "BET_ALLIN": return "ba";
    case "RAISE_HALF": return "rh";
    case "RAISE_POT": return "rp";
    case "RAISE_ALLIN": return "ra";
    default: return "?";
  }
}

// ── State Transitions ───────────────────────────────────────────────────

function applyAction(state, action) {
  const s = { ...state };
  const p = s.activePlayer;
  const opp = 1 - p;

  s.actionHistory = s.actionHistory + encodeFlopAction(action);
  s.actionsThisStreet++;

  switch (action) {
    case "FOLD": {
      s.isTerminal = true;
      // Winner gets the pot. Payoff = net gain on flop (not counting preflop investment).
      const p0Inv = s.p0Invested;
      const p1Inv = s.p1Invested;
      if (opp === 0) {
        s.payoff = [p1Inv, -p1Inv];   // p0 wins p1's flop investment
      } else {
        s.payoff = [-p0Inv, p0Inv];   // p1 wins p0's flop investment
      }
      return s;
    }

    case "CHECK": {
      // If both checked (OOP checked, IP checked), flop is over → showdown
      if (s.actionsThisStreet >= 2 && s.currentBet === 0) {
        return resolveShowdown(s);
      }
      s.activePlayer = opp;
      return s;
    }

    case "CALL": {
      const amount = getBetAmount(state, action);
      s.pot += amount;
      if (p === 0) { s.p0Invested += amount; s.p0Stack -= amount; }
      else { s.p1Invested += amount; s.p1Stack -= amount; }

      // Call ends the betting → showdown (flop-only, no more streets)
      return resolveShowdown(s);
    }

    case "BET_33":
    case "BET_66":
    case "BET_POT":
    case "BET_ALLIN":
    case "RAISE_HALF":
    case "RAISE_POT":
    case "RAISE_ALLIN": {
      const amount = getBetAmount(state, action);
      s.pot += amount;
      if (p === 0) { s.p0Invested += amount; s.p0Stack -= amount; }
      else { s.p1Invested += amount; s.p1Stack -= amount; }
      s.currentBet = p === 0 ? s.p0Invested : s.p1Invested;
      s.raisesThisStreet++;

      // If all-in and opponent also all-in, go to showdown
      const oppStack = opp === 0 ? s.p0Stack : s.p1Stack;
      if (oppStack <= 0) {
        return resolveShowdown(s);
      }

      s.activePlayer = opp;
      return s;
    }

    default:
      throw new Error(`Unknown action: ${action}`);
  }
}

// ── Showdown ────────────────────────────────────────────────────────────

/**
 * Resolve showdown on the flop.
 *
 * Since this is flop-only, we need to deal turn+river to evaluate
 * properly. We use a heuristic strength comparison instead to avoid
 * needing pre-dealt turn/river cards. This is the standard approach
 * for single-street CFR — equity at this point IS the expected value.
 *
 * For training, we use the hand strength heuristic (bucket-based).
 * Since both players' hands are bucketed the same way, relative
 * comparison is consistent.
 */
function resolveShowdown(state) {
  const s = { ...state };
  s.isTerminal = true;

  const str0 = evaluateHandStrength(s.p0Cards, s.board, "FLOP");
  const str1 = evaluateHandStrength(s.p1Cards, s.board, "FLOP");

  const p0Inv = s.p0Invested;
  const p1Inv = s.p1Invested;

  const eps = 0.0001;
  if (str0 > str1 + eps) {
    // OOP wins
    s.payoff = [p1Inv, -p1Inv];
  } else if (str1 > str0 + eps) {
    // IP wins
    s.payoff = [-p0Inv, p0Inv];
  } else {
    // Tie — split (net zero)
    s.payoff = [0, 0];
  }

  return s;
}

// ── Information Set Key ─────────────────────────────────────────────────

function getStackBucket(stack) {
  if (stack < 30) return 0;  // short (<30bb remaining)
  if (stack < 80) return 1;  // medium
  return 2;                   // deep (80bb+)
}

function getInfoSetKey(state) {
  const p = state.activePlayer;
  const cards = p === 0 ? state.p0Cards : state.p1Cards;
  const stack = p === 0 ? state.p0Stack : state.p1Stack;

  const strength = evaluateHandStrength(cards, state.board, "FLOP");
  const bucket = strengthToBucket(strength, NUM_BUCKETS);
  const stackBucket = getStackBucket(stack);
  const pos = p === 0 ? "OOP" : "IP";
  const potClass = state.potClass || "SRP";

  return `FLOP:${bucket}:s${stackBucket}:${pos}:${potClass}:${state.actionHistory}`;
}

// ── Deal for Iteration ──────────────────────────────────────────────────

/**
 * Deal cards for one MCCFR iteration.
 * Samples 2 hole cards per player + 3 flop cards.
 * Also randomly selects a pot scenario to train across all preflop contexts.
 */
function dealForIteration(rng) {
  const rand = rng || Math.random;
  const deck = shuffleDeck(buildDeck(), rand);
  const p0Cards = [deck[0], deck[1]];
  const p1Cards = [deck[2], deck[3]];
  const board = [deck[4], deck[5], deck[6]];

  // Randomly select pot scenario: 60% SRP, 25% 3BP, 15% LP
  // (weighted by frequency at 6-max microstakes)
  const r = rand();
  let potClass;
  if (r < 0.60) potClass = "SRP";
  else if (r < 0.85) potClass = "3BP";
  else potClass = "LP";

  // Vary effective stacks: 70% medium (100bb), 20% short (40bb), 10% deep (150bb)
  const sr = rand();
  let effectiveStack;
  if (sr < 0.70) effectiveStack = 100;
  else if (sr < 0.90) effectiveStack = 40;
  else effectiveStack = 150;

  return { p0Cards, p1Cards, board, potClass, effectiveStack };
}

// ── Module exports ──────────────────────────────────────────────────────

// Wrapper for train-cfr.js compatibility
function createInitialStateFromDeal(p0Cards, p1Cards, board) {
  // Called by the existing trainer with 3 args
  // Default to SRP, 100bb
  return createInitialState(p0Cards, p1Cards, board);
}

// Full deal-aware creation (used when trainer passes deal object)
function createInitialStateFromFullDeal(deal) {
  return createInitialState(
    deal.p0Cards, deal.p1Cards, deal.board,
    { potClass: deal.potClass, effectiveStack: deal.effectiveStack }
  );
}

module.exports = {
  createInitialState: createInitialStateFromDeal,
  createInitialStateFull: createInitialStateFromFullDeal,
  getLegalActions,
  applyAction,
  getInfoSetKey,
  dealForIteration,
  resolveShowdown,
  NUM_BUCKETS,
  NUM_PLAYERS,
  MAX_RAISES,
  buildDeck,
  shuffleDeck,
  POT_SCENARIOS,
  encodeFlopAction,
};
