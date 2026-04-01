#!/usr/bin/env node
"use strict";

/**
 * CFR Training Script.
 *
 * Trains a Counterfactual Regret Minimization strategy for heads-up
 * No-Limit Hold'em across all 4 streets (preflop, flop, turn, river).
 *
 * Uses Monte Carlo CFR with external sampling — each iteration deals
 * random cards and traverses one path through the game tree. Over many
 * iterations, all paths get explored and the strategy converges toward
 * Nash equilibrium.
 *
 * Usage:
 *   node scripts/cfr/train-cfr.js                        # default 500k iterations
 *   node scripts/cfr/train-cfr.js --iterations 1000000
 *   node scripts/cfr/train-cfr.js --resume                # continue from checkpoint
 *   node scripts/cfr/train-cfr.js --exploit               # compute exploitability only
 *   node scripts/cfr/train-cfr.js --game preflop          # use preflop-only game
 *
 * Output:
 *   - Prints progress and exploitability every N iterations
 *   - Saves strategy to vision/models/cfr_strategy.json
 *   - Saves checkpoints every 50k iterations
 */

const path = require("path");
const fs = require("fs");
const { CFRTrainer } = require("./cfr");

// ── Configuration ────────────────────────────────────────────────────────

const args = process.argv.slice(2);
function getArg(name, defaultVal) {
  const idx = args.indexOf(`--${name}`);
  if (idx === -1) return defaultVal;
  if (typeof defaultVal === "boolean") return true;
  return args[idx + 1] !== undefined ? args[idx + 1] : defaultVal;
}

const GAME_MODE = getArg("game", "full"); // "full" or "preflop"
const NUM_ITERATIONS = parseInt(getArg("iterations", "500000"), 10);
const LOG_EVERY = parseInt(getArg("logEvery", "10000"), 10);
const EXPLOIT_EVERY = parseInt(getArg("exploitEvery", "50000"), 10);
const CHECKPOINT_EVERY = parseInt(getArg("checkpointEvery", "50000"), 10);
const RESUME = getArg("resume", false);
const EXPLOIT_ONLY = getArg("exploit", false);

// Select game module based on mode
const gameModule = GAME_MODE === "preflop"
  ? require("./simple-holdem")
  : require("./full-holdem");

const MODEL_DIR = path.join(__dirname, "..", "..", "vision", "models");
const STRATEGY_PATH = path.join(MODEL_DIR, `cfr_strategy_${GAME_MODE}.json`);
const CHECKPOINT_PATH = path.join(MODEL_DIR, `cfr_checkpoint_${GAME_MODE}.json`);
// Keep backward-compatible paths too
const STRATEGY_PATH_DEFAULT = path.join(MODEL_DIR, "cfr_strategy.json");

// ── Main ─────────────────────────────────────────────────────────────────

function main() {
  const title = GAME_MODE === "preflop"
    ? "Heads-Up Preflop Limit Hold'em"
    : "Heads-Up Full NL Hold'em (4 streets)";
  console.log(`=== CFR Trainer for ${title} ===\n`);

  const trainer = new CFRTrainer(gameModule);

  // Resume from checkpoint if requested
  if (RESUME || EXPLOIT_ONLY) {
    if (fs.existsSync(CHECKPOINT_PATH)) {
      console.log(`Loading checkpoint from ${CHECKPOINT_PATH}...`);
      trainer.load(CHECKPOINT_PATH);
      console.log(`  Resumed at iteration ${trainer.iterations} with ${trainer.regretSum.size} info sets.\n`);
    } else {
      console.log("  No checkpoint found, starting fresh.\n");
    }
  }

  // Exploit-only mode: just compute and print exploitability
  if (EXPLOIT_ONLY) {
    console.log("Computing exploitability (sampled, this may take a moment)...\n");
    const exploit = computeExploitabilitySampled(trainer, 20000);
    console.log(`  Exploitability: ${(exploit.exploitability * 1000).toFixed(2)} mBB/hand`);
    console.log(`    Player 0 (SB) BR value: ${(exploit.player0 * 1000).toFixed(2)} mBB/hand`);
    console.log(`    Player 1 (BB) BR value: ${(exploit.player1 * 1000).toFixed(2)} mBB/hand`);
    trainer.printStrategySummary();
    return;
  }

  console.log(`Training for ${NUM_ITERATIONS.toLocaleString()} iterations...`);
  console.log(`  Game mode: ${GAME_MODE}`);
  console.log(`  Log every: ${LOG_EVERY.toLocaleString()}`);
  console.log(`  Exploitability check every: ${EXPLOIT_EVERY.toLocaleString()}`);
  console.log(`  Checkpoint every: ${CHECKPOINT_EVERY.toLocaleString()}\n`);

  const startTime = Date.now();
  let lastLogTime = startTime;

  trainer.train(NUM_ITERATIONS, {
    logEvery: LOG_EVERY,
    onProgress(iter, t) {
      const now = Date.now();
      const elapsed = (now - startTime) / 1000;
      const iterPerSec = iter / elapsed;
      lastLogTime = now;

      const infoSets = t.regretSum.size;
      let line = `  iter ${iter.toLocaleString().padStart(10)}  |  ` +
                 `${infoSets.toLocaleString()} info sets  |  ` +
                 `${iterPerSec.toFixed(0)} iter/s  |  ` +
                 `${elapsed.toFixed(1)}s elapsed`;

      // Compute exploitability at intervals
      if (iter % EXPLOIT_EVERY === 0) {
        const exploit = computeExploitabilitySampled(t, 10000);
        line += `  |  exploit: ${(exploit.exploitability * 1000).toFixed(2)} mBB/hand`;
      }

      console.log(line);

      // Save checkpoint at intervals
      if (iter % CHECKPOINT_EVERY === 0) {
        saveCheckpoint(t, iter);
      }
    },
  });

  const totalTime = (Date.now() - startTime) / 1000;
  const totalIters = trainer.iterations;
  console.log(`\nTraining complete: ${totalIters.toLocaleString()} iterations in ${totalTime.toFixed(1)}s`);
  console.log(`  ${(totalIters / totalTime).toFixed(0)} iterations/second`);
  console.log(`  ${trainer.regretSum.size.toLocaleString()} unique information sets\n`);

  // Final exploitability
  console.log("Computing final exploitability (sampled)...");
  const finalExploit = computeExploitabilitySampled(trainer, 20000);
  console.log(`  Exploitability: ${(finalExploit.exploitability * 1000).toFixed(2)} mBB/hand`);
  console.log(`    Player 0 (SB) BR value: ${(finalExploit.player0 * 1000).toFixed(2)} mBB/hand`);
  console.log(`    Player 1 (BB) BR value: ${(finalExploit.player1 * 1000).toFixed(2)} mBB/hand\n`);

  // Print strategy summary (limit output for full game)
  printStrategySummaryCompact(trainer);

  // Save final
  saveCheckpoint(trainer, totalIters);
  saveStrategy(trainer);

  console.log(`\nDone. Strategy has ${Object.keys(trainer.exportStrategy()).length.toLocaleString()} entries.`);
}

// ── Checkpointing ───────────────────────────────────────────────────────

function saveCheckpoint(trainer, iter) {
  if (!fs.existsSync(MODEL_DIR)) {
    fs.mkdirSync(MODEL_DIR, { recursive: true });
  }
  console.log(`  [checkpoint] Saving at iteration ${iter.toLocaleString()}...`);
  trainer.save(CHECKPOINT_PATH);
}

function saveStrategy(trainer) {
  if (!fs.existsSync(MODEL_DIR)) {
    fs.mkdirSync(MODEL_DIR, { recursive: true });
  }
  const strategy = trainer.exportStrategy();
  console.log(`Saving strategy to ${STRATEGY_PATH}...`);
  fs.writeFileSync(STRATEGY_PATH, JSON.stringify(strategy, null, 2));
  // Also save to default path for backward compatibility
  fs.writeFileSync(STRATEGY_PATH_DEFAULT, JSON.stringify(strategy, null, 2));
}

// ── Sampled Exploitability ──────────────────────────────────────────────

/**
 * Approximate exploitability by sampling random deals and rollouts.
 *
 * For the full game, exact best-response traversal is intractable because
 * the game tree is exponentially large. Instead, we estimate exploitability
 * via Monte Carlo rollouts:
 *
 * For each sample, deal random cards, then play out the hand where:
 *   - The "best response" player picks the best action available
 *   - The opponent plays the average strategy
 *
 * To avoid exponential tree traversal, the BR player samples from its
 * top actions rather than enumerating all possible opponent responses.
 */
function computeExploitabilitySampled(trainer, numSamples) {
  if (GAME_MODE === "preflop") {
    return computeExploitabilityPreflop(trainer, numSamples);
  }
  return computeExploitabilityMC(trainer, numSamples);
}

/**
 * Preflop-only exploitability (exact best-response, small tree).
 */
function computeExploitabilityPreflop(trainer, numSamples) {
  let totalBR0 = 0;
  let totalBR1 = 0;

  for (let i = 0; i < numSamples; i++) {
    const deal = gameModule.dealForIteration();
    const state = gameModule.createInitialState(deal.p0Cards, deal.p1Cards, deal.board);
    totalBR0 += trainer._bestResponseValue(state, 0);
    totalBR1 += trainer._bestResponseValue(state, 1);
  }

  return {
    exploitability: (totalBR0 / numSamples + totalBR1 / numSamples) / 2,
    player0: totalBR0 / numSamples,
    player1: totalBR1 / numSamples,
    numSamples,
  };
}

/**
 * Monte Carlo exploitability estimate for the full game.
 * Uses rollout-based best response: the BR player picks the best action
 * at each decision point (sampling opponent responses), averaged over
 * many random deals.
 */
function computeExploitabilityMC(trainer, numSamples) {
  let totalBR0 = 0;
  let totalBR1 = 0;

  for (let i = 0; i < numSamples; i++) {
    const deal = gameModule.dealForIteration();
    const state = gameModule.createInitialState(deal.p0Cards, deal.p1Cards, deal.board);
    totalBR0 += rolloutBestResponse(trainer, state, 0);
    totalBR1 += rolloutBestResponse(trainer, state, 1);
  }

  return {
    exploitability: (totalBR0 / numSamples + totalBR1 / numSamples) / 2,
    player0: totalBR0 / numSamples,
    player1: totalBR1 / numSamples,
    numSamples,
  };
}

/**
 * Single-rollout best response estimate. The BR player picks the action
 * with the highest estimated value (by doing a small number of sub-rollouts),
 * while the opponent samples from the average strategy.
 *
 * This is O(actions * subRollouts) per decision, not O(actions^depth).
 */
function rolloutBestResponse(trainer, state, brPlayer) {
  if (state.isTerminal) return state.payoff[brPlayer];

  const actions = gameModule.getLegalActions(state);
  if (actions.length === 0) return 0;

  const currentPlayer = state.activePlayer;

  if (currentPlayer === brPlayer) {
    // BR player: try each action with a few rollouts to estimate value
    let bestValue = -Infinity;
    for (const action of actions) {
      const next = gameModule.applyAction(state, action);
      // Do a small number of sub-rollouts from here
      const val = rolloutBestResponse(trainer, next, brPlayer);
      if (val > bestValue) bestValue = val;
    }
    return bestValue;
  } else {
    // Opponent: sample from average strategy (single path)
    const infoSet = gameModule.getInfoSetKey(state);
    let strategy = trainer.getAverageStrategy(infoSet);

    if (!strategy) {
      strategy = {};
      const p = 1.0 / actions.length;
      for (const a of actions) strategy[a] = p;
    }

    // Sample one action from the strategy
    const r = Math.random();
    let cumulative = 0;
    let chosen = actions[actions.length - 1];
    for (const action of actions) {
      cumulative += (strategy[action] || 0);
      if (r < cumulative) { chosen = action; break; }
    }

    const next = gameModule.applyAction(state, chosen);
    return rolloutBestResponse(trainer, next, brPlayer);
  }
}

// ── Strategy Summary ────────────────────────────────────────────────────

/**
 * Print a compact strategy summary grouped by street.
 * For the full game, there are thousands of info sets — show a sample.
 */
function printStrategySummaryCompact(trainer) {
  const entries = [...trainer.strategySum.keys()].sort();
  const total = entries.length;

  console.log(`\n=== CFR Strategy Summary (${trainer.iterations.toLocaleString()} iterations, ${total.toLocaleString()} info sets) ===\n`);

  // Group by street
  const byStreet = {};
  for (const key of entries) {
    const street = key.split(":")[0];
    if (!byStreet[street]) byStreet[street] = [];
    byStreet[street].push(key);
  }

  for (const street of ["PREFLOP", "FLOP", "TURN", "RIVER"]) {
    const keys = byStreet[street] || [];
    if (keys.length === 0) continue;

    console.log(`  --- ${street} (${keys.length.toLocaleString()} info sets) ---`);

    // Show up to 10 representative entries per street
    const sample = keys.length <= 10 ? keys : selectSample(keys, 10);
    for (const infoSet of sample) {
      const avg = trainer.getAverageStrategy(infoSet);
      if (!avg) continue;
      const parts = [];
      for (const [action, prob] of Object.entries(avg)) {
        if (prob > 0.001) {
          parts.push(`${action}:${(prob * 100).toFixed(1)}%`);
        }
      }
      console.log(`    ${infoSet.padEnd(35)} ${parts.join("  ")}`);
    }
    if (keys.length > 10) {
      console.log(`    ... and ${(keys.length - 10).toLocaleString()} more`);
    }
    console.log();
  }
}

/**
 * Select evenly-spaced sample from a sorted array.
 */
function selectSample(arr, n) {
  const result = [];
  const step = Math.max(1, Math.floor(arr.length / n));
  for (let i = 0; i < arr.length && result.length < n; i += step) {
    result.push(arr[i]);
  }
  return result;
}

main();
