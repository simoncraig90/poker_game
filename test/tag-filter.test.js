#!/usr/bin/env node
"use strict";

/**
 * Tag-Based Study Filtering Tests
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "tag-" + Date.now());
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
    const id = `tf-${++cmdId}`;
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

const TABLE = { tableId: "tf-t", tableName: "TagFilter", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Per-Hand Tag Presence in Counts
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Tag Presence in Counts ===");
  {
    const port = 9200 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t1", "sessions"), actorsDir: path.join(testDir, "t1", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid = welcome.sessionId;

    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    for (let i = 0; i < 3; i++) { const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep; }

    // Hand 1: mistake + interesting
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "mistake", text: "bad call" });
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "interesting", text: "unusual line" });
    // Hand 2: review
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "2", tag: "review", text: "need to revisit" });
    // Hand 3: no annotations

    const c = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T1: counts ok", c.ok);

    const h1 = c.state.counts["1"];
    check("T1: hand 1 count = 2", h1 && h1.count === 2);
    check("T1: hand 1 tags include mistake", h1 && h1.tags.includes("mistake"));
    check("T1: hand 1 tags include interesting", h1 && h1.tags.includes("interesting"));
    check("T1: hand 1 tags length = 2", h1 && h1.tags.length === 2);

    const h2 = c.state.counts["2"];
    check("T1: hand 2 count = 1", h2 && h2.count === 1);
    check("T1: hand 2 tags = [review]", h2 && h2.tags.length === 1 && h2.tags[0] === "review");

    check("T1: hand 3 absent", !c.state.counts["3"]);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Tag Filter Returns Only Matching Hands
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Tag Filter ===");
  {
    const port = 9300 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t2", "sessions"), actorsDir: path.join(testDir, "t2", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid = welcome.sessionId;

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    for (let i = 0; i < 4; i++) { const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep; }

    // Hand 1: mistake
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "mistake", text: "x" });
    // Hand 2: mistake + good
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "2", tag: "mistake", text: "y" });
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "2", tag: "good", text: "z" });
    // Hand 3: good
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "3", tag: "good", text: "w" });
    // Hand 4: no annotations

    // Get counts + query
    const counts = (await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid })).state.counts;
    const all = (await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId })).state.hands;

    // Simulate tag filter: "mistake"
    const mistakeHands = all.filter((h) => {
      const info = counts[h.handId];
      return info && info.tags.includes("mistake");
    });
    check("T2: mistake filter = 2 hands", mistakeHands.length === 2);
    check("T2: mistake hands are 1 and 2", mistakeHands.some((h) => h.handId === "1") && mistakeHands.some((h) => h.handId === "2"));

    // Simulate tag filter: "good"
    const goodHands = all.filter((h) => {
      const info = counts[h.handId];
      return info && info.tags.includes("good");
    });
    check("T2: good filter = 2 hands", goodHands.length === 2);
    check("T2: good hands are 2 and 3", goodHands.some((h) => h.handId === "2") && goodHands.some((h) => h.handId === "3"));

    // Simulate tag filter: "review" (none)
    const reviewHands = all.filter((h) => {
      const info = counts[h.handId];
      return info && info.tags.includes("review");
    });
    check("T2: review filter = 0", reviewHands.length === 0);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Multiple Tags on One Hand
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Multiple Tags One Hand ===");
  {
    const port = 9350 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t3", "sessions"), actorsDir: path.join(testDir, "t3", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid = welcome.sessionId;

    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;

    // Add 3 annotations with different tags to same hand
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "mistake", text: "a" });
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "interesting", text: "b" });
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "mistake", text: "c" }); // duplicate tag

    const counts = (await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid })).state.counts;
    const h1 = counts["1"];
    check("T3: count = 3", h1 && h1.count === 3);
    check("T3: tags deduplicated = 2", h1 && h1.tags.length === 2);
    check("T3: has mistake", h1 && h1.tags.includes("mistake"));
    check("T3: has interesting", h1 && h1.tags.includes("interesting"));

    // Hand matches both "mistake" and "interesting" filters
    check("T3: matches mistake filter", h1 && h1.tags.includes("mistake"));
    check("T3: matches interesting filter", h1 && h1.tags.includes("interesting"));
    check("T3: does not match review filter", h1 && !h1.tags.includes("review"));

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Delete Updates Tag Results
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Delete Updates Tags ===");
  {
    const port = 9400 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t4", "sessions"), actorsDir: path.join(testDir, "t4", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid = welcome.sessionId;

    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;

    // Add mistake + good
    const a1 = await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "mistake", text: "x" });
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "good", text: "y" });

    let counts = (await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid })).state.counts;
    check("T4: before delete: 2 tags", counts["1"] && counts["1"].tags.length === 2);

    // Delete the "mistake" annotation
    await sendCmd(ws, "DELETE_ANNOTATION", { sessionId: sid, annotationId: a1.state.annotation.id });
    counts = (await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid })).state.counts;
    check("T4: after delete: 1 tag", counts["1"] && counts["1"].tags.length === 1);
    check("T4: remaining tag is good", counts["1"] && counts["1"].tags[0] === "good");
    check("T4: mistake gone from filter", counts["1"] && !counts["1"].tags.includes("mistake"));

    // Delete the "good" annotation too
    const anns = (await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" })).state.annotations;
    await sendCmd(ws, "DELETE_ANNOTATION", { sessionId: sid, annotationId: anns[0].id });
    counts = (await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid })).state.counts;
    check("T4: after all deleted: hand absent", !counts["1"]);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Multi-Session Tag Isolation
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Multi-Session Isolation ===");
  {
    const port = 9450 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t5", "sessions"), actorsDir: path.join(testDir, "t5", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid1 = welcome.sessionId;

    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    let ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid1, handId: "1", tag: "mistake", text: "s1" });

    // Archive and start session 2
    await sendCmd(ws, "ARCHIVE_SESSION");
    ws.close();
    const { ws: ws2, welcome: w2 } = await connectWS(port);
    const sid2 = w2.sessionId;
    await sendCmd(ws2, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws2, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    ep = collectUntilHandEnd(ws2); await sendCmd(ws2, "START_HAND"); await playShowdownViaWS(ws2); await ep;
    await sendCmd(ws2, "ADD_ANNOTATION", { sessionId: sid2, handId: "1", tag: "good", text: "s2" });

    // Session 1 tags
    const c1 = (await sendCmd(ws2, "GET_ANNOTATION_COUNTS", { sessionId: sid1 })).state.counts;
    check("T5: s1 hand 1 has mistake", c1["1"] && c1["1"].tags.includes("mistake"));
    check("T5: s1 hand 1 does not have good", c1["1"] && !c1["1"].tags.includes("good"));

    // Session 2 tags
    const c2 = (await sendCmd(ws2, "GET_ANNOTATION_COUNTS", { sessionId: sid2 })).state.counts;
    check("T5: s2 hand 1 has good", c2["1"] && c2["1"].tags.includes("good"));
    check("T5: s2 hand 1 does not have mistake", c2["1"] && !c2["1"].tags.includes("mistake"));

    ws2.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: No Regression ===");
  {
    const port = 9500 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t6", "sessions"), actorsDir: path.join(testDir, "t6", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
    const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;

    // Replay
    const hevt = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
    check("T6: replay data ok", hevt.ok && hevt.events.length > 0);

    // Query
    const q = await sendCmd(ws, "QUERY_HANDS", {});
    check("T6: query ok", q.ok && q.state.hands.length > 0);

    // Annotations
    const sid = (await sendCmd(ws, "GET_STATE")).state ? null : null;
    const qs = (await sendCmd(ws, "QUERY_HANDS", {})).state.hands[0];
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: qs.sessionId, handId: qs.handId, tag: "review", text: "test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: qs.sessionId, handId: qs.handId });
    check("T6: annotations ok", anns.ok && anns.state.annotations.length >= 1);

    // Stats
    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Test" });
    const stats = await sendCmd(ws, "GET_ACTOR_STATS", { actorId: cr.state.actor.actorId });
    check("T6: stats ok", stats.ok);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════

  console.log(`\n*** TAG FILTER TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
