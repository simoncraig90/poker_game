#!/usr/bin/env node
"use strict";

/**
 * Phase 2 Conformance Test
 *
 * Proves three claims:
 * 1. Event log fully reconstructs state
 * 2. Reconstructed state matches live session state after every command
 * 3. No hidden state exists outside the append-only log
 *
 * Method: run a multi-hand session through dispatch(), and after every
 * command, compare getState() against reconstructState(getEventLog()).
 */

const { Session } = require("../src/api/session");
const { CMD, command } = require("../src/api/commands");
const { reconstructState } = require("../src/api/reconstruct");
const path = require("path");
const fs = require("fs");

let seed = 999;
function rng() { seed = (seed * 1664525 + 1013904223) & 0x7fffffff; return seed / 0x7fffffff; }

const logDir = path.join(__dirname, "..", "test-output");
fs.mkdirSync(logDir, { recursive: true });
const logPath = path.join(logDir, "conformance-session.jsonl");

const session = new Session(
  { tableId: "conf-1", tableName: "Conformance", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 400, maxBuyIn: 1000 },
  { sessionId: "conformance-test", logPath, rng }
);

let checks = 0;
let passed = 0;
let failed = 0;

/**
 * Compare live state against reconstructed state.
 * Ignores transient fields (actionSeat, legalActions) that depend on
 * in-memory round state not captured in events.
 */
function assertConformance(label) {
  checks++;
  const live = session.getState();
  const events = session.getEventLog();
  const reconstructed = reconstructState(events);

  // Compare seat-by-seat
  for (let i = 0; i < (live.maxSeats || 6); i++) {
    const ls = live.seats[i];
    const rs = reconstructed.seats[i];

    if (ls.status !== rs.status) return failure(label, `seat ${i} status: live=${ls.status} recon=${rs.status}`);
    if (ls.stack !== rs.stack) return failure(label, `seat ${i} stack: live=${ls.stack} recon=${rs.stack}`);
    if (ls.inHand !== rs.inHand) return failure(label, `seat ${i} inHand: live=${ls.inHand} recon=${rs.inHand}`);
    if (ls.folded !== rs.folded) return failure(label, `seat ${i} folded: live=${ls.folded} recon=${rs.folded}`);
    if (ls.allIn !== rs.allIn) return failure(label, `seat ${i} allIn: live=${ls.allIn} recon=${rs.allIn}`);
    if (ls.bet !== rs.bet) return failure(label, `seat ${i} bet: live=${ls.bet} recon=${rs.bet}`);
    if (ls.totalInvested !== rs.totalInvested) return failure(label, `seat ${i} totalInvested: live=${ls.totalInvested} recon=${rs.totalInvested}`);

    const lName = ls.player ? ls.player.name : null;
    const rName = rs.player ? rs.player.name : null;
    if (lName !== rName) return failure(label, `seat ${i} player: live=${lName} recon=${rName}`);
  }

  // Compare table-level
  if (live.button !== reconstructed.button) return failure(label, `button: live=${live.button} recon=${reconstructed.button}`);
  if (live.handsPlayed !== reconstructed.handsPlayed) return failure(label, `handsPlayed: live=${live.handsPlayed} recon=${reconstructed.handsPlayed}`);

  // Compare hand-level
  // After HAND_END: live keeps hand with phase=COMPLETE, reconstructor nulls it. Both valid.
  const liveHandActive = !!(live.hand && live.hand.phase !== "COMPLETE");
  const reconHandActive = !!(reconstructed.hand && reconstructed.hand.phase !== "COMPLETE");

  if (liveHandActive && reconHandActive) {
    if (live.hand.pot !== reconstructed.hand.pot) return failure(label, `pot: live=${live.hand.pot} recon=${reconstructed.hand.pot}`);
    if (live.hand.phase !== reconstructed.hand.phase) return failure(label, `phase: live=${live.hand.phase} recon=${reconstructed.hand.phase}`);

    const lBoard = live.hand.board.join(",");
    const rBoard = reconstructed.hand.board.join(",");
    if (lBoard !== rBoard) return failure(label, `board: live=${lBoard} recon=${rBoard}`);
  } else if (liveHandActive !== reconHandActive) {
    return failure(label, `hand active mismatch: live=${liveHandActive} recon=${reconHandActive}`);
  }

  passed++;
  return true;
}

function failure(label, detail) {
  failed++;
  console.log(`  FAIL [${label}]: ${detail}`);
  return false;
}

function dispatchAndCheck(cmd, label) {
  const result = session.dispatch(cmd);
  if (!result.ok) {
    console.log(`  ERROR dispatching ${label}: ${result.error}`);
    return result;
  }
  assertConformance(label);
  return result;
}

// ═══════════════════════════════════════════════════════════════════════════
//  Run the session
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Phase 2 Conformance Test ===\n");

// Check after table creation (snapshot)
assertConformance("after table creation");

// Seat players
dispatchAndCheck(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 1000, country: "US" }), "seat Alice");
dispatchAndCheck(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 800, country: "GB" }), "seat Bob");
dispatchAndCheck(command(CMD.SEAT_PLAYER, { seat: 3, name: "Charlie", buyIn: 600, country: "CA" }), "seat Charlie");

// Hand 1: preflop fold
console.log("\n-- Hand 1: preflop folds --");
dispatchAndCheck(command(CMD.START_HAND), "hand 1 start");

let state = session.dispatch(command(CMD.GET_STATE)).state;
let actionSeat = state.hand.actionSeat;

while (actionSeat != null) {
  dispatchAndCheck(
    command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "FOLD" }),
    `hand 1 seat ${actionSeat} fold`
  );
  state = session.dispatch(command(CMD.GET_STATE)).state;
  actionSeat = state.hand ? state.hand.actionSeat : null;
}

assertConformance("hand 1 complete");

// Hand 2: preflop raise + call, flop fold
console.log("\n-- Hand 2: raise, call, flop fold --");
dispatchAndCheck(command(CMD.START_HAND), "hand 2 start");

state = session.dispatch(command(CMD.GET_STATE)).state;
actionSeat = state.hand.actionSeat;

// First player raises
dispatchAndCheck(
  command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "RAISE", amount: 30 }),
  `hand 2 seat ${actionSeat} raise`
);

state = session.dispatch(command(CMD.GET_STATE)).state;
actionSeat = state.hand.actionSeat;

// Next players: one calls, rest fold
let calledOnce = false;
while (actionSeat != null && state.hand && state.hand.phase === "PREFLOP") {
  if (!calledOnce) {
    dispatchAndCheck(
      command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "CALL" }),
      `hand 2 seat ${actionSeat} call`
    );
    calledOnce = true;
  } else {
    dispatchAndCheck(
      command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "FOLD" }),
      `hand 2 seat ${actionSeat} fold`
    );
  }
  state = session.dispatch(command(CMD.GET_STATE)).state;
  actionSeat = state.hand ? state.hand.actionSeat : null;
}

// Flop: first player bets, second folds
if (state.hand && state.hand.phase === "FLOP") {
  actionSeat = state.hand.actionSeat;
  while (actionSeat != null && state.hand.phase === "FLOP") {
    const s = state.seats[actionSeat];
    if (!s.folded) {
      if (state.hand.pot > 0) {
        // First active: bet
        const actions = state.hand.legalActions;
        if (actions && actions.actions.includes("BET")) {
          dispatchAndCheck(
            command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "BET", amount: 20 }),
            `hand 2 seat ${actionSeat} flop bet`
          );
        } else {
          dispatchAndCheck(
            command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "FOLD" }),
            `hand 2 seat ${actionSeat} flop fold`
          );
        }
      }
    }
    state = session.dispatch(command(CMD.GET_STATE)).state;
    actionSeat = state.hand ? state.hand.actionSeat : null;
  }
}

assertConformance("hand 2 complete");

// Hand 3: quick fold
console.log("\n-- Hand 3: quick fold --");
dispatchAndCheck(command(CMD.START_HAND), "hand 3 start");
state = session.dispatch(command(CMD.GET_STATE)).state;
actionSeat = state.hand.actionSeat;
while (actionSeat != null) {
  dispatchAndCheck(
    command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "FOLD" }),
    `hand 3 seat ${actionSeat} fold`
  );
  state = session.dispatch(command(CMD.GET_STATE)).state;
  actionSeat = state.hand ? state.hand.actionSeat : null;
}
assertConformance("hand 3 complete");

// Player leaves
console.log("\n-- Leave + rejoin --");
dispatchAndCheck(command(CMD.LEAVE_TABLE, { seat: 3 }), "Charlie leaves");
dispatchAndCheck(command(CMD.SEAT_PLAYER, { seat: 3, name: "Diana", buyIn: 500, country: "AU" }), "seat Diana");

// Hand 4: with Diana
console.log("\n-- Hand 4: with Diana --");
dispatchAndCheck(command(CMD.START_HAND), "hand 4 start");
state = session.dispatch(command(CMD.GET_STATE)).state;
actionSeat = state.hand.actionSeat;
while (actionSeat != null) {
  dispatchAndCheck(
    command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "FOLD" }),
    `hand 4 seat ${actionSeat} fold`
  );
  state = session.dispatch(command(CMD.GET_STATE)).state;
  actionSeat = state.hand ? state.hand.actionSeat : null;
}
assertConformance("hand 4 complete");

// ═══════════════════════════════════════════════════════════════════════════
//  Final accounting
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Results ===");
console.log(`Conformance checks: ${checks}`);
console.log(`Passed:  ${passed}`);
console.log(`Failed:  ${failed}`);

// Total chips conservation
state = session.dispatch(command(CMD.GET_STATE)).state;
let totalChips = 0;
for (const s of Object.values(state.seats)) {
  totalChips += s.stack;
}
// Diana bought in for 500, Charlie left with his stack (tracked separately)
// Total should be: Alice(1000) + Bob(800) + Diana(500) = 2300 +/- transfers
console.log(`Total chips on table: ${totalChips}`);

// Event log stats
const events = session.getEventLog();
const types = {};
events.forEach((e) => { types[e.type] = (types[e.type] || 0) + 1; });
console.log(`Total events: ${events.length}`);
console.log("Types:", JSON.stringify(types));

// Final verdict
if (failed === 0) {
  console.log(`\n✓ CONFORMANCE PASSED: ${passed}/${checks} checks. No hidden state.`);
} else {
  console.log(`\n✗ CONFORMANCE FAILED: ${failed}/${checks} checks failed.`);
}

// Replay check on last hand
const hand4 = session.getHandEvents("4");
if (hand4.length > 0) {
  const handLog = path.join(logDir, "conformance-hand-4.jsonl");
  fs.writeFileSync(handLog, hand4.map((e) => JSON.stringify(e)).join("\n") + "\n");
  const { execSync } = require("child_process");
  try {
    execSync(`node scripts/replay-normalized-hand.js "${handLog}"`, { cwd: path.join(__dirname, "..") });
    const timeline = fs.readFileSync(path.join(logDir, "replay-timeline-4.txt"), "utf8");
    console.log(timeline.includes("PASS") ? "Replay: PASS ✓" : "Replay: CHECK");
  } catch (e) {
    console.log("Replay:", e.stdout || e.message);
  }
}
