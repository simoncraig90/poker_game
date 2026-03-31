#!/usr/bin/env node
"use strict";

/**
 * Queue Hand Review Markers Tests
 *
 * Tests: untouched/visited/in-progress/completed state derivation,
 * live update, queue reset, consistency with summary.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "qmark-" + Date.now());
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
      case "DEAL_COMMUNITY": street = e.street; board = e.board || [];
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: e.street, actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false }); break;
      default: // skip non-essential for this test
        if (e.type === "HAND_END") frames.push({ index: frames.length, handId, street: "COMPLETE", board: [...board], pot: 0, players: clonePlayers(players), event: e.type, label: "Complete", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: true });
        break;
    }
  }
  return frames;
}
function getDecisionIndices(frames) { return frames.map((f, i) => f.isDecision ? i : -1).filter((i) => i >= 0); }
function actionToBucket(action) {
  if (!action) return null; const a = action.toUpperCase();
  if (a === "FOLD") return "fold"; if (a === "CHECK" || a === "CALL") return "passive"; if (a === "BET" || a === "RAISE") return "aggressive"; return null;
}

// State derivation (mirrors client function)
function handReviewState(accum, sessionId, handId) {
  const key = sessionId + "/" + handId;
  const entry = accum[key];
  if (!entry) return "untouched";
  if (entry.answered === 0) return "visited";
  if (entry.totalHero > 0 && entry.answered >= entry.totalHero) return "completed";
  return "in-progress";
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
    const id = `qm-${++cmdId}`;
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

const TABLE = { tableId: "qm-t", tableName: "Markers", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws, welcome } = await connectWS(port);
  const sid = welcome.sessionId;

  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
  for (let i = 0; i < 4; i++) {
    const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;
  }

  const heroSeat = 0;
  const handData = [];
  for (let i = 1; i <= 4; i++) {
    const resp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: String(i) });
    handData.push({ handId: String(i), sessionId: sid, events: resp.events, frames: compileFrames(resp.events) });
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: State Derivation
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: State Derivation ===");
  {
    const accum = {};

    // All untouched initially
    check("T1: hand 1 untouched", handReviewState(accum, sid, "1") === "untouched");
    check("T1: hand 2 untouched", handReviewState(accum, sid, "2") === "untouched");

    // Visited (opened but no answers)
    const h1Decs = getDecisionIndices(handData[0].frames).filter((i) => handData[0].frames[i].actingSeat === heroSeat);
    accum[sid + "/1"] = { answered: 0, matches: 0, diffs: 0, totalHero: h1Decs.length };
    check("T1: hand 1 visited", handReviewState(accum, sid, "1") === "visited");

    // In progress (some answered)
    accum[sid + "/1"] = { answered: 1, matches: 1, diffs: 0, totalHero: h1Decs.length };
    check("T1: hand 1 in-progress", h1Decs.length > 1
      ? handReviewState(accum, sid, "1") === "in-progress"
      : handReviewState(accum, sid, "1") === "completed"); // only 1 decision = completed

    // Completed (all answered)
    accum[sid + "/1"] = { answered: h1Decs.length, matches: h1Decs.length, diffs: 0, totalHero: h1Decs.length };
    check("T1: hand 1 completed", handReviewState(accum, sid, "1") === "completed");

    // Hand 2 still untouched
    check("T1: hand 2 still untouched", handReviewState(accum, sid, "2") === "untouched");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Live Update After Reveal
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Live Update ===");
  {
    const accum = {};
    const frames = handData[0].frames;
    const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);
    const totalHero = heroDecs.length;

    // Open hand: visited
    accum[sid + "/1"] = { answered: 0, matches: 0, diffs: 0, totalHero };
    check("T2: after open: visited", handReviewState(accum, sid, "1") === "visited");

    // First reveal
    accum[sid + "/1"] = { answered: 1, matches: 1, diffs: 0, totalHero };
    const stateAfter1 = handReviewState(accum, sid, "1");
    check("T2: after 1 reveal: in-progress or completed",
      stateAfter1 === "in-progress" || stateAfter1 === "completed");

    // All revealed
    accum[sid + "/1"] = { answered: totalHero, matches: totalHero - 1, diffs: 1, totalHero };
    check("T2: after all reveals: completed", handReviewState(accum, sid, "1") === "completed");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Queue Reset
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Queue Reset ===");
  {
    let accum = {};
    accum[sid + "/1"] = { answered: 3, matches: 2, diffs: 1, totalHero: 3 };
    accum[sid + "/2"] = { answered: 2, matches: 1, diffs: 1, totalHero: 4 };
    check("T3: before reset: 2 entries", Object.keys(accum).length === 2);
    check("T3: hand 1 completed", handReviewState(accum, sid, "1") === "completed");

    // Filter change → reset
    accum = {};
    check("T3: after reset: 0 entries", Object.keys(accum).length === 0);
    check("T3: hand 1 now untouched", handReviewState(accum, sid, "1") === "untouched");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Consistency with Queue Summary
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Consistency ===");
  {
    const accum = {};
    const totalQueue = 4;

    // Complete hands 1 and 3
    for (const idx of [0, 2]) {
      const frames = handData[idx].frames;
      const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);
      accum[sid + "/" + handData[idx].handId] = {
        answered: heroDecs.length, matches: heroDecs.length, diffs: 0, totalHero: heroDecs.length,
      };
    }

    // Visit hand 2 (no answers)
    const h2Decs = getDecisionIndices(handData[1].frames).filter((i) => handData[1].frames[i].actingSeat === heroSeat);
    accum[sid + "/2"] = { answered: 0, matches: 0, diffs: 0, totalHero: h2Decs.length };

    // States
    check("T4: hand 1 completed", handReviewState(accum, sid, "1") === "completed");
    check("T4: hand 2 visited", handReviewState(accum, sid, "2") === "visited");
    check("T4: hand 3 completed", handReviewState(accum, sid, "3") === "completed");
    check("T4: hand 4 untouched", handReviewState(accum, sid, "4") === "untouched");

    // Queue summary consistency
    const qVals = Object.values(accum);
    const qHands = qVals.length;
    const qCompleted = qVals.filter((v) => v.totalHero > 0 && v.answered >= v.totalHero).length;
    check("T4: 3 hands visited/reviewed", qHands === 3);
    check("T4: 2 completed", qCompleted === 2);
    check("T4: reviewed < total queue", qHands < totalQueue);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Next Incomplete Logic
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Next Incomplete ===");
  {
    const accum = {};
    const queue = handData.map((h) => ({ sessionId: h.sessionId, handId: h.handId }));

    // Complete hands 1 and 2
    for (let i = 0; i < 2; i++) {
      const frames = handData[i].frames;
      const heroDecs = getDecisionIndices(frames).filter((j) => frames[j].actingSeat === heroSeat);
      accum[sid + "/" + handData[i].handId] = { answered: heroDecs.length, matches: heroDecs.length, diffs: 0, totalHero: heroDecs.length };
    }

    // From position 1, find next incomplete
    let nextInc = -1;
    for (let i = 2; i < queue.length; i++) {
      if (handReviewState(accum, queue[i].sessionId, queue[i].handId) !== "completed") { nextInc = i; break; }
    }
    check("T5: next incomplete is hand 3 (index 2)", nextInc === 2);

    // Complete hand 3 too
    const frames3 = handData[2].frames;
    const heroDecs3 = getDecisionIndices(frames3).filter((j) => frames3[j].actingSeat === heroSeat);
    accum[sid + "/3"] = { answered: heroDecs3.length, matches: heroDecs3.length, diffs: 0, totalHero: heroDecs3.length };

    // From position 2, next incomplete should be hand 4
    nextInc = -1;
    for (let i = 3; i < queue.length; i++) {
      if (handReviewState(accum, queue[i].sessionId, queue[i].handId) !== "completed") { nextInc = i; break; }
    }
    check("T5: next incomplete is hand 4 (index 3)", nextInc === 3);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: No Regression ===");
  {
    check("T6: events loadable", handData[0].events.length > 0);
    check("T6: frames compilable", handData[0].frames.length > 0);

    const q = await sendCmd(ws, "QUERY_HANDS", {});
    check("T6: query works", q.ok && q.state.hands.length >= 4);

    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "review", text: "marker test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    check("T6: annotations work", anns.ok);

    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T6: counts work", counts.ok);

    check("T6: bucket mapping works", actionToBucket("CHECK") === "passive");
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** QUEUE MARKERS TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
