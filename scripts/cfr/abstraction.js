"use strict";

/**
 * Card abstraction for CFR.
 *
 * Full NL Hold'em has ~10^160 information sets. We reduce this by:
 *   1. Bucketing hole cards by hand strength (10 buckets)
 *   2. Bucketing postflop by hand strength + board texture (10 buckets)
 *   3. Using fixed bet size fractions instead of continuous amounts
 *
 * The information set key encodes:
 *   <card_bucket>|<action_history>
 *
 * Example: "7|cc-r50-c" = bucket 7, preflop check-check, flop raise 50% pot, call
 */

// ── Hand Strength Evaluation ─────────────────────────────────────────────

/**
 * Evaluate preflop hand strength as a 0..1 value.
 * Uses a simple heuristic (same as bot-players.js TAG strategy).
 * For production CFR, this would be replaced by Monte Carlo rollouts.
 */
function evaluateHandStrength(cards, board, phase) {
  if (!cards || cards.length < 2) return 0.5;
  const c1 = cards[0], c2 = cards[1];
  const r1 = c1.rank, r2 = c2.rank;
  const suited = c1.suit === c2.suit;
  const pair = r1 === r2;
  const highCard = Math.max(r1, r2);
  const gap = Math.abs(r1 - r2);

  let pf = 0;
  if (pair) {
    pf = 0.5 + (r1 / 14) * 0.5;
  } else {
    pf = (highCard / 14) * 0.4;
    if (suited) pf += 0.08;
    if (gap <= 1) pf += 0.06;
    if (gap <= 3) pf += 0.03;
    if (r1 >= 10 && r2 >= 10) pf += 0.15;
    if (highCard === 14) pf += 0.1;
  }

  if (!phase || phase === "PREFLOP" || !board || board.length === 0) {
    return Math.min(1, pf);
  }

  // Postflop: augment with board hits
  const boardRanks = board.map(c => c.rank);
  let post = pf;
  if (boardRanks.includes(r1)) post += 0.25;
  if (boardRanks.includes(r2)) post += 0.20;
  if (boardRanks.includes(r1) && boardRanks.includes(r2) && !pair) post += 0.20;
  if (pair && boardRanks.includes(r1)) post += 0.35;
  if (pair && boardRanks.length > 0 && r1 > Math.max(...boardRanks)) post += 0.15;

  // Flush draw detection
  const allSuits = [...cards, ...board].map(c => c.suit);
  const suitCounts = {};
  for (const s of allSuits) suitCounts[s] = (suitCounts[s] || 0) + 1;
  const maxSuitCount = Math.max(...Object.values(suitCounts));
  if (maxSuitCount >= 5) post += 0.30; // flush made
  else if (maxSuitCount === 4) post += 0.10; // flush draw

  // Straight draw detection (simplified)
  const allRanks = [...new Set([...cards, ...board].map(c => c.rank))].sort((a, b) => a - b);
  let maxConsec = 1, curConsec = 1;
  for (let i = 1; i < allRanks.length; i++) {
    if (allRanks[i] === allRanks[i - 1] + 1) { curConsec++; maxConsec = Math.max(maxConsec, curConsec); }
    else curConsec = 1;
  }
  if (maxConsec >= 5) post += 0.25; // straight made
  else if (maxConsec === 4) post += 0.08; // open-ended draw

  return Math.min(1, post);
}

/**
 * Map a hand strength (0..1) into a discrete bucket (0..numBuckets-1).
 */
function strengthToBucket(strength, numBuckets) {
  const bucket = Math.floor(strength * numBuckets);
  return Math.min(bucket, numBuckets - 1);
}

// ── Information Set Key Construction ─────────────────────────────────────

/**
 * Build an information set key from the player's perspective.
 *
 * @param {number} cardBucket  - The player's card strength bucket
 * @param {string} actionHistory - The sequence of actions (e.g., "cc-r50c")
 * @returns {string} Information set key
 */
function makeInfoSetKey(cardBucket, actionHistory) {
  return `${cardBucket}|${actionHistory}`;
}

/**
 * Encode an action into a compact string for the action history.
 * Actions:
 *   f = fold, k = check, c = call
 *   bh/bp/ba = bet half/pot/all-in
 *   rh/rp/ra = raise half/pot/all-in
 *   r = raise (legacy limit game)
 *   b = bet (legacy)
 */
function encodeAction(action) {
  switch (action) {
    case "FOLD": return "f";
    case "CHECK": return "k";
    case "CALL": return "c";
    case "BET": return "b";
    case "RAISE": return "r";
    case "BET_HALF": return "bh";
    case "BET_POT": return "bp";
    case "BET_ALLIN": return "ba";
    case "RAISE_HALF": return "rh";
    case "RAISE_POT": return "rp";
    case "RAISE_ALLIN": return "ra";
    default: return "?";
  }
}

// ── Bet Size Abstraction ─────────────────────────────────────────────────

/**
 * Abstract bet sizes into discrete fractions of the pot.
 * For no-limit, we limit raises to these pot fractions:
 *   0.5x pot, 1x pot, 2x pot, all-in
 *
 * Returns the closest abstract size label.
 */
const BET_FRACTIONS = [0.5, 1.0, 2.0, Infinity]; // Infinity = all-in
const BET_LABELS = ["h", "p", "d", "a"]; // half, pot, double, all-in

function abstractBetSize(amount, potSize) {
  if (potSize <= 0) return "a"; // all-in if no pot context
  const fraction = amount / potSize;

  let bestIdx = 0;
  let bestDist = Math.abs(fraction - BET_FRACTIONS[0]);
  for (let i = 1; i < BET_FRACTIONS.length; i++) {
    const dist = Math.abs(fraction - BET_FRACTIONS[i]);
    if (dist < bestDist) { bestDist = dist; bestIdx = i; }
  }
  return BET_LABELS[bestIdx];
}

/**
 * Map an abstract bet label back to an actual amount.
 */
function concreteBetSize(label, potSize, minBet, maxBet) {
  let target;
  switch (label) {
    case "h": target = potSize * 0.5; break;
    case "p": target = potSize * 1.0; break;
    case "d": target = potSize * 2.0; break;
    case "a": return maxBet;
    default: target = potSize * 0.5;
  }
  return Math.max(minBet, Math.min(Math.round(target), maxBet));
}

// ── Preflop Hand Ranking (for more accurate bucketing) ──────────────────

/**
 * Get a canonical preflop hand string (e.g., "AKs", "TTo", "72o").
 * Used for lookup-based bucketing when available.
 */
function canonicalHand(cards) {
  const RANK_CHARS = { 14: "A", 13: "K", 12: "Q", 11: "J", 10: "T", 9: "9", 8: "8", 7: "7", 6: "6", 5: "5", 4: "4", 3: "3", 2: "2" };
  const r1 = cards[0].rank, r2 = cards[1].rank;
  const suited = cards[0].suit === cards[1].suit;
  const high = Math.max(r1, r2), low = Math.min(r1, r2);
  const h = RANK_CHARS[high], l = RANK_CHARS[low];
  if (high === low) return `${h}${l}`;
  return `${h}${l}${suited ? "s" : "o"}`;
}

module.exports = {
  evaluateHandStrength,
  strengthToBucket,
  makeInfoSetKey,
  encodeAction,
  abstractBetSize,
  concreteBetSize,
  canonicalHand,
  BET_FRACTIONS,
  BET_LABELS,
};
