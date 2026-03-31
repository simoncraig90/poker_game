#!/usr/bin/env node
"use strict";

/**
 * Annotation Visibility in Study Hand List Tests
 *
 * Tests: annotation counts in hand list, noted-only filter,
 * count updates after add/delete, multi-session isolation.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "ann-vis-" + Date.now());
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
    const id = `av-${++cmdId}`;
    const h = (raw) => { const m = JSON.parse(raw.toString()); if (m.id === id) { ws.removeListener("message", h); resolve(m); } };
    ws.on("message", h);
    ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
  });
}
function collectUntilHandEnd(ws) {
  return new Promise((resolve) => {
    const events = [];
    const h = (raw) => {
      const msg = JSON.parse(raw.toString());
      const evts = msg.broadcast ? msg.events : msg.events;
      if (evts) for (const e of evts) { events.push(e); if (e.type === "HAND_END") { ws.removeListener("message", h); resolve(events); return; } }
    };
    ws.on("message", h);
  });
}
async function playShowdownViaWS(ws) {
  let safety = 0;
  while (safety++ < 200) {
    const r = await sendCmd(ws, "GET_STATE");
    const st = r.state;
    if (!st || !st.hand || st.hand.phase === "COMPLETE") break;
    const seat = st.hand.actionSeat;
    if (seat == null) break;
    const la = st.hand.legalActions;
    if (!la) break;
    if (la.actions.includes("CALL")) await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CALL" });
    else if (la.actions.includes("CHECK")) await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CHECK" });
    else break;
  }
}

const TABLE = { tableId: "av-t", tableName: "AnnVis", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: GET_ANNOTATION_COUNTS returns correct counts
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Annotation Counts ===");
  {
    const port = 9200 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t1", "sessions"), actorsDir: path.join(testDir, "t1", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid = welcome.sessionId;

    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play 3 hands
    for (let i = 0; i < 3; i++) {
      const ep = collectUntilHandEnd(ws);
      await sendCmd(ws, "START_HAND");
      await playShowdownViaWS(ws);
      await ep;
    }

    // No annotations yet
    const c0 = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T1: initial counts empty", c0.ok && Object.keys(c0.state.counts).length === 0);

    // Annotate hand 1 (2 notes) and hand 3 (1 note)
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", text: "note 1a" });
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", text: "note 1b" });
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "3", text: "note 3a" });

    const c1 = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T1: counts ok", c1.ok);
    check("T1: hand 1 has 2", c1.state.counts["1"] && c1.state.counts["1"].count === 2);
    check("T1: hand 2 has 0 (absent)", !c1.state.counts["2"]);
    check("T1: hand 3 has 1", c1.state.counts["3"] && c1.state.counts["3"].count === 1);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Counts update after delete
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Counts After Delete ===");
  {
    const port = 9300 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t2", "sessions"), actorsDir: path.join(testDir, "t2", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid = welcome.sessionId;

    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    const ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    await ep;

    // Add 2 annotations
    const a1 = await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", text: "first" });
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", text: "second" });

    const c1 = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T2: before delete = 2", c1.state.counts["1"] && c1.state.counts["1"].count === 2);

    // Delete one
    await sendCmd(ws, "DELETE_ANNOTATION", { sessionId: sid, annotationId: a1.state.annotation.id });
    const c2 = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T2: after delete = 1", c2.state.counts["1"] && c2.state.counts["1"].count === 1);

    // Delete the other (get its ID first)
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    await sendCmd(ws, "DELETE_ANNOTATION", { sessionId: sid, annotationId: anns.state.annotations[0].id });
    const c3 = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T2: after deleting all = absent", !c3.state.counts["1"]);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Multi-session isolation
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Multi-Session Isolation ===");
  {
    const port = 9350 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t3", "sessions"), actorsDir: path.join(testDir, "t3", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid1 = welcome.sessionId;

    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    let ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    await ep;

    // Annotate in session 1
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid1, handId: "1", text: "s1 note" });

    // Archive and start session 2
    await sendCmd(ws, "ARCHIVE_SESSION");
    ws.close();
    const { ws: ws2, welcome: w2 } = await connectWS(port);
    const sid2 = w2.sessionId;

    await sendCmd(ws2, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws2, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    ep = collectUntilHandEnd(ws2);
    await sendCmd(ws2, "START_HAND");
    await playShowdownViaWS(ws2);
    await ep;

    // Session 1 counts
    const c1 = await sendCmd(ws2, "GET_ANNOTATION_COUNTS", { sessionId: sid1 });
    check("T3: session 1 has counts", c1.state.counts["1"] && c1.state.counts["1"].count === 1);

    // Session 2 counts — should be empty
    const c2 = await sendCmd(ws2, "GET_ANNOTATION_COUNTS", { sessionId: sid2 });
    check("T3: session 2 is empty", Object.keys(c2.state.counts).length === 0);

    // Annotate session 2
    await sendCmd(ws2, "ADD_ANNOTATION", { sessionId: sid2, handId: "1", text: "s2 note" });
    const c2b = await sendCmd(ws2, "GET_ANNOTATION_COUNTS", { sessionId: sid2 });
    check("T3: session 2 now has count", c2b.state.counts["1"] && c2b.state.counts["1"].count === 1);

    // Session 1 unchanged
    const c1b = await sendCmd(ws2, "GET_ANNOTATION_COUNTS", { sessionId: sid1 });
    check("T3: session 1 unchanged", c1b.state.counts["1"] && c1b.state.counts["1"].count === 1);

    ws2.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Noted-only filter simulation
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Noted-Only Filter ===");
  {
    const port = 9400 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t4", "sessions"), actorsDir: path.join(testDir, "t4", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid = welcome.sessionId;

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

    // Annotate hand 2 only
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "2", text: "annotated" });

    // Query all hands
    const all = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    check("T4: 3 total hands", all.state.hands.length === 3);

    // Get counts
    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });

    // Simulate noted-only filter: keep only hands where count > 0
    const noted = all.state.hands.filter((h) => {
      const sc = counts.state.counts || {};
      const info = sc[h.handId];
      return info && info.count > 0;
    });
    check("T4: noted-only filter returns 1", noted.length === 1);
    check("T4: noted hand is hand 2", noted[0].handId === "2");

    // Non-noted hands excluded
    const notNoted = all.state.hands.filter((h) => {
      const sc = counts.state.counts || {};
      const info = sc[h.handId];
      return !info || info.count === 0;
    });
    check("T4: 2 hands without notes", notNoted.length === 2);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: No regression — replay, export, session filter
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: No Regression ===");
  {
    const port = 9450 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t5", "sessions"), actorsDir: path.join(testDir, "t5", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid = welcome.sessionId;

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    const ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    await ep;

    // Replay data path
    const hevt = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
    check("T5: replay data ok", hevt.ok && hevt.events.length > 0);

    // Export data path (query)
    const q = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    check("T5: query ok", q.ok && q.state.hands.length === 1);

    // Session filter
    const q2 = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, sessionId: sid });
    check("T5: session filter ok", q2.ok && q2.state.hands.length === 1);

    // Stats
    const st = await sendCmd(ws, "GET_ACTOR_STATS", { actorId: aliceId });
    check("T5: stats ok", st.ok && st.state.stats.handsDealt === 1);

    // Annotations still work
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", text: "test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    check("T5: annotations ok", anns.ok && anns.state.annotations.length === 1);

    // Session list
    const sl = await sendCmd(ws, "GET_SESSION_LIST");
    check("T5: session list ok", sl.ok && sl.state.sessions.length >= 1);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: Error handling
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: Error Handling ===");
  {
    const port = 9500 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t6", "sessions"), actorsDir: path.join(testDir, "t6", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    const r = await sendCmd(ws, "GET_ANNOTATION_COUNTS", {});
    check("T6: missing sessionId → error", r.ok === false);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════

  console.log(`\n*** ANNOTATION VISIBILITY TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
