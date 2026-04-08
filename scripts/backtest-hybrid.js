#!/usr/bin/env node
"use strict";

/**
 * Hybrid backtest: Preflop chart (6-max ranges) + CFR-100 (postflop).
 *
 * For each PS hand:
 *   1. Preflop: use the 6-max preflop chart to decide open/call/fold
 *   2. Postflop: use CFR-100 strategy for bet/raise/call/fold decisions
 *   3. Compare hybrid decisions vs actual play, estimate EV impact
 *
 * Usage:
 *   node --max-old-space-size=4096 scripts/backtest-hybrid.js --all
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const { evaluateHandStrength, strengthToBucket } = require("./cfr/abstraction");

const HERO = "Skurj_poker";
const NUM_BUCKETS = 100;
const BB = 0.10;
const PYTHON = "C:\\Users\\Simon\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";

// Load CFR-100 strategy
const stratPath = path.resolve("vision/models/cfr_strategy_sixmax_100bucket.json");
console.log(`Loading CFR-100 strategy...`);
const strategy = JSON.parse(fs.readFileSync(stratPath, "utf8"));
console.log(`  ${Object.keys(strategy).length} info sets loaded.`);

// ── Card parsing ──────────────────────────────────────────────────────

const RANK_MAP = { "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
                   "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14 };
const SUIT_MAP = { "c": 1, "d": 2, "h": 3, "s": 4 };

function parseCard(s) {
  return { rank: RANK_MAP[s[0]], suit: SUIT_MAP[s[1].toLowerCase()], str: s };
}

// ── Parse PS hands ────────────────────────────────────────────────────

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

    // Was hero facing a raise preflop?
    let facingRaise = false;
    for (const act of streets.PREFLOP) {
      if (act.isHero) break;
      if (act.action === "RAISE" || act.action === "BET") facingRaise = true;
    }

    // Hero's actual preflop action
    let heroPreflopAction = "FOLD";
    for (const act of streets.PREFLOP) {
      if (act.isHero) { heroPreflopAction = act.action; break; }
    }

    // Did hero see flop?
    const sawFlop = boardCards.length >= 3 && heroPreflopAction !== "FOLD";

    // Result
    const potMatch = hand.match(/Total pot \$([\d.]+)/);
    const pot = potMatch ? parseFloat(potMatch[1]) : 0;
    const heroWon = hand.includes(HERO + " collected");
    const collectMatch = hand.match(new RegExp(HERO.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + " collected \\$([\\.\\d]+)"));
    const collected = collectMatch ? parseFloat(collectMatch[1]) : 0;

    // Hero invested
    let invested = 0;
    if (hand.includes(HERO + ": posts small blind")) invested += 0.05;
    if (hand.includes(HERO + ": posts big blind")) invested += 0.10;
    for (const st of Object.values(streets)) {
      for (const a of st) {
        if (a.isHero && ["CALL", "BET", "RAISE"].includes(a.action)) invested += a.amount;
      }
    }

    const profit = heroWon ? collected - invested : -invested;

    hands.push({
      cardStrs, cards, position, isIP, boardCards, streets, facingRaise,
      heroPreflopAction, sawFlop, pot, heroWon, collected, invested, profit, numPlayers,
    });
  }
  return hands;
}

// ── Get preflop chart advice via Python ───────────────────────────────

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

  const tmpScript = path.join(__dirname, "_hybrid_tmp.py");
  fs.writeFileSync(tmpScript, pyScript);
  try {
    const output = execSync(`${PYTHON} ${tmpScript}`, {
      input: JSON.stringify(input), encoding: "utf8", timeout: 30000, cwd: process.cwd(),
    });
    const lines = output.trim().split("\n");
    const jsonLine = lines.find(l => l.trimStart().startsWith("["));
    return JSON.parse(jsonLine);
  } finally {
    try { fs.unlinkSync(tmpScript); } catch {}
  }
}

// ── CFR postflop lookup ───────────────────────────────────────────────

function abstractBet(amount, potSize) {
  if (potSize <= 0) return "bh";
  const ratio = amount / potSize;
  if (ratio >= 1.5) return "ba";
  if (ratio >= 0.7) return "bp";
  return "bh";
}

function lookupCFR(bucket, isIP, street, actionHistory) {
  const pos = isIP ? "IP" : "OOP";

  // Try multiple action history patterns since 6-max history won't exactly match HU training
  const historyVariants = [actionHistory];

  // Also try common preflop prefixes + current street actions
  // Training keys look like: FLOP:37:s0:OOP:cbhc-k  (preflop: cbhc, flop: k)
  // For postflop, try common preflop sequences prepended
  if (street !== "PREFLOP" && actionHistory.length <= 4) {
    const commonPreflopSeqs = ["c", "cbhc", "cbpc", "rpc", "rhc", "cbhrpc", "cbhrhc"];
    for (const pfx of commonPreflopSeqs) {
      historyVariants.push(`${pfx}-${actionHistory}`);
    }
    // Also try just the street-local actions with no preflop prefix
    // And try with empty street actions (first to act)
    if (actionHistory.length > 0) {
      historyVariants.push(actionHistory);
    }
  }

  for (const hist of historyVariants) {
    const key = `${street}:${bucket}:s0:${pos}:${hist}`;
    if (strategy[key]) return { probs: strategy[key], key, exact: hist === actionHistory };

    // Try nearby buckets
    for (let d = 1; d <= 10; d++) {
      for (const delta of [d, -d]) {
        const b = bucket + delta;
        if (b < 0 || b >= NUM_BUCKETS) continue;
        const k = `${street}:${b}:s0:${pos}:${hist}`;
        if (strategy[k]) return { probs: strategy[k], key: k, exact: false };
      }
    }
  }

  // Last resort: try truncating action history
  for (let trim = 1; trim <= Math.min(3, actionHistory.length); trim++) {
    const shorter = actionHistory.slice(0, -trim);
    const k = `${street}:${bucket}:s0:${pos}:${shorter}`;
    if (strategy[k]) return { probs: strategy[k], key: k, exact: false, trimmed: true };
    for (let d = 1; d <= 5; d++) {
      for (const delta of [d, -d]) {
        const b = bucket + delta;
        if (b < 0 || b >= NUM_BUCKETS) continue;
        const kk = `${street}:${b}:s0:${pos}:${shorter}`;
        if (strategy[kk]) return { probs: strategy[kk], key: kk, exact: false, trimmed: true };
      }
    }
  }

  return null;
}

function getCFRAction(cards, board, isIP, street, actionHistory) {
  const strength = evaluateHandStrength(cards, board, street);
  let bucket = strengthToBucket(strength, NUM_BUCKETS);
  if (isIP) bucket = Math.min(NUM_BUCKETS - 1, bucket + 5);

  const result = lookupCFR(bucket, isIP, street, actionHistory);
  if (!result) return null;

  const entries = Object.entries(result.probs).sort((a, b) => b[1] - a[1]);
  const topAction = entries[0][0];
  const topProb = entries[0][1];

  // Normalize
  let norm = topAction;
  if (["BET_HALF", "BET_POT", "BET_ALLIN"].includes(norm)) norm = "BET";
  if (["RAISE_HALF", "RAISE_POT", "RAISE_ALLIN"].includes(norm)) norm = "RAISE";

  return {
    action: topAction, normalized: norm, prob: topProb, strength, bucket,
    probs: entries.slice(0, 3).map(([a, p]) => `${a}:${(p * 100).toFixed(0)}%`).join(" "),
    exact: result.exact,
  };
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

// Get preflop chart advice
console.log("  Getting preflop chart advice...");
const pfAdvice = getPreflopAdvice(allHands);

console.log("=".repeat(80));
console.log("  HYBRID BACKTEST — Preflop Chart + CFR-100 Postflop");
console.log("  vs Actual Play — PokerStars 10NL 6-max");
console.log("=".repeat(80));

// ── Stats tracking ────────────────────────────────────────────────────

let actualProfit = 0;
let hybridProfit = 0;
let preflopAgree = 0, preflopDisagree = 0;
let postflopDecisions = 0, postflopHits = 0, postflopAgree = 0, postflopDisagree = 0;
const preflopDiffs = [];
const postflopDiffs = [];

// Hands where hybrid would have folded but hero played (and lost)
let savedByFolding = 0;
let savedCount = 0;
// Hands where hybrid would have played but hero folded
let missedByFolding = 0;
let missedCount = 0;

for (let i = 0; i < allHands.length; i++) {
  const hand = allHands[i];
  const pf = pfAdvice[i];
  const heroAction = hand.heroPreflopAction;
  const chartAction = pf.action;

  actualProfit += hand.profit;

  // ── Preflop comparison ──────────────────────────────────────────

  // Normalize for comparison
  const heroNorm = heroAction === "CHECK" ? "CALL" : heroAction; // BB check = call
  const chartNorm = chartAction === "CHECK" ? "CALL" : chartAction;

  let pfAgree = heroNorm === chartNorm ||
    (heroNorm === "RAISE" && chartNorm === "RAISE") ||
    (heroNorm === "CALL" && chartNorm === "CALL");

  // Both play = agree for hybrid purposes
  const heroPlays = heroNorm !== "FOLD";
  const chartPlays = chartNorm !== "FOLD";

  if (heroPlays === chartPlays) {
    preflopAgree++;
  } else {
    preflopDisagree++;

    if (!chartPlays && heroPlays && !hand.heroWon) {
      // Chart says fold, hero played and lost
      savedByFolding += hand.invested;
      savedCount++;
      preflopDiffs.push({
        hand: pf.hand_key, pos: hand.position,
        hero: heroAction, chart: chartAction,
        profit: hand.profit, note: `SAVE $${hand.invested.toFixed(2)}`,
      });
    } else if (!chartPlays && heroPlays && hand.heroWon) {
      // Chart says fold, but hero played and won — chart would have missed this
      missedByFolding += hand.profit;
      missedCount++;
      preflopDiffs.push({
        hand: pf.hand_key, pos: hand.position,
        hero: heroAction, chart: chartAction,
        profit: hand.profit, note: `MISS +$${hand.profit.toFixed(2)}`,
      });
    } else if (chartPlays && !heroPlays) {
      // Chart says play, hero folded — missed opportunity
      preflopDiffs.push({
        hand: pf.hand_key, pos: hand.position,
        hero: heroAction, chart: chartAction,
        profit: hand.profit, note: "OPEN?",
      });
    }
  }

  // ── Postflop CFR analysis (only for hands that see a flop) ─────

  if (!hand.sawFlop || hand.boardCards.length < 3) continue;

  const streetOrder = ["FLOP", "TURN", "RIVER"];
  for (const street of streetOrder) {
    const streetActions = hand.streets[street] || [];
    if (streetActions.length === 0) continue;

    const board = street === "FLOP" ? hand.boardCards.slice(0, 3) :
                  street === "TURN" ? hand.boardCards.slice(0, 4) :
                  hand.boardCards.slice(0, 5);

    // Build action history up to hero's decision
    let hist = "";
    for (const act of streetActions) {
      if (act.isHero) {
        postflopDecisions++;
        const cfr = getCFRAction(hand.cards, board, hand.isIP, street, hist);
        if (cfr) {
          postflopHits++;
          const heroNormPost = act.action === "BET" || act.action === "RAISE" ? "AGG" :
                               act.action === "CALL" ? "CALL" :
                               act.action === "CHECK" ? "PASSIVE" : "FOLD";
          const cfrNormPost = cfr.normalized === "BET" || cfr.normalized === "RAISE" ? "AGG" :
                              cfr.normalized === "CALL" ? "CALL" :
                              cfr.normalized === "CHECK" ? "PASSIVE" : "FOLD";

          if (heroNormPost === cfrNormPost) {
            postflopAgree++;
          } else {
            postflopDisagree++;
            postflopDiffs.push({
              hand: hand.cardStrs.join(" "), board: board.map(c => c.str).join(" "),
              street, pos: hand.position,
              hero: act.action, cfr: cfr.action, prob: cfr.prob,
              probs: cfr.probs, strength: cfr.strength, won: hand.heroWon,
            });
          }
        }
        break; // first hero action per street
      } else {
        // Opponent action
        if (act.action === "FOLD") continue;
        let enc = act.action === "CHECK" ? "k" : act.action === "CALL" ? "c" :
                  act.action === "BET" ? abstractBet(act.amount, hand.pot) :
                  act.action === "RAISE" ? "r" + abstractBet(act.amount, hand.pot).slice(1) : "?";
        hist += enc;
      }
    }
  }
}

// ── Estimate hybrid profit ────────────────────────────────────────────

// For hands where chart agrees with hero: same profit
// For hands where chart says fold but hero played: would have saved invested (if lost) or missed win
let hybridDelta = savedByFolding - missedByFolding;

console.log(`\n  ═══ PREFLOP: Chart vs Actual ═══`);
console.log(`  Agree (both play or both fold): ${preflopAgree}/${allHands.length} (${(preflopAgree / allHands.length * 100).toFixed(1)}%)`);
console.log(`  Disagree:                       ${preflopDisagree}/${allHands.length} (${(preflopDisagree / allHands.length * 100).toFixed(1)}%)`);
console.log();
console.log(`  Chart saves (fold where you played & lost):  ${savedCount} hands, $${savedByFolding.toFixed(2)}`);
console.log(`  Chart misses (fold where you played & won):  ${missedCount} hands, $${missedByFolding.toFixed(2)}`);
console.log(`  Net preflop EV delta:                        ${hybridDelta >= 0 ? "+" : ""}$${hybridDelta.toFixed(2)}`);

if (preflopDiffs.length > 0) {
  console.log(`\n  Preflop differences:`);
  console.log(`  ${"Hand".padEnd(5)} ${"Pos".padEnd(4)} ${"You".padEnd(6)} ${"Chart".padEnd(6)} ${"P&L".padStart(8)} ${"Impact"}`);
  console.log("  " + "-".repeat(55));
  for (const d of preflopDiffs.slice(0, 25)) {
    const pnl = d.profit >= 0 ? `+$${d.profit.toFixed(2)}` : `-$${Math.abs(d.profit).toFixed(2)}`;
    console.log(`  ${d.hand.padEnd(5)} ${d.pos.padEnd(4)} ${d.hero.padEnd(6)} ${d.chart.padEnd(6)} ${pnl.padStart(8)} ${d.note}`);
  }
  if (preflopDiffs.length > 25) console.log(`  ... and ${preflopDiffs.length - 25} more`);
}

console.log(`\n  ═══ POSTFLOP: CFR-100 vs Actual ═══`);
console.log(`  Decisions:  ${postflopDecisions}`);
console.log(`  CFR match:  ${postflopHits} (${postflopDecisions > 0 ? (postflopHits / postflopDecisions * 100).toFixed(1) : 0}%)`);
if (postflopHits > 0) {
  const postTotal = postflopAgree + postflopDisagree;
  console.log(`  Agree:      ${postflopAgree}/${postTotal} (${(postflopAgree / postTotal * 100).toFixed(1)}%)`);
  console.log(`  Disagree:   ${postflopDisagree}/${postTotal} (${(postflopDisagree / postTotal * 100).toFixed(1)}%)`);
}

if (postflopDiffs.length > 0) {
  console.log(`\n  Postflop disagreements:`);
  console.log(`  ${"Hand".padEnd(7)} ${"Board".padEnd(16)} ${"St".padEnd(6)} ${"Pos".padEnd(4)} ${"You".padEnd(7)} ${"CFR".padEnd(14)} ${"Probs".padEnd(32)} ${"Str".padEnd(5)} ${"W?"}`);
  console.log("  " + "-".repeat(105));
  postflopDiffs.sort((a, b) => b.prob - a.prob);
  for (const d of postflopDiffs.slice(0, 20)) {
    console.log(`  ${d.hand.padEnd(7)} ${d.board.padEnd(16)} ${d.street.padEnd(6)} ${d.pos.padEnd(4)} ${d.hero.padEnd(7)} ${d.cfr.padEnd(14)} ${d.probs.padEnd(32)} ${d.strength.toFixed(2).padEnd(5)} ${d.won ? "W" : "L"}`);
  }
}

console.log(`\n  ═══ SUMMARY ═══`);
console.log(`  Actual P&L over ${allHands.length} hands:     ${actualProfit >= 0 ? "+" : ""}$${actualProfit.toFixed(2)} (${(actualProfit / BB / (allHands.length / 100)).toFixed(1)} bb/100)`);
console.log(`  Preflop chart EV delta:          ${hybridDelta >= 0 ? "+" : ""}$${hybridDelta.toFixed(2)} (${(hybridDelta / BB / (allHands.length / 100)).toFixed(1)} bb/100)`);
console.log(`  Estimated hybrid P&L:            ${(actualProfit + hybridDelta) >= 0 ? "+" : ""}$${(actualProfit + hybridDelta).toFixed(2)} (${((actualProfit + hybridDelta) / BB / (allHands.length / 100)).toFixed(1)} bb/100)`);

console.log("\n" + "=".repeat(80));
