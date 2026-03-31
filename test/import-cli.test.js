#!/usr/bin/env node
"use strict";

/**
 * PokerStars Import CLI Tests
 */

const path = require("path");
const fs = require("fs");
const { execSync } = require("child_process");
const { SessionStorage } = require("../src/api/storage");
const { queryHands } = require("../src/api/query");

const testDir = path.join(__dirname, "..", "test-output", "cli-import-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

const SCRIPT = path.join(__dirname, "..", "scripts", "import-pokerstars.js");

const SAMPLE = `PokerStars Hand #999000001: Hold'em No Limit ($0.05/$0.10 USD) - 2021/06/01 10:00:00 ET
Table 'TestTable' 6-max Seat #1 is the button
Seat 1: Hero ($10.00 in chips)
Seat 2: Villain ($10.00 in chips)
Hero: posts small blind $0.05
Villain: posts big blind $0.10
*** HOLE CARDS ***
Dealt to Hero [As Kh]
Hero: raises $0.10 to $0.20
Villain: folds
Uncalled bet ($0.10) returned to Hero
Hero collected $0.25 from pot
*** SUMMARY ***
Total pot $0.25 | Rake $0.00

PokerStars Hand #999000002: Hold'em No Limit ($0.05/$0.10 USD) - 2021/06/01 10:05:00 ET
Table 'TestTable' 6-max Seat #2 is the button
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
Total pot $0.25 | Rake $0.00`;

function run(args, opts = {}) {
  const dataDir = opts.dataDir || path.join(testDir, "default-sessions");
  try {
    const output = execSync(`node "${SCRIPT}" ${args} --data-dir "${dataDir}"`, {
      encoding: "utf8", timeout: 10000, cwd: path.join(__dirname, ".."),
    });
    return { ok: true, output, code: 0 };
  } catch (e) {
    return { ok: false, output: e.stdout || e.stderr || e.message, code: e.status || 1 };
  }
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 1: Single File Import
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 1: Single File ===");
{
  const dataDir = path.join(testDir, "t1-sessions");
  const samplePath = path.join(testDir, "t1-sample.txt");
  fs.writeFileSync(samplePath, SAMPLE);

  const r = run(`"${samplePath}"`, { dataDir });
  check("T1: exits ok", r.ok);
  check("T1: output mentions hands", r.output.includes("2 hand"));
  check("T1: output mentions session", r.output.includes("import-"));
  check("T1: output mentions Done", r.output.includes("Done"));

  // Verify session exists on disk
  const storage = new SessionStorage(dataDir);
  const sessions = storage.list();
  check("T1: 1 session created", sessions.length === 1);
  check("T1: session is complete", sessions[0].status === "complete");
  check("T1: 2 hands played", sessions[0].handsPlayed === 2);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 2: Directory Import
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 2: Directory ===");
{
  const dataDir = path.join(testDir, "t2-sessions");
  const dirPath = path.join(testDir, "t2-hands");
  fs.mkdirSync(dirPath, { recursive: true });
  fs.writeFileSync(path.join(dirPath, "file1.txt"), SAMPLE);
  fs.writeFileSync(path.join(dirPath, "file2.txt"), SAMPLE.replace("999000001", "999000003").replace("999000002", "999000004"));

  const r = run(`"${dirPath}"`, { dataDir });
  check("T2: exits ok", r.ok);
  check("T2: mentions 2 file(s)", r.output.includes("2 file(s)"));

  const storage = new SessionStorage(dataDir);
  const sessions = storage.list();
  check("T2: 2 sessions created", sessions.length === 2);
  check("T2: both complete", sessions.every((s) => s.status === "complete"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 3: Invalid Path
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 3: Invalid Path ===");
{
  const r = run(`"/nonexistent/path/file.txt"`, { dataDir: path.join(testDir, "t3") });
  check("T3: exits with error", !r.ok);
  check("T3: error message", r.output.includes("does not exist"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 4: No Args
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 4: No Args ===");
{
  const r = run("", { dataDir: path.join(testDir, "t4") });
  check("T4: exits with error", !r.ok);
  check("T4: usage hint", r.output.includes("Usage") || r.output.includes("No input"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 5: Empty Directory
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 5: Empty Dir ===");
{
  const emptyDir = path.join(testDir, "t5-empty");
  fs.mkdirSync(emptyDir, { recursive: true });
  const r = run(`"${emptyDir}"`, { dataDir: path.join(testDir, "t5") });
  check("T5: exits with error", !r.ok);
  check("T5: mentions no .txt files", r.output.includes("No .txt"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 6: Study Queryable After CLI Import
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 6: Study Queryable ===");
{
  const dataDir = path.join(testDir, "t6-sessions");
  const samplePath = path.join(testDir, "t6-sample.txt");
  fs.writeFileSync(samplePath, SAMPLE);

  run(`"${samplePath}"`, { dataDir });

  const storage = new SessionStorage(dataDir);
  const sessions = storage.list();
  check("T6: session exists", sessions.length === 1);

  // Query hands via the Study data path
  const hands = queryHands(storage, { sessionId: sessions[0].sessionId });
  check("T6: hands queryable", hands.length > 0);
  check("T6: hands have sessionId", hands.every((h) => h.sessionId === sessions[0].sessionId));
  check("T6: hands have handId", hands.every((h) => h.handId));

  // Load events for replay
  const info = storage.load(sessions[0].sessionId);
  const content = fs.readFileSync(info.eventsPath, "utf8").trim();
  const events = content.split("\n").filter(Boolean).map((l) => JSON.parse(l));
  const h1Events = events.filter((e) => e.handId === "1");
  check("T6: hand events loadable", h1Events.length > 0);
  check("T6: has HAND_START", h1Events.some((e) => e.type === "HAND_START"));
  check("T6: has HAND_END", h1Events.some((e) => e.type === "HAND_END"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 7: Help Flag
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 7: Help ===");
{
  const r = run("--help", { dataDir: path.join(testDir, "t7") });
  check("T7: exits ok", r.ok);
  check("T7: shows usage", r.output.includes("Usage"));
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n*** IMPORT CLI TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
if (failed > 0) process.exit(1);
