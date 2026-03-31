#!/usr/bin/env node
"use strict";

/**
 * Study Export + Session Filter Tests
 *
 * Tests: session-scoped queries via WS, export data shape,
 * multi-session filtering correctness.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "study-exp-" + Date.now());
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
    const id = `se-${++cmdId}`;
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

const TABLE = { tableId: "se-t", tableName: "Export", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Session filter via WS
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Session Filter ===");
  {
    const port = 9200 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t1", "sessions"), actorsDir: path.join(testDir, "t1", "actors"), table: TABLE });
    const { ws, welcome: w1 } = await connectWS(port);
    const sid1 = w1.sessionId;

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play 2 hands in session 1
    for (let i = 0; i < 2; i++) {
      const ep = collectUntilHandEnd(ws);
      await sendCmd(ws, "START_HAND");
      await playShowdownViaWS(ws);
      await ep;
    }

    // Archive and start session 2
    await sendCmd(ws, "ARCHIVE_SESSION");
    // Reconnect to get new session welcome
    ws.close();
    const { ws: ws2, welcome: w2 } = await connectWS(port);
    const sid2 = w2.sessionId;

    await sendCmd(ws2, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws2, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play 3 hands in session 2
    for (let i = 0; i < 3; i++) {
      const ep = collectUntilHandEnd(ws2);
      await sendCmd(ws2, "START_HAND");
      await playShowdownViaWS(ws2);
      await ep;
    }

    // Unfiltered: 5 hands for Alice
    const all = await sendCmd(ws2, "QUERY_HANDS", { actorId: aliceId });
    check("T1: all sessions = 5 hands", all.state.hands.length === 5);

    // Session 1 only: 2 hands
    const s1 = await sendCmd(ws2, "QUERY_HANDS", { actorId: aliceId, sessionId: sid1 });
    check("T1: session 1 = 2 hands", s1.state.hands.length === 2);
    check("T1: all from session 1", s1.state.hands.every((h) => h.sessionId === sid1));

    // Session 2 only: 3 hands
    const s2 = await sendCmd(ws2, "QUERY_HANDS", { actorId: aliceId, sessionId: sid2 });
    check("T1: session 2 = 3 hands", s2.state.hands.length === 3);
    check("T1: all from session 2", s2.state.hands.every((h) => h.sessionId === sid2));

    // Session list available for populating dropdown
    const sl = await sendCmd(ws2, "GET_SESSION_LIST");
    check("T1: session list has 2 entries", sl.state.sessions.length === 2);

    // Stats scoped to session 1
    const stats1 = await sendCmd(ws2, "GET_ACTOR_STATS", { actorId: aliceId, sessionId: sid1 });
    check("T1: session 1 stats handsDealt = 2", stats1.state.stats.handsDealt === 2);

    // Stats scoped to session 2
    const stats2 = await sendCmd(ws2, "GET_ACTOR_STATS", { actorId: aliceId, sessionId: sid2 });
    check("T1: session 2 stats handsDealt = 3", stats2.state.stats.handsDealt === 3);

    ws2.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Export Data Shape
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Export Data Shape ===");
  {
    const port = 9300 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t2", "sessions"), actorsDir: path.join(testDir, "t2", "actors"), table: TABLE });
    const { ws } = await connectWS(port);

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    const ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    await ep;

    // Query hands — verify the export-relevant fields exist
    const qr = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
    const hand = qr.state.hands[0];

    check("T2: has sessionId", typeof hand.sessionId === "string");
    check("T2: has handId", typeof hand.handId === "string");
    check("T2: has position", typeof hand.position === "string");
    check("T2: has result", typeof hand.result === "string");
    check("T2: has netResult", typeof hand.netResult === "number");
    check("T2: has showdown", typeof hand.showdown === "boolean");
    check("T2: has startStack", typeof hand.startStack === "number");
    check("T2: has totalInvested", typeof hand.totalInvested === "number");
    check("T2: has totalWon", typeof hand.totalWon === "number");
    check("T2: has handRank (or null)", hand.handRank === null || typeof hand.handRank === "string");

    // Simulate the export format (tab-separated)
    const header = "session\thand\tposition\tresult\tnet\tshowdown\thandRank\tstartStack\tinvested\twon";
    const row = [hand.sessionId, hand.handId, hand.position, hand.result, hand.netResult, hand.showdown, hand.handRank || "", hand.startStack, hand.totalInvested, hand.totalWon].join("\t");
    const tsv = header + "\n" + row;

    check("T2: TSV has header", tsv.split("\n")[0].includes("session\thand"));
    check("T2: TSV has data row", tsv.split("\n").length === 2);
    check("T2: data row has 10 columns", row.split("\t").length === 10);

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Combined Session + Showdown Filter
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Combined Filters ===");
  {
    const port = 9350 + Math.floor(Math.random() * 100);
    const srv = startServer({ port, dataDir: path.join(testDir, "t3", "sessions"), actorsDir: path.join(testDir, "t3", "actors"), table: TABLE });
    const { ws, welcome } = await connectWS(port);
    const sid = welcome.sessionId;

    const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
    const aliceId = cr.state.actor.actorId;
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

    // Play 1 fold-out + 1 showdown
    {
      const ep = collectUntilHandEnd(ws);
      await sendCmd(ws, "START_HAND");
      const st = (await sendCmd(ws, "GET_STATE")).state;
      await sendCmd(ws, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });
      await ep;
    }
    {
      const ep = collectUntilHandEnd(ws);
      await sendCmd(ws, "START_HAND");
      await playShowdownViaWS(ws);
      await ep;
    }

    // Session + showdown
    const sdOnly = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, sessionId: sid, showdown: true });
    check("T3: session + showdown = 1", sdOnly.state.hands.length === 1);
    check("T3: is showdown", sdOnly.state.hands[0].showdown);

    // Session + fold-out
    const foOnly = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, sessionId: sid, showdown: false });
    check("T3: session + fold-out = 1", foOnly.state.hands.length === 1);
    check("T3: is fold-out", !foOnly.state.hands[0].showdown);

    // Session + won
    const wonOnly = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId, sessionId: sid, result: "won" });
    check("T3: won hands have result=won", wonOnly.state.hands.every((h) => h.result === "won"));

    ws.close();
    srv.close();
  }

  // ═══════════════════════════════════════════════════════════════════════

  console.log(`\n*** STUDY EXPORT TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
