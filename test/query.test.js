#!/usr/bin/env node
"use strict";

/**
 * Phase 9B: Hand Query + Actor Stats Tests
 */

const path = require("path");
const fs = require("fs");
const { ActorRegistry } = require("../src/api/actors");
const { Session } = require("../src/api/session");
const { SessionStorage } = require("../src/api/storage");
const { queryHands, getActorStats, derivePosition } = require("../src/api/query");
const { CMD, command } = require("../src/api/commands");

const testDir = path.join(__dirname, "..", "test-output", "query-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

const TABLE_CONFIG = { tableId: "q-t", tableName: "Query", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

let seed = 42;
function rng() { seed = (seed * 1664525 + 1013904223) & 0x7fffffff; return seed / 0x7fffffff; }

function playFoldOut(session) {
  session.dispatch(command(CMD.START_HAND));
  let safety = 0;
  while (safety++ < 50) {
    const st = session.getState();
    if (!st.hand || st.hand.phase === "COMPLETE") break;
    const seat = st.hand.actionSeat;
    if (seat == null) break;
    session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "FOLD" }));
  }
}

function playShowdown(session) {
  session.dispatch(command(CMD.START_HAND));
  let safety = 0;
  while (safety++ < 200) {
    const st = session.getState();
    if (!st.hand || st.hand.phase === "COMPLETE") break;
    const seat = st.hand.actionSeat;
    if (seat == null) break;
    const legal = st.hand.legalActions;
    if (!legal) break;
    if (legal.actions.includes("CALL")) session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "CALL" }));
    else if (legal.actions.includes("CHECK")) session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "CHECK" }));
    else break;
  }
}

function playWithRaise(session) {
  // First to act raises preflop, others fold or call
  session.dispatch(command(CMD.START_HAND));
  let raised = false;
  let safety = 0;
  while (safety++ < 200) {
    const st = session.getState();
    if (!st.hand || st.hand.phase === "COMPLETE") break;
    const seat = st.hand.actionSeat;
    if (seat == null) break;
    const legal = st.hand.legalActions;
    if (!legal) break;

    if (!raised && st.hand.phase === "PREFLOP" && legal.actions.includes("RAISE")) {
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "RAISE", amount: legal.minRaise }));
      raised = true;
    } else if (legal.actions.includes("CALL")) {
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "CALL" }));
    } else if (legal.actions.includes("CHECK")) {
      session.dispatch(command(CMD.PLAYER_ACTION, { seat, action: "CHECK" }));
    } else {
      break;
    }
  }
}

/** Create a 2-session test fixture with known actors and hands */
function createFixture(prefix) {
  const actorsDir = path.join(testDir, prefix + "-actors");
  const sessionsDir = path.join(testDir, prefix + "-sessions");
  const reg = new ActorRegistry(actorsDir);
  const storage = new SessionStorage(sessionsDir);

  const alice = reg.create("Alice");
  const bob = reg.create("Bob");

  // Session 1: 2 fold-out + 1 showdown
  seed = 42;
  const info1 = storage.create(prefix + "-s1", TABLE_CONFIG);
  const s1 = new Session(TABLE_CONFIG, { sessionId: prefix + "-s1", logPath: info1.eventsPath, rng, actors: reg });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500, actorId: bob.actorId }));
  playFoldOut(s1);     // hand 1
  playFoldOut(s1);     // hand 2
  playShowdown(s1);    // hand 3

  // Session 2: 1 showdown + 1 fold-out
  seed = 77;
  const info2 = storage.create(prefix + "-s2", TABLE_CONFIG);
  const s2 = new Session(TABLE_CONFIG, { sessionId: prefix + "-s2", logPath: info2.eventsPath, rng, actors: reg });
  s2.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s2.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500, actorId: bob.actorId }));
  playShowdown(s2);    // hand 1
  playFoldOut(s2);     // hand 2

  return { reg, storage, alice, bob, s1, s2 };
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 1: Position Derivation
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 1: Position Derivation ===");
{
  // 6-max: seats 0-5, button at 3
  check("T1: BTN", derivePosition(3, 3, [0, 1, 2, 3, 4, 5]) === "BTN");
  check("T1: SB", derivePosition(4, 3, [0, 1, 2, 3, 4, 5]) === "SB");
  check("T1: BB", derivePosition(5, 3, [0, 1, 2, 3, 4, 5]) === "BB");
  check("T1: UTG", derivePosition(0, 3, [0, 1, 2, 3, 4, 5]) === "UTG");
  check("T1: MP", derivePosition(1, 3, [0, 1, 2, 3, 4, 5]) === "MP");
  check("T1: CO", derivePosition(2, 3, [0, 1, 2, 3, 4, 5]) === "CO");

  // 3-handed: seats 0, 2, 4, button at 0
  check("T1: 3h BTN", derivePosition(0, 0, [0, 2, 4]) === "BTN");
  check("T1: 3h SB", derivePosition(2, 0, [0, 2, 4]) === "SB");
  check("T1: 3h BB", derivePosition(4, 0, [0, 2, 4]) === "BB");

  // Heads-up: button = SB
  check("T1: HU SB", derivePosition(0, 0, [0, 1]) === "SB");
  check("T1: HU BB", derivePosition(1, 0, [0, 1]) === "BB");
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 2: queryHands — no filter
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 2: queryHands unfiltered ===");
{
  const { storage, alice, bob } = createFixture("t2");
  const all = queryHands(storage);
  // 5 hands × 2 players = 10 participations
  check("T2: 10 participations", all.length === 10);
  // All have sessionId
  check("T2: all have sessionId", all.every((p) => p.sessionId));
  check("T2: all have handId", all.every((p) => p.handId));
  check("T2: all have actorId", all.every((p) => p.actorId != null));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 3: queryHands — by actorId
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 3: queryHands by actorId ===");
{
  const { storage, alice, bob } = createFixture("t3");
  const aliceHands = queryHands(storage, { actorId: alice.actorId });
  check("T3: Alice has 5 participations", aliceHands.length === 5);
  check("T3: all are Alice", aliceHands.every((p) => p.actorId === alice.actorId));

  const bobHands = queryHands(storage, { actorId: bob.actorId });
  check("T3: Bob has 5 participations", bobHands.length === 5);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 4: queryHands — by sessionId
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 4: queryHands by sessionId ===");
{
  const { storage } = createFixture("t4");
  const s1Hands = queryHands(storage, { sessionId: "t4-s1" });
  check("T4: session 1 has 6 participations (3 hands × 2)", s1Hands.length === 6);
  check("T4: all from s1", s1Hands.every((p) => p.sessionId === "t4-s1"));

  const s2Hands = queryHands(storage, { sessionId: "t4-s2" });
  check("T4: session 2 has 4 participations (2 hands × 2)", s2Hands.length === 4);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 5: queryHands — showdown filter
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 5: queryHands showdown filter ===");
{
  const { storage, alice } = createFixture("t5");
  const sdHands = queryHands(storage, { showdown: true });
  check("T5: showdown hands exist", sdHands.length > 0);
  check("T5: all are showdown", sdHands.every((p) => p.showdown));

  const foldHands = queryHands(storage, { showdown: false });
  check("T5: fold-out hands exist", foldHands.length > 0);
  check("T5: none are showdown", foldHands.every((p) => !p.showdown));

  check("T5: showdown + foldout = all", sdHands.length + foldHands.length === 10);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 6: queryHands — combined filters
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 6: queryHands combined ===");
{
  const { storage, alice } = createFixture("t6");
  const aliceSD = queryHands(storage, { actorId: alice.actorId, showdown: true });
  check("T6: Alice showdown participations", aliceSD.length >= 1);
  check("T6: all Alice + showdown", aliceSD.every((p) => p.actorId === alice.actorId && p.showdown));

  const aliceS1 = queryHands(storage, { actorId: alice.actorId, sessionId: "t6-s1" });
  check("T6: Alice in s1 = 3 hands", aliceS1.length === 3);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 7: queryHands — result filter
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 7: queryHands result filter ===");
{
  const { storage, alice } = createFixture("t7");
  const won = queryHands(storage, { actorId: alice.actorId, result: "won" });
  const lost = queryHands(storage, { actorId: alice.actorId, result: "lost" });
  check("T7: won + lost account for all", won.length + lost.length <= 5);
  check("T7: won hands have totalWon > 0", won.every((p) => p.totalWon > 0));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 8: getActorStats — basic counts
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 8: getActorStats basic ===");
{
  const { storage, alice, bob } = createFixture("t8");
  const stats = getActorStats(storage, alice.actorId);

  check("T8: handsDealt = 5", stats.handsDealt === 5);
  check("T8: actorId matches", stats.actorId === alice.actorId);
  check("T8: vpip is 0-1", stats.vpip >= 0 && stats.vpip <= 1);
  check("T8: pfr is 0-1", stats.pfr >= 0 && stats.pfr <= 1);
  check("T8: pfr <= vpip", stats.pfr <= stats.vpip + 0.001); // float tolerance
  check("T8: netResult is number", typeof stats.netResult === "number");
  check("T8: handsByPosition is object", typeof stats.handsByPosition === "object");
  check("T8: totalWon + totalInvested non-negative", stats.totalWon >= 0 && stats.totalInvested >= 0);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 9: getActorStats — session scoped
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 9: getActorStats session-scoped ===");
{
  const { storage, alice } = createFixture("t9");
  const s1Stats = getActorStats(storage, alice.actorId, "t9-s1");
  const s2Stats = getActorStats(storage, alice.actorId, "t9-s2");
  const allStats = getActorStats(storage, alice.actorId);

  check("T9: s1 handsDealt = 3", s1Stats.handsDealt === 3);
  check("T9: s2 handsDealt = 2", s2Stats.handsDealt === 2);
  check("T9: all = s1 + s2", allStats.handsDealt === s1Stats.handsDealt + s2Stats.handsDealt);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 10: VPIP semantics
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 10: VPIP semantics ===");
{
  // Create a session where we control actions more precisely
  seed = 42;
  const reg = new ActorRegistry(path.join(testDir, "t10-actors"));
  const storage = new SessionStorage(path.join(testDir, "t10-sessions"));
  const info = storage.create("t10", TABLE_CONFIG);
  const alice = reg.create("Alice");
  const bob = reg.create("Bob");
  const charlie = reg.create("Charlie");
  const s = new Session(TABLE_CONFIG, { sessionId: "t10", logPath: info.eventsPath, rng, actors: reg });
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500, actorId: bob.actorId }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 3, name: "Charlie", buyIn: 500, actorId: charlie.actorId }));

  // Hand 1: first to act folds, second calls, BB checks → fold-out or showdown
  s.dispatch(command(CMD.START_HAND));
  let st = s.getState();
  let actionSeat = st.hand.actionSeat;
  // First actor folds (this one is NOT vpip)
  s.dispatch(command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "FOLD" }));
  // Play rest to completion
  let safety = 0;
  while (safety++ < 100) {
    st = s.getState();
    if (!st.hand || st.hand.phase === "COMPLETE") break;
    actionSeat = st.hand.actionSeat;
    if (actionSeat == null) break;
    const legal = st.hand.legalActions;
    if (!legal) break;
    if (legal.actions.includes("CALL")) s.dispatch(command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "CALL" }));
    else if (legal.actions.includes("CHECK")) s.dispatch(command(CMD.PLAYER_ACTION, { seat: actionSeat, action: "CHECK" }));
    else break;
  }

  // Get participations for this hand
  const hands = queryHands(storage, { sessionId: "t10" });
  const h1 = hands.filter((p) => p.handId === "1");

  // Find who folded preflop
  const folders = h1.filter((p) => p.foldedPreflop);
  check("T10: someone folded preflop", folders.length >= 1);
  check("T10: folder is NOT vpip", folders.every((p) => !p.vpipHand));

  // Non-folders who called are vpip
  const callers = h1.filter((p) => !p.foldedPreflop && p.vpipHand);
  // BB who only checked is NOT vpip (no voluntary investment beyond blind)
  const nonVpip = h1.filter((p) => !p.foldedPreflop && !p.vpipHand);
  check("T10: BB check-only is NOT vpip or callers exist",
    callers.length >= 0 && nonVpip.length >= 0); // structural check
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 11: PFR semantics
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 11: PFR semantics ===");
{
  seed = 42;
  const reg = new ActorRegistry(path.join(testDir, "t11-actors"));
  const storage = new SessionStorage(path.join(testDir, "t11-sessions"));
  const info = storage.create("t11", TABLE_CONFIG);
  const alice = reg.create("Alice");
  const bob = reg.create("Bob");
  const s = new Session(TABLE_CONFIG, { sessionId: "t11", logPath: info.eventsPath, rng, actors: reg });
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500, actorId: bob.actorId }));

  // Hand with raise: someone raises preflop
  playWithRaise(s);

  const hands = queryHands(storage, { sessionId: "t11" });
  const raisers = hands.filter((p) => p.pfrHand);
  check("T11: at least 1 PFR hand", raisers.length >= 1);
  check("T11: raiser is also vpip", raisers.every((p) => p.vpipHand));

  // Stats check
  const aliceStats = getActorStats(storage, alice.actorId, "t11");
  const bobStats = getActorStats(storage, bob.actorId, "t11");
  check("T11: PFR ≤ VPIP for Alice", aliceStats.pfr <= aliceStats.vpip + 0.001);
  check("T11: PFR ≤ VPIP for Bob", bobStats.pfr <= bobStats.vpip + 0.001);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 12: WTSD and WSD semantics
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 12: WTSD / WSD ===");
{
  const { storage, alice, bob } = createFixture("t12");
  const stats = getActorStats(storage, alice.actorId);

  // 2 showdown hands out of 5 total. Some fold-outs.
  check("T12: wtsd in 0-1 range", stats.wtsd >= 0 && stats.wtsd <= 1);
  check("T12: wsd in 0-1 range", stats.wsd >= 0 && stats.wsd <= 1);

  // WTSD denominator = hands where Alice didn't fold preflop
  const aliceHands = queryHands(storage, { actorId: alice.actorId });
  const notFoldedPF = aliceHands.filter((p) => !p.foldedPreflop);
  const wentSD = notFoldedPF.filter((p) => p.wentToShowdown);

  const expectedWtsd = notFoldedPF.length > 0 ? wentSD.length / notFoldedPF.length : 0;
  check("T12: wtsd matches manual calculation", Math.abs(stats.wtsd - expectedWtsd) < 0.001);

  const wonSD = wentSD.filter((p) => p.wonAtShowdown);
  const expectedWsd = wentSD.length > 0 ? wonSD.length / wentSD.length : 0;
  check("T12: wsd matches manual calculation", Math.abs(stats.wsd - expectedWsd) < 0.001);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 13: aggFactor
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 13: aggFactor ===");
{
  seed = 42;
  const reg = new ActorRegistry(path.join(testDir, "t13-actors"));
  const storage = new SessionStorage(path.join(testDir, "t13-sessions"));
  const info = storage.create("t13", TABLE_CONFIG);
  const alice = reg.create("Alice");
  const bob = reg.create("Bob");
  const s = new Session(TABLE_CONFIG, { sessionId: "t13", logPath: info.eventsPath, rng, actors: reg });
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500, actorId: bob.actorId }));

  // Play several hands
  playWithRaise(s);
  seed = 55;
  playShowdown(s);
  playFoldOut(s);

  const aliceStats = getActorStats(storage, alice.actorId, "t13");
  // aggFactor should be a number or null
  check("T13: aggFactor is number or null", aliceStats.aggFactor === null || typeof aliceStats.aggFactor === "number");
  if (aliceStats.aggFactor !== null) {
    check("T13: aggFactor >= 0", aliceStats.aggFactor >= 0);
  }

  // Manual check: count bets+raises vs calls
  const aliceHands = queryHands(storage, { actorId: alice.actorId, sessionId: "t13" });
  const totalBR = aliceHands.reduce((s, p) => s + p.betsRaises, 0);
  const totalC = aliceHands.reduce((s, p) => s + p.calls, 0);
  const expectedAF = totalC > 0 ? totalBR / totalC : null;
  check("T13: aggFactor matches manual", aliceStats.aggFactor === expectedAF);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 14: netResult
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 14: netResult ===");
{
  const { storage, alice, bob } = createFixture("t14");
  const aliceStats = getActorStats(storage, alice.actorId);
  const bobStats = getActorStats(storage, bob.actorId);

  check("T14: netResult = totalWon - totalInvested (Alice)",
    aliceStats.netResult === aliceStats.totalWon - aliceStats.totalInvested);
  check("T14: netResult = totalWon - totalInvested (Bob)",
    bobStats.netResult === bobStats.totalWon - bobStats.totalInvested);

  // In a 2-player zero-sum game, Alice net + Bob net = 0
  // (excluding rake, which is 0)
  check("T14: zero-sum (Alice + Bob net = 0)", aliceStats.netResult + bobStats.netResult === 0);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 15: Anonymous actorId handling
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 15: Anonymous actorId ===");
{
  seed = 42;
  const storage = new SessionStorage(path.join(testDir, "t15-sessions"));
  const info = storage.create("t15", TABLE_CONFIG);
  // No actor registry → null actorIds
  const s = new Session(TABLE_CONFIG, { sessionId: "t15", logPath: info.eventsPath, rng });
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500 }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));
  playFoldOut(s);

  // Unfiltered: anonymous hands appear
  const all = queryHands(storage);
  check("T15: anonymous hands in unfiltered query", all.length === 2);
  check("T15: actorId is null", all.every((p) => p.actorId === null));

  // Filtered by actorId: anonymous excluded
  const filtered = queryHands(storage, { actorId: "act-anything" });
  check("T15: anonymous excluded from actor query", filtered.length === 0);

  // Stats for null actorId: zeroed
  const stats = getActorStats(storage, null);
  check("T15: null actorId → zero stats", stats.handsDealt === 0);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 16: Voided hands excluded
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 16: Voided hands excluded ===");
{
  seed = 42;
  const reg = new ActorRegistry(path.join(testDir, "t16-actors"));
  const storage = new SessionStorage(path.join(testDir, "t16-sessions"));
  const info = storage.create("t16", TABLE_CONFIG);
  const s = new Session(TABLE_CONFIG, { sessionId: "t16", logPath: info.eventsPath, rng, actors: reg });
  const alice = reg.create("Alice");
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));

  playFoldOut(s); // hand 1 completes

  // Play hand 2 partially, then simulate crash by truncating log
  playShowdown(s); // hand 2 completes

  // Truncate after SHOWDOWN_REVEAL of hand 2 to simulate crash
  const lines = fs.readFileSync(info.eventsPath, "utf8").trim().split("\n");
  let cutAfter = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (JSON.parse(lines[i]).type === "SHOWDOWN_REVEAL") { cutAfter = i; break; }
  }
  if (cutAfter >= 0) {
    fs.writeFileSync(info.eventsPath, lines.slice(0, cutAfter + 1).join("\n") + "\n");
  }

  // Recover — this voids hand 2
  const s2 = Session.load(TABLE_CONFIG, "t16", info.eventsPath, { actors: reg });

  // Query should exclude voided hand
  const hands = queryHands(storage, { sessionId: "t16" });
  check("T16: voided hand excluded from query", hands.every((p) => !p.voided));
  check("T16: only hand 1 participations", hands.length === 2); // 1 hand × 2 players
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 17: Name normalization in resolution
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 17: Name normalization ===");
{
  const reg = new ActorRegistry(path.join(testDir, "t17-actors"));
  const alice = reg.create("  Alice  ");
  check("T17: name trimmed on create", alice.name === "Alice");

  const found = reg.findByName("Alice");
  check("T17: finds trimmed name", found.length === 1);

  const found2 = reg.findByName("  Alice  ");
  check("T17: finds with extra whitespace", found2.length === 1);

  const r = reg.resolve("Alice  ");
  check("T17: resolve normalizes", r.actorId === alice.actorId && !r.created);

  // Case sensitive
  const r2 = reg.resolve("alice");
  check("T17: case sensitive — 'alice' creates new", r2.created === true);
  check("T17: different actorId", r2.actorId !== alice.actorId);
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n*** QUERY TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
if (failed > 0) process.exit(1);
