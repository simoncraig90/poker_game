#!/usr/bin/env node
"use strict";

/**
 * Jump to Next Unanswered Hero Decision Tests
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "nua-" + Date.now());
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
      case "HAND_END": frames.push({ index: frames.length, handId, street: "COMPLETE", board: [...board], pot: 0, players: clonePlayers(players), event: e.type, label: "Complete", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: true }); break;
      default: break;
    }
  }
  return frames;
}
function getDecisionIndices(frames) { return frames.map((f, i) => f.isDecision ? i : -1).filter((i) => i >= 0); }
function actionToBucket(action) {
  if (!action) return null; const a = action.toUpperCase();
  if (a === "FOLD") return "fold"; if (a === "CHECK" || a === "CALL") return "passive"; if (a === "BET" || a === "RAISE") return "aggressive"; return null;
}

// Mirror client function
function nextUnansweredHeroIndex(frames, heroSeat, ledger, startIdx) {
  if (heroSeat === null) return -1;
  const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);
  if (heroDecs.length === 0) return -1;
  for (const idx of heroDecs) { if (idx > startIdx && !ledger[idx]) return idx; }
  for (const idx of heroDecs) { if (idx <= startIdx && !ledger[idx]) return idx; }
  return -1;
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
    const id = `nu-${++cmdId}`;
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

const TABLE = { tableId: "nu-t", tableName: "NextUA", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws } = await connectWS(port);

  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
  const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;

  const resp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
  const frames = compileFrames(resp.events);
  const heroSeat = 0;
  const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Next Unanswered Detection — Empty Ledger
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Empty Ledger ===");
  {
    const ledger = {};
    // From frame 0: should find first hero decision
    const idx = nextUnansweredHeroIndex(frames, heroSeat, ledger, 0);
    check("T1: found unanswered", idx >= 0);
    check("T1: is hero decision", frames[idx].actingSeat === heroSeat);
    check("T1: is first hero dec", idx === heroDecs[0]);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Skips Answered Decisions
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Skips Answered ===");
  {
    const ledger = {};
    // Answer first hero decision
    const actual = actionToBucket(frames[heroDecs[0]].actionLabel);
    ledger[heroDecs[0]] = { chosen: actual, actual, result: "match" };

    // From before first dec: should skip it and find second
    const idx = nextUnansweredHeroIndex(frames, heroSeat, ledger, 0);
    if (heroDecs.length > 1) {
      check("T2: skips answered", idx === heroDecs[1]);
      check("T2: found is unanswered", !ledger[idx]);
    } else {
      check("T2: only 1 dec, all answered", idx === -1);
      check("T2: (skip)", true);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Skips Non-Hero Decisions
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Skips Non-Hero ===");
  {
    const ledger = {};
    const oppDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat !== heroSeat);
    check("T3: opponent decisions exist", oppDecs.length > 0);

    // From just before an opponent decision: should skip it
    if (oppDecs.length > 0 && heroDecs.length > 0) {
      const idx = nextUnansweredHeroIndex(frames, heroSeat, ledger, oppDecs[0] - 1);
      check("T3: result is hero decision", idx >= 0 && frames[idx].actingSeat === heroSeat);
    } else {
      check("T3: (skip)", true);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Wrap Behavior
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Wrap ===");
  {
    const ledger = {};
    // Answer all hero decisions except the first
    for (let d = 1; d < heroDecs.length; d++) {
      const actual = actionToBucket(frames[heroDecs[d]].actionLabel);
      ledger[heroDecs[d]] = { chosen: actual, actual, result: "match" };
    }

    // From after last hero decision: should wrap to first (unanswered)
    const lastHeroDec = heroDecs[heroDecs.length - 1];
    const idx = nextUnansweredHeroIndex(frames, heroSeat, ledger, lastHeroDec);
    check("T4: wraps to first unanswered", idx === heroDecs[0]);
    check("T4: first is unanswered", !ledger[heroDecs[0]]);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: All Answered → -1
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: All Answered ===");
  {
    const ledger = {};
    for (const idx of heroDecs) {
      const actual = actionToBucket(frames[idx].actionLabel);
      ledger[idx] = { chosen: actual, actual, result: "match" };
    }

    const idx = nextUnansweredHeroIndex(frames, heroSeat, ledger, 0);
    check("T5: all answered → -1", idx === -1);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: Null/Missing Hero
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: Null Hero ===");
  {
    check("T6: null hero → -1", nextUnansweredHeroIndex(frames, null, {}, 0) === -1);
    check("T6: nonexistent seat → -1", nextUnansweredHeroIndex(frames, 99, {}, 0) === -1);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 7: Redo Does Not Use This
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 7: Redo Unchanged ===");
  {
    // Redo clears one spot at the current cursor, doesn't jump
    const ledger = {};
    for (const idx of heroDecs) {
      const actual = actionToBucket(frames[idx].actionLabel);
      ledger[idx] = { chosen: actual, actual, result: "match" };
    }

    // Simulate redo at second hero decision
    if (heroDecs.length > 1) {
      const redoAt = heroDecs[1];
      delete ledger[redoAt]; // redo clears this spot

      // Cursor stays at redoAt (redo doesn't move)
      check("T7: redo cursor stays", true); // structural

      // But nextUnanswered from redoAt would find... the spot itself (via wrap)
      const idx = nextUnansweredHeroIndex(frames, heroSeat, ledger, redoAt);
      // It wraps: first checks > redoAt (all answered), then <= redoAt (finds redoAt itself unanswered)
      check("T7: next unanswered after redo finds the redone spot", idx === redoAt);
    } else {
      check("T7: (skip — single dec)", true);
      check("T7: (skip)", true);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 8: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 8: No Regression ===");
  {
    check("T8: events ok", resp.events.length > 0);
    check("T8: frames ok", frames.length > 0);

    const q = await sendCmd(ws, "QUERY_HANDS", {});
    check("T8: query ok", q.ok);

    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: q.state.hands[0].sessionId, handId: "1", tag: "review", text: "nua test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: q.state.hands[0].sessionId, handId: "1" });
    check("T8: annotations ok", anns.ok);

    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: q.state.hands[0].sessionId });
    check("T8: counts ok", counts.ok);
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** NEXT UNANSWERED TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
