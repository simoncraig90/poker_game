#!/usr/bin/env node
"use strict";

/**
 * Population-based player profiling.
 *
 * Builds archetype clusters from known opponents, then classifies new
 * players after just 10-15 hands. Returns strategy adjustments for the
 * CFR solver and advisor.
 *
 * Archetypes:
 *   FISH  — high VPIP, low PFR, passive (calls too much, rarely raises)
 *   NIT   — low VPIP, low PFR (plays very few hands, folds to aggression)
 *   TAG   — moderate VPIP, moderate-high PFR (solid, aggressive)
 *   LAG   — high VPIP, high PFR (loose-aggressive, lots of raises)
 *   WHALE — very high VPIP, very low PFR (calls everything, never folds)
 *
 * Usage:
 *   const profiler = require('./player-profiler');
 *   profiler.buildClusters();  // from opponent-profiles.json
 *   const type = profiler.classify({ vpip: 45, pfr: 5, af: 0.5, hands: 12 });
 *   const adj = profiler.getAdjustments(type);
 */

const fs = require("fs");
const path = require("path");

// ── Archetype definitions (cluster centroids) ───────────────────────────

// Seed centroids from poker theory, refined by actual data
const ARCHETYPES = {
  FISH:  { vpip: 42, pfr: 8,  af: 0.8, label: "FISH",  color: "#4fc3f7" },
  NIT:   { vpip: 14, pfr: 8,  af: 1.0, label: "NIT",   color: "#9e9e9e" },
  TAG:   { vpip: 22, pfr: 17, af: 2.5, label: "TAG",   color: "#66bb6a" },
  LAG:   { vpip: 30, pfr: 26, af: 3.0, label: "LAG",   color: "#ffa726" },
  WHALE: { vpip: 55, pfr: 3,  af: 0.3, label: "WHALE", color: "#ef5350" },
};

// ── Classification ──────────────────────────────────────────────────────

/**
 * Classify a player into an archetype based on their stats.
 * Uses weighted Euclidean distance to cluster centroids.
 *
 * @param {Object} stats - { vpip, pfr, af, hands }
 * @returns {string} archetype label
 */
function classify(stats) {
  const vpip = stats.vpip || 0;
  const pfr = stats.pfr || 0;
  const af = Math.min(stats.af || 0, 10); // cap AF to avoid outlier distortion

  // Quick rules for low-sample players (< 15 hands)
  if ((stats.hands || 0) < 8) return "UNKNOWN";

  let bestType = "TAG"; // default
  let bestDist = Infinity;

  for (const [type, centroid] of Object.entries(ARCHETYPES)) {
    // Weighted distance: VPIP and PFR matter most
    const dv = (vpip - centroid.vpip) / 15;  // normalize by typical spread
    const dp = (pfr - centroid.pfr) / 12;
    const da = (af - centroid.af) / 2;
    const dist = dv * dv * 2 + dp * dp * 1.5 + da * da * 0.5;

    if (dist < bestDist) {
      bestDist = dist;
      bestType = type;
    }
  }

  return bestType;
}

/**
 * Get strategy adjustments for playing against a given archetype.
 * Returns multipliers to apply to CFR strategy probabilities.
 *
 * @param {string} type - archetype label
 * @returns {Object} adjustments
 */
function getAdjustments(type) {
  switch (type) {
    case "FISH":
      return {
        description: "Value bet wide, never bluff, size up",
        foldMult: 0.3,      // fold much less (they give free money)
        callMult: 0.7,      // call less (bet instead)
        betMult: 1.8,       // bet much more (they call)
        raiseMult: 1.5,     // raise more for value
        bluffFreq: 0.1,     // almost never bluff
        valueBetFreq: 0.9,  // value bet relentlessly
        sizingMult: 1.3,    // bet bigger (they call regardless)
      };
    case "WHALE":
      return {
        description: "Max value, zero bluffs, pot-size bets",
        foldMult: 0.1,
        callMult: 0.5,
        betMult: 2.0,
        raiseMult: 2.0,
        bluffFreq: 0.0,
        valueBetFreq: 1.0,
        sizingMult: 1.5,
      };
    case "NIT":
      return {
        description: "Steal blinds, fold to aggression, don't pay off",
        foldMult: 1.2,      // fold more to their bets (they have it)
        callMult: 0.6,      // call less
        betMult: 1.4,       // bet more (they fold)
        raiseMult: 0.8,     // raise less (they only continue with nuts)
        bluffFreq: 0.6,     // bluff often (they fold)
        valueBetFreq: 0.4,  // don't value bet thin (they only call with good hands)
        sizingMult: 0.7,    // smaller bets (don't need big to make them fold)
      };
    case "LAG":
      return {
        description: "Trap more, widen call range, let them bluff",
        foldMult: 0.8,      // fold less (they bluff a lot)
        callMult: 1.5,      // call more (catch bluffs)
        betMult: 0.7,       // bet less (let them bet)
        raiseMult: 1.2,     // raise their bluffs
        bluffFreq: 0.3,     // bluff less (they re-raise)
        valueBetFreq: 0.6,
        sizingMult: 1.0,
      };
    case "TAG":
      return {
        description: "Play GTO, small edges",
        foldMult: 1.0,
        callMult: 1.0,
        betMult: 1.0,
        raiseMult: 1.0,
        bluffFreq: 0.5,
        valueBetFreq: 0.5,
        sizingMult: 1.0,
      };
    default: // UNKNOWN
      return {
        description: "Default GTO until classified",
        foldMult: 1.0,
        callMult: 1.0,
        betMult: 1.0,
        raiseMult: 1.0,
        bluffFreq: 0.5,
        valueBetFreq: 0.5,
        sizingMult: 1.0,
      };
  }
}

/**
 * Apply adjustments to a CFR strategy (action probabilities).
 *
 * @param {Object} strategy - { FOLD: 0.3, CHECK: 0.2, CALL: 0.2, BET: 0.2, RAISE: 0.1 }
 * @param {Object} adj - from getAdjustments()
 * @returns {Object} adjusted strategy (normalized to sum to 1)
 */
function adjustStrategy(strategy, adj) {
  const adjusted = {};
  for (const [action, prob] of Object.entries(strategy)) {
    let mult = 1.0;
    if (action === "FOLD") mult = adj.foldMult;
    else if (action === "CALL") mult = adj.callMult;
    else if (action === "CHECK") mult = adj.callMult; // check treated like passive
    else if (action.startsWith("BET")) mult = adj.betMult;
    else if (action.startsWith("RAISE")) mult = adj.raiseMult;
    adjusted[action] = prob * mult;
  }

  // Normalize
  const total = Object.values(adjusted).reduce((s, v) => s + v, 0);
  if (total > 0) {
    for (const key of Object.keys(adjusted)) {
      adjusted[key] /= total;
    }
  }
  return adjusted;
}

// ── Live tracker ────────────────────────────────────────────────────────

/**
 * Track opponent actions during a live session.
 * Call update() with each observed action to build stats incrementally.
 */
class LiveTracker {
  constructor() {
    this.players = {}; // name -> { hands, vpipHands, pfrHands, bets, calls, checks, folds }
  }

  /**
   * Record that a player was dealt into a hand.
   */
  newHand(playerName) {
    if (!this.players[playerName]) {
      this.players[playerName] = {
        hands: 0, vpipHands: 0, pfrHands: 0,
        bets: 0, raises: 0, calls: 0, checks: 0, folds: 0,
        _thisHandVpip: false, _thisHandPfr: false,
      };
    }
    const p = this.players[playerName];
    p.hands++;
    p._thisHandVpip = false;
    p._thisHandPfr = false;
  }

  /**
   * Record a player action.
   */
  action(playerName, action, isPreflop) {
    const p = this.players[playerName];
    if (!p) return;

    if (action === "CALL") {
      p.calls++;
      if (isPreflop && !p._thisHandVpip) { p.vpipHands++; p._thisHandVpip = true; }
    } else if (action === "RAISE" || action === "BET") {
      if (action === "RAISE") p.raises++; else p.bets++;
      if (isPreflop) {
        if (!p._thisHandVpip) { p.vpipHands++; p._thisHandVpip = true; }
        if (!p._thisHandPfr) { p.pfrHands++; p._thisHandPfr = true; }
      }
    } else if (action === "CHECK") {
      p.checks++;
    } else if (action === "FOLD") {
      p.folds++;
    }
  }

  /**
   * Get current stats for a player.
   */
  getStats(playerName) {
    const p = this.players[playerName];
    if (!p || p.hands === 0) return null;
    const vpip = (p.vpipHands / p.hands) * 100;
    const pfr = (p.pfrHands / p.hands) * 100;
    const passiveActions = p.calls + p.checks;
    const af = passiveActions > 0 ? (p.bets + p.raises) / passiveActions : 0;
    return { vpip, pfr, af, hands: p.hands };
  }

  /**
   * Classify a player based on accumulated stats.
   */
  classifyPlayer(playerName) {
    const stats = this.getStats(playerName);
    if (!stats) return "UNKNOWN";
    return classify(stats);
  }

  /**
   * Get summary of all tracked players.
   */
  summary() {
    const result = [];
    for (const [name, p] of Object.entries(this.players)) {
      const stats = this.getStats(name);
      if (!stats) continue;
      const type = classify(stats);
      result.push({ name, ...stats, type });
    }
    return result.sort((a, b) => b.hands - a.hands);
  }
}

// ── Build clusters from existing data ───────────────────────────────────

function buildClusters() {
  const profilesPath = path.join(__dirname, "opponent-profiles.json");
  if (!fs.existsSync(profilesPath)) {
    console.log("No profiles found");
    return;
  }
  const profiles = JSON.parse(fs.readFileSync(profilesPath, "utf8"));
  const players = Object.values(profiles).filter(p => p.handsPlayed >= 10);

  console.log(`\nClassifying ${players.length} opponents:\n`);

  const counts = {};
  for (const p of players) {
    const type = classify({
      vpip: p.vpip, pfr: p.pfr,
      af: p.aggressionFactor || 0,
      hands: p.handsPlayed,
    });
    counts[type] = (counts[type] || 0) + 1;
    if (p.handsPlayed >= 30) {
      const adj = getAdjustments(type);
      console.log(`  ${p.name.padEnd(20)} ${type.padEnd(6)} VPIP:${p.vpip}% PFR:${p.pfr}% — ${adj.description}`);
    }
  }

  console.log(`\nDistribution:`);
  for (const [type, count] of Object.entries(counts).sort((a, b) => b[1] - a[1])) {
    const pct = ((count / players.length) * 100).toFixed(0);
    console.log(`  ${type.padEnd(8)} ${count} players (${pct}%)`);
  }
}

// ── CLI ─────────────────────────────────────────────────────────────────

if (require.main === module) {
  buildClusters();
}

module.exports = {
  classify,
  getAdjustments,
  adjustStrategy,
  LiveTracker,
  ARCHETYPES,
  buildClusters,
};
