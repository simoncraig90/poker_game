"use strict";

const { EVENT } = require("./types");

let globalSeq = 0;

function makeSource() {
  return { origin: "engine", ts: Date.now() };
}

function base(sessionId, handId, type) {
  return { sessionId, handId, seq: globalSeq++, type, _source: makeSource() };
}

function resetSeq() {
  globalSeq = 0;
}

// ── Event Factories ────────────────────────────────────────────────────────

function tableSnapshot(sessionId, table) {
  const seats = [];
  for (let i = 0; i < table.maxSeats; i++) {
    const s = table.seats[i];
    seats.push(s ? {
      seat: i, status: s.status,
      player: s.player ? { name: s.player.name, country: s.player.country, stack: s.stack } : null,
      bet: s.bet,
    } : { seat: i, status: "EMPTY", player: null, bet: 0 });
  }
  return {
    ...base(sessionId, null, EVENT.TABLE_SNAPSHOT),
    tableId: table.tableId, tableName: table.tableName,
    gameType: table.gameType, maxSeats: table.maxSeats,
    sb: table.sb, bb: table.bb, minBuyIn: table.minBuyIn, maxBuyIn: table.maxBuyIn,
    seats, button: table.button,
  };
}

function handStart(sessionId, handId, table, playerMap) {
  resetSeq();
  return {
    ...base(sessionId, handId, EVENT.HAND_START),
    tableId: table.tableId, tableName: table.tableName,
    button: table.button, sb: table.sb, bb: table.bb,
    players: playerMap,
  };
}

function blindPost(sessionId, handId, seat, playerName, amount, blindType) {
  return {
    ...base(sessionId, handId, EVENT.BLIND_POST),
    seat, player: playerName, amount, blindType, street: "PREFLOP",
  };
}

function heroCards(sessionId, handId, seat, cards) {
  return {
    ...base(sessionId, handId, EVENT.HERO_CARDS),
    seat, cards: cards.map((c) => c.display),
  };
}

function playerAction(sessionId, handId, seat, playerName, action, totalBet, delta, street, inferred) {
  return {
    ...base(sessionId, handId, EVENT.PLAYER_ACTION),
    seat, player: playerName, action, totalBet, delta, street,
    inferred: inferred || false,
  };
}

function betReturn(sessionId, handId, seat, playerName, amount) {
  return {
    ...base(sessionId, handId, EVENT.BET_RETURN),
    seat, player: playerName, amount,
  };
}

function dealCommunity(sessionId, handId, street, newCards, board) {
  return {
    ...base(sessionId, handId, EVENT.DEAL_COMMUNITY),
    street, newCards: newCards.map((c) => c.display), board: board.map((c) => c.display),
  };
}

function potAward(sessionId, handId, potIndex, awards) {
  return {
    ...base(sessionId, handId, EVENT.POT_AWARD),
    potIndex, awards,
  };
}

function showdownReveal(sessionId, handId, reveals) {
  // reveals: [{ seat, player, cards: ["As","Kh"], handName, bestFive: ["As",...] }]
  return { ...base(sessionId, handId, EVENT.SHOWDOWN_REVEAL), reveals };
}

function handSummary(sessionId, handId, winSeat, winPlayer, showdown, totalPot, board, handRank, winCards) {
  return {
    ...base(sessionId, handId, EVENT.HAND_SUMMARY),
    winSeat, winPlayer, showdown, totalPot,
    handRank: handRank || null,
    winCards: winCards || null,
    board: board.length > 0 ? board.map((c) => c.display || c) : null,
  };
}

function handResult(sessionId, handId, potIndex, results) {
  return {
    ...base(sessionId, handId, EVENT.HAND_RESULT),
    potIndex, results,
  };
}

function handEnd(sessionId, handId, tableId) {
  return { ...base(sessionId, handId, EVENT.HAND_END), tableId };
}

function seatPlayer(sessionId, seatIndex, playerName, buyIn, country, actorId) {
  return {
    ...base(sessionId, null, EVENT.SEAT_PLAYER),
    seat: seatIndex, player: playerName, buyIn, country: country || "XX",
    actorId: actorId || null,
  };
}

function leaveTable(sessionId, seatIndex, playerName) {
  return {
    ...base(sessionId, null, EVENT.LEAVE_TABLE),
    seat: seatIndex, player: playerName,
  };
}

module.exports = {
  resetSeq, tableSnapshot, handStart, blindPost, heroCards,
  playerAction, betReturn, dealCommunity, potAward, showdownReveal, handSummary, handResult, handEnd,
  seatPlayer, leaveTable,
};
