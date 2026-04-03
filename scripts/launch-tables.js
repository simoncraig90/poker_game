#!/usr/bin/env node
"use strict";

/**
 * Launch multiple poker table browser windows in a grid layout.
 *
 * Opens N browser windows, each connecting to a different table.
 * Tiles them in a grid on the primary monitor.
 *
 * Usage:
 *   node scripts/launch-tables.js              # 4 tables (default)
 *   node scripts/launch-tables.js --tables 6   # 6 tables
 *   node scripts/launch-tables.js --tables 2   # 2 tables
 */

const { execSync } = require("child_process");

const args = process.argv.slice(2);
let numTables = 4;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--tables" && args[i + 1]) numTables = parseInt(args[i + 1]);
}

const PORT = 9100;
const BASE_URL = `http://localhost:${PORT}`;

// Grid layout: calculate rows/cols
const cols = Math.ceil(Math.sqrt(numTables));
const rows = Math.ceil(numTables / cols);

// Screen dimensions (approximate for positioning)
const SCREEN_W = 1920;
const SCREEN_H = 1080;
const WINDOW_W = Math.floor(SCREEN_W / cols);
const WINDOW_H = Math.floor(SCREEN_H / rows);

console.log(`Launching ${numTables} tables in ${cols}x${rows} grid`);
console.log(`Window size: ${WINDOW_W}x${WINDOW_H}`);
console.log();

for (let i = 0; i < numTables; i++) {
  const tableId = i + 1;
  const col = i % cols;
  const row = Math.floor(i / cols);
  const x = col * WINDOW_W;
  const y = row * WINDOW_H;
  const url = `${BASE_URL}?table=${tableId}`;

  console.log(`  Table ${tableId}: ${url} @ (${x}, ${y})`);

  // Launch Chrome with specific window position and size
  try {
    execSync(`start "" "chrome" "--new-window" "--window-size=${WINDOW_W},${WINDOW_H}" "--window-position=${x},${y}" "--app=${url}"`, {
      shell: true,
      stdio: "ignore",
    });
  } catch (e) {
    // Fallback: just open in default browser
    execSync(`start ${url}`, { shell: true, stdio: "ignore" });
  }
}

console.log(`\nAll ${numTables} tables launched.`);
console.log("Now start bot-players for each table:");
for (let i = 1; i <= numTables; i++) {
  console.log(`  node scripts/bot-players.js --table ${i}`);
}
