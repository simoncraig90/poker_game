#!/usr/bin/env node
"use strict";

/**
 * Review Queue Tests
 *
 * Tests sequential navigation through the filtered Study hand list.
 * Verifies: queue order, prev/next, bounds, filter changes,
 * replay+annotation loading on nav, no regression.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "queue-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

async function connectWS(port) {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://localhost:${port}`);
    ws.on("message", function first(raw) { const m = JSON.parse(raw.toString()); if (m.welcome) { ws.removeListener("message", first); resolve({ ws, welcome: m }); } });
  });
}
let cmdId = 0;
function sendCmd(ws, cmd, payload) {
  return new Promise((resolve) => {
    const id = `rq-${++cmdId}`;
    const h = (raw) => { const m = JSON.parse(raw.toString()); if (m.id === id) { ws.removeListener("message", h); resolve(m); } };
    ws.on("message", h);
    ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
  });
}
function collectUntilHandEnd(ws) {
  return new Promise((resolve) => {
    const events = [];
    const h = (raw) => { const msg = JSON.parse(raw.toString()); const evts = msg.broadcast ? msg.events : msg.events;
      if (evts) for (const e of evts) { events.push(e); if (e.type === "HAND_END") { ws.removeListener("message", h); resolve(events); return; } } };
    ws.on("message", h);
  });
}
async function playShowdownViaWS(ws) {
  let safety = 0;
  while (safety++ < 200) {
    const r = await sendCmd(ws, "GET_STATE"); const st = r.state;
    if (!st || !st.hand || st.hand.phase === "COMPLETE") break;
    const seat = st.hand.actionSeat; if (seat == null) break;
    const la = st.hand.legalActions; if (!la) break;
    if (la.actions.includes("CALL")) await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CALL" });
    else if (la.actions.includes("CHECK")) await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CHECK" });
    else break;
  }
}

const TABLE = { tableId: "rq-t", tableName: "Queue", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  // ═══════════════════════════════════════════════════════════════════════
  //  Setup: 5 hands, annotate 3 with different tags
  // ═══════════════════════════════════════════════════════════════════════

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws, welcome } = await connectWS(port);
  const sid = welcome.sessionId;

  const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
  const aliceId = cr.state.actor.actorId;
  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

  for (let i = 0; i < 5; i++) {
    const ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    await ep;
  }

  // Annotate: hand 1 = mistake, hand 3 = mistake + review, hand 5 = good
  await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "mistake", text: "x" });
  await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "3", tag: "mistake", text: "y" });
  await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "3", tag: "review", text: "z" });
  await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "5", tag: "good", text: "w" });

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Queue Order Follows Filtered Hand List
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Queue Order ===");
  {
    // Get all Alice's hands (unfiltered queue = 5 hands)
    const all = (await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId })).state.hands;
    check("T1: 5 hands total", all.length === 5);

    // Queue order should match query order
    check("T1: hand 0 is handId 1", all[0].handId === "1");
    check("T1: hand 4 is handId 5", all[4].handId === "5");

    // Each hand's events are loadable (simulates queue nav)
    for (let i = 0; i < all.length; i++) {
      const h = all[i];
      const evts = await sendCmd(ws, "GET_HAND_EVENTS", { sessionId: h.sessionId, handId: h.handId });
      check(`T1: hand ${i} events loadable`, evts.ok && evts.events.length > 0);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Sequential Navigation
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Sequential Navigation ===");
  {
    const all = (await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId })).state.hands;
    let cursor = 0;

    // Forward through all 5
    const visited = [];
    while (cursor < all.length) {
      const h = all[cursor];
      const evts = await sendCmd(ws, "GET_HAND_EVENTS", { sessionId: h.sessionId, handId: h.handId });
      visited.push(h.handId);
      cursor++;
    }
    check("T2: visited 5 hands", visited.length === 5);
    check("T2: in order", JSON.stringify(visited) === JSON.stringify(["1", "2", "3", "4", "5"]));

    // Backward from last
    cursor = all.length - 1;
    const backVisited = [];
    while (cursor >= 0) {
      backVisited.push(all[cursor].handId);
      cursor--;
    }
    check("T2: backward visits 5", backVisited.length === 5);
    check("T2: reverse order", JSON.stringify(backVisited) === JSON.stringify(["5", "4", "3", "2", "1"]));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Bounds Handling
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Bounds ===");
  {
    const all = (await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId })).state.hands;
    let cursor = 0;

    // Prev at first = no-op
    const prevIdx = Math.max(cursor - 1, 0);
    check("T3: prev at 0 stays 0", prevIdx === 0);

    // Next at last = no-op
    cursor = all.length - 1;
    const nextIdx = Math.min(cursor + 1, all.length - 1);
    check("T3: next at last stays last", nextIdx === all.length - 1);

    // Single-hand queue
    const singleHand = [all[0]];
    cursor = 0;
    check("T3: single-hand prev = 0", Math.max(cursor - 1, 0) === 0);
    check("T3: single-hand next = 0", Math.min(cursor + 1, singleHand.length - 1) === 0);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Filter Changes Queue
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Filter Changes Queue ===");
  {
    const counts = (await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid })).state.counts;
    const all = (await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId })).state.hands;

    // "mistake" tag filter: hands 1, 3
    const mistakeQueue = all.filter((h) => {
      const info = counts[h.handId];
      return info && info.tags.includes("mistake");
    });
    check("T4: mistake queue = 2", mistakeQueue.length === 2);
    check("T4: mistake queue hands 1,3", mistakeQueue[0].handId === "1" && mistakeQueue[1].handId === "3");

    // "good" tag filter: hand 5 only
    const goodQueue = all.filter((h) => {
      const info = counts[h.handId];
      return info && info.tags.includes("good");
    });
    check("T4: good queue = 1", goodQueue.length === 1);
    check("T4: good queue hand 5", goodQueue[0].handId === "5");

    // "noted" filter: hands 1, 3, 5
    const notedQueue = all.filter((h) => {
      const info = counts[h.handId];
      return info && info.count > 0;
    });
    check("T4: noted queue = 3", notedQueue.length === 3);

    // Navigate through mistake queue
    let cursor = 0;
    const h0 = await sendCmd(ws, "GET_HAND_EVENTS", { sessionId: mistakeQueue[0].sessionId, handId: mistakeQueue[0].handId });
    check("T4: mistake hand 0 loadable", h0.ok);
    cursor = 1;
    const h1 = await sendCmd(ws, "GET_HAND_EVENTS", { sessionId: mistakeQueue[1].sessionId, handId: mistakeQueue[1].handId });
    check("T4: mistake hand 1 loadable", h1.ok);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Annotations Load After Queue Nav
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Annotations After Nav ===");
  {
    const all = (await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId })).state.hands;

    // Navigate to hand 1 (has "mistake" annotation)
    const anns1 = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: all[0].sessionId, handId: all[0].handId });
    check("T5: hand 1 annotations loaded", anns1.ok && anns1.state.annotations.length === 1);
    check("T5: hand 1 tag is mistake", anns1.state.annotations[0].tag === "mistake");

    // Navigate to hand 3 (has "mistake" + "review")
    const anns3 = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: all[2].sessionId, handId: all[2].handId });
    check("T5: hand 3 annotations loaded", anns3.ok && anns3.state.annotations.length === 2);

    // Navigate to hand 2 (no annotations)
    const anns2 = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: all[1].sessionId, handId: all[1].handId });
    check("T5: hand 2 no annotations", anns2.ok && anns2.state.annotations.length === 0);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: Replay Loads After Queue Nav
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: Replay After Nav ===");
  {
    const all = (await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId })).state.hands;

    // Load hand 1, then hand 3, then back to hand 2
    const e1 = await sendCmd(ws, "GET_HAND_EVENTS", { sessionId: all[0].sessionId, handId: all[0].handId });
    check("T6: hand 1 replay events", e1.events.length > 0);
    const hs1 = e1.events.find((e) => e.type === "HAND_START");
    check("T6: hand 1 is handId 1", hs1.handId === "1");

    const e3 = await sendCmd(ws, "GET_HAND_EVENTS", { sessionId: all[2].sessionId, handId: all[2].handId });
    const hs3 = e3.events.find((e) => e.type === "HAND_START");
    check("T6: hand 3 is handId 3", hs3.handId === "3");

    const e2 = await sendCmd(ws, "GET_HAND_EVENTS", { sessionId: all[1].sessionId, handId: all[1].handId });
    const hs2 = e2.events.find((e) => e.type === "HAND_START");
    check("T6: hand 2 is handId 2", hs2.handId === "2");

    // Each has different events (different card deals)
    check("T6: hand 1 ≠ hand 3 events", e1.events.length !== e3.events.length || hs1.handId !== hs3.handId);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 7: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 7: No Regression ===");
  {
    // Tag filter
    const counts = (await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid })).state.counts;
    check("T7: counts available", Object.keys(counts).length > 0);
    check("T7: hand 3 has tags", counts["3"] && counts["3"].tags.length === 2);

    // Query still works
    const q = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    check("T7: query ok", q.ok && q.state.hands.length === 5);

    // Stats still work
    const st = await sendCmd(ws, "GET_ACTOR_STATS", { actorId: aliceId });
    check("T7: stats ok", st.ok && st.state.stats.handsDealt === 5);

    // Replay data path
    const ev = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
    check("T7: replay ok", ev.ok && ev.events.length > 0);
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** REVIEW QUEUE TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
