"use strict";

/**
 * Counterfactual Regret Minimization (CFR) engine.
 *
 * Implements vanilla CFR with external sampling Monte Carlo (MCCFR)
 * for the chance nodes (card deals). This is the same algorithmic family
 * used by Libratus and Pluribus.
 *
 * Key concepts:
 *   - Information set: what a player knows (their cards + action history)
 *   - Regret: how much better an action would have been vs. what we did
 *   - Strategy: proportional to positive regrets (regret matching)
 *   - Average strategy: converges to Nash equilibrium over iterations
 *
 * References:
 *   - Zinkevich et al. 2007, "Regret Minimization in Games with Incomplete Information"
 *   - Lanctot et al. 2009, "Monte Carlo Sampling for Regret Minimization in Extensive Games"
 */

const fs = require("fs");
const path = require("path");

class CFRTrainer {
  constructor(gameModule) {
    /**
     * gameModule must provide:
     *   createInitialState(p0Cards, p1Cards, board)
     *   getLegalActions(state) -> string[]
     *   applyAction(state, action) -> newState
     *   getInfoSetKey(state) -> string
     *   dealForIteration(rng) -> { p0Cards, p1Cards, board }
     */
    this.game = gameModule;

    // Cumulative regret for each (infoSet, action) pair.
    // regretSum[infoSet][action] = number
    this.regretSum = new Map();

    // Cumulative strategy weights (for computing average strategy).
    // strategySum[infoSet][action] = number
    this.strategySum = new Map();

    this.iterations = 0;
  }

  // ── Regret Matching ──────────────────────────────────────────────────

  /**
   * Get current strategy for an information set using regret matching.
   * Strategy is proportional to positive cumulative regrets.
   * If all regrets are non-positive, returns uniform random.
   *
   * @param {string} infoSet
   * @param {string[]} actions - legal actions
   * @returns {Object} action -> probability
   */
  getStrategy(infoSet, actions) {
    const regrets = this.regretSum.get(infoSet);
    const strategy = {};

    if (!regrets) {
      // No data yet: uniform
      const p = 1.0 / actions.length;
      for (const a of actions) strategy[a] = p;
      return strategy;
    }

    // Sum of positive regrets
    let positiveSum = 0;
    for (const a of actions) {
      positiveSum += Math.max(0, regrets[a] || 0);
    }

    if (positiveSum > 0) {
      for (const a of actions) {
        strategy[a] = Math.max(0, regrets[a] || 0) / positiveSum;
      }
    } else {
      // All regrets <= 0: play uniformly
      const p = 1.0 / actions.length;
      for (const a of actions) strategy[a] = p;
    }

    return strategy;
  }

  /**
   * Get the converged average strategy (for actual play).
   * This is the strategy that converges to Nash equilibrium.
   *
   * @param {string} infoSet
   * @returns {Object} action -> probability
   */
  getAverageStrategy(infoSet) {
    const sums = this.strategySum.get(infoSet);
    if (!sums) return null;

    const strategy = {};
    let total = 0;
    for (const a of Object.keys(sums)) {
      total += sums[a];
    }

    if (total <= 0) return null;

    for (const a of Object.keys(sums)) {
      strategy[a] = sums[a] / total;
    }

    return strategy;
  }

  // ── CFR Traversal ────────────────────────────────────────────────────

  /**
   * One traversal of the game tree for a given traversing player.
   * Uses external sampling: chance nodes (card deals) are sampled once
   * per iteration rather than enumerated.
   *
   * @param {Object} state - current game state
   * @param {number} traversingPlayer - 0 or 1
   * @param {number[]} reachProbs - [p0_reach, p1_reach] probability of reaching this state
   * @returns {number} expected utility for the traversing player
   */
  cfr(state, traversingPlayer, reachProbs) {
    // Terminal node: return payoff
    if (state.isTerminal) {
      return state.payoff[traversingPlayer];
    }

    const currentPlayer = state.activePlayer;
    const actions = this.game.getLegalActions(state);

    if (actions.length === 0) {
      // Shouldn't happen if game is well-formed, but safeguard
      return 0;
    }

    const infoSet = this.game.getInfoSetKey(state);
    const strategy = this.getStrategy(infoSet, actions);

    // Accumulate strategy weighted by reach probability of current player
    this._accumulateStrategy(infoSet, strategy, reachProbs[currentPlayer]);

    if (currentPlayer !== traversingPlayer) {
      // Opponent node: sample ONE action according to current strategy
      // (external sampling - reduces variance vs. full enumeration)
      const action = this._sampleAction(strategy);
      const nextState = this.game.applyAction(state, action);
      const newReach = reachProbs.slice();
      newReach[currentPlayer] *= strategy[action];
      return this.cfr(nextState, traversingPlayer, newReach);
    }

    // Traversing player's node: enumerate ALL actions to compute regrets
    const actionValues = {};
    let nodeValue = 0;

    for (const action of actions) {
      const nextState = this.game.applyAction(state, action);
      const newReach = reachProbs.slice();
      newReach[currentPlayer] *= strategy[action];
      actionValues[action] = this.cfr(nextState, traversingPlayer, newReach);
      nodeValue += strategy[action] * actionValues[action];
    }

    // Update regrets: regret[a] += opponent_reach * (value[a] - node_value)
    // The opponent reach probability is the counterfactual weight.
    const opponentReach = reachProbs[1 - traversingPlayer];

    if (!this.regretSum.has(infoSet)) {
      this.regretSum.set(infoSet, {});
    }
    const regrets = this.regretSum.get(infoSet);

    for (const action of actions) {
      const regret = opponentReach * (actionValues[action] - nodeValue);
      regrets[action] = (regrets[action] || 0) + regret;
    }

    return nodeValue;
  }

  /**
   * Accumulate strategy sums for computing average strategy.
   */
  _accumulateStrategy(infoSet, strategy, reachProb) {
    if (!this.strategySum.has(infoSet)) {
      this.strategySum.set(infoSet, {});
    }
    const sums = this.strategySum.get(infoSet);
    for (const [action, prob] of Object.entries(strategy)) {
      sums[action] = (sums[action] || 0) + reachProb * prob;
    }
  }

  /**
   * Sample an action from a strategy (probability distribution).
   */
  _sampleAction(strategy) {
    const r = Math.random();
    let cumulative = 0;
    for (const [action, prob] of Object.entries(strategy)) {
      cumulative += prob;
      if (r < cumulative) return action;
    }
    // Fallback: return last action (rounding)
    const keys = Object.keys(strategy);
    return keys[keys.length - 1];
  }

  // ── Training Loop ────────────────────────────────────────────────────

  /**
   * Train for N iterations using external sampling MCCFR.
   * Each iteration:
   *   1. Sample a random card deal
   *   2. Run CFR traversal for player 0
   *   3. Run CFR traversal for player 1
   *
   * @param {number} numIterations
   * @param {Object} options - { logEvery, onProgress }
   */
  train(numIterations, options = {}) {
    const logEvery = options.logEvery || 1000;
    const onProgress = options.onProgress || null;

    for (let i = 0; i < numIterations; i++) {
      // Sample random cards (external sampling of chance node)
      const deal = this.game.dealForIteration();
      const state = this.game.createInitialState(deal.p0Cards, deal.p1Cards, deal.board);

      // Traverse for both players
      this.cfr(state, 0, [1.0, 1.0]);

      // Re-deal for player 1 traversal (independent sample)
      const deal2 = this.game.dealForIteration();
      const state2 = this.game.createInitialState(deal2.p0Cards, deal2.p1Cards, deal2.board);
      this.cfr(state2, 1, [1.0, 1.0]);

      this.iterations++;

      if (onProgress && this.iterations % logEvery === 0) {
        onProgress(this.iterations, this);
      }
    }
  }

  // ── Exploitability ───────────────────────────────────────────────────

  /**
   * Compute exploitability: the maximum a best-response opponent can gain
   * against our average strategy. Lower = closer to Nash equilibrium.
   *
   * For the simplified game, we compute this by enumerating all possible
   * card deals and computing the best response value.
   *
   * This is expensive but feasible for the small preflop-only game.
   */
  computeExploitability() {
    const deck = this.game.buildDeck();
    let totalBRValue0 = 0; // best response value for player 0
    let totalBRValue1 = 0; // best response value for player 1
    let numDeals = 0;

    // Enumerate all possible 2-card deals for each player (no overlap)
    for (let i = 0; i < deck.length; i++) {
      for (let j = i + 1; j < deck.length; j++) {
        for (let k = 0; k < deck.length; k++) {
          if (k === i || k === j) continue;
          for (let l = k + 1; l < deck.length; l++) {
            if (l === i || l === j) continue;

            const p0Cards = [deck[i], deck[j]];
            const p1Cards = [deck[k], deck[l]];
            const state = this.game.createInitialState(p0Cards, p1Cards, []);

            // Best response for player 0 (opponent plays average strategy)
            totalBRValue0 += this._bestResponseValue(state, 0);
            // Best response for player 1
            totalBRValue1 += this._bestResponseValue(state, 1);
            numDeals++;
          }
        }
      }
    }

    // Average exploitability in milli-big-blinds per hand
    const exploit0 = totalBRValue0 / numDeals;
    const exploit1 = totalBRValue1 / numDeals;
    return {
      exploitability: (exploit0 + exploit1) / 2, // average
      player0: exploit0,
      player1: exploit1,
      numDeals,
    };
  }

  /**
   * Compute the best response value for `brPlayer` at the given state.
   * The BR player plays optimally; the opponent plays the average strategy.
   */
  _bestResponseValue(state, brPlayer) {
    if (state.isTerminal) {
      return state.payoff[brPlayer];
    }

    const currentPlayer = state.activePlayer;
    const actions = this.game.getLegalActions(state);
    if (actions.length === 0) return 0;

    if (currentPlayer === brPlayer) {
      // BR player: pick the action with highest value
      let bestValue = -Infinity;
      for (const action of actions) {
        const next = this.game.applyAction(state, action);
        const val = this._bestResponseValue(next, brPlayer);
        if (val > bestValue) bestValue = val;
      }
      return bestValue;
    } else {
      // Opponent: play according to average strategy
      const infoSet = this.game.getInfoSetKey(state);
      let strategy = this.getAverageStrategy(infoSet);

      if (!strategy) {
        // No data: uniform
        strategy = {};
        const p = 1.0 / actions.length;
        for (const a of actions) strategy[a] = p;
      }

      let value = 0;
      for (const action of actions) {
        const prob = strategy[action] || 0;
        if (prob > 0) {
          const next = this.game.applyAction(state, action);
          value += prob * this._bestResponseValue(next, brPlayer);
        }
      }
      return value;
    }
  }

  // ── Persistence ──────────────────────────────────────────────────────

  /**
   * Save the trained strategy to a JSON file.
   * Saves both regret sums and strategy sums for continued training,
   * plus the average strategy for play.
   */
  save(filePath) {
    const data = {
      iterations: this.iterations,
      numInfoSets: this.regretSum.size,
      regretSum: Object.fromEntries(this.regretSum),
      strategySum: Object.fromEntries(this.strategySum),
    };

    const dir = path.dirname(filePath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    fs.writeFileSync(filePath, JSON.stringify(data, null, 2));
  }

  /**
   * Load a previously trained strategy.
   */
  load(filePath) {
    const raw = fs.readFileSync(filePath, "utf8");
    const data = JSON.parse(raw);

    this.iterations = data.iterations || 0;
    this.regretSum = new Map(Object.entries(data.regretSum || {}));
    this.strategySum = new Map(Object.entries(data.strategySum || {}));
  }

  /**
   * Export the average strategy as a compact lookup table.
   * This is what a bot loads for actual play.
   */
  exportStrategy() {
    const result = {};
    for (const [infoSet, sums] of this.strategySum) {
      const avg = this.getAverageStrategy(infoSet);
      if (avg) {
        result[infoSet] = avg;
      }
    }
    return result;
  }

  /**
   * Print a summary of the learned strategy for inspection.
   */
  printStrategySummary() {
    const entries = [...this.strategySum.keys()].sort();
    console.log(`\n=== CFR Strategy Summary (${this.iterations} iterations, ${entries.length} info sets) ===\n`);

    for (const infoSet of entries) {
      const avg = this.getAverageStrategy(infoSet);
      if (!avg) continue;

      const parts = [];
      for (const [action, prob] of Object.entries(avg)) {
        if (prob > 0.001) {
          parts.push(`${action}:${(prob * 100).toFixed(1)}%`);
        }
      }
      console.log(`  ${infoSet.padEnd(20)} ${parts.join("  ")}`);
    }
  }
}

module.exports = { CFRTrainer };
