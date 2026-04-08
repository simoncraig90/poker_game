#!/usr/bin/env node
"use strict";

/**
 * Round-robin bot evaluation framework.
 *
 * Runs all registered strategies against each other in fair 6-max games.
 * Each strategy plays equal hands in every seat position to eliminate
 * positional bias. Computes bb/100 and ELO ratings.
 *
 * Usage:
 *   node scripts/eval-bots.js                          # default: 2k hands/matchup
 *   node scripts/eval-bots.js --hands 5000             # more hands per matchup
 *   node scripts/eval-bots.js --strategies tag,cfr     # specific matchup
 *   node --max-old-space-size=4096 scripts/eval-bots.js --strategies tag,cfr,random,fish,lag
 */

const { createGame, ACTION, PHASE } = require("../src/index");
const { getLegalActions } = require("../src/engine/betting");
const path = require("path");
const fs = require("fs");

// ── RNG ────────────────────────────────────────────────────────────────

function createRng(seed = 42) {
  let s = seed;
  return function () {
    s = (s * 1664525 + 1013904223) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

// ── Hand Strength (shared by strategies) ───────────────────────────────

function evaluateHandStrength(cards, board, phase) {
  if (!cards || cards.length < 2) return 0.5;
  const c1 = cards[0], c2 = cards[1];
  const r1 = c1.rank, r2 = c2.rank;
  const suited = c1.suit === c2.suit;
  const pair = r1 === r2;
  const highCard = Math.max(r1, r2);
  const gap = Math.abs(r1 - r2);

  let pf = 0;
  if (pair) { pf = 0.5 + (r1 / 14) * 0.5; }
  else {
    pf = (highCard / 14) * 0.4;
    if (suited) pf += 0.08;
    if (gap <= 1) pf += 0.06;
    if (gap <= 3) pf += 0.03;
    if (r1 >= 10 && r2 >= 10) pf += 0.15;
    if (highCard === 14) pf += 0.1;
  }
  if (phase === PHASE.PREFLOP) return Math.min(1, pf);

  const boardRanks = board.map(c => c.rank);
  const boardSuits = board.map(c => c.suit);
  let post = pf;
  if (boardRanks.includes(r1)) post += 0.25;
  if (boardRanks.includes(r2)) post += 0.20;
  if (boardRanks.includes(r1) && boardRanks.includes(r2) && !pair) post += 0.20;
  if (pair && boardRanks.includes(r1)) post += 0.35;
  const suitCount = boardSuits.filter(s => s === c1.suit).length;
  if (suitCount >= 2 && suited) post += 0.12;
  if (suitCount >= 3 && (c1.suit === boardSuits[0] || c2.suit === boardSuits[0])) post += 0.30;
  if (pair && boardRanks.length > 0 && r1 > Math.max(...boardRanks)) post += 0.15;
  return Math.min(1, post);
}

// ── Strategies ─────────────────────────────────────────────────────────

function randomStrategy(seat, legal, state, rng) {
  const { actions, minBet, minRaise } = legal;
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };
  const pick = actions[Math.floor(rng() * actions.length)];
  if (pick === ACTION.BET) return { action: pick, amount: minBet };
  if (pick === ACTION.RAISE) return { action: pick, amount: minRaise };
  return { action: pick };
}

function tagStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const phase = hand.phase;
  const potSize = hand.pot || 0;
  const stack = seatState.stack;
  const strength = evaluateHandStrength(cards, hand.board || [], phase);

  if (phase === PHASE.PREFLOP) {
    const facingRaise = callAmount > (state.table.bb || 10);
    if (facingRaise) {
      // 3-bet with premiums
      if (strength > 0.85 && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(callAmount * 3, maxRaise)) };
      }
      // Call with strong hands
      if (strength > 0.50 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
      // Occasional light 3-bet (BTN/CO bluff)
      if (rng() < 0.08 && strength > 0.40 && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(callAmount * 3, maxRaise)) };
      }
      if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
      return { action: ACTION.FOLD };
    }
    // Open raise
    if (strength > 0.55 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
    }
    if (strength > 0.35 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }

  // Postflop
  const betRatio = potSize > 0 ? callAmount / potSize : 0;

  if (strength > 0.75) {
    // Strong hand: value bet/raise. Check-raise 15% for deception
    if (actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.75), maxRaise)) };
    }
    if (rng() < 0.15 && actions.includes(ACTION.CHECK) && !callAmount) {
      return { action: ACTION.CHECK }; // check-raise trap
    }
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.min(Math.floor(potSize * 0.66), stack)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  if (strength > 0.50) {
    // Medium hand: c-bet 65%, check-call otherwise
    if (!callAmount && rng() < 0.65 && actions.includes(ACTION.BET)) {
      return { action: ACTION.BET, amount: Math.max(minBet, Math.min(Math.floor(potSize * 0.50), stack)) };
    }
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && betRatio < 0.75) return { action: ACTION.CALL };
    return { action: ACTION.FOLD };
  }
  if (strength > 0.30) {
    // Weak hand: check, call small bets, fold to big bets
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && betRatio < 0.40) return { action: ACTION.CALL };
    // Bluff 12%
    if (rng() < 0.12 && actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.50)) };
    return { action: ACTION.FOLD };
  }
  // Junk: check or fold. Bluff 8%
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  if (rng() < 0.08 && actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.50)) };
  return { action: ACTION.FOLD };
}

// FISH: loose-passive, calls too much, rarely raises, but folds to big bets with nothing
function fishStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);
  const betRatio = potSize > 0 ? callAmount / potSize : 0;

  if (hand.phase === PHASE.PREFLOP) {
    // Fish plays ~50% of hands, rarely raises
    if (strength > 0.80 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: minRaise }; // min-raise only
    }
    if (strength > 0.20 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (rng() < 0.20 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.FOLD };
  }

  // Postflop: calls a lot but sizing-aware
  if (strength > 0.80) {
    // Strong: slow-play 60% (fish don't value bet enough), raise 40%
    if (rng() < 0.40 && actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: minRaise };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.40)) };
    return { action: ACTION.CHECK };
  }
  if (strength > 0.30) {
    // Medium: call almost always, fold to overbets
    if (betRatio > 1.0) {
      // Overbet: even fish fold sometimes
      if (rng() < 0.50) return { action: ACTION.FOLD };
    }
    if (betRatio > 0.75 && strength < 0.45) {
      // Big bet with weak medium hand: fold 30%
      if (rng() < 0.30) return { action: ACTION.FOLD };
    }
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    // Donk bet with medium hands sometimes
    if (rng() < 0.20 && actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.40)) };
    return { action: ACTION.CHECK };
  }
  // Weak: still calls small bets, folds to big ones
  if (betRatio < 0.40 && rng() < 0.40 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  if (rng() < 0.10 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
  return { action: ACTION.FOLD };
}

// LAG: loose-aggressive, wide opens, light 3-bets, barrels turn, bluffs frequently
function lagStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const stack = seatState.stack;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  if (hand.phase === PHASE.PREFLOP) {
    const facingRaise = callAmount > (state.table.bb || 10);
    if (facingRaise) {
      // 3-bet wide: premiums + bluffs
      if (strength > 0.70 && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(callAmount * 3, maxRaise)) };
      }
      // Light 3-bet 15%
      if (rng() < 0.15 && strength > 0.30 && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(callAmount * 3, maxRaise)) };
      }
      if (strength > 0.30 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
      if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
      return { action: ACTION.FOLD };
    }
    // Open very wide
    if (strength > 0.30 && actions.includes(ACTION.RAISE)) {
      const amt = Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.75), maxRaise));
      return { action: ACTION.RAISE, amount: amt };
    }
    if (rng() < 0.15 && actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: minRaise };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }

  // Postflop: aggressive with strong, barrels with medium, bluffs with weak
  const betRatio = potSize > 0 ? callAmount / potSize : 0;

  if (strength > 0.55) {
    // Strong: bet/raise big
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize), maxRaise)) };
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.min(Math.floor(potSize * 0.75), stack)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  if (strength > 0.30) {
    // Medium: barrel turn (bet when checked to), call flop bets, fold to big raises
    if (!callAmount && actions.includes(ACTION.BET)) {
      // Probe bet / barrel 55%
      if (rng() < 0.55) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.60)) };
    }
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && betRatio < 0.60) return { action: ACTION.CALL };
    // Float: call one bet, plan to bet turn
    if (rng() < 0.20 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.FOLD };
  }
  // Weak: bluff 25%, fold to bets
  if (!callAmount && rng() < 0.25 && actions.includes(ACTION.BET)) {
    return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.55)) };
  }
  // Bluff-raise 8%
  if (rng() < 0.08 && actions.includes(ACTION.RAISE)) {
    return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize * 0.75), maxRaise)) };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  return { action: ACTION.FOLD };
}

// NIT: ultra-tight, only plays premium hands, folds to 3-bets without AA/KK
function nitStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  if (hand.phase === PHASE.PREFLOP) {
    const facingRaise = callAmount > (state.table.bb || 10);
    const facing3bet = callAmount > (state.table.bb || 10) * 5; // rough 3-bet detection
    if (facing3bet) {
      // Nit folds to 3-bets without top premiums
      if (strength > 0.90 && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(callAmount * 2.5, maxRaise)) };
      }
      if (strength > 0.85 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
      return { action: ACTION.FOLD };
    }
    if (strength > 0.8 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
    }
    if (strength > 0.6 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }
  // Postflop: only continue with strong hands, rare river bluff
  if (strength > 0.7) {
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize * 0.5), maxRaise)) };
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.5)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  if (strength > 0.5) {
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.3) return { action: ACTION.CALL };
    return { action: ACTION.FOLD };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  // Rare river bluff when draw missed (5%)
  if (rng() < 0.05 && actions.includes(ACTION.BET)) {
    return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.66)) };
  }
  return { action: ACTION.FOLD };
}

// CFR strategies (loaded on demand)
let _cfrStrategyFn = null;
function getCFRStrategy() {
  if (!_cfrStrategyFn) {
    const { createCFRStrategy } = require("./cfr/cfr-bot");
    _cfrStrategyFn = createCFRStrategy("./vision/models/cfr_strategy.json");
  }
  return _cfrStrategyFn;
}
function cfrStrategy(seat, legal, state, rng) {
  return getCFRStrategy()(seat, legal, state, rng);
}

let _cfr50StrategyFn = null;
function getCFR50Strategy() {
  if (!_cfr50StrategyFn) {
    const { createCFRStrategy } = require("./cfr/cfr-bot");
    _cfr50StrategyFn = createCFRStrategy("./vision/models/cfr_strategy_50bucket.json");
  }
  return _cfr50StrategyFn;
}
function cfr50Strategy(seat, legal, state, rng) {
  return getCFR50Strategy()(seat, legal, state, rng);
}

let _cfr100StrategyFn = null;
function getCFR100Strategy() {
  if (!_cfr100StrategyFn) {
    const { createCFRStrategy } = require("./cfr/cfr-bot");
    _cfr100StrategyFn = createCFRStrategy("./vision/models/cfr_strategy_sixmax_100bucket.json", 100);
  }
  return _cfr100StrategyFn;
}
function cfr100Strategy(seat, legal, state, rng) {
  return getCFR100Strategy()(seat, legal, state, rng);
}

// ── Stratified Strategy (preflop chart + flop CFR + turn/river rules) ──

let _flopCFR = null;
function loadFlopCFR() {
  if (!_flopCFR) {
    const flopPath = path.resolve("./vision/models/cfr_strategy_flop.json");
    if (fs.existsSync(flopPath)) {
      _flopCFR = JSON.parse(fs.readFileSync(flopPath, "utf8"));
      console.log(`[Stratified] Loaded flop CFR: ${Object.keys(_flopCFR).length} info sets`);
    }
  }
  return _flopCFR;
}

// Preflop ranges (simplified JS version of preflop_chart.py)
const PREFLOP_OPEN = {
  EP: new Set(["AA","KK","QQ","JJ","TT","99","AKs","AQs","AJs","ATs","KQs","KJs","AKo","AQo"]),
  MP: new Set(["AA","KK","QQ","JJ","TT","99","88","77","AKs","AQs","AJs","ATs","A9s","A8s","KQs","KJs","KTs","QJs","QTs","JTs","AKo","AQo","AJo","KQo"]),
  CO: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","QJs","QTs","Q9s","JTs","J9s","T9s","98s","87s","76s","AKo","AQo","AJo","ATo","KQo","KJo","QJo"]),
  BTN: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","44","33","22","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","K8s","K7s","K6s","K5s","QJs","QTs","Q9s","Q8s","JTs","J9s","J8s","T9s","T8s","98s","97s","87s","86s","76s","75s","65s","54s","AKo","AQo","AJo","ATo","A9o","A8o","A7o","A6o","A5o","KQo","KJo","KTo","QJo","QTo","JTo"]),
};

function handKey(c1, c2) {
  const RANK_CHARS = {2:"2",3:"3",4:"4",5:"5",6:"6",7:"7",8:"8",9:"9",10:"T",11:"J",12:"Q",13:"K",14:"A"};
  let r1 = c1.rank, r2 = c2.rank, s1 = c1.suit, s2 = c2.suit;
  if (r1 < r2) { [r1,r2,s1,s2] = [r2,r1,s2,s1]; }
  const rc1 = RANK_CHARS[r1], rc2 = RANK_CHARS[r2];
  if (r1 === r2) return `${rc1}${rc2}`;
  return s1 === s2 ? `${rc1}${rc2}s` : `${rc1}${rc2}o`;
}

function flopActionEncode(action, amount, pot) {
  if (action === "FOLD") return "f";
  if (action === "CHECK") return "k";
  if (action === "CALL") return "c";
  const ratio = pot > 0 ? amount / pot : 0.5;
  if (ratio >= 0.85) return "bp";
  if (ratio >= 0.5) return "bs";
  return "bt";
}

function stratifiedStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const board = hand.board || [];
  const phase = hand.phase;
  const potSize = hand.pot || 0;
  const stack = seatState.stack;
  const strength = evaluateHandStrength(cards, board, phase);
  const rand = rng || Math.random;

  // Position
  const dealer = hand.dealer ?? 0;
  const numSeats = Object.keys(state.table.seats).filter(s => state.table.seats[s]).length;
  const relPos = ((seat - dealer) % numSeats + numSeats) % numSeats;
  const posName = relPos === 0 ? "BTN" : relPos === 1 ? "SB" : relPos === 2 ? "BB" :
                  relPos === numSeats - 1 ? "CO" : relPos === numSeats - 2 ? "MP" : "EP";
  const isIP = posName === "BTN" || posName === "CO";

  // ── PREFLOP: chart-based ──
  if (phase === PHASE.PREFLOP) {
    if (cards.length < 2) return { action: ACTION.FOLD };
    const key = handKey(cards[0], cards[1]);
    const range = PREFLOP_OPEN[posName] || PREFLOP_OPEN["CO"];

    if (posName === "BB" && !actions.includes(ACTION.RAISE) && actions.includes(ACTION.CHECK)) {
      return { action: ACTION.CHECK }; // free look
    }

    const facingRaise = callAmount > 0 && callAmount > (state.table.bb || 10);
    if (facingRaise) {
      // Facing raise: 3-bet premiums, call good hands, fold rest
      const premiums = new Set(["AA","KK","QQ","AKs","AKo"]);
      const calls = new Set(["JJ","TT","99","88","77","AQs","AJs","ATs","KQs","KJs","QJs","JTs","AQo"]);
      if (premiums.has(key) && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.min(callAmount * 3, maxRaise) };
      }
      if ((premiums.has(key) || calls.has(key)) && actions.includes(ACTION.CALL)) {
        return { action: ACTION.CALL };
      }
      if (posName === "BB" && range.has(key) && actions.includes(ACTION.CALL)) {
        return { action: ACTION.CALL }; // BB defends wider
      }
      return { action: ACTION.FOLD };
    }

    if (range.has(key) && actions.includes(ACTION.RAISE)) {
      const amt = Math.max(minRaise, Math.min(Math.floor(potSize + (state.table.bb || 10) * 2.5), maxRaise));
      return { action: ACTION.RAISE, amount: amt };
    }

    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }

  // ── FLOP: CFR lookup ──
  if (phase === PHASE.FLOP) {
    const flopCFR = loadFlopCFR();
    if (flopCFR && board.length >= 3) {
      const bucket = Math.min(49, Math.floor(strength * 50));
      const sb = stack < 300 ? 0 : stack < 800 ? 1 : 2;
      const pos = isIP ? "IP" : "OOP";
      const potBB = potSize / (state.table.bb || 10);
      const potClass = potBB >= 15 ? "3BP" : potBB <= 3 ? "LP" : "SRP";

      // Build action history from this street's actions (before hero)
      let hist = "";
      const streetActions = (hand.actions || []).filter(a =>
        a.street === "FLOP" && a.seat !== seat && a.type !== "FOLD"
      );
      // Simplified: just track opponent actions before hero
      for (const a of streetActions) {
        if (a.type === "CHECK") hist += "k";
        else if (a.type === "CALL") hist += "c";
        else if (a.type === "BET" || a.type === "RAISE") {
          const r = potSize > 0 ? (a.amount || 0) / potSize : 0.5;
          hist += r >= 0.85 ? "bp" : r >= 0.5 ? "bs" : "bt";
        }
      }

      // Lookup with fuzzy bucket matching
      let probs = null;
      for (let d = 0; d <= 5; d++) {
        for (const delta of d === 0 ? [0] : [d, -d]) {
          const b = bucket + delta;
          if (b < 0 || b >= 50) continue;
          const k = `FLOP:${b}:s${sb}:${pos}:${potClass}:${hist}`;
          if (flopCFR[k]) { probs = flopCFR[k]; break; }
        }
        if (probs) break;
      }

      if (probs) {
        // Argmax: pick the highest-probability action category
        const fold = probs.FOLD || 0;
        const check = probs.CHECK || 0;
        const call = probs.CALL || 0;
        const bet33 = probs.BET_33 || 0;
        const bet66 = probs.BET_66 || 0;
        const betPot = probs.BET_POT || 0;
        const betAll = probs.BET_ALLIN || 0;
        const raiseH = probs.RAISE_HALF || 0;
        const raiseP = probs.RAISE_POT || 0;
        const raiseA = probs.RAISE_ALLIN || 0;
        const agg = bet33 + bet66 + betPot + betAll + raiseH + raiseP + raiseA;

        // Pick best category
        const best = Math.max(fold, check, call, agg);
        if (best <= 0) { /* fall through to rules */ }
        else if (best === fold && actions.includes(ACTION.FOLD)) {
          return { action: ACTION.FOLD };
        } else if (best === check && actions.includes(ACTION.CHECK)) {
          return { action: ACTION.CHECK };
        } else if (best === call && actions.includes(ACTION.CALL)) {
          return { action: ACTION.CALL };
        } else if (best === agg) {
          // Pick best sizing within aggressive actions
          const sizes = [
            { p: bet33, act: ACTION.BET, amt: Math.max(minBet, Math.floor(potSize * 0.33)) },
            { p: bet66, act: ACTION.BET, amt: Math.max(minBet, Math.floor(potSize * 0.66)) },
            { p: betPot, act: ACTION.BET, amt: Math.max(minBet, potSize) },
            { p: betAll, act: ACTION.BET, amt: stack },
            { p: raiseH, act: ACTION.RAISE, amt: Math.max(minRaise, Math.floor(potSize * 0.5)) },
            { p: raiseP, act: ACTION.RAISE, amt: Math.max(minRaise, potSize) },
            { p: raiseA, act: ACTION.RAISE, amt: maxRaise },
          ];
          sizes.sort((a, b) => b.p - a.p);
          for (const s of sizes) {
            if (s.p > 0 && actions.includes(s.act)) {
              return { action: s.act, amount: Math.min(s.amt, stack) };
            }
          }

        }
      }
    }
    // Fallback to equity rules
  }

  // ── TURN/RIVER: equity + rules with trap/float detection ──
  if (!stratifiedStrategy._stats) stratifiedStrategy._stats = { turnRiver: 0, trapDetect: 0, barrel: 0 };
  stratifiedStrategy._stats.turnRiver++;

  // Detect opponent patterns from action history
  const handActions = hand.actions || [];
  const oppActions = handActions.filter(a => a.seat !== seat && a.type !== "FOLD" && a.type !== "BLIND_SB" && a.type !== "BLIND_BB");
  const oppPostflopActions = oppActions.filter(a => a.street && a.street !== "PREFLOP");

  // Trap detection: opponent was passive then suddenly aggressive
  const oppChecks = oppPostflopActions.filter(a => a.type === "CHECK").length;
  const oppCalls = oppPostflopActions.filter(a => a.type === "CALL").length;
  const oppBets = oppPostflopActions.filter(a => a.type === "BET" || a.type === "RAISE").length;
  const oppPassiveCount = oppChecks + oppCalls;
  const wasPassive = oppPassiveCount >= 1 && oppBets <= 1; // lowered threshold: even 1 passive action counts
  const suddenAggression = wasPassive && callAmount > 0 && callAmount > potSize * 0.40;

  // Float detection: opponent called flop, now we're on turn
  const oppCalledFlop = oppPostflopActions.some(a => a.street === "FLOP" && a.type === "CALL");
  const oppCheckedFlop = oppPostflopActions.some(a => a.street === "FLOP" && a.type === "CHECK");
  const onTurn = phase === PHASE.TURN;
  const onRiver = phase === PHASE.RIVER;

  // Did we bet the previous street? Or was it checked through?
  const heroPrevBets = handActions.filter(a => a.seat === seat && (a.type === "BET" || a.type === "RAISE"));
  const heroBetFlop = heroPrevBets.some(a => a.street === "FLOP");
  const heroBetTurn = heroPrevBets.some(a => a.street === "TURN");
  const flopCheckedThrough = !heroBetFlop && oppCheckedFlop; // both checked flop

  // Adjust equity based on opponent pattern
  let adjStrength = strength;
  if (suddenAggression && onRiver) {
    // Opponent was passive, now betting big on river = likely has it
    adjStrength = strength * 0.65; // heavy discount
    stratifiedStrategy._stats.trapDetect++;
  } else if (callAmount > potSize * 0.75) {
    // Big bet from anyone = discount
    adjStrength = strength * 0.80;
  }

  // Strong hand
  if (adjStrength > 0.60) {
    // But be cautious of traps on river
    if (suddenAggression && adjStrength < 0.75) {
      // Trap likely — just call, don't raise
      if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    }
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize * 0.75), maxRaise)) };
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.min(Math.floor(potSize * 0.66), stack)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }

  // Medium hand
  if (adjStrength > 0.30) {
    // Turn barrel: continue aggression or take initiative after checked flop
    if (onTurn && !callAmount && actions.includes(ACTION.BET)) {
      if (heroBetFlop && oppCalledFlop && rand() < 0.55) {
        // Classic barrel: we bet flop, they called, keep firing
        stratifiedStrategy._stats.barrel++;
        return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.55)) };
      }
      if (flopCheckedThrough && rand() < 0.50) {
        // Delayed c-bet: flop went check-check, take it on turn
        stratifiedStrategy._stats.barrel++;
        return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.50)) };
      }
    }
    // River barrel: if we bet flop+turn, fire river 35% with medium+
    if (onRiver && (heroBetFlop || heroBetTurn) && !callAmount && adjStrength > 0.40 && rand() < 0.35 && actions.includes(ACTION.BET)) {
      return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.55)) };
    }

    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };

    // Facing a bet: fold to sudden river aggression from passive opponent
    if (suddenAggression) return { action: ACTION.FOLD };
    if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.50) return { action: ACTION.CALL };
    // Thin value bet sometimes
    if (rand() < 0.15 && actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.45)) };
    return { action: ACTION.FOLD };
  }

  // Weak hand
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  // Turn bluff: after flop bet or checked-through flop, bluff turn 20%
  if (onTurn && (heroBetFlop || flopCheckedThrough) && !callAmount && rand() < 0.20 && actions.includes(ACTION.BET)) {
    return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.50)) };
  }
  // River bluff 10%
  if (rand() < 0.10 && actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.50)) };
  return { action: ACTION.FOLD };
}

// ── Exploitative Counter-Bots ──────────────────────────────────────────

// FLOAT BOT: calls flop bets, then bets turn when checked to.
// Exploits one-and-done c-bets and passive turn play.
function floatStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  // Preflop: play like TAG
  if (hand.phase === PHASE.PREFLOP) {
    if (strength > 0.55 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
    }
    if (strength > 0.35 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }

  // Flop: call bets with any piece or draw (float)
  if (hand.phase === PHASE.FLOP) {
    if (strength > 0.60) {
      if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.floor(potSize * 0.75)) };
      if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.60)) };
      if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    }
    // Float with medium+ hands — call the flop, plan to take it on turn
    if (strength > 0.20 && actions.includes(ACTION.CALL) && callAmount < potSize * 0.80) {
      return { action: ACTION.CALL };
    }
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }

  // Turn: bet when checked to (the float payoff). This is the exploit.
  if (!callAmount && actions.includes(ACTION.BET)) {
    // Bet 70% of the time when checked to on turn (the probe)
    if (rng() < 0.70) {
      return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.60)) };
    }
  }
  // With strong hands, always bet/raise
  if (strength > 0.65) {
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.floor(potSize * 0.75)) };
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.66)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.40) return { action: ACTION.CALL };
  return { action: ACTION.FOLD };
}

// PROBE BOT: bets every time it's checked to. Punishes passive play.
function probeStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const stack = seatState.stack;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  // Preflop: TAG-ish
  if (hand.phase === PHASE.PREFLOP) {
    if (strength > 0.55 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
    }
    if (strength > 0.35 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }

  // Postflop: ALWAYS bet when checked to, regardless of hand strength
  if (!callAmount && actions.includes(ACTION.BET)) {
    const size = strength > 0.60 ? Math.floor(potSize * 0.66) : Math.floor(potSize * 0.40);
    return { action: ACTION.BET, amount: Math.max(minBet, Math.min(size, stack)) };
  }

  // Facing a bet: play normally
  if (strength > 0.65) {
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.floor(potSize * 0.75)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
  }
  if (strength > 0.35 && actions.includes(ACTION.CALL) && callAmount < potSize * 0.60) {
    return { action: ACTION.CALL };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  return { action: ACTION.FOLD };
}

// CHECK-RAISE BOT: check-raises flop 25% with strong hands + bluffs.
// Tests response to aggression after showing weakness.
function checkRaiseStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  // Preflop: TAG
  if (hand.phase === PHASE.PREFLOP) {
    if (strength > 0.55 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
    }
    if (strength > 0.40 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }

  // Flop: check-raise strategy
  if (hand.phase === PHASE.FLOP) {
    // If facing a bet: raise strong hands + bluffs
    if (callAmount > 0) {
      // Check-raise with strong hands (70%) and bluffs (15%)
      if (strength > 0.70 && rng() < 0.70 && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize * 0.80), maxRaise)) };
      }
      // Bluff check-raise with weak hands
      if (strength < 0.25 && rng() < 0.15 && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize * 0.80), maxRaise)) };
      }
      if (strength > 0.35 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
      return { action: ACTION.FOLD };
    }
    // Not facing bet: check to induce (the setup for check-raise)
    if (strength > 0.70 && rng() < 0.60) return { action: ACTION.CHECK }; // trap
    if (strength > 0.50 && actions.includes(ACTION.BET)) {
      return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.50)) };
    }
    return { action: ACTION.CHECK };
  }

  // Turn/River: standard play
  if (strength > 0.65) {
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.66)) };
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.floor(potSize * 0.75)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  if (strength > 0.35) {
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.50) return { action: ACTION.CALL };
    return { action: ACTION.FOLD };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  return { action: ACTION.FOLD };
}

// SQUEEZE BOT: 3-bets wide from blinds vs late position opens.
// Tests preflop chart resilience under pressure.
function squeezeStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  if (hand.phase === PHASE.PREFLOP) {
    const facingRaise = callAmount > (state.table.bb || 10);
    if (facingRaise) {
      // Squeeze: 3-bet 30% of the time with any playable hand
      if (strength > 0.75 && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(callAmount * 3.5, maxRaise)) };
      }
      if (rng() < 0.30 && strength > 0.25 && actions.includes(ACTION.RAISE)) {
        return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(callAmount * 3, maxRaise)) };
      }
      if (strength > 0.35 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
      if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
      return { action: ACTION.FOLD };
    }
    // Open: standard
    if (strength > 0.50 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
    }
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }

  // Postflop: c-bet aggressively after 3-betting, give up if called
  if (strength > 0.55) {
    if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.66)) };
    if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.floor(potSize * 0.75)) };
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    return { action: ACTION.CHECK };
  }
  // C-bet bluff 50% on flop after 3-betting
  if (hand.phase === PHASE.FLOP && !callAmount && rng() < 0.50 && actions.includes(ACTION.BET)) {
    return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.50)) };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.30) return { action: ACTION.CALL };
  return { action: ACTION.FOLD };
}

// TRAP BOT: slow-plays strong hands, check-calls flop/turn, raises river.
// Tests if opponent pays off when trap springs.
function trapStrategy(seat, legal, state, rng) {
  const { actions, callAmount, minBet, minRaise, maxRaise } = legal;
  const hand = state.hand;
  const seatState = state.table.seats[seat];
  if (!actions.length) return null;
  if (actions.length === 1) return { action: actions[0] };

  const cards = seatState.holeCards || [];
  const potSize = hand.pot || 0;
  const strength = evaluateHandStrength(cards, hand.board || [], hand.phase);

  // Preflop: tight, flat premiums (don't 3-bet, just call to trap)
  if (hand.phase === PHASE.PREFLOP) {
    if (strength > 0.85 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL }; // flat AA/KK
    if (strength > 0.70 && actions.includes(ACTION.RAISE)) {
      return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(minRaise + Math.floor(potSize * 0.5), maxRaise)) };
    }
    if (strength > 0.45 && actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    return { action: ACTION.FOLD };
  }

  // Postflop: slow-play strong, fold weak
  const isRiver = hand.phase === PHASE.RIVER;

  if (strength > 0.75) {
    if (isRiver) {
      // Spring the trap on river: big bet or raise
      if (actions.includes(ACTION.RAISE)) return { action: ACTION.RAISE, amount: Math.max(minRaise, Math.min(Math.floor(potSize), maxRaise)) };
      if (actions.includes(ACTION.BET)) return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.80)) };
    }
    // Flop/Turn: check-call (the slow-play)
    if (actions.includes(ACTION.CALL)) return { action: ACTION.CALL };
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    // If no one bets, small bet to build pot
    if (rng() < 0.30 && actions.includes(ACTION.BET)) {
      return { action: ACTION.BET, amount: Math.max(minBet, Math.floor(potSize * 0.33)) };
    }
    return { action: ACTION.CHECK };
  }
  if (strength > 0.40) {
    if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
    if (actions.includes(ACTION.CALL) && callAmount < potSize * 0.50) return { action: ACTION.CALL };
    return { action: ACTION.FOLD };
  }
  if (actions.includes(ACTION.CHECK)) return { action: ACTION.CHECK };
  return { action: ACTION.FOLD };
}

// Strategy registry
const STRATEGIES = {
  random:     { name: "Random",     fn: randomStrategy },
  tag:        { name: "TAG",        fn: tagStrategy },
  fish:       { name: "FISH",       fn: fishStrategy },
  lag:        { name: "LAG",        fn: lagStrategy },
  nit:        { name: "NIT",        fn: nitStrategy },
  cfr:        { name: "CFR-10",     fn: cfrStrategy },
  cfr50:      { name: "CFR-50",     fn: cfr50Strategy },
  cfr100:     { name: "CFR-100",    fn: cfr100Strategy },
  stratified: { name: "STRAT",      fn: stratifiedStrategy },
  float:      { name: "FLOAT",      fn: floatStrategy },
  probe:      { name: "PROBE",      fn: probeStrategy },
  checkraise: { name: "XR",         fn: checkRaiseStrategy },
  squeeze:    { name: "SQUEEZE",    fn: squeezeStrategy },
  trap:       { name: "TRAP",       fn: trapStrategy },
};

// ── Run a single table session ─────────────────────────────────────────

function runSession(strategyNames, numHands, seed, startStack) {
  const numSeats = strategyNames.length;
  const rng = createRng(seed);
  const strategies = strategyNames.map(s => STRATEGIES[s].fn);
  const names = strategyNames.map(s => STRATEGIES[s].name);

  const game = createGame(
    { tableId: "eval", tableName: "Eval", maxSeats: numSeats, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 50000 },
    { sessionId: `eval-${seed}`, logPath: null, rng }
  );

  for (let i = 0; i < numSeats; i++) {
    game.sitDown(i, `${names[i]}_s${i}`, startStack);
  }

  const results = names.map(name => ({
    name, handsPlayed: 0, profit: 0, wins: 0, vpip: 0, pfr: 0,
    perHandProfits: [],  // track each hand's profit for stdev/CI
  }));

  let handsCompleted = 0;
  let errors = 0;

  for (let h = 0; h < numHands; h++) {
    // Rebuy busted players
    try {
      const st = game.getState();
      for (let i = 0; i < numSeats; i++) {
        const s = st.table.seats[i];
        if (s && s.stack < 20) {
          game.leave(i);
          game.sitDown(i, `${names[i]}_s${i}`, startStack);
        }
      }
    } catch (e) {}

    try { game.startHand(); } catch (e) { errors++; continue; }

    const preState = game.getState();
    const preStacks = {};
    for (let i = 0; i < numSeats; i++) {
      const s = preState.table.seats[i];
      if (s) preStacks[i] = s.stack;
    }

    let actionCount = 0;
    while (!game.isHandComplete() && actionCount < 100) {
      const actionSeat = game.getActionSeat();
      if (actionSeat === null) break;
      const currentState = game.getState();
      const seatState = currentState.table.seats[actionSeat];
      if (!seatState || !seatState.inHand) break;
      const legal = getLegalActions(seatState, currentState.hand, currentState.table.bb);
      if (!legal.actions.length) break;

      const decision = strategies[actionSeat](actionSeat, legal, currentState, rng);
      if (!decision) break;

      try {
        game.act(actionSeat, decision.action, decision.amount);
      } catch (e) {
        try { game.act(actionSeat, ACTION.FOLD); } catch (_) {}
        errors++;
      }
      actionCount++;
    }

    handsCompleted++;
    const postState = game.getState();
    for (let i = 0; i < numSeats; i++) {
      const s = postState.table.seats[i];
      if (s && preStacks[i] !== undefined) {
        const profit = s.stack - preStacks[i];
        results[i].profit += profit;
        results[i].perHandProfits.push(profit);
        results[i].handsPlayed++;
        if (profit > 0) results[i].wins++;
      }
    }
  }

  return { results, handsCompleted, errors };
}

// ── Round-Robin Evaluation ─────────────────────────────────────────────

function runRoundRobin(strategyKeys, handsPerMatchup, startStack) {
  const BB = 10;
  const pairResults = {}; // "A vs B" -> { aProfit, bProfit, hands }

  // Run all strategies together at one 6-max table.
  // If fewer than 6 strategies, pad with TAG bots.
  // If more than 6, run multiple tables (not yet supported).
  // Rotate seat assignments across multiple sessions for position fairness.

  const numSeats = 6;
  const paddedKeys = strategyKeys.slice(0, numSeats);
  while (paddedKeys.length < numSeats) paddedKeys.push("tag"); // pad to 6

  // Per-strategy aggregate stats
  const aggStats = {};
  for (const k of strategyKeys) {
    aggStats[k] = { profit: 0, hands: 0, wins: 0, perHandProfits: [] };
  }

  // Run multiple rotations: shift seat assignments each time
  const numRotations = numSeats; // one rotation per seat position
  const handsPerRotation = Math.floor(handsPerMatchup / numRotations);

  console.log(`\nRunning ${numRotations} rotations x ${handsPerRotation} hands = ${numRotations * handsPerRotation} hands total...\n`);

  for (let rot = 0; rot < numRotations; rot++) {
    // Rotate seating: shift all strategies by 'rot' positions
    const seating = [];
    for (let i = 0; i < numSeats; i++) {
      seating.push(paddedKeys[(i + rot) % numSeats]);
    }

    const seed = (rot + 1) * 10000;
    const { results, handsCompleted, errors } = runSession(seating, handsPerRotation, seed, startStack);

    for (let i = 0; i < results.length; i++) {
      const stratKey = seating[i];
      if (aggStats[stratKey]) {
        aggStats[stratKey].profit += results[i].profit;
        aggStats[stratKey].hands += results[i].handsPlayed;
        aggStats[stratKey].wins += results[i].wins;
        aggStats[stratKey].perHandProfits.push(...results[i].perHandProfits);
      }
    }

    // Show progress
    const seatNames = seating.map(k => STRATEGIES[k].name);
    process.stdout.write(`  Rotation ${rot + 1}/${numRotations} [${seatNames.join(",")}]`);
    const rotResults = [];
    for (const k of strategyKeys) {
      const s = aggStats[k];
      const bb100 = s.hands > 0 ? (s.profit / BB) / (s.hands / 100) : 0;
      rotResults.push(`${STRATEGIES[k].name}:${bb100 >= 0 ? "+" : ""}${bb100.toFixed(0)}`);
    }
    console.log(` → ${rotResults.join(" | ")}`);
  }

  // Build pairwise from aggregate (for ELO)
  for (let i = 0; i < strategyKeys.length; i++) {
    for (let j = i + 1; j < strategyKeys.length; j++) {
      const a = strategyKeys[i], b = strategyKeys[j];
      const sa = aggStats[a], sb = aggStats[b];
      const aBB100 = sa.hands > 0 ? (sa.profit / BB) / (sa.hands / 100) : 0;
      const bBB100 = sb.hands > 0 ? (sb.profit / BB) / (sb.hands / 100) : 0;
      pairResults[`${a} vs ${b}`] = { a, b, aBB100, bBB100, aHands: sa.hands, bHands: sb.hands };
    }
  }

  return { pairResults, aggStats };
}

// ── ELO Calculation ────────────────────────────────────────────────────

function computeELO(pairResults, strategyKeys) {
  // Simple iterative ELO: start at 1500, update from pairwise results
  const elo = {};
  for (const k of strategyKeys) elo[k] = 1500;

  const K = 32;
  // Run 10 iterations to stabilize
  for (let iter = 0; iter < 10; iter++) {
    for (const key of Object.keys(pairResults)) {
      const { a, b, aBB100, bBB100 } = pairResults[key];
      // Convert bb/100 difference to win probability
      const diff = aBB100 - bBB100;
      const actualA = diff > 0 ? 1 : diff < 0 ? 0 : 0.5;

      const expectedA = 1 / (1 + Math.pow(10, (elo[b] - elo[a]) / 400));
      elo[a] += K * (actualA - expectedA);
      elo[b] += K * ((1 - actualA) - (1 - expectedA));
    }
  }
  return elo;
}

// ── Main ───────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const opts = {};
for (let i = 0; i < args.length; i += 2) {
  const key = args[i] ? args[i].replace("--", "") : "";
  const val = args[i + 1];
  if (key === "hands") opts.hands = parseInt(val);
  else if (key === "strategies") opts.strategies = val;
  else if (key === "stack") opts.stack = parseInt(val);
}

const handsPerMatchup = opts.hands || 20000;
const startStack = opts.stack || 1000;
const strategyKeys = opts.strategies
  ? opts.strategies.split(",").filter(k => STRATEGIES[k])
  : ["tag", "fish", "lag", "nit", "random"];

console.log("=" .repeat(60));
console.log("  BOT EVALUATION FRAMEWORK (Round-Robin)");
console.log("=".repeat(60));
console.log(`  Strategies: ${strategyKeys.map(k => STRATEGIES[k].name).join(", ")}`);
console.log(`  Hands/matchup: ${handsPerMatchup}`);
console.log(`  Starting stack: ${startStack} chips`);
console.log(`  Total matchups: ${strategyKeys.length * (strategyKeys.length - 1) / 2}`);

const BB = 10;
const { pairResults, aggStats } = runRoundRobin(strategyKeys, handsPerMatchup, startStack);

// ── Print Pairwise Results ─────────────────────────────────────────────

console.log("\n" + "=".repeat(60));
console.log("  PAIRWISE RESULTS");
console.log("=".repeat(60));
for (const [key, r] of Object.entries(pairResults)) {
  const aName = STRATEGIES[r.a].name;
  const bName = STRATEGIES[r.b].name;
  console.log(`  ${aName} vs ${bName}: ${r.aBB100 >= 0 ? "+" : ""}${r.aBB100.toFixed(1)} vs ${r.bBB100 >= 0 ? "+" : ""}${r.bBB100.toFixed(1)} bb/100 (${r.aHands + r.bHands} hands)`);
}

// ── Print Aggregate Rankings ───────────────────────────────────────────

console.log("\n" + "=".repeat(60));
console.log("  AGGREGATE RANKINGS");
console.log("=".repeat(60));

const rankings = strategyKeys.map(k => {
  const s = aggStats[k];
  const bb100 = s.hands > 0 ? (s.profit / BB) / (s.hands / 100) : 0;
  const mbbHand = bb100 / 10; // mbb/hand = bb/100 / 10
  const winPct = s.hands > 0 ? (s.wins / s.hands * 100) : 0;

  // Standard deviation and 95% confidence interval
  let stdev = 0, ci95 = 0;
  if (s.perHandProfits.length > 1) {
    const profitsBB = s.perHandProfits.map(p => p / BB); // convert to BB
    const mean = profitsBB.reduce((a, b) => a + b, 0) / profitsBB.length;
    const variance = profitsBB.reduce((a, b) => a + (b - mean) ** 2, 0) / (profitsBB.length - 1);
    stdev = Math.sqrt(variance);
    const se = stdev / Math.sqrt(profitsBB.length); // standard error
    ci95 = se * 1.96; // 95% CI half-width in BB/hand
  }

  return { key: k, name: STRATEGIES[k].name, bb100, mbbHand, winPct, hands: s.hands, stdev, ci95: ci95 * 100 }; // ci95 in bb/100
}).sort((a, b) => b.bb100 - a.bb100);

// ELO
const elo = computeELO(pairResults, strategyKeys);

console.log(`\n  ${"Rank".padEnd(5)} ${"Strategy".padEnd(10)} ${"bb/100".padStart(12)} ${"mbb/h".padStart(8)} ${"95% CI".padStart(12)} ${"Win%".padStart(8)} ${"ELO".padStart(7)} ${"Hands".padStart(8)}`);
console.log("  " + "-".repeat(75));
for (let i = 0; i < rankings.length; i++) {
  const r = rankings[i];
  const eloVal = Math.round(elo[r.key]);
  const bb100Str = (r.bb100 >= 0 ? "+" : "") + r.bb100.toFixed(1);
  const mbbStr = (r.mbbHand >= 0 ? "+" : "") + r.mbbHand.toFixed(1);
  const ciStr = `±${r.ci95.toFixed(1)}`;
  console.log(`  ${String(i + 1).padEnd(5)} ${r.name.padEnd(10)} ${bb100Str.padStart(12)} ${mbbStr.padStart(8)} ${ciStr.padStart(12)} ${r.winPct.toFixed(1).padStart(7)}% ${String(eloVal).padStart(7)} ${String(r.hands).padStart(8)}`);
}

console.log("\n" + "=".repeat(60));

// Save results to JSON
const outPath = path.join(__dirname, "..", "vision", "data", "eval_results.json");
const outData = {
  timestamp: new Date().toISOString(),
  config: { handsPerMatchup, startStack, strategies: strategyKeys },
  pairwise: pairResults,
  rankings: rankings.map(r => ({ ...r, elo: Math.round(elo[r.key]), perHandProfits: undefined })),
};
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(outData, null, 2));
console.log(`  Results saved to ${outPath}`);

// Print stratified bot debug stats if available
if (stratifiedStrategy._stats) {
  const s = stratifiedStrategy._stats;
  console.log(`\n  [STRAT debug] Turn/River decisions: ${s.turnRiver}, Trap detections: ${s.trapDetect}, Turn barrels: ${s.barrel}`);
}
