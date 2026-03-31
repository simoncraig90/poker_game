#!/usr/bin/env node
"use strict";

/**
 * Actor-Aware Study Visibility Mode Tests
 *
 * A. Preflop visibility
 * B. Future-board hiding across streets
 * C. Opponent-card hiding before reveal
 * D. Reveal behavior after showdown event
 * E. Default actor / actor selection
 * F. No regression to replay navigation
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "vis-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

// ── Duplicate pure functions from client for testing ──────────────────────

function c$(v) { return v == null ? "--" : Math.abs(v) >= 100 ? "$" + (v / 100).toFixed(2) : v + "c"; }
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
        frames.push({ index: frames.length, handId, street, board: [...board], pot, players: clonePlayers(players), event: e.type, label: `${e.player} dealt ${e.cards.join(" ")}`, actingSeat: e.seat, actionLabel: "dealt", isDecision: false, isTerminal: false }); break; }
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

function getShowdownRevealIndex(frames) {
  for (let i = 0; i < frames.length; i++) if (frames[i].event === "SHOWDOWN_REVEAL") return i;
  return -1;
}

function applyStudyVisibility(frame, heroSeat, showdownRevealIdx) {
  const out = { ...frame, players: {}, board: [...frame.board], label: frame.label };
  const pastShowdown = showdownRevealIdx >= 0 && frame.index >= showdownRevealIdx;
  for (const [s, p] of Object.entries(frame.players)) {
    const seatNum = parseInt(s);
    if (seatNum === heroSeat) out.players[s] = { ...p, cards: p.cards ? [...p.cards] : null };
    else if (pastShowdown) out.players[s] = { ...p, cards: p.cards ? [...p.cards] : null };
    else out.players[s] = { ...p, cards: null };
  }
  if (frame.event === "HERO_CARDS" && frame.actingSeat !== heroSeat) {
    out.label = `${frame.players[String(frame.actingSeat)]?.name || "Opponent"} dealt cards`;
  }
  return out;
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
    const id = `sv-${++cmdId}`;
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

const TABLE = { tableId: "sv-t", tableName: "Vis", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  // ═══════════════════════════════════════════════════════════════════════
  //  Setup: play a showdown hand and a fold-out hand, fetch their events
  // ═══════════════════════════════════════════════════════════════════════

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws } = await connectWS(port);

  const cr = await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
  const aliceId = cr.state.actor.actorId;
  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500, actorId: aliceId });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

  // Hand 1: showdown
  let ep = collectUntilHandEnd(ws);
  await sendCmd(ws, "START_HAND");
  await playShowdownViaWS(ws);
  await ep;

  // Hand 2: fold-out
  ep = collectUntilHandEnd(ws);
  await sendCmd(ws, "START_HAND");
  let st = (await sendCmd(ws, "GET_STATE")).state;
  await sendCmd(ws, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });
  await ep;

  const sdResp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
  const sdEvents = sdResp.events;
  const foldResp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "2" });
  const foldEvents = foldResp.events;

  const sdFrames = compileFrames(sdEvents);
  const foldFrames = compileFrames(foldEvents);
  const sdRevealIdx = getShowdownRevealIndex(sdFrames);

  // Identify seats
  const heroSeat = 0; // Alice
  const oppSeat = 1;  // Bob

  // ═══════════════════════════════════════════════════════════════════════
  //  A. Preflop Visibility
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== A. Preflop Visibility ===");
  {
    // Find HERO_CARDS frame for hero (Alice, seat 0)
    const heroCardsFrame = sdFrames.find((f) => f.event === "HERO_CARDS" && f.actingSeat === heroSeat);
    const oppCardsFrame = sdFrames.find((f) => f.event === "HERO_CARDS" && f.actingSeat === oppSeat);
    check("A1: hero HERO_CARDS frame exists", !!heroCardsFrame);
    check("A2: opponent HERO_CARDS frame exists", !!oppCardsFrame);

    // Full info: both have cards
    check("A3: full info hero has cards", heroCardsFrame.players[String(heroSeat)].cards !== null);
    check("A4: full info opponent has cards", oppCardsFrame.players[String(oppSeat)].cards !== null);

    // Study mode at hero's HERO_CARDS frame: hero sees own cards, opponent hidden
    const visHero = applyStudyVisibility(heroCardsFrame, heroSeat, sdRevealIdx);
    check("A5: study hero sees own cards", visHero.players[String(heroSeat)].cards !== null);
    check("A6: study hero doesn't see opp cards", visHero.players[String(oppSeat)].cards === null);

    // Study mode at opponent's HERO_CARDS frame: still can't see opponent
    const visOpp = applyStudyVisibility(oppCardsFrame, heroSeat, sdRevealIdx);
    check("A7: study at opp deal: opp cards hidden", visOpp.players[String(oppSeat)].cards === null);
    check("A8: study at opp deal: hero cards visible", visOpp.players[String(heroSeat)].cards !== null);

    // Label redaction for opponent's HERO_CARDS
    check("A9: opp HERO_CARDS label redacted", visOpp.label.includes("dealt cards") && !visOpp.label.includes(oppCardsFrame.players[String(oppSeat)].cards?.[0] || "NOMATCH"));
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  B. Board Visibility Across Streets
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== B. Board Across Streets ===");
  {
    // Preflop frames: no board
    const pfFrame = sdFrames.find((f) => f.street === "PREFLOP" && f.event === "PLAYER_ACTION");
    const visPf = applyStudyVisibility(pfFrame, heroSeat, sdRevealIdx);
    check("B1: preflop board empty", visPf.board.length === 0);

    // Flop: 3 cards
    const flopFrame = sdFrames.find((f) => f.street === "FLOP" && f.event === "DEAL_COMMUNITY");
    const visFlop = applyStudyVisibility(flopFrame, heroSeat, sdRevealIdx);
    check("B2: flop board has 3 cards", visFlop.board.length === 3);

    // Turn: 4 cards
    const turnFrame = sdFrames.find((f) => f.street === "TURN" && f.event === "DEAL_COMMUNITY");
    const visTurn = applyStudyVisibility(turnFrame, heroSeat, sdRevealIdx);
    check("B3: turn board has 4 cards", visTurn.board.length === 4);

    // River: 5 cards
    const riverFrame = sdFrames.find((f) => f.street === "RIVER" && f.event === "DEAL_COMMUNITY");
    const visRiver = applyStudyVisibility(riverFrame, heroSeat, sdRevealIdx);
    check("B4: river board has 5 cards", visRiver.board.length === 5);

    // Board doesn't leak into earlier frames
    const preflopLast = sdFrames.filter((f) => f.street === "PREFLOP").pop();
    const visPfLast = applyStudyVisibility(preflopLast, heroSeat, sdRevealIdx);
    check("B5: last preflop frame has no board", visPfLast.board.length === 0);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  C. Opponent Card Hiding Before Reveal
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== C. Opponent Hiding ===");
  {
    // Check every frame before showdown: opponent cards must be null
    const framesBeforeSD = sdFrames.filter((f) => f.index < sdRevealIdx);
    let allHidden = true;
    for (const f of framesBeforeSD) {
      const vis = applyStudyVisibility(f, heroSeat, sdRevealIdx);
      if (vis.players[String(oppSeat)].cards !== null) { allHidden = false; break; }
    }
    check("C1: all pre-showdown frames hide opponent cards", allHidden);

    // Hero cards visible from HERO_CARDS onward
    const heroStart = sdFrames.findIndex((f) => f.event === "HERO_CARDS" && f.actingSeat === heroSeat);
    const framesAfterHeroDeal = sdFrames.filter((f) => f.index >= heroStart && f.index < sdRevealIdx);
    let heroAlwaysVisible = true;
    for (const f of framesAfterHeroDeal) {
      const vis = applyStudyVisibility(f, heroSeat, sdRevealIdx);
      if (vis.players[String(heroSeat)].cards === null) { heroAlwaysVisible = false; break; }
    }
    check("C2: hero cards visible from deal onward (before showdown)", heroAlwaysVisible);

    // Before hero is dealt: hero cards are null (HAND_START frame)
    const handStartVis = applyStudyVisibility(sdFrames[0], heroSeat, sdRevealIdx);
    check("C3: hero cards null at HAND_START", handStartVis.players[String(heroSeat)].cards === null);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  D. Reveal at Showdown
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== D. Showdown Reveal ===");
  {
    check("D1: showdown reveal index found", sdRevealIdx >= 0);

    // At showdown frame: opponent cards become visible
    const sdFrame = sdFrames[sdRevealIdx];
    const visSD = applyStudyVisibility(sdFrame, heroSeat, sdRevealIdx);
    check("D2: opponent cards visible at showdown", visSD.players[String(oppSeat)].cards !== null);
    check("D3: hero cards visible at showdown", visSD.players[String(heroSeat)].cards !== null);

    // After showdown (POT_AWARD, HAND_END): cards remain visible
    const postSD = sdFrames.filter((f) => f.index > sdRevealIdx);
    let allVisibleAfter = true;
    for (const f of postSD) {
      const vis = applyStudyVisibility(f, heroSeat, sdRevealIdx);
      if (vis.players[String(oppSeat)].cards === null && f.players[String(oppSeat)].cards !== null) {
        allVisibleAfter = false; break;
      }
    }
    check("D4: opponent cards stay visible after showdown", allVisibleAfter);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  E. Fold-Out Hand (No Showdown)
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== E. Fold-Out Hand ===");
  {
    const foldRevealIdx = getShowdownRevealIndex(foldFrames);
    check("E1: no showdown in fold-out", foldRevealIdx === -1);

    // Opponent cards never visible in study mode (no showdown to reveal them)
    let oppAlwaysHidden = true;
    for (const f of foldFrames) {
      const vis = applyStudyVisibility(f, heroSeat, foldRevealIdx);
      if (vis.players[String(oppSeat)] && vis.players[String(oppSeat)].cards !== null) {
        oppAlwaysHidden = false; break;
      }
    }
    check("E2: opponent cards never visible in fold-out study mode", oppAlwaysHidden);

    // Hero cards visible after deal
    const heroStart = foldFrames.findIndex((f) => f.event === "HERO_CARDS" && f.actingSeat === heroSeat);
    if (heroStart >= 0) {
      const vis = applyStudyVisibility(foldFrames[heroStart], heroSeat, foldRevealIdx);
      check("E3: hero cards visible after deal in fold-out", vis.players[String(heroSeat)].cards !== null);
    } else {
      check("E3: (no hero deal in fold-out — skip)", true);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  F. Hero Seat Selection
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== F. Hero Selection ===");
  {
    // Switching hero to Bob (seat 1): Bob's cards visible, Alice's hidden
    const heroCardsFrame = sdFrames.find((f) => f.event === "HERO_CARDS" && f.actingSeat === oppSeat);
    const visBobHero = applyStudyVisibility(heroCardsFrame, oppSeat, sdRevealIdx);
    check("F1: Bob-as-hero sees own cards", visBobHero.players[String(oppSeat)].cards !== null);
    check("F2: Bob-as-hero hides Alice", visBobHero.players[String(heroSeat)].cards === null);

    // At showdown: both visible regardless of hero choice
    const visSD = applyStudyVisibility(sdFrames[sdRevealIdx], oppSeat, sdRevealIdx);
    check("F3: showdown reveals all for Bob-as-hero", visSD.players[String(heroSeat)].cards !== null && visSD.players[String(oppSeat)].cards !== null);

    // Default hero inference: actorId match
    const hs = sdEvents.find((e) => e.type === "HAND_START");
    let inferredSeat = null;
    if (hs && hs.players) {
      for (const [s, p] of Object.entries(hs.players)) {
        if (p.actorId === aliceId) { inferredSeat = parseInt(s); break; }
      }
    }
    check("F4: hero inferred from actorId", inferredSeat === 0);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  G. No Regression — Replay Navigation + Annotations
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== G. No Regression ===");
  {
    // Full-info frames unchanged
    check("G1: full-info showdown frame has both cards",
      sdFrames[sdRevealIdx].players[String(heroSeat)].cards !== null &&
      sdFrames[sdRevealIdx].players[String(oppSeat)].cards !== null);

    // Annotations still work
    const sid = (await sendCmd(ws, "GET_STATE")).state.tableId ? null : null;
    // Use the session from the welcome
    const qr = await sendCmd(ws, "QUERY_HANDS", {});
    const hand = qr.state.hands[0];
    await sendCmd(ws, "ADD_ANNOTATION", { sessionId: hand.sessionId, handId: hand.handId, text: "vis test" });
    const anns = await sendCmd(ws, "GET_ANNOTATIONS", { sessionId: hand.sessionId, handId: hand.handId });
    check("G2: annotations still work", anns.ok && anns.state.annotations.length >= 1);

    // GET_HAND_EVENTS still returns data
    const hevt = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
    check("G3: replay data path ok", hevt.ok && hevt.events.length > 0);

    // Query still works
    check("G4: query still works", qr.ok && qr.state.hands.length > 0);
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** STUDY VISIBILITY TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
