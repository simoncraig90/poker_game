"use strict";

const events = require("./events");

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

module.exports = { settleNoShowdown };
