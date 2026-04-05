#!/usr/bin/env node
"use strict";

/**
 * Anti-bot detection feature extraction and analysis.
 *
 * Runs bot strategies through the engine and records detailed decision-level
 * data, then computes detection signals:
 *
 *   1. Timing entropy — how variable are decision times?
 *   2. Bet sizing precision — exact pot fractions vs human-like rounding?
 *   3. Action distribution stability — VPIP/PFR/AF consistency across sessions
 *   4. Positional awareness — does strategy change by position?
 *   5. Tilt resistance — does play change after bad beats?
 *   6. Bet sizing clustering — how many distinct bet sizes are used?
 *
 * Usage:
 *   node scripts/bot-detector.js                           # profile all bots
 *   node scripts/bot-detector.js --strategies tag,cfr50    # specific bots
 *   node scripts/bot-detector.js --hands 10000             # more hands
 */

const { createGame, ACTION, PHASE } = require("../src/index");
const { getLegalActions } = require("../src/engine/betting");
const fs = require("fs");
const path = require("path");

// ── RNG ────────────────────────────────────────────────────────────────

function createRng(seed = 42) {
  let s = seed;
  return function () {
    s = (s * 1664525 + 1013904223) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

// ── Hand Strength ──────────────────────────────────────────────────────

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

// ── Strategies (same as eval-bots.js) ──────────────────────────────────

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
    if (strength > 0.7 && actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
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

function fishStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };
  const cards = seatState.holeCards || [];
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);
  if (strength > 0.8 && actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: minRaise };
  if (strength > 0.15) {
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  }
  if (rng() < 0.3 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  return { action: ACTION.FOLD };
}

function lagStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };
  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);
  if (hand.phase === PHASE.PREFLOP) {
    if (strength > 0.4 && actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.75), maxRaise)) };
    if (strength > 0.2 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (rng() < 0.15 && actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: minRaise };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }
  if (strength > 0.5) {
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize), maxRaise)) };
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.min(Math.floor(potSize * 0.75), seatState.stack)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  if (rng() < 0.25) {
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.5)) };
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: minRaise };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.4) return { action: ACTION.CALL };
  return { action: ACTION.FOLD };
}

let _cfr50Fn = null;
function cfr50Strategy(seat, legal, state, rng) {
  if (!_cfr50Fn) {
    const { createCFRStrategy } = require("./cfr/cfr-bot");
    _cfr50Fn = createCFRStrategy("./vision/models/cfr_strategy_50bucket.json");
  }
  return _cfr50Fn(seat, legal, state, rng);
}

// ── Humanization wrapper ────────────────────────────────────────────────
// Adds bet size noise and tilt simulation to any strategy

function humanizeBetSize(amount, pot, minBet, maxRaise, rng) {
  if (!amount || amount <= 0 || pot <= 0) return amount;
  const r = rng();

  // 40%: absolute dollar amounts humans type at micros
  if (r < 0.40) {
    const humanAmounts = [15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 75, 80, 100, 125, 150, 175, 200];
    let best = humanAmounts[0], bestDist = Math.abs(amount - best);
    for (const ha of humanAmounts) {
      const dist = Math.abs(amount - ha);
      if (dist < bestDist) { best = ha; bestDist = dist; }
    }
    best += Math.floor(rng() * 7) - 3;
    return Math.max(minBet || amount, Math.min(best, maxRaise || amount));
  }

  // 35%: jitter ±8-20% plus random offset to break fractions
  if (r < 0.75) {
    const jitterPct = 0.08 + rng() * 0.12;
    const direction = rng() < 0.5 ? 1 : -1;
    let humanized = Math.round(amount * (1 + direction * jitterPct));
    humanized += Math.floor(rng() * 5) + 1;
    return Math.max(minBet || amount, Math.min(humanized, maxRaise || amount));
  }

  // 25%: slider-style, nudge off exact pot fractions
  let humanized = amount + Math.floor(rng() * 20) - 10;
  humanized = Math.round(humanized / 5) * 5;
  const frac = humanized / pot;
  const COMMON = [0.25, 0.33, 0.5, 0.66, 0.67, 0.75, 1.0, 1.5, 2.0];
  if (COMMON.some(cf => Math.abs(frac - cf) < 0.02)) {
    humanized += (rng() < 0.5 ? 3 : -3);
  }
  return Math.max(minBet || amount, Math.min(humanized, maxRaise || amount));
}

function createHumanizedStrategy(baseFn) {
  let tiltLevel = 0;
  const recentResults = [];

  return function humanizedStrategy(seat, legal, state, rng) {
    let decision = baseFn(seat, legal, state, rng);
    if (!decision) return decision;

    const { actions, minBet, minRaise, maxRaise } = legal;
    const pot = state.hand.pot || 0;

    // Apply tilt: loosen calls, increase aggression after losses
    if (tiltLevel > 0.2) {
      if (decision.action === "FOLD" && tiltLevel > 0.3 && rng() < tiltLevel * 0.5) {
        if (actions.includes("CALL")) decision = { action: "CALL" };
      }
      if (decision.action === "CALL" && tiltLevel > 0.5 && rng() < tiltLevel * 0.3) {
        if (actions.includes("RAISE") && minRaise) {
          decision = { action: "RAISE", amount: maxRaise || minRaise };
        }
      }
    }

    // Humanize bet sizing
    if (decision.amount && (decision.action === "BET" || decision.action === "RAISE")) {
      decision = { ...decision };
      decision.amount = humanizeBetSize(
        decision.amount, pot,
        decision.action === "BET" ? minBet : minRaise,
        maxRaise, rng
      );
    }

    return decision;
  };
}

// Tilt updater — called from profileStrategy after each hand
function updateTiltState(strategyState, profitBB) {
  strategyState.recentResults.push(profitBB);
  if (strategyState.recentResults.length > 10) strategyState.recentResults.shift();
  const recentSum = strategyState.recentResults.reduce((a, b) => a + b, 0);
  const consecutiveLosses = strategyState.recentResults.slice().reverse().findIndex(x => x >= 0);
  const lossStreak = consecutiveLosses === -1 ? strategyState.recentResults.length : consecutiveLosses;
  strategyState.tiltLevel = Math.min(1, Math.max(0, -recentSum / 30 + lossStreak * 0.1));
}

// Screen-reading bot strategy v2 — humanized decision logic
// Fixes: varied bet sizing, higher VPIP, tilt simulation, bet rounding
function screenbotStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };
  const cards = seatState.holeCards || [];
  const phase = hand.phase;
  const potSize = hand.pot || 0;
  const stack = seatState.stack || 1000;
  const facingBet = actions.includes(ACTION.CALL);

  // Tilt state (tracked externally via humanized wrapper, but add baseline here)
  const tiltBonus = (rng() < 0.08) ? 0.15 : 0; // 8% chance of "tilty" play

  // --- BET SIZING: pick from weighted pool ---
  function pickBetSize() {
    const r = rng();
    let amount;
    if (r < 0.30)      amount = Math.floor(potSize * 0.50);  // half pot (30%)
    else if (r < 0.60) amount = Math.floor(potSize * 0.66);  // 2/3 pot (30%)
    else if (r < 0.85) amount = Math.floor(potSize * 1.0);   // pot (25%)
    else                amount = Math.floor(potSize * 1.5);   // overbet (15%)

    // Add noise ±10%
    const noise = 1.0 + (rng() - 0.5) * 0.20;
    amount = Math.floor(amount * noise);

    // Round to nearest 5 cents (like humans)
    amount = Math.round(amount / 5) * 5;

    // Clamp to legal range
    return Math.max(minRaise || minBet || amount, Math.min(amount, maxRaise || stack));
  }

  // Preflop
  if (phase === PHASE.PREFLOP) {
    const strength = evaluateHandStrength(cards, [], phase);
    // Higher VPIP: lower threshold from 0.40 to 0.28 + tilt
    const threshold = 0.28 - tiltBonus;

    if (strength > 0.65 && actions.includes(ACTION.RAISE)) {
      // Varied preflop raise sizing: 2.5-3.5x BB
      const bbSize = 10; // cents
      const multiplier = 2.5 + rng() * 1.0;
      let raiseAmt = Math.floor(bbSize * multiplier);
      raiseAmt = Math.round(raiseAmt / 5) * 5;
      raiseAmt = Math.max(minRaise, Math.min(raiseAmt, maxRaise));
      return { action: ACTION.RAISE, amount: raiseAmt };
    }
    if (strength > threshold && facingBet && actions.includes(ACTION.CALL))
      return { action: ACTION.CALL };
    if (strength > threshold && actions.includes(ACTION.CHECK))
      return { action: ACTION.CHECK };
    // Occasional speculative call with very weak hands (2% — curiosity)
    if (rng() < 0.02 && facingBet && actions.includes(ACTION.CALL))
      return { action: ACTION.CALL };
    return { action: ACTION.FOLD };
  }

  // Postflop: equity-based with varied sizing
  const strength = evaluateHandStrength(cards, hand.board || [], phase);
  const potOdds = potSize > 0 && callAmount > 0 ? callAmount / (potSize + callAmount) : 0;

  if (strength > 0.70 + tiltBonus * 0.5) {
    // Strong: bet/raise with varied sizing
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: pickBetSize() };
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: pickBetSize() };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  if (strength > 0.45 - tiltBonus) {
    // Medium: bet sometimes, check/call facing bets
    if (!facingBet) {
      // Bet for value ~40% of the time with medium hands
      if (rng() < 0.40 && actions.includes(ACTION.BET))
        return { action: ACTION.BET, amount: pickBetSize() };
      return { action: ACTION.CHECK };
    }
    if (facingBet && strength > potOdds && actions.includes(ACTION.CALL))
      return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }
  if (strength > 0.25) {
    // Drawing: check, call with odds
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (facingBet && strength > potOdds * 0.9 && actions.includes(ACTION.CALL))
      return { action: ACTION.CALL };
    return { action: ACTION.FOLD };
  }
  // Weak: occasional bluff (5%)
  if (rng() < 0.05 && actions.includes(ACTION.BET))
    return { action: ACTION.BET, amount: pickBetSize() };
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  return { action: ACTION.FOLD };
}

const STRATEGIES = {
  tag:   { name: "TAG",    fn: tagStrategy },
  fish:  { name: "FISH",   fn: fishStrategy },
  lag:   { name: "LAG",    fn: lagStrategy },
  cfr50: { name: "CFR-50", fn: cfr50Strategy },
  screenbot: { name: "SCREENBOT", fn: screenbotStrategy },
  // Humanized versions
  "tag-h":   { name: "TAG-H",    fn: createHumanizedStrategy(tagStrategy) },
  "fish-h":  { name: "FISH-H",   fn: createHumanizedStrategy(fishStrategy) },
  "lag-h":   { name: "LAG-H",    fn: createHumanizedStrategy(lagStrategy) },
  "cfr50-h": { name: "CFR50-H",  fn: createHumanizedStrategy(cfr50Strategy) },
  "screenbot-h": { name: "SCREENBOT-H", fn: createHumanizedStrategy(screenbotStrategy) },
};

// ── Decision Recorder ──────────────────────────────────────────────────

class DecisionRecorder {
  constructor(name) {
    this.name = name;
    this.decisions = [];       // all decisions for detailed analysis
    this.sessionStats = [];    // per-session VPIP/PFR/AF blocks
    this.currentSession = { hands: 0, vpip: 0, pfr: 0, bets: 0, raises: 0, calls: 0, folds: 0, checks: 0 };
    this.recentResults = [];   // last N hand profits for tilt analysis
  }

  record(decision, context) {
    this.decisions.push({
      action: decision.action,
      amount: decision.amount || 0,
      pot: context.pot,
      stack: context.stack,
      phase: context.phase,
      position: context.position,
      strength: context.strength,
      callAmount: context.callAmount,
      handProfit: 0, // filled in after hand
    });

    // Track session stats
    const a = decision.action;
    if (context.phase === "PREFLOP") {
      if (a === "CALL" || a === "RAISE" || a === "BET") this.currentSession.vpip++;
      if (a === "RAISE") this.currentSession.pfr++;
    }
    if (a === "BET" || a === "RAISE") this.currentSession.bets++;
    if (a === "CALL") this.currentSession.calls++;
    if (a === "FOLD") this.currentSession.folds++;
    if (a === "CHECK") this.currentSession.checks++;
  }

  endHand(profit) {
    this.currentSession.hands++;
    this.recentResults.push(profit);

    // Mark last few decisions with hand profit (for tilt analysis)
    for (let i = this.decisions.length - 1; i >= 0 && i >= this.decisions.length - 10; i--) {
      if (this.decisions[i].handProfit === 0) {
        this.decisions[i].handProfit = profit;
      } else break;
    }

    // Save session every 500 hands
    if (this.currentSession.hands >= 500) {
      this.sessionStats.push({ ...this.currentSession });
      this.currentSession = { hands: 0, vpip: 0, pfr: 0, bets: 0, raises: 0, calls: 0, folds: 0, checks: 0 };
    }
  }

  finalize() {
    if (this.currentSession.hands > 0) {
      this.sessionStats.push({ ...this.currentSession });
    }
  }
}

// ── Feature Extraction ─────────────────────────────────────────────────

function extractFeatures(recorder) {
  const d = recorder.decisions;
  const sessions = recorder.sessionStats;
  if (d.length === 0) return null;

  const BB = 10;
  const features = {};

  // ── 1. Bet Sizing Precision ──────────────────────────────────────
  // Bots use exact pot fractions; humans round to convenient amounts
  const betDecisions = d.filter(x => (x.action === "BET" || x.action === "RAISE") && x.amount > 0 && x.pot > 0);
  if (betDecisions.length > 0) {
    const potFractions = betDecisions.map(x => x.amount / x.pot);

    // How many bets are exact common fractions (0.33, 0.5, 0.66, 0.75, 1.0)?
    const COMMON_FRACTIONS = [0.25, 0.33, 0.5, 0.66, 0.67, 0.75, 1.0, 1.5, 2.0];
    const TOLERANCE = 0.02;
    let exactFractionCount = 0;
    for (const frac of potFractions) {
      if (COMMON_FRACTIONS.some(cf => Math.abs(frac - cf) < TOLERANCE)) {
        exactFractionCount++;
      }
    }
    features.betSizePrecision = exactFractionCount / potFractions.length;

    // Number of distinct bet sizes (bots use few; humans use many)
    const roundedSizes = betDecisions.map(x => Math.round(x.amount / 5) * 5); // round to 5
    features.distinctBetSizes = new Set(roundedSizes).size;
    features.betSizeEntropy = shannonEntropy(roundedSizes);

    // Bet-to-pot ratio distribution
    const sortedFracs = potFractions.slice().sort((a, b) => a - b);
    features.betSizeMean = mean(potFractions);
    features.betSizeStd = stdev(potFractions);
    features.betSizeMedian = sortedFracs[Math.floor(sortedFracs.length / 2)];
  } else {
    features.betSizePrecision = 0;
    features.distinctBetSizes = 0;
    features.betSizeEntropy = 0;
    features.betSizeMean = 0;
    features.betSizeStd = 0;
    features.betSizeMedian = 0;
  }

  // ── 2. Action Distribution ───────────────────────────────────────
  const actionCounts = { FOLD: 0, CHECK: 0, CALL: 0, BET: 0, RAISE: 0 };
  for (const x of d) actionCounts[x.action] = (actionCounts[x.action] || 0) + 1;
  const total = d.length;
  features.foldPct = actionCounts.FOLD / total;
  features.checkPct = actionCounts.CHECK / total;
  features.callPct = actionCounts.CALL / total;
  features.betPct = actionCounts.BET / total;
  features.raisePct = actionCounts.RAISE / total;
  features.aggressionFactor = (actionCounts.BET + actionCounts.RAISE) / Math.max(1, actionCounts.CALL);

  // ── 3. Session-to-Session Stability ──────────────────────────────
  // Bots have very consistent VPIP/PFR across sessions; humans drift
  if (sessions.length >= 2) {
    const vpipRates = sessions.map(s => s.hands > 0 ? s.vpip / s.hands : 0);
    const pfrRates = sessions.map(s => s.hands > 0 ? s.pfr / s.hands : 0);
    const afRates = sessions.map(s => {
      const agg = s.bets + s.raises;
      return agg / Math.max(1, s.calls);
    });

    features.vpipStability = stdev(vpipRates);   // low = bot-like
    features.pfrStability = stdev(pfrRates);      // low = bot-like
    features.afStability = stdev(afRates);        // low = bot-like
    features.vpipMean = mean(vpipRates);
    features.pfrMean = mean(pfrRates);
  } else {
    features.vpipStability = 0;
    features.pfrStability = 0;
    features.afStability = 0;
    features.vpipMean = 0;
    features.pfrMean = 0;
  }

  // ── 4. Positional Awareness ──────────────────────────────────────
  // Good bots/players adjust by position; random bots don't
  const positionGroups = {};
  for (const x of d) {
    if (x.phase !== "PREFLOP") continue;
    const pos = x.position;
    if (!positionGroups[pos]) positionGroups[pos] = { total: 0, voluntary: 0, raise: 0 };
    positionGroups[pos].total++;
    if (x.action === "CALL" || x.action === "RAISE" || x.action === "BET") positionGroups[pos].voluntary++;
    if (x.action === "RAISE") positionGroups[pos].raise++;
  }
  const posVPIPs = Object.values(positionGroups).filter(g => g.total > 10).map(g => g.voluntary / g.total);
  features.positionalAwareness = posVPIPs.length >= 2 ? stdev(posVPIPs) : 0; // high = position-aware

  // ── 5. Tilt Resistance ───────────────────────────────────────────
  // After big losses, humans play looser/more aggressive. Bots don't.
  // Compare action distribution after big losses vs normal
  const results = recorder.recentResults;
  if (results.length > 100) {
    const bigLossThreshold = -5 * BB; // losing 5+ BB in a hand
    let afterLossAgg = 0, afterLossTotal = 0;
    let normalAgg = 0, normalTotal = 0;

    for (let i = 1; i < results.length && i < d.length; i++) {
      const prevResult = results[i - 1];
      const action = d[Math.min(i * 3, d.length - 1)]; // rough mapping
      const isAgg = action && (action.action === "BET" || action.action === "RAISE");

      if (prevResult < bigLossThreshold) {
        afterLossTotal++;
        if (isAgg) afterLossAgg++;
      } else {
        normalTotal++;
        if (isAgg) normalAgg++;
      }
    }

    const afterLossAggRate = afterLossTotal > 10 ? afterLossAgg / afterLossTotal : 0;
    const normalAggRate = normalTotal > 10 ? normalAgg / normalTotal : 0;
    features.tiltResistance = Math.abs(afterLossAggRate - normalAggRate); // low = bot-like (no tilt)
  } else {
    features.tiltResistance = 0;
  }

  // ── 6. Decision Complexity Correlation ───────────────────────────
  // Bots decide equally fast for easy and hard spots.
  // Since we can't measure time in self-play, use hand strength variance
  // as a proxy: bots fold trash and value bet strong equally "confidently"
  const foldStrengths = d.filter(x => x.action === "FOLD").map(x => x.strength);
  const raiseStrengths = d.filter(x => x.action === "RAISE" || x.action === "BET").map(x => x.strength);
  if (foldStrengths.length > 10 && raiseStrengths.length > 10) {
    features.foldStrengthMean = mean(foldStrengths);
    features.foldStrengthStd = stdev(foldStrengths);
    features.raiseStrengthMean = mean(raiseStrengths);
    features.raiseStrengthStd = stdev(raiseStrengths);
    // Separation: how cleanly does strength predict action?
    // High separation = deterministic bot. Low = mixed/human-like
    features.strengthSeparation = features.raiseStrengthMean - features.foldStrengthMean;
  } else {
    features.foldStrengthMean = 0;
    features.foldStrengthStd = 0;
    features.raiseStrengthMean = 0;
    features.raiseStrengthStd = 0;
    features.strengthSeparation = 0;
  }

  // ── 7. Bot Score (composite) ─────────────────────────────────────
  // Higher = more bot-like
  features.botScore =
    features.betSizePrecision * 30 +           // exact pot fractions
    (1 - Math.min(features.vpipStability * 20, 1)) * 20 + // stable sessions
    (1 - Math.min(features.tiltResistance * 10, 1)) * 15 + // no tilt
    (features.strengthSeparation > 0.3 ? 15 : 0) +        // deterministic
    (features.distinctBetSizes < 5 ? 10 : 0) +             // few bet sizes
    (features.betSizeEntropy < 2 ? 10 : 0);                // low sizing entropy

  return features;
}

// ── Math Helpers ───────────────────────────────────────────────────────

function mean(arr) {
  return arr.length > 0 ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
}

function stdev(arr) {
  if (arr.length < 2) return 0;
  const m = mean(arr);
  const variance = arr.reduce((sum, x) => sum + (x - m) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(variance);
}

function shannonEntropy(arr) {
  const counts = {};
  for (const x of arr) counts[x] = (counts[x] || 0) + 1;
  const total = arr.length;
  let entropy = 0;
  for (const c of Object.values(counts)) {
    const p = c / total;
    if (p > 0) entropy -= p * Math.log2(p);
  }
  return entropy;
}

// ── Profile Runner ─────────────────────────────────────────────────────

function profileStrategy(strategyKey, numHands, seed) {
  const strategy = STRATEGIES[strategyKey];
  if (!strategy) return null;

  const BB = 10;
  const numSeats = 6;
  const startStack = 1000;
  const rng = createRng(seed);

  // Put the target strategy in seat 0, fill rest with TAG
  const game = createGame(
    { tableId: "detect", tableName: "Detect", maxSeats: numSeats, sb: 5, bb: BB, minBuyIn: 100, maxBuyIn: 50000 },
    { sessionId: `detect-${seed}`, logPath: null, rng }
  );

  const botNames = [strategy.name, "TAG_1", "TAG_2", "TAG_3", "TAG_4", "TAG_5"];
  for (let i = 0; i < numSeats; i++) {
    game.sitDown(i, botNames[i], startStack);
  }

  const recorder = new DecisionRecorder(strategy.name);

  for (let h = 0; h < numHands; h++) {
    // Rebuy
    try {
      const st = game.getState();
      for (let i = 0; i < numSeats; i++) {
        const s = st.table.seats[i];
        if (s && s.stack < 20) { game.leave(i); game.sitDown(i, botNames[i], startStack); }
      }
    } catch (e) {}

    try { game.startHand(); } catch (e) { continue; }

    const preStack = game.getState().table.seats[0] ? game.getState().table.seats[0].stack : startStack;

    let actionCount = 0;
    while (!game.isHandComplete() && actionCount < 100) {
      const actionSeat = game.getActionSeat();
      if (actionSeat === null) break;
      const state = game.getState();
      const seatState = state.table.seats[actionSeat];
      if (!seatState || !seatState.inHand) break;
      const legal = getLegalActions(seatState, state.hand, state.table.bb);
      if (!legal.actions.length) break;

      const stratFn = actionSeat === 0 ? strategy.fn : tagStrategy;
      const decision = stratFn(actionSeat, legal, state, rng);
      if (!decision) break;

      // Record seat 0's decisions
      if (actionSeat === 0) {
        const cards = seatState.holeCards || [];
        const strength = evaluateHandStrength(cards, state.hand.board || [], state.hand.phase);
        const dealer = state.hand.dealer ?? 0;
        const relPos = ((0 - dealer) % numSeats + numSeats) % numSeats;
        const posNames = ["BTN", "SB", "BB", "UTG", "MP", "CO"];

        recorder.record(decision, {
          pot: state.hand.pot || 0,
          stack: seatState.stack || 0,
          phase: state.hand.phase,
          position: posNames[relPos] || "?",
          strength,
          callAmount: legal.callAmount || 0,
        });
      }

      try {
        game.act(actionSeat, decision.action, decision.amount);
      } catch (e) {
        try { game.act(actionSeat, ACTION.FOLD); } catch (_) {}
      }
      actionCount++;
    }

    // End of hand — record profit for seat 0
    const postState = game.getState();
    const postStack = postState.table.seats[0] ? postState.table.seats[0].stack : 0;
    recorder.endHand(postStack - preStack);
  }

  recorder.finalize();
  return recorder;
}

// ── Main ───────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const opts = {};
for (let i = 0; i < args.length; i += 2) {
  const key = args[i] ? args[i].replace("--", "") : "";
  const val = args[i + 1];
  if (key === "hands") opts.hands = parseInt(val);
  else if (key === "strategies") opts.strategies = val;
}

const numHands = opts.hands || 5000;
const strategyKeys = opts.strategies
  ? opts.strategies.split(",").filter(k => STRATEGIES[k])
  : Object.keys(STRATEGIES);

console.log("=".repeat(70));
console.log("  ANTI-BOT DETECTION — Feature Extraction");
console.log("=".repeat(70));
console.log(`  Strategies: ${strategyKeys.map(k => STRATEGIES[k].name).join(", ")}`);
console.log(`  Hands per profile: ${numHands}`);
console.log();

const profiles = {};

for (const key of strategyKeys) {
  process.stdout.write(`  Profiling ${STRATEGIES[key].name}...`);
  const recorder = profileStrategy(key, numHands, 42);
  const features = extractFeatures(recorder);
  profiles[key] = features;
  console.log(` done (${recorder.decisions.length} decisions)`);
}

// ── Print Detection Report ─────────────────────────────────────────────

console.log("\n" + "=".repeat(70));
console.log("  DETECTION SIGNALS");
console.log("=".repeat(70));

const featureLabels = {
  betSizePrecision:    "Bet Size Precision",
  distinctBetSizes:    "Distinct Bet Sizes",
  betSizeEntropy:      "Bet Size Entropy",
  aggressionFactor:    "Aggression Factor",
  vpipMean:            "VPIP",
  pfrMean:             "PFR",
  vpipStability:       "VPIP Stability (σ)",
  pfrStability:        "PFR Stability (σ)",
  positionalAwareness: "Position Awareness",
  tiltResistance:      "Tilt Resistance",
  strengthSeparation:  "Strength Separation",
  botScore:            "BOT SCORE",
};

// Header
const nameWidth = 22;
const colWidth = 10;
let header = "  " + "Signal".padEnd(nameWidth);
for (const k of strategyKeys) header += STRATEGIES[k].name.padStart(colWidth);
console.log("\n" + header);
console.log("  " + "-".repeat(nameWidth + strategyKeys.length * colWidth));

for (const [fKey, label] of Object.entries(featureLabels)) {
  let line = "  " + label.padEnd(nameWidth);
  for (const sKey of strategyKeys) {
    const val = profiles[sKey][fKey];
    if (fKey === "botScore") {
      line += String(Math.round(val)).padStart(colWidth);
    } else if (fKey === "distinctBetSizes") {
      line += String(val).padStart(colWidth);
    } else if (val >= 10) {
      line += val.toFixed(1).padStart(colWidth);
    } else {
      line += val.toFixed(3).padStart(colWidth);
    }
  }
  if (fKey === "botScore") line = "\n" + line; // visual separator
  console.log(line);
}

console.log("\n" + "=".repeat(70));
console.log("  Bot Score: 0-100 (higher = more bot-like)");
console.log("  Key signals: high bet precision, low stability σ, no tilt, few bet sizes");
console.log("=".repeat(70));

// Save
const outPath = path.join(__dirname, "..", "vision", "data", "detection_profiles.json");
fs.writeFileSync(outPath, JSON.stringify({ timestamp: new Date().toISOString(), numHands, profiles }, null, 2));
console.log(`\n  Profiles saved to ${outPath}`);
