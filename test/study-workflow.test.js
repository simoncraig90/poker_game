#!/usr/bin/env node
"use strict";

/**
 * Phase 9 — Study Workflow Tightening Tests
 *
 * Tests: click-through from study list to hand detail,
 * position filter, date range filter, heads-up labels.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");
const { derivePosition } = require("../src/api/query");

const testDir = path.join(__dirname, "..", "test-output", "study-wf-" + Date.now());
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
    const id = `sw-${++cmdId}`;
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

const TABLE = { tableId: "sw-t", tableName: "StudyWF", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Click-Through — GET_HAND_EVENTS by sessionId + handId
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Study Click-Through ===");
  {
    const port = 9200 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t1", "sessions"), actorsDir: path.join(testDir, "t1", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play a showdown hand
    let ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    await ep;

    // Query Alice's hands
    const qr = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    check("T1: query returns hand", qr.state.hands.length >= 1);
    const hand = qr.state.hands[0];

    // Click-through: fetch hand events using sessionId + handId from query result
    const dr = await sendCmd(ws, "GET_HAND_EVENTS", { sessionId: hand.sessionId, handId: hand.handId });
    check("T1: GET_HAND_EVENTS ok", dr.ok);
    check("T1: events returned", dr.events.length > 0);

    // Verify events contain the expected types for a showdown hand
    const types = dr.events.map((e) => e.type);
    check("T1: has HAND_START", types.includes("HAND_START"));
    check("T1: has SHOWDOWN_REVEAL", types.includes("SHOWDOWN_REVEAL"));
    check("T1: has HAND_END", types.includes("HAND_END"));

    // Verify the sessionId in query result matches the session
    check("T1: sessionId in query result", typeof hand.sessionId === "string" && hand.sessionId.length > 0);
    check("T1: handId in query result", typeof hand.handId === "string");

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Position Filter
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Position Filter ===");
  {
    const port = 9300 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t2", "sessions"), actorsDir: path.join(testDir, "t2", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play 4 hands (button rotates, so Alice alternates positions)
    for (let i = 0; i < 4; i++) {
      const ep2 = collectUntilHandEnd(ws);
      await sendCmd(ws, "START_HAND");
      await playShowdownViaWS(ws);
      await ep2;
    }

    // Query all of Alice's hands
    const all = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    check("T2: Alice has 4 hands", all.state.hands.length === 4);

    // Get positions
    const positions = all.state.hands.map((h) => h.position);
    check("T2: positions are valid", positions.every((p) => ["SB", "BB", "BTN", "UTG", "MP", "CO"].includes(p)));

    // Filter by SB
    const sbHands = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, position: "SB" });
    check("T2: SB filter returns subset", sbHands.state.hands.length <= 4);
    check("T2: all SB", sbHands.state.hands.every((h) => h.position === "SB"));

    // Filter by BB
    const bbHands = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, position: "BB" });
    check("T2: BB filter returns subset", bbHands.state.hands.length <= 4);
    check("T2: all BB", bbHands.state.hands.every((h) => h.position === "BB"));

    // SB + BB should account for all hands in heads-up
    check("T2: SB + BB = all in HU", sbHands.state.hands.length + bbHands.state.hands.length === 4);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Date Range Filter
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Date Range Filter ===");
  {
    const port = 9350 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t3", "sessions"), actorsDir: path.join(testDir, "t3", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play 2 hands (all will have timestamps within the current second)
    for (let i = 0; i < 2; i++) {
      const ep3 = collectUntilHandEnd(ws);
      await sendCmd(ws, "START_HAND");
      await playShowdownViaWS(ws);
      await ep3;
    }

    const now = Date.now();

    // "after" far in the past → all hands
    const pastFilter = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, after: 0 });
    check("T3: after=0 returns all", pastFilter.state.hands.length === 2);

    // "after" in the future → no hands
    const futureFilter = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, after: now + 86400000 });
    check("T3: after=future returns none", futureFilter.state.hands.length === 0);

    // "before" far in the future → all hands
    const beforeFuture = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, before: now + 86400000 });
    check("T3: before=future returns all", beforeFuture.state.hands.length === 2);

    // "before" in the past → no hands
    const beforePast = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, before: 1000 });
    check("T3: before=past returns none", beforePast.state.hands.length === 0);

    // Narrow range around now → all hands (they were just played)
    const narrow = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, after: now - 60000, before: now + 60000 });
    check("T3: narrow range around now returns all", narrow.state.hands.length === 2);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Heads-Up Position Labels
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Heads-Up Position Labels ===");
  {
    // Direct unit test of derivePosition for HU
    check("T4: HU seat 0 btn=0 → SB", derivePosition(0, 0, [0, 1]) === "SB");
    check("T4: HU seat 1 btn=0 → BB", derivePosition(1, 0, [0, 1]) === "BB");
    check("T4: HU seat 1 btn=1 → SB", derivePosition(1, 1, [0, 1]) === "SB");
    check("T4: HU seat 0 btn=1 → BB", derivePosition(0, 1, [0, 1]) === "BB");

    // Non-contiguous seats
    check("T4: HU seat 2 btn=2 → SB", derivePosition(2, 2, [2, 5]) === "SB");
    check("T4: HU seat 5 btn=2 → BB", derivePosition(5, 2, [2, 5]) === "BB");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: No Regression — Existing Query/Stats Still Work
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: No Regression ===");
  {
    const port = 9400 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t5", "sessions"), actorsDir: path.join(testDir, "t5", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play 3 hands
    for (let i = 0; i < 3; i++) {
      const ep5 = collectUntilHandEnd(ws);
      await sendCmd(ws, "START_HAND");
      await playShowdownViaWS(ws);
      await ep5;
    }

    // Unfiltered query
    const all = await sendCmd(ws, "QUERY_HANDS", {});
    check("T5: unfiltered = 6 (3 hands × 2 players)", all.state.hands.length === 6);

    // Actor query
    const alice = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    check("T5: Alice = 3", alice.state.hands.length === 3);

    // Stats
    const stats = await sendCmd(ws, "GET_ACTOR_STATS", { actorId: aliceId });
    check("T5: stats ok", stats.ok);
    check("T5: handsDealt = 3", stats.state.stats.handsDealt === 3);
    check("T5: vpip in range", stats.state.stats.vpip >= 0 && stats.state.stats.vpip <= 1);

    // Showdown filter
    const sd = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, showdown: true });
    check("T5: showdown filter works", sd.state.hands.every((h) => h.showdown));

    // Result filter
    const won = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, result: "won" });
    check("T5: won filter works", won.state.hands.every((h) => h.result === "won"));

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════

  console.log(`\n*** STUDY WORKFLOW TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
