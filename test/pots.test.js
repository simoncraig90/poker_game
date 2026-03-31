#!/usr/bin/env node
"use strict";

/**
 * Phase 8 — Slice 2: Side-Pot Calculator Tests
 *
 * Pure function tests — no engine coupling.
 */

const { calculatePots, awardPots, verifyPotAccounting } = require("../src/engine/pots");

let checks = 0, passed = 0, failed = 0;

function check(label, cond) {
  checks++;
  if (cond) {
    passed++;
  } else {
    failed++;
    console.log(`  FAIL: ${label}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 1: Basic Pot Calculation
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 1: Basic Pot Calculation ===");

// T1: 2 players, equal investment → single pot
{
  const players = [
    { seat: 0, invested: 100, folded: false },
    { seat: 1, invested: 100, folded: false },
  ];
  const pots = calculatePots(players);
  check("T1: single pot", pots.length === 1);
  check("T1: amount 200", pots[0].amount === 200);
  check("T1: both eligible", pots[0].eligible.length === 2);
  check("T1: accounting", verifyPotAccounting(pots, players));
}

// T2: 2 players, one folds after partial investment → single pot, folder ineligible
{
  const players = [
    { seat: 0, invested: 50, folded: true },
    { seat: 1, invested: 100, folded: false },
  ];
  const pots = calculatePots(players);
  check("T2: two tiers", pots.length === 2);
  // Tier 1: 0-50, both contributed → 50 * 2 = 100, only seat 1 eligible
  check("T2: main pot 100", pots[0].amount === 100);
  check("T2: main eligible [1]", pots[0].eligible.length === 1 && pots[0].eligible[0] === 1);
  // Tier 2: 50-100, only seat 1 → 50 * 1 = 50
  check("T2: side pot 50", pots[1].amount === 50);
  check("T2: side eligible [1]", pots[1].eligible.length === 1 && pots[1].eligible[0] === 1);
  check("T2: accounting", verifyPotAccounting(pots, players));
}

// T3: 3 players, equal investment
{
  const players = [
    { seat: 0, invested: 100, folded: false },
    { seat: 1, invested: 100, folded: false },
    { seat: 2, invested: 100, folded: false },
  ];
  const pots = calculatePots(players);
  check("T3: single pot", pots.length === 1);
  check("T3: amount 300", pots[0].amount === 300);
  check("T3: all 3 eligible", pots[0].eligible.length === 3);
  check("T3: accounting", verifyPotAccounting(pots, players));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 2: Multi-Way All-In (Uneven Stacks)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 2: Multi-Way All-In ===");

// T4: Classic 3-way uneven all-in (plan example)
// Alice 100, Bob 300, Charlie 500
{
  const players = [
    { seat: 0, invested: 100, folded: false },
    { seat: 1, invested: 300, folded: false },
    { seat: 2, invested: 500, folded: false },
  ];
  const pots = calculatePots(players);
  check("T4: 3 pots", pots.length === 3);
  // Main: 100 * 3 = 300, all eligible
  check("T4: main 300", pots[0].amount === 300);
  check("T4: main all eligible", pots[0].eligible.length === 3);
  // Side 1: 200 * 2 = 400, Bob + Charlie
  check("T4: side1 400", pots[1].amount === 400);
  check("T4: side1 seats 1,2", pots[1].eligible.includes(1) && pots[1].eligible.includes(2) && pots[1].eligible.length === 2);
  // Side 2: 200 * 1 = 200, Charlie only (uncontested)
  check("T4: side2 200", pots[2].amount === 200);
  check("T4: side2 seat 2 only", pots[2].eligible.length === 1 && pots[2].eligible[0] === 2);
  check("T4: accounting", verifyPotAccounting(pots, players));
  check("T4: total 900", pots.reduce((s, p) => s + p.amount, 0) === 900);
}

// T5: 4-way uneven all-in
// Seat 0: 50, Seat 1: 100, Seat 2: 300, Seat 3: 500
{
  const players = [
    { seat: 0, invested: 50,  folded: false },
    { seat: 1, invested: 100, folded: false },
    { seat: 2, invested: 300, folded: false },
    { seat: 3, invested: 500, folded: false },
  ];
  const pots = calculatePots(players);
  check("T5: 4 pots", pots.length === 4);
  // Main: 50 * 4 = 200, all 4
  check("T5: main 200", pots[0].amount === 200);
  check("T5: main 4 eligible", pots[0].eligible.length === 4);
  // Side 1: 50 * 3 = 150, seats 1,2,3
  check("T5: side1 150", pots[1].amount === 150);
  check("T5: side1 3 eligible", pots[1].eligible.length === 3 && !pots[1].eligible.includes(0));
  // Side 2: 200 * 2 = 400, seats 2,3
  check("T5: side2 400", pots[2].amount === 400);
  check("T5: side2 2 eligible", pots[2].eligible.length === 2);
  // Side 3: 200 * 1 = 200, seat 3 only
  check("T5: side3 200", pots[3].amount === 200);
  check("T5: side3 1 eligible", pots[3].eligible.length === 1 && pots[3].eligible[0] === 3);
  check("T5: accounting", verifyPotAccounting(pots, players));
  check("T5: total 950", pots.reduce((s, p) => s + p.amount, 0) === 950);
}

// T6: 2-way all-in, uneven stacks
{
  const players = [
    { seat: 0, invested: 200, folded: false },
    { seat: 1, invested: 500, folded: false },
  ];
  const pots = calculatePots(players);
  check("T6: 2 pots", pots.length === 2);
  check("T6: main 400", pots[0].amount === 400);
  check("T6: main both eligible", pots[0].eligible.length === 2);
  check("T6: side 300", pots[1].amount === 300);
  check("T6: side seat 1 only (uncontested)", pots[1].eligible.length === 1 && pots[1].eligible[0] === 1);
  check("T6: accounting", verifyPotAccounting(pots, players));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 3: Folded Player Dead Money
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 3: Folded Player Dead Money ===");

// T7: 3 players, one folds early with small investment
{
  const players = [
    { seat: 0, invested: 10,  folded: true },
    { seat: 1, invested: 200, folded: false },
    { seat: 2, invested: 200, folded: false },
  ];
  const pots = calculatePots(players);
  // Tier 10: 10 * 3 = 30, eligible: seats 1,2
  // Tier 200: 190 * 2 = 380, eligible: seats 1,2
  check("T7: 2 tiers", pots.length === 2);
  check("T7: tier1 30", pots[0].amount === 30);
  check("T7: tier1 eligible [1,2]", pots[0].eligible.length === 2 && !pots[0].eligible.includes(0));
  check("T7: tier2 380", pots[1].amount === 380);
  check("T7: total 410", pots.reduce((s, p) => s + p.amount, 0) === 410);
  check("T7: accounting", verifyPotAccounting(pots, players));
}

// T8: Multiple folders at different investment levels
{
  const players = [
    { seat: 0, invested: 10,  folded: true },   // folded preflop
    { seat: 1, invested: 50,  folded: true },   // folded flop
    { seat: 2, invested: 200, folded: false },
    { seat: 3, invested: 200, folded: false },
  ];
  const pots = calculatePots(players);
  // Tier 10: 10 * 4 = 40
  // Tier 50: 40 * 3 = 120
  // Tier 200: 150 * 2 = 300
  check("T8: 3 tiers", pots.length === 3);
  check("T8: tier1 40", pots[0].amount === 40);
  check("T8: tier2 120", pots[1].amount === 120);
  check("T8: tier3 300", pots[2].amount === 300);
  // All pots: only seats 2,3 eligible
  check("T8: no folder eligible in any pot",
    pots.every((p) => p.eligible.length === 2 && p.eligible.includes(2) && p.eligible.includes(3)));
  check("T8: accounting", verifyPotAccounting(pots, players));
  check("T8: total 460", pots.reduce((s, p) => s + p.amount, 0) === 460);
}

// T9: Folder invested more than an active player (folder contributes to main pot)
{
  const players = [
    { seat: 0, invested: 150, folded: true },   // folded on turn after investing 150
    { seat: 1, invested: 100, folded: false },   // all-in for 100
    { seat: 2, invested: 300, folded: false },
  ];
  const pots = calculatePots(players);
  // Tier 100: 100 * 3 = 300, eligible: 1, 2
  // Tier 150: 50 * 2 = 100, eligible: 2
  // Tier 300: 150 * 1 = 150, eligible: 2
  check("T9: 3 tiers", pots.length === 3);
  check("T9: main 300", pots[0].amount === 300);
  check("T9: main eligible [1,2]", pots[0].eligible.length === 2 && pots[0].eligible.includes(1) && pots[0].eligible.includes(2));
  check("T9: side1 100", pots[1].amount === 100);
  check("T9: side1 eligible [2]", pots[1].eligible.length === 1 && pots[1].eligible[0] === 2);
  check("T9: side2 150", pots[2].amount === 150);
  check("T9: accounting", verifyPotAccounting(pots, players));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 4: Award Distribution
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 4: Award Distribution ===");

// T10: Simple single-pot, single winner
{
  const pots = [{ amount: 300, eligible: [0, 1, 2] }];
  const winnersPerPot = [[1]]; // seat 1 wins
  const seatOrder = [0, 1, 2]; // clockwise from button
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T10: 1 award group", result.awards.length === 1);
  check("T10: seat 1 gets 300", result.awards[0].distributions.length === 1 && result.awards[0].distributions[0].seat === 1 && result.awards[0].distributions[0].amount === 300);
  check("T10: total 300", result.total === 300);
}

// T11: Single pot, 2-way split
{
  const pots = [{ amount: 300, eligible: [0, 1, 2] }];
  const winnersPerPot = [[0, 2]];
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T11: 2 distributions", result.awards[0].distributions.length === 2);
  check("T11: seat 0 gets 150", result.awards[0].distributions[0].amount === 150);
  check("T11: seat 2 gets 150", result.awards[0].distributions[1].amount === 150);
  check("T11: total 300", result.total === 300);
}

// T12: Multi-pot, different winners per pot
{
  // Main: 300, eligible [0,1,2], winner seat 0
  // Side: 400, eligible [1,2], winner seat 2
  const pots = [
    { amount: 300, eligible: [0, 1, 2] },
    { amount: 400, eligible: [1, 2] },
  ];
  const winnersPerPot = [[0], [2]];
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T12: 2 award groups", result.awards.length === 2);
  check("T12: pot 0 → seat 0 gets 300", result.awards[0].distributions[0].seat === 0 && result.awards[0].distributions[0].amount === 300);
  check("T12: pot 1 → seat 2 gets 400", result.awards[1].distributions[0].seat === 2 && result.awards[1].distributions[0].amount === 400);
  check("T12: total 700", result.total === 700);
}

// T13: Uncontested side pot (1 eligible, 1 winner)
{
  const pots = [
    { amount: 300, eligible: [0, 1, 2] },
    { amount: 200, eligible: [2] },
  ];
  const winnersPerPot = [[1], [2]]; // main→seat1, side→seat2 (auto)
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T13: uncontested pot returned", result.awards[1].distributions[0].seat === 2 && result.awards[1].distributions[0].amount === 200);
  check("T13: total 500", result.total === 500);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 5: Odd-Chip Handling
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 5: Odd-Chip Handling ===");

// T14: Odd chip, 2-way split, pot=101
{
  const pots = [{ amount: 101, eligible: [0, 1] }];
  const winnersPerPot = [[0, 1]];
  const seatOrder = [0, 1, 2]; // seat 0 is first clockwise from button
  const result = awardPots(pots, winnersPerPot, seatOrder);
  // seat 0 is first in order → gets the extra chip
  check("T14: seat 0 gets 51 (odd chip)", result.awards[0].distributions[0].seat === 0 && result.awards[0].distributions[0].amount === 51);
  check("T14: seat 1 gets 50", result.awards[0].distributions[1].seat === 1 && result.awards[0].distributions[1].amount === 50);
  check("T14: total 101", result.total === 101);
}

// T15: Odd chip, 3-way split, pot=100
{
  const pots = [{ amount: 100, eligible: [0, 1, 2] }];
  const winnersPerPot = [[0, 1, 2]];
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  // 100 / 3 = 33 remainder 1 → seat 0 gets 34, others get 33
  check("T15: seat 0 gets 34", result.awards[0].distributions[0].amount === 34);
  check("T15: seat 1 gets 33", result.awards[0].distributions[1].amount === 33);
  check("T15: seat 2 gets 33", result.awards[0].distributions[2].amount === 33);
  check("T15: total 100", result.total === 100);
}

// T16: Odd chip, 3-way split, pot=101 (remainder 2 → two players get extra)
{
  const pots = [{ amount: 101, eligible: [0, 1, 2] }];
  const winnersPerPot = [[0, 1, 2]];
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  // 101 / 3 = 33 remainder 2 → seats 0,1 get 34, seat 2 gets 33
  check("T16: seat 0 gets 34", result.awards[0].distributions[0].amount === 34);
  check("T16: seat 1 gets 34", result.awards[0].distributions[1].amount === 34);
  check("T16: seat 2 gets 33", result.awards[0].distributions[2].amount === 33);
  check("T16: total 101", result.total === 101);
}

// T17: Odd chip respects seat order — button-relative
{
  // Seat order: [3, 0, 1, 2] (seat 3 is first clockwise from button)
  // Winners: seats 0 and 2, pot=101
  // In seat order: seat 0 comes before seat 2
  const pots = [{ amount: 101, eligible: [0, 1, 2, 3] }];
  const winnersPerPot = [[0, 2]];
  const seatOrder = [3, 0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  // seat 0 is earlier in order than seat 2 → seat 0 gets extra chip
  check("T17: seat 0 gets 51 (first in button order)", result.awards[0].distributions[0].seat === 0 && result.awards[0].distributions[0].amount === 51);
  check("T17: seat 2 gets 50", result.awards[0].distributions[1].seat === 2 && result.awards[0].distributions[1].amount === 50);
  check("T17: total 101", result.total === 101);
}

// T18: Odd chip in side pot (not just main)
{
  const pots = [
    { amount: 300, eligible: [0, 1, 2] },
    { amount: 101, eligible: [1, 2] },
  ];
  const winnersPerPot = [[0], [1, 2]]; // main→0, side split between 1 and 2
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T18: side pot seat 1 gets 51", result.awards[1].distributions[0].seat === 1 && result.awards[1].distributions[0].amount === 51);
  check("T18: side pot seat 2 gets 50", result.awards[1].distributions[1].seat === 2 && result.awards[1].distributions[1].amount === 50);
  check("T18: total 401", result.total === 401);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 6: Complex Scenarios
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 6: Complex Scenarios ===");

// T19: Tied main pot with single-winner side pot
{
  // 3 players: seat 0 invested 100, seat 1 invested 300, seat 2 invested 300
  const players = [
    { seat: 0, invested: 100, folded: false },
    { seat: 1, invested: 300, folded: false },
    { seat: 2, invested: 300, folded: false },
  ];
  const pots = calculatePots(players);
  check("T19: 2 pots", pots.length === 2);
  check("T19: main 300", pots[0].amount === 300);
  check("T19: side 400", pots[1].amount === 400);

  // Main pot tied between seats 0 and 1, side pot won by seat 2
  const winnersPerPot = [[0, 1], [2]];
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T19: main split 150/150", result.awards[0].distributions[0].amount === 150 && result.awards[0].distributions[1].amount === 150);
  check("T19: side 400 to seat 2", result.awards[1].distributions[0].seat === 2 && result.awards[1].distributions[0].amount === 400);
  check("T19: total 700", result.total === 700);
  check("T19: accounting", verifyPotAccounting(pots, players));
}

// T20: Single-winner main pot with tied side pot
{
  const players = [
    { seat: 0, invested: 100, folded: false },
    { seat: 1, invested: 300, folded: false },
    { seat: 2, invested: 300, folded: false },
  ];
  const pots = calculatePots(players);

  // Main pot won by seat 0, side pot tied between 1 and 2
  const winnersPerPot = [[0], [1, 2]];
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T20: main 300 to seat 0", result.awards[0].distributions[0].seat === 0 && result.awards[0].distributions[0].amount === 300);
  check("T20: side split 200/200", result.awards[1].distributions[0].amount === 200 && result.awards[1].distributions[1].amount === 200);
  check("T20: total 700", result.total === 700);
}

// T21: Board-play scenario — all players tie every pot
{
  const players = [
    { seat: 0, invested: 100, folded: false },
    { seat: 1, invested: 300, folded: false },
    { seat: 2, invested: 300, folded: false },
  ];
  const pots = calculatePots(players);

  // All 3 tie main, seats 1,2 tie side
  const winnersPerPot = [[0, 1, 2], [1, 2]];
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T21: main 3-way split 100 each", result.awards[0].distributions.every((d) => d.amount === 100));
  check("T21: side 2-way split 200 each", result.awards[1].distributions.every((d) => d.amount === 200));
  check("T21: total 700", result.total === 700);
}

// T22: 5-player scenario — 2 fold, 3 all-in uneven
{
  const players = [
    { seat: 0, invested: 10,   folded: true },
    { seat: 1, invested: 30,   folded: true },
    { seat: 2, invested: 100,  folded: false },
    { seat: 3, invested: 200,  folded: false },
    { seat: 4, invested: 500,  folded: false },
  ];
  const pots = calculatePots(players);
  // Tiers at: 10, 30, 100, 200, 500
  // Tier 10: 10*5=50, eligible: 2,3,4
  // Tier 30: 20*4=80, eligible: 2,3,4
  // Tier 100: 70*3=210, eligible: 2,3,4
  // Tier 200: 100*2=200, eligible: 3,4
  // Tier 500: 300*1=300, eligible: 4
  check("T22: 5 tiers", pots.length === 5);
  check("T22: tier amounts", pots[0].amount === 50 && pots[1].amount === 80 && pots[2].amount === 210 && pots[3].amount === 200 && pots[4].amount === 300);
  check("T22: folders never eligible", pots.every((p) => !p.eligible.includes(0) && !p.eligible.includes(1)));
  check("T22: accounting", verifyPotAccounting(pots, players));
  check("T22: total 840", pots.reduce((s, p) => s + p.amount, 0) === 840);
}

// T23: All players fold except one (trivial — no showdown, but pots still calculable)
{
  const players = [
    { seat: 0, invested: 50,  folded: true },
    { seat: 1, invested: 100, folded: true },
    { seat: 2, invested: 200, folded: false },
  ];
  const pots = calculatePots(players);
  // All pots' only eligible is seat 2
  check("T23: all pots eligible only seat 2", pots.every((p) => p.eligible.length === 1 && p.eligible[0] === 2));
  check("T23: accounting", verifyPotAccounting(pots, players));
  check("T23: total 350", pots.reduce((s, p) => s + p.amount, 0) === 350);
}

// T24: Two players same all-in amount among a larger field
{
  const players = [
    { seat: 0, invested: 100, folded: false },
    { seat: 1, invested: 100, folded: false },
    { seat: 2, invested: 300, folded: false },
  ];
  const pots = calculatePots(players);
  check("T24: 2 pots (100 and 300 tiers)", pots.length === 2);
  check("T24: main 300", pots[0].amount === 300);
  check("T24: main 3 eligible", pots[0].eligible.length === 3);
  check("T24: side 200", pots[1].amount === 200);
  check("T24: side 1 eligible (seat 2)", pots[1].eligible.length === 1 && pots[1].eligible[0] === 2);
  check("T24: accounting", verifyPotAccounting(pots, players));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 7: Accounting Invariant
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 7: Accounting Invariant ===");

// T25: Award total equals pot total in multi-pot split with odd chips
{
  const players = [
    { seat: 0, invested: 100, folded: false },
    { seat: 1, invested: 301, folded: false },
    { seat: 2, invested: 301, folded: false },
  ];
  const pots = calculatePots(players);
  const potTotal = pots.reduce((s, p) => s + p.amount, 0);
  check("T25: pot total = invested total", verifyPotAccounting(pots, players));

  // All tie everywhere
  const winnersPerPot = [
    [0, 1, 2],  // main: 3-way
    [1, 2],     // side: 2-way
  ];
  const seatOrder = [0, 1, 2];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T25: award total equals pot total", result.total === potTotal);
}

// T26: Award total equals pot total in 4-way uneven with mixed winners
{
  const players = [
    { seat: 0, invested: 50,  folded: false },
    { seat: 1, invested: 100, folded: false },
    { seat: 2, invested: 300, folded: false },
    { seat: 3, invested: 500, folded: false },
  ];
  const pots = calculatePots(players);
  const potTotal = pots.reduce((s, p) => s + p.amount, 0);

  // Pot 0: seat 0 wins, Pot 1: seat 1 wins, Pot 2: seats 2,3 tie, Pot 3: seat 3
  const winnersPerPot = [[0], [1], [2, 3], [3]];
  const seatOrder = [0, 1, 2, 3];
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T26: award total equals pot total", result.total === potTotal);
  check("T26: pot accounting", verifyPotAccounting(pots, players));
}

// T27: Empty players array
{
  const pots = calculatePots([]);
  check("T27: empty → no pots", pots.length === 0);
}

// T28: Single player (shouldn't happen in real game, but defensively correct)
{
  const players = [{ seat: 0, invested: 100, folded: false }];
  const pots = calculatePots(players);
  check("T28: single player → 1 pot", pots.length === 1);
  check("T28: amount 100", pots[0].amount === 100);
  check("T28: accounting", verifyPotAccounting(pots, players));
}

// T29: Player with zero investment (not dealt in but present in array)
{
  const players = [
    { seat: 0, invested: 0,   folded: false },
    { seat: 1, invested: 100, folded: false },
    { seat: 2, invested: 100, folded: false },
  ];
  const pots = calculatePots(players);
  check("T29: zero-invest player ignored in tiers", pots.length === 1);
  check("T29: amount 200", pots[0].amount === 200);
  // seat 0 invested 0, so they can't be eligible (didn't reach the tier)
  check("T29: seat 0 not eligible", !pots[0].eligible.includes(0));
  check("T29: accounting", verifyPotAccounting(pots, players));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 8: Seat Order Edge Cases for Odd-Chip
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 8: Seat Order Edge Cases ===");

// T30: Non-contiguous seat indices
{
  const pots = [{ amount: 101, eligible: [1, 4] }];
  const winnersPerPot = [[1, 4]];
  const seatOrder = [2, 3, 4, 5, 0, 1]; // button at seat 1, so order starts at seat 2
  // In this order: seat 4 comes before seat 1
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T30: seat 4 gets odd chip (earlier in order)", result.awards[0].distributions[0].seat === 4 && result.awards[0].distributions[0].amount === 51);
  check("T30: seat 1 gets 50", result.awards[0].distributions[1].seat === 1 && result.awards[0].distributions[1].amount === 50);
  check("T30: total 101", result.total === 101);
}

// T31: Odd chip with reversed seat order
{
  const pots = [{ amount: 101, eligible: [0, 1, 2] }];
  const winnersPerPot = [[0, 2]];
  const seatOrder = [2, 0, 1]; // seat 2 is first from button
  const result = awardPots(pots, winnersPerPot, seatOrder);
  check("T31: seat 2 gets odd chip (first in button order)", result.awards[0].distributions[0].seat === 2 && result.awards[0].distributions[0].amount === 51);
  check("T31: seat 0 gets 50", result.awards[0].distributions[1].seat === 0 && result.awards[0].distributions[1].amount === 50);
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n=== Pots Tests: ${passed}/${checks} passed, ${failed} failed ===`);
if (failed > 0) process.exit(1);
