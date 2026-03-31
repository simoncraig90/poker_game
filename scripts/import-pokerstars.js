#!/usr/bin/env node
"use strict";

/**
 * Import PokerStars hand histories into the Study pipeline.
 *
 * Usage:
 *   node scripts/import-pokerstars.js <file-or-directory> [--data-dir path]
 *
 * Examples:
 *   node scripts/import-pokerstars.js ~/hands/session.txt
 *   node scripts/import-pokerstars.js ~/hands/
 *   node scripts/import-pokerstars.js ~/hands/ --data-dir ./data/sessions
 */

const fs = require("fs");
const path = require("path");
const { importPokerStars } = require("../src/import/pokerstars");
const { SessionStorage } = require("../src/api/storage");

// ── Parse args ────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
let inputPath = null;
let dataDir = path.join(process.cwd(), "data", "sessions");

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--data-dir" && args[i + 1]) {
    dataDir = path.resolve(args[++i]);
  } else if (args[i] === "--help" || args[i] === "-h") {
    console.log("Usage: node scripts/import-pokerstars.js <file-or-directory> [--data-dir path]");
    console.log("");
    console.log("  <file-or-directory>  Path to a .txt hand history file, or a directory of .txt files");
    console.log("  --data-dir           Session storage directory (default: ./data/sessions)");
    process.exit(0);
  } else if (!inputPath) {
    inputPath = path.resolve(args[i]);
  }
}

if (!inputPath) {
  console.error("Error: No input path specified.");
  console.error("Usage: node scripts/import-pokerstars.js <file-or-directory>");
  process.exit(1);
}

// ── Resolve files ─────────────────────────────────────────────────────────

let files = [];

if (!fs.existsSync(inputPath)) {
  console.error(`Error: Path does not exist: ${inputPath}`);
  process.exit(1);
}

const stat = fs.statSync(inputPath);
if (stat.isFile()) {
  files = [inputPath];
} else if (stat.isDirectory()) {
  files = fs.readdirSync(inputPath)
    .filter((f) => f.endsWith(".txt"))
    .map((f) => path.join(inputPath, f))
    .sort();
  if (files.length === 0) {
    console.error(`Error: No .txt files found in ${inputPath}`);
    process.exit(1);
  }
} else {
  console.error(`Error: Not a file or directory: ${inputPath}`);
  process.exit(1);
}

// ── Import ────────────────────────────────────────────────────────────────

const storage = new SessionStorage(dataDir);
let totalHands = 0;
let totalFiles = 0;
let totalErrors = 0;

console.log(`Importing ${files.length} file(s) into ${dataDir}\n`);

for (const file of files) {
  const basename = path.basename(file);
  try {
    const result = importPokerStars(file, storage);
    totalFiles++;
    totalHands += result.handsImported;
    totalErrors += result.errors.length;

    console.log(`  ${basename}: ${result.handsImported} hands → session ${result.sessionId}`);
    for (const err of result.errors) {
      console.log(`    WARNING: ${err}`);
    }
  } catch (e) {
    totalErrors++;
    console.log(`  ${basename}: FAILED — ${e.message}`);
  }
}

// ── Summary ───────────────────────────────────────────────────────────────

console.log("");
console.log(`Done. ${totalFiles} file(s), ${totalHands} hand(s) imported, ${totalErrors} warning(s).`);
if (totalHands > 0) {
  console.log(`Sessions are ready in the Study tab.`);
}
process.exit(totalErrors > 0 && totalHands === 0 ? 1 : 0);
