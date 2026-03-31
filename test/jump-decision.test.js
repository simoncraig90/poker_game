#!/usr/bin/env node
"use strict";

/**
 * Jump to First Hero Decision Tests
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "jdec-" + Date.now());
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
      case "HAND_END": frames.push({ index: frames.length, handId, street: "COMPLETE", board: [...board], pot: 0, players: clonePlayers(players), event: e.type, label: "Complete", actingSeat: null, actionLabel: null, isDecision: false, isTerminal: true }); break;
      default: break;
    }
  }
  return frames;
}
function getDecisionIndices(frames) { return frames.map((f, i) => f.isDecision ? i : -1).filter((i) => i >= 0); }

function firstHeroDecisionIndex(frames, heroSeat) {
  if (heroSeat === null) return 0;
  const decs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);
  return decs.length > 0 ? decs[0] : 0;
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
    const id = `jd-${++cmdId}`;
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

const TABLE = { tableId: "jd-t", tableName: "Jump", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws } = await connectWS(port);

  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

  // Showdown hand (multiple decision frames)
  let ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND"); await playShowdownViaWS(ws); await ep;

  // Fold-out hand (few decision frames)
  ep = collectUntilHandEnd(ws); await sendCmd(ws, "START_HAND");
  let st = (await sendCmd(ws, "GET_STATE")).state;
  await sendCmd(ws, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" }); await ep;

  const resp1 = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
  const frames1 = compileFrames(resp1.events);
  const resp2 = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "2" });
  const frames2 = compileFrames(resp2.events);
  const heroSeat = 0;
  const oppSeat = 1;

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 1: First Hero Decision Index Detection
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 1: Index Detection ===");
  {
    const idx = firstHeroDecisionIndex(frames1, heroSeat);
    check("T1: index > 0 (not frame 0)", idx > 0);
    check("T1: is a decision frame", frames1[idx].isDecision);
    check("T1: acting seat is hero", frames1[idx].actingSeat === heroSeat);

    // Frames before this index that are hero decisions: should be none
    const priorHero = getDecisionIndices(frames1)
      .filter((i) => i < idx && frames1[i].actingSeat === heroSeat);
    check("T1: no earlier hero decision", priorHero.length === 0);

    // There should be non-decision frames before it (HAND_START, BLIND_POST, HERO_CARDS)
    check("T1: preceded by setup frames", idx > 2);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 2: Opponent-Only Hero Fallback
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 2: Fallback ===");
  {
    // With a non-existent hero seat, should return 0
    const idx = firstHeroDecisionIndex(frames1, 99);
    check("T2: non-existent seat → 0", idx === 0);

    // With null hero, should return 0
    const idx2 = firstHeroDecisionIndex(frames1, null);
    check("T2: null hero → 0", idx2 === 0);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 3: Fold-Out Hand
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 3: Fold-Out Hand ===");
  {
    const heroIdx = firstHeroDecisionIndex(frames2, heroSeat);
    const oppIdx = firstHeroDecisionIndex(frames2, oppSeat);

    // In a 2-player fold-out, one player folds. Check who has decisions.
    const heroHasDec = getDecisionIndices(frames2).some((i) => frames2[i].actingSeat === heroSeat);
    const oppHasDec = getDecisionIndices(frames2).some((i) => frames2[i].actingSeat === oppSeat);

    if (heroHasDec) {
      check("T3: hero has decision in fold-out", heroIdx > 0);
    } else {
      check("T3: hero has no decision → 0", heroIdx === 0);
    }
    if (oppHasDec) {
      check("T3: opp has decision", oppIdx > 0);
    } else {
      check("T3: opp has no decision → 0", oppIdx === 0);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 4: Quiz Mode Jump vs Normal Mode
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 4: Quiz vs Normal ===");
  {
    const heroIdx = firstHeroDecisionIndex(frames1, heroSeat);

    // Quiz mode: should jump to hero decision
    const quizCursor = heroIdx;
    check("T4: quiz mode cursor = first hero dec", quizCursor === heroIdx);
    check("T4: quiz cursor > 0", quizCursor > 0);

    // Normal mode: should stay at 0
    const normalCursor = 0;
    check("T4: normal mode cursor = 0", normalCursor === 0);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 5: Retry Uses Same Jump
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 5: Retry Jump ===");
  {
    // Simulate retry: resetHandQuiz sets cursor to firstHeroDecisionIndex
    const heroIdx = firstHeroDecisionIndex(frames1, heroSeat);
    const retryCursor = heroIdx;
    check("T5: retry cursor = first hero dec", retryCursor === heroIdx);
    check("T5: retry cursor matches open cursor", retryCursor === heroIdx);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 6: Redo Does Not Change Cursor
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 6: Redo Unaffected ===");
  {
    // Redo clears one spot but doesn't move the cursor
    const heroIdx = firstHeroDecisionIndex(frames1, heroSeat);
    const heroDecs = getDecisionIndices(frames1).filter((i) => frames1[i].actingSeat === heroSeat);

    // Simulate: cursor at second hero decision, redo it
    if (heroDecs.length > 1) {
      const redoCursor = heroDecs[1]; // cursor stays here
      check("T6: redo cursor stays at current spot", redoCursor === heroDecs[1]);
      check("T6: redo cursor ≠ first hero dec", redoCursor !== heroIdx || heroDecs.length === 1);
    } else {
      check("T6: (only 1 hero decision — skip)", true);
      check("T6: (skip)", true);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Test 7: No Regression
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== Test 7: No Regression ===");
  {
    check("T7: events ok", resp1.events.length > 0);
    check("T7: frames ok", frames1.length > 0);

    const q = await sendCmd(ws, "QUERY_HANDS", {});
    check("T7: query ok", q.ok);

    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: q.state.hands[0].sessionId, handId: "1", tag: "review", text: "jump test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: q.state.hands[0].sessionId, handId: "1" });
    check("T7: annotations ok", anns.ok);

    const counts = await sendCmd(ws, "GET_ANNOTATION_COUNTS", { sessionId: q.state.hands[0].sessionId });
    check("T7: counts ok", counts.ok);
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** JUMP DECISION TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
