#!/usr/bin/env node
"use strict";

/**
 * Self-play with neural net bot vs TAG bot (or neural vs neural).
 *
 * Requires the inference server running at localhost:9200.
 * Start it with: python vision/inference_server.py
 *
 * Usage:
 *   node scripts/self-play-nn.js                          # neural vs TAG, 10k hands
 *   node scripts/self-play-nn.js --hands 50000
 *   node scripts/self-play-nn.js --mode nn-vs-nn          # neural vs neural
 *   node scripts/self-play-nn.js --mode tag-vs-tag        # baseline
 *   node scripts/self-play-nn.js --greedy                 # use argmax instead of sampling
 *   node scripts/self-play-nn.js --port 9200
 */

const { createGame, ACTION, PHASE } = require("../src/index");
const { getLegalActions } = require("../src/engine/betting");
const http = require("http");

// ── Card Encoding ──────────────────────────────────────────────────────

function encodeCard(card) {
  if (!card) return 52;
  return (card.rank - 2) * 4 + (card.suit - 1);
}

// ── TAG Strategy (copied from self-play.js for standalone use) ─────────

function evaluateHandStrength(cards, board, phase) {
  if (!cards || cards.length < 2) return 0.5;
  const c1 = cards[0], c2 = cards[1];
  const r1 = c1.rank, r2 = c2.rank;
  const suited = c1.suit === c2.suit;
  const pair = r1 === r2;
  const highCard = Math.max(r1, r2);
  const gap = Math.abs(r1 - r2);
  const connected = gap === 1;
  let pfStrength = 0;
  if (pair) { pfStrength = 0.5 + (r1 / 14) * 0.5; }
  else {
    pfStrength = (highCard / 14) * 0.4;
    if (suited) pfStrength += 0.08;
    if (connected) pfStrength += 0.06;
    if (gap <= 3) pfStrength += 0.03;
    if (r1 >= 10 && r2 >= 10) pfStrength += 0.15;
    if (highCard === 14) pfStrength += 0.1;
  }
  if (phase === PHASE.PREFLOP) return Math.min(1, pfStrength);
  const boardRanks = board.map(c => c.rank);
  const boardSuits = board.map(c => c.suit);
  let postStrength = pfStrength;
  if (boardRanks.includes(r1)) postStrength += 0.25;
  if (boardRanks.includes(r2)) postStrength += 0.20;
  if (boardRanks.includes(r1) && boardRanks.includes(r2) && !pair) postStrength += 0.20;
  if (pair && boardRanks.includes(r1)) postStrength += 0.35;
  const suitCount = boardSuits.filter(s => s === c1.suit).length;
  if (suitCount >= 2 && suited) postStrength += 0.12;
  if (suitCount >= 3 && (c1.suit === boardSuits[0] || c2.suit === boardSuits[0])) postStrength += 0.30;
  if (pair && r1 > Math.max(...boardRanks)) postStrength += 0.15;
  return Math.min(1, postStrength);
}

function tagStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (actions.length === 0) return null;
  if (actions.length === 1) return { action: actions[0] };
  const cards = seatState.holeCards || [];
  const phase = hand.phase;
  const potSize = hand.pot || 0;
  const stack = seatState.stack;
  let strength = evaluateHandStrength(cards, hand.board || [], phase);

  if (phase === PHASE.PREFLOP) {
    if (strength > 0.7 && actions.includes(ACTION.RAISE)) {
      const raiseAmt = Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise);
      return { action: ACTION.RAISE, amount: Math.max(minRaise, raiseAmt) };
    }
    if (strength > 0.35 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (strength > 0.35 && actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }
  if (strength > 0.7) {
    if (actions.includes(ACTION.RAISE)) {
      const raiseAmt = Math.min(minRaise + Math.floor(potSize * 0.75), maxRaise);
      return { action: ACTION.RAISE, amount: Math.max(minRaise, raiseAmt) };
    }
    if (actions.includes(ACTION.BET)) {
      const betAmt = Math.min(Math.floor(potSize * 0.66), stack, Math.max(minBet, 2));
      return { action: ACTION.BET, amount: Math.max(minBet, betAmt) };
    }
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

// ── Feature Extraction ─────────────────────────────────────────────────

const PHASE_MAP = { PREFLOP: 0, FLOP: 1, TURN: 2, RIVER: 3 };
const BB = 10;

function extractFeatures(seat, legal, state) {
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  const cards = seatState.holeCards || [];
  const board = hand.board || [];
  const heroCard1 = cards.length >= 1 ? encodeCard(cards[0]) : 52;
  const heroCard2 = cards.length >= 2 ? encodeCard(cards[1]) : 52;
  const boardCards = [];
  for (let i = 0; i < 5; i++) {
    boardCards.push(i < board.length ? encodeCard(board[i]) : 52);
  }
  const bb100 = BB * 100;
  const potNorm = (hand.pot || 0) / bb100;
  const stackNorm = (seatState.stack || 0) / bb100;
  const callNorm = (legal.callAmount || 0) / bb100;
  const potOdds = (hand.pot > 0 && legal.callAmount > 0)
    ? legal.callAmount / (hand.pot + legal.callAmount) : 0;
  let numOpponents = 0;
  for (const key of Object.keys(state.table.seats)) {
    const s = state.table.seats[key];
    if (s && s.inHand && !s.folded && parseInt(key) !== seat) numOpponents++;
  }
  const streetIdx = PHASE_MAP[hand.phase] || 0;
  const streetOneHot = [0, 0, 0, 0];
  streetOneHot[streetIdx] = 1;
  const maxSeats = state.table.maxSeats || 6;
  const posNorm = seat / (maxSeats - 1);
  const handStrength = evaluateHandStrength(cards, board, hand.phase);
  const betToPot = (hand.pot > 0 && legal.callAmount > 0)
    ? Math.min(legal.callAmount / hand.pot, 3) : 0;
  const spr = hand.pot > 0 ? (seatState.stack || 0) / hand.pot : 10;
  const sprNorm = Math.min(spr / 20, 1);
  return { heroCard1, heroCard2, boardCards, potNorm, stackNorm, callNorm,
           potOdds, numOpponents, streetOneHot, posNorm, handStrength, betToPot, sprNorm };
}

// ── Neural Strategy (sync HTTP) ────────────────────────────────────────

function createNeuralStrategy(port, greedy) {
  const endpoint = greedy ? "predict_greedy" : "predict";

  return function neuralStrategy(seat, legal, state, rng) {
    const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
    if (actions.length === 0) return null;
    if (actions.length === 1) return { action: actions[0] };

    const features = extractFeatures(seat, legal, state);
    try {
      const { execSync } = require("child_process");
      const payload = JSON.stringify({
        features,
        legal_actions: actions,
        min_bet: minBet,
        min_raise: minRaise,
        max_raise: maxRaise,
        call_amount: callAmount,
      });
      // Write payload to temp file to avoid shell escaping issues
      const fs = require("fs");
      const os = require("os");
      const path = require("path");
      const tmpFile = path.join(os.tmpdir(), `nn_payload_${seat}.json`);
      fs.writeFileSync(tmpFile, payload);
      const result = execSync(
        `curl -s -X POST http://localhost:${port}/${endpoint} -H "Content-Type: application/json" -d @${tmpFile}`,
        { timeout: 5000, encoding: "utf-8" }
      );
      const response = JSON.parse(result);
      return { action: response.action, amount: response.amount || 0 };
    } catch (e) {
      // Fallback to TAG
      return tagStrategy(seat, legal, state, rng);
    }
  };
}

// ── RNG ────────────────────────────────────────────────────────────────

function createRng(seed = 42) {
  let s = seed;
  return function () {
    s = (s * 1664525 + 1013904223) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

// ── Self-Play Runner ───────────────────────────────────────────────────

function runSelfPlay(opts = {}) {
  const numHands = opts.hands || 10000;
  const numSeats = opts.seats || 2;  // heads-up for cleaner measurement
  const startStack = opts.stack || 1000;
  const mode = opts.mode || "nn-vs-tag"; // nn-vs-tag, nn-vs-nn, tag-vs-tag
  const nnSeats = opts.nnSeats || 1; // how many neural bot seats (for nn-vs-tag)
  const seed = opts.seed || 42;
  const port = opts.port || 9200;
  const greedy = opts.greedy || false;

  const rng = createRng(seed);
  const strategies = [];
  const botNames = [];

  for (let i = 0; i < numSeats; i++) {
    if (mode === "nn-vs-tag") {
      if (i < nnSeats) {
        botNames.push(nnSeats > 1 ? `Neural_${i+1}` : "NeuralBot");
        strategies.push(createNeuralStrategy(port, greedy));
      } else {
        botNames.push(`TAG_${i}`);
        strategies.push(tagStrategy);
      }
    } else if (mode === "nn-vs-nn") {
      botNames.push(`Neural_${i}`);
      strategies.push(createNeuralStrategy(port, greedy));
    } else {
      botNames.push(`TAG_${i}`);
      strategies.push(tagStrategy);
    }
  }

  const game = createGame(
    {
      tableId: "nn-selfplay",
      tableName: "NN-SelfPlay",
      maxSeats: numSeats,
      sb: 5,
      bb: 10,
      minBuyIn: 100,
      maxBuyIn: 50000,
    },
    { sessionId: `nn-${seed}`, logPath: null, rng }
  );

  for (let i = 0; i < numSeats; i++) {
    game.sitDown(i, botNames[i], startStack);
  }

  const results = botNames.map(name => ({
    name, handsPlayed: 0, profit: 0, wins: 0,
  }));

  const startTime = Date.now();
  let handsCompleted = 0;
  let errors = 0;

  for (let h = 0; h < numHands; h++) {
    const state = game.getState();
    for (let i = 0; i < numSeats; i++) {
      const s = state.table.seats[i];
      if (s && s.stack < 20) {
        try { game.leave(i); game.sitDown(i, botNames[i], startStack); } catch (e) {}
      }
    }

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
      const strategy = strategies[actionSeat];
      const decision = strategy(actionSeat, legal, currentState, rng);
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
        results[i].handsPlayed++;
        if (profit > 0) results[i].wins++;
      }
    }

    if ((h + 1) % 500 === 0 || h === numHands - 1) {
      const elapsed = (Date.now() - startTime) / 1000;
      const hps = handsCompleted / elapsed;
      process.stdout.write(`\r  ${handsCompleted}/${numHands} hands (${Math.round(hps)} hands/sec)`);
    }
  }

  const totalElapsed = (Date.now() - startTime) / 1000;

  console.log("\n");
  console.log("=".repeat(60));
  console.log("NEURAL NET SELF-PLAY RESULTS");
  console.log("=".repeat(60));
  console.log(`Hands: ${handsCompleted} | Time: ${totalElapsed.toFixed(1)}s | Speed: ${Math.round(handsCompleted / totalElapsed)} hands/sec`);
  console.log(`Mode: ${mode} | Seats: ${numSeats} | Errors: ${errors}`);
  console.log("-".repeat(60));

  for (const r of results) {
    const bb100 = r.handsPlayed ? ((r.profit / 10) / (r.handsPlayed / 100)).toFixed(1) : "0";
    console.log(`  ${r.name}:`);
    console.log(`    Profit: ${r.profit > 0 ? "+" : ""}${r.profit} chips (${bb100} bb/100)`);
    console.log(`    Win rate: ${r.wins}/${r.handsPlayed} (${((r.wins / r.handsPlayed) * 100).toFixed(1)}%)`);
  }

  console.log("=".repeat(60));

  // Return structured results for the orchestrator
  return {
    handsCompleted,
    elapsed: totalElapsed,
    errors,
    players: results.map(r => ({
      name: r.name,
      profit: r.profit,
      bb100: r.handsPlayed ? (r.profit / 10) / (r.handsPlayed / 100) : 0,
      winRate: r.handsPlayed ? r.wins / r.handsPlayed : 0,
    })),
  };
}

// ── CLI ────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const opts = {};
for (let i = 0; i < args.length; i += 2) {
  const key = args[i].replace("--", "");
  const val = args[i + 1];
  if (key === "hands") opts.hands = parseInt(val);
  else if (key === "seats") opts.seats = parseInt(val);
  else if (key === "stack") opts.stack = parseInt(val);
  else if (key === "mode") opts.mode = val;
  else if (key === "seed") opts.seed = parseInt(val);
  else if (key === "port") opts.port = parseInt(val);
  else if (key === "nn-seats") opts.nnSeats = parseInt(val);
  else if (key === "greedy") { opts.greedy = true; i--; }
}

console.log("Neural Net Self-Play");
console.log("=".repeat(60));
console.log(`Config: ${opts.hands || 10000} hands, ${opts.seats || 2} seats, mode=${opts.mode || "nn-vs-tag"}`);
console.log();

const results = runSelfPlay(opts);

// Output JSON for orchestrator to parse
if (process.env.STRUCTURED_OUTPUT) {
  console.log("\n__RESULTS_JSON__");
  console.log(JSON.stringify(results));
}
