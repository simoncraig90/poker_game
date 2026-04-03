#!/usr/bin/env node
"use strict";

/**
 * Generate RL training data from self-play.
 *
 * Runs the engine with TAG (or neural) strategy and records every decision
 * point along with the eventual hand outcome (profit/loss).
 *
 * Output: vision/data/rl_training_data.jsonl
 * Each line = one decision point:
 *   { state: {...features}, action: {type, amount}, reward: chipProfit }
 *
 * Usage:
 *   node scripts/generate-rl-data.js                       # 100k hands, TAG
 *   node scripts/generate-rl-data.js --hands 50000
 *   node scripts/generate-rl-data.js --strategy cfr        # use CFR strategy (recommended)
 *   node scripts/generate-rl-data.js --strategy neural     # use inference server
 *   node scripts/generate-rl-data.js --append              # append to existing file
 */

const { createGame, ACTION, PHASE } = require("../src/index");
const { getLegalActions } = require("../src/engine/betting");
const { evaluateHandStrength: cfrEvalStrength, strengthToBucket, encodeAction: encodeCFRAction } = require("./cfr/abstraction");
const fs = require("fs");
const path = require("path");
const http = require("http");

// ── CFR Strategy ──────────────────────────────────────────────────────

const CFR_NUM_BUCKETS = 10;
const CFR_POS_NAMES = ["BTN", "SB", "BB", "UTG", "MP", "CO"];

function createCFRSixmaxStrategy(strategyPath) {
  const fullPath = path.resolve(strategyPath);
  if (!fs.existsSync(fullPath)) {
    throw new Error(`CFR strategy file not found: ${fullPath}`);
  }
  const strategyTable = JSON.parse(fs.readFileSync(fullPath, "utf8"));
  console.log(`[CFR] Loaded 6-max strategy with ${Object.keys(strategyTable).length} info sets`);

  return function cfrSixmaxStrategy(seat, legal, state, rng) {
    const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
    if (actions.length === 0) return null;
    if (actions.length === 1) return { action: actions[0] };

    const hand = state.hand;
    const seatState = state.table.seats[seat];
    const cards = seatState.holeCards || [];
    const board = hand.board || [];
    const phase = hand.phase;

    // Hand strength and bucket (10 buckets for 6-max)
    const strength = cfrEvalStrength(cards, board, phase);
    const bucket = strengthToBucket(strength, CFR_NUM_BUCKETS);

    // Stack bucket
    const bb = state.table.bb || 10;
    const bbs = (seatState.stack || 0) / bb;
    const stackBucket = bbs < 30 ? 0 : bbs < 80 ? 1 : 2;

    // Position: map seat relative to dealer
    const dealer = hand.dealer ?? 0;
    const numSeats = Object.keys(state.table.seats).length;
    const relPos = ((seat - dealer) % numSeats + numSeats) % numSeats;
    const pos = CFR_POS_NAMES[relPos] || "UTG";

    // Count active players
    let numActive = 0;
    for (const k of Object.keys(state.table.seats)) {
      const s = state.table.seats[k];
      if (s && s.inHand && !s.folded) numActive++;
    }

    // Build action history from hand.actions
    // Map engine actions to CFR compact notation, separated by street
    const streetActions = { PREFLOP: "", FLOP: "", TURN: "", RIVER: "" };
    let currentStreet = "PREFLOP";
    if (hand.actions) {
      for (const a of hand.actions) {
        if (a.type === "BLIND_SB" || a.type === "BLIND_BB") continue;
        if (a.type === "DEAL_FLOP") { currentStreet = "FLOP"; continue; }
        if (a.type === "DEAL_TURN") { currentStreet = "TURN"; continue; }
        if (a.type === "DEAL_RIVER") { currentStreet = "RIVER"; continue; }
        // Include ALL player actions (not just opponents) for the history
        if (a.type === "FOLD") streetActions[currentStreet] += "f";
        else if (a.type === "CHECK") streetActions[currentStreet] += "k";
        else if (a.type === "CALL") streetActions[currentStreet] += "c";
        else if (a.type === "RAISE" || a.type === "BET") {
          // Map to CFR sizing: check if it's closer to half-pot or all-in
          const potSize = hand.pot || 1;
          const amount = a.amount || 0;
          if (amount >= seatState.stack * 0.9) {
            streetActions[currentStreet] += (a.type === "BET" ? "ba" : "ra");
          } else {
            streetActions[currentStreet] += (a.type === "BET" ? "bh" : "rh");
          }
        }
      }
    }

    // Build full history with street separators
    const parts = [];
    const streets = ["PREFLOP", "FLOP", "TURN", "RIVER"];
    for (const st of streets) {
      if (streetActions[st]) parts.push(streetActions[st]);
      if (st === phase) break;
    }
    const actionHistory = parts.join("-");

    // Try progressively simpler keys until we find a match
    const key6 = `${phase}:${bucket}:s${stackBucket}:${pos}:${numActive}p:${actionHistory}`;
    const keyNoHistory = `${phase}:${bucket}:s${stackBucket}:${pos}:${numActive}p:`;
    const keySimple = `${phase}:${bucket}:s${stackBucket}:${pos}:${numActive}p`;
    const strategy = strategyTable[key6] || strategyTable[keyNoHistory] || strategyTable[keySimple];

    if (!strategy) {
      // Fallback to TAG if no CFR match
      return tagStrategy(seat, legal, state, rng);
    }

    // Extract action probabilities for legal engine actions
    const actionProbs = [];
    for (const engineAction of actions) {
      let prob = 0;
      if (engineAction === ACTION.FOLD) prob = strategy["FOLD"] || 0;
      else if (engineAction === ACTION.CHECK) prob = strategy["CHECK"] || 0;
      else if (engineAction === ACTION.CALL) prob = strategy["CALL"] || 0;
      else if (engineAction === ACTION.BET) prob = (strategy["BET_HALF"] || 0) + (strategy["BET_POT"] || 0) + (strategy["BET_ALLIN"] || 0);
      else if (engineAction === ACTION.RAISE) prob = (strategy["RAISE_HALF"] || 0) + (strategy["RAISE_POT"] || 0) + (strategy["RAISE_ALLIN"] || 0);
      actionProbs.push({ action: engineAction, prob });
    }

    const totalProb = actionProbs.reduce((sum, ap) => sum + ap.prob, 0);
    if (totalProb <= 0) {
      return tagStrategy(seat, legal, state, rng);
    }

    // Sample from CFR distribution
    const r = rng();
    let cumulative = 0;
    let chosen = actionProbs[actionProbs.length - 1].action;
    for (const ap of actionProbs) {
      cumulative += ap.prob / totalProb;
      if (r < cumulative) {
        chosen = ap.action;
        break;
      }
    }

    // Determine bet/raise sizing from CFR sub-action probabilities
    if (chosen === ACTION.BET) {
      const potSize = hand.pot || 1;
      const halfP = strategy["BET_HALF"] || 0;
      const potP = strategy["BET_POT"] || 0;
      const allP = strategy["BET_ALLIN"] || 0;
      const total = halfP + potP + allP;
      const r2 = rng() * total;
      let betAmount;
      if (r2 < halfP) betAmount = Math.round(potSize * 0.5);
      else if (r2 < halfP + potP) betAmount = Math.round(potSize);
      else betAmount = seatState.stack;
      return { action: chosen, amount: Math.max(minBet, Math.min(betAmount, seatState.stack)) };
    }
    if (chosen === ACTION.RAISE) {
      const potSize = hand.pot || 1;
      const halfP = strategy["RAISE_HALF"] || 0;
      const potP = strategy["RAISE_POT"] || 0;
      const allP = strategy["RAISE_ALLIN"] || 0;
      const total = halfP + potP + allP;
      const r2 = rng() * total;
      let raiseAmount;
      if (r2 < halfP) raiseAmount = callAmount + Math.round(potSize * 0.5);
      else if (r2 < halfP + potP) raiseAmount = callAmount + Math.round(potSize);
      else raiseAmount = seatState.stack;
      return { action: chosen, amount: Math.max(minRaise, Math.min(raiseAmount, maxRaise)) };
    }

    return { action: chosen };
  };
}

// ── Card Encoding ──────────────────────────────────────────────────────

function encodeCard(card) {
  if (!card) return 52; // empty slot
  return (card.rank - 2) * 4 + (card.suit - 1);
}

// ── Strategies ─────────────────────────────────────────────────────────

// Reuse TAG from self-play.js
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
  if (pair) {
    pfStrength = 0.5 + (r1 / 14) * 0.5;
  } else {
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

  // Small epsilon for exploration (5%) — just enough to see all actions
  // but not so much that garbage data poisons the reward signal
  const epsilon = 0.05;
  if (rng() < epsilon) {
    const idx = Math.floor(rng() * actions.length) % actions.length;
    const a = actions[idx];
    let amount = null;
    if (a === ACTION.BET) {
      const frac = 0.5 + rng() * 0.5;
      amount = Math.max(minBet, Math.min(Math.floor(potSize * frac), stack));
    } else if (a === ACTION.RAISE) {
      const frac = rng();
      amount = Math.max(minRaise, Math.min(Math.floor(minRaise + frac * potSize), maxRaise));
    }
    return { action: a, amount };
  }

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

// Neural strategy: calls inference server at localhost:9200
function createNeuralStrategy(port = 9200) {
  // Synchronous HTTP is not great but works for data generation speed
  // We'll use a buffer approach: batch decisions
  return function neuralStrategy(seat, legal, state, rng) {
    const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
    if (actions.length === 0) return null;
    if (actions.length === 1) return { action: actions[0] };

    // Build feature vector for the server
    const features = extractFeatures(seat, legal, state);

    // Synchronous HTTP request (using execSync as workaround)
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
      const result = execSync(
        `curl -s -X POST http://localhost:${port}/predict -H "Content-Type: application/json" -d '${payload.replace(/'/g, "'\\''")}'`,
        { timeout: 5000, encoding: "utf-8" }
      );
      const response = JSON.parse(result);
      return { action: response.action, amount: response.amount };
    } catch (e) {
      // Fallback to TAG if server is down
      return tagStrategy(seat, legal, state, rng);
    }
  };
}

// ── Feature Extraction ─────────────────────────────────────────────────

const PHASE_MAP = { PREFLOP: 0, FLOP: 1, TURN: 2, RIVER: 3 };
const ACTION_MAP = { FOLD: 0, CHECK: 1, CALL: 2, BET: 3, RAISE: 4 };
const BB = 10; // big blind size

function extractFeatures(seat, legal, state) {
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  const cards = seatState.holeCards || [];
  const board = hand.board || [];

  // Card encodings
  const heroCard1 = cards.length >= 1 ? encodeCard(cards[0]) : 52;
  const heroCard2 = cards.length >= 2 ? encodeCard(cards[1]) : 52;
  const boardCards = [];
  for (let i = 0; i < 5; i++) {
    boardCards.push(i < board.length ? encodeCard(board[i]) : 52);
  }

  // Numeric features (normalized by 100bb = 1000 chips at 5/10)
  const bb100 = BB * 100; // 1000
  const potNorm = (hand.pot || 0) / bb100;
  const stackNorm = (seatState.stack || 0) / bb100;
  const callNorm = (legal.callAmount || 0) / bb100;
  const potOdds = (hand.pot > 0 && legal.callAmount > 0)
    ? legal.callAmount / (hand.pot + legal.callAmount)
    : 0;

  // Count opponents still in hand
  let numOpponents = 0;
  for (const key of Object.keys(state.table.seats)) {
    const s = state.table.seats[key];
    if (s && s.inHand && !s.folded && parseInt(key) !== seat) {
      numOpponents++;
    }
  }

  // Street one-hot
  const streetIdx = PHASE_MAP[hand.phase] || 0;
  const streetOneHot = [0, 0, 0, 0];
  streetOneHot[streetIdx] = 1;

  // Position: 0 = first to act, 1 = last to act (button)
  const maxSeats = state.table.maxSeats || 6;
  const posNorm = seat / (maxSeats - 1);

  // Hand strength heuristic (0-1) — quick inline eval
  const handStrength = evaluateHandStrength(cards, board, hand.phase);

  // Bet-to-pot ratio (how big is the facing bet relative to pot)
  const betToPot = (hand.pot > 0 && legal.callAmount > 0)
    ? legal.callAmount / hand.pot
    : 0;

  // Stack-to-pot ratio (SPR — key decision factor)
  const spr = (hand.pot > 0) ? (seatState.stack || 0) / hand.pot : 10;
  const sprNorm = Math.min(spr / 20, 1); // normalize, cap at 20

  return {
    heroCard1,
    heroCard2,
    boardCards,
    potNorm,
    stackNorm,
    callNorm,
    potOdds,
    numOpponents,
    streetOneHot,
    posNorm,
    handStrength,
    betToPot: Math.min(betToPot, 3), // cap at 3x pot
    sprNorm,
  };
}

function encodeAction(decision) {
  const actionIdx = ACTION_MAP[decision.action] || 0;
  const amount = decision.amount || 0;
  return { actionIdx, amount };
}

// ── RNG ────────────────────────────────────────────────────────────────

function createRng(seed = 42) {
  let s = seed;
  return function () {
    s = (s * 1664525 + 1013904223) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

// ── Main Loop ──────────────────────────────────────────────────────────

function generateData(opts = {}) {
  const numHands = opts.hands || 100000;
  const numSeats = opts.seats || 6;
  const startStack = opts.stack || 1000;
  const strategyName = opts.strategy || "tag";
  const seed = opts.seed || 42;
  const appendMode = opts.append || false;

  const outDir = path.join(__dirname, "..", "vision", "data");
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, "rl_training_data.jsonl");

  const flags = appendMode ? "a" : "w";
  const outStream = fs.createWriteStream(outPath, { flags });

  const rng = createRng(seed);
  const strategies = [];
  const botNames = [];

  // Load CFR strategy if needed (once, shared by all bots)
  let cfrStrategy = null;
  if (strategyName === "cfr") {
    const cfrPath = path.join(__dirname, "..", "vision", "models", "cfr_strategy.json");
    cfrStrategy = createCFRSixmaxStrategy(cfrPath);
  }

  for (let i = 0; i < numSeats; i++) {
    botNames.push(`Bot${i + 1}`);
    if (strategyName === "cfr") {
      strategies.push(cfrStrategy);
    } else if (strategyName === "neural") {
      strategies.push(createNeuralStrategy(opts.port || 9200));
    } else {
      strategies.push(tagStrategy);
    }
  }

  const game = createGame(
    {
      tableId: "rl-data",
      tableName: "RL-Data",
      maxSeats: numSeats,
      sb: 5,
      bb: 10,
      minBuyIn: 100,
      maxBuyIn: 50000,
    },
    { sessionId: `rldata-${seed}`, logPath: null, rng }
  );

  for (let i = 0; i < numSeats; i++) {
    game.sitDown(i, botNames[i], startStack);
  }

  const startTime = Date.now();
  let handsCompleted = 0;
  let decisionsRecorded = 0;
  let errors = 0;

  for (let h = 0; h < numHands; h++) {
    // Rebuy busted players (only possible between hands)
    try {
      const state = game.getState();
      for (let i = 0; i < numSeats; i++) {
        const s = state.table.seats[i];
        if (s && s.stack < 20) {
          game.leave(i);
          game.sitDown(i, botNames[i], startStack);
        }
      }
    } catch (e) { /* rebuy failures between hands are benign */ }

    // Start hand
    try {
      game.startHand();
    } catch (e) {
      errors++;
      continue;
    }

    // Record starting stacks
    const preState = game.getState();
    const preStacks = {};
    for (let i = 0; i < numSeats; i++) {
      const s = preState.table.seats[i];
      if (s) preStacks[i] = s.stack;
    }

    // Collect decision points for this hand
    const handDecisions = []; // { seat, features, action, amount }

    let actionCount = 0;
    const maxActions = 100;

    while (!game.isHandComplete() && actionCount < maxActions) {
      const actionSeat = game.getActionSeat();
      if (actionSeat === null) break;

      const currentState = game.getState();
      const seatState = currentState.table.seats[actionSeat];
      if (!seatState || !seatState.inHand) break;

      const legal = getLegalActions(seatState, currentState.hand, currentState.table.bb);
      if (!legal.actions.length) break;

      // Extract features BEFORE acting
      const features = extractFeatures(actionSeat, legal, currentState);

      const strategy = strategies[actionSeat];
      const decision = strategy(actionSeat, legal, currentState, rng);
      if (!decision) break;

      // Record decision
      const encodedAction = encodeAction(decision);
      handDecisions.push({
        seat: actionSeat,
        features,
        actionIdx: encodedAction.actionIdx,
        amount: encodedAction.amount,
        legalActions: legal.actions.map(a => ACTION_MAP[a] || 0),
        minBet: legal.minBet,
        minRaise: legal.minRaise,
        maxRaise: legal.maxRaise,
        callAmount: legal.callAmount,
      });

      try {
        game.act(actionSeat, decision.action, decision.amount);
      } catch (e) {
        try { game.act(actionSeat, ACTION.FOLD); } catch (_) {}
        errors++;
      }

      actionCount++;
    }

    handsCompleted++;

    // Calculate profit for each seat
    const postState = game.getState();
    const profits = {};
    for (let i = 0; i < numSeats; i++) {
      const s = postState.table.seats[i];
      if (s && preStacks[i] !== undefined) {
        profits[i] = s.stack - preStacks[i];
      }
    }

    // Write decision points with shaped rewards
    for (let di = 0; di < handDecisions.length; di++) {
      const d = handDecisions[di];
      const handProfit = profits[d.seat] || 0;

      // Find how many more decisions this seat made after this one
      let futureSteps = 0;
      for (let dj = di + 1; dj < handDecisions.length; dj++) {
        if (handDecisions[dj].seat === d.seat) futureSteps++;
      }

      // Shaped reward = discounted outcome + immediate action signal
      const gamma = 0.95;
      const outcomeReward = handProfit * Math.pow(gamma, futureSteps);

      // Immediate signal based on action quality relative to hand strength
      const strength = d.features.handStrength || 0.5;
      const actionIdx = d.actionIdx;
      let shaping = 0;
      if (actionIdx === 0) {
        // FOLD: good if weak, bad if strong
        shaping = (strength < 0.35) ? 0.5 : -strength * 2;
      } else if (actionIdx === 2) {
        // CALL: penalize calling with weak hands (anti calling-station signal)
        // Slightly reward calling with medium-strong hands
        if (strength < 0.35) shaping = -1.0;       // calling station penalty
        else if (strength < 0.5) shaping = -0.3;    // marginal call, slight penalty
        else shaping = 0.2;                          // good call
      } else if (actionIdx === 3 || actionIdx === 4) {
        // BET/RAISE: reward with strong hands, penalize bluffs
        if (strength > 0.6) shaping = strength * 1.5;  // value bet/raise
        else if (strength > 0.4) shaping = 0.1;         // semi-bluff OK
        else shaping = -0.5;                             // bad bluff
      }
      // Scale shaping relative to pot size
      const potBB = (d.features.potNorm || 0) * 100;
      shaping *= Math.min(potBB / 10, 1.0) * 0.5;

      const reward = outcomeReward + shaping * BB;
      const rewardNorm = reward / BB; // normalize by big blind
      const record = {
        s: d.features,
        a: d.actionIdx,
        amt: d.amount,
        r: rewardNorm,
        legal: d.legalActions,
        minBet: d.minBet,
        minRaise: d.minRaise,
        maxRaise: d.maxRaise,
        callAmt: d.callAmount,
      };
      outStream.write(JSON.stringify(record) + "\n");
      decisionsRecorded++;
    }

    // Progress
    if ((h + 1) % 5000 === 0 || h === numHands - 1) {
      const elapsed = (Date.now() - startTime) / 1000;
      const hps = handsCompleted / elapsed;
      process.stdout.write(
        `\r  ${handsCompleted}/${numHands} hands | ${decisionsRecorded} decisions | ${Math.round(hps)} hands/sec`
      );
    }
  }

  outStream.end();

  const totalElapsed = (Date.now() - startTime) / 1000;
  console.log("\n");
  console.log("Data generation complete.");
  console.log(`  Hands: ${handsCompleted} | Decisions: ${decisionsRecorded}`);
  console.log(`  Time: ${totalElapsed.toFixed(1)}s | Speed: ${Math.round(handsCompleted / totalElapsed)} hands/sec`);
  console.log(`  Errors: ${errors}`);
  console.log(`  Output: ${outPath}`);

  return { handsCompleted, decisionsRecorded, outPath };
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
  else if (key === "strategy") opts.strategy = val;
  else if (key === "seed") opts.seed = parseInt(val);
  else if (key === "port") opts.port = parseInt(val);
  else if (key === "append") { opts.append = true; i--; }
}

console.log("RL Data Generation");
console.log("=".repeat(60));
console.log(`Config: ${opts.hands || 100000} hands, ${opts.seats || 6} seats, strategy=${opts.strategy || "tag"}`);
console.log();

generateData(opts);
