#!/usr/bin/env node
"use strict";

/**
 * Bot players — connects to the poker-lab server and auto-plays for seats 1-5.
 * Seat 0 is the human player (controls via browser UI).
 * Bots use TAG strategy (or neural if inference server is running).
 *
 * Usage: node scripts/bot-players.js
 */

const WebSocket = require("ws");
const { getLegalActions } = require("../src/engine/betting");

const http = require("http");
const { createCFRStrategy } = require("./cfr/cfr-bot");

// Support --table N for multi-table, remote host + API key via env vars
const _tableArg = process.argv.indexOf("--table");
const TABLE_ID = _tableArg >= 0 && process.argv[_tableArg + 1] ? process.argv[_tableArg + 1] : "1";
const SERVER_HOST = process.env.POKER_HOST || "localhost:9100";
const API_KEY = process.env.POKER_API_KEY || "";
const keyParam = API_KEY ? `&key=${encodeURIComponent(API_KEY)}` : "";
const WS_URL = `ws://${SERVER_HOST}?table=${TABLE_ID}${keyParam}`;
const NN_URL = "http://localhost:9200/predict";
const HUMAN_SEAT = 0;
const BOT_SEATS = [1, 2, 3, 4, 5];
const USE_CFR = true;  // Use CFR strategy (real learned play)
const USE_NEURAL = false;

// ── Load opponent profiles ──────────────────────────────────────────────
const fs = require("fs");
const path = require("path");

let opponentProfiles = {};
const PROFILES_PATH = path.join(__dirname, "opponent-profiles.json");
try {
  opponentProfiles = JSON.parse(fs.readFileSync(PROFILES_PATH, "utf8"));
  console.log(`Loaded ${Object.keys(opponentProfiles).length} opponent profiles`);
} catch (e) {
  console.log("No opponent profiles found, using default bot names");
}

// Pick top 5 opponents by hands played (with 10+ hands) to use as bot personalities
const rankedOpponents = Object.values(opponentProfiles)
  .filter(p => p.handsPlayed >= 10)
  .sort((a, b) => b.handsPlayed - a.handsPlayed)
  .slice(0, 5);

const BOT_NAMES = rankedOpponents.length >= 5
  ? rankedOpponents.map(p => p.name)
  : ["Bot_Alice", "Bot_Bob", "Bot_Charlie", "Bot_Diana", "Bot_Eve"];

// Map each bot seat to a profile (or null for default behavior)
const BOT_PROFILES = {};
for (let i = 0; i < BOT_SEATS.length; i++) {
  if (i < rankedOpponents.length) {
    BOT_PROFILES[BOT_SEATS[i]] = rankedOpponents[i];
    console.log(`  Seat ${BOT_SEATS[i]}: ${rankedOpponents[i].name} [${rankedOpponents[i].classification.toUpperCase()}] (VPIP ${rankedOpponents[i].vpip}%, PFR ${rankedOpponents[i].pfr}%)`);
  }
}

// Load CFR strategy
let cfrStrategy = null;
if (USE_CFR) {
  try {
    cfrStrategy = createCFRStrategy("./vision/models/cfr_strategy.json");
    console.log("CFR strategy loaded");
  } catch (e) {
    console.log("CFR strategy not available, falling back to profile-based play:", e.message);
  }
}

// ── TAG Strategy (same as self-play.js) ─────────────────────────────────

function evaluateHandStrength(cards, board, phase) {
  if (!cards || cards.length < 2) return 0.5;
  const c1 = cards[0], c2 = cards[1];
  const r1 = c1.rank, r2 = c2.rank;
  const suited = c1.suit === c2.suit;
  const pair = r1 === r2;
  const highCard = Math.max(r1, r2);
  const gap = Math.abs(r1 - r2);

  let pf = 0;
  if (pair) {
    pf = 0.5 + (r1 / 14) * 0.5;
  } else {
    pf = (highCard / 14) * 0.4;
    if (suited) pf += 0.08;
    if (gap <= 1) pf += 0.06;
    if (gap <= 3) pf += 0.03;
    if (r1 >= 10 && r2 >= 10) pf += 0.15;
    if (highCard === 14) pf += 0.1;
  }

  if (phase === "PREFLOP") return Math.min(1, pf);

  const boardRanks = board.map(c => c.rank);
  let post = pf;
  if (boardRanks.includes(r1)) post += 0.25;
  if (boardRanks.includes(r2)) post += 0.20;
  if (boardRanks.includes(r1) && boardRanks.includes(r2) && !pair) post += 0.20;
  if (pair && boardRanks.includes(r1)) post += 0.35;
  if (pair && boardRanks.length > 0 && r1 > Math.max(...boardRanks)) post += 0.15;

  return Math.min(1, post);
}

// ── Card encoding for neural net ─────────────────────────────────────
function encodeCard(card) {
  if (!card) return 52;
  return (card.rank - 2) * 4 + (card.suit - 1);
}

const PHASE_MAP = { PREFLOP: 0, FLOP: 1, TURN: 2, RIVER: 3 };
const ACTION_NAMES = ["FOLD", "CHECK", "CALL", "BET", "RAISE"];
const BB = 10;

function extractFeatures(seat, state) {
  const hand = state.hand;
  const s = state.seats[seat];
  const cards = s.holeCards || [];
  const board = hand.board || [];
  const legal = hand.legalActions;

  const heroCard1 = cards.length >= 1 ? encodeCard(cards[0]) : 52;
  const heroCard2 = cards.length >= 2 ? encodeCard(cards[1]) : 52;
  const boardCards = [];
  for (let i = 0; i < 5; i++) boardCards.push(i < board.length ? encodeCard(board[i]) : 52);

  const bb100 = BB * 100;
  const potNorm = (hand.pot || 0) / bb100;
  const stackNorm = (s.stack || 0) / bb100;
  const callNorm = (legal.callAmount || 0) / bb100;
  const potOdds = (hand.pot > 0 && legal.callAmount > 0) ? legal.callAmount / (hand.pot + legal.callAmount) : 0;

  let numOpponents = 0;
  for (const key of Object.keys(state.seats)) {
    const ss = state.seats[key];
    if (ss && ss.inHand && !ss.folded && parseInt(key) !== seat) numOpponents++;
  }

  const streetIdx = PHASE_MAP[hand.phase] || 0;
  const streetOneHot = [0, 0, 0, 0];
  streetOneHot[streetIdx] = 1;
  const posNorm = seat / 5;
  const handStrength = evaluateHandStrength(cards, board, hand.phase);
  const betToPot = (hand.pot > 0 && legal.callAmount > 0) ? Math.min(legal.callAmount / hand.pot, 3) : 0;
  const spr = hand.pot > 0 ? (s.stack || 0) / hand.pot : 10;
  const sprNorm = Math.min(spr / 20, 1);

  return { heroCard1, heroCard2, boardCards, potNorm, stackNorm, callNorm,
           potOdds, numOpponents, streetOneHot, posNorm, handStrength, betToPot, sprNorm };
}

function neuralDecide(seat, state, callback) {
  const hand = state.hand;
  const legal = hand.legalActions;
  const actions = legal.actions;
  const features = extractFeatures(seat, state);

  // Build legal mask
  const legalMask = [false, false, false, false, false];
  for (const a of actions) {
    const idx = ACTION_NAMES.indexOf(a);
    if (idx >= 0) legalMask[idx] = true;
  }

  const body = JSON.stringify({ features, legal_mask: legalMask });
  const req = http.request(NN_URL, { method: "POST", headers: { "Content-Type": "application/json" } }, (res) => {
    let data = "";
    res.on("data", d => data += d);
    res.on("end", () => {
      try {
        const result = JSON.parse(data);
        const action = result.action;
        const sizing = result.sizing || 0;
        const pot = hand.pot || 0;

        let amount;
        if (action === "BET") amount = Math.max(legal.minBet || BB, Math.round(sizing * pot));
        if (action === "RAISE") amount = Math.max(legal.minRaise || BB * 2, Math.round(sizing * pot));

        callback({ action, amount });
      } catch (e) {
        // Fallback to TAG
        callback(botDecideTAG(seat, state));
      }
    });
  });
  req.on("error", () => callback(botDecideTAG(seat, state)));
  req.write(body);
  req.end();
}

// ── Profile-based decision (opponent personality) ───────────────────────
function botDecideProfile(seat, state, profile) {
  const s = state.seats[seat];
  if (!s || s.status !== "OCCUPIED" || !s.inHand || s.folded || s.allIn) return null;

  const hand = state.hand;
  if (!hand || hand.actionSeat !== seat) return null;

  const legal = hand.legalActions;
  if (!legal || !legal.actions || legal.actions.length === 0) return null;

  const actions = legal.actions;
  const cards = s.holeCards || [];
  const board = hand.board || [];
  const phase = hand.phase;
  const pot = hand.pot || 0;
  const callAmount = legal.callAmount || 0;
  const minBet = legal.minBet || 0;
  const minRaise = legal.minRaise || 0;
  const maxRaise = legal.maxRaise || 0;

  const strength = evaluateHandStrength(cards, board, phase);
  const rand = Math.random();
  const type = profile.classification;

  // ── FISH: loose-passive, calls too much, rarely raises ──────────────
  if (type === "fish") {
    if (phase === "PREFLOP") {
      // Fish VPIP is high — call with almost anything
      const vpipThreshold = 1 - (profile.vpip / 100);  // e.g., VPIP 40% -> threshold 0.60
      if (strength > 0.65 && actions.includes("RAISE") && rand < 0.3) {
        return { action: "RAISE", amount: minRaise };
      }
      if (strength > vpipThreshold && actions.includes("CALL")) return { action: "CALL" };
      if (actions.includes("CHECK")) return { action: "CHECK" };
      // Fish even call with junk sometimes
      if (rand < 0.15 && actions.includes("CALL")) return { action: "CALL" };
      return { action: "FOLD" };
    }
    // Postflop: check-call station
    if (strength > 0.55) {
      if (actions.includes("BET") && rand < 0.35) return { action: "BET", amount: Math.max(minBet, Math.floor(pot * 0.4)) };
      if (actions.includes("CALL")) return { action: "CALL" };
      if (actions.includes("CHECK")) return { action: "CHECK" };
    }
    if (strength > 0.2) {
      if (actions.includes("CHECK")) return { action: "CHECK" };
      // Fish call with any piece of the board
      if (actions.includes("CALL") && callAmount < pot * 1.0) return { action: "CALL" };
      return { action: "FOLD" };
    }
    if (actions.includes("CHECK")) return { action: "CHECK" };
    if (rand < 0.12 && actions.includes("CALL")) return { action: "CALL" };
    return { action: "FOLD" };
  }

  // ── TAG: tight preflop, aggressive postflop ─────────────────────────
  if (type === "tag") {
    if (phase === "PREFLOP") {
      // TAG only enters with good hands, but usually raises
      if (strength > 0.55 && actions.includes("RAISE")) {
        const amt = Math.min(minRaise + Math.floor(pot * 0.6), maxRaise);
        return { action: "RAISE", amount: Math.max(minRaise, amt) };
      }
      if (strength > 0.45 && actions.includes("CALL") && rand < 0.4) return { action: "CALL" };
      if (actions.includes("CHECK")) return { action: "CHECK" };
      return { action: "FOLD" };
    }
    // Postflop: aggressive when in hand
    if (strength > 0.5) {
      if (actions.includes("RAISE") && rand < 0.5) {
        return { action: "RAISE", amount: Math.max(minRaise, Math.min(minRaise + Math.floor(pot * 0.75), maxRaise)) };
      }
      if (actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, Math.floor(pot * 0.65)) };
      if (actions.includes("CALL")) return { action: "CALL" };
      return { action: "CHECK" };
    }
    if (strength > 0.3) {
      if (actions.includes("CHECK")) return { action: "CHECK" };
      // TAG bluffs sometimes with equity
      if (rand < 0.25 && actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, Math.floor(pot * 0.5)) };
      if (actions.includes("CALL") && callAmount < pot * 0.5) return { action: "CALL" };
      return { action: "FOLD" };
    }
    if (actions.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  // ── LAG: loose preflop, aggressive everywhere ───────────────────────
  if (type === "lag") {
    if (phase === "PREFLOP") {
      if (strength > 0.40 && actions.includes("RAISE")) {
        const amt = Math.min(minRaise + Math.floor(pot * 0.5), maxRaise);
        return { action: "RAISE", amount: Math.max(minRaise, amt) };
      }
      // LAG raises with marginal hands too
      if (strength > 0.25 && actions.includes("RAISE") && rand < 0.4) {
        return { action: "RAISE", amount: minRaise };
      }
      if (strength > 0.20 && actions.includes("CALL")) return { action: "CALL" };
      if (actions.includes("CHECK")) return { action: "CHECK" };
      // Even raise with junk as a bluff
      if (rand < 0.12 && actions.includes("RAISE")) return { action: "RAISE", amount: minRaise };
      return { action: "FOLD" };
    }
    // Postflop: bet and raise frequently
    if (strength > 0.45) {
      if (actions.includes("RAISE") && rand < 0.6) {
        return { action: "RAISE", amount: Math.max(minRaise, Math.min(minRaise + Math.floor(pot * 0.8), maxRaise)) };
      }
      if (actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, Math.floor(pot * 0.7)) };
      if (actions.includes("CALL")) return { action: "CALL" };
      return { action: "CHECK" };
    }
    if (strength > 0.2) {
      // LAG bluffs aggressively
      if (rand < 0.40 && actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, Math.floor(pot * 0.55)) };
      if (rand < 0.30 && actions.includes("RAISE")) return { action: "RAISE", amount: minRaise };
      if (actions.includes("CHECK")) return { action: "CHECK" };
      if (actions.includes("CALL") && callAmount < pot * 0.7) return { action: "CALL" };
      return { action: "FOLD" };
    }
    // Even with weak hands, LAG fires sometimes
    if (rand < 0.20 && actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, Math.floor(pot * 0.5)) };
    if (actions.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  // ── NIT: very tight, only plays premiums ────────────────────────────
  if (type === "nit") {
    if (phase === "PREFLOP") {
      // Nit only plays top ~15% of hands
      if (strength > 0.75 && actions.includes("RAISE")) {
        return { action: "RAISE", amount: Math.max(minRaise, Math.min(minRaise + Math.floor(pot * 0.5), maxRaise)) };
      }
      if (strength > 0.65 && actions.includes("CALL")) return { action: "CALL" };
      if (actions.includes("CHECK")) return { action: "CHECK" };
      return { action: "FOLD" };
    }
    // Postflop: only continues with strong hands
    if (strength > 0.6) {
      if (actions.includes("BET")) return { action: "BET", amount: Math.max(minBet, Math.floor(pot * 0.6)) };
      if (actions.includes("RAISE")) return { action: "RAISE", amount: Math.max(minRaise, Math.min(minRaise + Math.floor(pot * 0.6), maxRaise)) };
      if (actions.includes("CALL")) return { action: "CALL" };
      return { action: "CHECK" };
    }
    if (strength > 0.4) {
      if (actions.includes("CHECK")) return { action: "CHECK" };
      if (actions.includes("CALL") && callAmount < pot * 0.3) return { action: "CALL" };
      return { action: "FOLD" };
    }
    if (actions.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  // ── MANIAC: raises everything, huge aggression ──────────────────────
  if (type === "maniac") {
    if (phase === "PREFLOP") {
      if (actions.includes("RAISE") && (strength > 0.25 || rand < 0.35)) {
        const amt = Math.min(minRaise + Math.floor(pot * 0.7), maxRaise);
        return { action: "RAISE", amount: Math.max(minRaise, amt) };
      }
      if (actions.includes("CALL")) return { action: "CALL" };
      if (actions.includes("CHECK")) return { action: "CHECK" };
      return { action: "FOLD" };
    }
    // Postflop: bet/raise relentlessly
    if (actions.includes("RAISE") && rand < 0.5) {
      return { action: "RAISE", amount: Math.max(minRaise, Math.min(minRaise + Math.floor(pot * 1.0), maxRaise)) };
    }
    if (actions.includes("BET") && rand < 0.7) {
      return { action: "BET", amount: Math.max(minBet, Math.floor(pot * 0.75)) };
    }
    if (actions.includes("CALL")) return { action: "CALL" };
    if (actions.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  // ── REG (default): balanced play ────────────────────────────────────
  return null;  // Fall through to TAG/CFR
}

// TAG fallback
function botDecideTAG(seat, state) {
  const s = state.seats[seat];
  if (!s || s.status !== "OCCUPIED" || !s.inHand || s.folded || s.allIn) return null;

  const hand = state.hand;
  if (!hand || hand.actionSeat !== seat) return null;

  const legal = hand.legalActions;
  if (!legal || !legal.actions || legal.actions.length === 0) return null;

  const actions = legal.actions;
  const cards = s.holeCards || [];
  const board = hand.board || [];
  const phase = hand.phase;
  const pot = hand.pot || 0;
  const callAmount = legal.callAmount || 0;
  const minBet = legal.minBet || 0;
  const minRaise = legal.minRaise || 0;
  const maxRaise = legal.maxRaise || 0;

  const strength = evaluateHandStrength(cards, board, phase);

  // Add some randomness to timing and decisions
  const rand = Math.random();

  if (phase === "PREFLOP") {
    // Raise with strong hands
    if (strength > 0.65 && actions.includes("RAISE")) {
      const amt = Math.min(minRaise + Math.floor(pot * 0.5), maxRaise);
      return { action: "RAISE", amount: Math.max(minRaise, amt) };
    }
    // Call with decent hands — much looser than before
    if (strength > 0.20 && actions.includes("CALL")) return { action: "CALL" };
    if (actions.includes("CHECK")) return { action: "CHECK" };
    // Sometimes call with junk (10% of the time)
    if (rand < 0.10 && actions.includes("CALL")) return { action: "CALL" };
    return { action: "FOLD" };
  }

  // Postflop — more aggressive
  if (strength > 0.6) {
    if (actions.includes("RAISE") && rand < 0.6) {
      const amt = Math.min(minRaise + Math.floor(pot * 0.75), maxRaise);
      return { action: "RAISE", amount: Math.max(minRaise, amt) };
    }
    if (actions.includes("BET")) {
      const amt = Math.max(minBet, Math.floor(pot * 0.6));
      return { action: "BET", amount: amt };
    }
    if (actions.includes("CALL")) return { action: "CALL" };
    return { action: "CHECK" };
  }

  if (strength > 0.25) {
    if (actions.includes("CHECK")) return { action: "CHECK" };
    // Call raises with medium hands more often
    if (actions.includes("CALL") && callAmount < pot * 0.8) return { action: "CALL" };
    // Bluff bet sometimes
    if (rand < 0.25 && actions.includes("BET")) return { action: "BET", amount: minBet };
    if (rand < 0.15 && actions.includes("CALL")) return { action: "CALL" };
    return { action: "FOLD" };
  }

  // Weak hands — still play sometimes
  if (actions.includes("CHECK")) return { action: "CHECK" };
  // Bluff 15% of the time
  if (rand < 0.15 && actions.includes("BET")) return { action: "BET", amount: minBet };
  if (rand < 0.08 && actions.includes("CALL")) return { action: "CALL" };
  return { action: "FOLD" };
}

// ── Humanization Layer ──────────────────────────────────────────────────
// Makes bot decisions harder to detect by adding realistic imperfections

// Tilt state per seat: tracks recent big losses
const tiltState = {};  // seat -> { recentLosses: number[], tiltLevel: 0-1 }

function initTilt(seat) {
  tiltState[seat] = { recentLosses: [], tiltLevel: 0 };
}

function recordHandResult(seat, profitBB) {
  if (!tiltState[seat]) initTilt(seat);
  const ts = tiltState[seat];
  ts.recentLosses.push(profitBB);
  // Rolling window of last 10 hands
  if (ts.recentLosses.length > 10) ts.recentLosses.shift();
  // Tilt increases with consecutive losses, decays with wins
  const recentSum = ts.recentLosses.reduce((a, b) => a + b, 0);
  const consecutiveLosses = ts.recentLosses.slice().reverse().findIndex(x => x >= 0);
  const lossStreak = consecutiveLosses === -1 ? ts.recentLosses.length : consecutiveLosses;
  ts.tiltLevel = Math.min(1, Math.max(0, -recentSum / 30 + lossStreak * 0.1));
}

function getTiltLevel(seat) {
  return tiltState[seat]?.tiltLevel || 0;
}

// Bet size humanization: make sizing look human, not pot-fractional
function humanizeBetSize(amount, pot, minBet, maxRaise) {
  if (!amount || amount <= 0 || pot <= 0) return amount;

  const r = Math.random();

  // 40% of the time: pick an absolute dollar amount humans would type
  // Humans at micros think in dollar amounts, not pot fractions
  if (r < 0.40) {
    // Common micro-stakes bet amounts ($0.15 to $2.00) — absolute, not pot-relative
    const humanAmounts = [15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 75, 80, 100, 125, 150, 175, 200];
    // Pick the closest human amount to the original
    let best = humanAmounts[0];
    let bestDist = Math.abs(amount - best);
    for (const ha of humanAmounts) {
      const dist = Math.abs(amount - ha);
      if (dist < bestDist) { best = ha; bestDist = dist; }
    }
    // Add small jitter (±1-3 units)
    best += Math.floor(Math.random() * 7) - 3;
    return Math.max(minBet || amount, Math.min(best, maxRaise || amount));
  }

  // 35% of the time: jitter the calculated amount by ±8-20%
  if (r < 0.75) {
    const jitterPct = 0.08 + Math.random() * 0.12;
    const direction = Math.random() < 0.5 ? 1 : -1;
    let humanized = Math.round(amount * (1 + direction * jitterPct));
    // Add random offset of 1-5 units to break exact fractions
    humanized += Math.floor(Math.random() * 5) + 1;
    return Math.max(minBet || amount, Math.min(humanized, maxRaise || amount));
  }

  // 25% of the time: use the slider-style amount (round to nearest 5)
  // But NOT pot-relative — just round the jittered amount
  let humanized = amount + Math.floor(Math.random() * 20) - 10;
  humanized = Math.round(humanized / 5) * 5;
  // Ensure we don't accidentally hit exact pot fractions
  const frac = humanized / pot;
  const COMMON = [0.25, 0.33, 0.5, 0.66, 0.67, 0.75, 1.0, 1.5, 2.0];
  if (COMMON.some(cf => Math.abs(frac - cf) < 0.02)) {
    humanized += (Math.random() < 0.5 ? 3 : -3); // nudge off exact fraction
  }
  return Math.max(minBet || amount, Math.min(humanized, maxRaise || amount));
}

// Timing humanization: log-normal distribution (humans have fast checks, slow decisions)
function humanDelay(action, strength) {
  // Base: log-normal with mean ~1.5s, heavy right tail
  const logMean = 0.3;
  const logStd = 0.6;
  // Box-Muller transform for normal distribution
  const u1 = Math.random(), u2 = Math.random();
  const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  let delay = Math.exp(logMean + logStd * z) * 1000; // ms

  // Fast-path for obvious actions (check, easy fold)
  if (action === "CHECK") delay *= 0.5;
  if (action === "FOLD" && strength < 0.15) delay *= 0.4;

  // Slow down for big decisions (raises, close spots)
  if (action === "RAISE" || action === "BET") delay *= 1.4;
  if (strength > 0.3 && strength < 0.6) delay *= 1.3; // tough spot

  // Occasional "tank" (2-8% of decisions, 4-8 seconds)
  if (Math.random() < 0.05) delay = 4000 + Math.random() * 4000;

  // Clamp to 300ms-10s
  return Math.max(300, Math.min(10000, delay));
}

// Apply tilt to a decision: loosen calling, increase aggression
function applyTilt(decision, tiltLevel, actions, legal) {
  if (tiltLevel < 0.2) return decision; // not tilted enough

  const r = Math.random();

  // Tilted players call more instead of folding
  if (decision.action === "FOLD" && tiltLevel > 0.3 && r < tiltLevel * 0.5) {
    if (actions.includes("CALL")) return { action: "CALL" };
  }

  // Tilted players raise more aggressively
  if (decision.action === "CALL" && tiltLevel > 0.5 && r < tiltLevel * 0.3) {
    if (actions.includes("RAISE") && legal.minRaise) {
      return { action: "RAISE", amount: legal.maxRaise || legal.minRaise }; // shove tendency
    }
  }

  return decision;
}

// Humanize a complete decision (sizing + tilt)
function humanizeDecision(decision, seat, state) {
  if (!decision) return decision;

  const hand = state.hand;
  const legal = hand?.legalActions;
  const actions = legal?.actions || [];
  const pot = hand?.pot || 0;
  const tilt = getTiltLevel(seat);

  // Apply tilt modifications
  decision = applyTilt(decision, tilt, actions, legal);

  // Humanize bet sizing
  if (decision.amount && (decision.action === "BET" || decision.action === "RAISE")) {
    decision.amount = humanizeBetSize(
      decision.amount, pot,
      decision.action === "BET" ? legal?.minBet : legal?.minRaise,
      legal?.maxRaise
    );
  }

  return decision;
}

// ── Main ─────────────────────────────────────────────────────────────────

let state = null;
let msgId = 0;
let ws = null;
let seatedBots = new Set();

// ── BB/hour tracking ────────────────────────────────────────────────────
const stats = {};  // seat -> { name, buyIn, hands, startTime }

function initStats(seat, name, buyIn) {
  stats[seat] = { name, buyIn, totalBuyIn: buyIn, hands: 0, startTime: Date.now() };
}

function updateStats(newState) {
  if (!newState || !newState.seats) return;
  for (const seat of [...BOT_SEATS, HUMAN_SEAT]) {
    const s = newState.seats[seat];
    if (!s || s.status === "EMPTY") continue;
    if (!stats[seat]) {
      initStats(seat, s.player?.name || `Seat ${seat}`, s.stack);
    }
  }
}

function printStats() {
  const now = Date.now();
  const lines = [];
  for (const seat of [HUMAN_SEAT, ...BOT_SEATS]) {
    const st = stats[seat];
    if (!st || !state?.seats[seat]) continue;
    const s = state.seats[seat];
    if (s.status === "EMPTY") continue;
    const stack = s.stack;
    const profitCents = stack - st.totalBuyIn;
    const profitBB = profitCents / BB;
    const hoursElapsed = (now - st.startTime) / 3600000;
    const bbPerHour = hoursElapsed > 0 ? profitBB / hoursElapsed : 0;
    const tag = seat === HUMAN_SEAT ? " (HERO)" : "";
    lines.push(`  Seat ${seat} ${st.name}${tag}: ${profitBB >= 0 ? "+" : ""}${profitBB.toFixed(1)} bb (${bbPerHour >= 0 ? "+" : ""}${bbPerHour.toFixed(1)} bb/hr) | ${st.hands} hands | $${(stack / 100).toFixed(2)}`);
  }
  if (lines.length > 0) {
    console.log("\n── BB/Hour Stats ──────────────────────────────────");
    lines.forEach(l => console.log(l));
    console.log("───────────────────────────────────────────────────\n");
  }
}

// Track rebuys: if stack increases between hands more than pot won, it's a rebuy
let prevStacks = {};

function send(cmd, payload) {
  msgId++;
  ws.send(JSON.stringify({ id: `bot-${msgId}`, cmd, payload: payload || {} }));
}

function handleState(newState) {
  state = newState;

  if (!state || !state.hand) return;

  const actionSeat = state.hand.actionSeat;
  if (actionSeat === null || actionSeat === undefined) return;

  // Only act for bot seats, not the human
  if (actionSeat === HUMAN_SEAT) return;
  if (!BOT_SEATS.includes(actionSeat)) return;

  function executeAction(decision) {
    if (!decision) return;

    // Humanize the decision (tilt + bet sizing noise)
    const s = state.seats[actionSeat];
    const strength = evaluateHandStrength(s.holeCards || [], (state.hand.board || []), state.hand.phase);
    decision = humanizeDecision(decision, actionSeat, state);

    // Human-like timing (log-normal distribution)
    const delay = humanDelay(decision.action, strength);

    setTimeout(() => {
      const payload = { seat: actionSeat, action: decision.action };
      if (decision.amount !== undefined) payload.amount = decision.amount;
      const tilt = getTiltLevel(actionSeat);
      const tiltTag = tilt > 0.2 ? ` [TILT ${(tilt * 100).toFixed(0)}%]` : "";
      console.log(`  [Bot seat ${actionSeat}] ${decision.action}${decision.amount ? " $" + (decision.amount / 100).toFixed(2) : ""}${tiltTag} (${(delay / 1000).toFixed(1)}s)`);
      send("PLAYER_ACTION", payload);
    }, delay);
  }

  const hand = state.hand;
  const s = state.seats[actionSeat];
  const legal = hand.legalActions;
  const actions = legal.actions;

  // Use opponent-profile-based personality if available for this seat
  const profile = BOT_PROFILES[actionSeat];
  if (profile) {
    const profileDecision = botDecideProfile(actionSeat, state, profile);
    if (profileDecision) {
      executeAction(profileDecision);
      return;
    }
    // If profile returns null (e.g., REG type), fall through to CFR/TAG
  }

  // Use CFR strategy if available
  if (USE_CFR && cfrStrategy) {
    try {
      // Convert string cards ("Ah") to objects ({rank:14, suit:1}) for CFR
      const RANK_MAP = {'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9,'T':10,'J':11,'Q':12,'K':13,'A':14};
      const SUIT_MAP = {'c':1,'d':2,'h':3,'s':4};
      function convertCard(c) {
        if (!c || typeof c !== 'string') return c;
        return { rank: RANK_MAP[c[0]] || 0, suit: SUIT_MAP[c[1]] || 0 };
      }
      // Deep copy seats with converted cards
      const cfrSeats = {};
      for (const [k, seat] of Object.entries(state.seats)) {
        cfrSeats[k] = { ...seat };
        if (seat.holeCards) cfrSeats[k].holeCards = seat.holeCards.map(convertCard);
      }
      const cfrHand = { ...hand };
      if (cfrHand.board) cfrHand.board = cfrHand.board.map(convertCard);

      const cfrDecision = cfrStrategy(actionSeat, legal, { hand: cfrHand, table: { seats: cfrSeats, maxSeats: state.maxSeats || 6, bb: state.bb || 10 } }, Math.random);
      if (cfrDecision) {
        executeAction(cfrDecision);
        return;
      }
    } catch (e) {
      // Fall through to hardcoded
    }
  }

  // Fallback: micro-stakes strategy
  const cards = s.holeCards || [];
  const board = hand.board || [];
  const strength = evaluateHandStrength(cards, board, hand.phase);
  const r = Math.random();
  const pot = hand.pot || 0;

  let decision = null;

  if (hand.phase === "PREFLOP") {
    // Micro-stakes preflop: call most hands, raise good ones, fold trash
    if (strength > 0.65) {
      if (actions.includes("RAISE")) decision = { action: "RAISE", amount: legal.minRaise };
      else if (actions.includes("CALL")) decision = { action: "CALL" };
    } else if (strength > 0.25) {
      // Call with most hands — typical loose micro player
      if (actions.includes("CALL")) decision = { action: "CALL" };
      else if (actions.includes("CHECK")) decision = { action: "CHECK" };
      // Occasionally raise (limp-raise)
      else if (r < 0.15 && actions.includes("RAISE")) decision = { action: "RAISE", amount: legal.minRaise };
    } else {
      // Junk — still call 30% of the time (it's micro stakes)
      if (r < 0.30 && actions.includes("CALL")) decision = { action: "CALL" };
      else if (actions.includes("CHECK")) decision = { action: "CHECK" };
      else decision = { action: "FOLD" };
    }
  } else {
    // Postflop: check/call heavy, bet with strong hands
    if (strength > 0.6) {
      // Strong — bet or raise
      if (r < 0.7 && actions.includes("BET")) decision = { action: "BET", amount: Math.max(legal.minBet || 10, Math.floor(pot * 0.5)) };
      else if (r < 0.5 && actions.includes("RAISE")) decision = { action: "RAISE", amount: legal.minRaise };
      else if (actions.includes("CALL")) decision = { action: "CALL" };
      else if (actions.includes("CHECK")) decision = { action: "CHECK" };
    } else if (strength > 0.3) {
      // Medium — check/call
      if (actions.includes("CHECK")) decision = { action: "CHECK" };
      else if (actions.includes("CALL")) decision = { action: "CALL" };
      // Occasional bluff
      else if (r < 0.20 && actions.includes("BET")) decision = { action: "BET", amount: legal.minBet || 10 };
      else decision = { action: "FOLD" };
    } else {
      // Weak — check or fold, bluff sometimes
      if (actions.includes("CHECK")) decision = { action: "CHECK" };
      else if (r < 0.15 && actions.includes("CALL")) decision = { action: "CALL" };
      else if (r < 0.10 && actions.includes("BET")) decision = { action: "BET", amount: legal.minBet || 10 };
      else decision = { action: "FOLD" };
    }
  }

  if (!decision) {
    if (actions.includes("CHECK")) decision = { action: "CHECK" };
    else if (actions.includes("FOLD")) decision = { action: "FOLD" };
    else decision = { action: actions[0] };
  }

  executeAction(decision);
}

function seatBots() {
  for (let i = 0; i < BOT_SEATS.length; i++) {
    const seat = BOT_SEATS[i];
    if (!seatedBots.has(seat)) {
      send("SEAT_PLAYER", {
        seat,
        name: BOT_NAMES[i],
        buyIn: 1000,
        country: "XX",
      });
    }
  }
}

function connect() {
  console.log("Bot Players — connecting to " + WS_URL);
  ws = new WebSocket(WS_URL);

  ws.on("open", () => {
    console.log("Connected");
  });

  ws.on("message", (raw) => {
    const msg = JSON.parse(raw.toString());

    if (msg.welcome) {
      state = msg.state;
      console.log(`Session: ${msg.sessionId}`);
      console.log(`Hands played: ${state.handsPlayed}`);

      // Check which seats need bots
      for (const seat of BOT_SEATS) {
        if (state.seats[seat] && state.seats[seat].status === "OCCUPIED") {
          seatedBots.add(seat);
        }
      }

      // Seat any missing bots
      seatBots();

      // Init stats for already-seated players
      updateStats(state);

      // If there's an active hand and it's a bot's turn, act
      if (state.hand && state.hand.actionSeat !== null) {
        handleState(state);
      }
      return;
    }

    if (msg.ok && msg.state) {
      handleState(msg.state);
      return;
    }

    if (msg.broadcast) {
      // Refresh state after any broadcast
      send("GET_STATE");
      return;
    }

    if (msg.ok && msg.events) {
      // Check if we need to refresh state
      const hasHandEnd = msg.events.some(e => e.type === "HAND_END");
      const hasSeat = msg.events.some(e => e.type === "SEAT_PLAYER");

      if (hasSeat) {
        msg.events.filter(e => e.type === "SEAT_PLAYER").forEach(e => {
          seatedBots.add(e.seat);
          initStats(e.seat, e.player, e.buyIn || 1000);
          initTilt(e.seat);
          console.log(`  Seated ${e.player} at seat ${e.seat}`);
        });
      }

      if (hasHandEnd) {
        // Track tilt: measure profit/loss for each bot this hand
        if (state?.seats) {
          for (const seat of BOT_SEATS) {
            const s = state.seats[seat];
            if (!s || s.status === "EMPTY") continue;
            const prev = prevStacks[seat];
            if (prev !== undefined) {
              const profitBB = (s.stack - prev) / BB;
              recordHandResult(seat, profitBB);
            }
          }
        }
        // Track hands and rebuys for bb/hour
        for (const seat of [HUMAN_SEAT, ...BOT_SEATS]) {
          if (stats[seat]) stats[seat].hands++;
        }
        // Detect rebuys: stack jumped above previous + pot won
        if (state?.seats) {
          for (const seat of [HUMAN_SEAT, ...BOT_SEATS]) {
            const s = state.seats[seat];
            if (!s || s.status === "EMPTY" || !stats[seat]) continue;
            const prev = prevStacks[seat];
            if (prev !== undefined && s.stack > prev + (state.hand?.pot || 0) + 100) {
              const rebuyAmount = s.stack - prev;
              stats[seat].totalBuyIn += rebuyAmount;
            }
            prevStacks[seat] = s.stack;
          }
        }
        // Print stats every 10 hands
        const totalHands = stats[BOT_SEATS[0]]?.hands || 0;
        if (totalHands > 0 && totalHands % 10 === 0) {
          printStats();
        }
        console.log("  Hand complete");
      }

      // Always refresh state to check if bot needs to act
      send("GET_STATE");
    }
  });

  ws.on("close", () => {
    console.log("Disconnected. Reconnecting in 3s...");
    setTimeout(connect, 3000);
  });

  ws.on("error", (err) => {
    console.log("Error: " + err.message);
  });
}

connect();
console.log("Press Ctrl+C to stop bots.");

process.on("SIGINT", () => {
  console.log("\nFinal stats:");
  printStats();
  process.exit(0);
});
