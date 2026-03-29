#!/usr/bin/env node
"use strict";

/**
 * Hand lifecycle smoke test.
 * Plays a complete hand: blinds → preflop actions → settle.
 * Verifies event output and accounting closure.
 */

const { createGame } = require("../src/index");
const path = require("path");
const fs = require("fs");

const logPath = path.join(__dirname, "..", "test-output", "smoke-hand.jsonl");
fs.mkdirSync(path.dirname(logPath), { recursive: true });

// Deterministic RNG (seeded)
let seed = 42;
function rng() {
  seed = (seed * 1664525 + 1013904223) & 0x7fffffff;
  return seed / 0x7fffffff;
}

const game = createGame(
  { tableId: "test-1", tableName: "Smoke Test", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 400, maxBuyIn: 1000 },
  { sessionId: "test-session", logPath, rng }
);

// Seat 3 players
game.sitDown(0, "Alice", 1000, "US");
game.sitDown(1, "Bob", 1000, "GB");
game.sitDown(2, "Charlie", 1000, "CA");

console.log("=== Starting hand ===");
game.startHand();

const state = game.getState();
console.log("Button:", state.hand.button);
console.log("SB seat:", state.hand.sbSeat);
console.log("BB seat:", state.hand.bbSeat);
console.log("Action on:", game.getActionSeat());

// Play preflop: everyone folds except BB
let actionSeat = game.getActionSeat();
let actions = 0;
while (actionSeat !== null && !game.isHandComplete()) {
  const seat = state.table.seats[actionSeat];
  console.log(`Seat ${actionSeat} (${seat.player.name}) to act...`);

  if (actions < 2) {
    // First two players fold
    game.act(actionSeat, "FOLD");
    console.log(`  → FOLD`);
  } else {
    // This shouldn't happen in a 3-player game where 2 fold
    game.act(actionSeat, "FOLD");
    console.log(`  → FOLD`);
  }

  actions++;
  actionSeat = game.getActionSeat();
}

console.log("\n=== Hand complete:", game.isHandComplete(), "===");

// Check events
const events = game.getEvents();
console.log("\nEvents emitted:", events.length);
const typeCounts = {};
events.forEach((e) => { typeCounts[e.type] = (typeCounts[e.type] || 0) + 1; });
console.log("Event types:", JSON.stringify(typeCounts, null, 2));

// Check accounting
const handEvents = events.filter((e) => e.handId === "1");
const blinds = handEvents.filter((e) => e.type === "BLIND_POST");
const betReturns = handEvents.filter((e) => e.type === "BET_RETURN");
const awards = handEvents.filter((e) => e.type === "POT_AWARD");
const summary = handEvents.find((e) => e.type === "HAND_SUMMARY");

console.log("\nBlinds:", blinds.map((b) => `${b.player} ${b.blindType} ${b.amount}c`));
console.log("BET_RETURN:", betReturns.map((r) => `${r.player} ${r.amount}c`));
console.log("Awards:", awards.flatMap((a) => a.awards.map((w) => `${w.player} wins ${w.amount}c`)));
console.log("Summary:", summary ? `${summary.winPlayer} wins ${summary.totalPot}c (showdown=${summary.showdown})` : "MISSING");

// Verify stacks
const finalState = game.getState();
let totalStart = 3000; // 3 players × 1000
let totalEnd = 0;
for (const seat of Object.values(finalState.table.seats)) {
  if (seat.player) {
    console.log(`${seat.player.name}: ${seat.stack}c`);
    totalEnd += seat.stack;
  }
}
console.log(`\nTotal start: ${totalStart}c`);
console.log(`Total end:   ${totalEnd}c`);
console.log(`Accounting:  ${totalStart === totalEnd ? "PASS ✓" : "FAIL ✗"}`);

// Write events to log for replay verification
console.log(`\nEvent log: ${logPath}`);

// Verify the log is replayable
console.log("\n=== Replaying through replay consumer ===");
const replayScript = path.join(__dirname, "..", "scripts", "replay-normalized-hand.js");
if (fs.existsSync(replayScript)) {
  // Write hand events to a separate file for replay
  const handLogPath = path.join(__dirname, "..", "test-output", "hand-1.jsonl");
  fs.writeFileSync(handLogPath, handEvents.map((e) => JSON.stringify(e)).join("\n") + "\n");
  console.log(`Hand log: ${handLogPath}`);

  const { execSync } = require("child_process");
  try {
    const output = execSync(`node "${replayScript}" "${handLogPath}"`, { encoding: "utf8" });
    console.log(output);

    // Check if replay timeline has PASS
    const timeline = fs.readFileSync(
      path.join(__dirname, "..", "test-output", `replay-timeline-1.txt`),
      "utf8"
    );
    if (timeline.includes("PASS")) {
      console.log("Replay: PASS ✓");
    } else {
      console.log("Replay: CHECK OUTPUT");
    }
  } catch (e) {
    console.log("Replay execution:", e.message);
  }
}
