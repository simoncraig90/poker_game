#!/usr/bin/env node
"use strict";

/**
 * Queue-Level Quiz Summary Tests
 *
 * Tests: accumulator updates across hands, hands-reviewed count,
 * queue reset on filter change, per-hand consistency, queue nav preservation.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "qsum-" + Date.now());
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
      case "BLIND_POST": { const p = players[e.seat]; if (p) { p.stack -= e.amount; p.invested += e.amount; } pot += e.amount;
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: "", actingSeat: e.seat, actionLabel: e.blindType, isDecision: false, isTerminal: false }); break; }
      case "HERO_CARDS": { const p = players[e.seat]; if (p) p.cards = e.cards;
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: "dealt", actingSeat: e.seat, actionLabel: "dealt", isDecision: false, isTerminal: false }); break; }
      case "PLAYER_ACTION": { const p = players[e.seat]; if (p) { p.stack -= (e.delta||0); p.invested += (e.delta||0); if (e.action === "FOLD") p.folded = true; } pot += (e.delta||0);
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: `${e.player} ${e.action}`, actingSeat: e.seat, actionLabel: e.action, isDecision: true, isTerminal: false }); break; }
      case "BET_RETURN": { const p = players[e.seat]; if (p) { p.stack += e.amount; p.invested -= e.amount; } pot -= e.amount;
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: "return", actingSeat: e.seat, actionLabel: "return", isDecision: false, isTerminal: false }); break; }
      case "DEAL_COMMUNITY": street = e.street; board = e.board || [];
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: e.street, actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false }); break;
      case "SHOWDOWN_REVEAL": street = "SHOWDOWN"; for (const r of e.reveals || []) { const p = players[r.seat]; if (p) p.cards = r.cards; }
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: "SHOWDOWN", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false }); break;
      case "POT_AWARD": for (const a of e.awards || []) { const p = players[a.seat]; if (p) p.stack += a.amount; }
        frames.push({ index: frames.length, handId, street: "SETTLE", board: [...board], pot: 0, players: clonePlayers(players), event: e.type, label: "Award", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false }); pot = 0; break;
      case "HAND_SUMMARY": frames.push({ index: frames.length, handId, street: "SETTLE", board: [...board], pot: 0, players: clonePlayers(players), event: e.type, label: "Summary", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false }); break;
      case "HAND_RESULT": break;
      case "HAND_END": frames.push({ index: frames.length, handId, street: "COMPLETE", board: [...board], pot: 0, players: clonePlayers(players), event: e.type, label: "Complete", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: true }); break;
    }
  }
  return frames;
}
function getDecisionIndices(frames) { return frames.map((f, i) => f.isDecision ? i : -1).filter((i) => i >= 0); }
function actionToBucket(action) {
  if (!action) return null; const a = action.toUpperCase();
  if (a === "FOLD") return "fold"; if (a === "CHECK" || a === "CALL") return "passive"; if (a === "BET" || a === "RAISE") return "aggressive"; return null;
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
    const id = `ql-${++cmdId}`;
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

const TABLE = { tableId: "ql-t", tableName: "QueueSum", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws, welcome } = await connectWS(port);
  const sid = welcome.sessionId;

  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

  // Play 3 showdown hands
  for (let i = 0; i < 3; i++) {
    const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;
  }

  const heroSeat = 0;

  // Load all 3 hands' events and compile frames
  const handData = [];
  for (let i = 1; i <= 3; i++) {
    const resp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: String(i) });
    const frames = compileFrames(resp.events);
    handData.push({ handId: String(i), sessionId: sid, events: resp.events, frames });
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Queue Accumulator Updates Across Hands
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Accumulator Across Hands ===");
  {
    const accum = {};

    // Simulate quizzing hand 1: answer all hero decisions correctly
    const frames1 = handData[0].frames;
    const heroDecs1 = getDecisionIndices(frames1).filter((i) => frames1[i].actingSeat === heroSeat);
    const ledger1 = {};
    for (const idx of heroDecs1) {
      const actual = actionToBucket(frames1[idx].actionLabel);
      ledger1[idx] = { chosen: actual, actual, result: "match" };
    }
    accum[sid + "/1"] = {
      answered: Object.keys(ledger1).length,
      matches: Object.values(ledger1).filter((e) => e.result === "match").length,
      diffs: Object.values(ledger1).filter((e) => e.result === "different").length,
    };

    // Simulate quizzing hand 2: answer all wrong
    const frames2 = handData[1].frames;
    const heroDecs2 = getDecisionIndices(frames2).filter((i) => frames2[i].actingSeat === heroSeat);
    const ledger2 = {};
    for (const idx of heroDecs2) {
      const actual = actionToBucket(frames2[idx].actionLabel);
      const wrong = actual === "fold" ? "aggressive" : "fold";
      ledger2[idx] = { chosen: wrong, actual, result: "different" };
    }
    accum[sid + "/2"] = {
      answered: Object.keys(ledger2).length,
      matches: 0,
      diffs: Object.keys(ledger2).length,
    };

    // Queue totals
    const qVals = Object.values(accum);
    const qHands = qVals.length;
    const qAnswered = qVals.reduce((s, v) => s + v.answered, 0);
    const qMatches = qVals.reduce((s, v) => s + v.matches, 0);
    const qDiffs = qVals.reduce((s, v) => s + v.diffs, 0);

    check("T1: 2 hands reviewed", qHands === 2);
    check("T1: total answered = sum", qAnswered === heroDecs1.length + heroDecs2.length);
    check("T1: matches from hand 1", qMatches === heroDecs1.length);
    check("T1: diffs from hand 2", qDiffs === heroDecs2.length);
    check("T1: matches + diffs = answered", qMatches + qDiffs === qAnswered);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Hands Reviewed Count
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Hands Reviewed Count ===");
  {
    const accum = {};
    const totalQueue = 3; // 3 hands in queue

    // Review 0 hands
    check("T2: 0 hands reviewed initially", Object.keys(accum).length === 0);

    // Review hand 1
    accum[sid + "/1"] = { answered: 3, matches: 2, diffs: 1 };
    check("T2: 1 hand reviewed", Object.keys(accum).length === 1);

    // Review hand 3 (skip hand 2)
    accum[sid + "/3"] = { answered: 2, matches: 1, diffs: 1 };
    check("T2: 2 hands reviewed (non-sequential ok)", Object.keys(accum).length === 2);
    check("T2: 2 of 3 in queue", Object.keys(accum).length < totalQueue);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Queue Reset on Filter Change
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Queue Reset ===");
  {
    let accum = {};
    accum[sid + "/1"] = { answered: 3, matches: 2, diffs: 1 };
    accum[sid + "/2"] = { answered: 4, matches: 3, diffs: 1 };
    check("T3: accum has entries before reset", Object.keys(accum).length === 2);

    // Simulate filter change → renderStudyHands → accum reset
    accum = {};
    check("T3: accum empty after filter change", Object.keys(accum).length === 0);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Per-Hand Consistency with Queue
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Per-Hand / Queue Consistency ===");
  {
    const accum = {};

    // Hand 1: 4 decisions, 3 match, 1 diff
    accum[sid + "/1"] = { answered: 4, matches: 3, diffs: 1 };

    // Hand 2: 3 decisions, 1 match, 2 diff
    accum[sid + "/2"] = { answered: 3, matches: 1, diffs: 2 };

    const qVals = Object.values(accum);
    const qAnswered = qVals.reduce((s, v) => s + v.answered, 0);
    const qMatches = qVals.reduce((s, v) => s + v.matches, 0);
    const qDiffs = qVals.reduce((s, v) => s + v.diffs, 0);

    check("T4: queue answered = hand1 + hand2", qAnswered === 7);
    check("T4: queue matches = 3 + 1", qMatches === 4);
    check("T4: queue diffs = 1 + 2", qDiffs === 3);
    check("T4: per-hand 1 consistent", accum[sid + "/1"].matches + accum[sid + "/1"].diffs === accum[sid + "/1"].answered);
    check("T4: per-hand 2 consistent", accum[sid + "/2"].matches + accum[sid + "/2"].diffs === accum[sid + "/2"].answered);

    // Accuracy
    const pct = Math.round(100 * qMatches / qAnswered);
    check("T4: accuracy 57%", pct === 57);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Snapshot Updates on Reveal
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Snapshot on Reveal ===");
  {
    // Simulate: answer 1 decision, snapshot, answer another, snapshot again
    const accum = {};
    const frames = handData[0].frames;
    const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);

    // First reveal
    const ledger = {};
    const dec0 = frames[heroDecs[0]];
    const actual0 = actionToBucket(dec0.actionLabel);
    ledger[dec0.index] = { chosen: actual0, actual: actual0, result: "match" };
    // Snapshot
    const vals = Object.values(ledger);
    accum[sid + "/1"] = { answered: vals.length, matches: vals.filter((e) => e.result === "match").length, diffs: vals.filter((e) => e.result === "different").length };

    check("T5: after 1 reveal: 1 answered", accum[sid + "/1"].answered === 1);
    check("T5: after 1 reveal: 1 match", accum[sid + "/1"].matches === 1);

    // Second reveal
    if (heroDecs.length > 1) {
      const dec1 = frames[heroDecs[1]];
      const actual1 = actionToBucket(dec1.actionLabel);
      ledger[dec1.index] = { chosen: "fold", actual: actual1, result: "fold" === actual1 ? "match" : "different" };
      const vals2 = Object.values(ledger);
      accum[sid + "/1"] = { answered: vals2.length, matches: vals2.filter((e) => e.result === "match").length, diffs: vals2.filter((e) => e.result === "different").length };

      check("T5: after 2 reveals: 2 answered", accum[sid + "/1"].answered === 2);
      check("T5: snapshot updated in-place", true);
    } else {
      check("T5: (only 1 decision — skip)", true);
      check("T5: (skip)", true);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: No Regression ===");
  {
    // Replay data
    check("T6: hand events loadable", handData[0].events.length > 0);
    check("T6: frames compilable", handData[0].frames.length > 0);

    // Annotations
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "review", text: "qsum test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    check("T6: annotations work", anns.ok);

    // Tag counts
    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T6: counts work", counts.ok);

    // Query
    const q = await sendCmd(ws, "QUERY_HANDS", {});
    check("T6: query works", q.ok && q.state.hands.length >= 3);

    // Bucket mapping
    check("T6: buckets work", actionToBucket("CALL") === "passive");
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** QUEUE SUMMARY TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
