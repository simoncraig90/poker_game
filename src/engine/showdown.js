"use strict";

/**
 * Showdown settlement assembly.
 *
 * Pure computation: takes table snapshot data, returns a settlement result.
 * Does NOT mutate state or emit events. Those are the caller's job.
 *
 * Depends on: evaluate.js (hand evaluation), pots.js (pot calculation).
 */

const { evaluateHand, compareHands, findWinners } = require("./evaluate");
const { calculatePots, awardPots, verifyPotAccounting } = require("./pots");

/**
 * Compute full showdown settlement.
 *
 * @param {object} params
 * @param {Array<{seat: number, invested: number, folded: boolean, holeCards: Card[]|null}>} params.players
 *   All players dealt into the hand. holeCards required for non-folded players.
 * @param {Card[]} params.board - 5 community cards ({rank, suit, display})
 * @param {number[]} params.seatOrder - seats clockwise from button (for odd-chip rule)
 *
 * @returns {SettlementResult}
 *
 * SettlementResult:
 * {
 *   pots: Array<{amount, eligible}>,           // from calculatePots
 *   reveals: Array<{seat, handName, category, ranks, bestFive}>,  // per non-folded player
 *   potResults: Array<{                         // one per pot
 *     potIndex: number,
 *     amount: number,
 *     winners: number[],                        // winning seat indices for this pot
 *     distributions: Array<{seat, amount}>,     // final chip distribution
 *     contested: boolean,                       // true if 2+ eligible players
 *   }>,
 *   totalAwarded: number,                       // must equal sum of invested
 *   accountingOk: boolean,                      // totalAwarded == sum(invested)
 * }
 */
function computeShowdown({ players, board, seatOrder }) {
  if (board.length !== 5) {
    throw new Error(`Showdown requires 5 board cards, got ${board.length}`);
  }

  // 1. Calculate pot structure
  const pots = calculatePots(players);

  // 2. Evaluate each non-folded player's hand
  const evaluated = new Map(); // seat → evaluation result
  const reveals = [];

  for (const p of players) {
    if (p.folded || !p.holeCards) continue;

    const allCards = [...p.holeCards, ...board];
    const result = evaluateHand(allCards);
    evaluated.set(p.seat, result);
    reveals.push({
      seat: p.seat,
      handName: result.handName,
      category: result.category,
      ranks: result.ranks,
      bestFive: result.bestFive,
    });
  }

  // 3. Determine winners per pot
  const winnersPerPot = [];
  const potResults = [];

  for (let i = 0; i < pots.length; i++) {
    const pot = pots[i];
    const contested = pot.eligible.length >= 2;

    if (!contested) {
      // Uncontested: sole eligible player wins automatically
      winnersPerPot.push(pot.eligible.slice());
      potResults.push({
        potIndex: i,
        amount: pot.amount,
        winners: pot.eligible.slice(),
        distributions: [], // filled by awardPots below
        contested: false,
      });
      continue;
    }

    // Contested: compare evaluated hands among eligible players
    const eligibleEvals = pot.eligible.map((seat) => {
      const ev = evaluated.get(seat);
      if (!ev) throw new Error(`No evaluation for eligible seat ${seat}`);
      return ev;
    });

    const winnerIndices = findWinners(eligibleEvals);
    const winnerSeats = winnerIndices.map((idx) => pot.eligible[idx]);

    winnersPerPot.push(winnerSeats);
    potResults.push({
      potIndex: i,
      amount: pot.amount,
      winners: winnerSeats,
      distributions: [], // filled below
      contested: true,
    });
  }

  // 4. Compute chip distributions (with odd-chip rule)
  const { awards, total } = awardPots(pots, winnersPerPot, seatOrder);

  // Merge distributions into potResults
  for (const award of awards) {
    potResults[award.potIndex].distributions = award.distributions;
  }

  // 5. Accounting check
  const investedTotal = players.reduce((s, p) => s + p.invested, 0);
  const accountingOk = total === investedTotal && verifyPotAccounting(pots, players);

  return {
    pots,
    reveals,
    potResults,
    totalAwarded: total,
    accountingOk,
  };
}

module.exports = { computeShowdown };
