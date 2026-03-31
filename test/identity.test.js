#!/usr/bin/env node
"use strict";

/**
 * Phase 9A: Identity (Actor Registry + Event Linkage) Tests
 */

const path = require("path");
const fs = require("fs");
const { ActorRegistry } = require("../src/api/actors");
const { Session } = require("../src/api/session");
const { SessionStorage } = require("../src/api/storage");
const { reconstructState } = require("../src/api/reconstruct");
const { CMD, command } = require("../src/api/commands");

const testDir = path.join(__dirname, "..", "test-output", "identity-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

const TABLE_CONFIG = { tableId: "id-t", tableName: "Identity", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

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

// ═══════════════════════════════════════════════════════════════════════════
//  Test 1: Actor CRUD
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 1: Actor CRUD ===");
{
  const reg = new ActorRegistry(path.join(testDir, "actors-t1"));
  const alice = reg.create("Alice", "Aggressive player");

  check("T1: actorId starts with act-", alice.actorId.startsWith("act-"));
  check("T1: name is Alice", alice.name === "Alice");
  check("T1: notes set", alice.notes === "Aggressive player");
  check("T1: createdAt is ISO string", alice.createdAt.includes("T"));

  const fetched = reg.get(alice.actorId);
  check("T1: get returns same actor", fetched.actorId === alice.actorId && fetched.name === "Alice");

  const bob = reg.create("Bob");
  const all = reg.list();
  check("T1: list returns 2 actors", all.length === 2);

  const updated = reg.update(alice.actorId, { name: "Alice V2", notes: "Tightened up" });
  check("T1: update returns updated actor", updated.name === "Alice V2" && updated.notes === "Tightened up");

  const refetched = reg.get(alice.actorId);
  check("T1: update persisted", refetched.name === "Alice V2");

  check("T1: get nonexistent returns null", reg.get("act-doesnotexist") === null);
  check("T1: update nonexistent returns null", reg.update("act-doesnotexist", { name: "X" }) === null);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 2: findByName
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 2: findByName ===");
{
  const reg = new ActorRegistry(path.join(testDir, "actors-t2"));
  reg.create("Alice");
  reg.create("Bob");
  reg.create("Alice"); // second Alice — different actorId

  const alices = reg.findByName("Alice");
  check("T2: two Alices found", alices.length === 2);
  check("T2: different actorIds", alices[0].actorId !== alices[1].actorId);

  const bobs = reg.findByName("Bob");
  check("T2: one Bob", bobs.length === 1);

  const unknowns = reg.findByName("Charlie");
  check("T2: no Charlie", unknowns.length === 0);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 3: resolve — exact match, no match, ambiguous
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 3: resolve ===");
{
  const reg = new ActorRegistry(path.join(testDir, "actors-t3"));
  const alice = reg.create("Alice");

  // Exact match by name (1 Alice → reuse)
  const r1 = reg.resolve("Alice");
  check("T3: exact match reuses actorId", r1.actorId === alice.actorId);
  check("T3: exact match not created", r1.created === false);

  // No match → creates
  const r2 = reg.resolve("Charlie");
  check("T3: new name creates actor", r2.created === true);
  check("T3: new actor has actorId", r2.actorId.startsWith("act-"));
  const charlie = reg.get(r2.actorId);
  check("T3: created actor persists", charlie.name === "Charlie");

  // Ambiguous (2 Alices) → creates new, doesn't guess
  reg.create("Alice"); // now 2 Alices
  const r3 = reg.resolve("Alice");
  check("T3: ambiguous creates new", r3.created === true);
  check("T3: ambiguous doesn't reuse", r3.actorId !== alice.actorId);

  // Explicit actorId → uses it
  const r4 = reg.resolve("Alice", alice.actorId);
  check("T3: explicit actorId reuses", r4.actorId === alice.actorId);
  check("T3: explicit not created", r4.created === false);

  // Explicit invalid actorId → creates new (defensive)
  const r5 = reg.resolve("Alice", "act-bogus");
  check("T3: invalid explicit creates new", r5.created === true);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 4: SEAT_PLAYER with actorId via Session
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 4: SEAT_PLAYER with actorId ===");
{
  const reg = new ActorRegistry(path.join(testDir, "actors-t4"));
  const storage = new SessionStorage(path.join(testDir, "sessions-t4"));
  const info = storage.create("t4-session", TABLE_CONFIG);

  const s = new Session(TABLE_CONFIG, { sessionId: "t4-session", logPath: info.eventsPath, rng, actors: reg });

  // Create actor explicitly, then seat with actorId
  const alice = reg.create("Alice");
  const res = s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  check("T4: seat ok", res.ok);

  const seatEvent = res.events[0];
  check("T4: event has actorId", seatEvent.actorId === alice.actorId);
  check("T4: event type is SEAT_PLAYER", seatEvent.type === "SEAT_PLAYER");

  // State reflects actorId
  const st = s.getState();
  check("T4: state has actorId", st.seats[0].player.actorId === alice.actorId);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 5: SEAT_PLAYER auto-resolve (no actorId provided)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 5: SEAT_PLAYER auto-resolve ===");
{
  const reg = new ActorRegistry(path.join(testDir, "actors-t5"));
  const storage = new SessionStorage(path.join(testDir, "sessions-t5"));
  const info = storage.create("t5-session", TABLE_CONFIG);

  const s = new Session(TABLE_CONFIG, { sessionId: "t5-session", logPath: info.eventsPath, rng, actors: reg });

  // No actor exists yet — auto-create
  const res1 = s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Bob", buyIn: 500 }));
  check("T5: auto-create ok", res1.ok);
  const ev1 = res1.events[0];
  check("T5: auto-created actorId present", ev1.actorId && ev1.actorId.startsWith("act-"));

  // Bob now exists in registry
  const bobs = reg.findByName("Bob");
  check("T5: Bob registered", bobs.length === 1);
  check("T5: Bob actorId matches event", bobs[0].actorId === ev1.actorId);

  // Seat another player — unique name resolves to existing
  const res2 = s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));
  const ev2 = res2.events[0];
  check("T5: same name reuses actorId", ev2.actorId === ev1.actorId);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 6: HAND_START includes actorId snapshot
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 6: HAND_START actorId snapshot ===");
{
  seed = 42;
  const reg = new ActorRegistry(path.join(testDir, "actors-t6"));
  const storage = new SessionStorage(path.join(testDir, "sessions-t6"));
  const info = storage.create("t6-session", TABLE_CONFIG);

  const s = new Session(TABLE_CONFIG, { sessionId: "t6-session", logPath: info.eventsPath, rng, actors: reg });

  const alice = reg.create("Alice");
  const bob = reg.create("Bob");
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500, actorId: bob.actorId }));

  playFoldOut(s);

  const events = s.getEventLog();
  const handStart = events.find((e) => e.type === "HAND_START");
  check("T6: HAND_START exists", !!handStart);
  check("T6: players[0] has actorId", handStart.players["0"].actorId === alice.actorId);
  check("T6: players[1] has actorId", handStart.players["1"].actorId === bob.actorId);
  check("T6: players[0] has name", handStart.players["0"].name === "Alice");
  check("T6: players[0] has stack", handStart.players["0"].stack === 500);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 7: Reconstruct with actorId events
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 7: Reconstruct with actorId ===");
{
  seed = 77;
  const reg = new ActorRegistry(path.join(testDir, "actors-t7"));
  const storage = new SessionStorage(path.join(testDir, "sessions-t7"));
  const info = storage.create("t7-session", TABLE_CONFIG);

  const s = new Session(TABLE_CONFIG, { sessionId: "t7-session", logPath: info.eventsPath, rng, actors: reg });
  const alice = reg.create("Alice");
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));

  playFoldOut(s);

  const events = s.getEventLog();
  const recon = reconstructState(events);

  check("T7: recon seat 0 has actorId", recon.seats[0].player.actorId === alice.actorId);
  // Bob was auto-created, so actorId should be present too
  check("T7: recon seat 1 has actorId (auto)", recon.seats[1].player.actorId != null);
  check("T7: stacks match", recon.seats[0].stack === s.getState().seats[0].stack);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 8: Backwards compat — events without actorId
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 8: Backwards compatibility ===");
{
  // Simulate old-format events without actorId
  const oldEvents = [
    { type: "TABLE_SNAPSHOT", sessionId: "old", tableId: "t", tableName: "Old", gameType: 2, maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000, seats: [], button: -1 },
    { type: "SEAT_PLAYER", sessionId: "old", seat: 0, player: "Alice", buyIn: 500, country: "XX" },
    { type: "SEAT_PLAYER", sessionId: "old", seat: 1, player: "Bob", buyIn: 500, country: "XX" },
    { type: "HAND_START", sessionId: "old", handId: "1", tableId: "t", tableName: "Old", button: 0, sb: 5, bb: 10, players: { 0: { name: "Alice", stack: 500 }, 1: { name: "Bob", stack: 500 } } },
    { type: "HAND_END", sessionId: "old", handId: "1", tableId: "t" },
  ];

  const recon = reconstructState(oldEvents);
  check("T8: recon works without actorId", recon != null);
  check("T8: seat 0 actorId is null", recon.seats[0].player.actorId === null);
  check("T8: seat 1 actorId is null", recon.seats[1].player.actorId === null);
  check("T8: seat 0 name preserved", recon.seats[0].player.name === "Alice");
  check("T8: handsPlayed correct", recon.handsPlayed === 1);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 9: No actor registry — session works without identity
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 9: Session without actor registry ===");
{
  seed = 99;
  const storage = new SessionStorage(path.join(testDir, "sessions-t9"));
  const info = storage.create("t9-session", TABLE_CONFIG);

  // No actors option → null registry
  const s = new Session(TABLE_CONFIG, { sessionId: "t9-session", logPath: info.eventsPath, rng });
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500 }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));

  const ev1 = s.getEventLog().find((e) => e.type === "SEAT_PLAYER" && e.seat === 0);
  check("T9: no registry → actorId is null", ev1.actorId === null);

  playFoldOut(s);
  check("T9: hand completes without registry", s.getState().handsPlayed === 1);

  // Actor commands fail gracefully
  const res = s.dispatch(command(CMD.LIST_ACTORS));
  check("T9: LIST_ACTORS fails without registry", res.ok === false);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 10: Cross-session actor persistence
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 10: Cross-session actor persistence ===");
{
  seed = 42;
  const actorsDir = path.join(testDir, "actors-t10");
  const reg = new ActorRegistry(actorsDir);
  const alice = reg.create("Alice");

  // Session 1
  const storage1 = new SessionStorage(path.join(testDir, "sessions-t10a"));
  const info1 = storage1.create("t10a", TABLE_CONFIG);
  const s1 = new Session(TABLE_CONFIG, { sessionId: "t10a", logPath: info1.eventsPath, rng, actors: reg });
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));
  playFoldOut(s1);

  // Session 2 — same actor registry
  seed = 55;
  const reg2 = new ActorRegistry(actorsDir); // fresh instance, same disk
  const storage2 = new SessionStorage(path.join(testDir, "sessions-t10b"));
  const info2 = storage2.create("t10b", TABLE_CONFIG);
  const s2 = new Session(TABLE_CONFIG, { sessionId: "t10b", logPath: info2.eventsPath, rng, actors: reg2 });
  s2.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 600, actorId: alice.actorId }));
  s2.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Charlie", buyIn: 500 }));
  playFoldOut(s2);

  // Both sessions should have the same actorId for Alice
  const hs1 = s1.getEventLog().find((e) => e.type === "HAND_START");
  const hs2 = s2.getEventLog().find((e) => e.type === "HAND_START");
  check("T10: session 1 Alice actorId", hs1.players["0"].actorId === alice.actorId);
  check("T10: session 2 Alice actorId", hs2.players["0"].actorId === alice.actorId);
  check("T10: same actorId across sessions", hs1.players["0"].actorId === hs2.players["0"].actorId);

  // Actor persists via disk reload
  const refetched = reg2.get(alice.actorId);
  check("T10: actor persists across registry instances", refetched.name === "Alice");
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 11: Actor commands via dispatch
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 11: Actor commands ===");
{
  const reg = new ActorRegistry(path.join(testDir, "actors-t11"));
  const storage = new SessionStorage(path.join(testDir, "sessions-t11"));
  const info = storage.create("t11-session", TABLE_CONFIG);
  const s = new Session(TABLE_CONFIG, { sessionId: "t11-session", logPath: info.eventsPath, rng, actors: reg });

  // CREATE_ACTOR
  const cr = s.dispatch(command(CMD.CREATE_ACTOR, { name: "Dave", notes: "Tight" }));
  check("T11: CREATE_ACTOR ok", cr.ok);
  check("T11: returns actor", cr.state.actor.name === "Dave");
  const daveId = cr.state.actor.actorId;

  // GET_ACTOR
  const gr = s.dispatch(command(CMD.GET_ACTOR, { actorId: daveId }));
  check("T11: GET_ACTOR ok", gr.ok);
  check("T11: correct actor", gr.state.actor.actorId === daveId);

  // LIST_ACTORS
  s.dispatch(command(CMD.CREATE_ACTOR, { name: "Eve" }));
  const lr = s.dispatch(command(CMD.LIST_ACTORS));
  check("T11: LIST_ACTORS ok", lr.ok);
  check("T11: 2 actors", lr.state.actors.length === 2);

  // UPDATE_ACTOR
  const ur = s.dispatch(command(CMD.UPDATE_ACTOR, { actorId: daveId, name: "David", notes: "Loosened up" }));
  check("T11: UPDATE_ACTOR ok", ur.ok);
  check("T11: name updated", ur.state.actor.name === "David");

  // GET_ACTOR after update
  const gr2 = s.dispatch(command(CMD.GET_ACTOR, { actorId: daveId }));
  check("T11: update persisted", gr2.state.actor.name === "David" && gr2.state.actor.notes === "Loosened up");

  // Error cases
  check("T11: GET_ACTOR missing id", s.dispatch(command(CMD.GET_ACTOR, {})).ok === false);
  check("T11: GET_ACTOR bad id", s.dispatch(command(CMD.GET_ACTOR, { actorId: "bad" })).ok === false);
  check("T11: CREATE_ACTOR missing name", s.dispatch(command(CMD.CREATE_ACTOR, {})).ok === false);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 12: Showdown hand with actorId — full integration
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 12: Showdown with actorId ===");
{
  seed = 42;
  const reg = new ActorRegistry(path.join(testDir, "actors-t12"));
  const storage = new SessionStorage(path.join(testDir, "sessions-t12"));
  const info = storage.create("t12-session", TABLE_CONFIG);
  const s = new Session(TABLE_CONFIG, { sessionId: "t12-session", logPath: info.eventsPath, rng, actors: reg });

  const alice = reg.create("Alice");
  const bob = reg.create("Bob");
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500, actorId: bob.actorId }));

  playShowdown(s);

  const events = s.getEventLog();
  const hs = events.find((e) => e.type === "HAND_START");
  const reveal = events.find((e) => e.type === "SHOWDOWN_REVEAL");
  const summary = events.find((e) => e.type === "HAND_SUMMARY");

  check("T12: HAND_START has actorIds", hs.players["0"].actorId === alice.actorId && hs.players["1"].actorId === bob.actorId);
  check("T12: showdown completed", !!reveal);
  check("T12: hand summary exists", !!summary);
  check("T12: accounting holds", Object.values(s.getState().seats).filter((x) => x.player).reduce((sum, x) => sum + x.stack, 0) === 1000);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 13: Recovery preserves actorId
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 13: Recovery preserves actorId ===");
{
  seed = 42;
  const reg = new ActorRegistry(path.join(testDir, "actors-t13"));
  const storage = new SessionStorage(path.join(testDir, "sessions-t13"));
  const info = storage.create("t13-session", TABLE_CONFIG);
  const s1 = new Session(TABLE_CONFIG, { sessionId: "t13-session", logPath: info.eventsPath, rng, actors: reg });

  const alice = reg.create("Alice");
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 0, name: "Alice", buyIn: 500, actorId: alice.actorId }));
  s1.dispatch(command(CMD.SEAT_PLAYER, { seat: 1, name: "Bob", buyIn: 500 }));
  playFoldOut(s1);

  const preSt = s1.getState();

  // Recover
  const s2 = Session.load(TABLE_CONFIG, "t13-session", info.eventsPath, { actors: reg });
  const postSt = s2.getState();

  check("T13: recovered seat 0 actorId", postSt.seats[0].player.actorId === alice.actorId);
  check("T13: recovered seat 1 actorId present", postSt.seats[1].player.actorId != null);
  check("T13: stacks match", postSt.seats[0].stack === preSt.seats[0].stack);
  check("T13: can play after recovery", true);
  playFoldOut(s2);
  check("T13: second hand works", s2.getState().handsPlayed === 2);
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n*** IDENTITY TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
if (failed > 0) process.exit(1);
