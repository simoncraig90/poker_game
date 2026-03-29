"use strict";

const { ACTION } = require("./types");

/**
 * Manages a single betting round (one street).
 * Tracks who has acted, current bet level, and when the round is complete.
 */
class BettingRound {
  constructor(actionOrder, seats, isPreflop) {
    // actionOrder: array of seat indices in action order
    // seats: reference to table.seats
    this.actionOrder = actionOrder;
    this.seats = seats;
    this.isPreflop = isPreflop;

    this.currentBet = 0;      // highest bet on this street
    this.lastRaiseSize = 0;   // size of last raise increment (for min-raise calc)
    this.lastAggressor = -1;  // seat that last bet/raised
    this.actedSince = new Set(); // seats that acted since last bet/raise
    this.actionIndex = 0;     // pointer into actionOrder
    this.started = false;
  }

  /**
   * Set initial state for preflop (after blinds are posted).
   */
  initPreflop(bbAmount, bbSeat) {
    this.currentBet = bbAmount;
    this.lastRaiseSize = bbAmount; // first raise must be at least 1 BB
    this.lastAggressor = bbSeat;   // BB is the "last aggressor" preflop
    // BB hasn't "acted" yet (they get option to raise)
    this.actedSince.clear();
  }

  /**
   * Get the next seat that needs to act. Returns seat index or null if round is over.
   */
  getNextToAct() {
    // Walk through action order, find first seat that still needs to act
    for (let attempts = 0; attempts < this.actionOrder.length * 2; attempts++) {
      if (this.actionIndex >= this.actionOrder.length) {
        this.actionIndex = 0; // wrap
      }

      const seatIdx = this.actionOrder[this.actionIndex];
      const seat = this.seats[seatIdx];

      // Skip folded, all-in, or not-in-hand
      if (!seat || !seat.inHand || seat.folded || seat.allIn) {
        this.actionIndex++;
        continue;
      }

      // Has this seat acted since the last aggression?
      if (this.actedSince.has(seatIdx)) {
        // They've acted and the action came back to them — round is over
        return null;
      }

      return seatIdx;
    }

    return null; // everyone acted or folded
  }

  /**
   * Record that a seat has acted. Returns true if the round is now complete.
   */
  applyAction(seatIdx, action, totalBet, delta) {
    const seat = this.seats[seatIdx];

    switch (action) {
      case ACTION.FOLD:
        seat.folded = true;
        break;

      case ACTION.CHECK:
        this.actedSince.add(seatIdx);
        break;

      case ACTION.CALL:
        this.actedSince.add(seatIdx);
        break;

      case ACTION.BET:
      case ACTION.RAISE: {
        const raiseIncrement = totalBet - this.currentBet;
        this.lastRaiseSize = Math.max(raiseIncrement, this.lastRaiseSize);
        this.currentBet = totalBet;
        this.lastAggressor = seatIdx;
        // Reset: everyone who acted before must act again
        this.actedSince.clear();
        this.actedSince.add(seatIdx); // aggressor has acted
        break;
      }
    }

    this.actionIndex++;
    this.started = true;
  }

  /**
   * Check if the round is complete.
   */
  isComplete() {
    const activePlayers = this.getActivePlayers();

    // Only 1 player left
    if (activePlayers.length <= 1) return true;

    // All active players have acted since last aggression and bets are equalized
    const canAct = activePlayers.filter((s) => !s.allIn);
    if (canAct.length === 0) return true; // everyone all-in

    for (const s of canAct) {
      if (!this.actedSince.has(s.seat)) return false;
    }

    return true;
  }

  getActivePlayers() {
    return this.actionOrder
      .map((idx) => this.seats[idx])
      .filter((s) => s && s.inHand && !s.folded);
  }

  /**
   * Get uncalled bet return info. Returns { seat, amount } or null.
   */
  getUncalledReturn() {
    const active = this.getActivePlayers();
    if (active.length < 1) return null;

    // Find highest and second-highest bets among active (non-folded) players
    const bets = active.map((s) => ({ seat: s.seat, bet: s.bet })).sort((a, b) => b.bet - a.bet);

    if (bets.length < 2) {
      // Only one player — return everything above 0? No, they already won.
      // Actually: if one player has a bet and everyone else folded, the excess is returned.
      // But "excess" means the part above the next-highest bet among ALL players (including folded).
      const allBets = this.actionOrder
        .map((idx) => this.seats[idx])
        .filter((s) => s && s.inHand)
        .map((s) => s.bet)
        .sort((a, b) => b - a);

      if (allBets.length >= 2 && allBets[0] > allBets[1]) {
        const highSeat = active[0].seat;
        return { seat: highSeat, amount: allBets[0] - allBets[1] };
      }
      return null;
    }

    if (bets[0].bet > bets[1].bet) {
      return { seat: bets[0].seat, amount: bets[0].bet - bets[1].bet };
    }

    return null;
  }

  getHandState() {
    return {
      currentBet: this.currentBet,
      lastRaiseSize: this.lastRaiseSize,
    };
  }
}

module.exports = { BettingRound };
