#!/usr/bin/env node
"use strict";

/**
 * Backtest CFR-100 strategy against real PS hand histories.
 *
 * The CFR was trained HU (IP vs OOP) with abstracted bet sizes.
 * We map each real 6-max hand to the closest CFR info set by:
 *   1. Computing hero's strength bucket (100 buckets)
 *   2. Abstracting the action history to match training format
 *   3. Looking up CFR's recommended strategy
 *
 * Usage:
 *   node --max-old-space-size=4096 scripts/backtest-cfr100.js --all
 */

const fs = require("fs");
const path = require("path");
const { evaluateHandStrength, strengthToBucket } = require("./cfr/abstraction");

const HERO = "Skurj_poker";
const NUM_BUCKETS = 100;

// Load CFR-100 strategy
const stratPath = path.resolve("vision/models/cfr_strategy_sixmax_100bucket.json");
console.log(`Loading CFR-100 strategy...`);
const strategy = JSON.parse(fs.readFileSync(stratPath, "utf8"));
const allKeys = Object.keys(strategy);
console.log(`  ${allKeys.length} info sets loaded.\n`);

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

    const cards = dealtMatch[1].split(" ").map(parseCard);

    // Seat info
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

    // Parse board
    const boardCards = [];
    const flopMatch = hand.match(/\*\*\* FLOP \*\*\* \[(.+?)\]/);
    const turnMatch = hand.match(/\*\*\* TURN \*\*\* \[.+?\] \[(.+?)\]/);
    const riverMatch = hand.match(/\*\*\* RIVER \*\*\* \[.+?\] \[(.+?)\]/);
    if (flopMatch) boardCards.push(...flopMatch[1].split(" ").map(parseCard));
    if (turnMatch) boardCards.push(parseCard(turnMatch[1]));
    if (riverMatch) boardCards.push(parseCard(riverMatch[1]));

    // Parse all actions by street with amounts
    const streets = { PREFLOP: [], FLOP: [], TURN: [], RIVER: [] };
    let currentStreet = "PREFLOP";
    let currentPot = 0.15; // SB + BB at 5/10c
    const bb = 0.10;

    const lines = hand.split("\n");
    for (const line of lines) {
      if (line.includes("*** FLOP ***")) { currentStreet = "FLOP"; continue; }
      if (line.includes("*** TURN ***")) { currentStreet = "TURN"; continue; }
      if (line.includes("*** RIVER ***")) { currentStreet = "RIVER"; continue; }
      if (line.includes("*** SHOW DOWN ***") || line.includes("*** SUMMARY ***")) break;

      // Skip blinds posting
      if (/posts (small|big) blind/i.test(line)) continue;

      const actionMatch = line.match(/^(.+?): (folds|checks|calls|bets|raises)(?: \$?([\d.]+))?(?: to \$?([\d.]+))?/i);
      if (!actionMatch) continue;

      const player = actionMatch[1];
      const verb = actionMatch[2].toLowerCase();
      const amount = actionMatch[4] ? parseFloat(actionMatch[4]) :
                     actionMatch[3] ? parseFloat(actionMatch[3]) : 0;

      let action;
      if (verb === "folds") action = "FOLD";
      else if (verb === "checks") action = "CHECK";
      else if (verb === "calls") action = "CALL";
      else if (verb === "bets") action = "BET";
      else if (verb === "raises") action = "RAISE";

      if (action) {
        streets[currentStreet].push({
          player, action, amount, isHero: player === HERO,
        });
      }
    }

    // Result
    const potMatch = hand.match(/Total pot \$([\d.]+)/);
    const pot = potMatch ? parseFloat(potMatch[1]) : 0;
    const heroWon = hand.includes(HERO + " collected");
    const collectMatch = hand.match(new RegExp(HERO.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + " collected \\$([\\.\\d]+)"));
    const collected = collectMatch ? parseFloat(collectMatch[1]) : 0;

    // Calculate hero invested
    let invested = 0;
    if (hand.includes(HERO + ": posts small blind")) invested += 0.05;
    if (hand.includes(HERO + ": posts big blind")) invested += 0.10;
    for (const st of Object.values(streets)) {
      for (const act of st) {
        if (act.isHero && (act.action === "CALL" || act.action === "BET" || act.action === "RAISE")) {
          invested += act.amount;
        }
      }
    }

    hands.push({
      cards, position, isIP, boardCards, streets, pot, heroWon, collected, invested, bb, numPlayers,
    });
  }
  return hands;
}

// ── CFR Action History Builder ────────────────────────────────────────

function abstractBet(amount, potSize) {
  if (potSize <= 0) return "bh";
  const ratio = amount / potSize;
  if (ratio >= 1.5) return "ba";
  if (ratio >= 0.7) return "bp";
  return "bh";
}

function abstractRaise(amount, potSize) {
  if (potSize <= 0) return "rh";
  const ratio = amount / potSize;
  if (ratio >= 1.5) return "ra";
  if (ratio >= 0.7) return "rp";
  return "rh";
}

/**
 * Build a simplified action history from hero's perspective.
 * Maps 6-max actions to HU IP/OOP format that matches training.
 *
 * Key insight: the CFR was trained HU, so we collapse multiple opponents
 * into a single "villain" and track hero's interaction with them.
 */
function buildActionHistory(streetActions, heroIsIP) {
  let hist = "";
  let runningPot = 0.15; // start with blinds

  for (const act of streetActions) {
    if (act.action === "FOLD" && !act.isHero) continue; // skip other folds in 6-max

    let encoded;
    if (act.action === "FOLD") encoded = "f";
    else if (act.action === "CHECK") encoded = "k";
    else if (act.action === "CALL") { encoded = "c"; runningPot += act.amount; }
    else if (act.action === "BET") { encoded = abstractBet(act.amount, runningPot); runningPot += act.amount; }
    else if (act.action === "RAISE") { encoded = abstractRaise(act.amount, runningPot); runningPot += act.amount; }
    else encoded = "?";

    hist += encoded;
  }
  return { hist, runningPot };
}

// ── CFR Lookup with fuzzy matching ────────────────────────────────────

function lookupCFR(bucket, isIP, street, actionHistory) {
  const pos = isIP ? "IP" : "OOP";
  const streetName = street;

  // Exact match
  const key = `${streetName}:${bucket}:s0:${pos}:${actionHistory}`;
  if (strategy[key]) return { probs: strategy[key], key, exact: true };

  // Fuzzy: try nearby buckets
  for (let delta = 1; delta <= 10; delta++) {
    for (const d of [delta, -delta]) {
      const b = bucket + d;
      if (b < 0 || b >= NUM_BUCKETS) continue;
      const k = `${streetName}:${b}:s0:${pos}:${actionHistory}`;
      if (strategy[k]) return { probs: strategy[k], key: k, exact: false, bucketDelta: d };
    }
  }

  // Fuzzy: try truncating action history (drop last action)
  if (actionHistory.length > 0) {
    // Try removing last 1-2 chars
    for (let trim = 1; trim <= 3; trim++) {
      const shorter = actionHistory.slice(0, -trim);
      for (let delta = 0; delta <= 5; delta++) {
        for (const d of delta === 0 ? [0] : [delta, -delta]) {
          const b = bucket + d;
          if (b < 0 || b >= NUM_BUCKETS) continue;
          const k = `${streetName}:${b}:s0:${pos}:${shorter}`;
          if (strategy[k]) return { probs: strategy[k], key: k, exact: false, trimmed: true };
        }
      }
    }
  }

  return null;
}

// ── Main ──────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
let files;
if (args.includes("--all")) {
  files = [
    "hands/poker_stars/hands_001.txt",
    "hands/poker_stars/hands_003.txt",
    "hands/ps_session_20260404.txt",
  ];
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
console.log("=".repeat(80));
console.log("  CFR-100 BACKTEST vs Actual Play — PokerStars 10NL 6-max");
console.log("=".repeat(80));

let totalDecisions = 0;
let cfrHits = 0;
let exactHits = 0;
let agreements = 0;
let disagreements = 0;
const streetStats = { PREFLOP: { agree: 0, disagree: 0, hit: 0, miss: 0 },
                      FLOP: { agree: 0, disagree: 0, hit: 0, miss: 0 },
                      TURN: { agree: 0, disagree: 0, hit: 0, miss: 0 },
                      RIVER: { agree: 0, disagree: 0, hit: 0, miss: 0 } };
const bigDisagreements = [];
let evSaved = 0;
let evMissed = 0;

for (const hand of allHands) {
  const { cards, boardCards, streets, isIP, heroWon, collected, pot, invested, bb } = hand;
  const streetOrder = ["PREFLOP", "FLOP", "TURN", "RIVER"];

  let fullHistory = "";

  for (const street of streetOrder) {
    const streetActions = streets[street] || [];
    if (streetActions.length === 0) continue;

    const board = street === "PREFLOP" ? [] :
                  street === "FLOP" ? boardCards.slice(0, 3) :
                  street === "TURN" ? boardCards.slice(0, 4) :
                  boardCards.slice(0, 5);

    // Build action history UP TO hero's first action this street
    let preHeroHistory = fullHistory;
    let preHeroActions = "";
    let runningPot = pot; // approximate

    for (const act of streetActions) {
      if (act.isHero) {
        // LOOKUP CFR HERE — before hero acts
        totalDecisions++;
        const strength = evaluateHandStrength(cards, board, street);
        let bucket = strengthToBucket(strength, NUM_BUCKETS);
        if (isIP) bucket = Math.min(NUM_BUCKETS - 1, bucket + 5);

        const histForLookup = preHeroHistory + preHeroActions;
        const result = lookupCFR(bucket, isIP, street, histForLookup);

        if (result) {
          cfrHits++;
          if (result.exact) exactHits++;
          streetStats[street].hit++;

          const entries = Object.entries(result.probs).sort((a, b) => b[1] - a[1]);
          const topAction = entries[0][0];
          const topProb = entries[0][1];

          // Normalize for comparison
          let cfrNorm = topAction;
          if (["BET_HALF", "BET_POT", "BET_ALLIN"].includes(cfrNorm)) cfrNorm = "BET/RAISE";
          if (["RAISE_HALF", "RAISE_POT", "RAISE_ALLIN"].includes(cfrNorm)) cfrNorm = "BET/RAISE";
          let heroNorm = act.action;
          if (heroNorm === "BET" || heroNorm === "RAISE") heroNorm = "BET/RAISE";

          const agree = heroNorm === cfrNorm;
          if (agree) { agreements++; streetStats[street].agree++; }
          else {
            disagreements++; streetStats[street].disagree++;

            // Track money impact
            if ((cfrNorm === "FOLD") && heroNorm !== "FOLD" && !heroWon) {
              evSaved += invested;
            }
            if (cfrNorm === "BET/RAISE" && heroNorm === "FOLD") {
              evMissed++;
            }

            bigDisagreements.push({
              hand: cards.map(c => c.str).join(" "),
              board: board.map(c => c.str).join(" ") || "-",
              street, position: hand.position,
              heroAction: act.action, cfrAction: topAction, cfrProb: topProb,
              probs: entries.slice(0, 3).map(([a, p]) => `${a}:${(p * 100).toFixed(0)}%`).join(" "),
              strength: strength.toFixed(2), bucket,
              pot: pot.toFixed(2), heroWon, invested: invested.toFixed(2),
              exact: result.exact,
            });
          }
        } else {
          streetStats[street].miss++;
        }

        // Encode hero's actual action
        let enc;
        if (act.action === "FOLD") enc = "f";
        else if (act.action === "CHECK") enc = "k";
        else if (act.action === "CALL") enc = "c";
        else if (act.action === "BET") enc = abstractBet(act.amount, runningPot);
        else if (act.action === "RAISE") enc = abstractRaise(act.amount, runningPot);
        else enc = "?";
        preHeroActions += enc;

        break; // Only analyze hero's FIRST action per street for now
      } else {
        // Opponent action (skip folds, encode significant actions)
        if (act.action === "FOLD") continue;
        let enc;
        if (act.action === "CHECK") enc = "k";
        else if (act.action === "CALL") enc = "c";
        else if (act.action === "BET") enc = abstractBet(act.amount, runningPot);
        else if (act.action === "RAISE") enc = abstractRaise(act.amount, runningPot);
        else enc = "?";
        preHeroActions += enc;
      }
    }

    // Update full history for next street
    const { hist } = buildActionHistory(streetActions, isIP);
    fullHistory += (fullHistory && hist ? "-" : "") + hist;
  }
}

// ── Results ───────────────────────────────────────────────────────────

const totalHit = agreements + disagreements;

console.log(`\n  COVERAGE`);
console.log(`  Hero decisions:    ${totalDecisions}`);
console.log(`  CFR matched:       ${cfrHits} (${(cfrHits / totalDecisions * 100).toFixed(1)}%)`);
console.log(`    Exact match:     ${exactHits}`);
console.log(`    Fuzzy match:     ${cfrHits - exactHits}`);
console.log(`  No match:          ${totalDecisions - cfrHits} (${((totalDecisions - cfrHits) / totalDecisions * 100).toFixed(1)}%)`);

if (totalHit > 0) {
  console.log(`\n  AGREEMENT (where CFR had an opinion)`);
  console.log(`  Agree:     ${agreements}/${totalHit} (${(agreements / totalHit * 100).toFixed(1)}%)`);
  console.log(`  Disagree:  ${disagreements}/${totalHit} (${(disagreements / totalHit * 100).toFixed(1)}%)`);
}

console.log(`\n  BY STREET`);
for (const [street, s] of Object.entries(streetStats)) {
  const total = s.agree + s.disagree;
  if (total === 0 && s.hit === 0) continue;
  const pct = total > 0 ? (s.agree / total * 100).toFixed(1) : "N/A";
  console.log(`    ${street.padEnd(8)}: ${s.hit} hits, ${s.agree}/${total} agree (${pct}%), ${s.miss} no-match`);
}

console.log(`\n  EV IMPACT ESTIMATE`);
console.log(`  Potential saved (CFR says FOLD, you played and lost): $${evSaved.toFixed(2)}`);
console.log(`  Missed aggression (CFR says BET/RAISE, you FOLD):    ${evMissed} spots`);

if (bigDisagreements.length > 0) {
  // Sort by confidence
  bigDisagreements.sort((a, b) => b.cfrProb - a.cfrProb);

  console.log(`\n  TOP DISAGREEMENTS (${Math.min(bigDisagreements.length, 25)} of ${bigDisagreements.length}):`);
  console.log(`  ${"Hand".padEnd(7)} ${"Board".padEnd(16)} ${"St".padEnd(8)} ${"Pos".padEnd(4)} ${"You".padEnd(7)} ${"CFR".padEnd(14)} ${"Probs".padEnd(35)} ${"Str".padEnd(5)} ${"Won".padEnd(4)} ${"E?".padEnd(3)}`);
  console.log("  " + "-".repeat(105));

  for (const d of bigDisagreements.slice(0, 25)) {
    const wonStr = d.heroWon ? "W" : "L";
    const exactStr = d.exact ? "=" : "~";
    console.log(`  ${d.hand.padEnd(7)} ${d.board.padEnd(16)} ${d.street.padEnd(8)} ${d.position.padEnd(4)} ${d.heroAction.padEnd(7)} ${d.cfrAction.padEnd(14)} ${d.probs.padEnd(35)} ${d.strength.padEnd(5)} ${wonStr.padEnd(4)} ${exactStr}`);
  }
}

console.log("\n" + "=".repeat(80));
