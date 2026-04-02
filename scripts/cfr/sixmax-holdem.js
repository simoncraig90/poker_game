#!/usr/bin/env node
"use strict";

/**
 * 6-Max No-Limit Hold'em game model for CFR training.
 *
 * 6 players, proper blind structure, positional awareness.
 * Uses aggressive abstraction to keep game tree manageable:
 *   - 50 card buckets via hand strength
 *   - 3 bet sizes (half-pot, pot, all-in)
 *   - Max 3 raises per street
 *   - Players fold out → reduces to heads-up/3-way
 *
 * Positions (relative to dealer):
 *   0=BTN, 1=SB, 2=BB, 3=UTG, 4=MP, 5=CO
 *
 * Preflop action order: UTG(3) → MP(4) → CO(5) → BTN(0) → SB(1) → BB(2)
 * Postflop action order: SB(1) → BB(2) → UTG(3) → MP(4) → CO(5) → BTN(0)
 */

const { evaluateHand, compareHands } = require("../../src/engine/evaluate");
const { evaluateHandStrength, strengthToBucket, encodeAction } = require("./abstraction");

const NUM_PLAYERS = 6;
const NUM_BUCKETS = 10;
const STARTING_STACK = 100; // BB
const SB_AMOUNT = 0.5;
const BB_AMOUNT = 1.0;
const MAX_RAISES_PER_STREET = 2;

const STREETS = ["PREFLOP", "FLOP", "TURN", "RIVER"];
const NEXT_STREET = { PREFLOP: "FLOP", FLOP: "TURN", TURN: "RIVER" };
const BOARD_CARDS_PER_STREET = { FLOP: 3, TURN: 1, RIVER: 1 };

// Position names for info set keys
const POS_NAMES = ["BTN", "SB", "BB", "UTG", "MP", "CO"];

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

// ── Preflop action order ────────────────────────────────────────────────

// Preflop: UTG(3) → MP(4) → CO(5) → BTN(0) → SB(1) → BB(2)
const PREFLOP_ORDER = [3, 4, 5, 0, 1, 2];
// Postflop: SB(1) → BB(2) → UTG(3) → MP(4) → CO(5) → BTN(0)
const POSTFLOP_ORDER = [1, 2, 3, 4, 5, 0];

function getNextPlayer(state, afterPlayer) {
  const order = state.street === "PREFLOP" ? PREFLOP_ORDER : POSTFLOP_ORDER;
  const idx = order.indexOf(afterPlayer);
  for (let i = 1; i < NUM_PLAYERS; i++) {
    const next = order[(idx + i) % NUM_PLAYERS];
    if (state.active[next] && !state.folded[next] && state.stacks[next] > 0) {
      return next;
    }
  }
  return -1; // no one left
}

function getFirstPlayer(state) {
  const order = state.street === "PREFLOP" ? PREFLOP_ORDER : POSTFLOP_ORDER;
  for (const p of order) {
    if (state.active[p] && !state.folded[p] && state.stacks[p] > 0) {
      return p;
    }
  }
  return -1;
}

function countActivePlayers(state) {
  let count = 0;
  for (let i = 0; i < NUM_PLAYERS; i++) {
    if (state.active[i] && !state.folded[i]) count++;
  }
  return count;
}

// ── Game State ──────────────────────────────────────────────────────────

function createInitialState(playerCards, fullBoard) {
  // playerCards: array of 6 elements, each [card, card]
  // fullBoard: 5 board cards

  const stacks = new Array(NUM_PLAYERS).fill(STARTING_STACK);
  const invested = new Array(NUM_PLAYERS).fill(0);
  const active = new Array(NUM_PLAYERS).fill(true);
  const folded = new Array(NUM_PLAYERS).fill(false);
  const allIn = new Array(NUM_PLAYERS).fill(false);

  // Post blinds
  stacks[1] -= SB_AMOUNT; invested[1] = SB_AMOUNT; // SB
  stacks[2] -= BB_AMOUNT; invested[2] = BB_AMOUNT; // BB

  return {
    playerCards,
    fullBoard: fullBoard || [],
    board: [],
    street: "PREFLOP",
    pot: SB_AMOUNT + BB_AMOUNT,
    stacks,
    invested,
    active,
    folded,
    allIn,
    currentBet: BB_AMOUNT,
    raisesThisStreet: 0,
    streetHistory: "",
    previousStreets: "",
    activePlayer: 3, // UTG first preflop
    actionsThisStreet: 0,
    lastRaiser: -1,   // track who raised last (for action-closes logic)
    isTerminal: false,
    payoff: new Array(NUM_PLAYERS).fill(0),
  };
}

// ── Legal Actions ───────────────────────────────────────────────────────

function getLegalActions(state) {
  if (state.isTerminal) return [];
  const p = state.activePlayer;
  if (p < 0 || state.folded[p] || !state.active[p]) return [];

  const toCall = state.currentBet - state.invested[p];
  const myStack = state.stacks[p];
  const actions = [];

  if (toCall > 0) {
    actions.push("FOLD");
    actions.push("CALL");
    if (state.raisesThisStreet < MAX_RAISES_PER_STREET && myStack > toCall) {
      const remaining = myStack - toCall;
      const potAfterCall = state.pot + toCall;
      const halfPot = Math.max(BB_AMOUNT, Math.floor(potAfterCall * 0.5 * 100) / 100);
      if (remaining >= halfPot) actions.push("RAISE_HALF");
      actions.push("RAISE_ALLIN");
    }
  } else {
    actions.push("CHECK");
    if (myStack > 0) {
      const halfPot = Math.max(BB_AMOUNT, Math.floor(state.pot * 0.5 * 100) / 100);
      if (myStack >= halfPot) actions.push("BET_HALF");
      actions.push("BET_ALLIN");
    }
  }

  return actions;
}

// ── Bet sizing ──────────────────────────────────────────────────────────

function getBetAmount(state, action) {
  const p = state.activePlayer;
  const myStack = state.stacks[p];
  const toCall = state.currentBet - state.invested[p];

  switch (action) {
    case "BET_HALF": return Math.min(myStack, Math.max(BB_AMOUNT, Math.floor(state.pot * 0.5 * 100) / 100));
    case "BET_POT": return Math.min(myStack, Math.max(BB_AMOUNT, state.pot));
    case "BET_ALLIN": return myStack;
    case "RAISE_HALF": { const r = Math.max(BB_AMOUNT, Math.floor((state.pot + toCall) * 0.5 * 100) / 100); return Math.min(myStack, toCall + r); }
    case "RAISE_POT": { const r = Math.max(BB_AMOUNT, state.pot + toCall); return Math.min(myStack, toCall + r); }
    case "RAISE_ALLIN": return myStack;
    case "CALL": return Math.min(myStack, toCall);
    default: return 0;
  }
}

// ── State Transitions ───────────────────────────────────────────────────

function applyAction(state, action) {
  const s = {
    ...state,
    stacks: state.stacks.slice(),
    invested: state.invested.slice(),
    active: state.active.slice(),
    folded: state.folded.slice(),
    allIn: state.allIn.slice(),
    payoff: state.payoff.slice(),
  };
  const p = s.activePlayer;

  s.streetHistory += encodeAction(action);
  s.actionsThisStreet++;

  switch (action) {
    case "FOLD": {
      s.folded[p] = true;
      const remaining = countActivePlayers(s);
      if (remaining === 1) {
        // Last player standing wins
        return settleLastStanding(s);
      }
      return advanceAction(s, p);
    }

    case "CHECK": {
      return advanceAction(s, p);
    }

    case "CALL": {
      const amount = getBetAmount(state, action);
      s.stacks[p] -= amount;
      s.invested[p] += amount;
      s.pot += amount;
      if (s.stacks[p] <= 0) s.allIn[p] = true;

      // Check if everyone is all-in or only one player can still act
      const canAct = [];
      for (let i = 0; i < NUM_PLAYERS; i++) {
        if (s.active[i] && !s.folded[i] && !s.allIn[i] && s.stacks[i] > 0) canAct.push(i);
      }
      if (canAct.length <= 1 && s.invested[p] >= s.currentBet) {
        // Everyone matched or all-in — check if street should end
        return checkStreetEnd(s, p);
      }

      return advanceAction(s, p);
    }

    case "BET_HALF":
    case "BET_POT":
    case "BET_ALLIN":
    case "RAISE_HALF":
    case "RAISE_POT":
    case "RAISE_ALLIN": {
      const amount = getBetAmount(state, action);
      s.stacks[p] -= amount;
      s.invested[p] += amount;
      s.pot += amount;
      s.currentBet = s.invested[p];
      s.raisesThisStreet++;
      s.lastRaiser = p;
      if (s.stacks[p] <= 0) s.allIn[p] = true;

      // Next active player
      const next = getNextPlayer(s, p);
      if (next === -1) return checkAllActed(s);
      s.activePlayer = next;
      return s;
    }

    default:
      throw new Error(`Unknown action: ${action}`);
  }
}

function advanceAction(state, lastActor) {
  const s = state;

  // Check if action is complete for this street
  // Action completes when we get back to the last raiser, or everyone has acted
  const next = getNextPlayer(s, lastActor);
  if (next === -1) {
    return checkStreetEnd(s, lastActor);
  }

  // Preflop special: BB gets option if no raise
  if (s.street === "PREFLOP" && s.raisesThisStreet === 0) {
    // Has BB had a chance to act?
    if (lastActor === 2 && s.actionsThisStreet >= NUM_PLAYERS) {
      // BB checked — street over
      return advanceStreet(s);
    }
    if (next === s.lastRaiser && s.lastRaiser !== -1) {
      return advanceStreet(s);
    }
    s.activePlayer = next;
    return s;
  }

  // If we've gone around to the last raiser (or back to start), street ends
  if (s.lastRaiser >= 0 && next === s.lastRaiser) {
    return advanceStreet(s);
  }

  // If no bet/raise this street and everyone has checked
  if (s.lastRaiser === -1 && s.currentBet === 0) {
    // Count how many have acted
    const activePlayers = [];
    for (let i = 0; i < NUM_PLAYERS; i++) {
      if (s.active[i] && !s.folded[i]) activePlayers.push(i);
    }
    if (s.actionsThisStreet >= activePlayers.length) {
      return advanceStreet(s);
    }
  }

  // If everyone has matched the current bet
  if (s.currentBet > 0) {
    let allMatched = true;
    for (let i = 0; i < NUM_PLAYERS; i++) {
      if (s.active[i] && !s.folded[i] && !s.allIn[i] && s.invested[i] < s.currentBet) {
        allMatched = false;
        break;
      }
    }
    if (allMatched && s.actionsThisStreet > 0 && next === s.lastRaiser) {
      return advanceStreet(s);
    }
  }

  s.activePlayer = next;
  return s;
}

function checkStreetEnd(state, lastActor) {
  return advanceStreet(state);
}

function checkAllActed(state) {
  // Everyone who can act has acted or is all-in
  const activePlayers = [];
  for (let i = 0; i < NUM_PLAYERS; i++) {
    if (state.active[i] && !state.folded[i]) activePlayers.push(i);
  }
  if (activePlayers.length <= 1) return settleLastStanding(state);

  // Check if anyone can still act
  const canAct = activePlayers.filter(i => !state.allIn[i] && state.stacks[i] > 0);
  if (canAct.length <= 1) {
    // Run out the board
    return runOutBoard(state);
  }
  return advanceStreet(state);
}

function settleLastStanding(state) {
  const s = { ...state, payoff: state.payoff.slice() };
  s.isTerminal = true;
  s.street = "SHOWDOWN";

  let winner = -1;
  for (let i = 0; i < NUM_PLAYERS; i++) {
    if (s.active[i] && !s.folded[i]) { winner = i; break; }
  }

  for (let i = 0; i < NUM_PLAYERS; i++) {
    const totalInvested = STARTING_STACK - s.stacks[i] - (s.folded[i] ? 0 : 0);
    const invested = STARTING_STACK - s.stacks[i];
    if (i === winner) {
      s.payoff[i] = s.pot - invested;
    } else {
      s.payoff[i] = -invested;
    }
  }
  return s;
}

function advanceStreet(state) {
  const s = {
    ...state,
    stacks: state.stacks.slice(),
    invested: state.invested.slice(),
    active: state.active.slice(),
    folded: state.folded.slice(),
    allIn: state.allIn.slice(),
    payoff: state.payoff.slice(),
  };

  const nextStreet = NEXT_STREET[s.street];
  s.previousStreets = s.previousStreets
    ? s.previousStreets + "-" + s.streetHistory
    : s.streetHistory;

  if (!nextStreet) {
    return resolveShowdown(s);
  }

  s.street = nextStreet;
  const numCards = BOARD_CARDS_PER_STREET[nextStreet];
  const boardStart = s.board.length;
  s.board = s.fullBoard.slice(0, boardStart + numCards);

  s.streetHistory = "";
  s.actionsThisStreet = 0;
  s.raisesThisStreet = 0;
  s.currentBet = 0;
  s.lastRaiser = -1;
  for (let i = 0; i < NUM_PLAYERS; i++) s.invested[i] = 0;

  const first = getFirstPlayer(s);
  if (first === -1) return runOutBoard(s);
  s.activePlayer = first;

  // If only 1 player can act (others all-in), just advance
  const canAct = [];
  for (let i = 0; i < NUM_PLAYERS; i++) {
    if (s.active[i] && !s.folded[i] && !s.allIn[i] && s.stacks[i] > 0) canAct.push(i);
  }
  if (canAct.length <= 1) {
    return runOutBoard(s);
  }

  return s;
}

function runOutBoard(state) {
  const s = {
    ...state,
    stacks: state.stacks.slice(),
    invested: state.invested.slice(),
    payoff: state.payoff.slice(),
  };
  s.board = s.fullBoard.slice(0, 5);
  s.street = "SHOWDOWN";
  return resolveShowdown(s);
}

// ── Showdown ────────────────────────────────────────────────────────────

function resolveShowdown(state) {
  const s = { ...state, payoff: state.payoff.slice() };
  s.isTerminal = true;
  s.street = "SHOWDOWN";

  const board = s.board.length >= 5 ? s.board.slice(0, 5) : s.fullBoard.slice(0, 5);

  // Find all active (non-folded) players
  const contenders = [];
  for (let i = 0; i < NUM_PLAYERS; i++) {
    if (s.active[i] && !s.folded[i]) contenders.push(i);
  }

  if (contenders.length === 0) return s;
  if (contenders.length === 1) return settleLastStanding(s);

  // Evaluate all hands
  let bestPlayer = contenders[0];
  let bestHand = null;
  try {
    bestHand = evaluateHand([...s.playerCards[bestPlayer], ...board]);
  } catch (e) {
    // Fallback to heuristic
    return resolveShowdownHeuristic(s);
  }

  const tied = [bestPlayer];

  for (let i = 1; i < contenders.length; i++) {
    const p = contenders[i];
    try {
      const hand = evaluateHand([...s.playerCards[p], ...board]);
      const cmp = compareHands(hand, bestHand);
      if (cmp > 0) {
        bestPlayer = p;
        bestHand = hand;
        tied.length = 0;
        tied.push(p);
      } else if (cmp === 0) {
        tied.push(p);
      }
    } catch (e) {
      continue;
    }
  }

  // Distribute pot
  const totalPot = s.pot;
  const share = totalPot / tied.length;
  for (let i = 0; i < NUM_PLAYERS; i++) {
    const invested = STARTING_STACK - s.stacks[i];
    if (tied.includes(i)) {
      s.payoff[i] = share - invested;
    } else {
      s.payoff[i] = -invested;
    }
  }

  return s;
}

function resolveShowdownHeuristic(state) {
  const s = { ...state, payoff: state.payoff.slice() };
  s.isTerminal = true;

  let bestPlayer = -1;
  let bestStr = -1;
  const contenders = [];
  for (let i = 0; i < NUM_PLAYERS; i++) {
    if (s.active[i] && !s.folded[i]) {
      const str = evaluateHandStrength(s.playerCards[i], s.board, s.street);
      contenders.push({ player: i, strength: str });
      if (str > bestStr) { bestStr = str; bestPlayer = i; }
    }
  }

  const eps = 0.001;
  const winners = contenders.filter(c => c.strength >= bestStr - eps).map(c => c.player);
  const share = s.pot / winners.length;

  for (let i = 0; i < NUM_PLAYERS; i++) {
    const invested = STARTING_STACK - s.stacks[i];
    if (winners.includes(i)) {
      s.payoff[i] = share - invested;
    } else {
      s.payoff[i] = -invested;
    }
  }

  return s;
}

// ── Information Set Key ─────────────────────────────────────────────────

function getInfoSetKey(state) {
  const p = state.activePlayer;
  const cards = state.playerCards[p];
  const strength = evaluateHandStrength(cards, state.board, state.street);
  const bucket = strengthToBucket(strength, NUM_BUCKETS);

  const stack = state.stacks[p];
  const bbs = stack;
  const stackBucket = bbs < 30 ? 0 : bbs < 80 ? 1 : 2;

  const pos = POS_NAMES[p];
  const numActive = countActivePlayers(state);

  let fullHistory = state.previousStreets || "";
  if (state.streetHistory) {
    fullHistory = fullHistory ? fullHistory + "-" + state.streetHistory : state.streetHistory;
  }

  // Format: STREET:bucket:stackBucket:POS:numPlayers:history
  return `${state.street}:${bucket}:s${stackBucket}:${pos}:${numActive}p:${fullHistory}`;
}

// ── Deal for iteration ──────────────────────────────────────────────────

function dealForIteration(rng) {
  const deck = shuffleDeck(buildDeck(), rng);
  const playerCards = [];
  for (let i = 0; i < NUM_PLAYERS; i++) {
    playerCards.push([deck[i * 2], deck[i * 2 + 1]]);
  }
  const board = [deck[12], deck[13], deck[14], deck[15], deck[16]];
  return { playerCards, board };
}

module.exports = {
  createInitialState,
  getLegalActions,
  applyAction,
  getInfoSetKey,
  dealForIteration,
  resolveShowdown,
  NUM_BUCKETS,
  NUM_PLAYERS,
  buildDeck,
  shuffleDeck,
  POS_NAMES,
};
