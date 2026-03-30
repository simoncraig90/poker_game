"use strict";

/**
 * Hand strength evaluation for the poker bot.
 * Covers preflop hand rankings and post-flop made-hand detection.
 */

// ── Preflop Hand Rankings ─────────────────────────────────────────────────
// Returns a score from 0 (worst) to 1 (best) for a 2-card holding.

const PREMIUM_PAIRS = new Set([14, 13, 12, 11]); // AA, KK, QQ, JJ
const MEDIUM_PAIRS = new Set([10, 9, 8]);         // TT, 99, 88
const SMALL_PAIRS = new Set([7, 6, 5, 4, 3, 2]);  // 77-22

function preflopScore(card1, card2) {
  const r1 = Math.max(card1.rank, card2.rank);
  const r2 = Math.min(card1.rank, card2.rank);
  const suited = card1.suit === card2.suit;
  const pair = r1 === r2;
  const gap = r1 - r2;
  const connected = gap === 1;

  if (pair) {
    if (PREMIUM_PAIRS.has(r1)) return 0.95 - (14 - r1) * 0.02; // AA=0.95, JJ=0.89
    if (MEDIUM_PAIRS.has(r1)) return 0.70 + (r1 - 8) * 0.03;   // TT=0.76, 88=0.70
    return 0.50 + (r1 - 2) * 0.02;                               // 77=0.60, 22=0.50
  }

  // High card combinations
  let score = 0;

  // Base score from high card rank
  score += (r1 - 2) * 0.03; // A=0.36, K=0.33, etc.

  // Kicker value
  score += (r2 - 2) * 0.01;

  // Suited bonus
  if (suited) score += 0.06;

  // Connectivity bonus
  if (connected) score += 0.04;
  else if (gap === 2) score += 0.02;

  // Premium broadway combos
  if (r1 === 14 && r2 === 13) score = suited ? 0.87 : 0.82; // AKs/AKo
  if (r1 === 14 && r2 === 12) score = suited ? 0.80 : 0.74; // AQs/AQo
  if (r1 === 14 && r2 === 11) score = suited ? 0.76 : 0.70; // AJs/AJo
  if (r1 === 14 && r2 === 10) score = suited ? 0.72 : 0.65; // ATs/ATo
  if (r1 === 13 && r2 === 12) score = suited ? 0.78 : 0.72; // KQs/KQo
  if (r1 === 13 && r2 === 11) score = suited ? 0.73 : 0.66; // KJs/KJo

  return Math.min(score, 0.99);
}

// ── Post-flop Hand Evaluation ─────────────────────────────────────────────
// Returns { category, strength } where strength is 0-1.

const HAND_CATEGORY = {
  HIGH_CARD: 0,
  PAIR: 1,
  TWO_PAIR: 2,
  THREE_OF_A_KIND: 3,
  STRAIGHT: 4,
  FLUSH: 5,
  FULL_HOUSE: 6,
  FOUR_OF_A_KIND: 7,
  STRAIGHT_FLUSH: 8,
};

function evaluateHand(holeCards, board) {
  const all = [...holeCards, ...board];
  if (all.length < 5) {
    // Not enough cards for full evaluation; estimate from pairs/draws
    return evaluatePartial(holeCards, board);
  }

  const ranks = all.map((c) => c.rank);
  const suits = all.map((c) => c.suit);

  // Count ranks
  const rankCounts = {};
  for (const r of ranks) rankCounts[r] = (rankCounts[r] || 0) + 1;

  // Count suits
  const suitCounts = {};
  for (const s of suits) suitCounts[s] = (suitCounts[s] || 0) + 1;

  const counts = Object.values(rankCounts).sort((a, b) => b - a);
  const uniqueRanks = Object.keys(rankCounts).map(Number).sort((a, b) => b - a);

  // Check flush
  let flushSuit = null;
  for (const [s, count] of Object.entries(suitCounts)) {
    if (count >= 5) flushSuit = Number(s);
  }

  // Check straight
  const hasStraight = checkStraight(uniqueRanks);

  // Check straight flush
  if (flushSuit !== null && hasStraight) {
    const flushRanks = all.filter((c) => c.suit === flushSuit).map((c) => c.rank);
    const flushUnique = [...new Set(flushRanks)].sort((a, b) => b - a);
    if (checkStraight(flushUnique)) {
      return { category: HAND_CATEGORY.STRAIGHT_FLUSH, strength: 0.99 };
    }
  }

  // Four of a kind
  if (counts[0] === 4) {
    return { category: HAND_CATEGORY.FOUR_OF_A_KIND, strength: 0.96 };
  }

  // Full house
  if (counts[0] === 3 && counts[1] >= 2) {
    return { category: HAND_CATEGORY.FULL_HOUSE, strength: 0.90 + topRankBonus(rankCounts, 3) };
  }

  // Flush
  if (flushSuit !== null) {
    return { category: HAND_CATEGORY.FLUSH, strength: 0.82 + topFlushBonus(all, flushSuit) };
  }

  // Straight
  if (hasStraight) {
    return { category: HAND_CATEGORY.STRAIGHT, strength: 0.75 + straightHighBonus(uniqueRanks) };
  }

  // Three of a kind
  if (counts[0] === 3) {
    return { category: HAND_CATEGORY.THREE_OF_A_KIND, strength: 0.65 + topRankBonus(rankCounts, 3) };
  }

  // Two pair
  if (counts[0] === 2 && counts[1] === 2) {
    return { category: HAND_CATEGORY.TWO_PAIR, strength: 0.50 + twoPairBonus(rankCounts) };
  }

  // One pair
  if (counts[0] === 2) {
    const pairRank = Number(Object.keys(rankCounts).find((r) => rankCounts[r] === 2));
    const usesHoleCard = holeCards.some((c) => c.rank === pairRank);
    const base = usesHoleCard ? 0.35 : 0.25; // board pair is weaker
    return { category: HAND_CATEGORY.PAIR, strength: base + (pairRank - 2) * 0.01 };
  }

  // High card
  const highRank = Math.max(...holeCards.map((c) => c.rank));
  return { category: HAND_CATEGORY.HIGH_CARD, strength: 0.05 + (highRank - 2) * 0.015 };
}

function evaluatePartial(holeCards, board) {
  const all = [...holeCards, ...board];
  const ranks = all.map((c) => c.rank);
  const rankCounts = {};
  for (const r of ranks) rankCounts[r] = (rankCounts[r] || 0) + 1;

  const counts = Object.values(rankCounts).sort((a, b) => b - a);

  if (counts[0] >= 3) {
    return { category: HAND_CATEGORY.THREE_OF_A_KIND, strength: 0.70 };
  }
  if (counts[0] === 2 && counts[1] === 2) {
    return { category: HAND_CATEGORY.TWO_PAIR, strength: 0.55 };
  }
  if (counts[0] === 2) {
    const pairRank = Number(Object.keys(rankCounts).find((r) => rankCounts[r] === 2));
    const usesHoleCard = holeCards.some((c) => c.rank === pairRank);
    return { category: HAND_CATEGORY.PAIR, strength: usesHoleCard ? 0.40 : 0.25 };
  }
  const highRank = Math.max(...holeCards.map((c) => c.rank));
  return { category: HAND_CATEGORY.HIGH_CARD, strength: 0.05 + (highRank - 2) * 0.015 };
}

// ── Draw Detection ────────────────────────────────────────────────────────

function countDraws(holeCards, board) {
  const all = [...holeCards, ...board];
  const suits = all.map((c) => c.suit);
  const ranks = [...new Set(all.map((c) => c.rank))].sort((a, b) => a - b);

  // Flush draw: 4 cards of same suit
  const suitCounts = {};
  for (const s of suits) suitCounts[s] = (suitCounts[s] || 0) + 1;
  const flushDraw = Object.values(suitCounts).some((c) => c === 4);

  // Open-ended straight draw: 4 consecutive ranks (not at the edges A-high or A-low only)
  let straightDraw = false;
  for (let i = 0; i <= ranks.length - 4; i++) {
    if (ranks[i + 3] - ranks[i] === 3) {
      straightDraw = true;
      break;
    }
  }

  return { flushDraw, straightDraw };
}

// ── Helpers ───────────────────────────────────────────────────────────────

function checkStraight(sortedUniqueRanks) {
  const ranks = [...sortedUniqueRanks];
  // Add ace-low (rank 14 also counts as 1)
  if (ranks.includes(14)) ranks.push(1);
  const sorted = [...new Set(ranks)].sort((a, b) => b - a);

  for (let i = 0; i <= sorted.length - 5; i++) {
    if (sorted[i] - sorted[i + 4] === 4) return true;
  }
  return false;
}

function topRankBonus(rankCounts, count) {
  const rank = Number(Object.keys(rankCounts).find((r) => rankCounts[r] === count));
  return (rank - 2) * 0.003;
}

function topFlushBonus(cards, flushSuit) {
  const flushCards = cards.filter((c) => c.suit === flushSuit);
  const highRank = Math.max(...flushCards.map((c) => c.rank));
  return (highRank - 2) * 0.005;
}

function straightHighBonus(sortedUniqueRanks) {
  return (sortedUniqueRanks[0] - 5) * 0.005;
}

function twoPairBonus(rankCounts) {
  const pairs = Object.keys(rankCounts)
    .filter((r) => rankCounts[r] === 2)
    .map(Number)
    .sort((a, b) => b - a);
  return (pairs[0] - 2) * 0.008 + (pairs[1] - 2) * 0.003;
}

module.exports = {
  preflopScore,
  evaluateHand,
  countDraws,
  HAND_CATEGORY,
};
