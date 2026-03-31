"use strict";

/**
 * Hand evaluator for Texas Hold'em.
 * Pure function: 7 cards in, ranked hand out. No engine coupling.
 *
 * Canonical rank format:
 *   { category: 0-8, ranks: number[] }
 *
 *   category:
 *     0 = High Card
 *     1 = One Pair
 *     2 = Two Pair
 *     3 = Three of a Kind
 *     4 = Straight
 *     5 = Flush
 *     6 = Full House
 *     7 = Four of a Kind
 *     8 = Straight Flush  (includes royal flush as the ace-high case)
 *
 *   ranks: array of rank values (14=A, 13=K, ... 2=2) ordered by
 *          hand-type-specific rules. Compared element-by-element for
 *          tie-breaking. Two hands with identical {category, ranks} are
 *          an exact tie (split pot).
 *
 * Cards use the engine's {rank, suit} format: rank 2-14, suit 1-4.
 */

// ── Combinations ──────────────────────────────────────────────────────────

function combinations(arr, k) {
  const result = [];
  function helper(start, combo) {
    if (combo.length === k) {
      result.push(combo.slice());
      return;
    }
    for (let i = start; i < arr.length; i++) {
      combo.push(arr[i]);
      helper(i + 1, combo);
      combo.pop();
    }
  }
  helper(0, []);
  return result;
}

// ── Classify a 5-card hand ────────────────────────────────────────────────

function classify5(cards) {
  const ranks = cards.map((c) => c.rank).sort((a, b) => b - a);
  const suits = cards.map((c) => c.suit);

  const isFlush = suits.every((s) => s === suits[0]);

  // Check straight (including ace-low)
  const unique = [...new Set(ranks)].sort((a, b) => b - a);
  let isStraight = false;
  let straightHigh = 0;

  if (unique.length === 5) {
    if (unique[0] - unique[4] === 4) {
      isStraight = true;
      straightHigh = unique[0];
    }
    // Ace-low: A-5-4-3-2
    if (unique[0] === 14 && unique[1] === 5 && unique[2] === 4 && unique[3] === 3 && unique[4] === 2) {
      isStraight = true;
      straightHigh = 5; // 5-high straight
    }
  }

  // Count rank frequencies
  const freq = {};
  for (const r of ranks) {
    freq[r] = (freq[r] || 0) + 1;
  }
  const groups = Object.entries(freq)
    .map(([r, c]) => ({ rank: parseInt(r), count: c }))
    .sort((a, b) => b.count - a.count || b.rank - a.rank);

  // Straight flush (category 8)
  if (isFlush && isStraight) {
    return { category: 8, ranks: [straightHigh] };
  }

  // Four of a kind (category 7)
  if (groups[0].count === 4) {
    const quad = groups[0].rank;
    const kicker = groups[1].rank;
    return { category: 7, ranks: [quad, kicker] };
  }

  // Full house (category 6)
  if (groups[0].count === 3 && groups[1].count === 2) {
    return { category: 6, ranks: [groups[0].rank, groups[1].rank] };
  }

  // Flush (category 5)
  if (isFlush) {
    return { category: 5, ranks: ranks };
  }

  // Straight (category 4)
  if (isStraight) {
    return { category: 4, ranks: [straightHigh] };
  }

  // Three of a kind (category 3)
  if (groups[0].count === 3) {
    const trips = groups[0].rank;
    const kickers = groups.filter((g) => g.count === 1).map((g) => g.rank).sort((a, b) => b - a);
    return { category: 3, ranks: [trips, ...kickers] };
  }

  // Two pair (category 2)
  if (groups[0].count === 2 && groups[1].count === 2) {
    const highPair = Math.max(groups[0].rank, groups[1].rank);
    const lowPair = Math.min(groups[0].rank, groups[1].rank);
    const kicker = groups[2].rank;
    return { category: 2, ranks: [highPair, lowPair, kicker] };
  }

  // One pair (category 1)
  if (groups[0].count === 2) {
    const pair = groups[0].rank;
    const kickers = groups.filter((g) => g.count === 1).map((g) => g.rank).sort((a, b) => b - a);
    return { category: 1, ranks: [pair, ...kickers] };
  }

  // High card (category 0)
  return { category: 0, ranks: ranks };
}

// ── Hand names ────────────────────────────────────────────────────────────

const RANK_NAMES = {
  14: "Ace", 13: "King", 12: "Queen", 11: "Jack", 10: "Ten",
  9: "Nine", 8: "Eight", 7: "Seven", 6: "Six", 5: "Five",
  4: "Four", 3: "Three", 2: "Two",
};

const RANK_NAMES_PLURAL = {
  14: "Aces", 13: "Kings", 12: "Queens", 11: "Jacks", 10: "Tens",
  9: "Nines", 8: "Eights", 7: "Sevens", 6: "Sixes", 5: "Fives",
  4: "Fours", 3: "Threes", 2: "Twos",
};

function handName(classified) {
  const r = classified.ranks;
  switch (classified.category) {
    case 8:
      return r[0] === 14 ? "Royal Flush" : `Straight Flush, ${RANK_NAMES[r[0]]}-high`;
    case 7:
      return `Four of a Kind, ${RANK_NAMES_PLURAL[r[0]]}`;
    case 6:
      return `Full House, ${RANK_NAMES_PLURAL[r[0]]} over ${RANK_NAMES_PLURAL[r[1]]}`;
    case 5:
      return `Flush, ${RANK_NAMES[r[0]]}-high`;
    case 4:
      return r[0] === 5 ? "Straight, Five-high" : `Straight, ${RANK_NAMES[r[0]]}-high`;
    case 3:
      return `Three of a Kind, ${RANK_NAMES_PLURAL[r[0]]}`;
    case 2:
      return `Two Pair, ${RANK_NAMES_PLURAL[r[0]]} and ${RANK_NAMES_PLURAL[r[1]]}`;
    case 1:
      return `Pair of ${RANK_NAMES_PLURAL[r[0]]}`;
    case 0:
      return `${RANK_NAMES[r[0]]}-high`;
    default:
      return "Unknown";
  }
}

// ── Evaluate 7 cards → best 5-card hand ──────────────────────────────────

function evaluateHand(cards) {
  if (cards.length < 5 || cards.length > 7) {
    throw new Error(`evaluateHand requires 5-7 cards, got ${cards.length}`);
  }

  const combos = combinations(cards, 5);
  let best = null;

  for (const combo of combos) {
    const classified = classify5(combo);
    if (!best || compareClassified(classified, best.classified) > 0) {
      best = { classified, cards: combo };
    }
  }

  return {
    category: best.classified.category,
    ranks: best.classified.ranks,
    handName: handName(best.classified),
    bestFive: best.cards,
  };
}

// ── Compare two classified hands ─────────────────────────────────────────

function compareClassified(a, b) {
  if (a.category !== b.category) return a.category - b.category;
  for (let i = 0; i < Math.max(a.ranks.length, b.ranks.length); i++) {
    const ra = a.ranks[i] || 0;
    const rb = b.ranks[i] || 0;
    if (ra !== rb) return ra - rb;
  }
  return 0;
}

function compareHands(a, b) {
  const cmp = compareClassified(
    { category: a.category, ranks: a.ranks },
    { category: b.category, ranks: b.ranks }
  );
  if (cmp > 0) return 1;
  if (cmp < 0) return -1;
  return 0;
}

// ── Find winners from evaluated hands ────────────────────────────────────

function findWinners(evaluatedHands) {
  if (evaluatedHands.length === 0) return [];

  let bestIdx = [0];
  for (let i = 1; i < evaluatedHands.length; i++) {
    const cmp = compareHands(evaluatedHands[i], evaluatedHands[bestIdx[0]]);
    if (cmp > 0) {
      bestIdx = [i];
    } else if (cmp === 0) {
      bestIdx.push(i);
    }
  }
  return bestIdx;
}

module.exports = { evaluateHand, compareHands, findWinners, classify5, handName };
