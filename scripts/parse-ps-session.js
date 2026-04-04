#!/usr/bin/env node
"use strict";

/**
 * Parse PokerStars hand history and extract session stats.
 *
 * Usage:
 *   node scripts/parse-ps-session.js hands/ps_session_20260404.txt
 */

const fs = require("fs");
const path = require("path");

const filePath = process.argv[2] || "hands/ps_session_20260404.txt";
const text = fs.readFileSync(filePath, "utf8");

// Split into individual hands
const rawHands = text.split(/\*{5,}\s*#\s*\d+\s*\*{5,}/).filter(h => h.includes("PokerStars Hand"));

const HERO = "Skurj_poker";
let stats = { hands: 0, vpip: 0, pfr: 0, showdowns: 0, won: 0, totalWon: 0, totalLost: 0 };
let bigHands = [];
let allHands = [];

for (const hand of rawHands) {
  if (!hand.includes(HERO)) continue;
  stats.hands++;

  // Hero cards
  const dealtMatch = hand.match(/Dealt to Skurj_poker \[(.+?)\]/);
  if (!dealtMatch) continue;
  const heroCards = dealtMatch[1];

  // Position — find button seat and hero seat
  const btnMatch = hand.match(/Seat #(\d+) is the button/);
  const heroSeatMatch = hand.match(/Seat (\d+): Skurj_poker/);
  let position = "?";
  if (btnMatch && heroSeatMatch) {
    const btn = parseInt(btnMatch[1]);
    const heroSeat = parseInt(heroSeatMatch[1]);
    // Count seats
    const seatMatches = hand.match(/Seat \d+:/g) || [];
    const numPlayers = seatMatches.length;
    // Simple position estimate
    if (heroSeat === btn) position = "BTN";
    else {
      // Count seats after button
      const allSeats = [];
      const seatRegex = /Seat (\d+):/g;
      let m;
      while ((m = seatRegex.exec(hand)) !== null) allSeats.push(parseInt(m[1]));
      allSeats.sort((a, b) => a - b);

      // Position order from button
      const btnIdx = allSeats.indexOf(btn);
      const heroIdx = allSeats.indexOf(heroSeat);
      const dist = (heroIdx - btnIdx + allSeats.length) % allSeats.length;
      if (dist === 1) position = "SB";
      else if (dist === 2) position = "BB";
      else if (dist === allSeats.length - 1) position = "CO";
      else if (dist === allSeats.length - 2) position = "MP";
      else position = "EP";
    }
  }

  // Preflop actions
  const flopSplit = hand.split("*** FLOP ***");
  const preflopSection = flopSplit[0];
  const heroPreflop = preflopSection.split("\n").filter(l => l.startsWith(HERO + ":"));
  const heroCalledOrRaised = heroPreflop.some(a => /calls|raises|bets/i.test(a));
  const heroRaised = heroPreflop.some(a => /raises|bets/i.test(a));
  if (heroCalledOrRaised) stats.vpip++;
  if (heroRaised) stats.pfr++;

  // Showdown
  if (hand.includes("*** SHOW DOWN ***")) stats.showdowns++;

  // Result
  const collectedMatch = hand.match(new RegExp(HERO + " collected \\$([\\.\\d]+)"));
  const potMatch = hand.match(/Total pot \$([\d.]+)/);
  const pot = potMatch ? parseFloat(potMatch[1]) : 0;
  const heroWon = !!collectedMatch;
  const amountWon = collectedMatch ? parseFloat(collectedMatch[1]) : 0;

  // Track invested amount (rough: sum of hero's bets/calls/raises/blinds)
  const heroAllActions = hand.split("\n").filter(l => l.startsWith(HERO + ":"));
  let invested = 0;
  for (const a of heroAllActions) {
    const amtMatch = a.match(/\$([\d.]+)/);
    if (amtMatch) {
      const action = a.toLowerCase();
      if (action.includes("calls") || action.includes("bets") || action.includes("raises")) {
        invested += parseFloat(amtMatch[1]);
      }
    }
  }
  // Blinds
  if (hand.includes(HERO + ": posts small blind")) invested += 0.05;
  if (hand.includes(HERO + ": posts big blind")) invested += 0.10;

  const profit = heroWon ? amountWon - invested : -invested;

  if (heroWon) {
    stats.won++;
    stats.totalWon += amountWon;
  } else {
    stats.totalLost += invested;
  }

  const handData = {
    cards: heroCards,
    position,
    pot: pot.toFixed(2),
    invested: invested.toFixed(2),
    profit: profit.toFixed(2),
    won: heroWon,
    showdown: hand.includes("*** SHOW DOWN ***"),
  };
  allHands.push(handData);

  if (pot > 0.80 || Math.abs(profit) > 0.50) {
    bigHands.push(handData);
  }
}

const netProfit = stats.totalWon - stats.totalLost;

console.log("=".repeat(60));
console.log("  SESSION STATS — " + HERO);
console.log("=".repeat(60));
console.log(`  Hands:      ${stats.hands}`);
console.log(`  VPIP:       ${(stats.vpip / stats.hands * 100).toFixed(1)}% (${stats.vpip}/${stats.hands})`);
console.log(`  PFR:        ${(stats.pfr / stats.hands * 100).toFixed(1)}% (${stats.pfr}/${stats.hands})`);
console.log(`  WTSD:       ${stats.showdowns} showdowns`);
console.log(`  Won:        ${stats.won}/${stats.hands} (${(stats.won / stats.hands * 100).toFixed(1)}%)`);
console.log(`  Net:        ${netProfit >= 0 ? "+" : ""}$${netProfit.toFixed(2)}`);
console.log();

console.log("  Significant hands:");
for (const h of bigHands) {
  const icon = h.won ? "W" : "L";
  console.log(`    [${icon}] ${h.cards.padEnd(6)} ${h.position.padEnd(3)} pot=$${h.pot} profit=${h.profit >= 0 ? "+" : ""}$${h.profit}`);
}

console.log();

// Position breakdown
const byPos = {};
for (const h of allHands) {
  if (!byPos[h.position]) byPos[h.position] = { hands: 0, vpip: 0, profit: 0 };
  byPos[h.position].hands++;
  byPos[h.position].profit += parseFloat(h.profit);
}
console.log("  By position:");
for (const pos of ["BTN", "CO", "MP", "EP", "SB", "BB"]) {
  const p = byPos[pos];
  if (!p) continue;
  console.log(`    ${pos.padEnd(3)}: ${p.hands} hands  net=${p.profit >= 0 ? "+" : ""}$${p.profit.toFixed(2)}`);
}

console.log("=".repeat(60));

// Save parsed data
const outPath = filePath.replace(".txt", "_parsed.json");
fs.writeFileSync(outPath, JSON.stringify({ stats, allHands, bigHands }, null, 2));
console.log(`\n  Parsed data saved to ${outPath}`);
