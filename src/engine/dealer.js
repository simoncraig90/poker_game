"use strict";

const { SEAT_STATUS } = require("./types");

// Advance clockwise from `from`, skipping empty seats. Returns the next occupied seat index.
function nextOccupied(from, seats, maxSeats) {
  for (let i = 1; i <= maxSeats; i++) {
    const idx = (from + i) % maxSeats;
    if (seats[idx] && seats[idx].status === SEAT_STATUS.OCCUPIED) {
      return idx;
    }
  }
  return -1; // no occupied seat found
}

function nextButton(currentButton, seats, maxSeats) {
  // First hand: pick first occupied seat
  if (currentButton < 0) {
    for (let i = 0; i < maxSeats; i++) {
      if (seats[i] && seats[i].status === SEAT_STATUS.OCCUPIED) return i;
    }
    return -1;
  }
  return nextOccupied(currentButton, seats, maxSeats);
}

function assignBlinds(button, seats, maxSeats) {
  const occupied = Object.values(seats).filter((s) => s.status === SEAT_STATUS.OCCUPIED);
  if (occupied.length < 2) throw new Error("Need at least 2 players for blinds");

  if (occupied.length === 2) {
    // Heads-up: button posts SB, other posts BB
    const sbSeat = button;
    const bbSeat = nextOccupied(button, seats, maxSeats);
    return { sbSeat, bbSeat };
  }

  // 3+ players: SB is first clockwise from button, BB is next after SB
  const sbSeat = nextOccupied(button, seats, maxSeats);
  const bbSeat = nextOccupied(sbSeat, seats, maxSeats);
  return { sbSeat, bbSeat };
}

// Build preflop action order: starts after BB, wraps around, ends at BB
function preflopActionOrder(button, sbSeat, bbSeat, seats, maxSeats) {
  const order = [];
  let current = bbSeat;
  for (let i = 0; i < maxSeats; i++) {
    current = nextOccupied(current, seats, maxSeats);
    if (current === -1) break;
    order.push(current);
    if (current === bbSeat) break; // wrapped around to BB
  }
  return order;
}

// Build postflop action order: starts at first active seat clockwise from button
function postflopActionOrder(button, seats, maxSeats) {
  const order = [];
  let current = button;
  for (let i = 0; i < maxSeats; i++) {
    current = nextOccupied(current, seats, maxSeats);
    if (current === -1) break;
    if (order.includes(current)) break; // wrapped
    order.push(current);
  }
  return order;
}

module.exports = { nextButton, assignBlinds, preflopActionOrder, postflopActionOrder, nextOccupied };
