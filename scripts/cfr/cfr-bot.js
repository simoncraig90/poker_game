"use strict";

/**
 * CFR Bot adapter for bot-players.js integration.
 *
 * Loads a trained CFR strategy (from cfr_strategy.json) and maps
 * live game state into information set keys to look up action probabilities.
 *
 * Usage in bot-players.js:
 *   const { createCFRStrategy } = require("./cfr/cfr-bot");
 *   const cfrStrategy = createCFRStrategy("./vision/models/cfr_strategy.json");
 *   // Then in bot decision:
 *   const decision = cfrStrategy(seat, legal, state);
 */

const fs = require("fs");
const path = require("path");
const { evaluateHandStrength, strengthToBucket, makeInfoSetKey, encodeAction } = require("./abstraction");

const NUM_BUCKETS = 10;

/**
 * Create a CFR strategy function that can be used as a bot strategy.
 *
 * @param {string} strategyPath - path to cfr_strategy.json
 * @returns {Function} strategy(seat, legal, state, rng) -> { action, amount? }
 */
function createCFRStrategy(strategyPath) {
  const fullPath = path.resolve(strategyPath);
  if (!fs.existsSync(fullPath)) {
    throw new Error(`CFR strategy file not found: ${fullPath}`);
  }

  const strategyTable = JSON.parse(fs.readFileSync(fullPath, "utf8"));
  console.log(`[CFR Bot] Loaded strategy with ${Object.keys(strategyTable).length} info sets`);

  return function cfrStrategy(seat, legal, state, rng) {
    const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
    const rand = rng || Math.random;

    if (actions.length === 0) return null;
    if (actions.length === 1) return { action: actions[0] };

    // Build information set key from live game state
    const hand = state.hand;
    const seatState = state.table.seats[seat];
    const cards = seatState.holeCards || [];
    const board = hand.board || [];
    const phase = hand.phase;

    // Card bucket
    const strength = evaluateHandStrength(cards, board, phase);
    const bucket = strengthToBucket(strength, NUM_BUCKETS);

    // Action history: encode the hand's action sequence
    // Filter to only player actions (not blinds)
    const actionHistory = (hand.actions || [])
      .filter(a => a.type !== "BLIND_SB" && a.type !== "BLIND_BB")
      .map(a => encodeAction(a.type))
      .join("");

    const infoSetKey = makeInfoSetKey(bucket, actionHistory);

    // Look up strategy
    const strategy = strategyTable[infoSetKey];

    if (!strategy) {
      // No strategy for this info set: fall back to simple heuristic
      return fallbackStrategy(actions, strength, callAmount, minBet, minRaise, maxRaise, rand);
    }

    // Map CFR action names to engine action names
    // CFR uses: FOLD, CHECK, CALL, BET_HALF, BET_POT, BET_ALLIN, RAISE_HALF, RAISE_POT, RAISE_ALLIN
    // Engine uses: FOLD, CHECK, CALL, BET, RAISE
    const actionProbs = [];
    for (const engineAction of actions) {
      let prob = 0;
      if (engineAction === "FOLD") prob = strategy["FOLD"] || 0;
      else if (engineAction === "CHECK") prob = strategy["CHECK"] || 0;
      else if (engineAction === "CALL") prob = strategy["CALL"] || 0;
      else if (engineAction === "BET") prob = (strategy["BET_HALF"] || 0) + (strategy["BET_POT"] || 0) + (strategy["BET_ALLIN"] || 0);
      else if (engineAction === "RAISE") prob = (strategy["RAISE_HALF"] || 0) + (strategy["RAISE_POT"] || 0) + (strategy["RAISE_ALLIN"] || 0);
      actionProbs.push({ action: engineAction, prob });
    }

    // Normalize (in case not all CFR actions map to legal engine actions)
    const totalProb = actionProbs.reduce((sum, ap) => sum + ap.prob, 0);
    if (totalProb <= 0) {
      return fallbackStrategy(actions, strength, callAmount, minBet, minRaise, maxRaise, rand);
    }

    // Sample from distribution
    const r = rand();
    let cumulative = 0;
    let chosen = actionProbs[actionProbs.length - 1].action;
    for (const ap of actionProbs) {
      cumulative += ap.prob / totalProb;
      if (r < cumulative) {
        chosen = ap.action;
        break;
      }
    }

    // Add amount for bet/raise — pick sizing from CFR sub-actions
    if (chosen === "BET") {
      const potSize = hand.pot || 1;
      const halfProb = strategy["BET_HALF"] || 0;
      const potProb = strategy["BET_POT"] || 0;
      const allProb = strategy["BET_ALLIN"] || 0;
      const total = halfProb + potProb + allProb;
      const r2 = rand() * total;
      let betAmount;
      if (r2 < halfProb) betAmount = Math.round(potSize * 0.5);
      else if (r2 < halfProb + potProb) betAmount = Math.round(potSize);
      else betAmount = seatState.stack; // all-in
      return { action: chosen, amount: Math.max(minBet, Math.min(betAmount, seatState.stack)) };
    }
    if (chosen === "RAISE") {
      const potSize = hand.pot || 1;
      const halfProb = strategy["RAISE_HALF"] || 0;
      const potProb = strategy["RAISE_POT"] || 0;
      const allProb = strategy["RAISE_ALLIN"] || 0;
      const total = halfProb + potProb + allProb;
      const r2 = rand() * total;
      let raiseAmount;
      if (r2 < halfProb) raiseAmount = callAmount + Math.round(potSize * 0.5);
      else if (r2 < halfProb + potProb) raiseAmount = callAmount + Math.round(potSize);
      else raiseAmount = seatState.stack; // all-in
      return { action: chosen, amount: Math.max(minRaise, Math.min(raiseAmount, maxRaise)) };
    }

    return { action: chosen };
  };
}

/**
 * Fallback strategy when no CFR data is available for the info set.
 * Uses a simple strength-based heuristic.
 */
function fallbackStrategy(actions, strength, callAmount, minBet, minRaise, maxRaise, rng) {
  if (strength > 0.7) {
    if (actions.includes("RAISE")) return { action: "RAISE", amount: minRaise };
    if (actions.includes("BET")) return { action: "BET", amount: minBet };
    if (actions.includes("CALL")) return { action: "CALL" };
    return { action: "CHECK" };
  }
  if (strength > 0.4) {
    if (actions.includes("CHECK")) return { action: "CHECK" };
    if (actions.includes("CALL")) return { action: "CALL" };
    return { action: "FOLD" };
  }
  // Weak hand
  if (actions.includes("CHECK")) return { action: "CHECK" };
  return { action: "FOLD" };
}

module.exports = { createCFRStrategy };
