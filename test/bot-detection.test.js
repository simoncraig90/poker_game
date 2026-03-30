#!/usr/bin/env node
"use strict";

/**
 * Bot Detection Test
 *
 * Feeds the analyzer with two profiles:
 *   - "bot": no telemetry (WebSocket-only bot), consistent timing
 *   - "human": realistic telemetry with mouse movement, varied timing
 *
 * Verifies the bot gets flagged and the human doesn't.
 */

const { BotDetector } = require("../src/detection/analyzer");

const detector = new BotDetector();

// ── Simulate a bot: no telemetry at all (like our WS bot) ────────────

console.log("=== Bot Detection Test ===\n");
console.log("Feeding bot actions (no telemetry)...");

for (let i = 0; i < 15; i++) {
  // Bot sends null telemetry (WebSocket-only, no mouse data)
  const result = detector.analyze(0, null);
  if (result.level !== "insufficient_data") {
    console.log(`  Action ${i + 1}: score=${result.score.toFixed(2)} level=${result.level}`);
  }
}

const botProfile = detector.getProfile(0);
console.log(`\n  Bot final: score=${botProfile.currentScore.toFixed(2)} avg=${botProfile.avgScore.toFixed(2)} flags=${botProfile.flagCount} warns=${botProfile.warnCount}`);

// ── Simulate a Puppeteer bot: has mouse data but it's mechanical ──────

console.log("\nFeeding Puppeteer bot actions (mechanical mouse)...");

for (let i = 0; i < 15; i++) {
  const result = detector.analyze(1, {
    reactionMs: 820 + Math.random() * 50,   // very consistent ~820-870ms
    timeSinceMouseMove: 10,
    trailLength: 12,
    trailDistance: 350,
    trailStraightness: 0.97,                 // almost perfectly straight
    trailAvgSpeed: 0.8,
    trailSpeedVariance: 0.01,                // very consistent speed
    trailDirectionChanges: 1,
    trailPauses: 0,
    clickOffsetX: 0.5 + Math.random(),       // near center
    clickOffsetY: 0.3 + Math.random(),
    clickDistFromCenter: 1.0 + Math.random(), // always within ~2px of center
    keystrokeCount: 0,
    keystrokeAvgInterval: null,
    keystrokeVariance: null,
    tabFocused: true,
    tabBlurCount: 0,
    scrollCount: 0,
    idleStreak: 0,
    ts: Date.now(),
  });
  if (result.level !== "insufficient_data") {
    console.log(`  Action ${i + 1}: score=${result.score.toFixed(2)} level=${result.level}`);
  }
}

const puppeteerProfile = detector.getProfile(1);
console.log(`\n  Puppeteer bot final: score=${puppeteerProfile.currentScore.toFixed(2)} avg=${puppeteerProfile.avgScore.toFixed(2)} flags=${puppeteerProfile.flagCount} warns=${puppeteerProfile.warnCount}`);

// ── Simulate a human: varied timing, natural mouse, imprecise clicks ──

console.log("\nFeeding human actions (natural telemetry)...");

for (let i = 0; i < 15; i++) {
  const thinkTime = 1500 + Math.random() * 8000; // 1.5s to 9.5s — wide variance
  const result = detector.analyze(2, {
    reactionMs: thinkTime,
    timeSinceMouseMove: 30 + Math.random() * 200,
    trailLength: 30 + Math.floor(Math.random() * 80),
    trailDistance: 200 + Math.random() * 600,
    trailStraightness: 0.3 + Math.random() * 0.4,  // curved, wandering paths
    trailAvgSpeed: 0.3 + Math.random() * 0.8,
    trailSpeedVariance: 0.1 + Math.random() * 0.5,  // variable speed
    trailDirectionChanges: 5 + Math.floor(Math.random() * 15),
    trailPauses: Math.floor(Math.random() * 3),
    clickOffsetX: (Math.random() - 0.5) * 20,       // imprecise
    clickOffsetY: (Math.random() - 0.5) * 12,
    clickDistFromCenter: 3 + Math.random() * 15,     // varies widely
    keystrokeCount: i % 3 === 0 ? 2 + Math.floor(Math.random() * 3) : 0,
    keystrokeAvgInterval: 120 + Math.random() * 80,
    keystrokeVariance: 200 + Math.random() * 500,    // variable typing
    tabFocused: Math.random() > 0.1,
    tabBlurCount: Math.floor(Math.random() * 3),
    scrollCount: Math.floor(Math.random() * 5),
    idleStreak: 0,
    ts: Date.now(),
  });
  if (result.level !== "insufficient_data") {
    console.log(`  Action ${i + 1}: score=${result.score.toFixed(2)} level=${result.level}`);
  }
}

const humanProfile = detector.getProfile(2);
console.log(`\n  Human final: score=${humanProfile.currentScore.toFixed(2)} avg=${humanProfile.avgScore.toFixed(2)} flags=${humanProfile.flagCount} warns=${humanProfile.warnCount}`);

// ── Results ───────────────────────────────────────────────────────────

console.log("\n" + "═".repeat(50));
console.log("Results:");
console.log("═".repeat(50));

const botFlagged = botProfile.flagCount > 0;
const puppeteerFlagged = puppeteerProfile.flagCount > 0 || puppeteerProfile.warnCount > 0;
const humanClean = humanProfile.flagCount === 0;

console.log(`  WS Bot:        ${botFlagged ? "DETECTED (flagged)" : "MISSED"} — score ${botProfile.avgScore.toFixed(2)}`);
console.log(`  Puppeteer Bot: ${puppeteerFlagged ? "DETECTED (flagged/warned)" : "MISSED"} — score ${puppeteerProfile.avgScore.toFixed(2)}`);
console.log(`  Human:         ${humanClean ? "CLEAN (not flagged)" : "FALSE POSITIVE"} — score ${humanProfile.avgScore.toFixed(2)}`);

const passed = botFlagged && humanClean;
console.log(`\n${passed ? "PASS ✓" : "FAIL ✗"}`);
if (!puppeteerFlagged) {
  console.log("  Note: Puppeteer bot not detected — detection for mechanical mouse patterns could be improved");
}
process.exit(passed ? 0 : 1);
