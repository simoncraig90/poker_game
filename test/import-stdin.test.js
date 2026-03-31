#!/usr/bin/env node
"use strict";

/**
 * PokerStars Stdin Import Tests
 */

const path = require("path");
const fs = require("fs");
const { execSync } = require("child_process");
const { SessionStorage } = require("../src/api/storage");
const { queryHands } = require("../src/api/query");

const testDir = path.join(__dirname, "..", "test-output", "stdin-import-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

const SCRIPT = path.join(__dirname, "..", "scripts", "import-pokerstars-stdin.js");

const SAMPLE = `PokerStars Hand #888000001: Hold'em No Limit ($0.05/$0.10 USD) - 2021/06/01 10:00:00 ET
Table 'StdinTest' 6-max Seat #1 is the button
Seat 1: Hero ($10.00 in chips)
Seat 2: Villain ($10.00 in chips)
Hero: posts small blind $0.05
Villain: posts big blind $0.10
*** HOLE CARDS ***
Dealt to Hero [Ah Kd]
Hero: raises $0.10 to $0.20
Villain: folds
Uncalled bet ($0.10) returned to Hero
Hero collected $0.25 from pot
*** SUMMARY ***
Total pot $0.25 | Rake $0.00

PokerStars Hand #888000002: Hold'em No Limit ($0.05/$0.10 USD) - 2021/06/01 10:05:00 ET
Table 'StdinTest' 6-max Seat #2 is the button
Seat 1: Hero ($10.15 in chips)
Seat 2: Villain ($9.85 in chips)
Villain: posts small blind $0.05
Hero: posts big blind $0.10
*** HOLE CARDS ***
Dealt to Hero [Qd Qh]
Villain: calls $0.05
Hero: checks
*** FLOP *** [7s 3d 2c]
Hero: bets $0.10
Villain: folds
Hero collected $0.25 from pot
*** SUMMARY ***
Total pot $0.25 | Rake $0.00

PokerStars Hand #888000003: Hold'em No Limit ($0.05/$0.10 USD) - 2021/06/01 10:10:00 ET
Table 'StdinTest' 6-max Seat #1 is the button
Seat 1: Hero ($10.30 in chips)
Seat 2: Villain ($9.70 in chips)
Hero: posts small blind $0.05
Villain: posts big blind $0.10
*** HOLE CARDS ***
Dealt to Hero [Jh Js]
Hero: raises $0.10 to $0.20
Villain: calls $0.10
*** FLOP *** [9s 5h 2c]
Villain: checks
Hero: bets $0.20
Villain: calls $0.20
*** TURN *** [9s 5h 2c] [7d]
Villain: checks
Hero: checks
*** RIVER *** [9s 5h 2c 7d] [3s]
Villain: checks
Hero: bets $0.30
Villain: folds
Uncalled bet ($0.30) returned to Hero
Hero collected $0.85 from pot
*** SUMMARY ***
Total pot $0.85 | Rake $0.00`;

function runStdin(input, opts = {}) {
  const dataDir = opts.dataDir || path.join(testDir, "default");
  try {
    const output = execSync(`node "${SCRIPT}" --data-dir "${dataDir}"`, {
      encoding: "utf8", timeout: 10000, input, cwd: path.join(__dirname, ".."),
    });
    return { ok: true, output, code: 0 };
  } catch (e) {
    return { ok: false, output: (e.stdout || "") + (e.stderr || ""), code: e.status || 1 };
  }
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 1: Pipe Multiple Hands
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 1: Pipe Multiple Hands ===");
{
  const dataDir = path.join(testDir, "t1");
  const r = runStdin(SAMPLE, { dataDir });
  check("T1: exits ok", r.ok);
  check("T1: mentions 3 hands", r.output.includes("3 hand"));
  check("T1: mentions session id", r.output.includes("import-"));
  check("T1: mentions Study tab", r.output.includes("Study"));

  const storage = new SessionStorage(dataDir);
  const sessions = storage.list();
  check("T1: session created", sessions.length === 1);
  check("T1: 3 hands", sessions[0].handsPlayed === 3);
  check("T1: complete", sessions[0].status === "complete");
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 2: Empty Input
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 2: Empty Input ===");
{
  const r = runStdin("", { dataDir: path.join(testDir, "t2") });
  check("T2: exits with error", !r.ok);
  check("T2: mentions no input", r.output.includes("No input"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 3: Garbage Input
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 3: Garbage Input ===");
{
  const r = runStdin("this is not a hand history\nfoo bar baz\n", { dataDir: path.join(testDir, "t3") });
  check("T3: exits with error", !r.ok);
  check("T3: mentions no parseable", r.output.includes("No parseable"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 4: Study Pipeline Queryable
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 4: Study Queryable ===");
{
  const dataDir = path.join(testDir, "t4");
  runStdin(SAMPLE, { dataDir });

  const storage = new SessionStorage(dataDir);
  const hands = queryHands(storage);
  check("T4: hands queryable", hands.length > 0);
  // 3 hands × 2 players = 6 participations
  check("T4: correct participation count", hands.length === 6);

  // Events replayable
  const info = storage.load(storage.list()[0].sessionId);
  const content = fs.readFileSync(info.eventsPath, "utf8").trim();
  const events = content.split("\n").filter(Boolean).map((l) => JSON.parse(l));
  const h1 = events.filter((e) => e.handId === "1");
  check("T4: hand 1 has HAND_START", h1.some((e) => e.type === "HAND_START"));
  check("T4: hand 1 has HERO_CARDS", h1.some((e) => e.type === "HERO_CARDS"));
  check("T4: hand 1 has HAND_END", h1.some((e) => e.type === "HAND_END"));

  // Hand 3 has DEAL_COMMUNITY (multi-street hand)
  const h3 = events.filter((e) => e.handId === "3");
  check("T4: hand 3 has DEAL_COMMUNITY", h3.some((e) => e.type === "DEAL_COMMUNITY"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 5: Help
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 5: Help ===");
{
  try {
    const output = execSync(`node "${SCRIPT}" --help`, { encoding: "utf8", timeout: 5000 });
    check("T5: help shows usage", output.includes("Usage") || output.includes("Paste"));
  } catch (e) {
    check("T5: help shows usage", (e.stdout || "").includes("Usage"));
  }
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n*** STDIN IMPORT TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
if (failed > 0) process.exit(1);
