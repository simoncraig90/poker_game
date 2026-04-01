#!/usr/bin/env node
"use strict";

/**
 * Parse PokerStars hand histories and build opponent profiles.
 * Reads all .txt files from hands/poker_stars/, calculates stats per opponent,
 * writes profiles to scripts/opponent-profiles.json, and prints a report.
 */

const fs = require("fs");
const path = require("path");

const HERO = "Skurj_poker";
const HANDS_DIR = path.join(__dirname, "..", "hands", "poker_stars");
const OUTPUT_FILE = path.join(__dirname, "opponent-profiles.json");

// ── Parse all hand history files ────────────────────────────────────────

function parseAllFiles() {
  const files = fs.readdirSync(HANDS_DIR).filter(f => f.endsWith(".txt"));
  const allHands = [];
  for (const file of files) {
    const content = fs.readFileSync(path.join(HANDS_DIR, file), "utf8");
    const hands = splitHands(content);
    allHands.push(...hands);
  }
  return allHands;
}

function splitHands(content) {
  // Split on PokerStars Hand # lines
  const blocks = content.split(/(?=PokerStars Hand #)/);
  return blocks.filter(b => b.trim().startsWith("PokerStars Hand #"));
}

function parseHand(block) {
  const lines = block.split("\n").map(l => l.trim()).filter(l => l);
  const hand = {
    id: null,
    players: {},      // name -> { seat, stack }
    blindPlayers: {},  // name -> "sb" | "bb"
    button: null,
    phases: { preflop: [], flop: [], turn: [], river: [] },
    currentPhase: "preflop",
    showdown: [],      // { player, cards }
    summary: [],
    winners: [],
    board: [],
    wentToShowdown: new Set(),
  };

  // Parse header
  const headerMatch = lines[0].match(/PokerStars Hand #(\d+)/);
  if (headerMatch) hand.id = headerMatch[1];

  const buttonMatch = block.match(/Seat #(\d+) is the button/);
  if (buttonMatch) hand.button = parseInt(buttonMatch[1]);

  let phase = "header";

  for (const line of lines) {
    // Seat info
    const seatMatch = line.match(/^Seat (\d+): (.+?) \(\$([0-9.]+) in chips\)/);
    if (seatMatch && phase === "header") {
      const seatNum = parseInt(seatMatch[1]);
      const name = seatMatch[2];
      const stack = parseFloat(seatMatch[3]);
      hand.players[name] = { seat: seatNum, stack };
      continue;
    }

    // Blinds
    const blindMatch = line.match(/^(.+?): posts (small blind|big blind) \$([0-9.]+)/);
    if (blindMatch) {
      const name = blindMatch[1];
      const blindType = blindMatch[2] === "small blind" ? "sb" : "bb";
      hand.blindPlayers[name] = blindType;
      continue;
    }

    // Phase markers
    if (line === "*** HOLE CARDS ***") { phase = "preflop"; continue; }
    if (line.startsWith("*** FLOP ***")) {
      phase = "flop";
      hand.currentPhase = "flop";
      const boardMatch = line.match(/\[(.+?)\]/);
      if (boardMatch) hand.board = boardMatch[1].split(" ");
      continue;
    }
    if (line.startsWith("*** TURN ***")) {
      phase = "turn";
      hand.currentPhase = "turn";
      const turnMatch = line.match(/\] \[(.+?)\]$/);
      if (turnMatch) hand.board.push(turnMatch[1]);
      continue;
    }
    if (line.startsWith("*** RIVER ***")) {
      phase = "river";
      hand.currentPhase = "river";
      const riverMatch = line.match(/\] \[(.+?)\]$/);
      if (riverMatch) hand.board.push(riverMatch[1]);
      continue;
    }
    if (line === "*** SHOW DOWN ***") { phase = "showdown"; continue; }
    if (line === "*** SUMMARY ***") { phase = "summary"; continue; }

    // Actions in preflop/flop/turn/river
    if (["preflop", "flop", "turn", "river"].includes(phase)) {
      const actionMatch = line.match(/^(.+?): (folds|checks|calls|bets|raises)(.*)/);
      if (actionMatch) {
        const name = actionMatch[1];
        let action = actionMatch[2];
        let amount = 0;
        const amtMatch = actionMatch[3].match(/\$([0-9.]+)/);
        if (amtMatch) amount = parseFloat(amtMatch[1]);

        hand.phases[phase].push({ player: name, action, amount });
        continue;
      }
    }

    // Showdown
    if (phase === "showdown") {
      const showMatch = line.match(/^(.+?): shows \[(.+?)\]/);
      if (showMatch) {
        hand.showdown.push({ player: showMatch[1], cards: showMatch[2] });
        hand.wentToShowdown.add(showMatch[1]);
      }
      const muckMatch = line.match(/^(.+?): mucks hand/);
      if (muckMatch) {
        hand.wentToShowdown.add(muckMatch[1]);
      }
    }

    // Summary - detect winners and mucked hands at showdown
    if (phase === "summary") {
      const winMatch = line.match(/^Seat \d+: (.+?)(?:\s+\(.+?\))?\s+collected \(\$([0-9.]+)\)/);
      if (winMatch) {
        hand.winners.push({ player: winMatch[1], amount: parseFloat(winMatch[2]) });
      }
      // Detect showed and won
      const showWonMatch = line.match(/^Seat \d+: (.+?)(?:\s+\(.+?\))?\s+showed \[(.+?)\] and won/);
      if (showWonMatch) {
        hand.wentToShowdown.add(showWonMatch[1]);
        if (!hand.showdown.find(s => s.player === showWonMatch[1])) {
          hand.showdown.push({ player: showWonMatch[1], cards: showWonMatch[2] });
        }
      }
      // Detect showed and lost
      const showLostMatch = line.match(/^Seat \d+: (.+?)(?:\s+\(.+?\))?\s+showed \[(.+?)\] and lost/);
      if (showLostMatch) {
        hand.wentToShowdown.add(showLostMatch[1]);
        if (!hand.showdown.find(s => s.player === showLostMatch[1])) {
          hand.showdown.push({ player: showLostMatch[1], cards: showLostMatch[2] });
        }
      }
      // Detect mucked
      const muckedMatch = line.match(/^Seat \d+: (.+?)(?:\s+\(.+?\))?\s+mucked \[(.+?)\]/);
      if (muckedMatch) {
        hand.wentToShowdown.add(muckedMatch[1]);
      }
    }

    // Collected outside of summary
    if (phase !== "summary") {
      const collectMatch = line.match(/^(.+?) collected \$([0-9.]+) from/);
      if (collectMatch) {
        const name = collectMatch[1];
        if (!hand.winners.find(w => w.player === name)) {
          hand.winners.push({ player: name, amount: parseFloat(collectMatch[2]) });
        }
      }
    }
  }

  return hand;
}

// ── Build opponent stats ────────────────────────────────────────────────

function buildStats(hands) {
  const stats = {}; // player -> stat accumulators

  function ensure(name) {
    if (!stats[name]) {
      stats[name] = {
        handsPlayed: 0,
        vpipCount: 0,
        pfrCount: 0,
        preflopCalls: 0,
        preflopRaises: 0,
        preflopFolds: 0,
        postflopBets: 0,
        postflopRaises: 0,
        postflopCalls: 0,
        postflopFolds: 0,
        postflopChecks: 0,
        wentToShowdown: 0,
        wonAtShowdown: 0,
        handsWon: 0,
        facedThreeBet: 0,
        foldedToThreeBet: 0,
        cbetOpportunities: 0,
        cbetMade: 0,
        stacks: [],
        showdownHands: [],
      };
    }
  }

  for (const hand of hands) {
    const playerNames = Object.keys(hand.players);

    for (const name of playerNames) {
      if (name === HERO) continue;
      ensure(name);
      const s = stats[name];
      s.handsPlayed++;
      s.stacks.push(hand.players[name].stack);

      // Determine if player was in blinds
      const isBB = hand.blindPlayers[name] === "bb";
      const isSB = hand.blindPlayers[name] === "sb";

      // Preflop actions for this player
      const preflopActions = hand.phases.preflop.filter(a => a.player === name);

      // VPIP: voluntarily put money in pot (call or raise preflop, not counting BB check)
      let vpip = false;
      let pfr = false;
      for (const a of preflopActions) {
        if (a.action === "calls") vpip = true;
        if (a.action === "raises") { vpip = true; pfr = true; }
      }
      // BB who just checks is NOT vpip
      if (isBB && preflopActions.length === 1 && preflopActions[0].action === "checks") {
        vpip = false;
      }
      if (vpip) s.vpipCount++;
      if (pfr) s.pfrCount++;

      // Count preflop action types
      for (const a of preflopActions) {
        if (a.action === "calls") s.preflopCalls++;
        if (a.action === "raises") s.preflopRaises++;
        if (a.action === "folds") s.preflopFolds++;
      }

      // 3-bet detection: if someone raised, then another raised,
      // and then this player faced that second raise
      const allPreflop = hand.phases.preflop;
      let raiseCount = 0;
      for (const a of allPreflop) {
        if (a.action === "raises") {
          raiseCount++;
          if (raiseCount >= 2) {
            // The player who faced this 3-bet is the original raiser
            // Actually, let's track if THIS player faced a 3-bet
          }
        }
      }
      // Simplified: if player raised and then someone re-raised after
      let playerRaiseIdx = -1;
      for (let i = 0; i < allPreflop.length; i++) {
        if (allPreflop[i].player === name && allPreflop[i].action === "raises") {
          playerRaiseIdx = i;
          break;
        }
      }
      if (playerRaiseIdx >= 0) {
        // Check if someone raised after this player's raise
        for (let i = playerRaiseIdx + 1; i < allPreflop.length; i++) {
          if (allPreflop[i].action === "raises" && allPreflop[i].player !== name) {
            s.facedThreeBet++;
            // Did player fold to it?
            for (let j = i + 1; j < allPreflop.length; j++) {
              if (allPreflop[j].player === name) {
                if (allPreflop[j].action === "folds") s.foldedToThreeBet++;
                break;
              }
            }
            break;
          }
        }
      }

      // C-bet: player raised preflop AND was first to act (or checked to) on flop
      if (pfr && hand.phases.flop.length > 0) {
        // Check if player had opportunity to cbet
        // Find first action on flop that's a bet/check by this player or someone else
        let hadCbetOpportunity = false;
        for (const a of hand.phases.flop) {
          if (a.player === name) {
            hadCbetOpportunity = true;
            s.cbetOpportunities++;
            if (a.action === "bets") s.cbetMade++;
            break;
          }
          // If someone else bet before player could act, no cbet opportunity
          if (a.action === "bets") break;
        }
      }

      // Postflop actions
      for (const phase of ["flop", "turn", "river"]) {
        for (const a of hand.phases[phase]) {
          if (a.player !== name) continue;
          if (a.action === "bets") s.postflopBets++;
          if (a.action === "raises") s.postflopRaises++;
          if (a.action === "calls") s.postflopCalls++;
          if (a.action === "folds") s.postflopFolds++;
          if (a.action === "checks") s.postflopChecks++;
        }
      }

      // Showdown
      if (hand.wentToShowdown.has(name)) {
        s.wentToShowdown++;
        const isWinner = hand.winners.some(w => w.player === name);
        if (isWinner) s.wonAtShowdown++;
        const shown = hand.showdown.find(sh => sh.player === name);
        if (shown) s.showdownHands.push(shown.cards);
      }

      // Won hand
      if (hand.winners.some(w => w.player === name)) {
        s.handsWon++;
      }
    }
  }

  return stats;
}

// ── Calculate derived stats & classify ──────────────────────────────────

function computeProfiles(stats) {
  const profiles = {};

  for (const [name, s] of Object.entries(stats)) {
    const vpip = s.handsPlayed > 0 ? (s.vpipCount / s.handsPlayed * 100) : 0;
    const pfr = s.handsPlayed > 0 ? (s.pfrCount / s.handsPlayed * 100) : 0;

    const totalAggressive = s.postflopBets + s.postflopRaises;
    const af = s.postflopCalls > 0 ? totalAggressive / s.postflopCalls : totalAggressive > 0 ? 99 : 0;

    const wtsd = s.handsPlayed > 0 ? (s.wentToShowdown / s.handsPlayed * 100) : 0;
    const wsd = s.wentToShowdown > 0 ? (s.wonAtShowdown / s.wentToShowdown * 100) : 0;

    const foldTo3bet = s.facedThreeBet > 0 ? (s.foldedToThreeBet / s.facedThreeBet * 100) : null;
    const cbetFreq = s.cbetOpportunities > 0 ? (s.cbetMade / s.cbetOpportunities * 100) : null;

    const avgStack = s.stacks.length > 0 ? s.stacks.reduce((a, b) => a + b, 0) / s.stacks.length : 0;

    // Classification
    let classification;
    if (vpip > 50 && af < 1.5) {
      classification = "fish";
    } else if (vpip > 40 && pfr > 25 && af >= 2) {
      classification = "lag";
    } else if (vpip > 55 && pfr > 35 && af >= 3) {
      classification = "maniac";
    } else if (vpip < 18 && pfr < 12) {
      classification = "nit";
    } else if (vpip >= 18 && vpip <= 28 && pfr >= 15 && af >= 1.5) {
      classification = "tag";
    } else if (vpip >= 28 && vpip <= 40 && pfr >= 18 && af >= 2) {
      classification = "lag";
    } else if (vpip > 35 && af < 1.2) {
      classification = "fish";
    } else if (vpip <= 22 && pfr <= 15) {
      classification = "nit";
    } else if (vpip > 30) {
      classification = af >= 1.5 ? "lag" : "fish";
    } else {
      classification = "reg";
    }

    profiles[name] = {
      name,
      handsPlayed: s.handsPlayed,
      vpip: Math.round(vpip * 10) / 10,
      pfr: Math.round(pfr * 10) / 10,
      aggressionFactor: Math.round(af * 100) / 100,
      wtsd: Math.round(wtsd * 10) / 10,
      wsd: Math.round(wsd * 10) / 10,
      foldTo3bet: foldTo3bet !== null ? Math.round(foldTo3bet * 10) / 10 : null,
      cbetFrequency: cbetFreq !== null ? Math.round(cbetFreq * 10) / 10 : null,
      avgStack: Math.round(avgStack * 100) / 100,
      handsWon: s.handsWon,
      winRate: Math.round((s.handsWon / s.handsPlayed * 100) * 10) / 10,
      showdownHands: s.showdownHands.slice(0, 10),  // keep up to 10 examples
      classification,
      // Raw counts for debugging
      _raw: {
        vpipCount: s.vpipCount,
        pfrCount: s.pfrCount,
        preflopCalls: s.preflopCalls,
        preflopRaises: s.preflopRaises,
        preflopFolds: s.preflopFolds,
        postflopBets: s.postflopBets,
        postflopRaises: s.postflopRaises,
        postflopCalls: s.postflopCalls,
        postflopFolds: s.postflopFolds,
        postflopChecks: s.postflopChecks,
        wentToShowdown: s.wentToShowdown,
        wonAtShowdown: s.wonAtShowdown,
        facedThreeBet: s.facedThreeBet,
        foldedToThreeBet: s.foldedToThreeBet,
        cbetOpportunities: s.cbetOpportunities,
        cbetMade: s.cbetMade,
      },
    };
  }

  return profiles;
}

// ── Print report ────────────────────────────────────────────────────────

function printReport(profiles) {
  console.log("=".repeat(80));
  console.log("  OPPONENT PROFILING REPORT  —  PokerStars Hand History Analysis");
  console.log("=".repeat(80));

  const sorted = Object.values(profiles).sort((a, b) => b.handsPlayed - a.handsPlayed);

  console.log(`\nTotal opponents found: ${sorted.length}`);
  console.log(`Opponents with 10+ hands: ${sorted.filter(p => p.handsPlayed >= 10).length}\n`);

  // Quick summary table
  console.log("─".repeat(80));
  console.log(
    "Player".padEnd(22) +
    "Hands".padStart(6) +
    "VPIP".padStart(7) +
    "PFR".padStart(7) +
    "AF".padStart(6) +
    "WTSD".padStart(7) +
    "W$SD".padStart(7) +
    "Type".padStart(10)
  );
  console.log("─".repeat(80));
  for (const p of sorted) {
    if (p.handsPlayed < 3) continue;
    console.log(
      p.name.padEnd(22) +
      String(p.handsPlayed).padStart(6) +
      (p.vpip + "%").padStart(7) +
      (p.pfr + "%").padStart(7) +
      String(p.aggressionFactor).padStart(6) +
      (p.wtsd + "%").padStart(7) +
      (p.wsd + "%").padStart(7) +
      p.classification.toUpperCase().padStart(10)
    );
  }
  console.log("─".repeat(80));

  // Detailed profiles for 10+ hands
  console.log("\n" + "=".repeat(80));
  console.log("  DETAILED PROFILES (10+ hands)");
  console.log("=".repeat(80));

  for (const p of sorted) {
    if (p.handsPlayed < 10) continue;

    console.log(`\n${"─".repeat(60)}`);
    console.log(`  ${p.name}  [${p.classification.toUpperCase()}]`);
    console.log(`${"─".repeat(60)}`);
    console.log(`  Hands played:     ${p.handsPlayed}`);
    console.log(`  Hands won:        ${p.handsWon} (${p.winRate}%)`);
    console.log(`  Avg stack:        $${p.avgStack.toFixed(2)}`);
    console.log();
    console.log(`  VPIP:             ${p.vpip}%  (${p._raw.vpipCount}/${p.handsPlayed})`);
    console.log(`  PFR:              ${p.pfr}%  (${p._raw.pfrCount}/${p.handsPlayed})`);
    console.log(`  Aggression Factor:${p.aggressionFactor}  (bets+raises: ${p._raw.postflopBets + p._raw.postflopRaises}, calls: ${p._raw.postflopCalls})`);
    console.log(`  WTSD:             ${p.wtsd}%  (${p._raw.wentToShowdown}/${p.handsPlayed})`);
    console.log(`  W$SD:             ${p.wsd}%  (${p._raw.wonAtShowdown}/${p._raw.wentToShowdown})`);
    if (p.foldTo3bet !== null) {
      console.log(`  Fold to 3-bet:    ${p.foldTo3bet}%  (${p._raw.foldedToThreeBet}/${p._raw.facedThreeBet})`);
    } else {
      console.log(`  Fold to 3-bet:    N/A (never faced)`);
    }
    if (p.cbetFrequency !== null) {
      console.log(`  C-bet frequency:  ${p.cbetFrequency}%  (${p._raw.cbetMade}/${p._raw.cbetOpportunities})`);
    } else {
      console.log(`  C-bet frequency:  N/A (no opportunities)`);
    }

    console.log();
    console.log(`  Preflop tendencies: ${p._raw.preflopRaises} raises, ${p._raw.preflopCalls} calls, ${p._raw.preflopFolds} folds`);
    console.log(`  Postflop tendencies: ${p._raw.postflopBets} bets, ${p._raw.postflopRaises} raises, ${p._raw.postflopCalls} calls, ${p._raw.postflopChecks} checks, ${p._raw.postflopFolds} folds`);

    if (p.showdownHands.length > 0) {
      console.log(`  Showdown hands:   ${p.showdownHands.join(" | ")}`);
    }

    // Play style analysis
    console.log();
    console.log(`  PLAY STYLE ANALYSIS:`);
    if (p.classification === "fish") {
      console.log(`    - Loose passive player. Calls too much preflop (VPIP ${p.vpip}%) without raising enough.`);
      console.log(`    - Low aggression factor (${p.aggressionFactor}) indicates check-call tendency.`);
      console.log(`    - Exploit by value betting thin and reducing bluffs.`);
    } else if (p.classification === "tag") {
      console.log(`    - Tight aggressive player. Selective preflop (VPIP ${p.vpip}%) but aggressive when entering.`);
      console.log(`    - PFR/VPIP ratio shows ${p.pfr > 0 ? Math.round(p.pfr / p.vpip * 100) : 0}% of entries are raises.`);
      console.log(`    - Respect their raises, widen your 3-bet range vs them, and avoid paying off big bets.`);
    } else if (p.classification === "lag") {
      console.log(`    - Loose aggressive player. Wide range (VPIP ${p.vpip}%) with high aggression (AF ${p.aggressionFactor}).`);
      console.log(`    - Puts pressure with bets/raises. Bluff frequency is higher than average.`);
      console.log(`    - Counter by trapping with strong hands and calling down lighter.`);
    } else if (p.classification === "nit") {
      console.log(`    - Very tight player. Only enters with premium hands (VPIP ${p.vpip}%).`);
      console.log(`    - When they bet/raise, give them credit for a strong hand.`);
      console.log(`    - Steal their blinds relentlessly and fold to their aggression.`);
    } else if (p.classification === "maniac") {
      console.log(`    - Extremely loose and aggressive. Raises constantly (PFR ${p.pfr}%).`);
      console.log(`    - High variance player who will give away chips with weak hands.`);
      console.log(`    - Be patient, wait for strong hands, and let them bluff into you.`);
    } else {
      console.log(`    - Regular player. Balanced between tight and loose (VPIP ${p.vpip}%, PFR ${p.pfr}%).`);
      console.log(`    - Standard strategy applies; look for specific leaks in postflop lines.`);
    }
  }

  console.log("\n" + "=".repeat(80));
  console.log("  BOT PERSONALITIES CREATED");
  console.log("=".repeat(80));
  const botCandidates = sorted.filter(p => p.handsPlayed >= 10);
  for (const p of botCandidates) {
    console.log(`  ${p.name} -> ${p.classification.toUpperCase()} bot (VPIP ${p.vpip}%, PFR ${p.pfr}%, AF ${p.aggressionFactor})`);
  }
  if (botCandidates.length < 5) {
    const extras = sorted.filter(p => p.handsPlayed >= 5 && p.handsPlayed < 10);
    for (const p of extras) {
      console.log(`  ${p.name} -> ${p.classification.toUpperCase()} bot (${p.handsPlayed} hands — small sample)`);
    }
  }
  console.log();
}

// ── Main ────────────────────────────────────────────────────────────────

const allHands = parseAllFiles();
console.log(`Parsed ${allHands.length} hand history blocks from ${HANDS_DIR}`);

const parsedHands = allHands.map(parseHand);
console.log(`Successfully parsed ${parsedHands.length} hands`);

const rawStats = buildStats(parsedHands);
const profiles = computeProfiles(rawStats);

// Write profiles JSON
const profilesForFile = {};
for (const [name, p] of Object.entries(profiles)) {
  // Strip _raw for the JSON file, keep clean stats
  const { _raw, ...clean } = p;
  profilesForFile[name] = clean;
}
fs.writeFileSync(OUTPUT_FILE, JSON.stringify(profilesForFile, null, 2));
console.log(`\nWrote ${Object.keys(profilesForFile).length} opponent profiles to ${OUTPUT_FILE}\n`);

printReport(profiles);
