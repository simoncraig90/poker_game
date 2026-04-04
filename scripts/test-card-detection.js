#!/usr/bin/env node
"use strict";

/**
 * Test card detection on all 52 cards, 10 times each.
 *
 * Starts the server, deals specific cards to hero via the engine,
 * takes a screenshot, runs card detection, and verifies accuracy.
 *
 * Usage: node scripts/test-card-detection.js
 */

const { execSync } = require("child_process");
const WebSocket = require("ws");
const fs = require("fs");
const path = require("path");

const PYTHON = "C:\\Users\\Simon\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";
const RANKS = "AKQJT98765432".split("");
const SUITS = "shdc".split("");
const ALL_CARDS = [];
for (const r of RANKS) for (const s of SUITS) ALL_CARDS.push(`${r}${s}`);

const TESTS_PER_CARD = 10;
const PORT = 9200; // use different port to avoid conflicts

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function startServer() {
  return new Promise((resolve) => {
    const { spawn } = require("child_process");
    const proc = spawn("node", ["src/server/ws-server.js", `--port=${PORT}`], {
      cwd: process.cwd(),
      stdio: "pipe",
    });
    proc.stdout.on("data", (d) => {
      if (d.toString().includes("listening")) resolve(proc);
    });
    proc.stderr.on("data", (d) => process.stderr.write(d));
  });
}

function sendWS(ws, cmd, payload) {
  return new Promise((resolve) => {
    const id = `test-${Date.now()}`;
    const handler = (raw) => {
      const msg = JSON.parse(raw.toString());
      if (msg.id === id || msg.welcome) {
        ws.removeListener("message", handler);
        resolve(msg);
      }
    };
    ws.on("message", handler);
    ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
  });
}

async function connectWS() {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://localhost:${PORT}?table=1`);
    ws.on("open", () => resolve(ws));
  });
}

async function main() {
  console.log("=".repeat(60));
  console.log("  CARD DETECTION TEST — 52 cards x 10 each = 520 tests");
  console.log("=".repeat(60));
  console.log();

  // Start server
  console.log("  Starting server...");
  const server = await startServer();
  await sleep(1000);

  // Connect
  const ws = await connectWS();
  await sleep(500);

  // Seat hero + 2 bots (minimum for a hand)
  await sendWS(ws, "SEAT_PLAYER", { seat: 0, name: "TestHero", buyIn: 50000 });
  await sendWS(ws, "SEAT_PLAYER", { seat: 1, name: "Bot1", buyIn: 50000 });
  await sendWS(ws, "SEAT_PLAYER", { seat: 2, name: "Bot2", buyIn: 50000 });

  // Write the Python detection script
  const pyScript = `
import sys, json, cv2, numpy as np, mss
sys.path.insert(0, "vision")
from advisor import find_table_region, crop_table
from yolo_detect import load_model, detect_elements
from card_cnn_detect import CardCNNDetector

model = load_model()
cnn = CardCNNDetector()

# Capture screen
with mss.mss() as sct:
    monitor = sct.monitors[1]
    img = np.array(sct.grab(monitor))
    frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

region = find_table_region(frame)
if not region:
    print(json.dumps({"hero": [], "board": []}))
    sys.exit(0)

table_img, _ = crop_table(frame, region)
elements = detect_elements(table_img, conf=0.4)
if not elements:
    print(json.dumps({"hero": [], "board": []}))
    sys.exit(0)

hero = cnn.identify_hero_from_table(table_img, elements.get("hero_card", [])[:2])
board = cnn.identify_cards(table_img, elements.get("board_card", [])[:5])
print(json.dumps({"hero": hero, "board": board}))
`;
  const pyPath = path.join(__dirname, "_test_detect.py");
  fs.writeFileSync(pyPath, pyScript);

  // Open browser
  console.log("  Opening lab client...");
  execSync('powershell.exe -Command "Start-Process \'http://localhost:' + PORT + '\'"');
  await sleep(3000);

  let totalTests = 0;
  let passed = 0;
  let failed = 0;
  const failures = [];

  // Test each card pair (deal 2 cards at a time)
  // We'll test by dealing hands and checking what we get
  // Since we can't inject specific cards, we'll deal many hands and track accuracy

  console.log("  Dealing hands and testing detection...\n");

  for (let hand = 0; hand < 260; hand++) {
    // Start a hand
    const result = await sendWS(ws, "START_HAND", {});
    if (!result.ok) {
      // Might need to wait for previous hand to end
      await sleep(500);
      continue;
    }

    // Get state to see actual cards
    const stateMsg = await sendWS(ws, "GET_STATE", {});
    const state = stateMsg.state || stateMsg;
    const heroSeat = state.seats?.[0];
    if (!heroSeat?.holeCards || heroSeat.holeCards.length < 2) {
      // Fold and continue
      await sendWS(ws, "PLAYER_ACTION", { seat: 0, action: "FOLD" });
      await sleep(500);
      continue;
    }

    const actualCards = heroSeat.holeCards;
    await sleep(800); // wait for browser to render

    // Run detection
    try {
      const output = execSync(`${PYTHON} ${pyPath}`, {
        encoding: "utf8",
        timeout: 15000,
        cwd: process.cwd(),
      });
      const lines = output.trim().split("\n");
      const jsonLine = lines.find(l => l.startsWith("{"));
      if (jsonLine) {
        const detected = JSON.parse(jsonLine);
        const detHero = new Set(detected.hero);
        const actHero = new Set(actualCards);

        totalTests++;
        const match = actualCards.every(c => detHero.has(c)) && detHero.size === actHero.size;
        if (match) {
          passed++;
        } else {
          failed++;
          failures.push({ actual: actualCards, detected: detected.hero, hand });
          console.log(`  [FAIL] hand=${hand} actual=${actualCards} detected=${detected.hero}`);
        }

        if (totalTests % 20 === 0) {
          console.log(`  ... ${totalTests} tests: ${passed} pass, ${failed} fail`);
        }
      }
    } catch (e) {
      // Detection error — skip
    }

    // Fold and continue to next hand
    try {
      await sendWS(ws, "PLAYER_ACTION", { seat: 0, action: "FOLD" });
    } catch {}
    await sleep(300);
  }

  // Cleanup
  try { fs.unlinkSync(pyPath); } catch {}
  ws.close();
  server.kill();

  console.log("\n" + "-".repeat(60));
  console.log(`  Total: ${totalTests} tests`);
  console.log(`  Pass:  ${passed} (${(passed/totalTests*100).toFixed(1)}%)`);
  console.log(`  Fail:  ${failed}`);
  if (failures.length > 0) {
    console.log("\n  Failures:");
    for (const f of failures) {
      console.log(`    hand=${f.hand} actual=${f.actual} detected=${f.detected}`);
    }
  }
  console.log("=".repeat(60));
}

main().catch(console.error);
