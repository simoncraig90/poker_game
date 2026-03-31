#!/usr/bin/env node
"use strict";

/**
 * Engine worker: stdin/stdout JSON-line interface to the poker engine.
 *
 * Reads one JSON command per line from stdin, executes it against
 * the engine, and writes one JSON response per line to stdout.
 *
 * Commands:
 *   {"cmd":"init","seats":6,"stacks":[1000,1000,...]}
 *   {"cmd":"start_hand"}
 *   {"cmd":"act","seat":N,"action":"FOLD","amount":N}
 *   {"cmd":"get_state"}
 *   {"cmd":"step_tag","nn_seats":[0]}  — run TAG actions until NN seat or hand end
 *
 * All engine errors are returned as {"ok":false,"error":"..."} without crashing.
 */

const { createGame, ACTION, PHASE } = require("../src/index");
const readline = require("readline");

// ── Globals ─────────────────────────────────────────────────────────────

let game = null;
let numSeats = 0;
let startStacks = [];
let botNames = [];

// ── Deterministic RNG for TAG strategy ──────────────────────────────────

let tagRngState = 42;

function tagRng() {
  tagRngState = (tagRngState * 1664525 + 1013904223) & 0x7fffffff;
  return tagRngState / 0x7fffffff;
}

// ── TAG Strategy (built into worker for zero-IPC TAG decisions) ─────────

function evaluateHandStrength(cards, board, phase) {
  if (!cards || cards.length < 2) return 0.5;
  const c1 = cards[0], c2 = cards[1];
  const r1 = c1.rank, r2 = c2.rank;
  const suited = c1.suit === c2.suit;
  const pair = r1 === r2;
  const highCard = Math.max(r1, r2);
  const gap = Math.abs(r1 - r2);
  const connected = gap === 1;
  let pfStrength = 0;
  if (pair) { pfStrength = 0.5 + (r1 / 14) * 0.5; }
  else {
    pfStrength = (highCard / 14) * 0.4;
    if (suited) pfStrength += 0.08;
    if (connected) pfStrength += 0.06;
    if (gap <= 3) pfStrength += 0.03;
    if (r1 >= 10 && r2 >= 10) pfStrength += 0.15;
    if (highCard === 14) pfStrength += 0.1;
  }
  if (phase === PHASE.PREFLOP) return Math.min(1, pfStrength);
  const boardRanks = board.map(c => c.rank);
  const boardSuits = board.map(c => c.suit);
  let postStrength = pfStrength;
  if (boardRanks.includes(r1)) postStrength += 0.25;
  if (boardRanks.includes(r2)) postStrength += 0.20;
  if (boardRanks.includes(r1) && boardRanks.includes(r2) && !pair) postStrength += 0.20;
  if (pair && boardRanks.includes(r1)) postStrength += 0.35;
  const suitCount = boardSuits.filter(s => s === c1.suit).length;
  if (suitCount >= 2 && suited) postStrength += 0.12;
  if (suitCount >= 3 && (c1.suit === boardSuits[0] || c2.suit === boardSuits[0])) postStrength += 0.30;
  if (pair && r1 > Math.max(...boardRanks)) postStrength += 0.15;
  return Math.min(1, postStrength);
}

function tagDecision(seatIdx) {
  const state = game.getState();
  const seat = state.table.seats[seatIdx];
  const hand = state.hand;
  const legal = game.getLegalActions(seatIdx);
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;

  if (actions.length === 0) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seat.holeCards || [];
  const phase = hand.phase;
  const potSize = hand.pot || 0;
  const stack = seat.stack;
  const strength = evaluateHandStrength(cards, hand.board || [], phase);

  if (phase === PHASE.PREFLOP) {
    if (strength > 0.7 && actions.includes(ACTION.RAISE)) {
      const raiseAmt = Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise);
      return { action: ACTION.RAISE, amount: Math.max(minRaise, raiseAmt) };
    }
    if (strength > 0.35 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (strength > 0.35 && actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }
  if (strength > 0.7) {
    if (actions.includes(ACTION.RAISE)) {
      const raiseAmt = Math.min(minRaise + Math.floor(potSize * 0.75), maxRaise);
      return { action: ACTION.RAISE, amount: Math.max(minRaise, raiseAmt) };
    }
    if (actions.includes(ACTION.BET)) {
      const betAmt = Math.min(Math.floor(potSize * 0.66), stack, Math.max(minBet, 2));
      return { action: ACTION.BET, amount: Math.max(minBet, betAmt) };
    }
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  if (strength > 0.35) {
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.5) return { action: ACTION.CALL };
    if (tagRng() < 0.15 && actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: minBet };
    return { action: ACTION.FOLD };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  if (tagRng() < 0.10 && actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: minBet };
  return { action: ACTION.FOLD };
}

// ── Helpers ─────────────────────────────────────────────────────────────

function serializeCard(card) {
  if (!card) return null;
  return { rank: card.rank, suit: card.suit, display: card.display };
}

function buildSeatStates() {
  const state = game.getState();
  const seats = {};
  for (let i = 0; i < numSeats; i++) {
    const s = state.table.seats[i];
    if (!s) continue;
    seats[i] = {
      stack: s.stack,
      holeCards: s.holeCards ? s.holeCards.map(serializeCard) : null,
      folded: !!s.folded,
      allIn: !!s.allIn,
      inHand: !!s.inHand,
      bet: s.bet || 0,
      totalInvested: s.totalInvested || 0,
      playerName: s.player ? s.player.name : null,
    };
  }
  return seats;
}

function buildHandState() {
  const state = game.getState();
  const hand = state.hand;
  if (!hand) return null;

  let legalActions = null;
  if (hand.actionSeat !== null && hand.actionSeat !== undefined) {
    const la = game.getLegalActions(hand.actionSeat);
    if (la.actions.length > 0) {
      legalActions = {
        actions: la.actions,
        callAmount: la.callAmount,
        minBet: la.minBet,
        minRaise: la.minRaise,
        maxRaise: la.maxRaise,
      };
    }
  }

  return {
    handId: hand.handId,
    phase: hand.phase,
    board: (hand.board || []).map(serializeCard),
    pot: hand.pot || 0,
    actionSeat: hand.actionSeat,
    complete: hand.phase === PHASE.COMPLETE,
    showdown: !!hand.showdown,
    winners: hand.winners || [],
    resultText: hand.resultText || [],
    currentBet: hand.currentBet || 0,
    legalActions,
  };
}

function buildFullResponse() {
  return {
    ok: true,
    seats: buildSeatStates(),
    hand: buildHandState(),
  };
}

// ── Command Handlers ────────────────────────────────────────────────────

function handleInit(msg) {
  numSeats = msg.seats || 6;
  startStacks = msg.stacks || new Array(numSeats).fill(1000);

  let rng = null;
  if (msg.seed !== undefined) {
    let s = msg.seed;
    rng = function () {
      s = (s * 1664525 + 1013904223) & 0x7fffffff;
      return s / 0x7fffffff;
    };
  }

  // Sync TAG RNG seed
  if (msg.tagSeed !== undefined) {
    tagRngState = msg.tagSeed;
  } else if (msg.seed !== undefined) {
    tagRngState = msg.seed;
  }

  game = createGame(
    {
      tableId: "fast-selfplay",
      tableName: "FastSelfPlay",
      maxSeats: numSeats,
      sb: msg.sb || 5,
      bb: msg.bb || 10,
      minBuyIn: 100,
      maxBuyIn: 50000,
    },
    { sessionId: `fast-${Date.now()}`, logPath: null, rng }
  );

  botNames = [];
  for (let i = 0; i < numSeats; i++) {
    const name = (msg.names && msg.names[i]) || `Bot${i}`;
    botNames.push(name);
    game.sitDown(i, name, startStacks[i]);
  }

  return { ok: true, seats: numSeats };
}

function handleStartHand(msg) {
  if (!game) return { ok: false, error: "Not initialized" };

  const state = game.getState();
  for (let i = 0; i < numSeats; i++) {
    const s = state.table.seats[i];
    if (s && s.stack < 20) {
      try {
        game.leave(i);
        game.sitDown(i, botNames[i], startStacks[i]);
      } catch (e) {}
    }
  }

  game.startHand();
  return buildFullResponse();
}

function handleAct(msg) {
  if (!game) return { ok: false, error: "Not initialized" };
  game.act(msg.seat, msg.action, msg.amount);
  return buildFullResponse();
}

/**
 * step_tag: Run TAG actions for non-NN seats until an NN seat needs to act
 * or the hand completes. Returns full state + stats about what happened.
 *
 * This is the key optimization: instead of one IPC roundtrip per TAG action,
 * all TAG decisions execute in-process and we only cross the IPC boundary
 * when the neural net needs to make a decision.
 */
function handleStepTag(msg) {
  if (!game) return { ok: false, error: "Not initialized" };

  const nnSeats = new Set(msg.nn_seats || []);
  let tagActions = 0;
  let tagErrors = 0;
  const vpipCounts = {};  // seat -> count of VPIP actions this step
  const pfrCounts = {};   // seat -> count of PFR actions this step
  const maxIter = 200;    // safety limit

  for (let iter = 0; iter < maxIter; iter++) {
    if (game.isHandComplete()) break;

    const actionSeat = game.getActionSeat();
    if (actionSeat === null) break;

    // If this is an NN seat, stop — Python needs to decide
    if (nnSeats.has(actionSeat)) break;

    // TAG decision (runs in-process, no IPC)
    const decision = tagDecision(actionSeat);
    if (!decision) break;

    // Track VPIP/PFR before acting
    const state = game.getState();
    const phase = state.hand ? state.hand.phase : null;
    if (phase === PHASE.PREFLOP) {
      if (decision.action === ACTION.CALL || decision.action === ACTION.RAISE || decision.action === ACTION.BET) {
        vpipCounts[actionSeat] = (vpipCounts[actionSeat] || 0) + 1;
      }
      if (decision.action === ACTION.RAISE) {
        pfrCounts[actionSeat] = (pfrCounts[actionSeat] || 0) + 1;
      }
    }

    try {
      game.act(actionSeat, decision.action, decision.amount);
      tagActions++;
    } catch (e) {
      try { game.act(actionSeat, ACTION.FOLD); } catch (_) {}
      tagErrors++;
      tagActions++;
    }
  }

  const resp = buildFullResponse();
  resp.tagActions = tagActions;
  resp.tagErrors = tagErrors;
  resp.vpipCounts = vpipCounts;
  resp.pfrCounts = pfrCounts;
  return resp;
}

function handleGetState(msg) {
  if (!game) return { ok: false, error: "Not initialized" };
  return buildFullResponse();
}

// ── Main Loop ───────────────────────────────────────────────────────────

const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on("line", (line) => {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: "Invalid JSON" }) + "\n");
    return;
  }

  let response;
  try {
    switch (msg.cmd) {
      case "init":       response = handleInit(msg); break;
      case "start_hand": response = handleStartHand(msg); break;
      case "act":        response = handleAct(msg); break;
      case "step_tag":   response = handleStepTag(msg); break;
      case "get_state":  response = handleGetState(msg); break;
      case "quit":
        process.stdout.write(JSON.stringify({ ok: true }) + "\n");
        process.exit(0);
        break;
      default:
        response = { ok: false, error: `Unknown command: ${msg.cmd}` };
    }
  } catch (e) {
    response = { ok: false, error: e.message };
  }

  process.stdout.write(JSON.stringify(response) + "\n");
});

rl.on("close", () => process.exit(0));

process.on("uncaughtException", (e) => {
  if (e.code === "EPIPE") process.exit(0);
  process.stderr.write(`engine-worker error: ${e.message}\n`);
});
