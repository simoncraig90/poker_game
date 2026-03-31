"use strict";

/**
 * Side-pot calculator for Texas Hold'em.
 * Pure function: player investment data in, pot structure out.
 * No engine coupling.
 *
 * Canonical pot structure:
 *   { amount: number, eligible: number[] }
 *
 *   amount:   total chips in this pot
 *   eligible: seat indices of non-folded players who can win this pot
 *
 * Pots are ordered: main pot first, then side pots by creation order
 * (ascending investment tier).
 */

// ── Calculate Pots ────────────────────────────────────────────────────────

/**
 * Given player investment data, compute main pot and side pots.
 *
 * @param {Array<{seat: number, invested: number, folded: boolean}>} players
 *   All players who were dealt into the hand (including folded).
 *   `invested` = total chips put in during the hand (blinds + bets).
 *
 * @returns {Array<{amount: number, eligible: number[]}>}
 *   Main pot first, then side pots. Sum of all amounts == sum of all invested.
 *   eligible contains only non-folded seats that invested enough to contest.
 */
function calculatePots(players) {
  if (players.length === 0) return [];

  // Collect unique investment tiers from ALL players (including folded),
  // sorted ascending. These are the boundaries where pots split.
  const tiers = [...new Set(players.map((p) => p.invested))]
    .filter((v) => v > 0)
    .sort((a, b) => a - b);

  if (tiers.length === 0) return [];

  const pots = [];
  let prevTier = 0;

  for (const tier of tiers) {
    const sliceSize = tier - prevTier;

    // Count players who invested at least this tier amount
    const contributors = players.filter((p) => p.invested >= tier);
    const amount = sliceSize * contributors.length;

    // Eligible = non-folded contributors at this tier
    const eligible = contributors
      .filter((p) => !p.folded)
      .map((p) => p.seat);

    if (amount > 0) {
      pots.push({ amount, eligible });
    }

    prevTier = tier;
  }

  return pots;
}

// ── Award Pots ────────────────────────────────────────────────────────────

/**
 * Distribute pot winnings given pot structure and per-pot winners.
 *
 * @param {Array<{amount: number, eligible: number[]}>} pots
 *   From calculatePots().
 *
 * @param {Array<number[]>} winnersPerPot
 *   For each pot index, array of winning seat indices.
 *   Ties: multiple seats. Uncontested: single seat.
 *
 * @param {number[]} seatOrder
 *   Seats in clockwise order starting from first seat after the button.
 *   Used for odd-chip allocation: first winner in this order gets the
 *   remainder chip(s).
 *
 * @returns {{ awards: Array<{potIndex: number, distributions: Array<{seat: number, amount: number}>}>, total: number }}
 *   awards: per-pot distributions (who gets what from each pot).
 *   total: sum of all distributed chips (must equal sum of pot amounts).
 */
function awardPots(pots, winnersPerPot, seatOrder) {
  const awards = [];
  let total = 0;

  for (let i = 0; i < pots.length; i++) {
    const pot = pots[i];
    const winners = winnersPerPot[i];

    if (!winners || winners.length === 0) {
      // No eligible winner (shouldn't happen if calculatePots is correct,
      // but defensive: return to sole eligible if possible)
      awards.push({ potIndex: i, distributions: [] });
      continue;
    }

    const share = Math.floor(pot.amount / winners.length);
    const remainder = pot.amount % winners.length;

    // Sort winners by seat order (clockwise from button) for odd-chip rule
    const ordered = sortBySeatOrder(winners, seatOrder);

    const distributions = [];
    for (let w = 0; w < ordered.length; w++) {
      const extra = w < remainder ? 1 : 0;
      const amount = share + extra;
      distributions.push({ seat: ordered[w], amount });
      total += amount;
    }

    awards.push({ potIndex: i, distributions });
  }

  return { awards, total };
}

/**
 * Sort seats by their position in the clockwise seat order.
 * Seats appearing earlier in seatOrder come first.
 */
function sortBySeatOrder(seats, seatOrder) {
  const orderMap = new Map();
  for (let i = 0; i < seatOrder.length; i++) {
    orderMap.set(seatOrder[i], i);
  }
  return [...seats].sort((a, b) => {
    const oa = orderMap.has(a) ? orderMap.get(a) : Infinity;
    const ob = orderMap.has(b) ? orderMap.get(b) : Infinity;
    return oa - ob;
  });
}

// ── Validation ────────────────────────────────────────────────────────────

/**
 * Verify pot accounting: sum(pot amounts) == sum(player investments).
 */
function verifyPotAccounting(pots, players) {
  const potTotal = pots.reduce((s, p) => s + p.amount, 0);
  const investedTotal = players.reduce((s, p) => s + p.invested, 0);
  return potTotal === investedTotal;
}

module.exports = { calculatePots, awardPots, verifyPotAccounting, sortBySeatOrder };
