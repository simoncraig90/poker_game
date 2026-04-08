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
 *   node scripts/cfr/train-cfr.js --threads 8             # multi-threaded (default: CPU count)
 *   node scripts/cfr/train-cfr.js --threads 1             # force single-threaded
 *
 * Output:
 *   - Prints progress and exploitability every N iterations
 *   - Saves strategy to vision/models/cfr_strategy.json
 *   - Saves checkpoints every 50k iterations
 */

const os = require("os");
const path = require("path");
const fs = require("fs");
const { Worker } = require("worker_threads");
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
const NUM_THREADS = parseInt(getArg("threads", String(os.cpus().length)), 10);

// Select game module based on mode
const gameModule = GAME_MODE === "preflop"
  ? require("./simple-holdem")
  : GAME_MODE === "sixmax"
  ? require("./sixmax-holdem")
  : GAME_MODE === "flop"
  ? require("./flop-holdem")
  : require("./full-holdem");

const MODEL_DIR = path.join(__dirname, "..", "..", "vision", "models");
const STRATEGY_PATH = path.join(MODEL_DIR, `cfr_strategy_${GAME_MODE}.json`);
const CHECKPOINT_PATH = path.join(MODEL_DIR, `cfr_checkpoint_${GAME_MODE}.json`);
// Keep backward-compatible paths too
const STRATEGY_PATH_DEFAULT = path.join(MODEL_DIR, "cfr_strategy.json");

// ── Main ─────────────────────────────────────────────────────────────────

async function main() {
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

  const useThreads = NUM_THREADS > 1;
  console.log(`Training for ${NUM_ITERATIONS.toLocaleString()} iterations...`);
  console.log(`  Game mode: ${GAME_MODE}`);
  console.log(`  Threads: ${NUM_THREADS}${useThreads ? "" : " (single-threaded)"}`);
  console.log(`  Log every: ${LOG_EVERY.toLocaleString()}`);
  console.log(`  Exploitability check every: ${EXPLOIT_EVERY.toLocaleString()}`);
  console.log(`  Checkpoint every: ${CHECKPOINT_EVERY.toLocaleString()}\n`);

  if (useThreads) {
    await trainMultiThreaded(trainer);
  } else {
    trainSingleThreaded(trainer);
  }

  const totalIters = trainer.iterations;

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

// ── Single-threaded training ────────────────────────────────────────────

function trainSingleThreaded(trainer) {
  const startTime = Date.now();

  trainer.train(NUM_ITERATIONS, {
    logEvery: LOG_EVERY,
    onProgress(iter, t) {
      const now = Date.now();
      const elapsed = (now - startTime) / 1000;
      const iterPerSec = iter / elapsed;

      const infoSets = t.regretSum.size;
      let line = `  iter ${iter.toLocaleString().padStart(10)}  |  ` +
                 `${infoSets.toLocaleString()} info sets  |  ` +
                 `${iterPerSec.toFixed(0)} iter/s  |  ` +
                 `${elapsed.toFixed(1)}s elapsed`;

      if (iter % EXPLOIT_EVERY === 0) {
        const exploit = computeExploitabilitySampled(t, 10000);
        line += `  |  exploit: ${(exploit.exploitability * 1000).toFixed(2)} mBB/hand`;
      }

      console.log(line);

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
}

// ── Multi-threaded training ─────────────────────────────────────────────

/**
 * Spawn worker threads, let each train independently, merge at the end.
 *
 * Previous approach sent full table snapshots every batch round, which
 * caused O(workers * info_sets) serialization overhead each round.
 * With full game mode (100k+ info sets), this made multi-threaded
 * training slower than single-threaded and appeared to hang.
 *
 * New approach:
 *   1. Snapshot current tables once and send to all workers
 *   2. Each worker trains its share of iterations independently
 *   3. Workers send periodic progress messages for logging
 *   4. When done, workers send back full deltas for merging
 *   5. Main thread merges all deltas and saves
 *
 * This trades off inter-round synchronization for throughput. CFR still
 * converges — workers explore different random paths independently, and
 * the merged regret/strategy sums remain valid.
 */
async function trainMultiThreaded(trainer) {
  const workerPath = path.join(__dirname, "cfr-worker.js");
  const numWorkers = NUM_THREADS;
  const startTime = Date.now();
  const baseIter = trainer.iterations;

  // Spawn workers and wait for them to be ready
  const workers = [];
  const readyPromises = [];

  for (let i = 0; i < numWorkers; i++) {
    const worker = new Worker(workerPath);
    workers.push(worker);

    readyPromises.push(
      new Promise((resolve, reject) => {
        const onMsg = (msg) => {
          if (msg.type === "ready") {
            worker.removeListener("message", onMsg);
            worker.removeListener("error", reject);
            resolve();
          }
        };
        worker.on("message", onMsg);
        worker.on("error", reject);
      })
    );

    worker.postMessage({ type: "init", gameMode: GAME_MODE });
  }

  await Promise.all(readyPromises);
  console.log(`  ${numWorkers} worker threads initialized.\n`);

  // Snapshot current tables once
  const tables = trainer.exportTables();

  // Divide iterations among workers
  const perWorker = Math.ceil(NUM_ITERATIONS / numWorkers);

  // Track aggregate progress from all workers
  const workerProgress = new Array(numWorkers).fill(0);
  let lastLogTotal = 0;

  function logProgress() {
    const totalDone = workerProgress.reduce((a, b) => a + b, 0);
    if (totalDone - lastLogTotal >= LOG_EVERY) {
      const globalIter = baseIter + totalDone;
      const elapsed = (Date.now() - startTime) / 1000;
      const iterPerSec = totalDone / elapsed;

      let line = `  iter ${globalIter.toLocaleString().padStart(10)}  |  ` +
                 `${iterPerSec.toFixed(0)} iter/s  |  ` +
                 `${elapsed.toFixed(1)}s elapsed`;

      console.log(line);

      lastLogTotal = totalDone - (totalDone % LOG_EVERY);
    }
  }

  // Dispatch to all workers and collect results
  const resultPromises = workers.map((worker, idx) => {
    const iters = Math.min(perWorker, NUM_ITERATIONS - idx * perWorker);
    if (iters <= 0) return Promise.resolve(null);

    return new Promise((resolve, reject) => {
      const onMsg = (msg) => {
        if (msg.type === "progress") {
          workerProgress[idx] = msg.iterations;
          logProgress();
        } else if (msg.type === "done") {
          worker.removeListener("message", onMsg);
          worker.removeListener("error", onError);
          workerProgress[idx] = msg.iterations;
          logProgress();
          resolve(msg);
        }
      };
      const onError = (err) => {
        worker.removeListener("message", onMsg);
        worker.removeListener("error", onError);
        reject(err);
      };
      worker.on("message", onMsg);
      worker.on("error", onError);

      worker.postMessage({
        type: "run",
        iterations: iters,
        progressEvery: LOG_EVERY,
        regretSum: tables.regretSum,
        strategySum: tables.strategySum,
      });
    });
  });

  const results = await Promise.all(resultPromises);

  // Merge deltas from all workers into the main trainer
  let totalIters = 0;
  for (const result of results) {
    if (!result) continue;
    trainer.mergeDeltas(result.regretDelta, result.strategyDelta);
    totalIters += result.iterations;
  }
  trainer.iterations += totalIters;

  // Terminate workers
  await Promise.all(workers.map((w) => w.terminate()));

  const totalTime = (Date.now() - startTime) / 1000;
  console.log(`\nTraining complete: ${totalIters.toLocaleString()} iterations in ${totalTime.toFixed(1)}s`);
  console.log(`  ${(totalIters / totalTime).toFixed(0)} iterations/second (${numWorkers} threads)`);
  console.log(`  ${trainer.regretSum.size.toLocaleString()} unique information sets\n`);
}

// ── Checkpointing ───────────────────────────────────────────────────────

function saveCheckpoint(trainer, iter) {
  if (!fs.existsSync(MODEL_DIR)) {
    fs.mkdirSync(MODEL_DIR, { recursive: true });
  }
  // For large game trees (6-max), skip full checkpoint — too large for JSON.stringify
  // Only save the strategy (much smaller than regret+strategy sums)
  const infoSets = trainer.regretSum.size;
  if (infoSets > 500000) {
    console.log(`  [checkpoint] ${iter.toLocaleString()} iters, ${infoSets.toLocaleString()} info sets (skipping full checkpoint, saving strategy only)`);
    saveStrategy(trainer);
    return;
  }
  console.log(`  [checkpoint] Saving at iteration ${iter.toLocaleString()}...`);
  trainer.save(CHECKPOINT_PATH);
}

function saveStrategy(trainer) {
  if (!fs.existsSync(MODEL_DIR)) {
    fs.mkdirSync(MODEL_DIR, { recursive: true });
  }
  const strategy = trainer.exportStrategy();
  const entries = Object.entries(strategy);
  console.log(`Saving strategy to ${STRATEGY_PATH}... (${entries.length.toLocaleString()} entries)`);

  // Stream write for large strategies — JSON.stringify can't handle >500MB strings
  const ws = fs.createWriteStream(STRATEGY_PATH);
  ws.write("{\n");
  for (let i = 0; i < entries.length; i++) {
    const [key, val] = entries[i];
    ws.write(`${JSON.stringify(key)}:${JSON.stringify(val)}${i < entries.length - 1 ? "," : ""}\n`);
  }
  ws.write("}\n");
  ws.end();

  // Also save to default path
  const ws2 = fs.createWriteStream(STRATEGY_PATH_DEFAULT);
  ws2.write("{\n");
  for (let i = 0; i < entries.length; i++) {
    const [key, val] = entries[i];
    ws2.write(`${JSON.stringify(key)}:${JSON.stringify(val)}${i < entries.length - 1 ? "," : ""}\n`);
  }
  ws2.write("}\n");
  ws2.end();
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
  const numPlayers = gameModule.NUM_PLAYERS || 2;
  const totals = new Array(numPlayers).fill(0);

  for (let i = 0; i < numSamples; i++) {
    const deal = gameModule.dealForIteration();
    const state = numPlayers === 2
      ? gameModule.createInitialState(deal.p0Cards, deal.p1Cards, deal.board)
      : gameModule.createInitialState(deal.playerCards, deal.board);
    // Only check exploitability for first 2 players (to keep cost manageable)
    for (let p = 0; p < Math.min(numPlayers, 2); p++) {
      totals[p] += rolloutBestResponse(trainer, state, p);
    }
  }

  return {
    exploitability: (totals[0] / numSamples + totals[1] / numSamples) / 2,
    player0: totals[0] / numSamples,
    player1: totals[1] / numSamples,
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

main().catch((err) => {
  console.error("Training failed:", err);
  process.exit(1);
});
