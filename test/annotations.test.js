#!/usr/bin/env node
"use strict";

/**
 * Annotation Persistence Tests
 *
 * Tests: add, load, delete, frame-linked vs hand-level,
 * hand-switch reset, no replay regression.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");
const { AnnotationStore } = require("../src/api/annotations");
const { SessionStorage } = require("../src/api/storage");

const testDir = path.join(__dirname, "..", "test-output", "ann-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

// ── WS helpers ────────────────────────────────────────────────────────────
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
    const id = `an-${++cmdId}`;
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

const TABLE = { tableId: "an-t", tableName: "Annotations", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  // ═══════════════════════════════════════════════════════════════════════
  //  Setup: Create a session with 2 hands
  // ═══════════════════════════════════════════════════════════════════════

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws, welcome } = await connectWS(port);
  const sid = welcome.sessionId;

  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

  // Hand 1: showdown
  let ep = collectUntilHandEnd(ws);
  await sendCmd(ws, "START_HAND");
  await playShowdownViaWS(ws);
  await ep;

  // Hand 2: fold-out
  ep = collectUntilHandEnd(ws);
  await sendCmd(ws, "START_HAND");
  let st = (await sendCmd(ws, "GET_STATE")).state;
  await sendCmd(ws, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });
  await ep;

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Add whole-hand annotation
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Whole-Hand Note ===");
  {
    const r = await sendCmd(ws, "ADD_ANNOTATION", {
      sessionId: sid, handId: "1",
      tag: "interesting", text: "Unusual line from Alice", frameIndex: null,
    });
    check("T1: add ok", r.ok);
    check("T1: has annotation", r.state && r.state.annotation);
    check("T1: has id", r.state.annotation.id.startsWith("ann-"));
    check("T1: frameIndex null", r.state.annotation.frameIndex === null);
    check("T1: tag correct", r.state.annotation.tag === "interesting");
    check("T1: text correct", r.state.annotation.text === "Unusual line from Alice");
    check("T1: sessionId correct", r.state.annotation.sessionId === sid);
    check("T1: handId correct", r.state.annotation.handId === "1");
    check("T1: createdAt present", typeof r.state.annotation.createdAt === "string");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Add frame-linked annotation
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Frame-Linked Note ===");
  {
    const r = await sendCmd(ws, "ADD_ANNOTATION", {
      sessionId: sid, handId: "1",
      tag: "mistake", text: "Should have raised here",
      frameIndex: 5, street: "PREFLOP",
    });
    check("T2: add ok", r.ok);
    check("T2: frameIndex 5", r.state.annotation.frameIndex === 5);
    check("T2: street PREFLOP", r.state.annotation.street === "PREFLOP");
    check("T2: tag mistake", r.state.annotation.tag === "mistake");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Load annotations for hand
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Load Annotations ===");
  {
    const r = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    check("T3: load ok", r.ok);
    check("T3: 2 annotations", r.state.annotations.length === 2);
    check("T3: sorted by createdAt", r.state.annotations[0].createdAt <= r.state.annotations[1].createdAt);

    // One is whole-hand, one is frame-linked
    const wholeHand = r.state.annotations.find((a) => a.frameIndex === null);
    const frameLinked = r.state.annotations.find((a) => a.frameIndex === 5);
    check("T3: whole-hand note exists", !!wholeHand);
    check("T3: frame-linked note exists", !!frameLinked);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Annotations don't leak across hands
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Hand Isolation ===");
  {
    // Hand 2 should have no annotations
    const r = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "2" });
    check("T4: hand 2 has 0 annotations", r.state.annotations.length === 0);

    // Add one to hand 2
    await sendCmd(ws, "ADD_ANNOTATION", {
      sessionId: sid, handId: "2", tag: "review", text: "Quick fold",
    });
    const r2 = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "2" });
    check("T4: hand 2 now has 1", r2.state.annotations.length === 1);

    // Hand 1 still has 2
    const r1 = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    check("T4: hand 1 still has 2", r1.state.annotations.length === 2);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Delete annotation
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Delete Annotation ===");
  {
    const anns = (await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" })).state.annotations;
    const toDelete = anns[0].id;

    const dr = await sendCmd(ws, "DELETE_ANNOTATION", { sessionId: sid, annotationId: toDelete });
    check("T5: delete ok", dr.ok);

    const after = (await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" })).state.annotations;
    check("T5: now 1 annotation", after.length === 1);
    check("T5: deleted one is gone", !after.some((a) => a.id === toDelete));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: Persistence across reload
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: Persistence Across Reload ===");
  {
    // Add a note, close the connection, reconnect, check it's still there
    await sendCmd(ws, "ADD_ANNOTATION", {
      sessionId: sid, handId: "1", tag: "good", text: "Nice value bet",
    });

    ws.close();
    const { ws: ws2 } = await connectWS(port);

    const r = await sendCmd(ws2, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    check("T6: annotations persist after reconnect", r.state.annotations.length === 2); // 1 survived delete + 1 new

    const good = r.state.annotations.find((a) => a.tag === "good");
    check("T6: new annotation found", !!good);
    check("T6: text preserved", good && good.text === "Nice value bet");

    ws2.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 7: Error handling
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 7: Error Handling ===");
  {
    const { ws: ws3 } = await connectWS(port);

    const r1 = await sendCmd(ws3, "ADD_ANNOTATION", { handId: "1" });
    check("T7: missing sessionId → error", r1.ok === false);

    const r2 = await sendCmd(ws3, "ADD_ANNOTATION", { sessionId: sid });
    check("T7: missing handId → error", r2.ok === false);

    const r3 = await sendCmd(ws3, "GET_ANNOTATIONS", {});
    check("T7: GET missing fields → error", r3.ok === false);

    const r4 = await sendCmd(ws3, "DELETE_ANNOTATION", {});
    check("T7: DELETE missing fields → error", r4.ok === false);

    ws3.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 8: Replay still works (no regression)
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 8: Replay Not Broken ===");
  {
    const { ws: ws4 } = await connectWS(port);

    // GET_HAND_EVENTS still works
    const r = await sendCmd(ws4, "GET_HAND_EVENTS", { handId: "1" });
    check("T8: GET_HAND_EVENTS ok", r.ok);
    check("T8: events present", r.events.length > 0);
    check("T8: HAND_START in events", r.events.some((e) => e.type === "HAND_START"));
    check("T8: HAND_END in events", r.events.some((e) => e.type === "HAND_END"));

    // QUERY_HANDS still works
    const q = await sendCmd(ws4, "QUERY_HANDS", {});
    check("T8: QUERY_HANDS ok", q.ok);
    check("T8: hands present", q.state.hands.length > 0);

    ws4.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 9: Unit test — AnnotationStore directly
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 9: AnnotationStore Unit ===");
  {
    const storage = new SessionStorage(path.join(testDir, "unit-sessions"));
    const info = storage.create("unit-s1", TABLE);
    const store = new AnnotationStore(storage);

    // Add
    const a1 = store.add("unit-s1", "h1", { tag: "mistake", text: "bad call", frameIndex: 3, street: "FLOP" });
    check("T9: add returns annotation", a1.id.startsWith("ann-"));
    check("T9: fields set", a1.tag === "mistake" && a1.frameIndex === 3 && a1.street === "FLOP");

    const a2 = store.add("unit-s1", "h1", { text: "overall note" });
    const a3 = store.add("unit-s1", "h2", { text: "different hand" });

    // Get for hand
    const h1Notes = store.getForHand("unit-s1", "h1");
    check("T9: h1 has 2 notes", h1Notes.length === 2);

    const h2Notes = store.getForHand("unit-s1", "h2");
    check("T9: h2 has 1 note", h2Notes.length === 1);

    // Delete
    store.delete("unit-s1", a1.id);
    const afterDelete = store.getForHand("unit-s1", "h1");
    check("T9: after delete h1 has 1", afterDelete.length === 1);
    check("T9: deleted note gone", !afterDelete.some((a) => a.id === a1.id));

    // Nonexistent session
    const empty = store.getForHand("unit-nonexistent", "h1");
    check("T9: nonexistent session → empty", empty.length === 0);
  }

  // ═══════════════════════════════════════════════════════════════════════

  srv.close();

  console.log(`\n*** ANNOTATION TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
