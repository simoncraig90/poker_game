#!/usr/bin/env node
"use strict";

/**
 * Phase 8 — Showdown Recovery Tests
 *
 * Proves that mid-showdown crashes (at any point in the settlement event
 * sequence) are correctly voided on recovery, with stacks restored to
 * pre-hand values and no partial settlement corruption.
 *
 * Crash points tested:
 *   1. After SHOWDOWN_REVEAL, before any POT_AWARD
 *   2. After first POT_AWARD in a multi-pot hand, before second
 *   3. After HAND_SUMMARY, before HAND_END
 *   4. Clean recovery of a completed showdown hand (no void needed)
 */

const path = require("path");
const fs = require("fs");
const { Session } = require("../src/api/session");
const { SessionStorage } = require("../src/api/storage");
const { reconstructState } = require("../src/api/reconstruct");
const { CMD, command } = require("../src/api/commands");

const testDir = path.join(__dirname, "..", "test-output", "showdown-recovery-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;

function check(label, cond) {
  checks++;
  if (cond) { passed++; } else { failed++; console.log(`  FAIL: ${label}`); }
}

const TABLE_CONFIG = {
  tableId: "sr-t", tableName: "ShowdownRecovery", maxSeats: 6,
  sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000,
};

let seed = 42;
function rng() { seed = (seed * 1664525 + 1013904223) & 0x7fffffff; return seed / 0x7fffffff; }

/**
 * Play a hand to showdown by having all players call/check through all streets.
 * Uses legalActions from state to pick the correct action.
 */
function playShowdownHand(session) {
  session.dispatch(command(CMD.START_HAND));
  let safety = 0;
  while (safety++ < 200) {
    const st = session.getState();
    if (!st.hand || st.hand.phase === "COMPLETE") break;
    const seat = st.hand.actionSeat;
    if (seat == null) break;
    const legal = st.hand.legalActions;
    if (!legal) break;
    if (legal.actions.includes("CALL")) {
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "CALL" }));
    } else if (legal.actions.includes("CHECK")) {
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "CHECK" }));
    } else {
      // Shouldn't happen, but fold as last resort
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "FOLD" }));
    }
  }
}

/**
 * Play a hand to showdown with a side pot: seat 0 (short stack) goes all-in,
 * others call and then bet on the flop to create a side pot.
 */
function playShowdownWithSidePot(session) {
  session.dispatch(command(CMD.START_HAND));
  let flopBetDone = false;
  let safety = 0;
  while (safety++ < 200) {
    const st = session.getState();
    if (!st.hand || st.hand.phase === "COMPLETE") break;
    const seat = st.hand.actionSeat;
    if (seat == null) break;

    const phase = st.hand.phase;
    const seatState = st.seats[seat];
    const legal = st.hand.legalActions;
    if (!legal) break;

    // Seat 0 goes all-in on preflop
    if (seat === 0 && seatState.stack > 0 && !seatState.allIn && phase === "PREFLOP") {
      if (legal.actions.includes("RAISE")) {
        session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "RAISE", amount: seatState.stack + seatState.bet }));
        continue;
      }
    }

    // On flop, seat 1 bets to create side pot
    if (phase === "FLOP" && seat === 1 && !flopBetDone && legal.actions.includes("BET")) {
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "BET", amount: 50 }));
      flopBetDone = true;
      continue;
    }

    // Default: call or check (use legal actions)
    if (legal.actions.includes("CALL")) {
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "CALL" }));
    } else if (legal.actions.includes("CHECK")) {
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "CHECK" }));
    } else {
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "FOLD" }));
    }
  }
}

/**
 * Truncate the event log at the last occurrence of a specific event type.
 * Simulates a crash immediately after that event was written.
 * Returns the number of lines kept.
 */
function truncateAfter(eventsPath, eventType) {
  const lines = fs.readFileSync(eventsPath, "utf8").trim().split("\n");
  let cutAfter = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    const e = JSON.parse(lines[i]);
    if (e.type === eventType) { cutAfter = i; break; }
  }
  if (cutAfter < 0) throw new Error(`Event ${eventType} not found in log`);
  const kept = lines.slice(0, cutAfter + 1);
  fs.writeFileSync(eventsPath, kept.join("\n") + "\n");
  return kept.length;
}

/**
 * Truncate after the Nth occurrence of an event type (1-based).
 */
function truncateAfterNth(eventsPath, eventType, n) {
  const lines = fs.readFileSync(eventsPath, "utf8").trim().split("\n");
  let count = 0;
  let cutAfter = -1;
  for (let i = 0; i < lines.length; i++) {
    const e = JSON.parse(lines[i]);
    if (e.type === eventType) {
      count++;
      if (count === n) { cutAfter = i; break; }
    }
  }
  if (cutAfter < 0) throw new Error(`Event ${eventType} occurrence ${n} not found`);
  const kept = lines.slice(0, cutAfter + 1);
  fs.writeFileSync(eventsPath, kept.join("\n") + "\n");
  return kept.length;
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 1: Crash After SHOWDOWN_REVEAL (Before Any POT_AWARD)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 1: Crash After SHOWDOWN_REVEAL ===");
{
  seed = 42;
  const storage = new SessionStorage(path.join(testDir, "t1"));
  const sessionId = "t1-session";
  const info = storage.create(sessionId, TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId, logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));

  // Play 1 complete fold-out hand to establish baseline
  s1.dispatch(command(CMD.START_HAND));
  const st1 = s1.getState();
  s1.dispatch(command(CMD.PLAYER_ACTION, { seat: st1.hand.actionSeat, action: "FOLD" }));

  // Record pre-hand stacks
  const preStacks = {};
  Object.values(s1.getState().seats).filter(s => s.player).forEach(s => { preStacks[s.seat] = s.stack; });

  // Play showdown hand to completion (so events exist in log)
  playShowdownHand(s1);

  // Verify the showdown completed
  const allEvents = s1.getEventLog();
  const hasReveal = allEvents.some(e => e.type === "SHOWDOWN_REVEAL");
  check("T1: showdown hand played", hasReveal);

  // Now truncate the log right after SHOWDOWN_REVEAL (remove POT_AWARD onwards)
  truncateAfter(info.eventsPath, "SHOWDOWN_REVEAL");

  // Recover
  const s2 = Session.load(TABLE_CONFIG, sessionId, info.eventsPath);
  const postSt = s2.getState();

  check("T1: stacks restored seat 0", postSt.seats[0].stack === preStacks[0]);
  check("T1: stacks restored seat 1", postSt.seats[1].stack === preStacks[1]);
  check("T1: no active hand", !postSt.hand || postSt.hand.phase === "COMPLETE");

  // Void event exists
  const voidEvent = s2.getEventLog().find(e => e.void === true);
  check("T1: void HAND_END exists", voidEvent != null);
  check("T1: void reason correct", voidEvent && voidEvent.voidReason === "mid-hand recovery");

  // Can play next hand
  seed = 200;
  playShowdownHand(s2);
  check("T1: next hand works", s2.getState().handsPlayed >= 2);

  // Stack conservation
  let total = 0;
  Object.values(s2.getState().seats).forEach(s => { total += s.stack; });
  check("T1: stack conservation (1000)", total === 1000);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 2: Crash After First POT_AWARD in Multi-Pot Hand
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 2: Crash After First POT_AWARD (Multi-Pot) ===");
{
  seed = 77;
  const storage = new SessionStorage(path.join(testDir, "t2"));
  const sessionId = "t2-session";
  const info = storage.create(sessionId, TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId, logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 100 }));  // short
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 2, name: "Charlie", buyIn: 500 }));

  // Record pre-hand stacks
  const preStacks = {};
  Object.values(s1.getState().seats).filter(s => s.player).forEach(s => { preStacks[s.seat] = s.stack; });

  // Play a showdown with side pot
  playShowdownWithSidePot(s1);

  // Verify multi-pot happened
  const allEvents = s1.getEventLog();
  const potAwards = allEvents.filter(e => e.type === "POT_AWARD");
  check("T2: multi-pot hand played", potAwards.length >= 2);

  // Truncate after first POT_AWARD (second pot award + summary + result + end are lost)
  // Count total POT_AWARDs to find where the first one is globally
  const lines = fs.readFileSync(info.eventsPath, "utf8").trim().split("\n");
  let firstPotAwardLine = -1;
  for (let i = 0; i < lines.length; i++) {
    const e = JSON.parse(lines[i]);
    if (e.type === "POT_AWARD" && e.handId === potAwards[potAwards.length - 1].handId) {
      firstPotAwardLine = i;
      break;
    }
  }
  check("T2: found first POT_AWARD", firstPotAwardLine >= 0);

  // Keep up to and including first POT_AWARD of last hand
  const kept = lines.slice(0, firstPotAwardLine + 1);
  fs.writeFileSync(info.eventsPath, kept.join("\n") + "\n");

  // Recover
  const s2 = Session.load(TABLE_CONFIG, sessionId, info.eventsPath);
  const postSt = s2.getState();

  // Stacks must be pre-hand values (the partial POT_AWARD is discarded by void)
  check("T2: stacks restored seat 0", postSt.seats[0].stack === preStacks[0]);
  check("T2: stacks restored seat 1", postSt.seats[1].stack === preStacks[1]);
  check("T2: stacks restored seat 2", postSt.seats[2].stack === preStacks[2]);

  // Void event
  const voidEvent = s2.getEventLog().find(e => e.void === true);
  check("T2: void HAND_END exists", voidEvent != null);

  // Can play next hand
  seed = 300;
  playShowdownHand(s2);
  check("T2: next hand works", s2.getState().handsPlayed >= 1);

  // Stack conservation
  let total = 0;
  Object.values(s2.getState().seats).forEach(s => { total += s.stack; });
  check("T2: stack conservation (1100)", total === 1100);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 3: Crash After HAND_SUMMARY (Before HAND_END)
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 3: Crash After HAND_SUMMARY ===");
{
  seed = 55;
  const storage = new SessionStorage(path.join(testDir, "t3"));
  const sessionId = "t3-session";
  const info = storage.create(sessionId, TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId, logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));

  // Record pre-hand stacks
  const preStacks = {};
  Object.values(s1.getState().seats).filter(s => s.player).forEach(s => { preStacks[s.seat] = s.stack; });

  // Play showdown to completion
  playShowdownHand(s1);

  // Truncate after HAND_SUMMARY (HAND_RESULT and HAND_END are lost)
  truncateAfter(info.eventsPath, "HAND_SUMMARY");

  // Recover
  const s2 = Session.load(TABLE_CONFIG, sessionId, info.eventsPath);
  const postSt = s2.getState();

  // Even though POT_AWARD was applied (it's before HAND_SUMMARY in the log),
  // the void logic restores to HAND_START stacks because there's no HAND_END.
  check("T3: stacks restored seat 0", postSt.seats[0].stack === preStacks[0]);
  check("T3: stacks restored seat 1", postSt.seats[1].stack === preStacks[1]);
  check("T3: no active hand", !postSt.hand || postSt.hand.phase === "COMPLETE");

  // Void event
  const voidEvent = s2.getEventLog().find(e => e.void === true);
  check("T3: void HAND_END exists", voidEvent != null);

  // Can play next hand
  seed = 400;
  playShowdownHand(s2);

  // Stack conservation
  let total = 0;
  Object.values(s2.getState().seats).forEach(s => { total += s.stack; });
  check("T3: stack conservation (1000)", total === 1000);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 4: Clean Recovery After Completed Showdown
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 4: Clean Recovery (Completed Showdown) ===");
{
  seed = 42;
  const storage = new SessionStorage(path.join(testDir, "t4"));
  const sessionId = "t4-session";
  const info = storage.create(sessionId, TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId, logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));

  // Play 2 showdown hands to completion
  playShowdownHand(s1);
  playShowdownHand(s1);

  const preSt = s1.getState();
  const preCount = s1.getEventLog().length;

  // Recover (no crash, no truncation)
  const s2 = Session.load(TABLE_CONFIG, sessionId, info.eventsPath);
  const postSt = s2.getState();

  check("T4: stacks match seat 0", preSt.seats[0].stack === postSt.seats[0].stack);
  check("T4: stacks match seat 1", preSt.seats[1].stack === postSt.seats[1].stack);
  check("T4: handsPlayed match", preSt.handsPlayed === postSt.handsPlayed);
  check("T4: button match", preSt.button === postSt.button);
  check("T4: no void event", !s2.getEventLog().some(e => e.void === true));
  check("T4: event count match", s2.getEventLog().length === preCount);

  // Can play hand 3
  playShowdownHand(s2);
  check("T4: hand 3 works", s2.getState().handsPlayed === 3);

  let total = 0;
  Object.values(s2.getState().seats).forEach(s => { total += s.stack; });
  check("T4: stack conservation (1000)", total === 1000);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 5: Reconstruct After Recovery Matches Live State
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 5: Reconstruct After Recovery ===");
{
  seed = 99;
  const storage = new SessionStorage(path.join(testDir, "t5"));
  const sessionId = "t5-session";
  const info = storage.create(sessionId, TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId, logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));

  // Play 1 complete showdown, then crash mid-showdown on hand 2
  playShowdownHand(s1);
  playShowdownHand(s1);
  truncateAfter(info.eventsPath, "SHOWDOWN_REVEAL");

  // Recover
  const s2 = Session.load(TABLE_CONFIG, sessionId, info.eventsPath);

  // Play a clean hand after recovery
  seed = 500;
  playShowdownHand(s2);

  // Reconstruct from final event log
  const events = s2.getEventLog();
  const recon = reconstructState(events);
  const live = s2.getState();

  check("T5: recon stacks match seat 0", recon.seats[0].stack === live.seats[0].stack);
  check("T5: recon stacks match seat 1", recon.seats[1].stack === live.seats[1].stack);
  check("T5: recon handsPlayed match", recon.handsPlayed === live.handsPlayed);
  check("T5: recon button match", recon.button === live.button);
  check("T5: recon hand cleared", recon.hand === null);

  let total = 0;
  Object.values(s2.getState().seats).forEach(s => { total += s.stack; });
  check("T5: stack conservation (1000)", total === 1000);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 6: Partial POT_AWARD Corruption Check
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 6: Partial POT_AWARD Corruption Check ===");
{
  // This test specifically verifies that partial POT_AWARDs in the log
  // do NOT corrupt stacks on recovery. The void restores from HAND_START.
  seed = 77;
  const storage = new SessionStorage(path.join(testDir, "t6"));
  const sessionId = "t6-session";
  const info = storage.create(sessionId, TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId, logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 100 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 2, name: "Charlie", buyIn: 500 }));

  const preStacks = {};
  Object.values(s1.getState().seats).filter(s => s.player).forEach(s => { preStacks[s.seat] = s.stack; });

  // Play multi-pot showdown
  playShowdownWithSidePot(s1);

  // Verify it was multi-pot
  const allEvents = s1.getEventLog();
  const potAwards = allEvents.filter(e => e.type === "POT_AWARD");
  if (potAwards.length < 2) {
    console.log("  (skipping T6 — hand didn't produce multi-pot, adjusting expectations)");
    // Still test single-pot crash
    truncateAfter(info.eventsPath, "POT_AWARD");
  } else {
    // Truncate after first POT_AWARD specifically
    const lastHandId = potAwards[potAwards.length - 1].handId;
    const lines = fs.readFileSync(info.eventsPath, "utf8").trim().split("\n");
    let firstPAOfLastHand = -1;
    for (let i = 0; i < lines.length; i++) {
      const e = JSON.parse(lines[i]);
      if (e.type === "POT_AWARD" && e.handId === lastHandId) {
        firstPAOfLastHand = i;
        break;
      }
    }
    const kept = lines.slice(0, firstPAOfLastHand + 1);
    fs.writeFileSync(info.eventsPath, kept.join("\n") + "\n");
  }

  // Key check: reconstructState would show corrupted stacks (partial award applied)
  // But Session.load's void logic should override with HAND_START stacks
  const rawEvents = fs.readFileSync(info.eventsPath, "utf8").trim().split("\n").map(l => JSON.parse(l));
  const rawRecon = reconstructState(rawEvents);

  // The raw reconstruct WILL have wrong stacks because POT_AWARD was partially applied
  // (this is expected — it's what the void is protecting against)

  // Now recover properly
  const s2 = Session.load(TABLE_CONFIG, sessionId, info.eventsPath);
  const postSt = s2.getState();

  check("T6: stacks restored seat 0 (not corrupted)", postSt.seats[0].stack === preStacks[0]);
  check("T6: stacks restored seat 1 (not corrupted)", postSt.seats[1].stack === preStacks[1]);
  check("T6: stacks restored seat 2 (not corrupted)", postSt.seats[2].stack === preStacks[2]);

  let total = 0;
  Object.values(s2.getState().seats).forEach(s => { total += s.stack; });
  check("T6: stack conservation (1100)", total === 1100);

  // The void overrides the partial reconstruct
  const voidEvent = s2.getEventLog().find(e => e.void === true);
  check("T6: void exists", voidEvent != null);
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n*** SHOWDOWN RECOVERY: ${passed}/${checks} passed, ${failed} failed ***`);
if (failed > 0) process.exit(1);
