"use strict";

const events = require("./events");
const { computeShowdown } = require("./showdown");

/**
 * Settle a hand where all but one player folded (no showdown).
 * Returns array of events to emit.
 */
function settleNoShowdown(sessionId, handId, table, hand, winnerSeat) {
  const emitted = [];
  const winner = table.seats[winnerSeat];

  // POT_AWARD
  const awards = [{ seat: winnerSeat, player: winner.player.name, amount: hand.pot }];
  emitted.push(events.potAward(sessionId, handId, 0, awards));

  // Apply to state
  winner.stack += hand.pot;

  // HAND_SUMMARY
  emitted.push(events.handSummary(
    sessionId, handId, winnerSeat, winner.player.name,
    false, hand.pot, hand.board
  ));

  // HAND_RESULT — one entry per player in hand
  const results = [];
  for (const seat of Object.values(table.seats)) {
    if (!seat.inHand) continue;
    if (seat.seat === winnerSeat) {
      results.push({ seat: seat.seat, player: seat.player.name, won: true, amount: hand.pot, text: "Takes down main pot." });
    } else {
      results.push({ seat: seat.seat, player: seat.player.name, won: false, amount: 0, text: "Loses main pot and mucks cards." });
    }
  }
  emitted.push(events.handResult(sessionId, handId, 0, results));

  // HAND_END
  emitted.push(events.handEnd(sessionId, handId, table.tableId));

  return emitted;
}

/**
 * Settle a hand at showdown (2+ players remain after river).
 * Evaluates hands, computes pots, distributes awards.
 * Returns array of events to emit. Mutates seat stacks.
 *
 * @param {string} sessionId
 * @param {string} handId
 * @param {object} table - table state (seats with holeCards, stacks, etc.)
 * @param {object} hand - hand state (board, pot)
 * @param {number[]} seatOrder - seats clockwise from button (for odd-chip)
 */
function settleShowdown(sessionId, handId, table, hand, seatOrder) {
  const emitted = [];

  // Build player data for computeShowdown
  const players = [];
  for (const seat of Object.values(table.seats)) {
    if (!seat.inHand) continue;
    players.push({
      seat: seat.seat,
      invested: seat.totalInvested,
      folded: seat.folded,
      holeCards: seat.holeCards,
    });
  }

  // Board cards: hand.board contains {rank, suit, display} or display strings.
  // Normalize to card objects if needed.
  const board = hand.board.map((card) => {
    if (typeof card === "object" && card.rank) return card;
    throw new Error(`Board card is not a card object: ${JSON.stringify(card)}`);
  });

  const settlement = computeShowdown({ players, board, seatOrder });

  if (!settlement.accountingOk) {
    console.error("SHOWDOWN ACCOUNTING VIOLATION", settlement);
  }

  // 1. SHOWDOWN_REVEAL — reveal all active players' cards and hand ranks
  const reveals = settlement.reveals.map((r) => {
    const seat = table.seats[r.seat];
    return {
      seat: r.seat,
      player: seat.player.name,
      cards: seat.holeCards.map((c) => c.display),
      handName: r.handName,
      bestFive: r.bestFive.map((c) => c.display),
    };
  });
  emitted.push(events.showdownReveal(sessionId, handId, reveals));

  // 2. POT_AWARD per pot + apply stack changes
  for (const pr of settlement.potResults) {
    const awards = pr.distributions.map((d) => {
      const seat = table.seats[d.seat];
      return { seat: d.seat, player: seat.player.name, amount: d.amount };
    });
    emitted.push(events.potAward(sessionId, handId, pr.potIndex, awards));

    // Apply winnings to stacks
    for (const d of pr.distributions) {
      table.seats[d.seat].stack += d.amount;
    }
  }

  // 3. HAND_SUMMARY — overall winner (winner of the main pot, or largest pot)
  // Find the primary winner: winner of pot 0 (main pot), first in list
  const mainWinners = settlement.potResults[0].winners;
  const mainWinnerSeat = mainWinners[0];
  const mainWinner = table.seats[mainWinnerSeat];
  const mainReveal = settlement.reveals.find((r) => r.seat === mainWinnerSeat);
  const totalPot = settlement.totalAwarded;

  emitted.push(events.handSummary(
    sessionId, handId,
    mainWinnerSeat, mainWinner.player.name,
    true, totalPot, hand.board,
    mainReveal ? mainReveal.handName : null,
    mainReveal ? mainReveal.bestFive.map((c) => c.display) : null
  ));

  // 4. HAND_RESULT per pot
  for (const pr of settlement.potResults) {
    const results = [];
    // Include all eligible players for this pot
    const potEligible = settlement.pots[pr.potIndex].eligible;
    for (const seat of potEligible) {
      const s = table.seats[seat];
      const won = pr.winners.includes(seat);
      const dist = pr.distributions.find((d) => d.seat === seat);
      const amount = dist ? dist.amount : 0;
      const reveal = settlement.reveals.find((r) => r.seat === seat);
      const handDesc = reveal ? reveal.handName : "folded";

      let text;
      if (won && pr.contested) {
        text = `Wins ${pr.potIndex === 0 ? "main" : "side"} pot with ${handDesc}.`;
      } else if (won && !pr.contested) {
        text = `Collects uncontested ${pr.potIndex === 0 ? "main" : "side"} pot.`;
      } else {
        text = `Loses ${pr.potIndex === 0 ? "main" : "side"} pot.`;
      }
      results.push({ seat, player: s.player.name, won, amount, text });
    }
    emitted.push(events.handResult(sessionId, handId, pr.potIndex, results));
  }

  // 5. HAND_END
  emitted.push(events.handEnd(sessionId, handId, table.tableId));

  return emitted;
}

module.exports = { settleNoShowdown, settleShowdown };
