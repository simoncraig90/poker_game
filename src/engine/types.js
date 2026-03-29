"use strict";

// ── Phases ─────────────────────────────────────────────────────────────────
const PHASE = {
  PREFLOP: "PREFLOP",
  FLOP: "FLOP",
  TURN: "TURN",
  RIVER: "RIVER",
  SHOWDOWN: "SHOWDOWN",
  SETTLING: "SETTLING",
  COMPLETE: "COMPLETE",
};

// ── Seat Status ────────────────────────────────────────────────────────────
const SEAT_STATUS = {
  EMPTY: "EMPTY",
  OCCUPIED: "OCCUPIED",
  SITTING_OUT: "SITTING_OUT",
};

// ── Action Types ───────────────────────────────────────────────────────────
const ACTION = {
  FOLD: "FOLD",
  CHECK: "CHECK",
  CALL: "CALL",
  BET: "BET",
  RAISE: "RAISE",
  BLIND_SB: "BLIND_SB",
  BLIND_BB: "BLIND_BB",
};

// ── Event Types ────────────────────────────────────────────────────────────
const EVENT = {
  TABLE_SNAPSHOT: "TABLE_SNAPSHOT",
  HAND_START: "HAND_START",
  BLIND_POST: "BLIND_POST",
  HERO_CARDS: "HERO_CARDS",
  PLAYER_ACTION: "PLAYER_ACTION",
  BET_RETURN: "BET_RETURN",
  DEAL_COMMUNITY: "DEAL_COMMUNITY",
  POT_UPDATE: "POT_UPDATE",
  POT_AWARD: "POT_AWARD",
  HAND_SUMMARY: "HAND_SUMMARY",
  HAND_RESULT: "HAND_RESULT",
  HAND_END: "HAND_END",
};

// ── Cards ──────────────────────────────────────────────────────────────────
const SUITS = { 1: "c", 2: "d", 3: "h", 4: "s" };
const SUIT_NAMES = { 1: "clubs", 2: "diamonds", 3: "hearts", 4: "spades" };

const RANKS = {
  2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
  10: "T", 11: "J", 12: "Q", 13: "K", 14: "A",
};

function cardDisplay(rank, suit) {
  return (RANKS[rank] || "?") + (SUITS[suit] || "?");
}

function makeCard(rank, suit) {
  return { rank, suit, display: cardDisplay(rank, suit) };
}

// ── Phase ordering for invariant checks ────────────────────────────────────
const PHASE_ORDER = [
  PHASE.PREFLOP, PHASE.FLOP, PHASE.TURN, PHASE.RIVER,
  PHASE.SHOWDOWN, PHASE.SETTLING, PHASE.COMPLETE,
];

function phaseIndex(phase) {
  return PHASE_ORDER.indexOf(phase);
}

module.exports = {
  PHASE, SEAT_STATUS, ACTION, EVENT,
  SUITS, SUIT_NAMES, RANKS,
  cardDisplay, makeCard,
  PHASE_ORDER, phaseIndex,
};
