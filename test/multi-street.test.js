#!/usr/bin/env node
"use strict";

/**
 * Multi-street hand test.
 * Plays a hand through flop and turn with actions, then fold to settle.
 */

const { createGame } = require("../src/index");
const path = require("path");
const fs = require("fs");

const logDir = path.join(__dirname, "..", "test-output");
fs.mkdirSync(logDir, { recursive: true });

let seed = 123;
function rng() {
  seed = (seed * 1664525 + 1013904223) & 0x7fffffff;
  return seed / 0x7fffffff;
}

const game = createGame(
  { tableId: "t2", tableName: "Multi-Street Test", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 400, maxBuyIn: 1000 },
  { sessionId: "test-multi", rng }
);

game.sitDown(0, "Alice", 1000, "US");
game.sitDown(1, "Bob", 1000, "GB");
game.sitDown(2, "Charlie", 1000, "CA");
game.sitDown(3, "Diana", 1000, "AU");

console.log("=== Starting multi-street hand ===");
game.startHand();

const hand = game.getState().hand;
console.log(`Button: ${hand.button}, SB: ${hand.sbSeat}, BB: ${hand.bbSeat}`);

// Preflop: seat after BB acts first
// Action order: UTG → Button → SB → BB
// Let's have everyone call the BB
function actAndLog(seat, action, amount) {
  const s = game.getState().table.seats[seat];
  const label = amount != null ? `${action} ${amount}` : action;
  game.act(seat, action, amount);
  console.log(`  Seat ${seat} (${s.player.name}): ${label}  [stack: ${s.stack}c]`);
}

console.log("\n-- PREFLOP --");
let next = game.getActionSeat();
// First player calls
actAndLog(next, "CALL");
next = game.getActionSeat();
// Second player raises to 30
actAndLog(next, "RAISE", 30);
next = game.getActionSeat();
// Third player folds
actAndLog(next, "FOLD");
next = game.getActionSeat();
// Fourth player calls
actAndLog(next, "CALL");
next = game.getActionSeat();
// First player calls the raise
if (next !== null) actAndLog(next, "CALL");

console.log(`  Phase: ${game.getState().hand.phase}, Board: ${game.getState().hand.board.map(c=>c.display).join(" ")}`);

// Flop
console.log("\n-- FLOP --");
next = game.getActionSeat();
while (next !== null && game.getState().hand.phase === "FLOP") {
  const s = game.getState().table.seats[next];
  if (!s.folded) {
    // First active player bets 20, others fold
    if (game.getState().hand.actions.filter(a => a.street === "FLOP" && (a.type === "BET" || a.type === "RAISE")).length === 0) {
      actAndLog(next, "BET", 20);
    } else {
      actAndLog(next, "FOLD");
    }
  }
  next = game.getActionSeat();
}

console.log(`  Phase: ${game.getState().hand.phase}`);
console.log(`  Hand complete: ${game.isHandComplete()}`);

// Final state
const events = game.getEvents();
const handEvents = events.filter(e => e.handId === "1");

console.log(`\n=== Events: ${handEvents.length} ===`);
const types = {};
handEvents.forEach(e => types[e.type] = (types[e.type] || 0) + 1);
console.log(JSON.stringify(types, null, 2));

// Accounting
let totalEnd = 0;
for (const seat of Object.values(game.getState().table.seats)) {
  if (seat.player) {
    console.log(`${seat.player.name}: ${seat.stack}c (${seat.stack > 1000 ? "+" : ""}${seat.stack - 1000}c)`);
    totalEnd += seat.stack;
  }
}
console.log(`\nTotal: ${totalEnd}c (expected 4000c)`);
console.log(`Accounting: ${totalEnd === 4000 ? "PASS ✓" : "FAIL ✗"}`);

// Replay check
const handLog = path.join(logDir, "multi-street-hand.jsonl");
fs.writeFileSync(handLog, handEvents.map(e => JSON.stringify(e)).join("\n") + "\n");
const { execSync } = require("child_process");
try {
  const out = execSync(`node scripts/replay-normalized-hand.js "${handLog}"`, { encoding: "utf8", cwd: path.join(__dirname, "..") });
  console.log(out);
} catch (e) {
  console.log("Replay error:", e.stdout || e.message);
}
