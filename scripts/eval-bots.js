#!/usr/bin/env node
"use strict";

/**
 * Round-robin bot evaluation framework.
 *
 * Runs all registered strategies against each other in fair 6-max games.
 * Each strategy plays equal hands in every seat position to eliminate
 * positional bias. Computes bb/100 and ELO ratings.
 *
 * Usage:
 *   node scripts/eval-bots.js                          # default: 2k hands/matchup
 *   node scripts/eval-bots.js --hands 5000             # more hands per matchup
 *   node scripts/eval-bots.js --strategies tag,cfr     # specific matchup
 *   node --max-old-space-size=4096 scripts/eval-bots.js --strategies tag,cfr,random,fish,lag
 */

const { createGame, ACTION, PHASE } = require("../src/index");
const { getLegalActions } = require("../src/engine/betting");
const path = require("path");
const fs = require("fs");

// ── RNG ────────────────────────────────────────────────────────────────

function createRng(seed = 42) {
  let s = seed;
  return function () {
    s = (s * 1664525 + 1013904223) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

// ── Hand Strength (shared by strategies) ───────────────────────────────

function evaluateHandStrength(cards, board, phase) {
  if (!cards || cards.length < 2) return 0.5;
  const c1 = cards[0], c2 = cards[1];
  const r1 = c1.rank, r2 = c2.rank;
  const suited = c1.suit === c2.suit;
  const pair = r1 === r2;
  const highCard = Math.max(r1, r2);
  const gap = Math.abs(r1 - r2);

  let pf = 0;
  if (pair) { pf = 0.5 + (r1 / 14) * 0.5; }
  else {
    pf = (highCard / 14) * 0.4;
    if (suited) pf += 0.08;
    if (gap <= 1) pf += 0.06;
    if (gap <= 3) pf += 0.03;
    if (r1 >= 10 && r2 >= 10) pf += 0.15;
    if (highCard === 14) pf += 0.1;
  }
  if (phase === PHASE.PREFLOP) return Math.min(1, pf);

  const boardRanks = board.map(c => c.rank);
  const boardSuits = board.map(c => c.suit);
  let post = pf;
  if (boardRanks.includes(r1)) post += 0.25;
  if (boardRanks.includes(r2)) post += 0.20;
  if (boardRanks.includes(r1) && boardRanks.includes(r2) && !pair) post += 0.20;
  if (pair && boardRanks.includes(r1)) post += 0.35;
  const suitCount = boardSuits.filter(s => s === c1.suit).length;
  if (suitCount >= 2 && suited) post += 0.12;
  if (suitCount >= 3 && (c1.suit === boardSuits[0] || c2.suit === boardSuits[0])) post += 0.30;
  if (pair && boardRanks.length > 0 && r1 > Math.max(...boardRanks)) post += 0.15;
  return Math.min(1, post);
}

// ── Strategies ─────────────────────────────────────────────────────────

function randomStrategy(seat, legal, state, rng) {
  const { actions, minBet, minRaise } = legal;
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };
  const pick = actions[Math.floor(rng() * actions.length)];
  if (pick === ACTION.BET) return { action: pick, amount: minBet };
  if (pick === ACTION.RAISE) return { action: pick, amount: minRaise };
  return { action: pick };
}

function tagStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const phase = hand.phase;
  const potSize = hand.pot || 0;
  const stack = seatState.stack;
  const strength = evaluateHandStrength(cards, hand.board || [], phase);

  if (phase === PHASE.PREFLOP) {
    if (strength > 0.7 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
    }
    if (strength > 0.35 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (strength > 0.35 && actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }
  if (strength > 0.7) {
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.75), maxRaise)) };
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.min(Math.floor(potSize * 0.66), stack)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  if (strength > 0.35) {
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.5) return { action: ACTION.CALL };
    if (rng() < 0.15 && actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: minBet };
    return { action: ACTION.FOLD };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  if (rng() < 0.10 && actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: minBet };
  return { action: ACTION.FOLD };
}

// FISH: loose-passive, calls too much, rarely raises
function fishStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  // FISH calls almost everything, folds only trash
  if (strength > 0.8 && actions.includes(ACTION.RAISE)) {
    return { action: ACTION.RAISE, amount: minRaise };
  }
  if (strength > 0.15) {
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  }
  // Even trash gets called 30% of the time
  if (rng() < 0.3 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  return { action: ACTION.FOLD };
}

// LAG: loose-aggressive, plays many hands with aggression
function lagStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  // LAG raises wide, especially preflop
  if (hand.phase === PHASE.PREFLOP) {
    if (strength > 0.4 && actions.includes(ACTION.RAISE)) {
      const amt = Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.75), maxRaise));
      return { action: ACTION.RAISE, amount: amt };
    }
    if (strength > 0.2 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (rng() < 0.15 && actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: minRaise };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }
  // Postflop: bet/raise aggressively
  if (strength > 0.5) {
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize), maxRaise)) };
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.min(Math.floor(potSize * 0.75), seatState.stack)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  // Bluff 25% with weak hands
  if (rng() < 0.25) {
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.5)) };
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: minRaise };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.4) return { action: ACTION.CALL };
  return { action: ACTION.FOLD };
}

// NIT: ultra-tight, only plays premium hands
function nitStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  if (hand.phase === PHASE.PREFLOP) {
    if (strength > 0.8 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
    }
    if (strength > 0.6 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }
  // Postflop: only continue with strong hands
  if (strength > 0.7) {
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize * 0.5), maxRaise)) };
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.5)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  if (strength > 0.5) {
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.3) return { action: ACTION.CALL };
    return { action: ACTION.FOLD };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  return { action: ACTION.FOLD };
}

// CFR strategies (loaded on demand)
let _cfrStrategyFn = null;
function getCFRStrategy() {
  if (!_cfrStrategyFn) {
    const { createCFRStrategy } = require("./cfr/cfr-bot");
    _cfrStrategyFn = createCFRStrategy("./vision/models/cfr_strategy.json");
  }
  return _cfrStrategyFn;
}
function cfrStrategy(seat, legal, state, rng) {
  return getCFRStrategy()(seat, legal, state, rng);
}

let _cfr50StrategyFn = null;
function getCFR50Strategy() {
  if (!_cfr50StrategyFn) {
    const { createCFRStrategy } = require("./cfr/cfr-bot");
    _cfr50StrategyFn = createCFRStrategy("./vision/models/cfr_strategy_50bucket.json");
  }
  return _cfr50StrategyFn;
}
function cfr50Strategy(seat, legal, state, rng) {
  return getCFR50Strategy()(seat, legal, state, rng);
}

// Strategy registry
const STRATEGIES = {
  random: { name: "Random", fn: randomStrategy },
  tag:    { name: "TAG",    fn: tagStrategy },
  fish:   { name: "FISH",   fn: fishStrategy },
  lag:    { name: "LAG",    fn: lagStrategy },
  nit:    { name: "NIT",    fn: nitStrategy },
  cfr:    { name: "CFR-10", fn: cfrStrategy },
  cfr50:  { name: "CFR-50", fn: cfr50Strategy },
};

// ── Run a single table session ─────────────────────────────────────────

function runSession(strategyNames, numHands, seed, startStack) {
  const numSeats = strategyNames.length;
  const rng = createRng(seed);
  const strategies = strategyNames.map(s => STRATEGIES[s].fn);
  const names = strategyNames.map(s => STRATEGIES[s].name);

  const game = createGame(
    { tableId: "eval", tableName: "Eval", maxSeats: numSeats, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 50000 },
    { sessionId: `eval-${seed}`, logPath: null, rng }
  );

  for (let i = 0; i < numSeats; i++) {
    game.sitDown(i, `${names[i]}_s${i}`, startStack);
  }

  const results = names.map(name => ({
    name, handsPlayed: 0, profit: 0, wins: 0, vpip: 0, pfr: 0,
    perHandProfits: [],  // track each hand's profit for stdev/CI
  }));

  let handsCompleted = 0;
  let errors = 0;

  for (let h = 0; h < numHands; h++) {
    // Rebuy busted players
    try {
      const st = game.getState();
      for (let i = 0; i < numSeats; i++) {
        const s = st.table.seats[i];
        if (s && s.stack < 20) {
          game.leave(i);
          game.sitDown(i, `${names[i]}_s${i}`, startStack);
        }
      }
    } catch (e) {}

    try { game.startHand(); } catch (e) { errors++; continue; }

    const preState = game.getState();
    const preStacks = {};
    for (let i = 0; i < numSeats; i++) {
      const s = preState.table.seats[i];
      if (s) preStacks[i] = s.stack;
    }

    let actionCount = 0;
    while (!game.isHandComplete() && actionCount < 100) {
      const actionSeat = game.getActionSeat();
      if (actionSeat === null) break;
      const currentState = game.getState();
      const seatState = currentState.table.seats[actionSeat];
      if (!seatState || !seatState.inHand) break;
      const legal = getLegalActions(seatState, currentState.hand, currentState.table.bb);
      if (!legal.actions.length) break;

      const decision = strategies[actionSeat](actionSeat, legal, currentState, rng);
      if (!decision) break;

      try {
        game.act(actionSeat, decision.action, decision.amount);
      } catch (e) {
        try { game.act(actionSeat, ACTION.FOLD); } catch (_) {}
        errors++;
      }
      actionCount++;
    }

    handsCompleted++;
    const postState = game.getState();
    for (let i = 0; i < numSeats; i++) {
      const s = postState.table.seats[i];
      if (s && preStacks[i] !== undefined) {
        const profit = s.stack - preStacks[i];
        results[i].profit += profit;
        results[i].perHandProfits.push(profit);
        results[i].handsPlayed++;
        if (profit > 0) results[i].wins++;
      }
    }
  }

  return { results, handsCompleted, errors };
}

// ── Round-Robin Evaluation ─────────────────────────────────────────────

function runRoundRobin(strategyKeys, handsPerMatchup, startStack) {
  const BB = 10;
  const pairResults = {}; // "A vs B" -> { aProfit, bProfit, hands }

  // Run all strategies together at one 6-max table.
  // If fewer than 6 strategies, pad with TAG bots.
  // If more than 6, run multiple tables (not yet supported).
  // Rotate seat assignments across multiple sessions for position fairness.

  const numSeats = 6;
  const paddedKeys = strategyKeys.slice(0, numSeats);
  while (paddedKeys.length < numSeats) paddedKeys.push("tag"); // pad to 6

  // Per-strategy aggregate stats
  const aggStats = {};
  for (const k of strategyKeys) {
    aggStats[k] = { profit: 0, hands: 0, wins: 0, perHandProfits: [] };
  }

  // Run multiple rotations: shift seat assignments each time
  const numRotations = numSeats; // one rotation per seat position
  const handsPerRotation = Math.floor(handsPerMatchup / numRotations);

  console.log(`\nRunning ${numRotations} rotations x ${handsPerRotation} hands = ${numRotations * handsPerRotation} hands total...\n`);

  for (let rot = 0; rot < numRotations; rot++) {
    // Rotate seating: shift all strategies by 'rot' positions
    const seating = [];
    for (let i = 0; i < numSeats; i++) {
      seating.push(paddedKeys[(i + rot) % numSeats]);
    }

    const seed = (rot + 1) * 10000;
    const { results, handsCompleted, errors } = runSession(seating, handsPerRotation, seed, startStack);

    for (let i = 0; i < results.length; i++) {
      const stratKey = seating[i];
      if (aggStats[stratKey]) {
        aggStats[stratKey].profit += results[i].profit;
        aggStats[stratKey].hands += results[i].handsPlayed;
        aggStats[stratKey].wins += results[i].wins;
        aggStats[stratKey].perHandProfits.push(...results[i].perHandProfits);
      }
    }

    // Show progress
    const seatNames = seating.map(k => STRATEGIES[k].name);
    process.stdout.write(`  Rotation ${rot + 1}/${numRotations} [${seatNames.join(",")}]`);
    const rotResults = [];
    for (const k of strategyKeys) {
      const s = aggStats[k];
      const bb100 = s.hands > 0 ? (s.profit / BB) / (s.hands / 100) : 0;
      rotResults.push(`${STRATEGIES[k].name}:${bb100 >= 0 ? "+" : ""}${bb100.toFixed(0)}`);
    }
    console.log(` → ${rotResults.join(" | ")}`);
  }

  // Build pairwise from aggregate (for ELO)
  for (let i = 0; i < strategyKeys.length; i++) {
    for (let j = i + 1; j < strategyKeys.length; j++) {
      const a = strategyKeys[i], b = strategyKeys[j];
      const sa = aggStats[a], sb = aggStats[b];
      const aBB100 = sa.hands > 0 ? (sa.profit / BB) / (sa.hands / 100) : 0;
      const bBB100 = sb.hands > 0 ? (sb.profit / BB) / (sb.hands / 100) : 0;
      pairResults[`${a} vs ${b}`] = { a, b, aBB100, bBB100, aHands: sa.hands, bHands: sb.hands };
    }
  }

  return { pairResults, aggStats };
}

// ── ELO Calculation ────────────────────────────────────────────────────

function computeELO(pairResults, strategyKeys) {
  // Simple iterative ELO: start at 1500, update from pairwise results
  const elo = {};
  for (const k of strategyKeys) elo[k] = 1500;

  const K = 32;
  // Run 10 iterations to stabilize
  for (let iter = 0; iter < 10; iter++) {
    for (const key of Object.keys(pairResults)) {
      const { a, b, aBB100, bBB100 } = pairResults[key];
      // Convert bb/100 difference to win probability
      const diff = aBB100 - bBB100;
      const actualA = diff > 0 ? 1 : diff < 0 ? 0 : 0.5;

      const expectedA = 1 / (1 + Math.pow(10, (elo[b] - elo[a]) / 400));
      elo[a] += K * (actualA - expectedA);
      elo[b] += K * ((1 - actualA) - (1 - expectedA));
    }
  }
  return elo;
}

// ── Main ───────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const opts = {};
for (let i = 0; i < args.length; i += 2) {
  const key = args[i] ? args[i].replace("--", "") : "";
  const val = args[i + 1];
  if (key === "hands") opts.hands = parseInt(val);
  else if (key === "strategies") opts.strategies = val;
  else if (key === "stack") opts.stack = parseInt(val);
}

const handsPerMatchup = opts.hands || 20000;
const startStack = opts.stack || 1000;
const strategyKeys = opts.strategies
  ? opts.strategies.split(",").filter(k => STRATEGIES[k])
  : ["tag", "fish", "lag", "nit", "random"];

console.log("=" .repeat(60));
console.log("  BOT EVALUATION FRAMEWORK (Round-Robin)");
console.log("=".repeat(60));
console.log(`  Strategies: ${strategyKeys.map(k => STRATEGIES[k].name).join(", ")}`);
console.log(`  Hands/matchup: ${handsPerMatchup}`);
console.log(`  Starting stack: ${startStack} chips`);
console.log(`  Total matchups: ${strategyKeys.length * (strategyKeys.length - 1) / 2}`);

const BB = 10;
const { pairResults, aggStats } = runRoundRobin(strategyKeys, handsPerMatchup, startStack);

// ── Print Pairwise Results ─────────────────────────────────────────────

console.log("\n" + "=".repeat(60));
console.log("  PAIRWISE RESULTS");
console.log("=".repeat(60));
for (const [key, r] of Object.entries(pairResults)) {
  const aName = STRATEGIES[r.a].name;
  const bName = STRATEGIES[r.b].name;
  console.log(`  ${aName} vs ${bName}: ${r.aBB100 >= 0 ? "+" : ""}${r.aBB100.toFixed(1)} vs ${r.bBB100 >= 0 ? "+" : ""}${r.bBB100.toFixed(1)} bb/100 (${r.aHands + r.bHands} hands)`);
}

// ── Print Aggregate Rankings ───────────────────────────────────────────

console.log("\n" + "=".repeat(60));
console.log("  AGGREGATE RANKINGS");
console.log("=".repeat(60));

const rankings = strategyKeys.map(k => {
  const s = aggStats[k];
  const bb100 = s.hands > 0 ? (s.profit / BB) / (s.hands / 100) : 0;
  const mbbHand = bb100 / 10; // mbb/hand = bb/100 / 10
  const winPct = s.hands > 0 ? (s.wins / s.hands * 100) : 0;

  // Standard deviation and 95% confidence interval
  let stdev = 0, ci95 = 0;
  if (s.perHandProfits.length > 1) {
    const profitsBB = s.perHandProfits.map(p => p / BB); // convert to BB
    const mean = profitsBB.reduce((a, b) => a + b, 0) / profitsBB.length;
    const variance = profitsBB.reduce((a, b) => a + (b - mean) ** 2, 0) / (profitsBB.length - 1);
    stdev = Math.sqrt(variance);
    const se = stdev / Math.sqrt(profitsBB.length); // standard error
    ci95 = se * 1.96; // 95% CI half-width in BB/hand
  }

  return { key: k, name: STRATEGIES[k].name, bb100, mbbHand, winPct, hands: s.hands, stdev, ci95: ci95 * 100 }; // ci95 in bb/100
}).sort((a, b) => b.bb100 - a.bb100);

// ELO
const elo = computeELO(pairResults, strategyKeys);

console.log(`\n  ${"Rank".padEnd(5)} ${"Strategy".padEnd(10)} ${"bb/100".padStart(12)} ${"mbb/h".padStart(8)} ${"95% CI".padStart(12)} ${"Win%".padStart(8)} ${"ELO".padStart(7)} ${"Hands".padStart(8)}`);
console.log("  " + "-".repeat(75));
for (let i = 0; i < rankings.length; i++) {
  const r = rankings[i];
  const eloVal = Math.round(elo[r.key]);
  const bb100Str = (r.bb100 >= 0 ? "+" : "") + r.bb100.toFixed(1);
  const mbbStr = (r.mbbHand >= 0 ? "+" : "") + r.mbbHand.toFixed(1);
  const ciStr = `±${r.ci95.toFixed(1)}`;
  console.log(`  ${String(i + 1).padEnd(5)} ${r.name.padEnd(10)} ${bb100Str.padStart(12)} ${mbbStr.padStart(8)} ${ciStr.padStart(12)} ${r.winPct.toFixed(1).padStart(7)}% ${String(eloVal).padStart(7)} ${String(r.hands).padStart(8)}`);
}

console.log("\n" + "=".repeat(60));

// Save results to JSON
const outPath = path.join(__dirname, "..", "vision", "data", "eval_results.json");
const outData = {
  timestamp: new Date().toISOString(),
  config: { handsPerMatchup, startStack, strategies: strategyKeys },
  pairwise: pairResults,
  rankings: rankings.map(r => ({ ...r, elo: Math.round(elo[r.key]), perHandProfits: undefined })),
};
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(outData, null, 2));
console.log(`  Results saved to ${outPath}`);
