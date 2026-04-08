#!/usr/bin/env node
"use strict";

/**
 * Backtest the stratified architecture against real PS hand histories.
 *
 * Strategy:
 *   - PREFLOP: deterministic chart (preflop_chart.py)
 *   - FLOP:    flop-only CFR (50-bucket, mmap binary)
 *   - TURN/RIVER: equity + opponent-adjusted rules
 *
 * Compares stratified decisions vs actual play across all streets.
 *
 * Usage:
 *   node --max-old-space-size=4096 scripts/backtest-stratified.js --all
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const { evaluateHandStrength, strengthToBucket } = require("./cfr/abstraction");

const HERO = "Skurj_poker";
const NUM_BUCKETS = 50;
const BB = 0.10;
const PYTHON = "C:\\Users\\Simon\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";

// Load flop CFR strategy
const flopStratPath = path.resolve("vision/models/cfr_strategy_flop.json");
console.log("Loading flop CFR strategy...");
const flopStrategy = JSON.parse(fs.readFileSync(flopStratPath, "utf8"));
console.log(`  ${Object.keys(flopStrategy).length} flop info sets loaded.`);

// ── Card parsing ──────────────────────────────────────────────────────

const RANK_MAP = { "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
                   "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14 };
const SUIT_MAP = { "c": 1, "d": 2, "h": 3, "s": 4 };

function parseCard(s) {
  return { rank: RANK_MAP[s[0]], suit: SUIT_MAP[s[1].toLowerCase()], str: s };
}

// ── PS Hand Parser ────────────────────────────────────────────────────

function parseHands(text) {
  const rawHands = text.split(/\*{5,}\s*#\s*\d+\s*\*{5,}/).filter(h => h.includes("PokerStars Hand"));
  const hands = [];

  for (const hand of rawHands) {
    if (!hand.includes(HERO)) continue;
    const dealtMatch = hand.match(/Dealt to Skurj_poker \[(.+?)\]/);
    if (!dealtMatch) continue;

    const cardStrs = dealtMatch[1].split(" ");
    const cards = cardStrs.map(parseCard);

    const btnMatch = hand.match(/Seat #(\d+) is the button/);
    const heroSeatMatch = hand.match(/Seat (\d+): Skurj_poker/);
    const allSeats = [];
    const seatRegex = /Seat (\d+):/g;
    let m;
    while ((m = seatRegex.exec(hand)) !== null) allSeats.push(parseInt(m[1]));
    allSeats.sort((a, b) => a - b);
    const numPlayers = allSeats.length;

    let position = "BTN";
    if (btnMatch && heroSeatMatch) {
      const btn = parseInt(btnMatch[1]);
      const heroSeat = parseInt(heroSeatMatch[1]);
      const btnIdx = allSeats.indexOf(btn);
      const heroIdx = allSeats.indexOf(heroSeat);
      const dist = (heroIdx - btnIdx + numPlayers) % numPlayers;
      if (dist === 0) position = "BTN";
      else if (dist === 1) position = "SB";
      else if (dist === 2) position = "BB";
      else if (dist === numPlayers - 1) position = "CO";
      else if (dist === numPlayers - 2) position = "MP";
      else position = "EP";
    }
    const isIP = ["BTN", "CO"].includes(position);

    // Board
    const boardCards = [];
    const flopMatch = hand.match(/\*\*\* FLOP \*\*\* \[(.+?)\]/);
    const turnMatch = hand.match(/\*\*\* TURN \*\*\* \[.+?\] \[(.+?)\]/);
    const riverMatch = hand.match(/\*\*\* RIVER \*\*\* \[.+?\] \[(.+?)\]/);
    if (flopMatch) boardCards.push(...flopMatch[1].split(" ").map(parseCard));
    if (turnMatch) boardCards.push(parseCard(turnMatch[1]));
    if (riverMatch) boardCards.push(parseCard(riverMatch[1]));

    // Actions by street
    const streets = { PREFLOP: [], FLOP: [], TURN: [], RIVER: [] };
    let currentStreet = "PREFLOP";
    const lines = hand.split("\n");
    for (const line of lines) {
      if (line.includes("*** FLOP ***")) { currentStreet = "FLOP"; continue; }
      if (line.includes("*** TURN ***")) { currentStreet = "TURN"; continue; }
      if (line.includes("*** RIVER ***")) { currentStreet = "RIVER"; continue; }
      if (line.includes("*** SHOW DOWN ***") || line.includes("*** SUMMARY ***")) break;
      if (/posts (small|big) blind/i.test(line)) continue;

      const am = line.match(/^(.+?): (folds|checks|calls|bets|raises)(?: \$?([\d.]+))?(?: to \$?([\d.]+))?/i);
      if (!am) continue;
      const verb = am[2].toLowerCase();
      const amount = am[4] ? parseFloat(am[4]) : (am[3] ? parseFloat(am[3]) : 0);
      let action = verb === "folds" ? "FOLD" : verb === "checks" ? "CHECK" :
                   verb === "calls" ? "CALL" : verb === "bets" ? "BET" : "RAISE";
      streets[currentStreet].push({ player: am[1], action, amount, isHero: am[1] === HERO });
    }

    // Facing raise preflop?
    let facingRaise = false;
    for (const act of streets.PREFLOP) {
      if (act.isHero) break;
      if (act.action === "RAISE" || act.action === "BET") facingRaise = true;
    }

    let heroPreflopAction = "FOLD";
    for (const act of streets.PREFLOP) {
      if (act.isHero) { heroPreflopAction = act.action; break; }
    }

    const sawFlop = boardCards.length >= 3 && heroPreflopAction !== "FOLD";

    // Result
    const potMatch = hand.match(/Total pot \$([\d.]+)/);
    const pot = potMatch ? parseFloat(potMatch[1]) : 0;
    const heroWon = hand.includes(HERO + " collected");
    const collectMatch = hand.match(new RegExp(HERO.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + " collected \\$([\\.\\d]+)"));
    const collected = collectMatch ? parseFloat(collectMatch[1]) : 0;

    let invested = 0;
    if (hand.includes(HERO + ": posts small blind")) invested += 0.05;
    if (hand.includes(HERO + ": posts big blind")) invested += 0.10;
    for (const st of Object.values(streets)) {
      for (const a of st) {
        if (a.isHero && ["CALL", "BET", "RAISE"].includes(a.action)) invested += a.amount;
      }
    }

    const profit = heroWon ? collected - invested : -invested;

    // Pot at each street start (approximate)
    const bbMatch = hand.match(/\$([\d.]+)\/\$([\d.]+)/);
    const bbVal = bbMatch ? parseFloat(bbMatch[2]) : 0.10;

    hands.push({
      cardStrs, cards, position, isIP, boardCards, streets, facingRaise,
      heroPreflopAction, sawFlop, pot, heroWon, collected, invested, profit, numPlayers, bbVal,
    });
  }
  return hands;
}

// ── Preflop chart via Python ──────────────────────────────────────────

function getPreflopAdvice(hands) {
  const input = hands.map(h => ({
    c1: h.cardStrs[0], c2: h.cardStrs[1], pos: h.position, facing: h.facingRaise,
  }));
  const pyScript = `
import sys, json
sys.path.insert(0, "vision")
from preflop_chart import preflop_advice
hands = json.loads(sys.stdin.read())
results = []
for h in hands:
    pf = preflop_advice(h["c1"], h["c2"], h["pos"], facing_raise=h["facing"])
    results.append({"action": pf["action"], "hand_key": pf["hand_key"], "note": pf.get("note","")})
print(json.dumps(results))
`;
  const tmpScript = path.join(__dirname, "_strat_tmp.py");
  fs.writeFileSync(tmpScript, pyScript);
  try {
    const output = execSync(`${PYTHON} ${tmpScript}`, {
      input: JSON.stringify(input), encoding: "utf8", timeout: 30000, cwd: process.cwd(),
    });
    const jsonLine = output.trim().split("\n").find(l => l.trimStart().startsWith("["));
    return JSON.parse(jsonLine);
  } finally {
    try { fs.unlinkSync(tmpScript); } catch {}
  }
}

// ── Flop CFR lookup ───────────────────────────────────────────────────

function flopActionEncode(action, amount, pot) {
  if (action === "FOLD") return "f";
  if (action === "CHECK") return "k";
  if (action === "CALL") return "c";
  if (action === "BET" || action === "RAISE") {
    const ratio = pot > 0 ? amount / pot : 0.5;
    if (ratio >= 0.85) return "bp";
    if (ratio >= 0.5) return "bs";
    return "bt";
  }
  return "?";
}

function lookupFlopCFR(bucket, stackBucket, pos, potClass, history) {
  // Exact
  let key = `FLOP:${bucket}:s${stackBucket}:${pos}:${potClass}:${history}`;
  if (flopStrategy[key]) return { probs: flopStrategy[key], key, exact: true };

  // Nearby buckets
  for (let d = 1; d <= 5; d++) {
    for (const delta of [d, -d]) {
      const b = bucket + delta;
      if (b < 0 || b >= NUM_BUCKETS) continue;
      const k = `FLOP:${b}:s${stackBucket}:${pos}:${potClass}:${history}`;
      if (flopStrategy[k]) return { probs: flopStrategy[k], key: k, exact: false };
    }
  }

  // Try alternate pot classes
  const altPots = ["SRP", "3BP", "LP"].filter(p => p !== potClass);
  for (const alt of altPots) {
    const k = `FLOP:${bucket}:s${stackBucket}:${pos}:${alt}:${history}`;
    if (flopStrategy[k]) return { probs: flopStrategy[k], key: k, exact: false };
    for (let d = 1; d <= 3; d++) {
      for (const delta of [d, -d]) {
        const b = bucket + delta;
        if (b < 0 || b >= NUM_BUCKETS) continue;
        const kk = `FLOP:${b}:s${stackBucket}:${pos}:${alt}:${history}`;
        if (flopStrategy[kk]) return { probs: flopStrategy[kk], key: kk, exact: false };
      }
    }
  }

  return null;
}

function getCFRTopAction(probs) {
  // Aggregate
  let fold = probs.FOLD || 0;
  let check = probs.CHECK || 0;
  let call = probs.CALL || 0;
  let agg = (probs.BET_33 || 0) + (probs.BET_66 || 0) + (probs.BET_POT || 0) +
            (probs.BET_ALLIN || 0) + (probs.BET_HALF || 0) +
            (probs.RAISE_HALF || 0) + (probs.RAISE_POT || 0) + (probs.RAISE_ALLIN || 0);

  const total = fold + check + call + agg;
  if (total <= 0) return { action: "CHECK", prob: 0 };

  fold /= total; check /= total; call /= total; agg /= total;

  const options = { FOLD: fold, CHECK: check, CALL: call, "BET/RAISE": agg };
  let best = "CHECK", bestP = 0;
  for (const [a, p] of Object.entries(options)) {
    if (p > bestP) { best = a; bestP = p; }
  }
  return { action: best, prob: bestP, probs: { fold, check, call, agg } };
}

// ── Turn/River rules ──────────────────────────────────────────────────

function turnRiverDecision(equity, facingBet, callAmount, pot) {
  if (!facingBet) {
    if (equity >= 0.70) return "BET/RAISE";
    return "CHECK";
  }
  const potOdds = callAmount / (pot + callAmount);
  if (equity > 0.85) return "BET/RAISE";
  if (equity > potOdds || equity > 0.35) return "CALL";
  return "FOLD";
}

// ── Main ──────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
let files;
if (args.includes("--all")) {
  files = ["hands/poker_stars/hands_001.txt", "hands/poker_stars/hands_003.txt", "hands/ps_session_20260404.txt"];
} else {
  files = [args[0] || "hands/ps_session_20260404.txt"];
}

let allHands = [];
for (const f of files) {
  const text = fs.readFileSync(f, "utf8");
  const hands = parseHands(text);
  console.log(`  ${f}: ${hands.length} hero hands`);
  allHands.push(...hands);
}

console.log(`\n  Total: ${allHands.length} hands\n`);

console.log("  Getting preflop chart advice...");
const pfAdvice = getPreflopAdvice(allHands);

console.log("=" .repeat(85));
console.log("  STRATIFIED BACKTEST — Chart (preflop) + Flop CFR + Turn/River Rules");
console.log("  vs Actual Play — PokerStars 10NL 6-max");
console.log("=".repeat(85));

// ── Stats ─────────────────────────────────────────────────────────────

let actualProfit = 0;
const streetStats = {
  PREFLOP: { agree: 0, disagree: 0, saved: 0, missed: 0, savedCount: 0, missedCount: 0 },
  FLOP: { agree: 0, disagree: 0, hit: 0, miss: 0, details: [] },
  TURN: { agree: 0, disagree: 0, details: [] },
  RIVER: { agree: 0, disagree: 0, details: [] },
};

for (let i = 0; i < allHands.length; i++) {
  const hand = allHands[i];
  const pf = pfAdvice[i];
  actualProfit += hand.profit;

  // ── PREFLOP ─────────────────────────────────────────────────────
  const heroPlays = hand.heroPreflopAction !== "FOLD";
  const chartPlays = pf.action !== "FOLD";

  if (heroPlays === chartPlays) {
    streetStats.PREFLOP.agree++;
  } else {
    streetStats.PREFLOP.disagree++;
    if (!chartPlays && heroPlays && !hand.heroWon) {
      streetStats.PREFLOP.saved += hand.invested;
      streetStats.PREFLOP.savedCount++;
    } else if (!chartPlays && heroPlays && hand.heroWon) {
      streetStats.PREFLOP.missed += hand.profit;
      streetStats.PREFLOP.missedCount++;
    }
  }

  // ── POSTFLOP ────────────────────────────────────────────────────
  if (!hand.sawFlop) continue;

  for (const street of ["FLOP", "TURN", "RIVER"]) {
    const streetActions = hand.streets[street] || [];
    if (streetActions.length === 0) continue;

    const board = street === "FLOP" ? hand.boardCards.slice(0, 3) :
                  street === "TURN" ? hand.boardCards.slice(0, 4) :
                  hand.boardCards.slice(0, 5);

    // Build action history up to hero's action
    let hist = "";
    let potEstimate = hand.pot * 100; // rough, in dollars

    for (const act of streetActions) {
      if (act.isHero) {
        const strength = evaluateHandStrength(hand.cards, board, street);
        const bucket = strengthToBucket(strength, NUM_BUCKETS);
        const pos = hand.isIP ? "IP" : "OOP";
        const stackBB = 100; // approximate
        const sb = stackBB < 30 ? 0 : stackBB < 80 ? 1 : 2;

        let stratAction, stratProb, source;
        const heroNorm = (act.action === "BET" || act.action === "RAISE") ? "BET/RAISE" :
                         act.action === "CHECK" ? "CHECK" :
                         act.action === "CALL" ? "CALL" : "FOLD";

        if (street === "FLOP") {
          // Determine pot class
          const potBB = hand.pot / hand.bbVal;
          const potClass = potBB >= 15 ? "3BP" : potBB <= 3 ? "LP" : "SRP";

          const result = lookupFlopCFR(bucket, sb, pos, potClass, hist);
          if (result) {
            streetStats.FLOP.hit++;
            const top = getCFRTopAction(result.probs);
            stratAction = top.action;
            stratProb = top.prob;
            source = result.exact ? "cfr=" : "cfr~";
          } else {
            streetStats.FLOP.miss++;
            // Fall back to equity rules
            const facing = act.action !== "CHECK" && hist.includes("b");
            stratAction = turnRiverDecision(strength, facing, act.amount, hand.pot);
            source = "rules";
          }
        } else {
          // Turn/River: equity rules
          const facing = streetActions.some(a => !a.isHero && (a.action === "BET" || a.action === "RAISE"));
          const callAmt = act.amount || 0;
          stratAction = turnRiverDecision(strength, facing, callAmt, hand.pot);
          source = "rules";
        }

        // Compare
        const agree = heroNorm === stratAction;
        if (agree) {
          streetStats[street].agree++;
        } else {
          streetStats[street].disagree++;
          streetStats[street].details.push({
            hand: hand.cardStrs.join(" "),
            board: board.map(c => c.str).join(" "),
            pos: hand.position,
            hero: act.action,
            strat: stratAction,
            prob: stratProb,
            strength: strength.toFixed(2),
            won: hand.heroWon,
            source,
          });
        }
        break; // first hero action per street
      } else {
        // Encode opponent action for flop history
        if (act.action !== "FOLD") {
          hist += flopActionEncode(act.action, act.amount, hand.pot);
        }
      }
    }
  }
}

// ── Results ───────────────────────────────────────────────────────────

const pf = streetStats.PREFLOP;
const pfTotal = pf.agree + pf.disagree;
const pfDelta = pf.saved - pf.missed;

console.log(`\n  ═══ PREFLOP: Chart vs Actual ═══`);
console.log(`  Agree: ${pf.agree}/${pfTotal} (${(pf.agree / pfTotal * 100).toFixed(1)}%)`);
console.log(`  Saves: ${pf.savedCount} hands, $${pf.saved.toFixed(2)}`);
console.log(`  Misses: ${pf.missedCount} hands, $${pf.missed.toFixed(2)}`);
console.log(`  Net delta: ${pfDelta >= 0 ? "+" : ""}$${pfDelta.toFixed(2)} (${(pfDelta / BB / (allHands.length / 100)).toFixed(1)} bb/100)`);

for (const street of ["FLOP", "TURN", "RIVER"]) {
  const s = streetStats[street];
  const total = s.agree + s.disagree;
  if (total === 0) continue;

  console.log(`\n  ═══ ${street}: ${street === "FLOP" ? "Flop CFR" : "Rules"} vs Actual ═══`);
  console.log(`  Agree: ${s.agree}/${total} (${(s.agree / total * 100).toFixed(1)}%)`);
  if (s.hit !== undefined) {
    console.log(`  CFR hits: ${s.hit}, misses: ${s.miss} (${s.hit + s.miss > 0 ? (s.hit / (s.hit + s.miss) * 100).toFixed(1) : 0}% coverage)`);
  }

  if (s.details.length > 0) {
    console.log(`\n  Disagreements (${Math.min(s.details.length, 15)} of ${s.details.length}):`);
    console.log(`  ${"Hand".padEnd(7)} ${"Board".padEnd(16)} ${"Pos".padEnd(4)} ${"You".padEnd(10)} ${"Strat".padEnd(10)} ${"Str".padEnd(5)} ${"Src".padEnd(6)} ${"W?"}`);
    console.log("  " + "-".repeat(70));
    const sorted = s.details.sort((a, b) => (b.prob || 0) - (a.prob || 0));
    for (const d of sorted.slice(0, 15)) {
      console.log(`  ${d.hand.padEnd(7)} ${d.board.padEnd(16)} ${d.pos.padEnd(4)} ${d.hero.padEnd(10)} ${d.strat.padEnd(10)} ${d.strength.padEnd(5)} ${(d.source || "").padEnd(6)} ${d.won ? "W" : "L"}`);
    }
  }
}

// ── Summary ───────────────────────────────────────────────────────────

const totalPostflop = ["FLOP", "TURN", "RIVER"].reduce((sum, st) => {
  const s = streetStats[st];
  return sum + s.agree + s.disagree;
}, 0);
const totalPostflopAgree = ["FLOP", "TURN", "RIVER"].reduce((sum, st) => sum + streetStats[st].agree, 0);

console.log(`\n  ═══ OVERALL SUMMARY ═══`);
console.log(`  Actual P&L: ${actualProfit >= 0 ? "+" : ""}$${actualProfit.toFixed(2)} (${(actualProfit / BB / (allHands.length / 100)).toFixed(1)} bb/100)`);
console.log(`  Preflop chart delta: ${pfDelta >= 0 ? "+" : ""}$${pfDelta.toFixed(2)} (${(pfDelta / BB / (allHands.length / 100)).toFixed(1)} bb/100)`);
console.log(`  Preflop agreement: ${(pf.agree / pfTotal * 100).toFixed(1)}%`);
if (totalPostflop > 0) {
  console.log(`  Postflop agreement: ${(totalPostflopAgree / totalPostflop * 100).toFixed(1)}% (${totalPostflopAgree}/${totalPostflop})`);
}
console.log(`  Estimated improvement: ${pfDelta >= 0 ? "+" : ""}$${pfDelta.toFixed(2)} preflop + postflop quality gains`);
console.log("\n" + "=".repeat(85));
