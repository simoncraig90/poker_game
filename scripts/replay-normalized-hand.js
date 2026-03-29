#!/usr/bin/env node
"use strict";

// ═══════════════════════════════════════════════════════════════════════════
//  Replay Consumer for Normalized Hand Events
//
//  Proves that normalized-hand-events.jsonl is sufficient to reconstruct
//  hand state over time. Maintains a deterministic state machine and emits
//  replay-state.jsonl (state snapshot after each event) and
//  replay-timeline.txt (human-readable step-by-step replay).
// ═══════════════════════════════════════════════════════════════════════════

const fs = require("fs");
const path = require("path");

const inputFile = process.argv[2];
if (!inputFile) {
  console.error("Usage: node replay-normalized-hand.js <hand-file.jsonl>");
  console.error("  e.g. node replay-normalized-hand.js captures/20260329_202750/hands/hand-260272188638.jsonl");
  process.exit(1);
}

if (!fs.existsSync(inputFile)) {
  console.error("File not found: " + inputFile);
  process.exit(1);
}

const events = fs
  .readFileSync(inputFile, "utf8")
  .trim()
  .split("\n")
  .map((l) => JSON.parse(l));

const outDir = path.dirname(inputFile);
const handId = events[0]?.handId || "unknown";
const stateFile = path.join(outDir, `replay-state-${handId}.jsonl`);
const timelineFile = path.join(outDir, `replay-timeline-${handId}.txt`);

// ═══════════════════════════════════════════════════════════════════════════
//  Replay State
// ═══════════════════════════════════════════════════════════════════════════

const state = {
  // Hand identity
  handId: null,
  tableId: null,
  tableName: null,
  button: null,
  sb: null,
  bb: null,

  // Lifecycle
  phase: "INIT", // INIT → PREFLOP → FLOP → TURN → RIVER → SHOWDOWN → COMPLETE

  // Players: seat → { name, startStack, stack, bet, folded, allIn, country }
  seats: {},

  // Cards
  heroCards: null,
  board: [],

  // Actions (ordered)
  actions: [],

  // Pot
  pot: 0,
  potBreakdown: [], // from POT_UPDATE

  // Result
  winners: [],
  showdown: null,
  totalPot: null,
  handRank: null,
  resultText: [],

  // Accounting
  inferredCount: 0,
};

// ═══════════════════════════════════════════════════════════════════════════
//  Helpers
// ═══════════════════════════════════════════════════════════════════════════

function c$(v) {
  if (v == null) return "?";
  return Math.abs(v) >= 100 ? "$" + (v / 100).toFixed(2) : v + "c";
}

function snapshot() {
  // Return a deep-ish copy of state for serialization
  return {
    handId: state.handId,
    phase: state.phase,
    seats: JSON.parse(JSON.stringify(state.seats)),
    heroCards: state.heroCards ? [...state.heroCards] : null,
    board: [...state.board],
    pot: state.pot,
    actionCount: state.actions.length,
    inferredCount: state.inferredCount,
    lastAction: state.actions.length > 0 ? state.actions[state.actions.length - 1] : null,
  };
}

const stateSnapshots = [];
const timelineLines = [];

function recordState(eventType, seq, detail) {
  const snap = snapshot();
  snap._event = { type: eventType, seq, detail };
  stateSnapshots.push(snap);
}

function tl(line) {
  timelineLines.push(line);
}

function seatName(seat) {
  const s = state.seats[seat];
  return s ? s.name : `Seat${seat}`;
}

function activePlayers() {
  return Object.values(state.seats).filter((s) => !s.folded);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Event Processors
// ═══════════════════════════════════════════════════════════════════════════

function processHandStart(e) {
  state.handId = e.handId;
  state.tableId = e.tableId;
  state.tableName = e.tableName;
  state.button = e.button;
  state.sb = e.sb;
  state.bb = e.bb;
  state.phase = "PREFLOP";

  for (const [seatStr, p] of Object.entries(e.players)) {
    const seat = parseInt(seatStr);
    state.seats[seat] = {
      name: p.name,
      startStack: p.stack,
      stack: p.stack,
      bet: 0, // current street bet
      totalInvested: 0, // total chips put in across all streets
      folded: false,
      allIn: false,
      country: p.country,
    };
  }

  tl(`╔══════════════════════════════════════════════════════════╗`);
  tl(`║  Hand #${state.handId}`);
  tl(`║  ${state.tableName} | ${c$(state.sb)}/${c$(state.bb)} NL Hold'em`);
  tl(`║  Button: Seat ${state.button} (${seatName(state.button)})`);
  tl(`╠══════════════════════════════════════════════════════════╣`);
  tl(`║  Seats:`);
  for (const [seat, s] of Object.entries(state.seats).sort(([a], [b]) => a - b)) {
    const isBtn = parseInt(seat) === state.button ? " [BTN]" : "";
    tl(`║    Seat ${seat}: ${s.name} (${s.country}) ${c$(s.stack)}${isBtn}`);
  }
  tl(`╚══════════════════════════════════════════════════════════╝`);
}

function processBlindPost(e) {
  const s = state.seats[e.seat];
  if (!s) return;

  s.stack -= e.amount;
  s.bet += e.amount;
  s.totalInvested += e.amount;
  state.pot += e.amount;

  const inf = "";
  tl(`  ${s.name} posts ${e.blindType} ${c$(e.amount)}  [stack: ${c$(s.stack)}]`);
}

function processHeroCards(e) {
  state.heroCards = e.cards;
  tl(`  ★ Hero cards: ${e.cards.join(" ")}`);
}

function processPlayerAction(e) {
  const s = state.seats[e.seat];
  if (!s) return;

  const inf = e.inferred ? " {inferred}" : "";
  if (e.inferred) state.inferredCount++;

  const action = {
    seat: e.seat,
    player: s.name,
    action: e.action,
    amount: e.totalBet || 0,
    delta: e.delta || 0,
    street: e.street || state.phase,
    inferred: e.inferred || false,
  };
  state.actions.push(action);

  switch (e.action) {
    case "FOLD":
      s.folded = true;
      tl(`  ${s.name} folds${inf}`);
      break;

    case "CHECK":
      tl(`  ${s.name} checks${inf}`);
      break;

    case "CALL": {
      const cost = e.delta || 0;
      s.stack -= cost;
      s.bet += cost;
      s.totalInvested += cost;
      state.pot += cost;
      if (s.stack === 0) s.allIn = true;
      tl(`  ${s.name} calls ${c$(e.totalBet)}${s.allIn ? " (ALL-IN)" : ""}  [stack: ${c$(s.stack)}]${inf}`);
      break;
    }

    case "BET": {
      const cost = e.delta || e.totalBet || 0;
      s.stack -= cost;
      s.bet += cost;
      s.totalInvested += cost;
      state.pot += cost;
      if (s.stack === 0) s.allIn = true;
      tl(`  ${s.name} bets ${c$(e.totalBet)}${s.allIn ? " (ALL-IN)" : ""}  [stack: ${c$(s.stack)}]${inf}`);
      break;
    }

    case "RAISE": {
      const cost = e.delta || 0;
      s.stack -= cost;
      s.bet += cost;
      s.totalInvested += cost;
      state.pot += cost;
      if (s.stack === 0) s.allIn = true;
      tl(`  ${s.name} raises to ${c$(e.totalBet)}${s.allIn ? " (ALL-IN)" : ""}  [stack: ${c$(s.stack)}]${inf}`);
      break;
    }

    default:
      tl(`  ${s.name} ${e.action} ${c$(e.totalBet)}${inf}`);
  }
}

function processDealCommunity(e) {
  state.board = e.board ? [...e.board] : state.board.concat(e.newCards || []);
  state.phase = e.street;

  // Reset per-street bets
  for (const s of Object.values(state.seats)) {
    s.bet = 0;
  }

  const active = activePlayers();
  tl("");
  tl(`  ── ${e.street} [${state.board.join(" ")}] ──  (${active.length} players remaining)`);
}

function processPotUpdate(e) {
  state.potBreakdown = e.pots || [];
  // We track pot from actions, but record the server's view for comparison
}

function processPotAward(e) {
  state.winners = (e.awards || []).map((a) => ({
    seat: a.seat,
    player: a.player || seatName(a.seat),
    amount: a.amount,
  }));

  tl("");
  for (const w of state.winners) {
    const s = state.seats[w.seat];
    if (s) s.stack += w.amount;
    tl(`  ★★ ${w.player} wins ${c$(w.amount)} ★★`);
  }
}

function processHandSummary(e) {
  state.showdown = e.showdown;
  state.totalPot = e.totalPot;
  state.handRank = e.handRank;

  const sd = e.showdown ? "SHOWDOWN" : "no showdown";
  tl(`  Result: ${e.winPlayer} wins ${c$(e.totalPot)} (${sd})`);
  if (e.handRank) tl(`  Hand: ${e.handRank}`);
  if (e.board && e.board.length > 0) tl(`  Board: ${e.board.join(" ")}`);
}

function processHandResult(e) {
  state.resultText = (e.results || []).map((r) => ({
    seat: r.seat,
    player: r.player || seatName(r.seat),
    won: r.won,
    text: r.text,
  }));

  tl("");
  for (const r of state.resultText) {
    tl(`  ${r.player}: ${r.text}`);
  }
}

function processHandEnd(e) {
  state.phase = "COMPLETE";

  // Final accounting
  tl("");
  tl(`┌── Final Stacks ──────────────────────────────────────────┐`);
  for (const [seat, s] of Object.entries(state.seats).sort(([a], [b]) => a - b)) {
    const delta = s.stack - s.startStack;
    const sign = delta >= 0 ? "+" : "";
    tl(`│  Seat ${seat}: ${s.name.padEnd(14)} ${c$(s.stack).padStart(8)}  (${sign}${c$(delta)})`);
  }
  tl(`├── Accounting ────────────────────────────────────────────┤`);

  // Verify pot math
  const totalInvested = Object.values(state.seats).reduce((sum, s) => sum + s.totalInvested, 0);
  const totalWon = state.winners.reduce((sum, w) => sum + w.amount, 0);
  const potFromActions = state.pot;
  const potFromSummary = state.totalPot || 0;

  tl(`│  Pot (from actions):  ${c$(potFromActions)}`);
  tl(`│  Pot (from summary):  ${c$(potFromSummary)}`);
  tl(`│  Total invested:      ${c$(totalInvested)}`);
  tl(`│  Total awarded:       ${c$(totalWon)}`);

  const balanceCheck = totalInvested === totalWon;
  tl(`│  Balance check:       ${balanceCheck ? "PASS ✓" : "FAIL ✗ (invested ≠ awarded)"}`);

  if (potFromActions !== potFromSummary) {
    tl(`│  Pot mismatch:        actions=${c$(potFromActions)} vs summary=${c$(potFromSummary)}`);
    tl(`│    (expected: inferred folds don't carry amounts)`);
  }

  tl(`│  Inferred events:     ${state.inferredCount}`);
  tl(`│  Actions:             ${state.actions.length}`);
  tl(`│  Board:               ${state.board.length > 0 ? state.board.join(" ") : "(none)"}`);
  tl(`│  Hero:                ${state.heroCards ? state.heroCards.join(" ") : "(not dealt)"}`);
  tl(`└──────────────────────────────────────────────────────────┘`);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Main Loop
// ═══════════════════════════════════════════════════════════════════════════

for (const e of events) {
  switch (e.type) {
    case "HAND_START":
      processHandStart(e);
      break;
    case "BLIND_POST":
      processBlindPost(e);
      break;
    case "HERO_CARDS":
      processHeroCards(e);
      break;
    case "PLAYER_ACTION":
      processPlayerAction(e);
      break;
    case "DEAL_COMMUNITY":
      processDealCommunity(e);
      break;
    case "POT_UPDATE":
      processPotUpdate(e);
      break;
    case "POT_AWARD":
      processPotAward(e);
      break;
    case "HAND_SUMMARY":
      processHandSummary(e);
      break;
    case "HAND_RESULT":
      processHandResult(e);
      break;
    case "HAND_END":
      processHandEnd(e);
      break;
    case "TABLE_SNAPSHOT":
      // TABLE_SNAPSHOT may appear if replaying from combined file — skip in hand replay
      break;
    default:
      tl(`  [unknown event: ${e.type}]`);
      break;
  }

  recordState(e.type, e.seq, null);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Output
// ═══════════════════════════════════════════════════════════════════════════

fs.writeFileSync(stateFile, stateSnapshots.map((s) => JSON.stringify(s)).join("\n") + "\n");
fs.writeFileSync(timelineFile, timelineLines.join("\n") + "\n");

console.log("Replay complete.");
console.log(`  Hand:     #${state.handId}`);
console.log(`  Events:   ${events.length}`);
console.log(`  Actions:  ${state.actions.length}`);
console.log(`  Inferred: ${state.inferredCount}`);
console.log(`  Board:    ${state.board.join(" ") || "(none)"}`);
console.log(`  Output:`);
console.log(`    ${stateFile}`);
console.log(`    ${timelineFile}`);
