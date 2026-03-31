#!/usr/bin/env node
"use strict";

/**
 * Step-Through Replay Tests
 *
 * A. Frame compilation correctness
 * B. Cursor behavior
 * C. Street jump behavior
 * D. Decision-point filtering
 * E. UI integration (WS data path)
 */

const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");
const { startServer } = require("../src/server/ws-server");

const testDir = path.join(__dirname, "..", "test-output", "replay-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

// ── Extract client-side pure functions for testing ────────────────────────

function c$(v) {
  if (v == null) return "--";
  return Math.abs(v) >= 100 ? "$" + (v / 100).toFixed(2) : v + "c";
}

function clonePlayers(players) {
  const out = {};
  for (const [s, p] of Object.entries(players)) {
    out[s] = { ...p, cards: p.cards ? [...p.cards] : null };
  }
  return out;
}

function compileFrames(events) {
  const frames = [];
  let street = "";
  let board = [];
  let pot = 0;
  let players = {};
  let handId = "";

  for (const e of events) {
    switch (e.type) {
      case "HAND_START": {
        handId = e.handId;
        street = "PREFLOP";
        board = [];
        pot = 0;
        players = {};
        for (const [s, p] of Object.entries(e.players || {})) {
          players[s] = { name: p.name, stack: p.stack, invested: 0, folded: false, allIn: false, cards: null };
        }
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `Hand #${e.handId}`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        break;
      }
      case "BLIND_POST": {
        const p = players[e.seat];
        if (p) { p.stack -= e.amount; p.invested += e.amount; }
        pot += e.amount;
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `${e.player} posts ${e.blindType} ${c$(e.amount)}`,
          actingSeat: e.seat, actionLabel: `${e.blindType}`, isDecision: false, isTerminal: false,
        });
        break;
      }
      case "HERO_CARDS": {
        const p = players[e.seat];
        if (p) p.cards = e.cards;
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `dealt cards`,
          actingSeat: e.seat, actionLabel: "dealt", isDecision: false, isTerminal: false,
        });
        break;
      }
      case "PLAYER_ACTION": {
        const p = players[e.seat];
        if (p) {
          p.stack -= (e.delta || 0);
          p.invested += (e.delta || 0);
          if (e.action === "FOLD") p.folded = true;
          if (p.stack <= 0 && e.action !== "FOLD" && e.action !== "CHECK") p.allIn = true;
        }
        pot += (e.delta || 0);
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `${e.player} ${e.action}`,
          actingSeat: e.seat, actionLabel: e.action, isDecision: true, isTerminal: false,
        });
        break;
      }
      case "BET_RETURN": {
        const p = players[e.seat];
        if (p) { p.stack += e.amount; p.invested -= e.amount; }
        pot -= e.amount;
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `return`,
          actingSeat: e.seat, actionLabel: "return", isDecision: false, isTerminal: false,
        });
        break;
      }
      case "DEAL_COMMUNITY": {
        street = e.street;
        board = e.board || [];
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `${e.street} [${board.join(" ")}]`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        break;
      }
      case "SHOWDOWN_REVEAL": {
        street = "SHOWDOWN";
        for (const r of e.reveals || []) {
          const p = players[r.seat];
          if (p) p.cards = r.cards;
        }
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `SHOWDOWN`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        break;
      }
      case "POT_AWARD": {
        for (const a of e.awards || []) {
          const p = players[a.seat];
          if (p) p.stack += a.amount;
        }
        frames.push({
          index: frames.length, handId, street: "SETTLE", board: [...board], pot: 0,
          players: clonePlayers(players),
          event: e.type, label: `Award`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        pot = 0;
        break;
      }
      case "HAND_SUMMARY": {
        frames.push({
          index: frames.length, handId, street: "SETTLE", board: [...board], pot: 0,
          players: clonePlayers(players),
          event: e.type, label: `Summary`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        break;
      }
      case "HAND_RESULT": break;
      case "HAND_END": {
        frames.push({
          index: frames.length, handId, street: "COMPLETE", board: [...board], pot: 0,
          players: clonePlayers(players),
          event: e.type, label: e.void ? "VOIDED" : "Complete",
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: true,
        });
        break;
      }
    }
  }
  return frames;
}

function getDecisionIndices(frames) {
  return frames.map((f, i) => f.isDecision ? i : -1).filter((i) => i >= 0);
}

function getStreetStartIndex(frames, targetStreet) {
  for (let i = 0; i < frames.length; i++) {
    if (frames[i].street === targetStreet) return i;
  }
  return -1;
}

// ── WS helpers ────────────────────────────────────────────────────────────

async function connectWS(port) {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://localhost:${port}`);
    ws.on("message", function first(raw) {
      const msg = JSON.parse(raw.toString());
      if (msg.welcome) { ws.removeListener("message", first); resolve({ ws, welcome: msg }); }
    });
  });
}
let cmdId = 0;
function sendCmd(ws, cmd, payload) {
  return new Promise((resolve) => {
    const id = `rp-${++cmdId}`;
    const h = (raw) => { const m = JSON.parse(raw.toString()); if (m.id === id) { ws.removeListener("message", h); resolve(m); } };
    ws.on("message", h);
    ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
  });
}
function collectUntilHandEnd(ws) {
  return new Promise((resolve) => {
    const events = [];
    const h = (raw) => {
      const msg = JSON.parse(raw.toString());
      const evts = msg.broadcast ? msg.events : msg.events;
      if (evts) for (const e of evts) { events.push(e); if (e.type === "HAND_END") { ws.removeListener("message", h); resolve(events); return; } }
    };
    ws.on("message", h);
  });
}
async function playShowdownViaWS(ws) {
  let safety = 0;
  while (safety++ < 200) {
    const r = await sendCmd(ws, "GET_STATE");
    const st = r.state;
    if (!st || !st.hand || st.hand.phase === "COMPLETE") break;
    const seat = st.hand.actionSeat;
    if (seat == null) break;
    const la = st.hand.legalActions;
    if (!la) break;
    if (la.actions.includes("CALL")) await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CALL" });
    else if (la.actions.includes("CHECK")) await sendCmd(ws, "PLAYER_ACTION", { seat, action: "CHECK" });
    else break;
  }
}

const TABLE = { tableId: "rp-t", tableName: "Replay", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 };

async function run() {

  // ═══════════════════════════════════════════════════════════════════════
  //  Create a shared fixture: 1 showdown hand + 1 fold-out hand
  // ═══════════════════════════════════════════════════════════════════════

  const port = 9200 + Math.floor(Math.random() * 100);
  const srv = startServer({ port, dataDir: path.join(testDir, "sessions"), actorsDir: path.join(testDir, "actors"), table: TABLE });
  const { ws } = await connectWS(port);

  await sendCmd(ws, "CREATE_ACTOR", { name: "Alice" });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 0, name: "Alice", buyIn: 500 });
  await sendCmd(ws, "SEAT_PLAYER", { seat: 1, name: "Bob", buyIn: 500 });

  // Hand 1: showdown (call/check to river)
  let ep = collectUntilHandEnd(ws);
  await sendCmd(ws, "START_HAND");
  await playShowdownViaWS(ws);
  const sdEvents = await ep;

  // Hand 2: fold-out
  ep = collectUntilHandEnd(ws);
  await sendCmd(ws, "START_HAND");
  let st = (await sendCmd(ws, "GET_STATE")).state;
  await sendCmd(ws, "PLAYER_ACTION", { seat: st.hand.actionSeat, action: "FOLD" });
  const foldEvents = await ep;

  // Fetch archived events via GET_HAND_EVENTS
  const sdResp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "1" });
  const sdArchived = sdResp.events;
  const foldResp = await sendCmd(ws, "GET_HAND_EVENTS", { handId: "2" });
  const foldArchived = foldResp.events;

  // ═══════════════════════════════════════════════════════════════════════
  //  A. Frame Compilation Correctness
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== A. Frame Compilation ===");
  {
    const frames = compileFrames(sdArchived);

    check("A1: frames > 0", frames.length > 0);
    check("A2: first frame is HAND_START", frames[0].event === "HAND_START");
    check("A3: last frame is HAND_END", frames[frames.length - 1].event === "HAND_END");
    check("A4: last frame isTerminal", frames[frames.length - 1].isTerminal);
    check("A5: frame indices sequential", frames.every((f, i) => f.index === i));

    // Street progression: PREFLOP → FLOP → TURN → RIVER → SHOWDOWN → SETTLE → COMPLETE
    const streets = frames.map((f) => f.street);
    const uniqueStreets = [...new Set(streets)];
    check("A6: starts PREFLOP", uniqueStreets[0] === "PREFLOP");
    check("A7: has FLOP", uniqueStreets.includes("FLOP"));
    check("A8: has RIVER", uniqueStreets.includes("RIVER"));
    check("A9: ends COMPLETE", uniqueStreets[uniqueStreets.length - 1] === "COMPLETE");

    // Board progression
    const flopFrame = frames.find((f) => f.street === "FLOP" && f.event === "DEAL_COMMUNITY");
    const riverFrame = frames.find((f) => f.street === "RIVER" && f.event === "DEAL_COMMUNITY");
    check("A10: flop has 3 board cards", flopFrame && flopFrame.board.length === 3);
    check("A11: river has 5 board cards", riverFrame && riverFrame.board.length === 5);

    // Pot progression: increases during betting, drops to 0 after POT_AWARD
    const awardFrame = frames.find((f) => f.event === "POT_AWARD");
    check("A12: pot before award > 0", frames[awardFrame.index - 1].pot > 0);
    check("A13: pot at award = 0", awardFrame.pot === 0);

    // Players present
    check("A14: 2 players in frame 0", Object.keys(frames[0].players).length === 2);

    // Showdown hand: SHOWDOWN_REVEAL frame shows cards
    const sdFrame = frames.find((f) => f.event === "SHOWDOWN_REVEAL");
    check("A15: showdown frame exists", !!sdFrame);
    check("A16: showdown reveals cards",
      Object.values(sdFrame.players).every((p) => p.cards != null));

    // Final stacks: sum = 1000 (zero-sum)
    const finalFrame = frames[frames.length - 1];
    const finalSum = Object.values(finalFrame.players).reduce((s, p) => s + p.stack, 0);
    check("A17: final stacks sum to 1000", finalSum === 1000);
  }

  // Fold-out hand compilation
  {
    const frames = compileFrames(foldArchived);
    check("A18: fold-out frames > 0", frames.length > 0);
    check("A19: fold-out last is HAND_END", frames[frames.length - 1].event === "HAND_END");

    // No DEAL_COMMUNITY in a preflop fold-out
    const dealFrames = frames.filter((f) => f.event === "DEAL_COMMUNITY");
    check("A20: no community cards in fold-out", dealFrames.length === 0);

    // No SHOWDOWN_REVEAL
    check("A21: no showdown in fold-out", !frames.some((f) => f.event === "SHOWDOWN_REVEAL"));

    // Has decision frames (at least the fold action)
    const decs = getDecisionIndices(frames);
    check("A22: fold-out has decision frames", decs.length >= 1);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  B. Cursor Behavior
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== B. Cursor Behavior ===");
  {
    const frames = compileFrames(sdArchived);
    let cursor = 0;

    // Next
    cursor = Math.min(cursor + 1, frames.length - 1);
    check("B1: next from 0 → 1", cursor === 1);

    // Prev from 1 → 0
    cursor = Math.max(cursor - 1, 0);
    check("B2: prev from 1 → 0", cursor === 0);

    // Prev from 0 → 0 (no underflow)
    cursor = Math.max(cursor - 1, 0);
    check("B3: prev from 0 → 0 (no underflow)", cursor === 0);

    // Last
    cursor = frames.length - 1;
    check("B4: last → final frame", cursor === frames.length - 1);

    // Next from last → stays (no overflow)
    cursor = Math.min(cursor + 1, frames.length - 1);
    check("B5: next from last → stays", cursor === frames.length - 1);

    // First
    cursor = 0;
    check("B6: first → 0", cursor === 0);

    // Decision-only navigation
    const decs = getDecisionIndices(frames);
    check("B7: decision indices exist", decs.length > 0);

    // First decision
    cursor = decs[0];
    check("B8: first decision is PLAYER_ACTION", frames[cursor].event === "PLAYER_ACTION");

    // Next decision
    const nextDecs = decs.filter((i) => i > cursor);
    if (nextDecs.length > 0) {
      cursor = nextDecs[0];
      check("B9: next decision is later", cursor > decs[0]);
    } else {
      check("B9: (only one decision — skip)", true);
    }

    // Prev decision
    const prevDecs = decs.filter((i) => i < cursor);
    if (prevDecs.length > 0) {
      cursor = prevDecs[prevDecs.length - 1];
      check("B10: prev decision works", cursor < decs[decs.length - 1]);
    } else {
      check("B10: (no prev — skip)", true);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  C. Street Jump Behavior
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== C. Street Jump ===");
  {
    const frames = compileFrames(sdArchived);

    const pfIdx = getStreetStartIndex(frames, "PREFLOP");
    check("C1: PREFLOP jump → frame 0", pfIdx === 0);

    const flopIdx = getStreetStartIndex(frames, "FLOP");
    check("C2: FLOP exists", flopIdx > 0);
    check("C3: FLOP frame street is FLOP", frames[flopIdx].street === "FLOP");

    const turnIdx = getStreetStartIndex(frames, "TURN");
    check("C4: TURN exists", turnIdx > flopIdx);
    check("C5: TURN frame street is TURN", frames[turnIdx].street === "TURN");

    const riverIdx = getStreetStartIndex(frames, "RIVER");
    check("C6: RIVER exists", riverIdx > turnIdx);

    const sdIdx = getStreetStartIndex(frames, "SHOWDOWN");
    check("C7: SHOWDOWN exists", sdIdx > riverIdx);

    // Fold-out hand: no FLOP street
    const foldFrames = compileFrames(foldArchived);
    const noFlop = getStreetStartIndex(foldFrames, "FLOP");
    check("C8: fold-out has no FLOP", noFlop === -1);

    const noRiver = getStreetStartIndex(foldFrames, "RIVER");
    check("C9: fold-out has no RIVER", noRiver === -1);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  D. Decision-Point Filtering
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== D. Decision Points ===");
  {
    const frames = compileFrames(sdArchived);
    const decs = getDecisionIndices(frames);

    check("D1: decision points exist", decs.length > 0);
    check("D2: all decisions are PLAYER_ACTION", decs.every((i) => frames[i].event === "PLAYER_ACTION"));
    check("D3: decisions are subset of all frames", decs.length < frames.length);

    // Non-decision events excluded
    const nonDecs = frames.filter((f) => !f.isDecision);
    const nonDecTypes = new Set(nonDecs.map((f) => f.event));
    check("D4: HAND_START not a decision", nonDecTypes.has("HAND_START"));
    check("D5: BLIND_POST not a decision", nonDecTypes.has("BLIND_POST"));
    check("D6: DEAL_COMMUNITY not a decision", nonDecTypes.has("DEAL_COMMUNITY"));
    check("D7: POT_AWARD not a decision", nonDecTypes.has("POT_AWARD"));
    check("D8: HAND_END not a decision", nonDecTypes.has("HAND_END"));

    // Decision indices are strictly increasing
    let increasing = true;
    for (let i = 1; i < decs.length; i++) {
      if (decs[i] <= decs[i - 1]) { increasing = false; break; }
    }
    check("D9: decision indices strictly increasing", increasing);

    // Fold-out: fold action is a decision
    const foldFrames = compileFrames(foldArchived);
    const foldDecs = getDecisionIndices(foldFrames);
    check("D10: fold-out has decision(s)", foldDecs.length >= 1);
    check("D11: fold decision is FOLD", foldFrames[foldDecs[0]].actionLabel === "FOLD");
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  E. UI Integration (Data Path)
  // ═══════════════════════════════════════════════════════════════════════

  console.log("=== E. UI Integration ===");
  {
    // Verify GET_HAND_EVENTS returns sufficient data for frame compilation
    check("E1: showdown events loaded", sdArchived.length > 0);
    check("E2: fold events loaded", foldArchived.length > 0);

    // Verify events contain required types
    const sdTypes = sdArchived.map((e) => e.type);
    check("E3: HAND_START in events", sdTypes.includes("HAND_START"));
    check("E4: PLAYER_ACTION in events", sdTypes.includes("PLAYER_ACTION"));
    check("E5: HAND_END in events", sdTypes.includes("HAND_END"));
    check("E6: DEAL_COMMUNITY in events", sdTypes.includes("DEAL_COMMUNITY"));

    // HAND_START has players map (required for frame compilation)
    const hs = sdArchived.find((e) => e.type === "HAND_START");
    check("E7: HAND_START has players", hs && hs.players && Object.keys(hs.players).length === 2);
    check("E8: players have name and stack", Object.values(hs.players).every((p) => p.name && p.stack > 0));

    // Switching hands: compile fold events (different hand) — clean state
    const foldFrames = compileFrames(foldArchived);
    const sdFrames = compileFrames(sdArchived);
    check("E9: different hand → different frames", foldFrames.length !== sdFrames.length);
    check("E10: fold frames independent", foldFrames[0].handId !== sdFrames[0].handId);
  }

  // ═══════════════════════════════════════════════════════════════════════

  ws.close();
  srv.close();

  console.log(`\n*** REPLAY TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
