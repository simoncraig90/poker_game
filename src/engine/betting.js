"use strict";

const { ACTION } = require("./types");

/**
 * Get legal actions for a seat given current state.
 * Returns { actions[], callAmount, minBet, minRaise, maxRaise }
 */
function getLegalActions(seat, handState, tableBB) {
  const s = seat;
  if (!s.inHand || s.folded || s.allIn) {
    return { actions: [], callAmount: 0, minBet: 0, minRaise: 0, maxRaise: 0 };
  }

  const currentBet = handState.currentBet || 0; // highest bet this street
  const myBet = s.bet;
  const toCall = currentBet - myBet;
  const stack = s.stack;

  const actions = [ACTION.FOLD]; // can always fold

  if (toCall === 0) {
    // No bet facing: can check or bet
    actions.push(ACTION.CHECK);
    if (stack > 0) {
      actions.push(ACTION.BET);
    }
    return {
      actions,
      callAmount: 0,
      minBet: Math.min(tableBB, stack), // min bet is BB (or all-in if less)
      minRaise: 0,
      maxRaise: 0,
    };
  }

  // Facing a bet
  if (stack > 0) {
    actions.push(ACTION.CALL);
  }

  const minRaiseIncrement = handState.lastRaiseSize || tableBB;
  const minRaiseTotal = currentBet + minRaiseIncrement;
  const raiseMax = myBet + stack; // all-in

  if (raiseMax > currentBet && stack > toCall) {
    // Can raise (have more than enough to call)
    actions.push(ACTION.RAISE);
  }

  return {
    actions,
    callAmount: Math.min(toCall, stack), // may be less if short-stacked
    minBet: 0,
    minRaise: Math.min(minRaiseTotal, raiseMax), // total raise amount (not increment)
    maxRaise: raiseMax,
  };
}

/**
 * Validate and normalize an action command.
 * Returns { valid, action, amount, delta, error }
 */
function validateAction(seatState, action, amount, handState, tableBB) {
  const legal = getLegalActions(seatState, handState, tableBB);

  if (!legal.actions.includes(action)) {
    return { valid: false, error: `${action} not legal. Legal: ${legal.actions.join(", ")}` };
  }

  switch (action) {
    case ACTION.FOLD:
      return { valid: true, action: ACTION.FOLD, amount: 0, delta: 0 };

    case ACTION.CHECK:
      return { valid: true, action: ACTION.CHECK, amount: 0, delta: 0 };

    case ACTION.CALL: {
      const delta = legal.callAmount;
      const totalBet = seatState.bet + delta;
      return { valid: true, action: ACTION.CALL, amount: totalBet, delta };
    }

    case ACTION.BET: {
      if (amount == null) return { valid: false, error: "BET requires amount" };
      if (amount < legal.minBet) {
        // Allow all-in for less
        if (amount !== seatState.stack) {
          return { valid: false, error: `BET ${amount} below minimum ${legal.minBet}` };
        }
      }
      if (amount > seatState.stack) {
        return { valid: false, error: `BET ${amount} exceeds stack ${seatState.stack}` };
      }
      return { valid: true, action: ACTION.BET, amount, delta: amount };
    }

    case ACTION.RAISE: {
      if (amount == null) return { valid: false, error: "RAISE requires amount (total)" };
      // amount is total raise-to (not increment)
      const toCall = (handState.currentBet || 0) - seatState.bet;
      const raiseTotal = amount; // total bet after raise
      const myAllIn = seatState.bet + seatState.stack;

      // Allow all-in for any amount
      if (raiseTotal === myAllIn || amount === seatState.stack + seatState.bet) {
        const delta = seatState.stack;
        return { valid: true, action: ACTION.RAISE, amount: myAllIn, delta };
      }

      if (raiseTotal < legal.minRaise) {
        return { valid: false, error: `RAISE to ${raiseTotal} below minimum ${legal.minRaise}` };
      }
      if (raiseTotal > legal.maxRaise) {
        return { valid: false, error: `RAISE to ${raiseTotal} exceeds max ${legal.maxRaise}` };
      }
      const delta = raiseTotal - seatState.bet;
      return { valid: true, action: ACTION.RAISE, amount: raiseTotal, delta };
    }

    default:
      return { valid: false, error: `Unknown action: ${action}` };
  }
}

module.exports = { getLegalActions, validateAction };
