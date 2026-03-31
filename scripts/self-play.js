#!/usr/bin/env node
"use strict";

/**
 * Self-play poker bot runner.
 * Runs bots against each other at maximum speed using the engine directly.
 * Supports multiple strategy types and tracks results.
 *
 * Usage:
 *   node scripts/self-play.js                     # 10k hands, 2 bots
 *   node scripts/self-play.js --hands 100000      # 100k hands
 *   node scripts/self-play.js --seats 6           # 6-max
 *   node scripts/self-play.js --strategy neural   # neural net bot (when trained)
 */

const { createGame, ACTION, PHASE } = require("../src/index");
const { getLegalActions } = require("../src/engine/betting");
const fs = require("fs");
const path = require("path");

// ── Bot Strategies ──────────────────────────────────────────────────────

/**
 * Random bot: picks a random legal action.
 * Useful as a baseline — any learning bot should beat this.
 */
function randomStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const pick = actions[Math.floor(rng() * actions.length)];

  if (pick === ACTION.BET) return { action: pick, amount: minBet };
  if (pick === ACTION.RAISE) return { action: pick, amount: minRaise };
  return { action: pick };
}

/**
 * TAG bot (Tight-Aggressive): plays solid fundamentals.
 * - Folds weak hands preflop
 * - Bets/raises with strong hands
 * - Calls with medium hands
 * - Bluffs occasionally
 */
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

  // Simple hand strength heuristic
  let strength = evaluateHandStrength(cards, hand.board || [], phase);

  // Preflop: fold weak hands, raise strong
  if (phase === PHASE.PREFLOP) {
    if (strength > 0.7 && actions.includes(ACTION.RAISE)) {
      const raiseAmt = Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise);
      return { action: ACTION.RAISE, amount: Math.max(minRaise, raiseAmt) };
    }
    if (strength > 0.35 && actions.includes(ACTION.CALL)) {
      return { action: ACTION.CALL };
    }
    if (strength > 0.35 && actions.includes(ACTION.CHECK)) {
      return { action: ACTION.CHECK };
    }
    return { action: ACTION.FOLD };
  }

  // Postflop
  // Strong hand: bet/raise
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

  // Medium hand: check/call
  if (strength > 0.35) {
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.5) {
      return { action: ACTION.CALL };
    }
    // Occasional bluff bet
    if (rng() < 0.15 && actions.includes(ACTION.BET)) {
      return { action: ACTION.BET, amount: minBet };
    }
    return { action: ACTION.FOLD };
  }

  // Weak hand: check or fold
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };

  // Bluff ~10% of the time
  if (rng() < 0.10 && actions.includes(ACTION.BET)) {
    return { action: ACTION.BET, amount: minBet };
  }

  return { action: ACTION.FOLD };
}

/**
 * Simple hand strength evaluator.
 * Returns 0-1 score based on hole cards and board.
 */
function evaluateHandStrength(cards, board, phase) {
  if (!cards || cards.length < 2) return 0.5;

  const c1 = cards[0], c2 = cards[1];
  const r1 = c1.rank, r2 = c2.rank;
  const suited = c1.suit === c2.suit;
  const pair = r1 === r2;
  const highCard = Math.max(r1, r2);
  const gap = Math.abs(r1 - r2);
  const connected = gap === 1;

  // Preflop hand strength
  let pfStrength = 0;

  // Pocket pairs
  if (pair) {
    pfStrength = 0.5 + (r1 / 14) * 0.5; // AA=1.0, 22=0.57
  } else {
    // High cards
    pfStrength = (highCard / 14) * 0.4;
    if (suited) pfStrength += 0.08;
    if (connected) pfStrength += 0.06;
    if (gap <= 3) pfStrength += 0.03;
    // Broadway
    if (r1 >= 10 && r2 >= 10) pfStrength += 0.15;
    // Ace-x
    if (highCard === 14) pfStrength += 0.1;
  }

  if (phase === PHASE.PREFLOP) return Math.min(1, pfStrength);

  // Postflop: check board hits
  const boardRanks = board.map(c => c.rank);
  const boardSuits = board.map(c => c.suit);
  let postStrength = pfStrength;

  // Pair with board
  if (boardRanks.includes(r1)) postStrength += 0.25;
  if (boardRanks.includes(r2)) postStrength += 0.20;

  // Two pair
  if (boardRanks.includes(r1) && boardRanks.includes(r2) && !pair) {
    postStrength += 0.20;
  }

  // Trips
  if (pair && boardRanks.includes(r1)) postStrength += 0.35;

  // Flush draw
  const suitCount = boardSuits.filter(s => s === c1.suit).length;
  if (suitCount >= 2 && suited) postStrength += 0.12;
  if (suitCount >= 3 && (c1.suit === boardSuits[0] || c2.suit === boardSuits[0])) {
    postStrength += 0.30; // flush made
  }

  // Overpair
  if (pair && r1 > Math.max(...boardRanks)) postStrength += 0.15;

  return Math.min(1, postStrength);
}


// ── Self-Play Runner ────────────────────────────────────────────────────

function createRng(seed = 42) {
  let s = seed;
  return function() {
    s = (s * 1664525 + 1013904223) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

function runSelfPlay(opts = {}) {
  const numHands = opts.hands || 10000;
  const numSeats = opts.seats || 2;
  const startStack = opts.stack || 1000; // 100bb at 5/10
  const strategyName = opts.strategy || "tag";
  const seed = opts.seed || Date.now();
  const verbose = opts.verbose || false;

  const rng = createRng(seed);
  const strategies = [];
  const botNames = [];

  for (let i = 0; i < numSeats; i++) {
    botNames.push(`Bot${i + 1}`);
    if (strategyName === "random") {
      strategies.push(randomStrategy);
    } else if (strategyName === "tag") {
      strategies.push(tagStrategy);
    } else if (strategyName === "mixed") {
      // First bot is TAG, rest are random
      strategies.push(i === 0 ? tagStrategy : randomStrategy);
    } else {
      strategies.push(tagStrategy);
    }
  }

  const game = createGame(
    {
      tableId: "self-play",
      tableName: "Self-Play",
      maxSeats: numSeats,
      sb: 5,
      bb: 10,
      minBuyIn: 100,
      maxBuyIn: 50000,
    },
    { sessionId: `selfplay-${seed}`, logPath: null, rng }
  );

  // Seat all bots
  for (let i = 0; i < numSeats; i++) {
    game.sitDown(i, botNames[i], startStack);
  }

  // Tracking
  const results = botNames.map(name => ({
    name,
    handsPlayed: 0,
    profit: 0,
    vpip: 0,    // voluntarily put $ in pot
    pfr: 0,     // preflop raise
    wins: 0,
  }));

  const startTime = Date.now();
  let handsCompleted = 0;
  let errors = 0;

  // ── Main loop ──────────────────────────────────────
  for (let h = 0; h < numHands; h++) {
    // Check stacks — rebuy if busted
    const state = game.getState();
    for (let i = 0; i < numSeats; i++) {
      const s = state.table.seats[i];
      if (s && s.stack < 20) {
        // Rebuy to starting stack
        try {
          game.leave(i);
          game.sitDown(i, botNames[i], startStack);
        } catch (e) {
          // Can't leave during hand, skip
        }
      }
    }

    // Start hand
    try {
      game.startHand();
    } catch (e) {
      errors++;
      if (verbose) console.log(`Hand ${h}: start error: ${e.message}`);
      continue;
    }

    // Record starting stacks for profit calc
    const preState = game.getState();
    const preStacks = {};
    for (let i = 0; i < numSeats; i++) {
      const s = preState.table.seats[i];
      if (s) preStacks[i] = s.stack;
    }

    // Play the hand
    let actionCount = 0;
    const maxActions = 100; // safety limit

    while (!game.isHandComplete() && actionCount < maxActions) {
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

        // Track VPIP/PFR
        if (currentState.hand.phase === PHASE.PREFLOP) {
          if (decision.action === ACTION.CALL || decision.action === ACTION.RAISE || decision.action === ACTION.BET) {
            results[actionSeat].vpip++;
          }
          if (decision.action === ACTION.RAISE) {
            results[actionSeat].pfr++;
          }
        }
      } catch (e) {
        if (verbose) console.log(`Hand ${h}, seat ${actionSeat}: ${e.message}`);
        // Try to fold as fallback
        try { game.act(actionSeat, ACTION.FOLD); } catch (_) {}
        errors++;
      }

      actionCount++;
    }

    handsCompleted++;

    // Calculate profit
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

    // Progress update
    if ((h + 1) % 1000 === 0 || h === numHands - 1) {
      const elapsed = (Date.now() - startTime) / 1000;
      const hps = handsCompleted / elapsed;
      process.stdout.write(`\r  ${handsCompleted}/${numHands} hands (${Math.round(hps)} hands/sec)`);
    }
  }

  const totalElapsed = (Date.now() - startTime) / 1000;
  const handsPerSec = handsCompleted / totalElapsed;

  // ── Results ──────────────────────────────────────
  console.log("\n");
  console.log("═".repeat(60));
  console.log("SELF-PLAY RESULTS");
  console.log("═".repeat(60));
  console.log(`Hands: ${handsCompleted} | Time: ${totalElapsed.toFixed(1)}s | Speed: ${Math.round(handsPerSec)} hands/sec`);
  console.log(`Strategy: ${strategyName} | Seats: ${numSeats} | Errors: ${errors}`);
  console.log("─".repeat(60));

  for (const r of results) {
    const vpipPct = r.handsPlayed ? ((r.vpip / r.handsPlayed) * 100).toFixed(1) : "0";
    const pfrPct = r.handsPlayed ? ((r.pfr / r.handsPlayed) * 100).toFixed(1) : "0";
    const winRate = r.handsPlayed ? (r.profit / r.handsPlayed).toFixed(2) : "0";
    const bb100 = r.handsPlayed ? ((r.profit / 10) / (r.handsPlayed / 100)).toFixed(1) : "0";

    console.log(`  ${r.name}:`);
    console.log(`    Profit: ${r.profit > 0 ? "+" : ""}${r.profit} chips (${bb100} bb/100)`);
    console.log(`    Win rate: ${r.wins}/${r.handsPlayed} (${((r.wins/r.handsPlayed)*100).toFixed(1)}%)`);
    console.log(`    VPIP: ${vpipPct}% | PFR: ${pfrPct}%`);
  }

  console.log("═".repeat(60));

  return results;
}


// ── CLI ──────────────────────────────────────────────────────────────────

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
  else if (key === "verbose") { opts.verbose = true; i--; }
}

console.log("Poker Self-Play");
console.log("═".repeat(60));
console.log(`Config: ${opts.hands || 10000} hands, ${opts.seats || 2} seats, strategy=${opts.strategy || "tag"}`);
console.log();

runSelfPlay(opts);
