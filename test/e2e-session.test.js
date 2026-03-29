#!/usr/bin/env node
"use strict";

/**
 * End-to-End Session Test
 *
 * Plays a full multi-hand session over WebSocket, verifies:
 * - Hand count, stack accounting, event log completeness
 * - GET_HAND_LIST and GET_HAND_EVENTS return correct data
 * - Event log reconstructs to live state (conformance)
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");
const { reconstructState } = require("../src/api/reconstruct");

const logDir = path.join(__dirname, "..", "test-output");
fs.mkdirSync(logDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;

function check(label, cond) {
  checks++;
  if (cond) { passed++; }
  else { failed++; console.log(`  FAIL: ${label}`); }
}

async function connectWS(port) {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://localhost:${port}`);
    ws.on("message", function first(raw) {
      const msg = JSON.parse(raw.toString());
      if (msg.welcome) { ws.removeListener("message", first); resolve({ ws, welcome: msg }); }
    });
  });
}

function sendCmd(ws, cmd, payload) {
  return new Promise((resolve) => {
    const id = `e2e-${++checks}`;
    checks--; // don't count as check
    const handler = (raw) => {
      const msg = JSON.parse(raw.toString());
      if (msg.id === id) { ws.removeListener("message", handler); resolve(msg); }
    };
    ws.on("message", handler);
    ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
  });
}

async function run() {
  const port = 9300 + Math.floor(Math.random() * 100);
  const logPath = path.join(logDir, "e2e-session-events.jsonl");

  let seed = 555;
  const server = startServer({
    port, logPath, sessionId: "e2e-test",
    table: { tableId: "e2e", tableName: "E2E Test", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 400, maxBuyIn: 1000 },
  });

  try {
    const { ws, welcome } = await connectWS(port);
    check("welcome received", welcome.welcome === true);

    // ── Seat 3 players ───────────────────────────────────────────────
    await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 1000, country: "US" });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 800, country: "GB" });
    await sendCmd(ws, "SEAT_PLAYER", { seat: 3, name: "Charlie", buyIn: 600, country: "CA" });

    let st = (await sendCmd(ws, "GET_STATE")).state;
    check("3 players seated", Object.values(st.seats).filter(s => s.status === "OCCUPIED").length === 3);

    // ── Play 5 fold-out hands ────────────────────────────────────────
    for (let h = 0; h < 5; h++) {
      const startResp = await sendCmd(ws, "START_HAND");
      check(`hand ${h + 1} starts`, startResp.ok && startResp.events.some(e => e.type === "HAND_START"));

      st = (await sendCmd(ws, "GET_STATE")).state;
      const startStacks = {};
      Object.values(st.seats).filter(s => s.status === "OCCUPIED").forEach(s => startStacks[s.seat] = s.stack + s.totalInvested);

      // Fold everyone
      let actionSeat = st.hand ? st.hand.actionSeat : null;
      while (actionSeat != null) {
        await sendCmd(ws, "PLAYER_ACTION", { seat: actionSeat, action: "FOLD" });
        st = (await sendCmd(ws, "GET_STATE")).state;
        actionSeat = (st.hand && st.hand.phase !== "COMPLETE") ? st.hand.actionSeat : null;
      }

      // Stack accounting
      let totalEnd = 0;
      Object.values(st.seats).filter(s => s.status === "OCCUPIED").forEach(s => totalEnd += s.stack);
      check(`hand ${h + 1} stacks conserved`, totalEnd === 2400);
    }

    // ── Play 1 multi-street hand ─────────────────────────────────────
    await sendCmd(ws, "START_HAND");
    st = (await sendCmd(ws, "GET_STATE")).state;
    let seat = st.hand.actionSeat;

    // Raise
    await sendCmd(ws, "PLAYER_ACTION", { seat, action: "RAISE", amount: 30 });
    st = (await sendCmd(ws, "GET_STATE")).state;
    seat = st.hand.actionSeat;

    // Call
    await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CALL" });
    st = (await sendCmd(ws, "GET_STATE")).state;
    seat = st.hand.actionSeat;

    // Fold (last preflop player)
    if (seat != null && st.hand.phase === "PREFLOP") {
      await sendCmd(ws, "PLAYER_ACTION", { seat, action: "FOLD" });
      st = (await sendCmd(ws, "GET_STATE")).state;
    }

    check("reached flop", st.hand && (st.hand.phase === "FLOP" || st.hand.phase === "COMPLETE"));

    if (st.hand && st.hand.phase === "FLOP") {
      check("board has 3 cards", st.hand.board.length === 3);
      seat = st.hand.actionSeat;

      // Bet on flop
      if (seat != null) {
        const legal = st.hand.legalActions;
        if (legal && legal.actions.includes("BET")) {
          await sendCmd(ws, "PLAYER_ACTION", { seat, action: "BET", amount: 20 });
        } else if (legal && legal.actions.includes("CHECK")) {
          await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CHECK" });
        }
        st = (await sendCmd(ws, "GET_STATE")).state;
        seat = st.hand ? st.hand.actionSeat : null;

        // Other player folds
        if (seat != null) {
          await sendCmd(ws, "PLAYER_ACTION", { seat, action: "FOLD" });
          st = (await sendCmd(ws, "GET_STATE")).state;
        }
      }
    }

    // Should be settled
    const multiComplete = !st.hand || st.hand.phase === "COMPLETE";
    check("multi-street hand settled", multiComplete);

    let totalEnd = 0;
    Object.values(st.seats).filter(s => s.status === "OCCUPIED").forEach(s => totalEnd += s.stack);
    check("final stacks conserved", totalEnd === 2400);
    check("hands played = 6", st.handsPlayed === 6);

    // ── Hand history ─────────────────────────────────────────────────
    const listResp = await sendCmd(ws, "GET_HAND_LIST");
    check("hand list ok", listResp.ok);
    const hands = listResp.state ? listResp.state.hands : [];
    check("6 hands in list", hands.length === 6);

    // Check each hand's events
    for (const h of hands) {
      const hResp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: h.handId });
      check(`hand ${h.handId} has events`, hResp.ok && hResp.events.length > 0);
      const types = hResp.events.map(e => e.type);
      check(`hand ${h.handId} has START+END`, types.includes("HAND_START") && types.includes("HAND_END"));
    }

    // ── Event log conformance ────────────────────────────────────────
    const logResp = await sendCmd(ws, "GET_EVENT_LOG");
    check("event log ok", logResp.ok);

    const allEvents = logResp.events;
    const snapshots = allEvents.filter(e => e.type === "TABLE_SNAPSHOT");
    const starts = allEvents.filter(e => e.type === "HAND_START");
    const ends = allEvents.filter(e => e.type === "HAND_END");
    const seats = allEvents.filter(e => e.type === "SEAT_PLAYER");

    check("1 TABLE_SNAPSHOT", snapshots.length === 1);
    check("6 HAND_START", starts.length === 6);
    check("6 HAND_END", ends.length === 6);
    check("START/END paired", starts.length === ends.length);
    check("3 SEAT_PLAYER", seats.length === 3);

    // Reconstruct and compare
    const reconstructed = reconstructState(allEvents);
    const live = (await sendCmd(ws, "GET_STATE")).state;

    let stateMatch = true;
    for (let i = 0; i < 6; i++) {
      if (live.seats[i].stack !== reconstructed.seats[i].stack) stateMatch = false;
      if (live.seats[i].status !== reconstructed.seats[i].status) stateMatch = false;
    }
    check("reconstructed state matches live", stateMatch);

    ws.close();
  } finally {
    server.close();
  }

  // ── Report ─────────────────────────────────────────────────────────
  console.log(`\n=== E2E Session Results ===`);
  console.log(`Checks: ${checks}`);
  console.log(`Passed: ${passed}`);
  console.log(`Failed: ${failed}`);
  console.log(failed === 0
    ? `\n*** E2E SESSION PASSED: ${passed}/${checks} ***`
    : `\n*** E2E SESSION FAILED: ${failed}/${checks} ***`);
}

run().catch((e) => { console.error("E2E error:", e); process.exit(1); });
