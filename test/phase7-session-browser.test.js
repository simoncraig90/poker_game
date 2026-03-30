#!/usr/bin/env node
"use strict";

/**
 * Phase 7 Test: Session browser, recovery metadata, archive flow
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");
const { Session } = require("../src/api/session");
const { SessionStorage } = require("../src/api/storage");
const { CMD, command } = require("../src/api/commands");

const testDir = path.join(__dirname, "..", "test-output", "p7-" + Date.now());
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
    const id = `p7-${++cmdId}`;
    const handler = (raw) => { const m = JSON.parse(raw.toString()); if (m.id === id) { ws.removeListener("message", handler); resolve(m); } };
    ws.on("message", handler);
    ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
  });
}

async function run() {
  const port = 9400 + Math.floor(Math.random() * 100);
  const dataDir = path.join(testDir, "sessions");
  const TABLE = { tableId: "p7-t", tableName: "P7 Test", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 400, maxBuyIn: 1000 };

  // ── Test 1: Fresh session welcome has recovered=false ────────────────
  console.log("=== Test 1: Fresh session welcome ===");
  {
    const srv = startServer({ port, dataDir, table: TABLE });
    const { ws, welcome } = await connectWS(port);
    check("welcome received", welcome.welcome === true);
    check("recovered is false", welcome.recovered === false);
    check("voidedHands is empty", welcome.voidedHands.length === 0);

    // Seat + play 2 hands
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "A", buyIn: 1000 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "B", buyIn: 800 });
    await sendCmd(ws, "START_HAND");
    let st = (await sendCmd(ws, "GET_STATE")).state;
    await sendCmd(ws, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });
    await sendCmd(ws, "START_HAND");
    st = (await sendCmd(ws, "GET_STATE")).state;
    await sendCmd(ws, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });

    ws.close();
    srv.close();
  }

  // ── Test 2: Recovery welcome has recovered=true ──────────────────────
  console.log("\n=== Test 2: Recovery welcome ===");
  {
    const srv = startServer({ port: port + 1, dataDir, table: TABLE });
    check("server recovered", srv.wasRecovered === true);

    const { ws, welcome } = await connectWS(port + 1);
    check("welcome recovered flag", welcome.recovered === true);
    check("welcome has sessionId", welcome.sessionId != null);
    check("welcome state has players", welcome.state.seats[0].player.name === "A");
    check("welcome handsPlayed", welcome.state.handsPlayed === 2);
    check("voidedHands empty (clean recovery)", welcome.voidedHands.length === 0);

    ws.close();
    srv.close();
  }

  // ── Test 3: Mid-hand crash → voided hand in welcome ──────────────────
  console.log("\n=== Test 3: Mid-hand void in welcome ===");
  {
    // Start fresh for this test
    const dataDir3 = path.join(testDir, "sessions3");
    const srv1 = startServer({ port: port + 2, dataDir: dataDir3, table: TABLE });
    const { ws: ws1, welcome: w1 } = await connectWS(port + 2);

    await sendCmd(ws1, "SEAT_PLAYER", { seat: 0, name: "X", buyIn: 1000 });
    await sendCmd(ws1, "SEAT_PLAYER", { seat: 1, name: "Y", buyIn: 800 });
    await sendCmd(ws1, "SEAT_PLAYER", { seat: 3, name: "Z", buyIn: 600 });

    // Play 1 hand cleanly
    await sendCmd(ws1, "START_HAND");
    let st = (await sendCmd(ws1, "GET_STATE")).state;
    await sendCmd(ws1, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });
    st = (await sendCmd(ws1, "GET_STATE")).state;
    if (st.hand && st.hand.actionSeat != null) {
      await sendCmd(ws1, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });
    }

    // Start hand 2 but don't finish
    await sendCmd(ws1, "START_HAND");

    ws1.close();
    srv1.close();

    // Now truncate the events file to simulate mid-hand crash
    const storage3 = new SessionStorage(dataDir3);
    const active = storage3.findActive();
    const rawLog = fs.readFileSync(active.eventsPath, "utf8").trim().split("\n");
    // Find last HAND_START and keep only a few events after it
    let lastStartIdx = -1;
    for (let i = rawLog.length - 1; i >= 0; i--) {
      if (JSON.parse(rawLog[i]).type === "HAND_START") { lastStartIdx = i; break; }
    }
    const truncated = rawLog.slice(0, lastStartIdx + 3); // keep HAND_START + 2 events (blinds)
    fs.writeFileSync(active.eventsPath, truncated.join("\n") + "\n");

    // Recover
    const srv2 = startServer({ port: port + 3, dataDir: dataDir3, table: TABLE });
    check("voided recovery", srv2.wasRecovered === true);
    check("has voided hands", srv2.voidedHands.length === 1);

    const { ws: ws2, welcome: w2 } = await connectWS(port + 3);
    check("welcome recovered", w2.recovered === true);
    check("welcome voidedHands has entry", w2.voidedHands.length === 1);

    ws2.close();
    srv2.close();
  }

  // ── Test 4: Session list + archive + browse ──────────────────────────
  console.log("\n=== Test 4: Session list, archive, browse ===");
  {
    const dataDir4 = path.join(testDir, "sessions4");
    const srv = startServer({ port: port + 4, dataDir: dataDir4, table: TABLE });
    const { ws, welcome } = await connectWS(port + 4);

    // Seat + play
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "P", buyIn: 1000 });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Q", buyIn: 800 });
    await sendCmd(ws, "START_HAND");
    let st = (await sendCmd(ws, "GET_STATE")).state;
    await sendCmd(ws, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });

    // Session list before archive
    const list1 = await sendCmd(ws, "GET_SESSION_LIST");
    check("1 session before archive", list1.state.sessions.length === 1);
    check("session is active", list1.state.sessions[0].status === "active");

    // Archive — set up welcome listener BEFORE sending command
    const welcomePromise = new Promise((resolve) => {
      const handler = (raw) => {
        const m = JSON.parse(raw.toString());
        if (m.welcome) { ws.removeListener("message", handler); resolve(m); }
      };
      ws.on("message", handler);
    });

    const archResp = await sendCmd(ws, "ARCHIVE_SESSION");
    check("archive ok", archResp.ok === true);

    // Wait for the new welcome
    await welcomePromise;

    // Session list after archive
    const list2 = await sendCmd(ws, "GET_SESSION_LIST");
    check("2 sessions after archive", list2.state.sessions.length === 2);
    const statuses = list2.state.sessions.map((s) => s.status).sort();
    check("one active one complete", statuses[0] === "active" && statuses[1] === "complete");

    // Browse archived session's hands
    const completedSid = list2.state.sessions.find((s) => s.status === "complete").sessionId;
    const archivedHands = await sendCmd(ws, "GET_HAND_LIST", { sessionId: completedSid });
    check("archived session has hands", archivedHands.ok && archivedHands.state.hands.length === 1);

    // Get hand events from archived session
    const hid = archivedHands.state.hands[0].handId;
    const handEvts = await sendCmd(ws, "GET_HAND_EVENTS", { sessionId: completedSid, handId: hid });
    check("archived hand events", handEvts.ok && handEvts.events.length > 0);
    check("has HAND_START", handEvts.events.some((e) => e.type === "HAND_START"));
    check("has HAND_END", handEvts.events.some((e) => e.type === "HAND_END"));

    ws.close();
    srv.close();
  }

  // ── Report ───────────────────────────────────────────────────────────
  console.log(`\n=== Phase 7 Results ===`);
  console.log(`Checks: ${checks}`);
  console.log(`Passed: ${passed}`);
  console.log(`Failed: ${failed}`);
  console.log(failed === 0 ? `\n*** PHASE 7 PASSED: ${passed}/${checks} ***` : `\n*** PHASE 7 FAILED: ${failed}/${checks} ***`);

  fs.rmSync(testDir, { recursive: true, force: true });
}

run().catch((e) => { console.error("P7 error:", e); process.exit(1); });
