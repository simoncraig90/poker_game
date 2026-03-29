#!/usr/bin/env node
"use strict";

/**
 * Test consecutive hands: button rotates, stacks carry over.
 */

const { createGame } = require("../src/index");

let seed = 77;
function rng() { seed = (seed * 1664525 + 1013904223) & 0x7fffffff; return seed / 0x7fffffff; }

const game = createGame(
  { tableId: "t3", tableName: "Consecutive", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 400, maxBuyIn: 1000 },
  { sessionId: "test-consec", rng }
);

game.sitDown(0, "Alice", 1000, "US");
game.sitDown(1, "Bob", 1000, "GB");
game.sitDown(3, "Charlie", 1000, "CA"); // seat 2 empty, testing skip

const handResults = [];

for (let h = 0; h < 5; h++) {
  game.startHand();
  const hand = game.getState().hand;

  // Everyone folds to BB (simplest hand)
  let next = game.getActionSeat();
  while (next !== null && !game.isHandComplete()) {
    game.act(next, "FOLD");
    next = game.getActionSeat();
  }

  // Record result
  const st = game.getState();
  const stacks = {};
  for (const s of Object.values(st.table.seats)) {
    if (s.player) stacks[s.player.name] = s.stack;
  }
  handResults.push({
    handId: hand.handId,
    button: hand.button,
    sbSeat: hand.sbSeat,
    bbSeat: hand.bbSeat,
    stacks,
  });
}

console.log("=== 5 Consecutive Hands ===\n");
for (const r of handResults) {
  console.log(`Hand #${r.handId}: btn=${r.button} sb=${r.sbSeat} bb=${r.bbSeat} | ` +
    Object.entries(r.stacks).map(([n, s]) => `${n}=${s}`).join(" "));
}

// Verify button rotates through 0, 1, 3 (skipping empty seat 2)
const buttons = handResults.map(r => r.button);
console.log(`\nButton sequence: ${buttons.join(" → ")}`);

// Verify total chips stay constant
const total = Object.values(handResults[4].stacks).reduce((a, b) => a + b, 0);
console.log(`Total chips: ${total}c (expected 3000c)`);
console.log(`Accounting: ${total === 3000 ? "PASS ✓" : "FAIL ✗"}`);

// Verify events count
const events = game.getEvents();
const handStarts = events.filter(e => e.type === "HAND_START").length;
const handEnds = events.filter(e => e.type === "HAND_END").length;
console.log(`\nHAND_START: ${handStarts}, HAND_END: ${handEnds}`);
console.log(`Paired: ${handStarts === handEnds && handStarts === 5 ? "PASS ✓" : "FAIL ✗"}`);
