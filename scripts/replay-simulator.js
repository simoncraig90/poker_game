#!/usr/bin/env node
"use strict";

/**
 * Replay PS hand histories with the stratified bot making hero's decisions.
 *
 * For each hand, replays the exact opponent actions from the real hand.
 * At each hero decision point, the bot decides instead. Then we compute
 * what the P&L would have been:
 *
 *   - If bot makes the same action as hero: same outcome
 *   - If bot folds earlier: saves whatever hero invested after that point
 *   - If bot calls where hero folded: we don't know the outcome (mark as unknown)
 *   - If bot raises where hero called: assume same outcome but with more money in pot
 *
 * This gives a lower-bound estimate of improvement (folding saves are certain,
 * extra value from aggression is estimated).
 *
 * Usage:
 *   node scripts/replay-simulator.js --all
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const { evaluateHandStrength, strengthToBucket } = require("./cfr/abstraction");

const HERO = "Skurj_poker";
const PYTHON = "C:\\Users\\Simon\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";

// Load flop CFR
const flopStratPath = path.resolve("vision/models/cfr_strategy_flop.json");
console.log("Loading flop CFR...");
const flopCFR = JSON.parse(fs.readFileSync(flopStratPath, "utf8"));
console.log(`  ${Object.keys(flopCFR).length} info sets.`);

// ── Card / hand parsing (same as backtest) ────────────────────────────

const RANK_MAP = { "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
                   "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14 };
const SUIT_MAP = { "c": 1, "d": 2, "h": 3, "s": 4 };
function parseCard(s) { return { rank: RANK_MAP[s[0]], suit: SUIT_MAP[s[1].toLowerCase()], str: s }; }

// Preflop ranges (JS version)
const OPEN_RANGES = {
  EP: new Set(["AA","KK","QQ","JJ","TT","99","AKs","AQs","AJs","ATs","KQs","KJs","AKo","AQo"]),
  MP: new Set(["AA","KK","QQ","JJ","TT","99","88","77","AKs","AQs","AJs","ATs","A9s","A8s","KQs","KJs","KTs","QJs","QTs","JTs","AKo","AQo","AJo","KQo"]),
  CO: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","QJs","QTs","Q9s","JTs","J9s","T9s","98s","87s","76s","AKo","AQo","AJo","ATo","KQo","KJo","QJo"]),
  BTN: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","44","33","22","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","K8s","K7s","K6s","K5s","QJs","QTs","Q9s","Q8s","JTs","J9s","J8s","T9s","T8s","98s","97s","87s","86s","76s","75s","65s","54s","AKo","AQo","AJo","ATo","A9o","A8o","A7o","A6o","A5o","KQo","KJo","KTo","QJo","QTo","JTo"]),
  SB: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","44","33","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","K8s","QJs","QTs","Q9s","Q8s","JTs","J9s","J8s","T9s","T8s","98s","97s","87s","86s","76s","75s","AKo","AQo","AJo","ATo","A9o","KQo","KJo","KTo"]),
  BB: new Set(["AA","KK","QQ","JJ","TT","99","88","77","66","55","44","33","22","AKs","AQs","AJs","ATs","A9s","A8s","A7s","A6s","A5s","A4s","A3s","A2s","KQs","KJs","KTs","K9s","K8s","QJs","QTs","Q9s","JTs","J9s","T9s","T8s","98s","97s","87s","86s","76s","75s","65s","54s","AKo","AQo","AJo","ATo","A9o","KQo","KJo","KTo","QJo","QTo","JTo"]),
};
const PREMIUMS = new Set(["AA","KK","QQ","AKs","AKo"]);
const CALL_VS_RAISE = new Set(["JJ","TT","99","88","77","AQs","AJs","ATs","KQs","KJs","QJs","JTs","AQo"]);

function handKey(c1, c2) {
  const RC = {2:"2",3:"3",4:"4",5:"5",6:"6",7:"7",8:"8",9:"9",10:"T",11:"J",12:"Q",13:"K",14:"A"};
  let r1=c1.rank, r2=c2.rank, s1=c1.suit, s2=c2.suit;
  if (r1<r2) { [r1,r2,s1,s2]=[r2,r1,s2,s1]; }
  if (r1===r2) return `${RC[r1]}${RC[r2]}`;
  return s1===s2 ? `${RC[r1]}${RC[r2]}s` : `${RC[r1]}${RC[r2]}o`;
}

// ── Parse PS hands ────────────────────────────────────────────────────

function parseHands(text) {
  const rawHands = text.split(/\*{5,}\s*#\s*\d+\s*\*{5,}/).filter(h => h.includes("PokerStars Hand"));
  const hands = [];

  for (const raw of rawHands) {
    if (!raw.includes(HERO)) continue;
    const dealtMatch = raw.match(/Dealt to Skurj_poker \[(.+?)\]/);
    if (!dealtMatch) continue;

    const cardStrs = dealtMatch[1].split(" ");
    const cards = cardStrs.map(parseCard);

    const btnMatch = raw.match(/Seat #(\d+) is the button/);
    const heroSeatMatch = raw.match(/Seat (\d+): Skurj_poker/);
    const allSeats = [];
    const seatRegex = /Seat (\d+):/g;
    let m;
    while ((m = seatRegex.exec(raw)) !== null) allSeats.push(parseInt(m[1]));
    allSeats.sort((a, b) => a - b);

    let position = "BTN";
    if (btnMatch && heroSeatMatch) {
      const btn = parseInt(btnMatch[1]);
      const heroSeat = parseInt(heroSeatMatch[1]);
      const btnIdx = allSeats.indexOf(btn);
      const heroIdx = allSeats.indexOf(heroSeat);
      const dist = (heroIdx - btnIdx + allSeats.length) % allSeats.length;
      if (dist === 0) position = "BTN";
      else if (dist === 1) position = "SB";
      else if (dist === 2) position = "BB";
      else if (dist === allSeats.length - 1) position = "CO";
      else if (dist === allSeats.length - 2) position = "MP";
      else position = "EP";
    }
    const isIP = ["BTN", "CO"].includes(position);

    // Board
    const boardCards = [];
    const flopMatch = raw.match(/\*\*\* FLOP \*\*\* \[(.+?)\]/);
    const turnMatch = raw.match(/\*\*\* TURN \*\*\* \[.+?\] \[(.+?)\]/);
    const riverMatch = raw.match(/\*\*\* RIVER \*\*\* \[.+?\] \[(.+?)\]/);
    if (flopMatch) boardCards.push(...flopMatch[1].split(" ").map(parseCard));
    if (turnMatch) boardCards.push(parseCard(turnMatch[1]));
    if (riverMatch) boardCards.push(parseCard(riverMatch[1]));

    // Parse ALL actions in order (all players, all streets)
    const allActions = [];
    let currentStreet = "PREFLOP";
    const lines = raw.split("\n");
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
      allActions.push({ player: am[1], action, amount, isHero: am[1] === HERO, street: currentStreet });
    }

    // Result
    const potMatch = raw.match(/Total pot \$([\d.]+)/);
    const pot = potMatch ? parseFloat(potMatch[1]) : 0;
    const heroWon = raw.includes(HERO + " collected");
    const collectMatch = raw.match(new RegExp(HERO.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + " collected \\$([\\.\\d]+)"));
    const collected = collectMatch ? parseFloat(collectMatch[1]) : 0;

    let invested = 0;
    if (raw.includes(HERO + ": posts small blind")) invested += 0.05;
    if (raw.includes(HERO + ": posts big blind")) invested += 0.10;
    for (const a of allActions) {
      if (a.isHero && ["CALL", "BET", "RAISE"].includes(a.action)) invested += a.amount;
    }
    const profit = heroWon ? collected - invested : -invested;

    const bbMatch = raw.match(/\$([\d.]+)\/\$([\d.]+)/);
    const bbVal = bbMatch ? parseFloat(bbMatch[2]) : 0.10;

    hands.push({
      cardStrs, cards, position, isIP, boardCards, allActions, pot,
      heroWon, collected, invested, profit, bbVal,
    });
  }
  return hands;
}

// ── Bot decision functions ────────────────────────────────────────────

function botPreflop(cards, position, facingRaise) {
  const key = handKey(cards[0], cards[1]);
  if (position === "BB" && !facingRaise) return "CHECK";
  if (facingRaise) {
    if (PREMIUMS.has(key)) return "RAISE";
    if (PREMIUMS.has(key) || CALL_VS_RAISE.has(key)) return "CALL";
    const bbDefend = OPEN_RANGES["BB"];
    if (position === "BB" && bbDefend.has(key)) return "CALL";
    return "FOLD";
  }
  const range = OPEN_RANGES[position] || OPEN_RANGES["CO"];
  if (range.has(key)) return "RAISE";
  return "FOLD";
}

function lookupFlop(bucket, sb, pos, potClass, hist) {
  for (let d = 0; d <= 5; d++) {
    for (const delta of d === 0 ? [0] : [d, -d]) {
      const b = bucket + delta;
      if (b < 0 || b >= 50) continue;
      const k = `FLOP:${b}:s${sb}:${pos}:${potClass}:${hist}`;
      if (flopCFR[k]) return flopCFR[k];
    }
  }
  return null;
}

function botFlop(cards, board, isIP, potBB, oppHistory) {
  const strength = evaluateHandStrength(cards, board, "FLOP");
  const bucket = Math.min(49, Math.floor(strength * 50));
  const sb = 1; // assume medium stack
  const pos = isIP ? "IP" : "OOP";
  const potClass = potBB >= 15 ? "3BP" : potBB <= 3 ? "LP" : "SRP";

  const probs = lookupFlop(bucket, sb, pos, potClass, oppHistory);
  if (probs) {
    // Pick highest probability action
    let fold = probs.FOLD || 0;
    let check = probs.CHECK || 0;
    let call = probs.CALL || 0;
    let agg = (probs.BET_33||0)+(probs.BET_66||0)+(probs.BET_POT||0)+(probs.BET_ALLIN||0)+
              (probs.BET_HALF||0)+(probs.RAISE_HALF||0)+(probs.RAISE_POT||0)+(probs.RAISE_ALLIN||0);

    const best = Math.max(fold, check, call, agg);
    if (best === agg) return { action: "BET/RAISE", strength, source: "cfr" };
    if (best === call) return { action: "CALL", strength, source: "cfr" };
    if (best === check) return { action: "CHECK", strength, source: "cfr" };
    return { action: "FOLD", strength, source: "cfr" };
  }

  // Fallback: equity rules
  return botTurnRiver(strength);
}

function botTurnRiver(strength) {
  if (strength >= 0.70) return { action: "BET/RAISE", strength, source: "rules" };
  if (strength >= 0.35) return { action: "CHECK", strength, source: "rules" };
  return { action: "FOLD", strength, source: "rules" };
}

// ── Main simulation ───────────────────────────────────────────────────

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
console.log(`  Total: ${allHands.length} hands\n`);

console.log("=".repeat(80));
console.log("  REPLAY SIMULATOR — Stratified Bot vs Real Opponent Actions");
console.log("=".repeat(80));

let actualTotalProfit = 0;
let botTotalProfit = 0;
let handsWhereBotDiffers = 0;
let foldSaves = 0;         // $ saved by folding where hero played and lost
let foldSaveCount = 0;
let missedWins = 0;         // $ missed by folding where hero played and won
let missedWinCount = 0;
let extraValue = 0;         // estimated extra value from better postflop play
let sameDecision = 0;
let differentDecision = 0;
const handDetails = [];

for (const hand of allHands) {
  actualTotalProfit += hand.profit;

  // Step through the hand action by action
  let botFolded = false;
  let botProfit = hand.profit; // start with actual, adjust
  let flopHist = "";
  let facingRaise = false;
  let currentStreet = "PREFLOP";
  let heroBetThisStreet = 0;   // how much hero invested on/after the street bot diverges
  let botDivergedOnStreet = null;

  for (const act of hand.allActions) {
    if (act.street !== currentStreet) {
      currentStreet = act.street;
      flopHist = "";
    }

    if (botFolded) break; // bot already out of the hand

    if (!act.isHero) {
      // Opponent action — track for flop CFR
      if (act.action === "RAISE" || act.action === "BET") facingRaise = true;
      if (currentStreet === "FLOP" && act.action !== "FOLD") {
        const r = hand.pot > 0 ? act.amount / hand.pot : 0.5;
        flopHist += act.action === "CHECK" ? "k" : act.action === "CALL" ? "c" :
                    r >= 0.85 ? "bp" : r >= 0.5 ? "bs" : "bt";
      }
      continue;
    }

    // HERO ACTION — bot decides instead
    let botAction;
    const heroNorm = (act.action === "BET" || act.action === "RAISE") ? "BET/RAISE" :
                     act.action;

    if (currentStreet === "PREFLOP") {
      botAction = botPreflop(hand.cards, hand.position, facingRaise);
      // Normalize
      if (botAction === "RAISE") botAction = "BET/RAISE";
    } else if (currentStreet === "FLOP") {
      const board = hand.boardCards.slice(0, 3);
      const potBB = hand.pot / hand.bbVal;
      const result = botFlop(hand.cards, board, hand.isIP, potBB, flopHist);
      botAction = result.action;
      // If facing a bet, CHECK isn't valid — map to CALL vs FOLD
      if (facingRaise && botAction === "CHECK") botAction = "CALL";
    } else {
      const board = currentStreet === "TURN" ? hand.boardCards.slice(0, 4) : hand.boardCards.slice(0, 5);
      const strength = evaluateHandStrength(hand.cards, board, currentStreet);
      if (facingRaise) {
        // Facing bet: call/fold/raise based on equity
        if (strength > 0.85) botAction = "BET/RAISE";
        else if (strength > 0.35) botAction = "CALL";
        else botAction = "FOLD";
      } else {
        const result = botTurnRiver(strength);
        botAction = result.action;
      }
    }

    // Compare
    const agree = heroNorm === botAction ||
      (heroNorm === "CHECK" && botAction === "CHECK") ||
      (heroNorm === "CALL" && botAction === "CALL");

    if (agree) {
      sameDecision++;
    } else {
      differentDecision++;

      // Calculate P&L impact
      if (botAction === "FOLD" && heroNorm !== "FOLD") {
        // Bot folds, hero continued
        botFolded = true;
        botDivergedOnStreet = currentStreet;

        if (!hand.heroWon) {
          // Hero lost — bot saves the remaining investment
          // Approximate: hero invested after this point
          let savedAmount = 0;
          let foundHeroAction = false;
          for (const a2 of hand.allActions) {
            if (a2 === act) { foundHeroAction = true; continue; }
            if (!foundHeroAction) continue;
            if (a2.isHero && ["CALL", "BET", "RAISE"].includes(a2.action)) {
              savedAmount += a2.amount;
            }
          }
          savedAmount += act.amount || 0; // include this action's amount
          foldSaves += savedAmount;
          foldSaveCount++;
          botProfit = hand.profit + savedAmount; // would have lost less

          handDetails.push({
            hand: hand.cardStrs.join(" "), pos: hand.position, street: currentStreet,
            hero: heroNorm, bot: botAction,
            heroProfit: hand.profit, botProfit,
            impact: `SAVE $${savedAmount.toFixed(2)}`, won: false,
          });
        } else {
          // Hero won — bot misses the win
          missedWins += hand.profit;
          missedWinCount++;
          // Bot breaks even on this hand (folded, lost blinds at most)
          let blindCost = 0;
          if (hand.position === "SB") blindCost = 0.05;
          if (hand.position === "BB") blindCost = 0.10;
          botProfit = -blindCost;

          handDetails.push({
            hand: hand.cardStrs.join(" "), pos: hand.position, street: currentStreet,
            hero: heroNorm, bot: botAction,
            heroProfit: hand.profit, botProfit,
            impact: `MISS $${hand.profit.toFixed(2)}`, won: true,
          });
        }
      } else if (heroNorm === "FOLD" && botAction !== "FOLD") {
        // Bot plays, hero folded — we can't know the outcome
        // Mark as potential value but don't count it
        handDetails.push({
          hand: hand.cardStrs.join(" "), pos: hand.position, street: currentStreet,
          hero: heroNorm, bot: botAction,
          heroProfit: hand.profit, botProfit: hand.profit, // unknown, keep same
          impact: "PLAY?", won: hand.heroWon,
        });
      } else {
        // Different aggression level (e.g., hero bets, bot checks or vice versa)
        // Keep same profit (conservative estimate)
        handDetails.push({
          hand: hand.cardStrs.join(" "), pos: hand.position, street: currentStreet,
          hero: heroNorm, bot: botAction,
          heroProfit: hand.profit, botProfit: hand.profit,
          impact: "DIFF", won: hand.heroWon,
        });
      }
    }

    // Reset facing raise for next action in this street
    facingRaise = false;
    break; // only first hero action per street for now
  }

  botTotalProfit += botProfit;
}

// ── Results ───────────────────────────────────────────────────────────

const BB = 0.10;
const numHands = allHands.length;
const h100 = numHands / 100;

console.log(`\n  DECISION COMPARISON`);
console.log(`  Same decision:      ${sameDecision}`);
console.log(`  Different decision: ${differentDecision}`);
console.log(`  Agreement rate:     ${(sameDecision / (sameDecision + differentDecision) * 100).toFixed(1)}%`);

console.log(`\n  P&L IMPACT`);
console.log(`  Your actual P&L:    ${actualTotalProfit >= 0 ? "+" : ""}$${actualTotalProfit.toFixed(2)} (${(actualTotalProfit / BB / h100).toFixed(1)} bb/100)`);
console.log(`  Bot estimated P&L:  ${botTotalProfit >= 0 ? "+" : ""}$${botTotalProfit.toFixed(2)} (${(botTotalProfit / BB / h100).toFixed(1)} bb/100)`);
console.log(`  Delta:              ${(botTotalProfit - actualTotalProfit) >= 0 ? "+" : ""}$${(botTotalProfit - actualTotalProfit).toFixed(2)} (${((botTotalProfit - actualTotalProfit) / BB / h100).toFixed(1)} bb/100)`);

console.log(`\n  FOLD ANALYSIS`);
console.log(`  Saves (bot folds, you played & lost): ${foldSaveCount} hands, +$${foldSaves.toFixed(2)}`);
console.log(`  Misses (bot folds, you played & won): ${missedWinCount} hands, -$${missedWins.toFixed(2)}`);
console.log(`  Net fold EV:                          ${(foldSaves - missedWins) >= 0 ? "+" : ""}$${(foldSaves - missedWins).toFixed(2)}`);

if (handDetails.length > 0) {
  console.log(`\n  HAND-BY-HAND DIFFERENCES (${Math.min(handDetails.length, 25)} of ${handDetails.length}):`);
  console.log(`  ${"Hand".padEnd(7)} ${"Pos".padEnd(4)} ${"St".padEnd(8)} ${"You".padEnd(10)} ${"Bot".padEnd(10)} ${"Your P&L".padStart(9)} ${"Bot P&L".padStart(9)} ${"Impact"}`);
  console.log("  " + "-".repeat(75));

  // Sort by impact magnitude
  handDetails.sort((a, b) => Math.abs(b.botProfit - b.heroProfit) - Math.abs(a.botProfit - a.heroProfit));
  for (const d of handDetails.slice(0, 25)) {
    const hp = d.heroProfit >= 0 ? `+$${d.heroProfit.toFixed(2)}` : `-$${Math.abs(d.heroProfit).toFixed(2)}`;
    const bp = d.botProfit >= 0 ? `+$${d.botProfit.toFixed(2)}` : `-$${Math.abs(d.botProfit).toFixed(2)}`;
    console.log(`  ${d.hand.padEnd(7)} ${d.pos.padEnd(4)} ${d.street.padEnd(8)} ${d.hero.padEnd(10)} ${d.bot.padEnd(10)} ${hp.padStart(9)} ${bp.padStart(9)} ${d.impact}`);
  }
}

console.log("\n" + "=".repeat(80));
