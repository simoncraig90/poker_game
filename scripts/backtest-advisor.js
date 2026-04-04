#!/usr/bin/env node
"use strict";

/**
 * Backtest the new advisor (preflop chart + equity model) against
 * actual PS hand history. Shows what the advisor WOULD have recommended
 * vs what actually happened.
 *
 * Usage:
 *   node scripts/backtest-advisor.js hands/ps_session_20260404.txt
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const PYTHON = "C:\\Users\\Simon\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";
const filePath = process.argv[2] || "hands/ps_session_20260404.txt";
const text = fs.readFileSync(filePath, "utf8");

const HERO = "Skurj_poker";

// Parse hands
const rawHands = text.split(/\*{5,}\s*#\s*\d+\s*\*{5,}/).filter(h => h.includes("PokerStars Hand"));

const parsedHands = [];

for (const hand of rawHands) {
  if (!hand.includes(HERO)) continue;

  const dealtMatch = hand.match(/Dealt to Skurj_poker \[(.+?)\]/);
  if (!dealtMatch) continue;
  const cardStr = dealtMatch[1];
  const cards = cardStr.split(" ");

  // Position
  const btnMatch = hand.match(/Seat #(\d+) is the button/);
  const heroSeatMatch = hand.match(/Seat (\d+): Skurj_poker/);
  let position = "BTN";
  if (btnMatch && heroSeatMatch) {
    const btn = parseInt(btnMatch[1]);
    const heroSeat = parseInt(heroSeatMatch[1]);
    const allSeats = [];
    const seatRegex = /Seat (\d+):/g;
    let m;
    while ((m = seatRegex.exec(hand)) !== null) allSeats.push(parseInt(m[1]));
    allSeats.sort((a, b) => a - b);
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

  // Was hero facing a raise preflop?
  const flopSplit = hand.split("*** FLOP ***");
  const preflopLines = flopSplit[0].split("\n");
  let facingRaise = false;
  let heroActedPreflop = false;
  let heroAction = "FOLD";
  for (const line of preflopLines) {
    if (line.startsWith(HERO + ":")) {
      heroActedPreflop = true;
      if (/folds/i.test(line)) heroAction = "FOLD";
      else if (/raises/i.test(line)) heroAction = "RAISE";
      else if (/calls/i.test(line)) heroAction = "CALL";
      else if (/checks/i.test(line)) heroAction = "CHECK";
      break;
    }
    // Someone raised before hero
    if (/raises/i.test(line) && !line.startsWith(HERO)) facingRaise = true;
    if (/bets/i.test(line) && !line.startsWith(HERO)) facingRaise = true;
  }

  // Board cards
  const boardCards = [];
  const flopMatch = hand.match(/\*\*\* FLOP \*\*\* \[(.+?)\]/);
  const turnMatch = hand.match(/\*\*\* TURN \*\*\* \[.+?\] \[(.+?)\]/);
  const riverMatch = hand.match(/\*\*\* RIVER \*\*\* \[.+?\] \[(.+?)\]/);
  if (flopMatch) boardCards.push(...flopMatch[1].split(" "));
  if (turnMatch) boardCards.push(turnMatch[1]);
  if (riverMatch) boardCards.push(riverMatch[1]);

  // Result
  const potMatch = hand.match(/Total pot \$([\d.]+)/);
  const pot = potMatch ? parseFloat(potMatch[1]) : 0;
  const heroWon = hand.includes(HERO + " collected");

  // How much hero invested
  const heroLines = hand.split("\n").filter(l => l.startsWith(HERO + ":"));
  let invested = 0;
  for (const l of heroLines) {
    const amt = l.match(/\$([\d.]+)/);
    if (amt && /calls|raises|bets/i.test(l)) invested += parseFloat(amt[1]);
  }
  if (hand.includes(HERO + ": posts small blind")) invested += 0.05;
  if (hand.includes(HERO + ": posts big blind")) invested += 0.10;

  parsedHands.push({
    cards, position, facingRaise, heroAction, boardCards, pot, heroWon, invested,
  });
}

// Get preflop chart advice for each hand via Python
const handsJson = JSON.stringify(parsedHands.map(h => ({
  c1: h.cards[0], c2: h.cards[1], pos: h.position, facing: h.facingRaise,
  board: h.boardCards,
})));

const pyScript = `
import sys, json
sys.path.insert(0, "vision")
from preflop_chart import preflop_advice
from advisor import equity_model_predict, assess_board_danger, _load_equity_model

_load_equity_model()

hands = json.loads(sys.stdin.read())
results = []
for h in hands:
    pf = preflop_advice(h["c1"], h["c2"], h["pos"], facing_raise=h["facing"])
    eq = None
    danger = None
    if h["board"]:
        eq = equity_model_predict([h["c1"], h["c2"]], h["board"])
        danger = assess_board_danger([h["c1"], h["c2"]], h["board"])
    results.append({
        "pf_action": pf["action"],
        "hand_key": pf["hand_key"],
        "note": pf["note"],
        "eq": eq,
        "danger": danger.get("warnings", []) if danger else [],
        "suppress": danger.get("suppress_raise", False) if danger else False,
    })
print(json.dumps(results))
`;

const tmpScript = path.join(__dirname, "_backtest_tmp.py");
fs.writeFileSync(tmpScript, pyScript);

let advisorResults;
try {
  const output = execSync(`${PYTHON} ${tmpScript}`, {
    input: handsJson,
    encoding: "utf8",
    timeout: 30000,
    cwd: process.cwd(),
  });
  // Find the JSON line (skip any print statements from model loading)
  const lines = output.trim().split("\n");
  const jsonLine = lines.find(l => {
    const t = l.trimStart();
    return t.startsWith("[{") || (t.startsWith("[") && !t.startsWith("[A") && !t.startsWith("[S"));
  });
  if (!jsonLine) throw new Error("No JSON output from Python. Output:\n" + output);
  advisorResults = JSON.parse(jsonLine);
} finally {
  try { fs.unlinkSync(tmpScript); } catch {}
}

// Compare
console.log("=".repeat(70));
console.log("  BACKTEST — New Advisor vs Actual Play");
console.log("=".repeat(70));
console.log();

let savedMoney = 0;
let missedValue = 0;
let agreements = 0;
let disagreements = 0;
let foldSaves = [];
let missedRaises = [];

for (let i = 0; i < parsedHands.length; i++) {
  const h = parsedHands[i];
  const a = advisorResults[i];
  const cardStr = h.cards.join(" ");

  // Compare preflop action
  const actual = h.heroAction;
  const advised = a.pf_action;

  const agree = (actual === advised) ||
    (actual === "CHECK" && advised === "CALL") ||
    (actual === "CALL" && advised === "CALL");

  if (agree) {
    agreements++;
  } else {
    disagreements++;

    // Would the advisor have saved money?
    if (advised === "FOLD" && actual !== "FOLD" && !h.heroWon) {
      savedMoney += h.invested;
      foldSaves.push({
        cards: cardStr, key: a.hand_key, pos: h.position,
        actual, advised, invested: h.invested, pot: h.pot
      });
    }

    // Would the advisor have made money we missed?
    if (advised === "RAISE" && actual === "FOLD" && h.position === "BTN") {
      missedRaises.push({
        cards: cardStr, key: a.hand_key, pos: h.position,
      });
    }
  }
}

// Print disagreements
console.log("  PREFLOP DISAGREEMENTS (advisor would have played differently):");
console.log();

for (let i = 0; i < parsedHands.length; i++) {
  const h = parsedHands[i];
  const a = advisorResults[i];
  const actual = h.heroAction;
  const advised = a.pf_action;

  const agree = (actual === advised) ||
    (actual === "CHECK" && (advised === "CALL" || advised === "FOLD")) ||
    (actual === "CALL" && advised === "CALL");
  if (agree) continue;

  const cardStr = h.cards.join(" ");
  const wonStr = h.heroWon ? "WON" : "LOST";
  const icon = (advised === "FOLD" && !h.heroWon) ? "$$" :
               (advised === "RAISE" && actual === "FOLD") ? "??" : "  ";

  console.log(`  ${icon} ${a.hand_key.padEnd(4)} ${h.position.padEnd(3)} | You: ${actual.padEnd(5)} | Chart: ${advised.padEnd(5)} | ${a.note} | pot=$${h.pot.toFixed(2)} ${wonStr} inv=$${h.invested.toFixed(2)}`);
}

console.log();

// Postflop analysis for hands that went to flop
console.log("  POSTFLOP — Equity + Board Danger:");
console.log();
let postflopCount = 0;
for (let i = 0; i < parsedHands.length; i++) {
  const h = parsedHands[i];
  const a = advisorResults[i];
  if (!h.boardCards.length || a.eq === null) continue;
  postflopCount++;

  const cardStr = h.cards.join(" ");
  const boardStr = h.boardCards.join(" ");
  const warnings = a.danger.length ? a.danger.join(" ") : "clean";
  const wonStr = h.heroWon ? "WON" : "LOST";
  const eqPct = `${(a.eq * 100).toFixed(0)}%`;

  // Only show significant hands
  if (h.pot < 1.0 && !a.danger.length) continue;

  const suppress = a.suppress ? " [NO RAISE]" : "";
  console.log(`    ${cardStr.padEnd(6)} | ${boardStr.padEnd(16)} | eq=${eqPct.padEnd(4)} ${warnings}${suppress} | pot=$${h.pot.toFixed(2)} ${wonStr}`);
}

console.log();
console.log("-".repeat(70));
console.log(`  Preflop: ${agreements} agree, ${disagreements} disagree`);
console.log(`  Potential savings: $${savedMoney.toFixed(2)} (hands where chart says FOLD, you played, and lost)`);
if (foldSaves.length > 0) {
  console.log(`  Saved hands: ${foldSaves.map(f => f.key + " " + f.pos).join(", ")}`);
}
if (missedRaises.length > 0) {
  console.log(`  Missed opens: ${missedRaises.map(m => m.key + " " + m.pos).join(", ")}`);
}
console.log("=".repeat(70));
