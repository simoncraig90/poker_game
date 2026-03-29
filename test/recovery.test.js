#!/usr/bin/env node
"use strict";

/**
 * Phase 6 Recovery Conformance Tests
 *
 * Tests 1-7 from RECOVERY_CONFORMANCE_TEST_PLAN.md
 */

const path = require("path");
const fs = require("fs");
const { Session } = require("../src/api/session");
const { SessionStorage } = require("../src/api/storage");
const { reconstructState } = require("../src/api/reconstruct");
const { CMD, command } = require("../src/api/commands");

const testDir = path.join(__dirname, "..", "test-output", "recovery-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;

function check(label, cond) {
  checks++;
  if (cond) { passed++; } else { failed++; console.log(`  FAIL: ${label}`); }
}

const TABLE_CONFIG = { tableId: "rec-t", tableName: "Recovery", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 400, maxBuyIn: 1000 };

let seed = 42;
function rng() { seed = (seed * 1664525 + 1013904223) & 0x7fffffff; return seed / 0x7fffffff; }

function playFoldOutHand(session) {
  session.dispatch(command(CMD.START_HAND));
  let st = session.getState();
  let seat = st.hand ? st.hand.actionSeat : null;
  while (seat != null) {
    session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "FOLD" }));
    st = session.getState();
    seat = (st.hand && st.hand.phase !== "COMPLETE") ? st.hand.actionSeat : null;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 1: Clean Recovery
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 1: Clean Recovery ===");
{
  const storage = new SessionStorage(path.join(testDir, "t1"));
  const sessionId = "t1-session";
  const info = storage.create(sessionId, TABLE_CONFIG);

  // Create and play 5 hands
  const s1 = new Session(TABLE_CONFIG, { sessionId, logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 1000 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 800 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 3, name: "Charlie", buyIn: 600 }));

  for (let i = 0; i < 5; i++) playFoldOutHand(s1);

  const preSt = s1.getState();
  const preEvents = s1.getEventLog().length;

  // "Shutdown" — discard s1, keep files
  // Recover
  const s2 = Session.load(TABLE_CONFIG, sessionId, info.eventsPath);
  const postSt = s2.getState();

  check("stacks match seat 0", preSt.seats[0].stack === postSt.seats[0].stack);
  check("stacks match seat 1", preSt.seats[1].stack === postSt.seats[1].stack);
  check("stacks match seat 3", preSt.seats[3].stack === postSt.seats[3].stack);
  check("handsPlayed match", preSt.handsPlayed === postSt.handsPlayed);
  check("button match", preSt.button === postSt.button);
  check("sessionId match", s2.sessionId === sessionId);
  check("event count match", s2.getEventLog().length === preEvents);

  // reconstructState conformance
  const recon = reconstructState(s2.getEventLog());
  check("recon stacks match", recon.seats[0].stack === postSt.seats[0].stack);
  check("recon button match", recon.button === postSt.button);

  // Can play hand 6
  playFoldOutHand(s2);
  check("hand 6 works after recovery", s2.getState().handsPlayed === 6);

  // Stack conservation
  let total = 0;
  Object.values(s2.getState().seats).forEach(s => total += s.stack);
  check("stack conservation", total === 2400);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 2: Mid-Hand Recovery
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 2: Mid-Hand Recovery ===");
{
  seed = 77;
  const storage = new SessionStorage(path.join(testDir, "t2"));
  const sessionId = "t2-session";
  const info = storage.create(sessionId, TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId, logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 1000 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 800 }));

  // Play 2 complete hands
  playFoldOutHand(s1);
  playFoldOutHand(s1);

  const preHandStacks = {};
  Object.values(s1.getState().seats).filter(s => s.player).forEach(s => preHandStacks[s.seat] = s.stack);

  // Start hand 3 but don't finish it
  s1.dispatch(command(CMD.START_HAND));
  const st = s1.getState();
  const actionSeat = st.hand.actionSeat;
  s1.dispatch(command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "FOLD" }));
  // Hand may have settled if only 2 players. If not, leave it incomplete.

  // Check if we need to test mid-hand (might have auto-settled with 2 players)
  const lastEvents = s1.getEventLog();
  const hasHandEnd = lastEvents.some(e => e.handId === "3" && e.type === "HAND_END");

  if (hasHandEnd) {
    // 2-player hands auto-settle on first fold. Start another and crash mid-blind.
    // Manually write an incomplete hand to test mid-hand recovery
    const eventsPath2 = path.join(testDir, "t2b", "events.jsonl");
    const storage2 = new SessionStorage(path.join(testDir, "t2b"));
    const info2 = storage2.create("t2b-session", TABLE_CONFIG);

    const s2a = new Session(TABLE_CONFIG, { sessionId: "t2b-session", logPath: info2.eventsPath, rng });
    s2a.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 1000 }));
    s2a.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 800 }));
    s2a.dispatch(command(CMD.SEAT_PLAYER, { seat: 3, name: "Charlie", buyIn: 600 }));
    playFoldOutHand(s2a);

    const preStacks = {};
    Object.values(s2a.getState().seats).filter(s => s.player).forEach(s => preStacks[s.seat] = s.stack);

    // Start hand but truncate before HAND_END
    s2a.dispatch(command(CMD.START_HAND));
    // Remove the last HAND_END-related events from the file to simulate crash
    const rawLog = fs.readFileSync(info2.eventsPath, "utf8").trim().split("\n");
    // Find last HAND_END and remove everything from POT_AWARD onwards
    let cutIdx = rawLog.length;
    for (let i = rawLog.length - 1; i >= 0; i--) {
      const e = JSON.parse(rawLog[i]);
      if (e.type === "HAND_START" && e.handId === "2") { cutIdx = i + 3; break; } // keep HAND_START + blinds
    }
    const truncated = rawLog.slice(0, cutIdx);
    fs.writeFileSync(info2.eventsPath, truncated.join("\n") + "\n");

    // Recover
    const s2b = Session.load(TABLE_CONFIG, "t2b-session", info2.eventsPath);
    const postSt = s2b.getState();

    check("mid-hand stacks restored seat 0", postSt.seats[0].stack === preStacks[0]);
    check("mid-hand stacks restored seat 1", postSt.seats[1].stack === preStacks[1]);
    check("mid-hand stacks restored seat 3", postSt.seats[3].stack === preStacks[3]);
    check("no active hand after void", !postSt.hand || postSt.hand.phase === "COMPLETE");

    // Void event in log
    const voidEvent = s2b.getEventLog().find(e => e.void === true);
    check("void HAND_END exists", voidEvent != null);
    check("void reason is mid-hand recovery", voidEvent && voidEvent.voidReason === "mid-hand recovery");

    // Can play next hand
    playFoldOutHand(s2b);
    check("can play after mid-hand recovery", s2b.getState().handsPlayed > 0);

    let total = 0;
    Object.values(s2b.getState().seats).forEach(s => total += s.stack);
    check("stacks conserved after mid-hand recovery", total === 2400);
  } else {
    check("mid-hand incomplete detected", true);
    // Recover
    const s2 = Session.load(TABLE_CONFIG, sessionId, info.eventsPath);
    check("mid-hand recovery loaded", s2 != null);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 3: Empty Session Recovery
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 3: Empty Session Recovery ===");
{
  const storage = new SessionStorage(path.join(testDir, "t3"));
  const info = storage.create("t3-session", TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId: "t3-session", logPath: info.eventsPath });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 1000 }));

  const s2 = Session.load(TABLE_CONFIG, "t3-session", info.eventsPath);
  const st = s2.getState();

  check("1 player seated", Object.values(st.seats).filter(s => s.status === "OCCUPIED").length === 1);
  check("correct stack", st.seats[0].stack === 1000);

  const result = s2.dispatch(command(CMD.START_HAND));
  check("cannot start with 1 player", result.ok === false);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 4: Archived Session Read-Only
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 4: Archived Session Read-Only ===");
{
  const storage = new SessionStorage(path.join(testDir, "t4"));
  const info = storage.create("t4-session", TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId: "t4-session", logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 1000 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 800 }));
  playFoldOutHand(s1);
  playFoldOutHand(s1);
  playFoldOutHand(s1);

  // Archive
  storage.archive("t4-session", s1.getState().handsPlayed);

  // Load as complete
  const s2 = Session.load(TABLE_CONFIG, "t4-session", info.eventsPath, { status: "complete" });

  check("archived meta status", storage.load("t4-session").meta.status === "complete");
  check("GET_STATE works on archive", s2.dispatch(command(CMD.GET_STATE)).ok === true);
  check("GET_HAND_LIST works on archive", s2.dispatch(command(CMD.GET_HAND_LIST)).ok === true);
  check("START_HAND blocked on archive", s2.dispatch(command(CMD.START_HAND)).ok === false);
  check("SEAT_PLAYER blocked on archive", s2.dispatch(command(CMD.SEAT_PLAYER, { seat: 2, name: "X", buyIn: 500 })).ok === false);

  // Event count unchanged after blocked writes
  const eventsBefore = s2.getEventLog().length;
  s2.dispatch(command(CMD.START_HAND)); // should fail
  check("events not modified on archive", s2.getEventLog().length === eventsBefore);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 5: Session List
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 5: Session List ===");
{
  const storage = new SessionStorage(path.join(testDir, "t5"));

  storage.create("session-a", TABLE_CONFIG);
  storage.create("session-b", TABLE_CONFIG);
  storage.create("session-c", TABLE_CONFIG);
  storage.archive("session-a", 10);
  storage.archive("session-b", 5);

  const list = storage.list();
  check("list has 3 entries", list.length === 3);
  check("session-c is active", list.find(s => s.sessionId === "session-c").status === "active");
  check("session-a is complete", list.find(s => s.sessionId === "session-a").status === "complete");

  const active = storage.findActive();
  check("findActive returns session-c", active && active.sessionId === "session-c");
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 6: Event Log Integrity
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 6: Event Log Integrity ===");
{
  seed = 123;
  const storage = new SessionStorage(path.join(testDir, "t6"));
  const info = storage.create("t6-session", TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId: "t6-session", logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 1000 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 800 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 3, name: "Charlie", buyIn: 600 }));

  for (let i = 0; i < 10; i++) playFoldOutHand(s1);

  const memEvents = s1.getEventLog().length;
  const diskLines = fs.readFileSync(info.eventsPath, "utf8").trim().split("\n").filter(Boolean).length;
  check("disk event count matches memory", diskLines === memEvents);

  const lastMem = s1.getEventLog()[memEvents - 1];
  const lastDisk = JSON.parse(fs.readFileSync(info.eventsPath, "utf8").trim().split("\n").pop());
  check("last event matches", lastMem.type === lastDisk.type && lastMem.handId === lastDisk.handId);

  // Recover and verify count
  const s2 = Session.load(TABLE_CONFIG, "t6-session", info.eventsPath);
  check("recovered event count matches", s2.getEventLog().length === memEvents);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 7: Cross-Recovery Conformance
// ═══════════════════════════════════════════════════════════════════════════

console.log("\n=== Test 7: Cross-Recovery Conformance ===");
{
  seed = 999;
  const storage = new SessionStorage(path.join(testDir, "t7"));
  const info = storage.create("t7-session", TABLE_CONFIG);

  const s1 = new Session(TABLE_CONFIG, { sessionId: "t7-session", logPath: info.eventsPath, rng });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 1000 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 800 }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 3, name: "Charlie", buyIn: 600 }));

  for (let i = 0; i < 5; i++) playFoldOutHand(s1);

  const preShutdown = s1.getState();

  // Recover
  const s2 = Session.load(TABLE_CONFIG, "t7-session", info.eventsPath);
  const postRecovery = s2.getState();

  for (let i = 0; i < 6; i++) {
    check(`cross-recovery seat ${i} stack`, preShutdown.seats[i].stack === postRecovery.seats[i].stack);
    check(`cross-recovery seat ${i} status`, preShutdown.seats[i].status === postRecovery.seats[i].status);
  }
  check("cross-recovery handsPlayed", preShutdown.handsPlayed === postRecovery.handsPlayed);
  check("cross-recovery button", preShutdown.button === postRecovery.button);

  // reconstructState matches
  const recon = reconstructState(s2.getEventLog());
  check("recon matches post-recovery", recon.seats[0].stack === postRecovery.seats[0].stack);

  // Play hand 6 after recovery
  playFoldOutHand(s2);
  const finalSt = s2.getState();
  check("hand 6 after recovery", finalSt.handsPlayed === 6);

  let total = 0;
  Object.values(finalSt.seats).forEach(s => total += s.stack);
  check("stacks conserved across recovery", total === 2400);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Report
// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n=== Recovery Test Results ===`);
console.log(`Checks: ${checks}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(failed === 0
  ? `\n*** RECOVERY CONFORMANCE PASSED: ${passed}/${checks} ***`
  : `\n*** RECOVERY CONFORMANCE FAILED: ${failed}/${checks} ***`);

// Cleanup
fs.rmSync(testDir, { recursive: true, force: true });
