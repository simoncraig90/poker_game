#!/usr/bin/env node
"use strict";

/**
 * Internal live test: stratified bot plays seat 0 against profile bots on seats 1-5.
 *
 * Connects to the WS server, auto-plays using the stratified strategy
 * (preflop chart + flop CFR + turn/river equity rules), tracks P&L.
 *
 * Usage:
 *   # Terminal 1: start bot opponents
 *   node scripts/bot-players.js
 *
 *   # Terminal 2: run stratified bot as hero
 *   node scripts/test-stratified-live.js --hands 1000
 *   node scripts/test-stratified-live.js --hands 500 --host 192.168.0.200:9100
 */

const WebSocket = require("ws");
const fs = require("fs");
const path = require("path");
const { getLegalActions } = require("../src/engine/betting");
const { evaluateHandStrength, strengthToBucket } = require("./cfr/abstraction");

// ── Config ──────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
function getArg(name, def) {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i+1] ? args[i+1] : def;
}

const TARGET_HANDS = parseInt(getArg("hands", "1000"));
const SERVER_HOST = getArg("host", "localhost:9100");
const TABLE_ID = getArg("table", "strat-test");
const API_KEY = process.env.POKER_API_KEY || "";
const keyParam = API_KEY ? `&key=${encodeURIComponent(API_KEY)}` : "";
const HERO_SEAT = 0;
const HERO_NAME = "StratBot_v1";
const BB = 10;

// ── Load flop CFR ───────────────────────────────────────────────────────

const flopPath = path.resolve("vision/models/cfr_strategy_flop.json");
let flopCFR = null;
if (fs.existsSync(flopPath)) {
  flopCFR = JSON.parse(fs.readFileSync(flopPath, "utf8"));
  console.log(`[StratBot] Flop CFR loaded: ${Object.keys(flopCFR).length} info sets`);
}

// ── Preflop ranges ──────────────────────────────────────────────────────

const OPEN_RANGES = {
  EP: new Set(["AA","KK","QQ","JJ","TT","99","AKs","AQs","AJs","ATs","KQs","KJs","AKo","AQo"]),
  MP: new Set(["AA","KK","QQ","JJ","TT","99","88","77","AKs","AQs","AJs","ATs","A9s","A8s","KQs","KJs","KTs","QJs","QTs","JTs","AKo","AQo","AJo","KQo"]),
  CO: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","QJs","QTs","Q9s","JTs","J9s","T9s","98s","87s","76s","AKo","AQo","AJo","ATo","KQo","KJo","QJo"]),
  BTN: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","44","33","22","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","K8s","K7s","K6s","K5s","QJs","QTs","Q9s","Q8s","JTs","J9s","J8s","T9s","T8s","98s","97s","87s","86s","76s","75s","65s","54s","AKo","AQo","AJo","ATo","A9o","A8o","A7o","A6o","A5o","KQo","KJo","KTo","QJo","QTo","JTo"]),
  SB: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","44","33","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","K8s","QJs","QTs","Q9s","Q8s","JTs","J9s","J8s","T9s","T8s","98s","97s","87s","86s","76s","75s","AKo","AQo","AJo","ATo","A9o","KQo","KJo","KTo"]),
  BB: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","44","33","22","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","K8s","QJs","QTs","Q9s","JTs","J9s","T9s","T8s","98s","97s","87s","86s","76s","75s","65s","54s","AKo","AQo","AJo","ATo","A9o","KQo","KJo","KTo","QJo","QTo","JTo"]),
};
const PREMIUMS = new Set(["AA","KK","QQ","AKs","AKo"]);
const CALL_3BET = new Set(["JJ","TT","99","88","77","AQs","AJs","ATs","KQs","KJs","QJs","JTs","AQo"]);

function handKey(c1, c2) {
  const RC = {2:"2",3:"3",4:"4",5:"5",6:"6",7:"7",8:"8",9:"9",10:"T",11:"J",12:"Q",13:"K",14:"A"};
  let r1=c1.rank,r2=c2.rank,s1=c1.suit,s2=c2.suit;
  if(r1<r2){[r1,r2,s1,s2]=[r2,r1,s2,s1];}
  if(r1===r2) return `${RC[r1]}${RC[r2]}`;
  return s1===s2?`${RC[r1]}${RC[r2]}s`:`${RC[r1]}${RC[r2]}o`;
}

// ── Stratified decision ─────────────────────────────────────────────────

function stratDecide(seat, state) {
  const s = state.seats[seat];
  if (!s || s.status !== "OCCUPIED" || !s.inHand || s.folded || s.allIn) return null;

  const hand = state.hand;
  if (!hand || hand.actionSeat !== seat) return null;

  const legal = hand.legalActions;
  if (!legal || !legal.actions || legal.actions.length === 0) return null;
  if (legal.actions.length === 1) return { action: legal.actions[0] };

  const actions = legal.actions;
  const cards = s.holeCards || [];
  const board = hand.board || [];
  const phase = hand.phase;
  const pot = hand.pot || 0;
  const callAmount = legal.callAmount || 0;
  const minBet = legal.minBet || 0;
  const minRaise = legal.minRaise || 0;
  const maxRaise = legal.maxRaise || 0;
  const stack = s.stack || 0;
  const strength = evaluateHandStrength(cards, board, phase);

  // Position
  const dealer = hand.dealer ?? 0;
  const numSeats = Object.keys(state.seats).filter(k => state.seats[k]).length;
  const relPos = ((seat - dealer) % numSeats + numSeats) % numSeats;
  const posName = relPos === 0 ? "BTN" : relPos === 1 ? "SB" : relPos === 2 ? "BB" :
                  relPos === numSeats - 1 ? "CO" : relPos === numSeats - 2 ? "MP" : "EP";
  const isIP = posName === "BTN" || posName === "CO";

  // ── PREFLOP ──
  if (phase === "PREFLOP") {
    if (cards.length < 2) return { action: "FOLD" };
    const key = handKey(cards[0], cards[1]);

    if (posName === "BB" && !actions.includes("RAISE") && actions.includes("CHECK")) {
      return { action: "CHECK" };
    }

    const facingRaise = callAmount > BB;
    if (facingRaise) {
      if (PREMIUMS.has(key) && actions.includes("RAISE"))
        return { action: "RAISE", amount: Math.min(callAmount * 3, maxRaise) };
      if ((PREMIUMS.has(key) || CALL_3BET.has(key)) && actions.includes("CALL"))
        return { action: "CALL" };
      if (posName === "BB" && (OPEN_RANGES.BB).has(key) && actions.includes("CALL"))
        return { action: "CALL" };
      return { action: "FOLD" };
    }

    const range = OPEN_RANGES[posName] || OPEN_RANGES.CO;
    if (range.has(key) && actions.includes("RAISE")) {
      return { action: "RAISE", amount: Math.max(minRaise, Math.min(Math.floor(BB * 2.5 + pot), maxRaise)) };
    }
    if (posName === "BB" && actions.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  // ── FLOP: CFR ──
  if (phase === "FLOP" && flopCFR && board.length >= 3) {
    const bucket = Math.min(49, Math.floor(strength * 50));
    const sb = stack/BB < 30 ? 0 : stack/BB < 80 ? 1 : 2;
    const pos = isIP ? "IP" : "OOP";
    const potBB = pot / BB;
    const potClass = potBB >= 15 ? "3BP" : potBB <= 3 ? "LP" : "SRP";

    // Build opponent action history this street
    let hist = "";
    const streetActions = (hand.actions || []).filter(a => a.street === "FLOP" && a.seat !== seat && a.type !== "FOLD");
    for (const a of streetActions) {
      if (a.type === "CHECK") hist += "k";
      else if (a.type === "CALL") hist += "c";
      else {
        const r = pot > 0 ? (a.amount||0)/pot : 0.5;
        hist += r >= 0.85 ? "bp" : r >= 0.5 ? "bs" : "bt";
      }
    }

    // CFR lookup with fuzzy
    let probs = null;
    for (let d = 0; d <= 5; d++) {
      for (const delta of d === 0 ? [0] : [d, -d]) {
        const b = bucket + delta;
        if (b < 0 || b >= 50) continue;
        const k = `FLOP:${b}:s${sb}:${pos}:${potClass}:${hist}`;
        if (flopCFR[k]) { probs = flopCFR[k]; break; }
      }
      if (probs) break;
    }

    if (probs) {
      // Sample from mixed strategy
      const fold = probs.FOLD||0, check = probs.CHECK||0, call = probs.CALL||0;
      const bet33 = probs.BET_33||0, bet66 = probs.BET_66||0, betP = probs.BET_POT||0;
      const betA = probs.BET_ALLIN||0, rH = probs.RAISE_HALF||0, rP = probs.RAISE_POT||0, rA = probs.RAISE_ALLIN||0;
      const total = fold+check+call+bet33+bet66+betP+betA+rH+rP+rA;

      if (total > 0) {
        const r = Math.random() * total;
        let cum = 0;

        cum += fold;
        if (r < cum && actions.includes("FOLD")) return { action: "FOLD" };
        cum += check;
        if (r < cum && actions.includes("CHECK")) return { action: "CHECK" };
        cum += call;
        if (r < cum && actions.includes("CALL")) return { action: "CALL" };
        cum += bet33;
        if (r < cum) {
          if (actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, Math.floor(pot*0.33)) };
          if (actions.includes("RAISE")) return { action: "RAISE", amount: minRaise };
        }
        cum += bet66;
        if (r < cum) {
          if (actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, Math.floor(pot*0.66)) };
          if (actions.includes("RAISE")) return { action: "RAISE", amount: Math.max(minRaise, Math.floor(pot*0.66)) };
        }
        cum += betP + rP;
        if (r < cum) {
          if (actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, pot) };
          if (actions.includes("RAISE")) return { action: "RAISE", amount: Math.max(minRaise, pot) };
        }
        // All-in
        if (actions.includes("RAISE")) return { action: "RAISE", amount: maxRaise };
        if (actions.includes("BET")) return { action: "BET", amount: stack };
      }
    }
  }

  // ── TURN/RIVER: equity rules ──
  if (strength > 0.70) {
    if (actions.includes("RAISE")) return { action: "RAISE", amount: Math.max(minRaise, Math.min(Math.floor(pot*0.75), maxRaise)) };
    if (actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, Math.min(Math.floor(pot*0.66), stack)) };
    if (actions.includes("CALL")) return { action: "CALL" };
    return { action: "CHECK" };
  }
  if (strength > 0.35) {
    if (actions.includes("CHECK")) return { action: "CHECK" };
    if (actions.includes("CALL") && callAmount < pot * 0.5) return { action: "CALL" };
    return { action: "FOLD" };
  }
  if (actions.includes("CHECK")) return { action: "CHECK" };
  if (Math.random() < 0.08 && actions.includes("BET")) return { action: "BET", amount: minBet };
  return { action: "FOLD" };
}

// ── WebSocket connection ────────────────────────────────────────────────

const WS_URL = `ws://${SERVER_HOST}?table=${TABLE_ID}${keyParam}`;
console.log(`[StratBot] Connecting to ${WS_URL}`);
console.log(`[StratBot] Playing ${TARGET_HANDS} hands as seat ${HERO_SEAT}\n`);

const ws = new WebSocket(WS_URL);
let msgId = 0;

function send(cmd, payload) {
  ws.send(JSON.stringify({ id: `strat-${++msgId}`, cmd, payload: payload || {} }));
}

let handsPlayed = 0;
let startingStack = null;
let currentHandId = null;
let lastStack = 0;
const handProfits = [];
let seated = false;

ws.on("open", () => {
  console.log("[StratBot] Connected. Sitting down...");
  send("SEAT_PLAYER", { seat: HERO_SEAT, name: HERO_NAME, buyIn: 1000 });
});

ws.on("message", (data) => {
  let msg;
  try { msg = JSON.parse(data); } catch { return; }

  if (msg.error) {
    // Suppress turn-order errors (expected during async play)
    if (!msg.error.includes("turn")) console.log(`[StratBot] Error: ${msg.error}`);
    return;
  }

  if (msg.welcome) {
    // Check if we're already seated from a previous session
    const welcomeSeats = msg.state?.seats || msg.state?.table?.seats || {};
    const ourSeat = welcomeSeats[HERO_SEAT];
    if (ourSeat && ourSeat.status === "OCCUPIED") {
      seated = true;
      lastStack = ourSeat.stack || 1000;
      startingStack = lastStack;
      console.log(`[StratBot] Already seated (recovered session). Stack: ${lastStack}. Playing...`);
      setTimeout(() => send("START_HAND", {}), 1000);
    } else {
      console.log("[StratBot] Sitting down...");
      send("SEAT_PLAYER", { seat: HERO_SEAT, name: HERO_NAME, buyIn: 1000 });
    }
    return;
  }

  if (msg.ok && !seated) {
    seated = true;
    console.log("[StratBot] Seated successfully. Requesting hand start...");
    setTimeout(() => send("START_HAND", {}), 1000);
    return;
  }

  // Handle command responses (ok/error)
  if (msg.ok !== undefined && msg.state) {
    // State update from our command
  }

  // Handle broadcasts (game events from other players)
  const state = msg.state || (msg.broadcast ? null : null);
  if (!state && !msg.broadcast) return;

  // For broadcasts, request fresh state
  if (msg.broadcast) {
    send("GET_STATE", {});
    return;
  }

  if (!state || !state.hand || !state.seats) return;

  // Only act when it's our turn
  const actionSeat = state.hand ? state.hand.actionSeat : (state.table && state.table.hand ? state.table.hand.actionSeat : null);
  if (actionSeat !== HERO_SEAT) return;

  // Unwrap table state if nested
  const gameState = state.table ? { hand: state.table.hand, seats: state.table.seats } : state;

  const seats = gameState.seats || state.seats || {};
  const hand = gameState.hand || state.hand || {};
  const heroSeat = seats[HERO_SEAT];
  if (!heroSeat) return;

  // Track hands
  const handId = hand.handId || hand.id;
  if (handId !== currentHandId && handId != null) {
    if (currentHandId !== null && lastStack > 0) {
      const profit = heroSeat.stack - lastStack;
      handProfits.push(profit);
      handsPlayed++;

      if (handsPlayed % 100 === 0) {
        const totalProfit = handProfits.reduce((a, b) => a + b, 0);
        const bb100 = (totalProfit / BB) / (handsPlayed / 100);
        console.log(`  [${handsPlayed}h] Stack: ${heroSeat.stack} | Profit: ${totalProfit >= 0 ? "+" : ""}${totalProfit} | ${bb100.toFixed(1)} bb/100`);
      }

      if (handsPlayed >= TARGET_HANDS) {
        printResults();
        ws.close();
        return;
      }
    }
    currentHandId = handId;
    lastStack = heroSeat.stack;
    if (startingStack === null) startingStack = heroSeat.stack;

    // Request next hand after a brief delay
    setTimeout(() => send("START_HAND", {}), 500);
  }

  // Rebuy if busted
  if (heroSeat.stack < BB * 2 && (!heroSeat.inHand || heroSeat.folded)) {
    send("LEAVE_TABLE", { seat: HERO_SEAT });
    setTimeout(() => {
      send("SEAT_PLAYER", { seat: HERO_SEAT, name: HERO_NAME, buyIn: 1000 });
      lastStack = 1000;
    }, 200);
    return;
  }

  // Make decision — pass state in the format stratDecide expects
  const decision = stratDecide(HERO_SEAT, { hand, seats, table: { seats, bb: BB } });
  if (decision) {
    send("PLAYER_ACTION", {
      seat: HERO_SEAT,
      action: decision.action,
      amount: decision.amount,
    });
  }
});

ws.on("close", () => {
  console.log("\n[StratBot] Disconnected.");
  if (handsPlayed > 0) printResults();
  process.exit(0);
});

ws.on("error", (err) => {
  console.error(`[StratBot] WS Error: ${err.message}`);
});

function printResults() {
  const totalProfit = handProfits.reduce((a, b) => a + b, 0);
  const bb100 = handsPlayed > 0 ? (totalProfit / BB) / (handsPlayed / 100) : 0;

  // Standard deviation
  const mean = totalProfit / Math.max(handsPlayed, 1);
  const variance = handProfits.reduce((sum, p) => sum + (p - mean) ** 2, 0) / Math.max(handsPlayed - 1, 1);
  const stdev = Math.sqrt(variance);
  const se = stdev / Math.sqrt(Math.max(handsPlayed, 1));
  const ci95 = se * 1.96 / BB * 100; // in bb/100

  console.log("\n" + "=".repeat(60));
  console.log("  STRATIFIED BOT — Internal Test Results");
  console.log("=".repeat(60));
  console.log(`  Hands played:   ${handsPlayed}`);
  console.log(`  Total profit:   ${totalProfit >= 0 ? "+" : ""}${totalProfit} chips (${(totalProfit/BB).toFixed(1)} BB)`);
  console.log(`  Win rate:       ${bb100.toFixed(1)} bb/100 ±${ci95.toFixed(1)}`);
  console.log(`  Std dev:        ${(stdev/BB).toFixed(1)} BB/hand`);
  console.log("=".repeat(60));
}

// Timeout safety
setTimeout(() => {
  console.log("\n[StratBot] Timeout reached.");
  if (handsPlayed > 0) printResults();
  process.exit(0);
}, 600000); // 10 min max
