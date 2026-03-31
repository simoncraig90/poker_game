#!/usr/bin/env node
"use strict";

/**
 * Phase 9C: Study UI (WS Protocol) Tests
 *
 * Tests actor management, query, and stats commands over WebSocket.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "study-client-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

async function connectWS(port) {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://localhost:${port}`);
    ws.on("message", function first(raw) {
      const msg = JSON.parse(raw.toString());
      if (msg.welcome) { ws.removeListener("message", first); resolve({ ws, welcome: msg }); }
    });
  });
}

let cmdId = 0;
function sendCmd(ws, cmd, payload) {
  return new Promise((resolve) => {
    const id = `sc-${++cmdId}`;
    const handler = (raw) => {
      const m = JSON.parse(raw.toString());
      if (m.id === id) { ws.removeListener("message", handler); resolve(m); }
    };
    ws.on("message", handler);
    ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
  });
}

function collectUntilHandEnd(ws) {
  return new Promise((resolve) => {
    const events = [];
    const handler = (raw) => {
      const msg = JSON.parse(raw.toString());
      const evts = msg.broadcast ? msg.events : msg.events;
      if (evts) {
        for (const e of evts) {
          events.push(e);
          if (e.type === "HAND_END") { ws.removeListener("message", handler); resolve(events); return; }
        }
      }
    };
    ws.on("message", handler);
  });
}

async function playShowdownViaWS(ws) {
  let safety = 0;
  while (safety++ < 200) {
    const resp = await sendCmd(ws, "GET_STATE");
    const st = resp.state;
    if (!st || !st.hand || st.hand.phase === "COMPLETE") break;
    const seat = st.hand.actionSeat;
    if (seat == null) break;
    const legal = st.hand.legalActions;
    if (!legal) break;
    if (legal.actions.includes("CALL")) await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CALL" });
    else if (legal.actions.includes("CHECK")) await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CHECK" });
    else break;
  }
}

const TABLE = { tableId: "st-t", tableName: "Study", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Actor CRUD over WS
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Actor CRUD ===");
  {
    const port = 9200 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t1", "sessions"), actorsDir: path.join(testDir, "t1", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    // CREATE
    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice", notes: "Test player" });
    check("T1: CREATE_ACTOR ok", cr.ok);
    check("T1: actor has actorId", cr.state.actor.actorId.startsWith("act-"));
    check("T1: actor name", cr.state.actor.name === "Alice");
    const aliceId = cr.state.actor.actorId;

    // GET
    const gr = await sendCmd(ws, "GET_ACTOR", { actorId: aliceId });
    check("T1: GET_ACTOR ok", gr.ok);
    check("T1: get returns Alice", gr.state.actor.name === "Alice");

    // LIST
    await sendCmd(ws, "CREATE_ACTOR", { name: "Bob" });
    const lr = await sendCmd(ws, "LIST_ACTORS");
    check("T1: LIST_ACTORS ok", lr.ok);
    check("T1: 2 actors", lr.state.actors.length === 2);

    // UPDATE
    const ur = await sendCmd(ws, "UPDATE_ACTOR", { actorId: aliceId, name: "Alice V2" });
    check("T1: UPDATE_ACTOR ok", ur.ok);
    check("T1: updated name", ur.state.actor.name === "Alice V2");

    // Errors
    const err1 = await sendCmd(ws, "GET_ACTOR", { actorId: "bad" });
    check("T1: GET_ACTOR bad id → error", err1.ok === false);
    const err2 = await sendCmd(ws, "CREATE_ACTOR", {});
    check("T1: CREATE_ACTOR no name → error", err2.ok === false);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: SEAT_PLAYER with actor auto-resolution
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Seat with actor ===");
  {
    const port = 9300 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t2", "sessions"), actorsDir: path.join(testDir, "t2", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    // Seat without prior actor — should auto-create
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    const st = (await sendCmd(ws, "GET_STATE")).state;
    check("T2: seat 0 has actorId", st.seats[0].player.actorId != null);

    // Create actor explicitly, seat with actorId
    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Bob" });
    const bobId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500, actorId: bobId });
    const st2 = (await sendCmd(ws, "GET_STATE")).state;
    check("T2: seat 1 has explicit actorId", st2.seats[1].player.actorId === bobId);

    // Play a hand — HAND_START should have actorIds
    const ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    const events = await ep;
    const hs = events.find((e) => e.type === "HAND_START");
    check("T2: HAND_START seat 0 actorId", hs.players["0"].actorId != null);
    check("T2: HAND_START seat 1 actorId", hs.players["1"].actorId === bobId);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: QUERY_HANDS over WS
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: QUERY_HANDS ===");
  {
    const port = 9350 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t3", "sessions"), actorsDir: path.join(testDir, "t3", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play 2 hands: fold-out + showdown
    let ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    let st = (await sendCmd(ws, "GET_STATE")).state;
    await sendCmd(ws, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });
    await ep;

    ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    await ep;

    // Unfiltered
    const all = await sendCmd(ws, "QUERY_HANDS", {});
    check("T3: QUERY_HANDS ok", all.ok);
    check("T3: 4 participations (2 hands × 2 players)", all.state.hands.length === 4);

    // By actorId
    const alice = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    check("T3: Alice has 2 hands", alice.state.hands.length === 2);
    check("T3: all Alice", alice.state.hands.every((h) => h.actorId === aliceId));

    // Showdown only
    const sd = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, showdown: true });
    check("T3: Alice showdown = 1", sd.state.hands.length === 1);
    check("T3: is showdown", sd.state.hands[0].showdown);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: GET_ACTOR_STATS over WS
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: GET_ACTOR_STATS ===");
  {
    const port = 9400 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t4", "sessions"), actorsDir: path.join(testDir, "t4", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play 3 hands
    for (let i = 0; i < 3; i++) {
      const ep = collectUntilHandEnd(ws);
      await sendCmd(ws, "START_HAND");
      await playShowdownViaWS(ws);
      await ep;
    }

    const resp = await sendCmd(ws, "GET_ACTOR_STATS", { actorId: aliceId });
    check("T4: GET_ACTOR_STATS ok", resp.ok);
    const stats = resp.state.stats;
    check("T4: handsDealt = 3", stats.handsDealt === 3);
    check("T4: vpip in range", stats.vpip >= 0 && stats.vpip <= 1);
    check("T4: pfr in range", stats.pfr >= 0 && stats.pfr <= 1);
    check("T4: netResult is number", typeof stats.netResult === "number");
    check("T4: handsByPosition exists", typeof stats.handsByPosition === "object");

    // Error: missing actorId
    const err = await sendCmd(ws, "GET_ACTOR_STATS", {});
    check("T4: missing actorId → error", err.ok === false);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Anonymous exclusion in actor queries
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Anonymous exclusion ===");
  {
    const port = 9450 + Math.floor(Math.random() * 100);
    // Create server — actors are auto-created via seating, but test that
    // actor-filtered queries only return linked hands
    const srv = startServer({ port, dataDir: path.join(testDir, "t5", "sessions"), actorsDir: path.join(testDir, "t5", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 }); // Bob auto-created

    const ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    await ep;

    // Query for Alice — should find 1
    const alice = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    check("T5: Alice query = 1 hand", alice.state.hands.length === 1);

    // Query for a nonexistent actor — should find 0
    const nobody = await sendCmd(ws, "QUERY_HANDS", { actorId: "act-doesnotexist" });
    check("T5: nonexistent actor = 0 hands", nobody.state.hands.length === 0);

    // Unfiltered — should find 2 (Alice + Bob)
    const all = await sendCmd(ws, "QUERY_HANDS", {});
    check("T5: unfiltered = 2", all.state.hands.length === 2);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: Session-scoped stats
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: Session-scoped stats ===");
  {
    const port = 9500 + Math.floor(Math.random() * 100);
    const sessDir = path.join(testDir, "t6", "sessions");
    const actDir = path.join(testDir, "t6", "actors");
    const srv = startServer({ port, dataDir: sessDir, actorsDir: actDir, table: TABLE });
    const { ws } = await connectWS(port);

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Get current session ID from state
    const stResp = await sendCmd(ws, "GET_STATE");
    // sessionId not directly in state; get from welcome. Use query to test scope.

    // Play 2 hands
    for (let i = 0; i < 2; i++) {
      const ep = collectUntilHandEnd(ws);
      await sendCmd(ws, "START_HAND");
      await playShowdownViaWS(ws);
      await ep;
    }

    // All stats
    const allStats = await sendCmd(ws, "GET_ACTOR_STATS", { actorId: aliceId });
    check("T6: all stats handsDealt = 2", allStats.state.stats.handsDealt === 2);

    // Session-scoped query (find the sessionId from a hand)
    const hands = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    const sid = hands.state.hands[0].sessionId;
    const scopedStats = await sendCmd(ws, "GET_ACTOR_STATS", { actorId: aliceId, sessionId: sid });
    check("T6: session-scoped stats ok", scopedStats.ok);
    check("T6: scoped handsDealt = 2", scopedStats.state.stats.handsDealt === 2);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════

  console.log(`\n*** STUDY CLIENT TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
