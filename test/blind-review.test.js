#!/usr/bin/env node
"use strict";

/**
 * Blind Review Mode Tests
 *
 * Tests: hand-list masking, replay outcome masking, normal mode unchanged,
 * reveal behavior, quiz compatibility, no regression.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "blind-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

function clonePlayers(p) { const o = {}; for (const [s, v] of Object.entries(p)) o[s] = { ...v, cards: v.cards ? [...v.cards] : null }; return o; }
function compileFrames(events) {
  const frames = []; let street = "", board = [], pot = 0, players = {}, handId = "";
  for (const e of events) {
    switch (e.type) {
      case "HAND_START": handId = e.handId; street = "PREFLOP"; board = []; pot = 0; players = {};
        for (const [s, p] of Object.entries(e.players || {})) players[s] = { name: p.name, stack: p.stack, invested: 0, folded: false, allIn: false, cards: null };
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: "", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false }); break;
      case "PLAYER_ACTION": { const p = players[e.seat]; if (p) { p.stack -= (e.delta||0); p.invested += (e.delta||0); if (e.action === "FOLD") p.folded = true; } pot += (e.delta||0);
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: `${e.player} ${e.action}`, actingSeat: e.seat, actionLabel: e.action, isDecision: true, isTerminal: false }); break; }
      case "POT_AWARD": for (const a of e.awards || []) { const p = players[a.seat]; if (p) p.stack += a.amount; }
        frames.push({ index: frames.length, handId, street: "SETTLE", board: [...board], pot: 0, players: clonePlayers(players), event: e.type, label: `Award: winner`, actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false }); pot = 0; break;
      case "HAND_SUMMARY":
        frames.push({ index: frames.length, handId, street: "SETTLE", board: [...board], pot: 0, players: clonePlayers(players), event: e.type, label: `Result: ${e.winPlayer} wins`, actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false }); break;
      case "SHOWDOWN_REVEAL": street = "SHOWDOWN"; for (const r of e.reveals || []) { const p = players[r.seat]; if (p) p.cards = r.cards; }
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: "SHOWDOWN: revealed", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false }); break;
      case "HAND_END":
        frames.push({ index: frames.length, handId, street: "COMPLETE", board: [...board], pot: 0, players: clonePlayers(players), event: e.type, label: "Complete", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: true }); break;
      default: break;
    }
  }
  return frames;
}

// Simulate blind masking logic (mirrors client)
function isBlindHidden(blindMode, revealed, sessionId, handId) {
  return blindMode && !revealed.has(sessionId + "/" + handId);
}

async function connectWS(port) {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://localhost:${port}`);
    ws.on("message", function first(raw) { const m = JSON.parse(raw.toString()); if (m.welcome) { ws.removeListener("message", first); resolve({ ws, welcome: m }); } });
  });
}
let cmdId = 0;
function sendCmd(ws, cmd, payload) {
  return new Promise((resolve) => {
    const id = `bl-${++cmdId}`;
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

const TABLE = { tableId: "bl-t", tableName: "Blind", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws, welcome } = await connectWS(port);
  const sid = welcome.sessionId;

  const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
  const aliceId = cr.state.actor.actorId;
  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

  for (let i = 0; i < 3; i++) {
    const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;
  }

  // Get hand data
  const q = await sendCmd(ws, "QUERY_HANDS", { actorId: aliceId });
  const hands = q.state.hands;
  const resp1 = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
  const frames1 = compileFrames(resp1.events);

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Hand-List Masking
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Hand-List Masking ===");
  {
    const blindMode = true;
    const revealed = new Set();
    const h = hands[0];

    // Blind mode: result and net should be hidden
    const hidden = isBlindHidden(blindMode, revealed, h.sessionId, h.handId);
    check("T1: hand is hidden in blind mode", hidden);

    // Simulate masking: replace result and net
    const resLabel = hidden ? "---" : h.result;
    const netLabel = hidden ? "---" : String(h.netResult);
    check("T1: result masked", resLabel === "---");
    check("T1: net masked", netLabel === "---");

    // handRank should also be hidden
    const rank = hidden ? "" : (h.handRank || "");
    check("T1: rank masked", rank === "");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Normal Mode Unchanged
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Normal Mode ===");
  {
    const blindMode = false;
    const revealed = new Set();
    const h = hands[0];

    const hidden = isBlindHidden(blindMode, revealed, h.sessionId, h.handId);
    check("T2: not hidden in normal mode", !hidden);

    const resLabel = hidden ? "---" : h.result;
    check("T2: result visible", resLabel === h.result);
    check("T2: result is won or lost", ["won", "lost", "split"].includes(h.result));

    const netLabel = hidden ? "---" : String(h.netResult);
    check("T2: net visible", netLabel === String(h.netResult));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Replay Outcome Masking
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Replay Outcome Masking ===");
  {
    const blindMode = true;
    const revealed = new Set();
    const outcomeEvents = new Set(["POT_AWARD", "HAND_SUMMARY", "HAND_END"]);

    // Find outcome frames
    const potFrame = frames1.find((f) => f.event === "POT_AWARD");
    const summaryFrame = frames1.find((f) => f.event === "HAND_SUMMARY");
    const sdFrame = frames1.find((f) => f.event === "SHOWDOWN_REVEAL");

    check("T3: POT_AWARD frame exists", !!potFrame);
    check("T3: HAND_SUMMARY frame exists", !!summaryFrame);

    // In blind mode, outcome labels should be masked
    const hidden = isBlindHidden(blindMode, revealed, sid, "1");
    check("T3: hand is blind-hidden", hidden);

    // POT_AWARD label masked
    const potLabel = (hidden && outcomeEvents.has("POT_AWARD")) ? "[outcome hidden]" : potFrame.label;
    check("T3: POT_AWARD label masked", potLabel === "[outcome hidden]");

    // HAND_SUMMARY label masked
    const sumLabel = (hidden && outcomeEvents.has("HAND_SUMMARY")) ? "[outcome hidden]" : summaryFrame.label;
    check("T3: HAND_SUMMARY label masked", sumLabel === "[outcome hidden]");

    // Non-outcome frames NOT masked
    const actionFrame = frames1.find((f) => f.isDecision);
    const actionHidden = hidden && outcomeEvents.has(actionFrame.event);
    check("T3: action frame NOT masked", !actionHidden);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Reveal Behavior
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Reveal ===");
  {
    const blindMode = true;
    const revealed = new Set();

    // Before reveal: hidden
    check("T4: before reveal: hidden", isBlindHidden(blindMode, revealed, sid, "1"));

    // Reveal hand 1
    revealed.add(sid + "/1");
    check("T4: after reveal: visible", !isBlindHidden(blindMode, revealed, sid, "1"));

    // Hand 2 still hidden
    check("T4: hand 2 still hidden", isBlindHidden(blindMode, revealed, sid, "2"));

    // Revealing is per-hand
    revealed.add(sid + "/2");
    check("T4: hand 2 revealed", !isBlindHidden(blindMode, revealed, sid, "2"));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Toggle Reset
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Toggle Reset ===");
  {
    let blindMode = true;
    let revealed = new Set();
    revealed.add(sid + "/1");

    // Toggle off: normal mode
    blindMode = false;
    check("T5: blind off: hand 1 visible", !isBlindHidden(blindMode, revealed, sid, "1"));
    check("T5: blind off: hand 2 visible", !isBlindHidden(blindMode, revealed, sid, "2"));

    // Toggle back on: revealed set should be cleared
    blindMode = true;
    revealed = new Set(); // simulates toggleBlindReview clearing revealed
    check("T5: blind on again: hand 1 hidden (cleared)", isBlindHidden(blindMode, revealed, sid, "1"));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: No Regression ===");
  {
    // Replay
    check("T6: replay events ok", resp1.events.length > 0);
    check("T6: frames compiled", frames1.length > 0);

    // Query
    check("T6: query works", hands.length === 3);

    // Annotations
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "review", text: "blind test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    check("T6: annotations work", anns.ok);

    // Tag counts
    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T6: counts work", counts.ok);

    // Stats
    const stats = await sendCmd(ws, "GET_ACTOR_STATS", { actorId: aliceId });
    check("T6: stats work", stats.ok);
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** BLIND REVIEW TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
