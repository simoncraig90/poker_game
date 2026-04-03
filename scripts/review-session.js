#!/usr/bin/env node
"use strict";

/**
 * Review advisor session logs — flags bad recommendations.
 *
 * Parses advisor_log.jsonl and checks each recommendation against
 * a proper hand evaluator that accounts for board texture.
 *
 * Usage:
 *   node scripts/review-session.js                          # latest log
 *   node scripts/review-session.js vision/data/advisor_log.jsonl
 */

const fs = require("fs");
const path = require("path");

const DEFAULT_LOG = path.join(__dirname, "..", "vision", "data", "advisor_log.jsonl");
const logPath = process.argv[2] || DEFAULT_LOG;

if (!fs.existsSync(logPath)) {
  console.error(`Log not found: ${logPath}`);
  process.exit(1);
}

// ── Card parsing ──────────────────────────────────────────────────────

const RANK_MAP = { "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14 };
const RANK_NAME = { 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "T", 11: "J", 12: "Q", 13: "K", 14: "A" };
const SUIT_MAP = { "c": 0, "d": 1, "h": 2, "s": 3 };

function parseCard(s) {
  if (!s || s.length < 2) return null;
  const rank = RANK_MAP[s[0].toUpperCase()];
  const suit = SUIT_MAP[s[1].toLowerCase()];
  if (rank === undefined || suit === undefined) return null;
  return { rank, suit };
}

// ── Proper hand evaluation ────────────────────────────────────────────

function evaluateHand(heroCards, boardCards) {
  const hero = heroCards.map(parseCard).filter(Boolean);
  const board = boardCards.map(parseCard).filter(Boolean);

  if (hero.length < 2) return null;

  const all = [...hero, ...board];
  const boardRanks = board.map(c => c.rank).sort((a, b) => a - b);
  const boardSuits = board.map(c => c.suit);
  const allRanks = all.map(c => c.rank).sort((a, b) => a - b);
  const r1 = hero[0].rank, r2 = hero[1].rank;
  const s1 = hero[0].suit, s2 = hero[1].suit;
  const suited = s1 === s2;
  const pair = r1 === r2;

  const result = {
    madeHand: "HIGH_CARD",
    handRank: 0,  // 0-9 scale
    kicker: Math.max(r1, r2),
    warnings: [],
    boardTexture: [],
    equity: 0,
  };

  if (board.length === 0) {
    // Preflop evaluation
    result.madeHand = pair ? "PAIR" : "HIGH_CARD";
    let pf = 0;
    if (pair) {
      pf = 0.5 + (r1 / 14) * 0.5;
    } else {
      pf = (Math.max(r1, r2) / 14) * 0.4;
      if (suited) pf += 0.06;
      if (Math.abs(r1 - r2) <= 1) pf += 0.04;
      if (r1 >= 10 && r2 >= 10) pf += 0.12;
      if (Math.max(r1, r2) === 14) pf += 0.08;
    }
    result.equity = Math.min(1, pf);
    result.handRank = pair ? 2 : 1;
    return result;
  }

  // ── Board texture analysis ────────────────────────────────────────

  // Paired board
  const rankCounts = {};
  for (const r of boardRanks) rankCounts[r] = (rankCounts[r] || 0) + 1;
  const boardPairs = Object.values(rankCounts).filter(c => c >= 2).length;
  const boardTrips = Object.values(rankCounts).filter(c => c >= 3).length;
  if (boardPairs > 0) result.boardTexture.push("PAIRED");
  if (boardPairs >= 2) result.boardTexture.push("DOUBLE_PAIRED");
  if (boardTrips > 0) result.boardTexture.push("TRIPS_ON_BOARD");

  // Flush draw / flush on board
  const suitCounts = {};
  for (const s of boardSuits) suitCounts[s] = (suitCounts[s] || 0) + 1;
  const maxSuitCount = Math.max(...Object.values(suitCounts));
  const flushSuit = Object.keys(suitCounts).find(s => suitCounts[s] === maxSuitCount);
  if (maxSuitCount >= 3) result.boardTexture.push("FLUSH_POSSIBLE");
  if (maxSuitCount >= 4) result.boardTexture.push("FLUSH_HEAVY");

  // Straight possible — check for 3+ connected board cards
  const uniqueBoardRanks = [...new Set(boardRanks)].sort((a, b) => a - b);
  // Add low ace
  if (uniqueBoardRanks.includes(14)) uniqueBoardRanks.unshift(1);
  let maxConnected = 1, conn = 1;
  for (let i = 1; i < uniqueBoardRanks.length; i++) {
    if (uniqueBoardRanks[i] - uniqueBoardRanks[i - 1] <= 2) conn++;
    else conn = 1;
    maxConnected = Math.max(maxConnected, conn);
  }
  if (maxConnected >= 3) result.boardTexture.push("STRAIGHT_POSSIBLE");
  if (maxConnected >= 4) result.boardTexture.push("STRAIGHT_HEAVY");

  // Check if a straight exists on the board itself
  function hasStraight(ranks) {
    const unique = [...new Set(ranks)].sort((a, b) => a - b);
    if (unique.includes(14)) unique.unshift(1);
    for (let i = 0; i <= unique.length - 5; i++) {
      if (unique[i + 4] - unique[i] === 4) return true;
    }
    return false;
  }
  if (hasStraight(boardRanks)) result.boardTexture.push("STRAIGHT_ON_BOARD");

  // ── Hero hand strength ────────────────────────────────────────────

  // Count hero+board rank occurrences
  const allRankCounts = {};
  for (const c of all) allRankCounts[c.rank] = (allRankCounts[c.rank] || 0) + 1;

  // Check for flush
  const heroFlush = (suited && suitCounts[s1] >= 3 &&
    board.filter(c => c.suit === s1).length >= 3);
  const heroFlushDraw = (suited && board.filter(c => c.suit === s1).length >= 2 && board.length < 5);
  const heroNutFlush = heroFlush && Math.max(r1, r2) === 14;

  // Check for straight using hero + board
  function bestStraight(cards) {
    const ranks = [...new Set(cards.map(c => c.rank))].sort((a, b) => a - b);
    if (ranks.includes(14)) ranks.unshift(1);
    let best = 0;
    for (let high = ranks.length - 1; high >= 4; high--) {
      let count = 1;
      for (let j = high - 1; j >= 0 && count < 5; j--) {
        if (ranks[high - (4 - count)] - ranks[j] <= (4 - count)) count++;
      }
    }
    // Simpler: check all 5-card windows
    for (let i = ranks.length - 1; i >= 4; i--) {
      if (ranks[i] - ranks[i - 4] === 4) {
        // Check both hero cards contribute
        const straightRanks = ranks.slice(i - 4, i + 1);
        const heroInStraight = straightRanks.includes(r1) || straightRanks.includes(r2) ||
          (r1 === 14 && straightRanks.includes(1)) || (r2 === 14 && straightRanks.includes(1));
        if (heroInStraight) best = Math.max(best, ranks[i]);
      }
    }
    return best;
  }
  const straightHigh = bestStraight(all);
  const boardStraightHigh = bestStraight(board);
  const heroMakesStraight = straightHigh > 0 && straightHigh > boardStraightHigh;

  // Pairs, sets, boats
  const heroHitsBoard1 = boardRanks.includes(r1);
  const heroHitsBoard2 = boardRanks.includes(r2);
  const heroOverpair = pair && r1 > Math.max(...boardRanks);
  const heroTopPair = (heroHitsBoard1 && r1 === Math.max(...boardRanks)) ||
    (heroHitsBoard2 && r2 === Math.max(...boardRanks));
  const heroMiddlePair = (heroHitsBoard1 || heroHitsBoard2) && !heroTopPair;
  const heroTwoPair = heroHitsBoard1 && heroHitsBoard2 && r1 !== r2;
  const heroSet = pair && boardRanks.includes(r1);
  const heroTrips = !pair && (allRankCounts[r1] >= 3 || allRankCounts[r2] >= 3);
  const heroFullHouse = (heroSet && boardPairs > 0) ||
    (heroTwoPair && boardPairs > 0) ||
    (Object.values(allRankCounts).filter(c => c >= 3).length > 0 &&
      Object.values(allRankCounts).filter(c => c >= 2).length > 1);
  const heroQuads = allRankCounts[r1] >= 4 || allRankCounts[r2] >= 4;

  // Assign hand rank and equity
  if (heroQuads) {
    result.madeHand = "QUADS"; result.handRank = 9; result.equity = 0.97;
  } else if (heroFullHouse) {
    result.madeHand = "FULL_HOUSE"; result.handRank = 8; result.equity = 0.92;
  } else if (heroFlush) {
    result.madeHand = "FLUSH"; result.handRank = 7;
    result.equity = heroNutFlush ? 0.90 : 0.82;
  } else if (heroMakesStraight) {
    result.madeHand = "STRAIGHT"; result.handRank = 6; result.equity = 0.78;
  } else if (heroSet) {
    result.madeHand = "SET"; result.handRank = 5; result.equity = 0.75;
  } else if (heroTrips) {
    result.madeHand = "TRIPS"; result.handRank = 5; result.equity = 0.70;
  } else if (heroTwoPair) {
    result.madeHand = "TWO_PAIR"; result.handRank = 4; result.equity = 0.65;
  } else if (heroOverpair) {
    result.madeHand = "OVERPAIR"; result.handRank = 3; result.equity = 0.65;
  } else if (heroTopPair) {
    result.madeHand = "TOP_PAIR"; result.handRank = 3;
    result.equity = 0.55 + (Math.max(r1, r2) / 14) * 0.1;
  } else if (heroMiddlePair) {
    result.madeHand = "MIDDLE_PAIR"; result.handRank = 2;
    result.equity = 0.35 + (Math.min(r1, r2) / 14) * 0.1;
  } else if (pair) {
    result.madeHand = "UNDERPAIR"; result.handRank = 2;
    result.equity = 0.30;
  } else if (heroHitsBoard1 || heroHitsBoard2) {
    const hitRank = heroHitsBoard1 ? r1 : r2;
    result.madeHand = "BOTTOM_PAIR"; result.handRank = 2;
    result.equity = 0.25 + (hitRank / 14) * 0.1;
  } else {
    result.madeHand = "HIGH_CARD"; result.handRank = 1;
    result.equity = 0.15 + (Math.max(r1, r2) / 14) * 0.1;
  }

  // Draws (only on flop/turn)
  if (board.length < 5) {
    if (heroFlushDraw) {
      result.equity += 0.15;
      result.warnings.push("FLUSH_DRAW");
    }
    // Open-ended straight draw
    const uniqueAll = [...new Set(all.map(c => c.rank))].sort((a, b) => a - b);
    for (let i = 0; i <= uniqueAll.length - 4; i++) {
      if (uniqueAll[i + 3] - uniqueAll[i] === 3) {
        const drawRanks = uniqueAll.slice(i, i + 4);
        if (drawRanks.includes(r1) || drawRanks.includes(r2)) {
          result.equity += 0.10;
          result.warnings.push("STRAIGHT_DRAW");
          break;
        }
      }
    }
  }

  // ── Danger warnings ───────────────────────────────────────────────

  if (result.boardTexture.includes("STRAIGHT_POSSIBLE") && result.handRank <= 3) {
    result.warnings.push("STRAIGHT_DANGER");
    result.equity *= 0.75;
  }
  if (result.boardTexture.includes("STRAIGHT_HEAVY") && result.handRank <= 4) {
    result.warnings.push("STRAIGHT_HEAVY_DANGER");
    result.equity *= 0.65;
  }
  if (result.boardTexture.includes("FLUSH_POSSIBLE") && !heroFlush && !heroFlushDraw && result.handRank <= 4) {
    result.warnings.push("FLUSH_DANGER");
    result.equity *= 0.85;
  }
  if (result.boardTexture.includes("FLUSH_HEAVY") && !heroFlush) {
    result.warnings.push("FLUSH_HEAVY_DANGER");
    result.equity *= 0.70;
  }
  if (result.boardTexture.includes("PAIRED") && result.handRank <= 3) {
    result.warnings.push("PAIRED_BOARD");
    result.equity *= 0.90;
  }
  if (result.boardTexture.includes("DOUBLE_PAIRED") && result.handRank <= 3) {
    result.warnings.push("DOUBLE_PAIRED_BOARD");
    result.equity *= 0.80;
  }

  result.equity = Math.min(1, Math.max(0, result.equity));
  return result;
}

// ── Recommendation assessment ────────────────────────────────────────

function assessRecommendation(entry, eval_) {
  const rec = entry.recommended_action || "";
  const issues = [];

  // No recommendation
  if (!rec || Object.keys(entry.action_probs || {}).length === 0) {
    issues.push("NO_RECOMMENDATION");
    return issues;
  }

  const recUpper = rec.toUpperCase();

  // Raising with weak hands
  if (recUpper.includes("RAISE") && eval_.handRank <= 2 && eval_.equity < 0.4) {
    issues.push(`RAISE_WITH_WEAK (${eval_.madeHand}, equity ${(eval_.equity * 100).toFixed(0)}%)`);
  }

  // Raising into dangerous boards
  if (recUpper.includes("RAISE") && eval_.warnings.some(w => w.includes("DANGER"))) {
    issues.push(`RAISE_INTO_DANGER (${eval_.warnings.join(", ")})`);
  }

  // Calling with nothing on scary boards
  if (recUpper.includes("CALL") && eval_.handRank <= 1 && eval_.equity < 0.25) {
    issues.push(`CALL_WITH_AIR (${eval_.madeHand}, equity ${(eval_.equity * 100).toFixed(0)}%)`);
  }

  // Advisor equity vs proper equity mismatch
  const advisorEq = entry.equity || 0;
  const properEq = eval_.equity;
  const eqDiff = Math.abs(advisorEq - properEq);
  if (eqDiff > 0.15) {
    issues.push(`EQUITY_MISMATCH (advisor ${(advisorEq * 100).toFixed(0)}% vs proper ${(properEq * 100).toFixed(0)}%)`);
  }

  return issues;
}

// ── Main ──────────────────────────────────────────────────────────────

const lines = fs.readFileSync(logPath, "utf8").trim().split("\n").filter(Boolean);
const entries = lines.map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);

// Deduplicate — same hero+board within 5 seconds is the same decision
const deduped = [];
for (const e of entries) {
  const last = deduped[deduped.length - 1];
  if (last &&
    JSON.stringify(last.hero) === JSON.stringify(e.hero) &&
    JSON.stringify(last.board) === JSON.stringify(e.board) &&
    e.timestamp - last.timestamp < 5) {
    continue;
  }
  deduped.push(e);
}

// Only postflop entries with boards (preflop evaluation is simpler)
const postflop = deduped.filter(e => e.board && e.board.length >= 3);

console.log("=" .repeat(65));
console.log("  SESSION REVIEW — Advisor Recommendation Analysis");
console.log("=".repeat(65));
console.log(`  Total log entries: ${entries.length}`);
console.log(`  Unique decisions:  ${deduped.length}`);
console.log(`  Postflop spots:    ${postflop.length}`);
console.log();

let flagCount = 0;
let noRecCount = 0;

for (const e of deduped) {
  const eval_ = evaluateHand(e.hero || [], e.board || []);
  if (!eval_) continue;

  const issues = assessRecommendation(e, eval_);

  if (issues.includes("NO_RECOMMENDATION")) {
    noRecCount++;
    continue;
  }

  if (issues.length > 0) {
    flagCount++;
    const heroStr = (e.hero || []).join(" ");
    const boardStr = (e.board || []).length > 0 ? (e.board || []).join(" ") : "(preflop)";
    const rec = e.recommended_action || "none";
    const probs = Object.entries(e.action_probs || {})
      .map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`)
      .join(", ");

    console.log(`  ${e.time}  ${heroStr}  |  ${boardStr}`);
    console.log(`    Hand: ${eval_.madeHand}  Proper equity: ${(eval_.equity * 100).toFixed(0)}%  Advisor equity: ${(e.equity * 100).toFixed(0)}%`);
    if (eval_.boardTexture.length > 0) {
      console.log(`    Board: ${eval_.boardTexture.join(", ")}`);
    }
    if (eval_.warnings.length > 0) {
      console.log(`    Warnings: ${eval_.warnings.join(", ")}`);
    }
    console.log(`    Advisor said: ${rec} (${probs})`);
    console.log(`    Issues: ${issues.join(" | ")}`);
    console.log();
  }
}

console.log("-".repeat(65));
console.log(`  Flagged:            ${flagCount} bad recommendations`);
console.log(`  No recommendation:  ${noRecCount} (empty action_probs)`);
console.log(`  Clean:              ${deduped.length - flagCount - noRecCount}`);
console.log("=".repeat(65));
