#!/usr/bin/env node
"use strict";

/**
 * Generate TRUE self-play training data.
 *
 * Key difference from generate-rl-data.js:
 * - All hands play to SHOWDOWN (no folding during data collection)
 * - This gives every decision a meaningful reward signal
 * - The bot learns from outcomes, not from copying TAG
 *
 * Usage:
 *   node scripts/generate-selfplay-data.js --hands 100000
 */

const { createGame, ACTION, PHASE } = require("../src/index");
const { getLegalActions } = require("../src/engine/betting");
const fs = require("fs");
const path = require("path");

function encodeCard(card) {
  if (!card) return 52;
  return (card.rank - 2) * 4 + (card.suit - 1);
}

function evaluateHandStrength(cards, board, phase) {
  if (!cards || cards.length < 2) return 0.5;
  const c1 = cards[0], c2 = cards[1];
  const r1 = c1.rank, r2 = c2.rank;
  const suited = c1.suit === c2.suit;
  const pair = r1 === r2;
  const highCard = Math.max(r1, r2);
  const gap = Math.abs(r1 - r2);
  let pf = 0;
  if (pair) pf = 0.5 + (r1 / 14) * 0.5;
  else {
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

const PHASE_MAP = { PREFLOP: 0, FLOP: 1, TURN: 2, RIVER: 3 };
const ACTION_MAP = { FOLD: 0, CHECK: 1, CALL: 2, BET: 3, RAISE: 4 };
const BB = 10;

function extractFeatures(seat, legal, state) {
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  const cards = seatState.holeCards || [];
  const board = hand.board || [];
  const heroCard1 = cards.length >= 1 ? encodeCard(cards[0]) : 52;
  const heroCard2 = cards.length >= 2 ? encodeCard(cards[1]) : 52;
  const boardCards = [];
  for (let i = 0; i < 5; i++) boardCards.push(i < board.length ? encodeCard(board[i]) : 52);
  const bb100 = BB * 100;
  const potNorm = (hand.pot || 0) / bb100;
  const stackNorm = (seatState.stack || 0) / bb100;
  const callNorm = (legal.callAmount || 0) / bb100;
  const potOdds = (hand.pot > 0 && legal.callAmount > 0) ? legal.callAmount / (hand.pot + legal.callAmount) : 0;
  let numOpponents = 0;
  for (const key of Object.keys(state.table.seats)) {
    const s = state.table.seats[key];
    if (s && s.inHand && !s.folded && parseInt(key) !== seat) numOpponents++;
  }
  const streetIdx = PHASE_MAP[hand.phase] || 0;
  const streetOneHot = [0, 0, 0, 0];
  streetOneHot[streetIdx] = 1;
  const posNorm = seat / 5;
  const handStrength = evaluateHandStrength(cards, board, hand.phase);
  const betToPot = (hand.pot > 0 && legal.callAmount > 0) ? Math.min(legal.callAmount / hand.pot, 3) : 0;
  const spr = hand.pot > 0 ? (seatState.stack || 0) / hand.pot : 10;
  const sprNorm = Math.min(spr / 20, 1);
  return { heroCard1, heroCard2, boardCards, potNorm, stackNorm, callNorm, potOdds, numOpponents, streetOneHot, posNorm, handStrength, betToPot, sprNorm };
}

// ── Self-play with MIXED strategy ──────────────────────────────────────
// Sometimes call, sometimes raise, sometimes fold — explore all actions
// This gives diverse training data with real outcomes

function mixedStrategy(seat, legal, state, rng) {
  const actions = legal.actions;
  if (actions.length === 0) return null;
  if (actions.length === 1) return { action: actions[0] };

  const seatState = state.table.seats[seat];
  const cards = seatState.holeCards || [];
  const board = state.hand.board || [];
  const strength = evaluateHandStrength(cards, board, state.hand.phase);
  const r = rng();

  // Mix of strategy + randomness:
  // Strong hands (>0.6): 60% raise, 30% call, 10% fold
  // Medium hands (0.3-0.6): 20% raise, 50% call/check, 30% fold
  // Weak hands (<0.3): 5% raise (bluff), 30% call, 65% fold

  if (strength > 0.6) {
    if (r < 0.6 && actions.includes("RAISE")) {
      return { action: "RAISE", amount: legal.minRaise || BB * 2 };
    }
    if (r < 0.9 && actions.includes("CALL")) return { action: "CALL" };
    if (actions.includes("CHECK")) return { action: "CHECK" };
    if (actions.includes("CALL")) return { action: "CALL" };
  } else if (strength > 0.3) {
    if (r < 0.2 && actions.includes("RAISE")) {
      return { action: "RAISE", amount: legal.minRaise || BB * 2 };
    }
    if (r < 0.5 && actions.includes("BET")) {
      return { action: "BET", amount: legal.minBet || BB };
    }
    if (r < 0.7 && actions.includes("CALL")) return { action: "CALL" };
    if (actions.includes("CHECK")) return { action: "CHECK" };
    if (r < 0.85 && actions.includes("CALL")) return { action: "CALL" };
    return { action: "FOLD" };
  } else {
    // Weak — but still play sometimes
    if (r < 0.05 && actions.includes("RAISE")) {
      return { action: "RAISE", amount: legal.minRaise || BB * 2 };
    }
    if (r < 0.15 && actions.includes("BET")) {
      return { action: "BET", amount: legal.minBet || BB };
    }
    if (r < 0.35 && actions.includes("CALL")) return { action: "CALL" };
    if (actions.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  // Fallback
  if (actions.includes("CHECK")) return { action: "CHECK" };
  if (actions.includes("CALL")) return { action: "CALL" };
  return { action: "FOLD" };
}

// ── Main ───────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const opts = {};
for (let i = 0; i < args.length; i += 2) {
  const key = args[i].replace("--", "");
  opts[key] = args[i + 1];
}

const numHands = parseInt(opts.hands) || 100000;
const numSeats = parseInt(opts.seats) || 6;
const seed = parseInt(opts.seed) || Date.now();

let rngState = seed;
function rng() {
  rngState = (rngState * 1664525 + 1013904223) & 0x7fffffff;
  return rngState / 0x7fffffff;
}

const outPath = path.join(__dirname, "..", "vision", "data", "selfplay_data.jsonl");
const outStream = fs.createWriteStream(outPath);

const game = createGame(
  { tableId: "selfplay", tableName: "SelfPlay", maxSeats: numSeats, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 50000 },
  { sessionId: `sp-${seed}`, logPath: null, rng }
);

for (let i = 0; i < numSeats; i++) game.sitDown(i, `P${i}`, 1000);

console.log(`Self-Play Data Generation (MIXED strategy)`);
console.log(`Hands: ${numHands}, Seats: ${numSeats}`);

let handsCompleted = 0;
let decisionsRecorded = 0;
let errors = 0;
const startTime = Date.now();

for (let h = 0; h < numHands; h++) {
  // Rebuy busted players
  const st = game.getState();
  for (let i = 0; i < numSeats; i++) {
    if (st.table.seats[i].stack < 20) {
      try { game.leave(i); game.sitDown(i, `P${i}`, 1000); } catch (e) {}
    }
  }

  try { game.startHand(); } catch (e) { errors++; continue; }

  const preState = game.getState();
  const preStacks = {};
  for (let i = 0; i < numSeats; i++) preStacks[i] = preState.table.seats[i].stack;

  const handDecisions = [];
  let actionCount = 0;

  while (!game.isHandComplete() && actionCount < 100) {
    const seat = game.getActionSeat();
    if (seat === null) break;

    const currentState = game.getState();
    const seatState = currentState.table.seats[seat];
    if (!seatState || !seatState.inHand) break;

    const legal = getLegalActions(seatState, currentState.hand, currentState.table.bb);
    if (!legal.actions.length) break;

    const features = extractFeatures(seat, legal, currentState);
    const decision = mixedStrategy(seat, legal, currentState, rng);
    if (!decision) break;

    const actionIdx = ACTION_MAP[decision.action] || 0;

    handDecisions.push({
      seat,
      features,
      actionIdx,
      amount: decision.amount || 0,
      legalActions: legal.actions.map(a => ACTION_MAP[a] || 0),
      minBet: legal.minBet || 0,
      minRaise: legal.minRaise || 0,
      maxRaise: legal.maxRaise || 0,
      callAmount: legal.callAmount || 0,
    });

    try {
      game.act(seat, decision.action, decision.amount);
    } catch (e) {
      try { game.act(seat, "FOLD"); } catch (_) {}
      errors++;
    }
    actionCount++;
  }

  handsCompleted++;

  // Calculate REAL profit per seat (non-zero because hands go to showdown)
  const postState = game.getState();
  const profits = {};
  for (let i = 0; i < numSeats; i++) {
    profits[i] = postState.table.seats[i].stack - preStacks[i];
  }

  // Write decisions with REAL rewards
  for (const d of handDecisions) {
    const reward = profits[d.seat] || 0;
    const rewardNorm = reward / BB;
    outStream.write(JSON.stringify({
      s: d.features,
      a: d.actionIdx,
      amt: d.amount,
      r: rewardNorm,
      legal: d.legalActions,
      minBet: d.minBet,
      minRaise: d.minRaise,
      maxRaise: d.maxRaise,
      callAmt: d.callAmount,
    }) + "\n");
    decisionsRecorded++;
  }

  if ((h + 1) % 5000 === 0) {
    const elapsed = (Date.now() - startTime) / 1000;
    process.stdout.write(`\r  ${handsCompleted}/${numHands} | ${decisionsRecorded} decisions | ${Math.round(handsCompleted / elapsed)} hands/sec`);
  }
}

outStream.end();
const elapsed = (Date.now() - startTime) / 1000;
console.log(`\n\nDone: ${handsCompleted} hands, ${decisionsRecorded} decisions, ${Math.round(handsCompleted / elapsed)} hands/sec`);
console.log(`Output: ${outPath}`);

// Verify reward distribution
const lines = fs.readFileSync(outPath, "utf8").trim().split("\n");
let pos = 0, neg = 0, zero = 0;
for (const line of lines) {
  const r = JSON.parse(line).r;
  if (r > 0.01) pos++;
  else if (r < -0.01) neg++;
  else zero++;
}
console.log(`Rewards: ${pos} positive, ${neg} negative, ${zero} zero (${(zero / lines.length * 100).toFixed(1)}% zero)`);
