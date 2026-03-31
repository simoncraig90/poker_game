#!/usr/bin/env node
"use strict";

/**
 * Phase 8 — Slice 3B: Showdown Integration Tests
 *
 * Tests the full hand lifecycle through the orchestrator when hands reach
 * showdown (2+ players at river end, and all-in run-out).
 * Verifies event emission, stack updates, accounting, and reconstruct.
 */

const path = require("path");
const fs = require("fs");
const { createTable, sitDown, resetHandState } = require("../src/engine/table");
const { HandOrchestrator } = require("../src/engine/orchestrator");
const { EventLog } = require("../src/engine/event-log");
const { reconstructState } = require("../src/api/reconstruct");
const ev = require("../src/engine/events");

const testDir = path.join(__dirname, "..", "test-output", "showdown-int-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;

function check(label, cond) {
  checks++;
  if (cond) { passed++; } else { failed++; console.log(`  FAIL: ${label}`); }
}

// Deterministic RNG — produces known card sequences
function makeRng(seed) {
  let s = seed;
  return function() {
    s = (s * 1664525 + 1013904223) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

function setupTable(id, opts = {}) {
  const logPath = path.join(testDir, `${id}.jsonl`);
  const table = createTable({
    tableId: id, tableName: "Test", maxSeats: 6,
    sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000,
  });
  const log = new EventLog(logPath);
  log.append(ev.tableSnapshot("test", table));
  return { table, log, logPath };
}

// Play through a hand until it completes by having specified seats call/check
// to reach showdown. Returns the events.
function playToShowdown(orch, seats, actionOverrides = {}) {
  orch.startHand();
  let safety = 0;
  while (!orch.isHandComplete()) {
    if (safety++ > 200) throw new Error("Infinite loop in playToShowdown");
    const actionSeat = orch.getActionSeat();
    if (actionSeat === null) break;
    // Default: call if there's a bet, otherwise check
    const override = actionOverrides[`${orch.table.hand.phase}:${actionSeat}`];
    if (override) {
      orch.act(actionSeat, override.action, override.amount);
    } else {
      // Try call, if not valid try check
      try {
        orch.act(actionSeat, "CALL", 0);
      } catch {
        orch.act(actionSeat, "CHECK", 0);
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 1: 2-Player Showdown — Call Down to River
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 1: 2-Player Showdown ===");
{
  const { table, log } = setupTable("t1");
  sitDown(table, 0, "Alice", 500, "US");
  sitDown(table, 1, "Bob", 500, "US");
  log.append(ev.seatPlayer("test", 0, "Alice", 500, "US"));
  log.append(ev.seatPlayer("test", 1, "Bob", 500, "US"));

  const orch = new HandOrchestrator(table, log, "test", makeRng(42));
  playToShowdown(orch, [0, 1]);

  check("T1: hand complete", orch.isHandComplete());
  check("T1: phase COMPLETE", table.hand.phase === "COMPLETE");
  check("T1: showdown flag true", table.hand.showdown === true);

  // Accounting
  const totalStacks = Object.values(table.seats)
    .filter((s) => s.player)
    .reduce((sum, s) => sum + s.stack, 0);
  check("T1: accounting (1000 total)", totalStacks === 1000);

  // Events should include SHOWDOWN_REVEAL
  const events = log.getEvents();
  const reveals = events.filter((e) => e.type === "SHOWDOWN_REVEAL");
  check("T1: has SHOWDOWN_REVEAL", reveals.length === 1);
  check("T1: reveal has 2 players", reveals[0].reveals.length === 2);
  check("T1: each reveal has handName", reveals[0].reveals.every((r) => typeof r.handName === "string"));
  check("T1: each reveal has bestFive", reveals[0].reveals.every((r) => r.bestFive.length === 5));

  // HAND_SUMMARY should have handRank populated
  const summary = events.find((e) => e.type === "HAND_SUMMARY");
  check("T1: summary exists", !!summary);
  check("T1: summary showdown=true", summary.showdown === true);
  check("T1: summary handRank populated", typeof summary.handRank === "string" && summary.handRank.length > 0);
  check("T1: summary winCards populated", Array.isArray(summary.winCards) && summary.winCards.length === 5);

  // POT_AWARD
  const potAwards = events.filter((e) => e.type === "POT_AWARD");
  check("T1: at least 1 POT_AWARD", potAwards.length >= 1);

  // Reconstruct matches
  const reconstructed = reconstructState(events);
  check("T1: reconstruct stacks match",
    Object.values(reconstructed.seats).filter((s) => s.player).every((s) => {
      return s.stack === table.seats[s.seat].stack;
    }));
  check("T1: reconstruct hand null (complete)", reconstructed.hand === null);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 2: All-In Preflop Run-Out
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 2: All-In Preflop Run-Out ===");
{
  const { table, log } = setupTable("t2");
  sitDown(table, 0, "Alice", 200, "US");
  sitDown(table, 1, "Bob", 200, "US");
  log.append(ev.seatPlayer("test", 0, "Alice", 200, "US"));
  log.append(ev.seatPlayer("test", 1, "Bob", 200, "US"));

  const orch = new HandOrchestrator(table, log, "test", makeRng(99));
  orch.startHand();

  // First to act goes all-in via RAISE
  const actionSeat = orch.getActionSeat();
  orch.act(actionSeat, "RAISE", 200); // all-in

  // Other player calls all-in
  const nextSeat = orch.getActionSeat();
  if (nextSeat !== null) {
    orch.act(nextSeat, "CALL", 0);
  }

  check("T2: hand complete", orch.isHandComplete());

  const events = log.getEvents();
  // Board should be fully dealt (5 cards)
  const communityEvents = events.filter((e) => e.type === "DEAL_COMMUNITY");
  const finalBoard = communityEvents.length > 0 ? communityEvents[communityEvents.length - 1].board : [];
  check("T2: full board dealt (5 cards)", finalBoard.length === 5);

  // Showdown happened
  const reveals = events.filter((e) => e.type === "SHOWDOWN_REVEAL");
  check("T2: SHOWDOWN_REVEAL present", reveals.length === 1);

  // Accounting
  const totalStacks = Object.values(table.seats)
    .filter((s) => s.player)
    .reduce((sum, s) => sum + s.stack, 0);
  check("T2: accounting (400 total)", totalStacks === 400);

  // Reconstruct
  const reconstructed = reconstructState(events);
  check("T2: reconstruct stacks match",
    Object.values(reconstructed.seats).filter((s) => s.player).every((s) => {
      return s.stack === table.seats[s.seat].stack;
    }));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 3: 3-Way Showdown with Side Pot
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 3: 3-Way with Side Pot ===");
{
  const { table, log } = setupTable("t3");
  sitDown(table, 0, "Alice", 100, "US");  // short stack
  sitDown(table, 1, "Bob", 500, "US");
  sitDown(table, 2, "Charlie", 500, "US");
  log.append(ev.seatPlayer("test", 0, "Alice", 100, "US"));
  log.append(ev.seatPlayer("test", 1, "Bob", 500, "US"));
  log.append(ev.seatPlayer("test", 2, "Charlie", 500, "US"));

  const orch = new HandOrchestrator(table, log, "test", makeRng(77));
  orch.startHand();

  // Alice goes all-in preflop. Bob and Charlie call.
  // Then on flop, Bob bets to create a side pot. Charlie calls.
  let safety = 0;
  let flopBetDone = false;
  while (!orch.isHandComplete()) {
    if (safety++ > 200) throw new Error("Infinite loop");
    const seat = orch.getActionSeat();
    if (seat === null) break;

    const s = table.seats[seat];
    const phase = table.hand.phase;

    if (seat === 0 && s.stack > 0 && !s.allIn) {
      // Alice goes all-in preflop
      orch.act(seat, "RAISE", s.stack + s.bet);
    } else if (phase === "FLOP" && seat === 1 && !flopBetDone) {
      // Bob bets 50 on flop to create side pot
      orch.act(seat, "BET", 50);
      flopBetDone = true;
    } else {
      try { orch.act(seat, "CALL", 0); } catch { orch.act(seat, "CHECK", 0); }
    }
  }

  check("T3: hand complete", orch.isHandComplete());

  const allEvents = log.getEvents();
  const potAwards = allEvents.filter((e) => e.type === "POT_AWARD");
  check("T3: multiple POT_AWARDs (main + side)", potAwards.length >= 2);

  // Accounting: 100 + 500 + 500 = 1100
  const totalStacks = Object.values(table.seats)
    .filter((s) => s.player)
    .reduce((sum, s) => sum + s.stack, 0);
  check("T3: accounting (1100 total)", totalStacks === 1100);

  // HAND_RESULT per pot
  const handResults = allEvents.filter((e) => e.type === "HAND_RESULT");
  check("T3: HAND_RESULT per pot", handResults.length === potAwards.length);

  // Reconstruct
  const reconstructed = reconstructState(allEvents);
  check("T3: reconstruct stacks match",
    Object.values(reconstructed.seats).filter((s) => s.player).every((s) => {
      return s.stack === table.seats[s.seat].stack;
    }));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 4: Fold-Out Still Works (Regression)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 4: Fold-Out Regression ===");
{
  const { table, log } = setupTable("t4");
  sitDown(table, 0, "Alice", 500, "US");
  sitDown(table, 1, "Bob", 500, "US");
  sitDown(table, 2, "Charlie", 500, "US");
  log.append(ev.seatPlayer("test", 0, "Alice", 500, "US"));
  log.append(ev.seatPlayer("test", 1, "Bob", 500, "US"));
  log.append(ev.seatPlayer("test", 2, "Charlie", 500, "US"));

  const orch = new HandOrchestrator(table, log, "test", makeRng(123));
  orch.startHand();

  // Everyone folds except last player
  let safety = 0;
  while (!orch.isHandComplete()) {
    if (safety++ > 50) throw new Error("Infinite loop");
    const seat = orch.getActionSeat();
    if (seat === null) break;
    orch.act(seat, "FOLD", 0);
  }

  check("T4: hand complete", orch.isHandComplete());
  check("T4: showdown false", table.hand.showdown === false);

  // No SHOWDOWN_REVEAL for fold-outs
  const events = log.getEvents();
  const reveals = events.filter((e) => e.type === "SHOWDOWN_REVEAL");
  check("T4: no SHOWDOWN_REVEAL", reveals.length === 0);

  // Summary has showdown=false
  const summary = events.find((e) => e.type === "HAND_SUMMARY" && e.handId);
  check("T4: summary showdown=false", summary && summary.showdown === false);
  check("T4: summary handRank null", summary && summary.handRank === null);

  // Accounting
  const totalStacks = Object.values(table.seats)
    .filter((s) => s.player)
    .reduce((sum, s) => sum + s.stack, 0);
  check("T4: accounting (1500 total)", totalStacks === 1500);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 5: Consecutive Hands — Mix of Fold-Out and Showdown
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 5: Consecutive Hands ===");
{
  const { table, log } = setupTable("t5");
  sitDown(table, 0, "Alice", 500, "US");
  sitDown(table, 1, "Bob", 500, "US");
  log.append(ev.seatPlayer("test", 0, "Alice", 500, "US"));
  log.append(ev.seatPlayer("test", 1, "Bob", 500, "US"));

  const rng = makeRng(55);
  const orch = new HandOrchestrator(table, log, "test", rng);

  // Hand 1: fold-out
  orch.startHand();
  const seat1 = orch.getActionSeat();
  orch.act(seat1, "FOLD", 0);
  check("T5: hand 1 complete", orch.isHandComplete());

  // Hand 2: showdown (call down)
  playToShowdown(orch, [0, 1]);
  check("T5: hand 2 complete", orch.isHandComplete());

  // Hand 3: fold-out
  orch.startHand();
  const seat3 = orch.getActionSeat();
  orch.act(seat3, "FOLD", 0);
  check("T5: hand 3 complete", orch.isHandComplete());

  // Accounting across all 3 hands
  const totalStacks = Object.values(table.seats)
    .filter((s) => s.player)
    .reduce((sum, s) => sum + s.stack, 0);
  check("T5: accounting (1000 total)", totalStacks === 1000);

  // Reconstruct full event log
  const events = log.getEvents();
  const reconstructed = reconstructState(events);
  check("T5: reconstruct stacks match",
    Object.values(reconstructed.seats).filter((s) => s.player).every((s) => {
      return s.stack === table.seats[s.seat].stack;
    }));
  check("T5: reconstruct handsPlayed 3", reconstructed.handsPlayed === 3);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 6: Event Sequencing
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 6: Event Sequencing ===");
{
  const { table, log } = setupTable("t6");
  sitDown(table, 0, "Alice", 500, "US");
  sitDown(table, 1, "Bob", 500, "US");
  log.append(ev.seatPlayer("test", 0, "Alice", 500, "US"));
  log.append(ev.seatPlayer("test", 1, "Bob", 500, "US"));

  const orch = new HandOrchestrator(table, log, "test", makeRng(42));
  playToShowdown(orch, [0, 1]);

  const events = log.getEvents();
  const handEvents = events.filter((e) => e.handId);
  const types = handEvents.map((e) => e.type);

  // Verify ordering: HAND_START ... SHOWDOWN_REVEAL ... POT_AWARD ... HAND_SUMMARY ... HAND_RESULT ... HAND_END
  const revealIdx = types.indexOf("SHOWDOWN_REVEAL");
  const firstPotAward = types.indexOf("POT_AWARD");
  const summaryIdx = types.indexOf("HAND_SUMMARY");
  const firstResult = types.indexOf("HAND_RESULT");
  const endIdx = types.indexOf("HAND_END");
  const startIdx = types.indexOf("HAND_START");

  check("T6: HAND_START first", startIdx === 0);
  check("T6: SHOWDOWN_REVEAL before POT_AWARD", revealIdx < firstPotAward);
  check("T6: POT_AWARD before HAND_SUMMARY", firstPotAward < summaryIdx);
  check("T6: HAND_SUMMARY before HAND_RESULT", summaryIdx < firstResult);
  check("T6: HAND_RESULT before HAND_END", firstResult < endIdx);
  check("T6: HAND_END is last", endIdx === types.length - 1);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 7: Reconstruct Conformance — No Hidden State
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 7: Reconstruct Conformance ===");
{
  const { table, log } = setupTable("t7");
  sitDown(table, 0, "Alice", 500, "US");
  sitDown(table, 1, "Bob", 500, "US");
  sitDown(table, 2, "Charlie", 500, "US");
  log.append(ev.seatPlayer("test", 0, "Alice", 500, "US"));
  log.append(ev.seatPlayer("test", 1, "Bob", 500, "US"));
  log.append(ev.seatPlayer("test", 2, "Charlie", 500, "US"));

  const orch = new HandOrchestrator(table, log, "test", makeRng(42));

  // Play 3 hands: mix of showdown and fold-out
  for (let i = 0; i < 3; i++) {
    if (i % 2 === 0) {
      // Showdown hand
      playToShowdown(orch, [0, 1, 2]);
    } else {
      // Fold-out hand
      orch.startHand();
      const seat = orch.getActionSeat();
      orch.act(seat, "FOLD", 0);
      // Second fold
      if (!orch.isHandComplete()) {
        const seat2 = orch.getActionSeat();
        if (seat2 !== null) orch.act(seat2, "FOLD", 0);
      }
    }
  }

  const events = log.getEvents();
  const reconstructed = reconstructState(events);

  // Compare stacks
  let stacksMatch = true;
  for (let i = 0; i < 6; i++) {
    const live = table.seats[i];
    const recon = reconstructed.seats[i];
    if (live.player && recon.player) {
      if (live.stack !== recon.stack) {
        stacksMatch = false;
        console.log(`  Stack mismatch seat ${i}: live=${live.stack} recon=${recon.stack}`);
      }
    }
  }
  check("T7: all stacks match after 3 hands", stacksMatch);
  check("T7: handsPlayed 3", reconstructed.handsPlayed === 3);
  check("T7: hand cleared (null)", reconstructed.hand === null);

  // Accounting
  const totalStacks = Object.values(table.seats)
    .filter((s) => s.player)
    .reduce((sum, s) => sum + s.stack, 0);
  check("T7: accounting (1500 total)", totalStacks === 1500);
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n=== Showdown Integration Tests: ${passed}/${checks} passed, ${failed} failed ===`);
if (failed > 0) process.exit(1);
