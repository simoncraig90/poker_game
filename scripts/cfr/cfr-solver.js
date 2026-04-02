#!/usr/bin/env node
"use strict";

/**
 * Real-time subgame solver for the poker advisor.
 *
 * Persistent process that accepts solve requests via stdin JSON lines
 * and returns CFR-solved strategies via stdout. Warm-starts from
 * pre-trained checkpoint for fast convergence.
 *
 * Protocol:
 *   → {"cmd":"solve", "heroCards":[...], "board":[...], "pot":3.5, ...}
 *   ← {"strategy":{...}, "solveTimeMs":142, "cached":false}
 *
 *   → {"cmd":"ping"}
 *   ← {"pong":true}
 *
 *   → {"cmd":"quit"}
 *   ← (process exits)
 */

const fs = require("fs");
const path = require("path");
const readline = require("readline");
const { CFRTrainer } = require("./cfr");
const gameModule = require("./full-holdem");
const { evaluateHandStrength, strengthToBucket } = require("./abstraction");

// ── Configuration ───────────────────────────────────────────────────────

const CHECKPOINT_PATH = path.join(__dirname, "../../vision/models/cfr_checkpoint_full.json");
const STRATEGY_PATH = path.join(__dirname, "../../vision/models/cfr_strategy.json");
const DEFAULT_SAMPLES = 20;
const DEFAULT_ITERS_PER_SAMPLE = 50;
const DEFAULT_TIME_BUDGET_MS = 2000;  // 2s solve, leaves 4s to read + act
const CACHE_MAX = 500;
const CACHE_TTL_MS = 30000;

// ── Pre-trained data ────────────────────────────────────────────────────

let pretrainedStrategy = null;  // avg strategy lookup (for fallback)
let pretrainedRegrets = null;   // regret sums (for warm start)
let pretrainedStrategySums = null; // strategy sums (for warm start)

function loadPretrained() {
  // Load strategy for fallback
  if (fs.existsSync(STRATEGY_PATH)) {
    pretrainedStrategy = JSON.parse(fs.readFileSync(STRATEGY_PATH, "utf8"));
    process.stderr.write(`[Solver] Loaded strategy: ${Object.keys(pretrainedStrategy).length} entries\n`);
  }

  // Skip checkpoint loading — subgame solver runs from scratch per solve
  // (warm start from 330MB checkpoint is too slow to copy per-trainer)
}

// ── Deck utilities ──────────────────────────────────────────────────────

function cardKey(c) {
  return `${c.rank}_${c.suit}`;
}

function buildDeckExcluding(excludeCards) {
  const excluded = new Set(excludeCards.map(cardKey));
  const deck = [];
  for (let suit = 1; suit <= 4; suit++) {
    for (let rank = 2; rank <= 14; rank++) {
      const c = { rank, suit };
      if (!excluded.has(cardKey(c))) {
        deck.push(c);
      }
    }
  }
  return deck;
}

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function sampleOpponentHands(deck, numSamples) {
  const hands = [];
  for (let i = 0; i < numSamples; i++) {
    const shuffled = shuffle(deck);
    hands.push([shuffled[0], shuffled[1]]);
  }
  return hands;
}

function completeBoard(board, deck, targetCards) {
  if (board.length >= targetCards) return board.slice(0, targetCards);
  const excluded = new Set(board.map(cardKey));
  const available = deck.filter(c => !excluded.has(cardKey(c)));
  const shuffled = shuffle(available);
  const result = board.slice();
  for (let i = 0; result.length < targetCards; i++) {
    result.push(shuffled[i]);
  }
  return result;
}

// ── LRU Cache ───────────────────────────────────────────────────────────

const cache = new Map();

function cacheKey(req) {
  const heroSorted = req.heroCards
    .map(c => `${c.rank}${c.suit}`)
    .sort()
    .join("");
  const boardSorted = (req.board || [])
    .map(c => `${c.rank}${c.suit}`)
    .sort()
    .join("");
  const potBucket = Math.floor((req.pot || 0) / 5);
  const stackBucket = (req.heroStack || 100) < 30 ? 0 : (req.heroStack || 100) < 80 ? 1 : 2;
  return `${heroSorted}|${boardSorted}|${req.street}|${req.actionHistory || ""}|${potBucket}|${stackBucket}`;
}

function cacheGet(key) {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.time > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  // Move to end (LRU)
  cache.delete(key);
  cache.set(key, entry);
  return entry.strategy;
}

function cacheSet(key, strategy) {
  if (cache.size >= CACHE_MAX) {
    // Delete oldest
    const first = cache.keys().next().value;
    cache.delete(first);
  }
  cache.set(key, { strategy, time: Date.now() });
}

// ── Warm start ──────────────────────────────────────────────────────────

// Pre-built trainer with warm start (created once at startup)
let warmTrainer = null;

function initWarmTrainer() {
  if (!pretrainedRegrets) return;
  warmTrainer = new CFRTrainer(gameModule);
  warmTrainer.importTables({
    regretSum: pretrainedRegrets,
    strategySum: pretrainedStrategySums,
    iterations: 500000,
  });
  process.stderr.write(`[Solver] Warm trainer initialized: ${warmTrainer.regretSum.size} info sets\n`);
}

// ── Subgame solver ──────────────────────────────────────────────────────

function solveSubgame(req) {
  const startTime = Date.now();
  const timeBudget = req.timeBudgetMs || DEFAULT_TIME_BUDGET_MS;
  const numSamples = req.numSamples || DEFAULT_SAMPLES;
  const itersPerSample = req.maxIterations
    ? Math.ceil(req.maxIterations / numSamples)
    : DEFAULT_ITERS_PER_SAMPLE;

  const heroCards = req.heroCards;
  const board = req.board || [];
  const street = req.street || "PREFLOP";

  // Build deck excluding known cards
  const knownCards = [...heroCards, ...board];
  const deck = buildDeckExcluding(knownCards);

  // Sample opponent hands
  const oppHands = sampleOpponentHands(deck, numSamples);

  // Use a fresh trainer per solve (lightweight, no warm start copying)
  // The trainer starts from scratch but converges fast on small subgames
  const trainer = new CFRTrainer(gameModule);
  let totalIterations = 0;
  let samplesUsed = 0;

  const heroPos = req.heroPosition || 0;

  for (let s = 0; s < numSamples; s++) {
    // Time check
    if (Date.now() - startTime > timeBudget * 0.85) break;

    const oppCards = oppHands[s];
    const oppDeck = buildDeckExcluding([...knownCards, ...oppCards]);
    const fullBoard = completeBoard(board, oppDeck, 5);

    const p0Cards = heroPos === 0 ? heroCards : oppCards;
    const p1Cards = heroPos === 0 ? oppCards : heroCards;

    // Create initial state and configure to match current situation
    const state = gameModule.createInitialState(p0Cards, p1Cards, fullBoard);
    state.street = street;
    state.board = board.slice();
    state.pot = req.pot || 1.5;
    state.activePlayer = heroPos;
    state.raisesThisStreet = req.raisesThisStreet || 0;
    state.actionsThisStreet = 0;

    if (heroPos === 0) {
      state.p0Stack = req.heroStack || 99;
      state.p1Stack = req.oppStack || 99;
      state.p0Invested = req.heroInvested || 0;
      state.p1Invested = req.oppInvested || 0;
    } else {
      state.p1Stack = req.heroStack || 99;
      state.p0Stack = req.oppStack || 99;
      state.p1Invested = req.heroInvested || 0;
      state.p0Invested = req.oppInvested || 0;
    }
    state.currentBet = req.currentBet || Math.max(state.p0Invested, state.p1Invested);
    state.previousStreets = req.actionHistory || "";
    state.streetHistory = "";

    // Run CFR iterations with this specific card deal
    // Instead of trainer.train() which re-deals, we traverse directly
    for (let it = 0; it < itersPerSample; it++) {
      trainer.cfr(state, 0, [1, 1]);
      trainer.cfr(state, 1, [1, 1]);
      totalIterations++;
    }

    samplesUsed++;
  }

  // Extract hero's strategy — look up the info set key for hero's actual hand
  // We need to construct one state to get the right key
  if (samplesUsed > 0) {
    const sampleOpp = oppHands[0];
    const oppDeck = buildDeckExcluding([...knownCards, ...sampleOpp]);
    const fullBoard = completeBoard(board, oppDeck, 5);
    const p0Cards = heroPos === 0 ? heroCards : sampleOpp;
    const p1Cards = heroPos === 0 ? sampleOpp : heroCards;
    const refState = gameModule.createInitialState(p0Cards, p1Cards, fullBoard);
    refState.street = street;
    refState.board = board.slice();
    refState.pot = req.pot || 1.5;
    refState.activePlayer = heroPos;
    refState.raisesThisStreet = req.raisesThisStreet || 0;
    if (heroPos === 0) {
      refState.p0Stack = req.heroStack || 99;
      refState.p1Stack = req.oppStack || 99;
    } else {
      refState.p1Stack = req.heroStack || 99;
      refState.p0Stack = req.oppStack || 99;
    }
    refState.previousStreets = req.actionHistory || "";
    refState.streetHistory = "";

    const infoSet = gameModule.getInfoSetKey(refState);
    const avg = trainer.getAverageStrategy(infoSet);

    if (avg) {
      return {
        strategy: avg,
        iterations: totalIterations,
        samples: samplesUsed,
        solveTimeMs: Date.now() - startTime,
        cached: false,
      };
    }
  }

  return {
    strategy: {},
    iterations: totalIterations,
    samples: samplesUsed,
    solveTimeMs: Date.now() - startTime,
    cached: false,
  };
}

// ── Main loop ───────────────────────────────────────────────────────────

function main() {
  // Load pre-trained data
  loadPretrained();

  // Signal ready
  const readyMsg = JSON.stringify({ ready: true }) + "\n";
  process.stdout.write(readyMsg);

  // Read commands from stdin
  const rl = readline.createInterface({ input: process.stdin, terminal: false });

  rl.on("line", (line) => {
    try {
      const req = JSON.parse(line.trim());

      if (req.cmd === "ping") {
        process.stdout.write(JSON.stringify({ pong: true }) + "\n");
        return;
      }

      if (req.cmd === "quit") {
        process.exit(0);
      }

      if (req.cmd === "solve") {
        // Check cache
        const key = cacheKey(req);
        const cached = cacheGet(key);
        if (cached) {
          process.stdout.write(JSON.stringify({
            id: req.id,
            strategy: cached,
            solveTimeMs: 0,
            cached: true,
            iterations: 0,
            samples: 0,
          }) + "\n");
          return;
        }

        // Solve
        const result = solveSubgame(req);
        result.id = req.id;

        // Cache result if we got a strategy
        if (Object.keys(result.strategy).length > 0) {
          cacheSet(key, result.strategy);
        }

        process.stdout.write(JSON.stringify(result) + "\n");
        return;
      }

      // Unknown command
      process.stdout.write(JSON.stringify({ error: `Unknown command: ${req.cmd}` }) + "\n");
    } catch (e) {
      process.stdout.write(JSON.stringify({ error: e.message }) + "\n");
    }
  });

  rl.on("close", () => {
    process.exit(0);
  });
}

main();
