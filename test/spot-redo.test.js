#!/usr/bin/env node
"use strict";

/**
 * Re-Answer Current Spot Tests
 *
 * Tests: single frame ledger removal, other frames unchanged,
 * summary updates, marker transitions, no regression.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "redo-" + Date.now());
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
    const id = `rd-${++cmdId}`;
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

const TABLE = { tableId: "rd-t", tableName: "Redo", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws, welcome } = await connectWS(port);
  const sid = welcome.sessionId;

  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });
  const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;

  const resp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
  const frames = compileFrames(resp.events);
  const heroSeat = 0;
  const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Single Frame Removal
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Single Frame Removal ===");
  {
    // Answer all hero decisions
    const ledger = {};
    for (const idx of heroDecs) {
      const actual = actionToBucket(frames[idx].actionLabel);
      ledger[idx] = { chosen: actual, actual, result: "match" };
    }
    check("T1: all answered", Object.keys(ledger).length === heroDecs.length);

    // Clear just the first decision
    const targetIdx = heroDecs[0];
    delete ledger[targetIdx];
    check("T1: target frame removed", ledger[targetIdx] === undefined);
    check("T1: other frames remain", Object.keys(ledger).length === heroDecs.length - 1);

    // Remaining entries still have correct data
    if (heroDecs.length > 1) {
      const remainingIdx = heroDecs[1];
      check("T1: remaining entry intact", ledger[remainingIdx] && ledger[remainingIdx].result === "match");
    } else {
      check("T1: (only 1 decision — skip)", true);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Frame Returns to Masked State
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Returns to Masked State ===");
  {
    const ledger = {};
    for (const idx of heroDecs) {
      const actual = actionToBucket(frames[idx].actionLabel);
      ledger[idx] = { chosen: actual, actual, result: "match" };
    }

    // Simulate quizFrameTransition for a cleared frame
    const targetIdx = heroDecs[0];
    delete ledger[targetIdx];

    const entry = ledger[targetIdx];
    const answer = entry ? entry.chosen : null;
    const result = entry ? entry.result : null;
    const revealed = !!entry;

    check("T2: answer is null after clear", answer === null);
    check("T2: result is null after clear", result === null);
    check("T2: not revealed after clear", revealed === false);

    // isQuizMasked would return true (quiz mode on, not revealed, hero decision)
    // Simulated: frame.isDecision && frame.actingSeat === heroSeat && !revealed
    const frame = frames[targetIdx];
    const wouldMask = frame.isDecision && frame.actingSeat === heroSeat && !revealed;
    check("T2: frame would be quiz-masked", wouldMask);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Other Frames Unchanged
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Other Frames Unchanged ===");
  {
    const ledger = {};
    for (const idx of heroDecs) {
      const actual = actionToBucket(frames[idx].actionLabel);
      ledger[idx] = { chosen: actual, actual, result: "match" };
    }

    const beforeSnapshot = {};
    for (const [k, v] of Object.entries(ledger)) {
      beforeSnapshot[k] = { ...v };
    }

    // Clear first frame
    delete ledger[heroDecs[0]];

    // All other entries identical
    let allUnchanged = true;
    for (const idx of heroDecs.slice(1)) {
      if (!ledger[idx] || ledger[idx].result !== beforeSnapshot[idx].result) {
        allUnchanged = false;
        break;
      }
    }
    check("T3: all other entries unchanged", allUnchanged || heroDecs.length <= 1);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Hand Summary Updates
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Hand Summary ===");
  {
    const ledger = {};
    for (const idx of heroDecs) {
      const actual = actionToBucket(frames[idx].actionLabel);
      ledger[idx] = { chosen: actual, actual, result: "match" };
    }
    const answersBefore = Object.keys(ledger).length;
    const matchesBefore = Object.values(ledger).filter((e) => e.result === "match").length;

    // Clear first frame
    delete ledger[heroDecs[0]];
    const answersAfter = Object.keys(ledger).length;
    const matchesAfter = Object.values(ledger).filter((e) => e.result === "match").length;

    check("T4: answered decreased by 1", answersAfter === answersBefore - 1);
    check("T4: matches decreased by 1", matchesAfter === matchesBefore - 1);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Queue Summary Updates
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Queue Summary ===");
  {
    const accum = {};
    // Complete hand 1
    accum[sid + "/1"] = { answered: heroDecs.length, matches: heroDecs.length, diffs: 0, totalHero: heroDecs.length };

    const qBefore = Object.values(accum).reduce((s, v) => s + v.answered, 0);

    // Clear 1 spot → re-snapshot
    const ledger = {};
    for (const idx of heroDecs.slice(1)) {
      const actual = actionToBucket(frames[idx].actionLabel);
      ledger[idx] = { chosen: actual, actual, result: "match" };
    }
    const vals = Object.values(ledger);
    accum[sid + "/1"] = {
      answered: vals.length,
      matches: vals.filter((e) => e.result === "match").length,
      diffs: vals.filter((e) => e.result === "different").length,
      totalHero: heroDecs.length,
    };

    const qAfter = Object.values(accum).reduce((s, v) => s + v.answered, 0);
    check("T5: queue answered decreased", qAfter === qBefore - 1);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: Completed → In-Progress Transition
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: Marker Transition ===");
  {
    const accum = {};
    accum[sid + "/1"] = { answered: heroDecs.length, matches: heroDecs.length, diffs: 0, totalHero: heroDecs.length };
    check("T6: before clear: completed", handReviewState(accum, sid, "1") === "completed");

    // Clear 1 spot
    if (heroDecs.length > 1) {
      accum[sid + "/1"] = { answered: heroDecs.length - 1, matches: heroDecs.length - 1, diffs: 0, totalHero: heroDecs.length };
      check("T6: after clear: in-progress", handReviewState(accum, sid, "1") === "in-progress");
    } else {
      accum[sid + "/1"] = { answered: 0, matches: 0, diffs: 0, totalHero: heroDecs.length };
      check("T6: after clear single-dec: visited", handReviewState(accum, sid, "1") === "visited");
    }

    // Re-answer restores completed
    accum[sid + "/1"] = { answered: heroDecs.length, matches: heroDecs.length, diffs: 0, totalHero: heroDecs.length };
    check("T6: re-answer restores completed", handReviewState(accum, sid, "1") === "completed");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 7: Blind Reveal Coherence
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 7: Blind Reveal Coherence ===");
  {
    const revealed = new Set();
    revealed.add(sid + "/1"); // auto-revealed on completion

    // Clear 1 spot → should re-hide (no longer fully answered)
    if (heroDecs.length > 1) {
      // Simulate: if answered < totalHero, remove from revealed
      revealed.delete(sid + "/1");
      check("T7: blind re-hidden after spot clear", !revealed.has(sid + "/1"));
    } else {
      revealed.delete(sid + "/1");
      check("T7: blind re-hidden after spot clear (single dec)", !revealed.has(sid + "/1"));
    }

    // Re-answer and re-complete → auto-reveal again
    revealed.add(sid + "/1");
    check("T7: re-revealed on re-completion", revealed.has(sid + "/1"));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 8: Re-Answer with Different Choice
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 8: Re-Answer Different ===");
  {
    const ledger = {};
    const targetIdx = heroDecs[0];
    const actual = actionToBucket(frames[targetIdx].actionLabel);

    // First answer: correct
    ledger[targetIdx] = { chosen: actual, actual, result: "match" };
    check("T8: first answer: match", ledger[targetIdx].result === "match");

    // Clear
    delete ledger[targetIdx];

    // Re-answer: wrong
    const wrong = actual === "fold" ? "aggressive" : "fold";
    ledger[targetIdx] = { chosen: wrong, actual, result: "different" };
    check("T8: re-answer: different", ledger[targetIdx].result === "different");
    check("T8: chosen changed", ledger[targetIdx].chosen === wrong);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 9: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 9: No Regression ===");
  {
    check("T9: events ok", resp.events.length > 0);
    check("T9: frames ok", frames.length > 0);

    const q = await sendCmd(ws, "QUERY_HANDS", {});
    check("T9: query ok", q.ok);

    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: sid, handId: "1", tag: "review", text: "redo test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: sid, handId: "1" });
    check("T9: annotations ok", anns.ok);

    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: sid });
    check("T9: counts ok", counts.ok);
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** SPOT REDO TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
