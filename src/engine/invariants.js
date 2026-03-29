"use strict";

const { PHASE, phaseIndex } = require("./types");

/**
 * Check reducer invariants. Returns { passed, violations[] }.
 * Call after every state transition in debug mode.
 */
function checkInvariants(table) {
  const violations = [];

  // INV-1: Stack non-negativity
  for (const seat of Object.values(table.seats)) {
    if (seat.stack < 0) {
      violations.push(`INV-1: Seat ${seat.seat} stack is ${seat.stack} (negative)`);
    }
  }

  const hand = table.hand;
  if (!hand) return { passed: violations.length === 0, violations };

  // INV-2: Pot non-negativity
  if (hand.pot < 0) {
    violations.push(`INV-2: Pot is ${hand.pot} (negative)`);
  }

  // INV-3: Active player count consistency
  const active = Object.values(table.seats).filter(
    (s) => s.inHand && !s.folded && s.status === "OCCUPIED"
  );
  if (hand.phase !== PHASE.COMPLETE && hand.phase !== PHASE.SETTLING) {
    if (active.length < 1 && hand.phase !== PHASE.SHOWDOWN) {
      violations.push(`INV-3: ${active.length} active players during ${hand.phase}`);
    }
  }

  // INV-4: Street ordering (phase must not go backward)
  // This is checked at transition time, not statically here.

  // INV-6: Bet reset on street change — verified at deal time

  return { passed: violations.length === 0, violations };
}

/**
 * Check closed accounting at hand end.
 * sum(startStacks) == sum(endStacks) + rake
 */
function checkAccountingClosure(table, startStacks, rake) {
  const violations = [];
  let startTotal = 0;
  let endTotal = 0;

  for (const [seat, startStack] of Object.entries(startStacks)) {
    startTotal += startStack;
    const s = table.seats[seat];
    if (s) endTotal += s.stack;
  }

  if (startTotal !== endTotal + rake) {
    violations.push(
      `INV-5: Accounting not closed. start=${startTotal} end=${endTotal} rake=${rake} diff=${startTotal - endTotal - rake}`
    );
  }

  return { passed: violations.length === 0, violations };
}

module.exports = { checkInvariants, checkAccountingClosure };
