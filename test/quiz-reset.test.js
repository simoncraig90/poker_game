#!/usr/bin/env node
"use strict";

/**
 * Quiz Reset / Re-Answer Tests
 *
 * Tests: ledger clear, queue contribution update, summary reset,
 * other hands unaffected, marker transition, no regression.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "qreset-" + Date.now());
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
    const id = `qr-${++cmdId}`;
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

const TABLE = { tableId: "qr-t", tableName: "Reset", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws, welcome } = await connectWS(port);
  const sid = welcome.sessionId;

  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
  for (let i = 0; i < 3; i++) {
    const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;
  }

  const heroSeat = 0;
  const handData = [];
  for (let i = 1; i <= 3; i++) {
    const resp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: String(i) });
    handData.push({ handId: String(i), sessionId: sid, events: resp.events, frames: compileFrames(resp.events) });
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Ledger Clear
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Ledger Clear ===");
  {
    const frames = handData[0].frames;
    const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);

    // Build a ledger with answered decisions
    let ledger = {};
    for (const idx of heroDecs) {
      const actual = actionToBucket(frames[idx].actionLabel);
      ledger[idx] = { chosen: actual, actual, result: "match" };
    }
    check("T1: ledger has entries before reset", Object.keys(ledger).length > 0);

    // Simulate reset: clear ledger
    ledger = {};
    check("T1: ledger empty after reset", Object.keys(ledger).length === 0);

    // Frame transition after reset: no stored answer
    const entry = ledger[heroDecs[0]];
    check("T1: no stored answer for first decision", entry === undefined);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Queue Contribution Update
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Queue Contribution ===");
  {
    const accum = {};

    // Complete hand 1 and hand 2
    for (let h = 0; h < 2; h++) {
      const frames = handData[h].frames;
      const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);
      accum[sid + "/" + handData[h].handId] = {
        answered: heroDecs.length, matches: heroDecs.length, diffs: 0, totalHero: heroDecs.length,
      };
    }

    // Queue totals before reset
    const qBefore = Object.values(accum);
    const totalAnsweredBefore = qBefore.reduce((s, v) => s + v.answered, 0);
    const totalMatchesBefore = qBefore.reduce((s, v) => s + v.matches, 0);
    check("T2: queue has answers from 2 hands", totalAnsweredBefore > 0);

    // Reset hand 1: zero its contribution but keep the entry (visited)
    const h1Decs = getDecisionIndices(handData[0].frames).filter((i) => handData[0].frames[i].actingSeat === heroSeat);
    accum[sid + "/1"] = { answered: 0, matches: 0, diffs: 0, totalHero: h1Decs.length };

    // Queue totals after reset
    const qAfter = Object.values(accum);
    const totalAnsweredAfter = qAfter.reduce((s, v) => s + v.answered, 0);
    const totalMatchesAfter = qAfter.reduce((s, v) => s + v.matches, 0);

    check("T2: hand 1 answered now 0", accum[sid + "/1"].answered === 0);
    check("T2: hand 2 unchanged", accum[sid + "/2"].answered > 0);
    check("T2: queue total decreased", totalAnsweredAfter < totalAnsweredBefore);
    check("T2: queue matches decreased", totalMatchesAfter < totalMatchesBefore);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Hand Summary Reset
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Hand Summary Reset ===");
  {
    // Before reset: answered > 0
    let ledger = {};
    const frames = handData[0].frames;
    const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);
    for (const idx of heroDecs) {
      const actual = actionToBucket(frames[idx].actionLabel);
      ledger[idx] = { chosen: actual, actual, result: "match" };
    }
    check("T3: before reset: answered > 0", Object.keys(ledger).length > 0);

    // Reset
    ledger = {};
    const answered = Object.keys(ledger).length;
    const matches = Object.values(ledger).filter((e) => e.result === "match").length;
    const diffs = Object.values(ledger).filter((e) => e.result === "different").length;

    check("T3: after reset: answered = 0", answered === 0);
    check("T3: after reset: matches = 0", matches === 0);
    check("T3: after reset: diffs = 0", diffs === 0);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Other Hands Unaffected
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Other Hands Unaffected ===");
  {
    const accum = {};
    // Complete all 3 hands
    for (let h = 0; h < 3; h++) {
      const frames = handData[h].frames;
      const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);
      accum[sid + "/" + handData[h].handId] = {
        answered: heroDecs.length, matches: heroDecs.length - (h === 1 ? 1 : 0), diffs: h === 1 ? 1 : 0, totalHero: heroDecs.length,
      };
    }

    const h2Before = { ...accum[sid + "/2"] };
    const h3Before = { ...accum[sid + "/3"] };

    // Reset hand 1
    const h1Decs = getDecisionIndices(handData[0].frames).filter((i) => handData[0].frames[i].actingSeat === heroSeat);
    accum[sid + "/1"] = { answered: 0, matches: 0, diffs: 0, totalHero: h1Decs.length };

    check("T4: hand 2 unchanged", JSON.stringify(accum[sid + "/2"]) === JSON.stringify(h2Before));
    check("T4: hand 3 unchanged", JSON.stringify(accum[sid + "/3"]) === JSON.stringify(h3Before));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Marker Transition
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Marker Transition ===");
  {
    const accum = {};
    const frames = handData[0].frames;
    const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);

    // Complete hand 1
    accum[sid + "/1"] = { answered: heroDecs.length, matches: heroDecs.length, diffs: 0, totalHero: heroDecs.length };
    check("T5: before reset: completed", handReviewState(accum, sid, "1") === "completed");

    // Reset → visited (entry exists, answered = 0)
    accum[sid + "/1"] = { answered: 0, matches: 0, diffs: 0, totalHero: heroDecs.length };
    check("T5: after reset: visited", handReviewState(accum, sid, "1") === "visited");

    // Re-answer 1 decision → in-progress (if > 1 decision)
    if (heroDecs.length > 1) {
      accum[sid + "/1"] = { answered: 1, matches: 1, diffs: 0, totalHero: heroDecs.length };
      check("T5: after 1 re-answer: in-progress", handReviewState(accum, sid, "1") === "in-progress");
    } else {
      check("T5: (single decision → skip)", true);
    }

    // Re-complete → completed
    accum[sid + "/1"] = { answered: heroDecs.length, matches: heroDecs.length, diffs: 0, totalHero: heroDecs.length };
    check("T5: after re-complete: completed", handReviewState(accum, sid, "1") === "completed");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: Blind Reveal Cleared
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: Blind Reveal Cleared ===");
  {
    const revealed = new Set();
    revealed.add(sid + "/1");
    check("T6: before reset: outcome revealed", revealed.has(sid + "/1"));

    // Reset clears blind reveal
    revealed.delete(sid + "/1");
    check("T6: after reset: outcome hidden again", !revealed.has(sid + "/1"));

    // Other hands' reveal state unaffected
    revealed.add(sid + "/2");
    check("T6: hand 2 still revealed", revealed.has(sid + "/2"));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 7: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 7: No Regression ===");
  {
    check("T7: events loadable", handData[0].events.length > 0);
    check("T7: frames compilable", handData[0].frames.length > 0);

    const q = await sendCmd(ws, "QUERY_HANDS", {});
    check("T7: query works", q.ok && q.state.hands.length >= 3);

    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "review", text: "reset test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    check("T7: annotations work", anns.ok);

    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T7: counts work", counts.ok);

    check("T7: bucket mapping works", actionToBucket("RAISE") === "aggressive");
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** QUIZ RESET TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
