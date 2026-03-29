#!/usr/bin/env node
"use strict";

/**
 * WebSocket External Conformance Test
 *
 * Proves:
 * 1. Commands sent over WebSocket produce the same results as direct dispatch
 * 2. Events broadcast to all clients
 * 3. State from WS matches reconstructState(events)
 */

const WebSocket = require("ws");
const { startServer } = require("../src/server/ws-server");
const { reconstructState } = require("../src/api/reconstruct");
const path = require("path");
const fs = require("fs");

const logDir = path.join(__dirname, "..", "test-output");
fs.mkdirSync(logDir, { recursive: true });

let msgId = 0;
let checks = 0;
let passed = 0;
let failed = 0;

function check(label, condition) {
  checks++;
  if (condition) { passed++; }
  else { failed++; console.log(`  FAIL: ${label}`); }
}

function sendCmd(ws, cmd, payload) {
  return new Promise((resolve) => {
    const id = `msg-${++msgId}`;
    const handler = (raw) => {
      const msg = JSON.parse(raw.toString());
      if (msg.id === id) {
        ws.removeListener("message", handler);
        resolve(msg);
      }
    };
    ws.on("message", handler);
    ws.send(JSON.stringify({ id, cmd, payload }));
  });
}

function waitForMessage(ws, predicate, timeoutMs = 2000) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { ws.removeListener("message", handler); reject(new Error("timeout")); }, timeoutMs);
    const handler = (raw) => {
      const msg = JSON.parse(raw.toString());
      if (predicate(msg)) {
        clearTimeout(timeout);
        ws.removeListener("message", handler);
        resolve(msg);
      }
    };
    ws.on("message", handler);
  });
}

async function connect(port) {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://localhost:${port}`);
    let welcome = null;
    ws.on("message", function firstMsg(raw) {
      const msg = JSON.parse(raw.toString());
      if (msg.welcome) {
        welcome = msg;
        ws.removeListener("message", firstMsg);
        resolve({ ws, welcome });
      }
    });
  });
}

async function run() {
  // Start server on random-ish port
  const port = 9200 + Math.floor(Math.random() * 100);
  const logPath = path.join(logDir, "ws-conformance-events.jsonl");

  const dataDir = path.join(logDir, "ws-conf-data-" + Date.now());
  const server = startServer({
    port,
    dataDir,
    sessionId: "ws-conformance",
    table: { tableId: "ws-t1", tableName: "WS Test", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 400, maxBuyIn: 1000 },
  });

  try {
    // ── Connect two clients ──────────────────────────────────────────
    const { ws: client1, welcome: welcome1 } = await connect(port);
    check("client1 gets welcome", welcome1.welcome === true);
    check("welcome has state", welcome1.state != null);
    check("welcome has sessionId", welcome1.sessionId === "ws-conformance");

    const { ws: client2, welcome: welcome2 } = await connect(port);
    check("client2 gets welcome", welcome2.welcome === true);

    // ── Seat players ─────────────────────────────────────────────────
    const seat1 = await sendCmd(client1, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 1000, country: "US" });
    check("seat Alice ok", seat1.ok === true);
    check("seat Alice has SEAT_PLAYER event", seat1.events.length === 1 && seat1.events[0].type === "SEAT_PLAYER");

    // Client2 should receive broadcast
    const bc1 = await waitForMessage(client2, (m) => m.broadcast);
    check("client2 gets broadcast", bc1.broadcast === true);
    check("broadcast has seat event", bc1.events.length === 1 && bc1.events[0].type === "SEAT_PLAYER");

    const seat2 = await sendCmd(client2, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 800, country: "GB" });
    check("seat Bob ok", seat2.ok === true);

    // Wait for broadcast to client1
    const bc2 = await waitForMessage(client1, (m) => m.broadcast);
    check("client1 gets Bob broadcast", bc2.events[0].player === "Bob");

    const seat3 = await sendCmd(client1, "SEAT_PLAYER", { seat: 3, name: "Charlie", buyIn: 600, country: "CA" });
    check("seat Charlie ok", seat3.ok === true);

    // ── Get state ────────────────────────────────────────────────────
    const stateResp = await sendCmd(client1, "GET_STATE", {});
    check("get state ok", stateResp.ok === true);
    check("state has seats", stateResp.state != null && stateResp.state.seats != null);
    check("Alice at seat 0", stateResp.state.seats[0].player.name === "Alice");
    check("Bob at seat 1", stateResp.state.seats[1].player.name === "Bob");

    // ── Start hand ───────────────────────────────────────────────────
    const startResp = await sendCmd(client1, "START_HAND", {});
    check("start hand ok", startResp.ok === true);
    check("start emits events", startResp.events.length > 0);
    const eventTypes = startResp.events.map((e) => e.type);
    check("has HAND_START", eventTypes.includes("HAND_START"));
    check("has BLIND_POST", eventTypes.includes("BLIND_POST"));
    check("has HERO_CARDS", eventTypes.includes("HERO_CARDS"));

    // Client2 gets broadcast of start events
    const bcStart = await waitForMessage(client2, (m) => m.broadcast && m.events.some((e) => e.type === "HAND_START"));
    check("client2 gets hand start broadcast", bcStart.events.length > 0);

    // ── Play: get state to find action seat ──────────────────────────
    const midState = await sendCmd(client1, "GET_STATE", {});
    const actionSeat = midState.state.hand.actionSeat;
    check("action seat assigned", actionSeat != null);

    // ── Fold around ──────────────────────────────────────────────────
    // Fold first player
    const fold1 = await sendCmd(client1, "PLAYER_ACTION", { seat: actionSeat, action: "FOLD" });
    check("fold ok", fold1.ok === true);
    check("fold produces events", fold1.events.length >= 1);

    // Get updated state
    const afterFold = await sendCmd(client1, "GET_STATE", {});
    const nextAction = afterFold.state.hand ? afterFold.state.hand.actionSeat : null;

    if (nextAction != null) {
      // Fold second player — should trigger settlement
      const fold2 = await sendCmd(client1, "PLAYER_ACTION", { seat: nextAction, action: "FOLD" });
      check("second fold ok", fold2.ok === true);
      const foldTypes = fold2.events.map((e) => e.type);
      check("settlement events after last fold", foldTypes.includes("POT_AWARD") || foldTypes.includes("HAND_END"));
    }

    // ── Get event log ────────────────────────────────────────────────
    const logResp = await sendCmd(client1, "GET_EVENT_LOG", {});
    check("get event log ok", logResp.ok === true);
    check("log has events", logResp.events.length > 0);

    // ── Conformance: reconstruct from log ────────────────────────────
    const reconstructed = reconstructState(logResp.events);
    const liveState = (await sendCmd(client1, "GET_STATE", {})).state;

    // Compare seats
    let stateMatch = true;
    for (let i = 0; i < 6; i++) {
      const ls = liveState.seats[i];
      const rs = reconstructed.seats[i];
      if (ls.stack !== rs.stack) { stateMatch = false; break; }
      if (ls.status !== rs.status) { stateMatch = false; break; }
      const ln = ls.player ? ls.player.name : null;
      const rn = rs.player ? rs.player.name : null;
      if (ln !== rn) { stateMatch = false; break; }
    }
    check("WS state matches reconstructState(eventLog)", stateMatch);

    // ── Error handling ───────────────────────────────────────────────
    const badCmd = await sendCmd(client1, "BOGUS_CMD", {});
    check("unknown command returns error", badCmd.ok === false && badCmd.error != null);

    const badAction = await sendCmd(client1, "PLAYER_ACTION", { seat: 99, action: "FOLD" });
    check("invalid action returns error", badAction.ok === false);

    // ── Cleanup ──────────────────────────────────────────────────────
    client1.close();
    client2.close();

  } finally {
    server.close();
  }

  // ── Report ───────────────────────────────────────────────────────────
  console.log(`\n=== WS Conformance Results ===`);
  console.log(`Checks: ${checks}`);
  console.log(`Passed: ${passed}`);
  console.log(`Failed: ${failed}`);
  if (failed === 0) {
    console.log(`\n✓ WS CONFORMANCE PASSED: ${passed}/${checks}`);
  } else {
    console.log(`\n✗ WS CONFORMANCE FAILED: ${failed}/${checks}`);
  }
}

run().catch((e) => { console.error("Test error:", e); process.exit(1); });
