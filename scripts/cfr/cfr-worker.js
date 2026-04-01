"use strict";

/**
 * CFR Worker Thread.
 *
 * Runs batches of MCCFR iterations independently, then returns
 * regret and strategy deltas to the main thread for merging.
 *
 * Protocol:
 *   Main -> Worker: { type: 'init', gameMode: 'full'|'preflop' }
 *   Main -> Worker: { type: 'run', iterations: N, regretSum: {...}, strategySum: {...} }
 *   Worker -> Main: { type: 'done', regretDelta: {...}, strategyDelta: {...}, iterations: N }
 *   Main -> Worker: { type: 'exit' }
 */

const { parentPort } = require("worker_threads");
const { CFRTrainer } = require("./cfr");

let gameModule = null;
let trainer = null;

parentPort.on("message", (msg) => {
  switch (msg.type) {
    case "init": {
      gameModule = msg.gameMode === "preflop"
        ? require("./simple-holdem")
        : require("./full-holdem");
      trainer = new CFRTrainer(gameModule);
      parentPort.postMessage({ type: "ready" });
      break;
    }

    case "run": {
      // Import the current tables from the main thread as our baseline
      const baseline = {
        regretSum: msg.regretSum || {},
        strategySum: msg.strategySum || {},
        iterations: 0,
      };
      trainer.importTables(baseline);

      // Run iterations locally
      const numIters = msg.iterations || 1000;
      trainer.train(numIters);

      // Compute deltas: (current table values) - (baseline values)
      const regretDelta = {};
      for (const [key, vals] of trainer.regretSum) {
        const base = baseline.regretSum[key];
        if (!base) {
          // Entirely new info set
          regretDelta[key] = { ...vals };
        } else {
          const delta = {};
          let hasDelta = false;
          for (const action of Object.keys(vals)) {
            const d = vals[action] - (base[action] || 0);
            if (d !== 0) {
              delta[action] = d;
              hasDelta = true;
            }
          }
          if (hasDelta) regretDelta[key] = delta;
        }
      }

      const strategyDelta = {};
      for (const [key, vals] of trainer.strategySum) {
        const base = baseline.strategySum[key];
        if (!base) {
          strategyDelta[key] = { ...vals };
        } else {
          const delta = {};
          let hasDelta = false;
          for (const action of Object.keys(vals)) {
            const d = vals[action] - (base[action] || 0);
            if (d !== 0) {
              delta[action] = d;
              hasDelta = true;
            }
          }
          if (hasDelta) strategyDelta[key] = delta;
        }
      }

      parentPort.postMessage({
        type: "done",
        regretDelta,
        strategyDelta,
        iterations: numIters,
      });
      break;
    }

    case "exit": {
      process.exit(0);
      break;
    }
  }
});
