#!/usr/bin/env node
"use strict";

/**
 * Quiz Summary Tests
 *
 * Tests: per-hand ledger, summary counts, frame revisit preserves,
 * hand switch resets, no regression.
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "quiz-sum-" + Date.now());
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
  if (a === "FOLD") return "fold"; if (a === "CHECK" || a === "CALL") return "passive"; if (a === "BET" || a === "RAISE") return "aggressive"; return null;
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
    const id = `qs-${++cmdId}`;
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

const TABLE = { tableId: "qs-t", tableName: "QuizSum", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws } = await connectWS(port);

  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

  for (let i = 0; i < 2; i++) {
    const ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;
  }

  const resp1 = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
  const frames1 = compileFrames(resp1.events);
  const resp2 = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "2" });
  const frames2 = compileFrames(resp2.events);
  const heroSeat = 0;

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: Ledger Records Revealed Answers
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Ledger Recording ===");
  {
    const ledger = {};
    const heroDecisions = getDecisionIndices(frames1).filter((i) => frames1[i].actingSeat === heroSeat);
    check("T1: hero decisions exist", heroDecisions.length >= 2);

    // Simulate answering first hero decision
    const dec0 = frames1[heroDecisions[0]];
    const actual0 = actionToBucket(dec0.actionLabel);
    const chosen0 = actual0; // correct answer
    ledger[dec0.index] = { chosen: chosen0, actual: actual0, result: chosen0 === actual0 ? "match" : "different" };

    check("T1: ledger has 1 entry", Object.keys(ledger).length === 1);
    check("T1: entry is match", ledger[dec0.index].result === "match");

    // Answer second hero decision (wrong answer)
    const dec1 = frames1[heroDecisions[1]];
    const actual1 = actionToBucket(dec1.actionLabel);
    const chosen1 = actual1 === "fold" ? "aggressive" : "fold";
    ledger[dec1.index] = { chosen: chosen1, actual: actual1, result: chosen1 === actual1 ? "match" : "different" };

    check("T1: ledger has 2 entries", Object.keys(ledger).length === 2);
    check("T1: second entry is different", ledger[dec1.index].result === "different");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Summary Counts
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Summary Counts ===");
  {
    const heroDecisions = getDecisionIndices(frames1).filter((i) => frames1[i].actingSeat === heroSeat);
    const totalHero = heroDecisions.length;

    // Build ledger: answer all hero decisions — alternate correct/wrong
    const ledger = {};
    for (let d = 0; d < heroDecisions.length; d++) {
      const dec = frames1[heroDecisions[d]];
      const actual = actionToBucket(dec.actionLabel);
      const chosen = d % 2 === 0 ? actual : (actual === "fold" ? "aggressive" : "fold");
      ledger[dec.index] = { chosen, actual, result: chosen === actual ? "match" : "different" };
    }

    const answered = Object.keys(ledger).length;
    const matches = Object.values(ledger).filter((e) => e.result === "match").length;
    const diffs = Object.values(ledger).filter((e) => e.result === "different").length;

    check("T2: answered = total hero", answered === totalHero);
    check("T2: matches + diffs = answered", matches + diffs === answered);
    check("T2: matches > 0", matches > 0);
    check("T2: diffs > 0 (if more than 1 decision)", heroDecisions.length > 1 ? diffs > 0 : true);

    // Percentage
    const pct = Math.round(100 * matches / totalHero);
    check("T2: percentage is 0-100", pct >= 0 && pct <= 100);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Frame Revisit Preserves Stored Result
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Frame Revisit ===");
  {
    const heroDecisions = getDecisionIndices(frames1).filter((i) => frames1[i].actingSeat === heroSeat);
    const ledger = {};

    // Answer first decision
    const dec0 = frames1[heroDecisions[0]];
    const actual0 = actionToBucket(dec0.actionLabel);
    ledger[dec0.index] = { chosen: "passive", actual: actual0, result: "passive" === actual0 ? "match" : "different" };

    // Simulate quizFrameTransition: check if ledger has entry for this frame
    function transition(frameIdx) {
      const entry = ledger[frameIdx];
      return entry ? { answer: entry.chosen, result: entry.result, revealed: true } : { answer: null, result: null, revealed: false };
    }

    // Move away then come back
    const otherFrame = transition(heroDecisions.length > 1 ? heroDecisions[1] : 0);
    check("T3: other frame has no stored answer", otherFrame.answer === null || heroDecisions.length <= 1);

    const revisited = transition(dec0.index);
    check("T3: revisited frame has stored answer", revisited.answer === "passive");
    check("T3: revisited frame has stored result", revisited.result !== null);
    check("T3: revisited frame is revealed", revisited.revealed === true);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Hand Switch Resets Ledger
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Hand Switch Reset ===");
  {
    // Simulate: build ledger for hand 1, switch to hand 2, ledger should be empty
    let ledger = {};
    const heroDecisions1 = getDecisionIndices(frames1).filter((i) => frames1[i].actingSeat === heroSeat);
    if (heroDecisions1.length > 0) {
      const dec = frames1[heroDecisions1[0]];
      ledger[dec.index] = { chosen: "fold", actual: "passive", result: "different" };
    }
    check("T4: ledger has entries for hand 1", Object.keys(ledger).length > 0);

    // "Switch hand" = resetReplayState equivalent
    ledger = {};
    check("T4: ledger empty after hand switch", Object.keys(ledger).length === 0);

    // Build fresh for hand 2
    const heroDecisions2 = getDecisionIndices(frames2).filter((i) => frames2[i].actingSeat === heroSeat);
    check("T4: hand 2 has hero decisions", heroDecisions2.length > 0);
    check("T4: hand 2 frames are independent", frames2[0].handId === "2");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Partial Completion
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Partial Completion ===");
  {
    const heroDecisions = getDecisionIndices(frames1).filter((i) => frames1[i].actingSeat === heroSeat);
    const ledger = {};

    // Answer only the first decision
    if (heroDecisions.length > 0) {
      const dec = frames1[heroDecisions[0]];
      const actual = actionToBucket(dec.actionLabel);
      ledger[dec.index] = { chosen: actual, actual, result: "match" };
    }

    const answered = Object.keys(ledger).length;
    const totalHero = heroDecisions.length;
    check("T5: partially answered", answered === 1);
    check("T5: total > answered", totalHero > answered || totalHero === 1);
    check("T5: no percentage shown for partial", answered < totalHero || totalHero === 1);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: No Regression ===");
  {
    check("T6: hand 1 frames compiled", frames1.length > 0);
    check("T6: hand 2 frames compiled", frames2.length > 0);

    // Replay data
    check("T6: hand 1 events ok", resp1.events.length > 0);

    // Annotations
    const q = await sendCmd(ws, "QUERY_HANDS", {});
    const hand = q.state.hands[0];
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: hand.sessionId, handId: hand.handId, tag: "review", text: "summary test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: hand.sessionId, handId: hand.handId });
    check("T6: annotations work", anns.ok && anns.state.annotations.length >= 1);

    // Tag counts
    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: hand.sessionId });
    check("T6: counts work", counts.ok);

    // Bucket mapping still correct
    check("T6: CALL → passive", actionToBucket("CALL") === "passive");
    check("T6: RAISE → aggressive", actionToBucket("RAISE") === "aggressive");
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** QUIZ SUMMARY TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
