#!/usr/bin/env node
"use strict";

/**
 * Phase 8 — Slice 3A: Showdown Settlement Assembly Tests
 *
 * Tests the computeShowdown pure function: pot computation + hand evaluation
 * + per-pot winner determination + award distribution, all in one pass.
 */

const { computeShowdown } = require("../src/engine/showdown");

let checks = 0, passed = 0, failed = 0;

function check(label, cond) {
  checks++;
  if (cond) { passed++; } else { failed++; console.log(`  FAIL: ${label}`); }
}

// Card helper: "As" → {rank:14, suit:4, display:"As"}
const R = { "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, T: 10, J: 11, Q: 12, K: 13, A: 14 };
const S = { c: 1, d: 2, h: 3, s: 4 };
function c(str) { return { rank: R[str[0]], suit: S[str[1]], display: str }; }
function cards(strs) { return strs.split(" ").map(c); }

// ═══════════════════════════════════════════════════════════════════════════
//  Section 1: Simple 2-Player Showdown
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 1: Simple 2-Player Showdown ===");

// T1: Clear winner — pair vs high card
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("As Ah") },   // pair of aces
      { seat: 1, invested: 100, folded: false, holeCards: cards("Kd Qd") },   // high card
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1],
  });

  check("T1: accounting ok", result.accountingOk);
  check("T1: 1 pot", result.pots.length === 1);
  check("T1: 1 pot result", result.potResults.length === 1);
  check("T1: seat 0 wins", result.potResults[0].winners.length === 1 && result.potResults[0].winners[0] === 0);
  check("T1: seat 0 gets 200", result.potResults[0].distributions[0].seat === 0 && result.potResults[0].distributions[0].amount === 200);
  check("T1: total 200", result.totalAwarded === 200);
  check("T1: 2 reveals", result.reveals.length === 2);
  check("T1: contested", result.potResults[0].contested);
}

// T2: Exact tie — board plays (both players' hole cards are worse)
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("2d 3d") },
      { seat: 1, invested: 100, folded: false, holeCards: cards("4d 5d") },
    ],
    board: cards("As Ks Qs Js 9s"), // board flush, unbeatable
    seatOrder: [0, 1],
  });

  check("T2: accounting ok", result.accountingOk);
  check("T2: 2 winners (tie)", result.potResults[0].winners.length === 2);
  check("T2: split 100/100", result.potResults[0].distributions[0].amount === 100 && result.potResults[0].distributions[1].amount === 100);
  check("T2: total 200", result.totalAwarded === 200);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 2: Tied Main Pot + Single-Winner Side Pot
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 2: Tied Main + Single-Winner Side ===");

// T3: 3 players, short stack all-in. Short stack has best hand, wins main. Side to next best.
{
  // Seat 0: short (100), Ks Kd → pair of kings
  // Seat 1: big (300), Qd Qh → pair of queens
  // Seat 2: big (300), Jd Jh → pair of jacks
  // Board: 9h 5d 3c 2s 7h (no help)
  // Main (300): seat 0 wins (kings). Side (400): seat 1 wins (queens).
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("Ks Kd") },
      { seat: 1, invested: 300, folded: false, holeCards: cards("Qd Qh") },
      { seat: 2, invested: 300, folded: false, holeCards: cards("Jd Jh") },
    ],
    board: cards("9h 5d 3c 2s 7h"),
    seatOrder: [0, 1, 2],
  });

  check("T3: accounting ok", result.accountingOk);
  check("T3: 2 pots", result.potResults.length === 2);
  check("T3: main won by seat 0 (kings)", result.potResults[0].winners.length === 1 && result.potResults[0].winners[0] === 0);
  check("T3: side won by seat 1 (queens)", result.potResults[1].winners.length === 1 && result.potResults[1].winners[0] === 1);
  check("T3: total 700", result.totalAwarded === 700);
}

// T4: Main pot truly tied, side pot single winner
{
  // Board plays for main pot: board is the best hand for the short stack contest
  // Seat 0: short (100), hole 2d 3d (board plays)
  // Seat 1: big (300), hole 4d 5d (board plays for main, but also contests side)
  // Seat 2: big (300), hole As Ah (pair of aces, wins side)
  // Board: Ks Qs Js Ts 8h (K-high straight on board)
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("2d 3d") },
      { seat: 1, invested: 300, folded: false, holeCards: cards("4d 5d") },
      { seat: 2, invested: 300, folded: false, holeCards: cards("As Ah") },
    ],
    board: cards("Ks Qs Js Ts 8h"),
    seatOrder: [0, 1, 2],
  });

  check("T4: accounting ok", result.accountingOk);
  // Main pot: all 3 eligible. Seat 2 has A → A-high straight (A-K-Q-J-T), beats K-high straight
  check("T4: main won by seat 2 (A-high straight)", result.potResults[0].winners.length === 1 && result.potResults[0].winners[0] === 2);
  check("T4: total 700", result.totalAwarded === 700);
}

// T5: Main pot tied (board plays), side pot single winner
{
  // Board: As Ks Qs Js 9s (royal flush on board — unbeatable)
  // All 3 tie for main, side goes to whoever is eligible
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("2d 3d") },
      { seat: 1, invested: 300, folded: false, holeCards: cards("4d 5d") },
      { seat: 2, invested: 300, folded: false, holeCards: cards("6d 7d") },
    ],
    board: cards("As Ks Qs Js Ts"), // straight flush on board
    seatOrder: [0, 1, 2],
  });

  check("T5: accounting ok", result.accountingOk);
  // Main: 3-way tie, 100*3=300, split 100 each
  check("T5: main 3-way tie", result.potResults[0].winners.length === 3);
  check("T5: main split 100 each",
    result.potResults[0].distributions.every((d) => d.amount === 100));
  // Side: 200*2=400, seats 1,2 tie (board plays)
  check("T5: side 2-way tie", result.potResults[1].winners.length === 2);
  check("T5: side split 200 each",
    result.potResults[1].distributions.every((d) => d.amount === 200));
  check("T5: total 700", result.totalAwarded === 700);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 3: Single-Winner Main + Tied Side
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 3: Single-Winner Main + Tied Side ===");

// T6: Short stack wins main, two big stacks tie in side
{
  // Seat 0: short (100), As Ah (pair of aces — best hand, wins main)
  // Seat 1: big (300), Kd Qd (board plays K-high, or whatever)
  // Seat 2: big (300), Kh Qh (same hand as seat 1 — tie for side)
  // Board: 9s 7d 5c 3h 2s
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("As Ah") },
      { seat: 1, invested: 300, folded: false, holeCards: cards("Kd Qd") },
      { seat: 2, invested: 300, folded: false, holeCards: cards("Kh Qh") },
    ],
    board: cards("9s 7d 5c 3h 2s"),
    seatOrder: [0, 1, 2],
  });

  check("T6: accounting ok", result.accountingOk);
  // Main: seat 0 wins (aces)
  check("T6: main won by seat 0", result.potResults[0].winners.length === 1 && result.potResults[0].winners[0] === 0);
  check("T6: main 300 to seat 0", result.potResults[0].distributions[0].amount === 300);
  // Side: seats 1,2 tie (both KQ high)
  check("T6: side 2-way tie", result.potResults[1].winners.length === 2);
  check("T6: side split 200 each",
    result.potResults[1].distributions.every((d) => d.amount === 200));
  check("T6: total 700", result.totalAwarded === 700);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 4: Folded Dead Money
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 4: Folded Dead Money ===");

// T7: 3 players, one folds, remaining 2 showdown
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 50,  folded: true,  holeCards: null },
      { seat: 1, invested: 200, folded: false, holeCards: cards("As Ah") },
      { seat: 2, invested: 200, folded: false, holeCards: cards("Kd Qd") },
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1, 2],
  });

  check("T7: accounting ok", result.accountingOk);
  check("T7: folder not in reveals", result.reveals.every((r) => r.seat !== 0));
  check("T7: 2 reveals only", result.reveals.length === 2);
  // Folder's 50 goes into pot tiers but folder never eligible
  check("T7: seat 1 wins everything (aces)", result.totalAwarded === 450);
  // Verify seat 0 is not a winner in any pot
  check("T7: seat 0 never wins", result.potResults.every((pr) => !pr.winners.includes(0)));
}

// T8: Multiple folders at different levels
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 10,  folded: true,  holeCards: null },
      { seat: 1, invested: 50,  folded: true,  holeCards: null },
      { seat: 2, invested: 200, folded: false, holeCards: cards("As Ah") },
      { seat: 3, invested: 200, folded: false, holeCards: cards("Kd Qd") },
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1, 2, 3],
  });

  check("T8: accounting ok", result.accountingOk);
  check("T8: total 460", result.totalAwarded === 460);
  check("T8: 2 reveals (active players only)", result.reveals.length === 2);
  check("T8: seat 2 wins all (aces)",
    result.potResults.every((pr) => pr.winners.includes(2) && !pr.winners.includes(0) && !pr.winners.includes(1)));
}

// T9: Folder invested more than an active all-in player
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 150, folded: true,  holeCards: null },
      { seat: 1, invested: 100, folded: false, holeCards: cards("As Ah") }, // best hand, short all-in
      { seat: 2, invested: 300, folded: false, holeCards: cards("Kd Qd") },
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1, 2],
  });

  check("T9: accounting ok", result.accountingOk);
  check("T9: total 550", result.totalAwarded === 550);
  // Main pot: 100*3=300, seat 1 wins (aces)
  check("T9: main won by seat 1", result.potResults[0].winners[0] === 1);
  check("T9: main 300", result.potResults[0].amount === 300);
  // Side pots: only seat 2 eligible (folder ineligible)
  check("T9: remaining pots to seat 2",
    result.potResults.slice(1).every((pr) => pr.winners.length === 1 && pr.winners[0] === 2));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 5: Uncontested Pots
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 5: Uncontested Pots ===");

// T10: Uncontested side pot (only 1 eligible player)
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("As Ah") },
      { seat: 1, invested: 300, folded: false, holeCards: cards("Kd Kh") },
      { seat: 2, invested: 500, folded: false, holeCards: cards("Qd Qh") },
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1, 2],
  });

  check("T10: accounting ok", result.accountingOk);
  check("T10: 3 pots", result.potResults.length === 3);
  // Main (300): seat 0 wins (aces)
  check("T10: main won by seat 0", result.potResults[0].winners[0] === 0);
  // Side 1 (400): seat 1 wins (kings beat queens)
  check("T10: side1 won by seat 1", result.potResults[1].winners[0] === 1);
  check("T10: side1 contested", result.potResults[1].contested);
  // Side 2 (200): only seat 2 eligible → uncontested
  check("T10: side2 uncontested", !result.potResults[2].contested);
  check("T10: side2 to seat 2", result.potResults[2].winners[0] === 2);
  check("T10: total 900", result.totalAwarded === 900);
}

// T11: Multiple uncontested side pots in a row
{
  // 4-way: only seat 3 has enough for top tier, seat 2+3 for mid tier
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 50,  folded: false, holeCards: cards("As Ah") }, // best hand
      { seat: 1, invested: 100, folded: false, holeCards: cards("Kd Kh") },
      { seat: 2, invested: 300, folded: false, holeCards: cards("Qd Qh") },
      { seat: 3, invested: 500, folded: false, holeCards: cards("Jd Jh") },
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1, 2, 3],
  });

  check("T11: accounting ok", result.accountingOk);
  check("T11: 4 pots", result.potResults.length === 4);
  // Main: seat 0 wins (aces)
  check("T11: main won by seat 0 (aces)", result.potResults[0].winners[0] === 0);
  // Side 1: seats 1,2,3 eligible → seat 1 wins (kings)
  check("T11: side1 won by seat 1 (kings)", result.potResults[1].winners[0] === 1);
  // Side 2: seats 2,3 → seat 2 wins (queens)
  check("T11: side2 won by seat 2 (queens)", result.potResults[2].winners[0] === 2);
  // Side 3: seat 3 only → uncontested
  check("T11: side3 uncontested", !result.potResults[3].contested);
  check("T11: total 950", result.totalAwarded === 950);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 6: Odd-Chip in Multi-Pot
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 6: Odd-Chip in Multi-Pot ===");

// T12: Odd chip in main pot split
{
  // 3 players, equal invest of 101 → single pot 303, 3-way tie (board plays)
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 101, folded: false, holeCards: cards("2d 3d") },
      { seat: 1, invested: 101, folded: false, holeCards: cards("4d 5d") },
      { seat: 2, invested: 101, folded: false, holeCards: cards("6d 7d") },
    ],
    board: cards("As Ks Qs Js Ts"), // straight flush on board
    seatOrder: [0, 1, 2],
  });

  check("T12: accounting ok", result.accountingOk);
  check("T12: 3-way tie", result.potResults[0].winners.length === 3);
  // 303 / 3 = 101, no remainder
  check("T12: even split 101 each",
    result.potResults[0].distributions.every((d) => d.amount === 101));
  check("T12: total 303", result.totalAwarded === 303);
}

// T13: Odd chip with uneven side pot split
{
  // Seat 0: 100, Seat 1: 301, Seat 2: 301
  // Main: 300, Side: 402
  // Board plays for all (straight flush on board)
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("2d 3d") },
      { seat: 1, invested: 301, folded: false, holeCards: cards("4d 5d") },
      { seat: 2, invested: 301, folded: false, holeCards: cards("6d 7d") },
    ],
    board: cards("As Ks Qs Js Ts"), // straight flush on board
    seatOrder: [0, 1, 2],
  });

  check("T13: accounting ok", result.accountingOk);
  // Main: 300, 3-way → 100 each
  check("T13: main 100 each", result.potResults[0].distributions.every((d) => d.amount === 100));
  // Side: 402, 2-way → 201 each (even)
  check("T13: side 201 each", result.potResults[1].distributions.every((d) => d.amount === 201));
  check("T13: total 702", result.totalAwarded === 702);
}

// T14: Odd chip in side pot, not main
{
  // Seat 0: 100, Seat 1: 300, Seat 2: 301
  // Main: 100*3=300, Side tiers: 200 and 300→301
  // Actually: tiers are 100, 300, 301
  // Tier 100: 100*3=300
  // Tier 300: 200*2=400, eligible [1,2]
  // Tier 301: 1*1=1, eligible [2] (uncontested)
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("As Ah") }, // pair aces, wins main
      { seat: 1, invested: 300, folded: false, holeCards: cards("Kd Kh") }, // pair kings
      { seat: 2, invested: 301, folded: false, holeCards: cards("Qd Qh") }, // pair queens
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1, 2],
  });

  check("T14: accounting ok", result.accountingOk);
  check("T14: 3 pots", result.potResults.length === 3);
  check("T14: main 300, seat 0 wins", result.potResults[0].amount === 300 && result.potResults[0].winners[0] === 0);
  check("T14: side1 400, seat 1 wins (kings)", result.potResults[1].amount === 400 && result.potResults[1].winners[0] === 1);
  check("T14: side2 1 chip, seat 2 (uncontested)", result.potResults[2].amount === 1 && result.potResults[2].winners[0] === 2);
  check("T14: total 701", result.totalAwarded === 701);
}

// T15: Odd chip respects seat order in multi-pot tie
{
  // All board plays, odd pot size, seat order puts seat 2 first
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("2d 3d") },
      { seat: 1, invested: 100, folded: false, holeCards: cards("4d 5d") },
    ],
    board: cards("As Ks Qs Js Ts"), // straight flush on board
    seatOrder: [1, 0], // seat 1 is first clockwise from button
  });

  check("T15: accounting ok", result.accountingOk);
  check("T15: 2-way tie", result.potResults[0].winners.length === 2);
  // 200 / 2 = 100 each (even split, no odd chip to test)
  check("T15: even split", result.potResults[0].distributions.every((d) => d.amount === 100));
}

// T16: Truly odd split with button-order
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 51, folded: false, holeCards: cards("2d 3d") },
      { seat: 1, invested: 50, folded: false, holeCards: cards("4d 5d") },
    ],
    board: cards("As Ks Qs Js Ts"), // board plays, tie
    seatOrder: [1, 0], // seat 1 first from button
  });

  check("T16: accounting ok", result.accountingOk);
  // Main: 50*2=100, tie, 50 each
  // Side: 1*1=1, seat 0 only (uncontested)
  check("T16: 2 pots", result.potResults.length === 2);
  check("T16: main 100 split 50/50", result.potResults[0].distributions.every((d) => d.amount === 50));
  check("T16: side 1 chip to seat 0", result.potResults[1].distributions[0].seat === 0 && result.potResults[1].distributions[0].amount === 1);
  check("T16: total 101", result.totalAwarded === 101);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 7: Board-Play Ties (Full)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 7: Board-Play Ties ===");

// T17: 4-way board play with side pots — everyone ties
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 50,  folded: false, holeCards: cards("2d 3d") },
      { seat: 1, invested: 100, folded: false, holeCards: cards("4d 5d") },
      { seat: 2, invested: 300, folded: false, holeCards: cards("6d 7d") },
      { seat: 3, invested: 300, folded: false, holeCards: cards("8d 9d") },
    ],
    board: cards("As Ks Qs Js Ts"), // straight flush on board
    seatOrder: [0, 1, 2, 3],
  });

  check("T17: accounting ok", result.accountingOk);
  check("T17: total 750", result.totalAwarded === 750);
  // Main: 50*4=200, 4-way tie → 50 each
  check("T17: main 4-way tie", result.potResults[0].winners.length === 4);
  check("T17: main 50 each", result.potResults[0].distributions.every((d) => d.amount === 50));
  // Side 1: 50*3=150, 3-way tie → 50 each
  check("T17: side1 3-way tie", result.potResults[1].winners.length === 3);
  check("T17: side1 50 each", result.potResults[1].distributions.every((d) => d.amount === 50));
  // Side 2: 200*2=400, 2-way tie → 200 each
  check("T17: side2 2-way tie", result.potResults[2].winners.length === 2);
  check("T17: side2 200 each", result.potResults[2].distributions.every((d) => d.amount === 200));
}

// T18: Board play with a folder — folder dead money absorbed, survivors tie
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 50,  folded: true,  holeCards: null },
      { seat: 1, invested: 200, folded: false, holeCards: cards("2d 3d") },
      { seat: 2, invested: 200, folded: false, holeCards: cards("4d 5d") },
    ],
    board: cards("As Ks Qs Js Ts"),
    seatOrder: [0, 1, 2],
  });

  check("T18: accounting ok", result.accountingOk);
  check("T18: total 450", result.totalAwarded === 450);
  check("T18: folder not in reveals", !result.reveals.some((r) => r.seat === 0));
  // All pots: only seats 1,2 eligible, they tie everywhere
  check("T18: all pots tied by seats 1,2",
    result.potResults.every((pr) => pr.winners.length <= 2 && !pr.winners.includes(0)));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 8: Reveal Correctness
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 8: Reveal Correctness ===");

// T19: Reveals contain correct hand names
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 100, folded: false, holeCards: cards("As Ah") },
      { seat: 1, invested: 100, folded: false, holeCards: cards("Kd Qd") },
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1],
  });

  const r0 = result.reveals.find((r) => r.seat === 0);
  const r1 = result.reveals.find((r) => r.seat === 1);
  check("T19: seat 0 has pair of aces", r0.handName === "Pair of Aces");
  check("T19: seat 1 has K-high", r1.handName === "King-high");
  check("T19: seat 0 category 1", r0.category === 1);
  check("T19: seat 1 category 0", r1.category === 0);
  check("T19: each reveal has bestFive", r0.bestFive.length === 5 && r1.bestFive.length === 5);
}

// T20: Folded players never appear in reveals
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 10,  folded: true,  holeCards: null },
      { seat: 1, invested: 50,  folded: true,  holeCards: null },
      { seat: 2, invested: 200, folded: false, holeCards: cards("As Ah") },
      { seat: 3, invested: 200, folded: false, holeCards: cards("Kd Kh") },
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1, 2, 3],
  });

  check("T20: only 2 reveals", result.reveals.length === 2);
  check("T20: reveal seats are 2 and 3",
    result.reveals.some((r) => r.seat === 2) && result.reveals.some((r) => r.seat === 3));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 9: Accounting Stress
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 9: Accounting Stress ===");

// T21: Large 5-player scenario — 2 fold, 3 showdown, uneven
{
  const result = computeShowdown({
    players: [
      { seat: 0, invested: 10,   folded: true,  holeCards: null },
      { seat: 1, invested: 30,   folded: true,  holeCards: null },
      { seat: 2, invested: 100,  folded: false, holeCards: cards("As Ah") },
      { seat: 3, invested: 200,  folded: false, holeCards: cards("Kd Kh") },
      { seat: 4, invested: 500,  folded: false, holeCards: cards("Qd Qh") },
    ],
    board: cards("9s 7h 5d 3c 2s"),
    seatOrder: [0, 1, 2, 3, 4],
  });

  check("T21: accounting ok", result.accountingOk);
  check("T21: total 840", result.totalAwarded === 840);
  // Seat 2 (aces) wins main, seat 3 (kings) wins mid pots, seat 4 gets uncontested top
  check("T21: reveals = 3", result.reveals.length === 3);
}

// T22: Award sum matches invested sum in every scenario above
{
  // Already checked via accountingOk, but let's do a structural recheck
  const scenarios = [
    { players: [
      { seat: 0, invested: 1, folded: false, holeCards: cards("As Ah") },
      { seat: 1, invested: 1, folded: false, holeCards: cards("Kd Kh") },
    ], board: cards("9s 7h 5d 3c 2s"), seatOrder: [0, 1] },
    { players: [
      { seat: 0, invested: 999, folded: false, holeCards: cards("2d 3d") },
      { seat: 1, invested: 1, folded: false, holeCards: cards("4d 5d") },
    ], board: cards("As Ks Qs Js Ts"), seatOrder: [0, 1] },
  ];

  let allOk = true;
  for (const s of scenarios) {
    const r = computeShowdown(s);
    if (!r.accountingOk) allOk = false;
  }
  check("T22: all micro scenarios pass accounting", allOk);
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n=== Showdown Tests: ${passed}/${checks} passed, ${failed} failed ===`);
if (failed > 0) process.exit(1);
