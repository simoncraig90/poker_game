#!/usr/bin/env node
"use strict";

/**
 * Humanness Scoring Framework
 *
 * Analyzes bot action logs and scores how human-like the behavior is
 * across 4 dimensions: Timing, Motor, Behavioral, Strategic.
 *
 * Each dimension scores 0-100 (100 = perfectly human).
 * Composite score is weighted average.
 *
 * Usage:
 *   node scripts/humanness-score.js                              # score latest log
 *   node scripts/humanness-score.js --log vision/data/bot_action_log.json
 *   node scripts/humanness-score.js --compare log1.json log2.json
 */

const fs = require("fs");
const path = require("path");

const DEFAULT_LOG = path.join(__dirname, "..", "vision", "data", "bot_action_log.json");

// ── Math Helpers ───────────────────────────────────────────────────────

function mean(arr) { return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0; }
function stdev(arr) {
  if (arr.length < 2) return 0;
  const m = mean(arr);
  return Math.sqrt(arr.reduce((s, x) => s + (x - m) ** 2, 0) / (arr.length - 1));
}
function median(arr) {
  const s = arr.slice().sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}
function entropy(arr) {
  // Shannon entropy of a discretized distribution
  const counts = {};
  for (const x of arr) counts[x] = (counts[x] || 0) + 1;
  const total = arr.length;
  let h = 0;
  for (const c of Object.values(counts)) {
    const p = c / total;
    if (p > 0) h -= p * Math.log2(p);
  }
  return h;
}
function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }
function score(value, ideal, tolerance) {
  // Score 0-100 based on how close value is to ideal within tolerance
  const diff = Math.abs(value - ideal);
  return clamp(100 * (1 - diff / tolerance), 0, 100);
}

// ── Timing Dimension (0-100) ───────────────────────────────────────────

function scoreTiming(actions) {
  if (actions.length < 5) return { score: 50, details: {} };

  const thinkTimes = actions.map(a => a.think_time);
  const totalTimes = actions.map(a => a.total_time);

  // 1. Think time entropy (discretize to 0.5s bins)
  // Humans: high entropy (varied timing). Bots: low entropy (consistent)
  const timeBins = thinkTimes.map(t => Math.round(t * 2) / 2); // 0.5s bins
  const timeEntropy = entropy(timeBins);
  // Human range: 2.5-4.0 bits. Bot: <1.5 bits
  const entropyScore = score(timeEntropy, 3.2, 2.0);

  // 2. Coefficient of variation (stdev/mean)
  // Humans: CV ~0.5-0.8. Bots: CV <0.2 or >1.5 (too consistent or random)
  const cv = stdev(thinkTimes) / Math.max(mean(thinkTimes), 0.01);
  const cvScore = score(cv, 0.6, 0.5);

  // 3. Autocorrelation (do consecutive decisions have related timing?)
  // Humans show some autocorrelation (streaks of fast/slow play)
  // Bots: zero autocorrelation (each decision independent)
  let autoCorr = 0;
  if (thinkTimes.length > 2) {
    const m = mean(thinkTimes);
    let num = 0, den = 0;
    for (let i = 0; i < thinkTimes.length - 1; i++) {
      num += (thinkTimes[i] - m) * (thinkTimes[i + 1] - m);
      den += (thinkTimes[i] - m) ** 2;
    }
    autoCorr = den > 0 ? num / den : 0;
  }
  // Humans: autocorr ~0.1-0.3. Bots: ~0 or negative
  const autoCorrScore = score(autoCorr, 0.2, 0.3);

  // 4. Multi-modality: are there distinct fast/slow clusters?
  // Check if the distribution has more than one peak
  const fast = thinkTimes.filter(t => t < 1.0).length / thinkTimes.length;
  const medium = thinkTimes.filter(t => t >= 1.0 && t < 2.5).length / thinkTimes.length;
  const slow = thinkTimes.filter(t => t >= 2.5).length / thinkTimes.length;
  // Humans have all three clusters. Bots tend to cluster in one.
  const clusterCount = [fast, medium, slow].filter(p => p > 0.1).length;
  const multiModalScore = clusterCount >= 3 ? 100 : clusterCount === 2 ? 60 : 20;

  const finalScore = Math.round(
    entropyScore * 0.3 + cvScore * 0.25 + autoCorrScore * 0.2 + multiModalScore * 0.25
  );

  return {
    score: finalScore,
    details: {
      entropy: { value: +timeEntropy.toFixed(2), score: Math.round(entropyScore) },
      cv: { value: +cv.toFixed(3), score: Math.round(cvScore) },
      autocorrelation: { value: +autoCorr.toFixed(3), score: Math.round(autoCorrScore) },
      multimodal: { clusters: clusterCount, score: multiModalScore },
      mean_think: +mean(thinkTimes).toFixed(3),
      stdev_think: +stdev(thinkTimes).toFixed(3),
    },
  };
}

// ── Motor Dimension (0-100) ────────────────────────────────────────────

function scoreMotor(actions) {
  if (actions.length < 5) return { score: 50, details: {} };

  // 1. Click offset variance
  // Humans: varied offsets (stdev 3-8px). Bots: very consistent or zero
  const offsetsX = actions.map(a => a.click_offset_x);
  const offsetsY = actions.map(a => a.click_offset_y);
  const offsetStdX = stdev(offsetsX);
  const offsetStdY = stdev(offsetsY);
  const avgOffsetStd = (offsetStdX + offsetStdY) / 2;
  // Human ideal: 4-6px stdev. Bot: <1px or >10px
  const offsetScore = score(avgOffsetStd, 5, 4);

  // 2. Click offset distribution shape
  // Humans: roughly normal. Bots: uniform or constant
  const offsetEntropy = entropy(offsetsX.map(x => Math.round(x)));
  // Human: 2.5-4 bits. Bot: <1.5
  const offsetEntropyScore = score(offsetEntropy, 3.0, 2.0);

  // 3. Move time variance
  // Humans: varied (0.05-0.3s). Bots: constant
  const moveTimes = actions.map(a => a.move_time);
  const moveCV = stdev(moveTimes) / Math.max(mean(moveTimes), 0.01);
  // Human: CV ~0.3-0.5. Bot: <0.1
  const moveScore = score(moveCV, 0.4, 0.3);

  // 4. Hesitation variance
  const hesitations = actions.map(a => a.hesitation);
  const hesCV = stdev(hesitations) / Math.max(mean(hesitations), 0.01);
  const hesScore = score(hesCV, 0.4, 0.3);

  // 5. No idle movement penalty (we can't measure this from click logs alone)
  // Placeholder: 0 if no idle data
  const idleScore = 0; // TODO: requires mouse position tracking between clicks

  const finalScore = Math.round(
    offsetScore * 0.3 + offsetEntropyScore * 0.2 + moveScore * 0.2 + hesScore * 0.2 + idleScore * 0.1
  );

  return {
    score: finalScore,
    details: {
      click_offset_std: { x: +offsetStdX.toFixed(1), y: +offsetStdY.toFixed(1), score: Math.round(offsetScore) },
      offset_entropy: { value: +offsetEntropy.toFixed(2), score: Math.round(offsetEntropyScore) },
      move_cv: { value: +moveCV.toFixed(3), score: Math.round(moveScore) },
      hesitation_cv: { value: +hesCV.toFixed(3), score: Math.round(hesScore) },
      idle_movement: { available: false, score: 0 },
    },
  };
}

// ── Behavioral Dimension (0-100) ───────────────────────────────────────

function scoreBehavioral(actions) {
  if (actions.length < 10) return { score: 50, details: {} };

  // 1. Session timing drift (fatigue)
  // Split session into thirds, compare think times
  const third = Math.floor(actions.length / 3);
  const early = actions.slice(0, third).map(a => a.think_time);
  const late = actions.slice(-third).map(a => a.think_time);
  const driftRatio = mean(late) / Math.max(mean(early), 0.01);
  // Humans: drift ratio 1.1-1.4 (slower over time). Bots: ~1.0
  const driftScore = score(driftRatio, 1.2, 0.3);

  // 2. Action variety over session
  // Count distinct actions used
  const actionTypes = new Set(actions.map(a => a.action));
  // Humans use 3-5 different actions. Bots may use only 1-2
  const varietyScore = score(actionTypes.size, 4, 2);

  // 3. Action distribution shift across session
  // Compare first half vs second half action frequencies
  const halfIdx = Math.floor(actions.length / 2);
  const firstHalf = actions.slice(0, halfIdx);
  const secondHalf = actions.slice(halfIdx);
  const freq1 = {};
  const freq2 = {};
  for (const a of firstHalf) freq1[a.action] = (freq1[a.action] || 0) + 1;
  for (const a of secondHalf) freq2[a.action] = (freq2[a.action] || 0) + 1;
  // Normalize
  const total1 = firstHalf.length || 1;
  const total2 = secondHalf.length || 1;
  let freqDiff = 0;
  const allActions = new Set([...Object.keys(freq1), ...Object.keys(freq2)]);
  for (const a of allActions) {
    freqDiff += Math.abs((freq1[a] || 0) / total1 - (freq2[a] || 0) / total2);
  }
  // Humans: shift ~0.1-0.3. Bots: ~0 (perfectly stable)
  const shiftScore = score(freqDiff, 0.15, 0.2);

  // 4. Gap between actions variance (includes between-hand waits)
  const gaps = [];
  for (let i = 1; i < actions.length; i++) {
    gaps.push(actions[i].timestamp - actions[i - 1].timestamp);
  }
  const gapCV = gaps.length > 1 ? stdev(gaps) / Math.max(mean(gaps), 0.01) : 0;
  // Humans: high gap CV (fast within hand, long waits between). ~0.8-1.5
  const gapScore = score(gapCV, 1.0, 0.7);

  const finalScore = Math.round(
    driftScore * 0.25 + varietyScore * 0.2 + shiftScore * 0.25 + gapScore * 0.3
  );

  return {
    score: finalScore,
    details: {
      fatigue_drift: { ratio: +driftRatio.toFixed(3), score: Math.round(driftScore) },
      action_variety: { types: actionTypes.size, score: Math.round(varietyScore) },
      session_shift: { diff: +freqDiff.toFixed(3), score: Math.round(shiftScore) },
      gap_cv: { value: +gapCV.toFixed(3), score: Math.round(gapScore) },
    },
  };
}

// ── Strategic Dimension (0-100) ────────────────────────────────────────

function scoreStrategic(actions) {
  if (actions.length < 10) return { score: 50, details: {} };

  // 1. Action distribution realism
  // Realistic 6-max: ~35-55% fold, 20-35% check, 10-20% call, 5-15% bet/raise
  const counts = {};
  for (const a of actions) counts[a.action] = (counts[a.action] || 0) + 1;
  const total = actions.length;
  const foldPct = (counts["FOLD"] || 0) / total;
  const checkPct = (counts["CHECK_CALL"] || 0) / total;

  // Check if distribution is realistic (not all one action)
  const dominantPct = Math.max(...Object.values(counts)) / total;
  // Humans: dominant action 30-60%. Bots: often >80%
  const balanceScore = score(dominantPct, 0.45, 0.25);

  // 2. Action entropy
  const actionEntropy = entropy(actions.map(a => a.action));
  // Humans: 1.5-2.5 bits. Bots: <1.0 (predictable)
  const actionEntropyScore = score(actionEntropy, 2.0, 1.2);

  // 3. Bet sizing (if we had it — placeholder from action types)
  // For now, score based on whether bet/raise actions exist
  const hasBets = counts["BET_RAISE"] || 0;
  const betRatio = hasBets / total;
  // Humans bet/raise 10-25% of the time
  const betScore = score(betRatio, 0.15, 0.12);

  const finalScore = Math.round(
    balanceScore * 0.35 + actionEntropyScore * 0.35 + betScore * 0.3
  );

  return {
    score: finalScore,
    details: {
      action_balance: { dominant_pct: +(dominantPct * 100).toFixed(1), score: Math.round(balanceScore) },
      action_entropy: { value: +actionEntropy.toFixed(2), score: Math.round(actionEntropyScore) },
      bet_ratio: { value: +(betRatio * 100).toFixed(1), score: Math.round(betScore) },
      distribution: Object.fromEntries(
        Object.entries(counts).map(([k, v]) => [k, +((v / total) * 100).toFixed(1)])
      ),
    },
  };
}

// ── Composite Score ────────────────────────────────────────────────────

function scoreHumanness(logData) {
  const actions = logData.actions || [];

  const timing = scoreTiming(actions);
  const motor = scoreMotor(actions);
  const behavioral = scoreBehavioral(actions);
  const strategic = scoreStrategic(actions);

  // Weighted composite
  const composite = Math.round(
    timing.score * 0.30 +
    motor.score * 0.20 +
    behavioral.score * 0.25 +
    strategic.score * 0.25
  );

  return {
    composite,
    dimensions: {
      timing: timing.score,
      motor: motor.score,
      behavioral: behavioral.score,
      strategic: strategic.score,
    },
    details: {
      timing: timing.details,
      motor: motor.details,
      behavioral: behavioral.details,
      strategic: strategic.details,
    },
    meta: {
      total_actions: actions.length,
      session_duration: logData.session_duration,
    },
  };
}

// ── CLI ────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
let logPaths = [];
let compareMode = false;

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--log" && args[i + 1]) {
    logPaths.push(args[++i]);
  } else if (args[i] === "--compare") {
    compareMode = true;
  } else if (!args[i].startsWith("--")) {
    logPaths.push(args[i]);
  }
}

if (logPaths.length === 0) logPaths = [DEFAULT_LOG];

console.log("=".repeat(65));
console.log("  HUMANNESS SCORE — Multi-Dimensional Analysis");
console.log("=".repeat(65));

const results = [];

for (const logPath of logPaths) {
  if (!fs.existsSync(logPath)) {
    console.log(`\n  File not found: ${logPath}`);
    continue;
  }

  const logData = JSON.parse(fs.readFileSync(logPath, "utf8"));
  const result = scoreHumanness(logData);
  results.push({ path: logPath, result });

  const name = path.basename(logPath);
  console.log(`\n  ── ${name} ──`);
  console.log(`  Actions: ${result.meta.total_actions} | Duration: ${Math.round(result.meta.session_duration)}s`);
  console.log();

  // Dimension scores
  const dims = result.dimensions;
  const bar = (s) => {
    const filled = Math.round(s / 5);
    return "█".repeat(filled) + "░".repeat(20 - filled);
  };

  console.log(`  Timing:      ${bar(dims.timing)} ${String(dims.timing).padStart(3)}/100`);
  console.log(`  Motor:       ${bar(dims.motor)} ${String(dims.motor).padStart(3)}/100`);
  console.log(`  Behavioral:  ${bar(dims.behavioral)} ${String(dims.behavioral).padStart(3)}/100`);
  console.log(`  Strategic:   ${bar(dims.strategic)} ${String(dims.strategic).padStart(3)}/100`);
  console.log(`  ──────────────────────────────────────`);
  console.log(`  COMPOSITE:   ${bar(result.composite)} ${String(result.composite).padStart(3)}/100`);

  // Detailed breakdown
  console.log(`\n  Details:`);
  for (const [dim, details] of Object.entries(result.details)) {
    console.log(`    ${dim}:`);
    for (const [key, val] of Object.entries(details)) {
      if (typeof val === "object" && val !== null) {
        const parts = Object.entries(val).map(([k, v]) => `${k}=${v}`).join(", ");
        console.log(`      ${key}: ${parts}`);
      } else {
        console.log(`      ${key}: ${val}`);
      }
    }
  }
}

// Compare mode
if (compareMode && results.length >= 2) {
  console.log("\n" + "=".repeat(65));
  console.log("  COMPARISON");
  console.log("=".repeat(65));
  const nameWidth = 25;
  console.log(`\n  ${"Log".padEnd(nameWidth)} Timing  Motor  Behav  Strat  TOTAL`);
  console.log("  " + "-".repeat(60));
  for (const { path: p, result: r } of results) {
    const name = path.basename(p).slice(0, nameWidth - 1);
    const d = r.dimensions;
    console.log(`  ${name.padEnd(nameWidth)} ${String(d.timing).padStart(5)}  ${String(d.motor).padStart(5)}  ${String(d.behavioral).padStart(5)}  ${String(d.strategic).padStart(5)}  ${String(r.composite).padStart(5)}`);
  }
}

console.log("\n" + "=".repeat(65));

// Save results
const outPath = path.join(__dirname, "..", "vision", "data", "humanness_scores.json");
fs.writeFileSync(outPath, JSON.stringify(results.map(r => ({ file: path.basename(r.path), ...r.result })), null, 2));
console.log(`  Scores saved to ${outPath}`);
