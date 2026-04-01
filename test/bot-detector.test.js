#!/usr/bin/env node
"use strict";

/**
 * Bot detector test.
 *
 * Simulates a human player (variable timing, varied actions)
 * and a bot player (consistent timing, deterministic actions)
 * and verifies the bot scores lower (more suspicious) than the human.
 */

const { BotDetector } = require("../src/engine/bot-detector");

let passed = 0;
let failed = 0;

function assert(condition, msg) {
  if (condition) {
    passed++;
    console.log(`  PASS: ${msg}`);
  } else {
    failed++;
    console.error(`  FAIL: ${msg}`);
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────

// Seeded RNG for reproducible tests
let seed = 12345;
function rng() {
  seed = (seed * 1664525 + 1013904223) & 0x7fffffff;
  return seed / 0x7fffffff;
}

function gaussianRng() {
  // Box-Muller transform for normally distributed values
  const u1 = rng();
  const u2 = rng();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

/**
 * Human-like response time: log-normal distribution centered around 5-8s
 * with occasional fast (2s) and slow (15s) outliers.
 */
function humanResponseTime() {
  const logMean = Math.log(6000); // ~6 seconds
  const logStd = 0.6;
  const t = Math.exp(logMean + logStd * gaussianRng());
  return Math.max(1500, Math.min(20000, t));
}

/**
 * Bot-like response time: very consistent ~500ms with tiny variance.
 */
function botResponseTime() {
  return 500 + (rng() - 0.5) * 50; // 475-525ms
}

const ACTIONS = ["FOLD", "CHECK", "CALL", "BET", "RAISE"];
const STRENGTHS = ["trash", "weak", "medium", "strong", "premium"];
const PHASES = ["PREFLOP", "FLOP", "TURN", "RIVER"];

/**
 * Human action: varies by hand strength but with randomness.
 */
function humanAction(strength, phase) {
  const r = rng();
  if (strength === "premium") {
    // Usually raise, sometimes call, rarely fold
    if (r < 0.7) return { action: "RAISE", amount: 20 + Math.round(rng() * 30) };
    if (r < 0.95) return { action: "CALL", amount: 10 };
    return { action: "CHECK", amount: 0 };
  }
  if (strength === "strong") {
    if (r < 0.5) return { action: "RAISE", amount: 15 + Math.round(rng() * 25) };
    if (r < 0.8) return { action: "CALL", amount: 10 };
    if (r < 0.9) return { action: "BET", amount: 10 + Math.round(rng() * 20) };
    return { action: "FOLD", amount: 0 };
  }
  if (strength === "medium") {
    if (r < 0.3) return { action: "FOLD", amount: 0 };
    if (r < 0.6) return { action: "CALL", amount: 10 };
    if (r < 0.8) return { action: "CHECK", amount: 0 };
    return { action: "BET", amount: 10 + Math.round(rng() * 15) };
  }
  if (strength === "weak") {
    if (r < 0.6) return { action: "FOLD", amount: 0 };
    if (r < 0.8) return { action: "CHECK", amount: 0 };
    if (r < 0.9) return { action: "CALL", amount: 10 };
    return { action: "BET", amount: Math.round(rng() * 15) };
  }
  // trash
  if (r < 0.8) return { action: "FOLD", amount: 0 };
  if (r < 0.95) return { action: "CHECK", amount: 0 };
  return { action: "CALL", amount: 10 };
}

/**
 * Bot action: completely deterministic by hand strength.
 * Always the same action for the same situation.
 */
function botAction(strength, phase) {
  if (strength === "premium") return { action: "RAISE", amount: 30 };
  if (strength === "strong") return { action: "RAISE", amount: 20 };
  if (strength === "medium") return { action: "CALL", amount: 10 };
  if (strength === "weak") return { action: "FOLD", amount: 0 };
  return { action: "FOLD", amount: 0 };
}

// ── Test: Human vs Bot scoring ──────────────────────────────────────────

console.log("=== Bot Detector Tests ===\n");

console.log("Test 1: Human scores higher than bot");
{
  const detector = new BotDetector();

  // Simulate 200 actions for each player
  for (let i = 0; i < 200; i++) {
    const strength = STRENGTHS[Math.floor(rng() * STRENGTHS.length)];
    const phase = PHASES[Math.floor(rng() * PHASES.length)];
    const potSize = 20 + Math.round(rng() * 200);

    // Human
    const hResp = humanResponseTime();
    const hAct = humanAction(strength, phase);
    detector.recordAction("Human", hAct.action, hAct.amount, hResp, strength, potSize, phase);

    // Bot
    const bResp = botResponseTime();
    const bAct = botAction(strength, phase);
    detector.recordAction("Bot", bAct.action, bAct.amount, bResp, strength, potSize, phase);
  }

  const humanScore = detector.getScore("Human");
  const botScore = detector.getScore("Bot");

  console.log(`  Human score: ${humanScore.score} (reliable: ${humanScore.reliable})`);
  console.log(`  Bot score:   ${botScore.score} (reliable: ${botScore.reliable})`);
  console.log(`  Human signals:`, JSON.stringify(humanScore.signals));
  console.log(`  Bot signals:  `, JSON.stringify(botScore.signals));

  assert(humanScore.reliable, "Human has enough actions for reliable score");
  assert(botScore.reliable, "Bot has enough actions for reliable score");
  assert(humanScore.score > botScore.score, `Human (${humanScore.score}) scores higher than Bot (${botScore.score})`);
  assert(!humanScore.suspicious, "Human is not flagged as suspicious");
  assert(botScore.suspicious, "Bot is flagged as suspicious");
}

console.log("\nTest 2: Insufficient data returns unreliable score");
{
  const detector = new BotDetector();

  for (let i = 0; i < 10; i++) {
    detector.recordAction("NewPlayer", "CALL", 10, 3000, "medium", 50, "PREFLOP");
  }

  const score = detector.getScore("NewPlayer");
  assert(!score.reliable, "Score is unreliable with only 10 actions");
  assert(!score.suspicious, "Not suspicious when unreliable");
}

console.log("\nTest 3: Unknown player returns default score");
{
  const detector = new BotDetector();
  const score = detector.getScore("Ghost");
  assert(score.score === 1, "Unknown player gets score of 1");
  assert(!score.reliable, "Unknown player is unreliable");
}

console.log("\nTest 4: Timing signal detects consistent response times");
{
  const detector = new BotDetector();

  // Very consistent times (bot-like)
  for (let i = 0; i < 50; i++) {
    detector.recordAction("ConsistentPlayer", "CALL", 10, 500 + rng() * 20, "medium", 50, "PREFLOP");
  }

  // Variable times (human-like)
  for (let i = 0; i < 50; i++) {
    detector.recordAction("VariablePlayer", "CALL", 10, humanResponseTime(), "medium", 50, "PREFLOP");
  }

  const consistent = detector.getScore("ConsistentPlayer");
  const variable = detector.getScore("VariablePlayer");

  assert(
    consistent.signals.timing < variable.signals.timing,
    `Consistent timing signal (${consistent.signals.timing}) < variable (${variable.signals.timing})`
  );
}

console.log("\nTest 5: Decision consistency detects deterministic play");
{
  const detector = new BotDetector();

  // Deterministic player: always same action per situation
  for (let i = 0; i < 100; i++) {
    const strength = STRENGTHS[i % STRENGTHS.length];
    const act = botAction(strength, "PREFLOP");
    detector.recordAction("Deterministic", act.action, act.amount, 3000, strength, 50, "PREFLOP");
  }

  // Random player: varies action per situation
  for (let i = 0; i < 100; i++) {
    const strength = STRENGTHS[i % STRENGTHS.length];
    const act = humanAction(strength, "PREFLOP");
    detector.recordAction("Random", act.action, act.amount, 3000, strength, 50, "PREFLOP");
  }

  const det = detector.getScore("Deterministic");
  const rand = detector.getScore("Random");

  assert(
    det.signals.decisionConsistency < rand.signals.decisionConsistency,
    `Deterministic consistency (${det.signals.decisionConsistency}) < random (${rand.signals.decisionConsistency})`
  );
}

console.log("\nTest 6: Bet sizing detects exact pot fractions");
{
  const detector = new BotDetector();

  // Exact-fraction bettor (bot-like)
  for (let i = 0; i < 40; i++) {
    const potSize = 100;
    const fractions = [1 / 3, 1 / 2, 2 / 3, 3 / 4];
    const frac = fractions[i % fractions.length];
    const amount = potSize * frac;
    detector.recordAction("ExactBetter", "BET", amount, 3000, "strong", potSize, "FLOP");
  }

  // Round-number bettor (human-like)
  for (let i = 0; i < 40; i++) {
    const potSize = 100;
    // Humans bet round numbers: 25, 30, 35, 40, 45, 55, 60, 70, 80
    const amounts = [25, 30, 35, 40, 45, 55, 60, 70, 80, 15];
    const amount = amounts[i % amounts.length];
    detector.recordAction("RoundBetter", "BET", amount, 3000, "strong", potSize, "FLOP");
  }

  const exact = detector.getScore("ExactBetter");
  const round = detector.getScore("RoundBetter");

  assert(
    exact.signals.betSizing < round.signals.betSizing,
    `Exact bettor sizing signal (${exact.signals.betSizing}) < round bettor (${round.signals.betSizing})`
  );
}

console.log("\nTest 7: getSummary returns all players");
{
  const detector = new BotDetector();
  for (let i = 0; i < 35; i++) {
    detector.recordAction("Alice", "CALL", 10, 3000, "medium", 50, "PREFLOP");
    detector.recordAction("Bob", "FOLD", 0, 500, "weak", 50, "PREFLOP");
  }

  const summary = detector.getSummary();
  assert(summary.length === 2, "Summary contains 2 players");
  assert(summary.some((s) => s.player === "Alice"), "Alice in summary");
  assert(summary.some((s) => s.player === "Bob"), "Bob in summary");
}

console.log("\nTest 8: Session tracking records session end");
{
  const detector = new BotDetector();
  for (let i = 0; i < 10; i++) {
    detector.recordAction("SessionPlayer", "CALL", 10, 3000, "medium", 50, "PREFLOP");
  }
  detector.recordSessionEnd("SessionPlayer");

  const raw = detector.getRawData("SessionPlayer");
  assert(raw.sessions.length === 1, "One session recorded");
  assert(raw.handsThisSession === 0, "Hands reset after session end");
}

console.log("\nTest 9: VPIP/PFR stability with enough preflop data");
{
  const detector = new BotDetector();

  // Stable player: always the same VPIP rate
  for (let i = 0; i < 200; i++) {
    const action = i % 3 === 0 ? "CALL" : "FOLD"; // exactly 33% VPIP
    detector.recordAction("StableVPIP", action, action === "CALL" ? 10 : 0, 3000, "medium", 50, "PREFLOP");
  }

  // Varying player: VPIP rate changes over time
  for (let i = 0; i < 200; i++) {
    let vpipRate;
    if (i < 50) vpipRate = 0.6;       // aggressive start
    else if (i < 100) vpipRate = 0.2;  // tightens up
    else if (i < 150) vpipRate = 0.45; // loosens
    else vpipRate = 0.3;               // settles
    const action = rng() < vpipRate ? "CALL" : "FOLD";
    detector.recordAction("VaryingVPIP", action, action === "CALL" ? 10 : 0, 3000, "medium", 50, "PREFLOP");
  }

  const stable = detector.getScore("StableVPIP");
  const varying = detector.getScore("VaryingVPIP");

  if (stable.signals.vpipPfrStability !== null && varying.signals.vpipPfrStability !== null) {
    assert(
      stable.signals.vpipPfrStability < varying.signals.vpipPfrStability,
      `Stable VPIP signal (${stable.signals.vpipPfrStability}) < varying (${varying.signals.vpipPfrStability})`
    );
  } else {
    assert(false, "VPIP/PFR signals should be available with 200 preflop decisions");
  }
}

// ── Summary ─────────────────────────────────────────────────────────────

console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
if (failed > 0) process.exit(1);
