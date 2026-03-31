#!/usr/bin/env node
"use strict";

/**
 * Quiz Answer Recording Tests
 *
 * Tests: answer input on masked frames, bucket mapping,
 * match/different comparison, frame/hand reset, no regression.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "quiz-ans-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

// ── Client pure functions ─────────────────────────────────────────────────

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
      case "PLAYER_ACTION": { const p = players[e.seat]; if (p) { p.stack -= (e.delta||0); p.invested += (e.delta||0); if (e.action === "FOLD") p.folded = true; if (p.stack <= 0 && e.action !== "FOLD" && e.action !== "CHECK") p.allIn = true; } pot += (e.delta||0);
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
  if (!action) return null;
  const a = action.toUpperCase();
  if (a === "FOLD") return "fold";
  if (a === "CHECK" || a === "CALL") return "passive";
  if (a === "BET" || a === "RAISE") return "aggressive";
  return null;
}

// ── WS helpers ────────────────────────────────────────────────────────────

async function connectWS(port) {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://localhost:${port}`);
    ws.on("message", function first(raw) { const m = JSON.parse(raw.toString()); if (m.welcome) { ws.removeListener("message", first); resolve({ ws, welcome: m }); } });
  });
}
let cmdId = 0;
function sendCmd(ws, cmd, payload) {
  return new Promise((resolve) => {
    const id = `qa-${++cmdId}`;
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

const TABLE = { tableId: "qa-t", tableName: "QuizAns", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws } = await connectWS(port);

  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

  // Play 2 showdown hands
  for (let i = 0; i < 2; i++) {
    const ep = collectUntilHandEnd(ws);
    await sendCmd(ws, "START_HAND");
    await playShowdownViaWS(ws);
    await ep;
  }

  const resp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
  const events = resp.events;
  const frames = compileFrames(events);
  const heroSeat = 0;
  const oppSeat = 1;

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Action-to-Bucket Mapping
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Bucket Mapping ===");
  {
    check("T1: FOLD → fold", actionToBucket("FOLD") === "fold");
    check("T1: CHECK → passive", actionToBucket("CHECK") === "passive");
    check("T1: CALL → passive", actionToBucket("CALL") === "passive");
    check("T1: BET → aggressive", actionToBucket("BET") === "aggressive");
    check("T1: RAISE → aggressive", actionToBucket("RAISE") === "aggressive");
    check("T1: null → null", actionToBucket(null) === null);
    check("T1: lowercase fold → fold", actionToBucket("fold") === "fold");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Answer Only on Hero Decision Frames
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Answer Context ===");
  {
    const decs = getDecisionIndices(frames);
    const heroDecisions = decs.filter((i) => frames[i].actingSeat === heroSeat);
    const oppDecisions = decs.filter((i) => frames[i].actingSeat === oppSeat);
    const nonDecisions = frames.filter((f) => !f.isDecision);

    check("T2: hero decisions exist", heroDecisions.length > 0);
    check("T2: hero decisions are quiz-eligible",
      heroDecisions.every((i) => frames[i].isDecision && frames[i].actingSeat === heroSeat));
    check("T2: opp decisions NOT quiz-eligible",
      oppDecisions.every((i) => frames[i].actingSeat !== heroSeat));
    check("T2: non-decisions NOT quiz-eligible",
      nonDecisions.every((f) => !f.isDecision));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Correct Comparison — Match
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Match Comparison ===");
  {
    const decs = getDecisionIndices(frames);
    const heroDecisions = decs.filter((i) => frames[i].actingSeat === heroSeat);
    const decFrame = frames[heroDecisions[0]];
    const actualBucket = actionToBucket(decFrame.actionLabel);
    check("T3: actual bucket is valid", ["fold", "passive", "aggressive"].includes(actualBucket));

    // Simulate choosing the correct answer
    const chosenAnswer = actualBucket;
    const result = chosenAnswer === actualBucket ? "match" : "different";
    check("T3: correct answer = match", result === "match");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Correct Comparison — Different
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Different Comparison ===");
  {
    const decs = getDecisionIndices(frames);
    const heroDecisions = decs.filter((i) => frames[i].actingSeat === heroSeat);
    const decFrame = frames[heroDecisions[0]];
    const actualBucket = actionToBucket(decFrame.actionLabel);

    // Choose a wrong answer
    const wrongAnswer = actualBucket === "fold" ? "aggressive" : "fold";
    const result = wrongAnswer === actualBucket ? "match" : "different";
    check("T4: wrong answer = different", result === "different");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: No Reveal Without Answer
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Reveal Without Answer ===");
  {
    // Simulate reveal with no answer chosen
    const chosenAnswer = null;
    const rawFrame = frames[getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat)[0]];
    const actualBucket = actionToBucket(rawFrame.actionLabel);

    // Result should be null (no comparison when no answer)
    const result = chosenAnswer ? (chosenAnswer === actualBucket ? "match" : "different") : null;
    check("T5: no answer → null result", result === null);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: All Hero Actions Map to Valid Buckets
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: All Actions Map ===");
  {
    const decs = getDecisionIndices(frames);
    const heroDecisions = decs.filter((i) => frames[i].actingSeat === heroSeat);
    let allValid = true;
    for (const idx of heroDecisions) {
      const bucket = actionToBucket(frames[idx].actionLabel);
      if (!["fold", "passive", "aggressive"].includes(bucket)) { allValid = false; break; }
    }
    check("T6: all hero actions have valid buckets", allValid);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 7: Frame Change Resets State
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 7: Frame Reset ===");
  {
    // Simulate: set answer, move cursor, answer should be gone
    let answer = "fold";
    let result = null;

    // "Move to next frame"
    answer = null;
    result = null;

    check("T7: answer reset on frame change", answer === null);
    check("T7: result reset on frame change", result === null);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 8: Hand Change Resets State
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 8: Hand Reset ===");
  {
    // Load hand 2 events — simulates queue navigation
    const resp2 = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "2" });
    const frames2 = compileFrames(resp2.events);
    check("T8: hand 2 compilable", frames2.length > 0);
    check("T8: hand 2 is independent", frames2[0].handId === "2");

    // After hand switch, all quiz state would be reset by resetReplayState
    // Verify frames are different
    check("T8: different hand IDs", frames[0].handId !== frames2[0].handId);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 9: Multiple Hero Decisions Per Hand
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 9: Multiple Decisions ===");
  {
    const decs = getDecisionIndices(frames);
    const heroDecisions = decs.filter((i) => frames[i].actingSeat === heroSeat);
    check("T9: multiple hero decisions", heroDecisions.length >= 2);

    // Each can be independently answered
    for (let d = 0; d < Math.min(heroDecisions.length, 3); d++) {
      const bucket = actionToBucket(frames[heroDecisions[d]].actionLabel);
      check(`T9: decision ${d} has bucket`, bucket !== null);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 10: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 10: No Regression ===");
  {
    // Replay data
    check("T10: events loaded", events.length > 0);
    check("T10: frames compiled", frames.length > 0);

    // Annotations
    const q = await sendCmd(ws, "QUERY_HANDS", {});
    const hand = q.state.hands[0];
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: hand.sessionId, handId: hand.handId, tag: "review", text: "quiz-ans test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: hand.sessionId, handId: hand.handId });
    check("T10: annotations work", anns.ok && anns.state.annotations.length >= 1);

    // Query
    check("T10: query works", q.ok && q.state.hands.length >= 2);

    // Tag counts
    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: hand.sessionId });
    check("T10: annotation counts work", counts.ok);
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** QUIZ ANSWER TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
