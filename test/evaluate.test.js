#!/usr/bin/env node
"use strict";

/**
 * Phase 8 — Slice 1: Hand Evaluator Tests
 *
 * Exhaustive test pack for the poker hand evaluator.
 * Pure function tests — no engine coupling.
 */

const { evaluateHand, compareHands, findWinners, classify5 } = require("../src/engine/evaluate");

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

// Helper: make a card from shorthand "As" = Ace of spades
const RANK_MAP = { "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, T: 10, J: 11, Q: 12, K: 13, A: 14 };
const SUIT_MAP = { c: 1, d: 2, h: 3, s: 4 };

function c(str) {
  return { rank: RANK_MAP[str[0]], suit: SUIT_MAP[str[1]], display: str };
}

function cards(strs) {
  return strs.split(" ").map(c);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 1: Hand Classification (5-card)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 1: 5-Card Classification ===");

// T1: High card
{
  const h = classify5(cards("As Ks Qd Jh 9c"));
  check("T1: high card category", h.category === 0);
  check("T1: high card ranks", JSON.stringify(h.ranks) === JSON.stringify([14, 13, 12, 11, 9]));
}

// T2: One pair
{
  const h = classify5(cards("As Ah Kd Qc 9s"));
  check("T2: one pair category", h.category === 1);
  check("T2: one pair ranks [pair, k1, k2, k3]", JSON.stringify(h.ranks) === JSON.stringify([14, 13, 12, 9]));
}

// T3: Two pair
{
  const h = classify5(cards("As Ah Kd Kc 9s"));
  check("T3: two pair category", h.category === 2);
  check("T3: two pair ranks [hi, lo, kicker]", JSON.stringify(h.ranks) === JSON.stringify([14, 13, 9]));
}

// T4: Three of a kind
{
  const h = classify5(cards("As Ah Ad Kc 9s"));
  check("T4: trips category", h.category === 3);
  check("T4: trips ranks [trips, k1, k2]", JSON.stringify(h.ranks) === JSON.stringify([14, 13, 9]));
}

// T5: Straight (normal)
{
  const h = classify5(cards("Ts 9h 8d 7c 6s"));
  check("T5: straight category", h.category === 4);
  check("T5: straight ranks [high]", JSON.stringify(h.ranks) === JSON.stringify([10]));
}

// T6: Ace-low straight (wheel)
{
  const h = classify5(cards("Ah 2s 3d 4c 5h"));
  check("T6: wheel category", h.category === 4);
  check("T6: wheel ranks [5] (five-high)", JSON.stringify(h.ranks) === JSON.stringify([5]));
}

// T7: Ace-high straight (broadway)
{
  const h = classify5(cards("As Kh Qd Jc Ts"));
  check("T7: broadway category", h.category === 4);
  check("T7: broadway ranks [14]", JSON.stringify(h.ranks) === JSON.stringify([14]));
}

// T8: Flush
{
  const h = classify5(cards("As Ks Qs 9s 7s"));
  check("T8: flush category", h.category === 5);
  check("T8: flush ranks", JSON.stringify(h.ranks) === JSON.stringify([14, 13, 12, 9, 7]));
}

// T9: Full house
{
  const h = classify5(cards("As Ah Ad Kc Ks"));
  check("T9: full house category", h.category === 6);
  check("T9: full house ranks [trips, pair]", JSON.stringify(h.ranks) === JSON.stringify([14, 13]));
}

// T10: Four of a kind
{
  const h = classify5(cards("As Ah Ad Ac Ks"));
  check("T10: quads category", h.category === 7);
  check("T10: quads ranks [quad, kicker]", JSON.stringify(h.ranks) === JSON.stringify([14, 13]));
}

// T11: Straight flush
{
  const h = classify5(cards("9s 8s 7s 6s 5s"));
  check("T11: straight flush category", h.category === 8);
  check("T11: straight flush ranks [9]", JSON.stringify(h.ranks) === JSON.stringify([9]));
}

// T12: Royal flush
{
  const h = classify5(cards("As Ks Qs Js Ts"));
  check("T12: royal flush category", h.category === 8);
  check("T12: royal flush ranks [14]", JSON.stringify(h.ranks) === JSON.stringify([14]));
}

// T13: Ace-low straight flush
{
  const h = classify5(cards("Ah 2h 3h 4h 5h"));
  check("T13: A-low straight flush category", h.category === 8);
  check("T13: A-low straight flush ranks [5]", JSON.stringify(h.ranks) === JSON.stringify([5]));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 2: 7-Card Evaluation (best-of-21)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 2: 7-Card Evaluation ===");

// T14: Best 5 from 7 — pair in hole, board has nothing
{
  const h = evaluateHand(cards("As Ah 9d 7c 5s 3h 2d"));
  check("T14: pair found in 7 cards", h.category === 1);
  check("T14: pair of aces", h.ranks[0] === 14);
}

// T15: Flush hidden in 7 cards (5 spades among 7)
{
  const h = evaluateHand(cards("As Ks 3s 7s 9s Jd 4h"));
  check("T15: flush from 7", h.category === 5);
  check("T15: A-high flush", h.ranks[0] === 14);
}

// T16: Full house from trips on board + pair in hole
{
  const h = evaluateHand(cards("9s 9h Kd Kc Ks 3h 2d"));
  check("T16: full house from 7", h.category === 6);
  check("T16: kings over nines", h.ranks[0] === 13 && h.ranks[1] === 9);
}

// T17: Straight from 7 cards where best straight uses non-obvious selection
{
  // Board: T 9 8 3 2. Hole: J 7. Best: J-T-9-8-7 straight
  const h = evaluateHand(cards("Jh 7c Ts 9d 8h 3c 2d"));
  check("T17: straight J-high from 7", h.category === 4);
  check("T17: J-high straight", h.ranks[0] === 11);
}

// T18: Two pair on board + higher pair in hole = best two pair
{
  // Hole: As Ah, Board: Kd Kc 5s 3h 2d → two pair A+K
  const h = evaluateHand(cards("As Ah Kd Kc 5s 3h 2d"));
  check("T18: two pair from 7", h.category === 2);
  check("T18: aces and kings", h.ranks[0] === 14 && h.ranks[1] === 13);
  check("T18: kicker is 5", h.ranks[2] === 5);
}

// T19: Four of a kind from board+hole
{
  const h = evaluateHand(cards("As Ah Ad Ac Ks 9h 2d"));
  check("T19: quads from 7", h.category === 7);
  check("T19: quad aces, K kicker", h.ranks[0] === 14 && h.ranks[1] === 13);
}

// T20: Straight flush from 7 cards (non-obvious selection)
{
  // 7 cards include 5h 6h 7h 8h 9h plus two red herrings
  const h = evaluateHand(cards("5h 6h 7h 8h 9h Kd Qs"));
  check("T20: straight flush from 7", h.category === 8);
  check("T20: 9-high SF", h.ranks[0] === 9);
}

// T21: Three-of-a-kind — best selection requires dropping higher kicker for trips
{
  // Hole: 9s 9h, Board: 9d Ah Kc Qd 2s → trips with A K kickers
  const h = evaluateHand(cards("9s 9h 9d Ah Kc Qd 2s"));
  check("T21: trips from 7", h.category === 3);
  check("T21: trip nines, A-K kickers", h.ranks[0] === 9 && h.ranks[1] === 14 && h.ranks[2] === 13);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 3: Hand Comparison
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 3: Hand Comparison ===");

// T22: Higher category wins
{
  const flush = evaluateHand(cards("As Ks Qs 9s 7s 3d 2h"));
  const straight = evaluateHand(cards("Ts 9h 8d 7c 6s 3d 2h"));
  check("T22: flush beats straight", compareHands(flush, straight) === 1);
}

// T23: Same category, higher primary rank wins
{
  const pairA = evaluateHand(cards("As Ah 9d 7c 5s 3h 2d"));
  const pairK = evaluateHand(cards("Ks Kh 9d 7c 5s 3h 2d"));
  check("T23: pair aces beats pair kings", compareHands(pairA, pairK) === 1);
}

// T24: Same pair, different kicker
{
  const pairAK = evaluateHand(cards("As Ah Kd 7c 5s 3h 2d"));
  const pairAQ = evaluateHand(cards("As Ah Qd 7c 5s 3h 2d"));
  check("T24: AA with K kicker beats AA with Q kicker", compareHands(pairAK, pairAQ) === 1);
}

// T25: Same pair, same first kicker, second kicker differs
{
  const h1 = evaluateHand(cards("As Ah Kd Qc 5s 3h 2d"));
  const h2 = evaluateHand(cards("As Ah Kd Jc 5s 3h 2d"));
  check("T25: AA-K-Q beats AA-K-J", compareHands(h1, h2) === 1);
}

// T26: Two pair comparison — high pair same, low pair differs
{
  const h1 = evaluateHand(cards("As Ah Ks Kh 3d 7c 2s"));
  const h2 = evaluateHand(cards("As Ah Qs Qh 3d 7c 2s"));
  check("T26: AA-KK beats AA-QQ", compareHands(h1, h2) === 1);
}

// T27: Two pair same, kicker differs
{
  const h1 = evaluateHand(cards("As Ah Ks Kh 9d 3c 2s"));
  const h2 = evaluateHand(cards("As Ah Ks Kh 8d 3c 2s"));
  check("T27: AA-KK-9 beats AA-KK-8", compareHands(h1, h2) === 1);
}

// T28: Flush kicker comparison
{
  const h1 = evaluateHand(cards("As Ks Qs 9s 7s 3d 2h"));
  const h2 = evaluateHand(cards("As Ks Qs 9s 6s 3d 2h"));
  check("T28: flush A-K-Q-9-7 beats A-K-Q-9-6", compareHands(h1, h2) === 1);
}

// T29: Full house comparison — trips differ
{
  const h1 = evaluateHand(cards("As Ah Ad Ks Kh 3d 2c"));
  const h2 = evaluateHand(cards("Ks Kh Kd As Ah 3d 2c"));
  check("T29: AAA-KK beats KKK-AA", compareHands(h1, h2) === 1);
}

// T30: Full house — same trips, different pair
{
  const h1 = evaluateHand(cards("As Ah Ad Ks Kh 3d 2c"));
  const h2 = evaluateHand(cards("As Ah Ad Qs Qh 3d 2c"));
  check("T30: AAA-KK beats AAA-QQ", compareHands(h1, h2) === 1);
}

// T31: Straight comparison
{
  const h1 = evaluateHand(cards("Ts 9h 8d 7c 6s 3d 2h"));
  const h2 = evaluateHand(cards("9s 8h 7d 6c 5s 3d 2h"));
  check("T31: T-high straight beats 9-high", compareHands(h1, h2) === 1);
}

// T32: Ace-low straight loses to 6-high straight
{
  const h1 = evaluateHand(cards("6s 5h 4d 3c 2s Kd Jh"));
  const h2 = evaluateHand(cards("Ah 2s 3d 4c 5h Kd Jd"));
  check("T32: 6-high straight beats wheel", compareHands(h1, h2) === 1);
}

// T33: Straight flush beats four of a kind
{
  const sf = evaluateHand(cards("5h 6h 7h 8h 9h Kd Qs"));
  const quads = evaluateHand(cards("As Ah Ad Ac Ks 9h 2d"));
  check("T33: straight flush beats quads", compareHands(sf, quads) === 1);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 4: Exact Ties
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 4: Exact Ties ===");

// T34: Identical high card (different suits, same ranks)
{
  const h1 = evaluateHand(cards("As Kh Qd Jc 9s 3h 2d"));
  const h2 = evaluateHand(cards("Ah Kd Qs Jd 9h 3c 2s"));
  check("T34: same high-card hands tie", compareHands(h1, h2) === 0);
}

// T35: Board plays — both players have worse hole cards
{
  // Board: As Ks Qs Js 9s (flush on board)
  // Player 1 hole: 2d 3d, Player 2 hole: 4d 5d
  // Both play the board flush
  const h1 = evaluateHand(cards("2d 3d As Ks Qs Js 9s"));
  const h2 = evaluateHand(cards("4d 5d As Ks Qs Js 9s"));
  check("T35: board plays — exact tie", compareHands(h1, h2) === 0);
}

// T36: Same pair, same kickers — tie
{
  const h1 = evaluateHand(cards("As Ac Kd Qh 9s 3c 2d"));
  const h2 = evaluateHand(cards("Ad Ah Ks Qc 9h 3d 2s"));
  check("T36: identical pair hands tie", compareHands(h1, h2) === 0);
}

// T37: Same two pair, same kicker — tie
{
  const h1 = evaluateHand(cards("As Ac Kd Kh 9s 3c 2d"));
  const h2 = evaluateHand(cards("Ad Ah Ks Kc 9h 3d 2s"));
  check("T37: identical two pair tie", compareHands(h1, h2) === 0);
}

// T38: Same straight — tie
{
  const h1 = evaluateHand(cards("Ts 9h 8d 7c 6s 3d 2h"));
  const h2 = evaluateHand(cards("Th 9d 8s 7d 6h 3c 2s"));
  check("T38: same straight tie", compareHands(h1, h2) === 0);
}

// T39: Same full house — tie
{
  const h1 = evaluateHand(cards("As Ah Ad Ks Kh 3d 2c"));
  const h2 = evaluateHand(cards("Ac Ad Ah Kd Kc 3s 2h"));
  check("T39: same full house tie", compareHands(h1, h2) === 0);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 5: findWinners
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 5: findWinners ===");

// T40: Clear single winner
{
  const hands = [
    evaluateHand(cards("As Ah Kd 7c 5s 3h 2d")),  // pair aces
    evaluateHand(cards("Ks Kh 9d 7c 5s 3h 2d")),  // pair kings
    evaluateHand(cards("Qs Qh 9d 7c 5s 3h 2d")),  // pair queens
  ];
  const w = findWinners(hands);
  check("T40: single winner (pair aces)", w.length === 1 && w[0] === 0);
}

// T41: Two-way tie
{
  const hands = [
    evaluateHand(cards("As Kh Qd Jc 9s 3d 2h")),  // A-high
    evaluateHand(cards("Ah Kd Qs Jd 9h 3c 2s")),  // A-high (same)
    evaluateHand(cards("Ks Qh Jd Tc 8s 3d 2h")),  // K-high
  ];
  const w = findWinners(hands);
  check("T41: two-way tie", w.length === 2 && w.includes(0) && w.includes(1));
}

// T42: Three-way tie (board plays)
{
  const board = cards("As Ks Qs Js Ts");
  const hands = [
    evaluateHand([...cards("2d 3d"), ...board]),
    evaluateHand([...cards("4d 5d"), ...board]),
    evaluateHand([...cards("6d 7d"), ...board]),
  ];
  const w = findWinners(hands);
  check("T42: three-way board plays tie", w.length === 3);
}

// T43: One player with flush on board but another has higher spade
{
  // Board: Ks Qs Js 9s 2s (spade flush on board)
  // Player 1: As 3d (has A of spades → A-high flush)
  // Player 2: Td 3h (plays board flush K-high)
  const board = cards("Ks Qs Js 9s 2s");
  const h1 = evaluateHand([...cards("As 3d"), ...board]);
  const h2 = evaluateHand([...cards("Td 3h"), ...board]);
  check("T43: A-spade improves board flush", compareHands(h1, h2) === 1);
  check("T43: player 1 has A-high flush", h1.category === 5 && h1.ranks[0] === 14);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 6: Edge Cases and Tricky Selections
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 6: Edge Cases ===");

// T44: 6 cards to a flush — best 5 selected
{
  // 6 spades in 7 cards. Best flush is top 5 spades.
  const h = evaluateHand(cards("As Ks Qs 9s 7s 4s 2d"));
  check("T44: 6-card flush selects best 5", h.category === 5);
  check("T44: ranks are top 5", JSON.stringify(h.ranks) === JSON.stringify([14, 13, 12, 9, 7]));
}

// T45: 7 cards to a flush
{
  const h = evaluateHand(cards("As Ks Qs Js 9s 7s 4s"));
  check("T45: 7-card flush selects best 5", h.category === 5);
  check("T45: ranks are A-K-Q-J-9", JSON.stringify(h.ranks) === JSON.stringify([14, 13, 12, 11, 9]));
}

// T46: Flush vs. straight — both possible, flush is better
{
  const h = evaluateHand(cards("9s 8s 7s 6s 4s Td 5h"));
  // 9s 8s 7s 6s 4s = flush (9-high)
  // T 9 8 7 6 = straight (T-high)
  // But 9-8-7-6-5 could be straight if 5 were spade... but it's not
  // Actually: T 9 8 7 6 is a straight. And 9s 8s 7s 6s 4s is a flush.
  // Flush (cat 5) > straight (cat 4). Evaluator should pick flush.
  check("T46: flush chosen over straight when both possible", h.category === 5);
}

// T47: Straight flush vs. higher non-flush straight
{
  // Cards include 5h 6h 7h 8h 9h (SF, 9-high) AND Th which would make T-high straight
  // But T is not part of a flush combo... let's be precise:
  // 5h 6h 7h 8h 9h Td Js → SF 9-high (cat 8) beats J-high straight (cat 4)
  const h = evaluateHand(cards("5h 6h 7h 8h 9h Td Js"));
  check("T47: straight flush beats higher straight", h.category === 8);
  check("T47: 9-high SF", h.ranks[0] === 9);
}

// T48: Full house with two trips available (pick higher trips)
{
  // 7 cards: Ks Kh Kd 9s 9h 9d 2c
  // Two possible FH: KKK-99 or 999-KK. KKK-99 is better.
  const h = evaluateHand(cards("Ks Kh Kd 9s 9h 9d 2c"));
  check("T48: picks higher trips for FH", h.category === 6);
  check("T48: kings over nines", h.ranks[0] === 13 && h.ranks[1] === 9);
}

// T49: Two pair — three pairs in 7 cards, pick best two
{
  // Hole: As Ah, Board: Ks Kh 9s 9h 2d
  // Three pairs: A, K, 9. Best two pair: A-K with 9 kicker
  const h = evaluateHand(cards("As Ah Ks Kh 9s 9h 2d"));
  check("T49: three pairs picks best two", h.category === 2);
  check("T49: aces and kings", h.ranks[0] === 14 && h.ranks[1] === 13);
  check("T49: kicker is 9", h.ranks[2] === 9);
}

// T50: Quads with two possible kickers — pick highest
{
  const h = evaluateHand(cards("As Ah Ad Ac Ks Qd 2c"));
  check("T50: quads best kicker", h.category === 7);
  check("T50: K kicker (not Q or 2)", h.ranks[1] === 13);
}

// T51: High card — 7 cards, correct top 5
{
  const h = evaluateHand(cards("As Kd Qh Jc 9s 7h 4d"));
  check("T51: high card best 5 from 7", h.category === 0);
  check("T51: A-K-Q-J-9", JSON.stringify(h.ranks) === JSON.stringify([14, 13, 12, 11, 9]));
}

// T52: Ace-low straight in 7 cards where higher straight is NOT available
{
  const h = evaluateHand(cards("Ah 2d 3c 4s 5h Kd Qc"));
  check("T52: wheel from 7 cards", h.category === 4);
  check("T52: five-high", h.ranks[0] === 5);
}

// T53: 7 cards where ace-high straight AND ace-low straight are both possible
{
  // A K Q J T 3 2 → A-high straight wins
  const h = evaluateHand(cards("As Kd Qh Jc Ts 3h 2d"));
  check("T53: A-high straight beats wheel", h.category === 4);
  check("T53: A-high", h.ranks[0] === 14);
}

// T54: Both straight and flush possible but straight flush NOT (different suits)
{
  // Flush: As Ks Qs 9s 7s. Straight: T 9 8 7 6. No overlap for SF.
  const h = evaluateHand(cards("As Ks Qs 9s 7s Td 8h"));
  check("T54: flush beats straight when no SF", h.category === 5);
}

// T55: One pair vs. two pair — category comparison
{
  const h1 = evaluateHand(cards("As Ah Kd Qh 9s 3c 2d"));
  const h2 = evaluateHand(cards("3s 3h 2d 2c 9s Kd Qh"));
  check("T55: AA one pair loses to 33-22 two pair? No — AA is one pair (1), 33-22 is two pair (2)", compareHands(h1, h2) === -1);
}

// T56: Four of a kind kicker tie-break
{
  const h1 = evaluateHand(cards("9s 9h 9d 9c As 3d 2h"));
  const h2 = evaluateHand(cards("9s 9h 9d 9c Ks 3d 2h"));
  check("T56: same quads, A kicker beats K kicker", compareHands(h1, h2) === 1);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 7: Hand Names
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 7: Hand Names ===");

// T57: Verify hand name strings
{
  check("T57a: royal flush name", evaluateHand(cards("As Ks Qs Js Ts 3d 2h")).handName === "Royal Flush");
  check("T57b: straight flush name", evaluateHand(cards("9s 8s 7s 6s 5s 3d 2h")).handName === "Straight Flush, Nine-high");
  check("T57c: quads name", evaluateHand(cards("As Ah Ad Ac Ks 3d 2h")).handName === "Four of a Kind, Aces");
  check("T57d: full house name", evaluateHand(cards("As Ah Ad Ks Kh 3d 2c")).handName === "Full House, Aces over Kings");
  check("T57e: flush name", evaluateHand(cards("As Ks Qs 9s 7s 3d 2h")).handName === "Flush, Ace-high");
  check("T57f: straight name", evaluateHand(cards("Ts 9h 8d 7c 6s 3d 2h")).handName === "Straight, Ten-high");
  check("T57g: trips name", evaluateHand(cards("As Ah Ad Kc 9s 3h 2d")).handName === "Three of a Kind, Aces");
  check("T57h: two pair name", evaluateHand(cards("As Ah Ks Kh 9d 3c 2d")).handName === "Two Pair, Aces and Kings");
  check("T57i: pair name", evaluateHand(cards("As Ah Kd Qc 9s 3h 2d")).handName === "Pair of Aces");
  check("T57j: high card name", evaluateHand(cards("As Kd Qh Jc 9s 3h 2d")).handName === "Ace-high");
  check("T57k: wheel name", evaluateHand(cards("Ah 2s 3d 4c 5h Kd Qc")).handName === "Straight, Five-high");
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 8: Ordering Consistency (transitivity)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 8: Ordering Consistency ===");

// T58: Rank every category against every lower category
{
  const hands = [
    evaluateHand(cards("As Kd Qh Jc 9s 3h 2d")),  // 0: high card
    evaluateHand(cards("As Ah Kd Qc 9s 3h 2d")),   // 1: pair
    evaluateHand(cards("As Ah Ks Kh 9d 3c 2d")),   // 2: two pair
    evaluateHand(cards("As Ah Ad Kc 9s 3h 2d")),    // 3: trips
    evaluateHand(cards("Ts 9h 8d 7c 6s 3d 2h")),   // 4: straight
    evaluateHand(cards("As Ks Qs 9s 7s 3d 2h")),   // 5: flush
    evaluateHand(cards("As Ah Ad Ks Kh 3d 2c")),   // 6: full house
    evaluateHand(cards("As Ah Ad Ac Ks 3d 2h")),   // 7: quads
    evaluateHand(cards("9s 8s 7s 6s 5s 3d 2h")),   // 8: straight flush
  ];

  let orderCorrect = true;
  for (let i = 0; i < hands.length; i++) {
    for (let j = i + 1; j < hands.length; j++) {
      if (compareHands(hands[j], hands[i]) !== 1) {
        orderCorrect = false;
        console.log(`  FAIL: category ${j} should beat ${i}`);
      }
    }
  }
  check("T58: all 9 categories in strict ascending order", orderCorrect);
}

// T59: compareHands is anti-symmetric: if A > B then B < A
{
  const h1 = evaluateHand(cards("As Ah Kd 7c 5s 3h 2d"));
  const h2 = evaluateHand(cards("Ks Kh 9d 7c 5s 3h 2d"));
  check("T59: anti-symmetric (A>B implies B<A)", compareHands(h1, h2) === 1 && compareHands(h2, h1) === -1);
}

// T60: compareHands reflexive: A == A
{
  const h = evaluateHand(cards("As Ah Kd 7c 5s 3h 2d"));
  check("T60: reflexive (A == A)", compareHands(h, h) === 0);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 9: 5-Card Input (minimum)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 9: 5-Card Input ===");

// T61: evaluateHand works with exactly 5 cards
{
  const h = evaluateHand(cards("As Ks Qs Js Ts"));
  check("T61: 5-card input works", h.category === 8 && h.ranks[0] === 14);
}

// T62: evaluateHand works with 6 cards
{
  const h = evaluateHand(cards("As Ks Qs Js Ts 3d"));
  check("T62: 6-card input works", h.category === 8 && h.ranks[0] === 14);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Section 10: Duplicate Rank Patterns
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Section 10: Duplicate Rank Patterns ===");

// T63: Two trips in 7 cards (higher trips used, lower becomes pair for FH)
{
  const h = evaluateHand(cards("As Ah Ad 5s 5h 5d Kc"));
  check("T63: two trips → FH with higher trips", h.category === 6);
  check("T63: aces over fives", h.ranks[0] === 14 && h.ranks[1] === 5);
}

// T64: Quads plus a pair — still quads (not FH)
{
  const h = evaluateHand(cards("As Ah Ad Ac Ks Kh 2d"));
  check("T64: quads + pair = quads", h.category === 7);
  check("T64: quad aces K kicker", h.ranks[0] === 14 && h.ranks[1] === 13);
}

// T65: Full house with two pair options — best FH wins
{
  // As Ah Ad Ks Kh Qs Qh → FH options: AAA-KK (best), AAA-QQ
  const h = evaluateHand(cards("As Ah Ad Ks Kh Qs Qh"));
  check("T65: FH picks best pair", h.category === 6);
  check("T65: AAA-KK not AAA-QQ", h.ranks[0] === 14 && h.ranks[1] === 13);
}

// T66: Three pairs → two pair (best two)
{
  const h = evaluateHand(cards("As Ah Ks Kh Qs Qh 2d"));
  check("T66: three pairs → two pair", h.category === 2);
  check("T66: AA-KK with Q kicker", h.ranks[0] === 14 && h.ranks[1] === 13 && h.ranks[2] === 12);
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n=== Evaluator Tests: ${passed}/${checks} passed, ${failed} failed ===`);
if (failed > 0) process.exit(1);
