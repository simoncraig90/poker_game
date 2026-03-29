"use strict";

const { SEAT_STATUS } = require("./types");

function createSeat(index) {
  return {
    seat: index,
    status: SEAT_STATUS.EMPTY,
    player: null,
    stack: 0,
    // Per-hand (reset each hand)
    inHand: false,
    folded: false,
    allIn: false,
    bet: 0,
    totalInvested: 0,
    holeCards: null,
  };
}

function createTable({ tableId, tableName, maxSeats = 6, sb, bb, minBuyIn, maxBuyIn }) {
  const seats = {};
  for (let i = 0; i < maxSeats; i++) {
    seats[i] = createSeat(i);
  }

  return {
    tableId,
    tableName,
    gameType: 2, // NL Hold'em
    maxSeats,
    sb,
    bb,
    minBuyIn,
    maxBuyIn,
    seats,
    hand: null,
    button: -1, // set on first hand
    handsPlayed: 0,
  };
}

function sitDown(table, seatIndex, playerName, buyIn, country) {
  if (seatIndex < 0 || seatIndex >= table.maxSeats) {
    throw new Error(`Invalid seat index: ${seatIndex}`);
  }
  const seat = table.seats[seatIndex];
  if (seat.status !== SEAT_STATUS.EMPTY) {
    throw new Error(`Seat ${seatIndex} is not empty`);
  }
  if (buyIn < table.minBuyIn || buyIn > table.maxBuyIn) {
    throw new Error(`Buy-in ${buyIn} out of range [${table.minBuyIn}, ${table.maxBuyIn}]`);
  }
  if (table.hand && table.hand.phase !== "COMPLETE") {
    // Allow seating between hands or when hand is complete
    // (In common path, we seat before starting any hand)
  }

  seat.status = SEAT_STATUS.OCCUPIED;
  seat.player = { name: playerName, country: country || "XX", avatarId: null };
  seat.stack = buyIn;
}

function leave(table, seatIndex) {
  const seat = table.seats[seatIndex];
  if (!seat || seat.status === SEAT_STATUS.EMPTY) return;
  if (seat.inHand) throw new Error(`Seat ${seatIndex} is in a hand, cannot leave`);

  seat.status = SEAT_STATUS.EMPTY;
  seat.player = null;
  seat.stack = 0;
}

function getOccupiedSeats(table) {
  return Object.values(table.seats).filter((s) => s.status === SEAT_STATUS.OCCUPIED);
}

function resetHandState(seat) {
  seat.inHand = false;
  seat.folded = false;
  seat.allIn = false;
  seat.bet = 0;
  seat.totalInvested = 0;
  seat.holeCards = null;
}

module.exports = { createTable, createSeat, sitDown, leave, getOccupiedSeats, resetHandState };
