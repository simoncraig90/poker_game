#!/usr/bin/env node
"use strict";

/**
 * Generate Monte Carlo equity training data for the hand strength neural net.
 *
 * For each sample: deal random hero cards + random board, then run N simulations
 * against random opponent hands to compute true equity. Also tags board texture
 * features (paired, flush draw, straight possible, etc).
 *
 * Output: vision/data/equity_training_data.jsonl
 *
 * Usage:
 *   node scripts/generate-equity-data.js                    # 100k samples
 *   node scripts/generate-equity-data.js --samples 500000   # more samples
 */

const fs = require("fs");
const path = require("path");

const SAMPLES = parseInt(process.argv.find(a => a.startsWith("--samples="))?.split("=")[1] || "100000");
const SIMS_PER_SAMPLE = 200;  // Monte Carlo simulations per hand
const OUTPUT_PATH = path.join(__dirname, "..", "vision", "data", "equity_training_data.jsonl");

// ── Card representation ──────────────────────────────────────────────

const RANKS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14];
const SUITS = [0, 1, 2, 3];

function makeDeck() {
  const deck = [];
  for (const r of RANKS) {
    for (const s of SUITS) {
      deck.push({ rank: r, suit: s });
    }
  }
  return deck;
}

function shuffleDeck(deck, rng) {
  for (let i = deck.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
}

function cardId(c) { return c.rank * 4 + c.suit; }

// ── Hand evaluation (7-card) ─────────────────────────────────────────
// Returns a numeric score: higher is better

function evaluate7(cards) {
  // Count ranks and suits
  const rankCount = new Array(15).fill(0);
  const suitCount = new Array(4).fill(0);
  const suitCards = [[], [], [], []];

  for (const c of cards) {
    rankCount[c.rank]++;
    suitCount[c.suit]++;
    suitCards[c.suit].push(c.rank);
  }

  // Check flush
  let flushSuit = -1;
  for (let s = 0; s < 4; s++) {
    if (suitCount[s] >= 5) { flushSuit = s; break; }
  }

  // Check straight (including ace-low)
  function bestStraight(ranks) {
    const has = new Array(15).fill(false);
    for (const r of ranks) has[r] = true;
    if (has[14]) has[1] = true; // ace-low
    let best = 0;
    for (let high = 14; high >= 5; high--) {
      if (has[high] && has[high - 1] && has[high - 2] && has[high - 3] && has[high - 4]) {
        best = high;
        break;
      }
    }
    return best;
  }

  const straightHigh = bestStraight(cards.map(c => c.rank));

  // Straight flush
  if (flushSuit >= 0) {
    const sfHigh = bestStraight(suitCards[flushSuit]);
    if (sfHigh > 0) {
      return 8000000 + sfHigh; // Straight flush
    }
  }

  // Four of a kind
  for (let r = 14; r >= 2; r--) {
    if (rankCount[r] === 4) {
      let kicker = 0;
      for (let k = 14; k >= 2; k--) {
        if (k !== r && rankCount[k] > 0) { kicker = k; break; }
      }
      return 7000000 + r * 100 + kicker;
    }
  }

  // Full house
  let threeRank = 0, pairRank = 0;
  for (let r = 14; r >= 2; r--) {
    if (rankCount[r] >= 3 && !threeRank) threeRank = r;
    else if (rankCount[r] >= 2 && !pairRank) pairRank = r;
  }
  if (threeRank && pairRank) {
    return 6000000 + threeRank * 100 + pairRank;
  }

  // Flush
  if (flushSuit >= 0) {
    const flushRanks = suitCards[flushSuit].sort((a, b) => b - a).slice(0, 5);
    let score = 5000000;
    for (let i = 0; i < 5; i++) score += flushRanks[i] * Math.pow(15, 4 - i);
    return score;
  }

  // Straight
  if (straightHigh) {
    return 4000000 + straightHigh;
  }

  // Three of a kind
  if (threeRank) {
    const kickers = [];
    for (let r = 14; r >= 2 && kickers.length < 2; r--) {
      if (r !== threeRank && rankCount[r] > 0) kickers.push(r);
    }
    return 3000000 + threeRank * 10000 + (kickers[0] || 0) * 100 + (kickers[1] || 0);
  }

  // Two pair / one pair
  const pairs = [];
  for (let r = 14; r >= 2; r--) {
    if (rankCount[r] >= 2) pairs.push(r);
  }

  if (pairs.length >= 2) {
    const kickers = [];
    for (let r = 14; r >= 2 && kickers.length < 1; r--) {
      if (r !== pairs[0] && r !== pairs[1] && rankCount[r] > 0) kickers.push(r);
    }
    return 2000000 + pairs[0] * 10000 + pairs[1] * 100 + (kickers[0] || 0);
  }

  if (pairs.length === 1) {
    const kickers = [];
    for (let r = 14; r >= 2 && kickers.length < 3; r--) {
      if (r !== pairs[0] && rankCount[r] > 0) kickers.push(r);
    }
    return 1000000 + pairs[0] * 10000 + (kickers[0] || 0) * 100 + (kickers[1] || 0);
  }

  // High card
  const sorted = cards.map(c => c.rank).sort((a, b) => b - a).slice(0, 5);
  let score = 0;
  for (let i = 0; i < 5; i++) score += sorted[i] * Math.pow(15, 4 - i);
  return score;
}

// ── Board texture features ───────────────────────────────────────────

function boardFeatures(board) {
  if (board.length === 0) return { paired: 0, flush3: 0, flush4: 0, straight3: 0, straight4: 0, highCard: 0 };

  const ranks = board.map(c => c.rank);
  const suits = board.map(c => c.suit);

  // Paired
  const rankCounts = {};
  for (const r of ranks) rankCounts[r] = (rankCounts[r] || 0) + 1;
  const paired = Object.values(rankCounts).some(c => c >= 2) ? 1 : 0;

  // Flush draws
  const suitCounts = {};
  for (const s of suits) suitCounts[s] = (suitCounts[s] || 0) + 1;
  const maxSuit = Math.max(...Object.values(suitCounts));
  const flush3 = maxSuit >= 3 ? 1 : 0;
  const flush4 = maxSuit >= 4 ? 1 : 0;

  // Straight connectivity
  const unique = [...new Set(ranks)].sort((a, b) => a - b);
  if (unique.includes(14)) unique.unshift(1);
  let maxConn = 1, conn = 1;
  for (let i = 1; i < unique.length; i++) {
    if (unique[i] - unique[i - 1] <= 2) conn++;
    else conn = 1;
    maxConn = Math.max(maxConn, conn);
  }
  const straight3 = maxConn >= 3 ? 1 : 0;
  const straight4 = maxConn >= 4 ? 1 : 0;

  const highCard = Math.max(...ranks) / 14;

  return { paired, flush3, flush4, straight3, straight4, highCard };
}

// ── Monte Carlo equity ───────────────────────────────────────────────

function computeEquity(heroCards, boardCards, numSims) {
  let wins = 0, ties = 0, total = 0;

  const usedIds = new Set([...heroCards, ...boardCards].map(cardId));

  for (let sim = 0; sim < numSims; sim++) {
    // Build remaining deck
    const remaining = [];
    for (const r of RANKS) {
      for (const s of SUITS) {
        const c = { rank: r, suit: s };
        if (!usedIds.has(cardId(c))) remaining.push(c);
      }
    }

    // Shuffle
    for (let i = remaining.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [remaining[i], remaining[j]] = [remaining[j], remaining[i]];
    }

    let idx = 0;

    // Deal opponent
    const opp = [remaining[idx++], remaining[idx++]];

    // Complete board
    const fullBoard = [...boardCards];
    while (fullBoard.length < 5) fullBoard.push(remaining[idx++]);

    // Evaluate
    const heroScore = evaluate7([...heroCards, ...fullBoard]);
    const oppScore = evaluate7([...opp, ...fullBoard]);

    if (heroScore > oppScore) wins++;
    else if (heroScore === oppScore) ties++;
    total++;
  }

  return (wins + ties * 0.5) / total;
}

// ── Main ──────────────────────────────────────────────────────────────

console.log("=" .repeat(55));
console.log("  EQUITY TRAINING DATA GENERATOR");
console.log("=".repeat(55));
console.log(`  Samples: ${SAMPLES.toLocaleString()}`);
console.log(`  Sims per sample: ${SIMS_PER_SAMPLE}`);
console.log(`  Output: ${OUTPUT_PATH}`);
console.log();

const stream = fs.createWriteStream(OUTPUT_PATH);
const startTime = Date.now();

for (let i = 0; i < SAMPLES; i++) {
  const deck = shuffleDeck(makeDeck(), Math.random);

  const hero = [deck[0], deck[1]];

  // Random board length: 0 (preflop), 3 (flop), 4 (turn), 5 (river)
  // Weight toward postflop since that's where evaluation matters most
  const r = Math.random();
  let boardLen;
  if (r < 0.15) boardLen = 0;
  else if (r < 0.45) boardLen = 3;
  else if (r < 0.70) boardLen = 4;
  else boardLen = 5;

  const board = deck.slice(2, 2 + boardLen);

  // Compute true equity via Monte Carlo
  const equity = computeEquity(hero, board, SIMS_PER_SAMPLE);

  // Board texture features
  const bf = boardFeatures(board);

  // Encode cards as integers (0-51)
  const heroInts = hero.map(c => (c.rank - 2) * 4 + c.suit);
  const boardInts = board.map(c => (c.rank - 2) * 4 + c.suit);

  // Hero features
  const suited = hero[0].suit === hero[1].suit ? 1 : 0;
  const pair = hero[0].rank === hero[1].rank ? 1 : 0;
  const gap = Math.abs(hero[0].rank - hero[1].rank) / 12;
  const highRank = Math.max(hero[0].rank, hero[1].rank) / 14;
  const lowRank = Math.min(hero[0].rank, hero[1].rank) / 14;

  // Does hero connect with board?
  const boardRanks = board.map(c => c.rank);
  const hits = boardRanks.filter(r => r === hero[0].rank || r === hero[1].rank).length / Math.max(1, board.length);

  // Hero flush draw
  const heroFlushCards = board.filter(c => c.suit === hero[0].suit || c.suit === hero[1].suit).length;
  const heroFlushDraw = (suited && heroFlushCards >= 2) ? 1 : 0;

  const entry = {
    heroCards: heroInts,
    boardCards: boardInts,
    boardLen,
    equity: Math.round(equity * 10000) / 10000,
    // Features
    suited, pair, gap, highRank, lowRank, hits,
    heroFlushDraw,
    ...bf,
  };

  stream.write(JSON.stringify(entry) + "\n");

  if ((i + 1) % 10000 === 0) {
    const elapsed = (Date.now() - startTime) / 1000;
    const rate = (i + 1) / elapsed;
    const eta = (SAMPLES - i - 1) / rate;
    console.log(`  ${(i + 1).toLocaleString()} / ${SAMPLES.toLocaleString()} (${rate.toFixed(0)}/s, ETA ${eta.toFixed(0)}s)`);
  }
}

stream.end();
const totalTime = (Date.now() - startTime) / 1000;
console.log(`\n  Done: ${SAMPLES.toLocaleString()} samples in ${totalTime.toFixed(1)}s`);
console.log(`  Saved to ${OUTPUT_PATH}`);
