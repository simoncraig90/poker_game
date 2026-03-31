#!/usr/bin/env node
"use strict";

/**
 * Import PokerStars hand histories from stdin (paste-friendly).
 *
 * Usage:
 *   node scripts/import-pokerstars-stdin.js [--data-dir path]
 *
 * Then paste hand history text and press Ctrl+D (Unix) or Ctrl+Z Enter (Windows).
 *
 * Or pipe:
 *   cat hands.txt | node scripts/import-pokerstars-stdin.js
 *   pbpaste | node scripts/import-pokerstars-stdin.js
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { parsePokerStarsText, handToEvents } = require("../src/import/pokerstars");
const { SessionStorage } = require("../src/api/storage");

const args = process.argv.slice(2);
let dataDir = path.join(process.cwd(), "data", "sessions");

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--data-dir" && args[i + 1]) dataDir = path.resolve(args[++i]);
  if (args[i] === "--help" || args[i] === "-h") {
    console.log("Usage: node scripts/import-pokerstars-stdin.js [--data-dir path]");
    console.log("Paste PokerStars hand history, then Ctrl+D (Unix) or Ctrl+Z Enter (Windows).");
    process.exit(0);
  }
}

// Read all of stdin
let input = "";
const isInteractive = process.stdin.isTTY;
if (isInteractive) {
  console.log("Paste PokerStars hand history below, then press Ctrl+Z Enter (Windows) or Ctrl+D (Unix):");
  console.log("---");
}

process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  if (!input.trim()) {
    console.error("Error: No input received.");
    process.exit(1);
  }

  const parsed = parsePokerStarsText(input);
  if (parsed.length === 0) {
    console.error("Error: No parseable PokerStars hands found in input.");
    process.exit(1);
  }

  const storage = new SessionStorage(dataDir);
  const sessionId = "import-" + crypto.randomUUID().slice(0, 12);
  const config = {
    tableId: "imported",
    tableName: parsed[0].tableName || "Imported",
    maxSeats: 6,
    sb: parsed[0].sb,
    bb: parsed[0].bb,
    minBuyIn: 0,
    maxBuyIn: 99999,
  };

  const info = storage.create(sessionId, config);
  let handsImported = 0;
  const errors = [];

  for (let i = 0; i < parsed.length; i++) {
    try {
      const handId = String(i + 1);
      const events = handToEvents(parsed[i], sessionId, handId);
      for (const e of events) {
        fs.appendFileSync(info.eventsPath, JSON.stringify(e) + "\n");
      }
      handsImported++;
    } catch (e) {
      errors.push(`Hand ${i + 1}: ${e.message}`);
    }
  }

  storage.updateMeta(sessionId, {
    status: "complete",
    handsPlayed: handsImported,
    lastEventAt: new Date().toISOString(),
  });

  for (const err of errors) console.log(`  WARNING: ${err}`);
  console.log(`\nImported ${handsImported} hand(s) → session ${sessionId}`);
  if (handsImported > 0) console.log("Ready in the Study tab.");
  process.exit(errors.length > 0 && handsImported === 0 ? 1 : 0);
});
