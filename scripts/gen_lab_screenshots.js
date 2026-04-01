#!/usr/bin/env node
"use strict";

/**
 * Generate 100+ poker-lab screenshots at various game states.
 * Connects via WebSocket to seat players, play hands, and capture screenshots
 * at each phase transition (preflop, flop, turn, river, showdown).
 */

const puppeteer = require("puppeteer");
const WebSocket = require("ws");
const path = require("path");
const fs = require("fs");

const SERVER_URL = "http://localhost:9100";
const WS_URL = "ws://localhost:9100";
const OUT_DIR = path.join(__dirname, "..", "vision", "captures", "lab_gen");

// Ensure output dir exists
fs.mkdirSync(OUT_DIR, { recursive: true });

let msgId = 0;

function sendCmd(ws, cmd, payload = {}) {
  return new Promise((resolve, reject) => {
    const id = String(++msgId);
    const handler = (raw) => {
      try {
        const msg = JSON.parse(raw.toString());
        // Response format: { id, ok, events, state, error } — no 'result' wrapper
        if (msg.id === id) {
          ws.removeListener("message", handler);
          resolve(msg);
        }
      } catch (e) { /* ignore non-JSON */ }
    };
    ws.on("message", handler);
    ws.send(JSON.stringify({ id, cmd, payload }));
    setTimeout(() => {
      ws.removeListener("message", handler);
      reject(new Error(`Timeout waiting for response to ${cmd} (id=${id})`));
    }, 10000);
  });
}

async function waitForWs(ws) {
  return new Promise((resolve, reject) => {
    if (ws.readyState === WebSocket.OPEN) { resolve(); return; }
    ws.on("open", resolve);
    ws.on("error", reject);
  });
}

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function takeScreenshot(page, label, index) {
  // Wait for UI to settle
  await sleep(400);
  const filename = `lab_${String(index).padStart(4, "0")}_${label}.png`;
  const filepath = path.join(OUT_DIR, filename);
  await page.screenshot({ path: filepath });
  return filepath;
}

// Player configurations for variety
const PLAYER_CONFIGS = [
  [
    { seat: 0, name: "Hero", buyIn: 1000 },
    { seat: 1, name: "Villain1", buyIn: 1500 },
    { seat: 3, name: "Villain2", buyIn: 800 },
  ],
  [
    { seat: 0, name: "Player1", buyIn: 2000 },
    { seat: 2, name: "Player2", buyIn: 1200 },
    { seat: 4, name: "Player3", buyIn: 900 },
    { seat: 5, name: "Player4", buyIn: 1100 },
  ],
  [
    { seat: 0, name: "Hero", buyIn: 5000 },
    { seat: 1, name: "Alice", buyIn: 3000 },
    { seat: 2, name: "Bob", buyIn: 2500 },
    { seat: 3, name: "Charlie", buyIn: 4000 },
    { seat: 4, name: "Diana", buyIn: 1800 },
    { seat: 5, name: "Eve", buyIn: 3500 },
  ],
  [
    { seat: 0, name: "Hero", buyIn: 1000 },
    { seat: 3, name: "Opponent", buyIn: 1000 },
  ],
  [
    { seat: 0, name: "HeroX", buyIn: 2500 },
    { seat: 1, name: "Shark", buyIn: 4000 },
    { seat: 2, name: "Fish", buyIn: 500 },
    { seat: 4, name: "Rock", buyIn: 3000 },
  ],
];

// Action sequences to create variety
const ACTION_PLANS = [
  // All call to showdown
  "allcall",
  // One raise preflop, rest call
  "raise_call",
  // Fold to one player
  "fold_to_one",
  // Check through
  "check_around",
  // Raise and reraise
  "raise_reraise",
  // All-in preflop
  "allin",
  // Bet flop, call
  "bet_flop_call",
  // Mixed: some fold, some call
  "mixed",
];

async function clearTable(ws) {
  // Get current state and remove all seated players
  const resp = await sendCmd(ws, "GET_STATE");
  if (resp.ok && resp.state) {
    const seats = resp.state.seats;
    for (const [seatIdx, seat] of Object.entries(seats)) {
      if (seat.status === "OCCUPIED") {
        try {
          await sendCmd(ws, "LEAVE_TABLE", { seat: parseInt(seatIdx) });
        } catch (e) { /* ignore */ }
      }
    }
  }
}

async function seatPlayers(ws, players) {
  for (const p of players) {
    await sendCmd(ws, "SEAT_PLAYER", p);
  }
}

async function getState(ws) {
  const resp = await sendCmd(ws, "GET_STATE");
  return resp.ok ? resp.state : null;
}

async function getActiveSeatCount(ws) {
  const state = await getState(ws);
  if (!state || !state.seats) return 0;
  return Object.values(state.seats).filter(s => s.status === "OCCUPIED" && s.stack > 0).length;
}

async function playHand(ws, page, screenshotIdx, actionPlan) {
  let idx = screenshotIdx;

  // Start hand
  let resp;
  try {
    resp = await sendCmd(ws, "START_HAND");
  } catch (e) {
    console.log(`  Failed to start hand: ${e.message}`);
    return idx;
  }

  if (!resp.ok) {
    console.log(`  START_HAND failed: ${resp.error || "unknown error"}`);
    return idx;
  }

  // Screenshot after deal (preflop)
  await takeScreenshot(page, "preflop", idx++);

  // Play through actions
  let maxActions = 30; // safety limit
  let actionCount = 0;

  while (actionCount < maxActions) {
    const state = await getState(ws);
    if (!state || !state.hand) break; // hand is over

    const actionSeat = state.hand.actionSeat;
    if (actionSeat == null) {
      // No action needed - might be between phases or hand over
      await sleep(100);
      const state2 = await getState(ws);
      if (!state2 || !state2.hand) break;
      if (state2.hand.actionSeat == null) break;
      continue;
    }

    const legal = state.hand.legalActions;
    if (!legal || legal.actions.length === 0) break;

    // Choose action based on plan
    let action = "CALL";
    let amount = undefined;

    const actions = legal.actions;

    switch (actionPlan) {
      case "allcall":
        action = actions.includes("CALL") ? "CALL" : "CHECK";
        break;
      case "raise_call":
        if (actionCount === 0 && actions.includes("RAISE")) {
          action = "RAISE";
          amount = legal.minRaise || (state.hand.pot + 20);
        } else {
          action = actions.includes("CALL") ? "CALL" : "CHECK";
        }
        break;
      case "fold_to_one":
        if (actionCount > 0 && actions.includes("FOLD")) {
          action = "FOLD";
        } else {
          action = actions.includes("CALL") ? "CALL" : "CHECK";
        }
        break;
      case "check_around":
        action = actions.includes("CHECK") ? "CHECK" : "CALL";
        break;
      case "raise_reraise":
        if (actionCount < 3 && actions.includes("RAISE")) {
          action = "RAISE";
          amount = legal.minRaise;
        } else {
          action = actions.includes("CALL") ? "CALL" : "CHECK";
        }
        break;
      case "allin":
        if (actionCount === 0 && actions.includes("RAISE")) {
          action = "RAISE";
          amount = legal.maxRaise;
        } else {
          action = actions.includes("CALL") ? "CALL" : (actions.includes("FOLD") ? "FOLD" : "CHECK");
        }
        break;
      case "bet_flop_call":
        if (state.hand.phase !== "PREFLOP" && actionCount < 5 && actions.includes("RAISE")) {
          action = "RAISE";
          amount = legal.minRaise;
        } else {
          action = actions.includes("CALL") ? "CALL" : "CHECK";
        }
        break;
      case "mixed":
      default:
        // Random mix
        if (actionCount % 3 === 0 && actions.includes("FOLD") && actionCount > 1) {
          action = "FOLD";
        } else if (actionCount % 4 === 0 && actions.includes("RAISE")) {
          action = "RAISE";
          amount = legal.minRaise;
        } else {
          action = actions.includes("CALL") ? "CALL" : "CHECK";
        }
        break;
    }

    // Track phase before action
    const phaseBefore = state.hand.phase;

    try {
      const actionPayload = { seat: actionSeat, action };
      if (amount !== undefined) actionPayload.amount = amount;
      await sendCmd(ws, "PLAYER_ACTION", actionPayload);
    } catch (e) {
      console.log(`  Action failed: ${e.message}`);
      break;
    }

    actionCount++;

    // Check if phase changed or hand ended
    const stateAfter = await getState(ws);
    if (!stateAfter || !stateAfter.hand) {
      // Hand ended - take showdown/end screenshot
      await takeScreenshot(page, "showdown", idx++);
      break;
    }

    const phaseAfter = stateAfter.hand.phase;
    if (phaseAfter !== phaseBefore) {
      // Phase transition - take screenshot
      const phaseLabel = phaseAfter.toLowerCase();
      await takeScreenshot(page, phaseLabel, idx++);
    }
  }

  return idx;
}

(async () => {
  console.log("=== Poker Lab Screenshot Generator ===");
  console.log(`Output: ${OUT_DIR}`);
  console.log();

  // Launch browser
  const browser = await puppeteer.launch({ headless: "new" });
  const page = await browser.newPage();
  await page.setViewport({ width: 400, height: 740 });
  await page.goto(SERVER_URL, { waitUntil: "networkidle2", timeout: 15000 });
  await sleep(1000);

  // Connect WebSocket for game control
  const ws = new WebSocket(WS_URL);
  await waitForWs(ws);

  // Wait for welcome message
  await sleep(500);

  let screenshotIdx = 0;

  // Take initial empty table screenshot
  await takeScreenshot(page, "empty", screenshotIdx++);

  // Run multiple sessions with different player configurations
  for (let configIdx = 0; configIdx < PLAYER_CONFIGS.length; configIdx++) {
    const players = PLAYER_CONFIGS[configIdx];
    console.log(`\n--- Config ${configIdx + 1}: ${players.length} players ---`);

    // Clear and re-seat
    await clearTable(ws);
    await sleep(300);
    await seatPlayers(ws, players);
    await sleep(300);

    // Screenshot after seating (pre-hand)
    await takeScreenshot(page, "seated", screenshotIdx++);

    // Play multiple hands with different action plans per config
    const handsPerConfig = Math.ceil(20 / PLAYER_CONFIGS.length);
    for (let handNum = 0; handNum < handsPerConfig; handNum++) {
      const plan = ACTION_PLANS[handNum % ACTION_PLANS.length];
      console.log(`  Hand ${handNum + 1}/${handsPerConfig} (${plan}) - screenshot idx ${screenshotIdx}`);

      // Check we still have enough players
      const activeCount = await getActiveSeatCount(ws);
      if (activeCount < 2) {
        console.log(`  Only ${activeCount} active players, re-seating...`);
        await clearTable(ws);
        await sleep(200);
        await seatPlayers(ws, players);
        await sleep(200);
      }

      const newIdx = await playHand(ws, page, screenshotIdx, plan);

      if (newIdx === screenshotIdx) {
        // No screenshots taken - hand failed, try re-seating
        console.log("  Hand produced no screenshots, re-seating...");
        await clearTable(ws);
        await sleep(200);
        await seatPlayers(ws, players);
        await sleep(200);
      }

      screenshotIdx = newIdx;
      await sleep(200);
    }
  }

  // Additional round: extra hands to push past 100
  console.log("\n--- Extra hands for variety ---");
  const extraPlayers = PLAYER_CONFIGS[2]; // 6 players
  await clearTable(ws);
  await sleep(300);
  await seatPlayers(ws, extraPlayers);
  await sleep(300);

  while (screenshotIdx < 120) {
    const plan = ACTION_PLANS[screenshotIdx % ACTION_PLANS.length];
    console.log(`  Extra hand (${plan}) - screenshot idx ${screenshotIdx}`);

    const activeCount = await getActiveSeatCount(ws);
    if (activeCount < 2) {
      await clearTable(ws);
      await sleep(200);
      await seatPlayers(ws, extraPlayers);
      await sleep(200);
    }

    const newIdx = await playHand(ws, page, screenshotIdx, plan);
    if (newIdx === screenshotIdx) {
      await clearTable(ws);
      await sleep(200);
      await seatPlayers(ws, extraPlayers);
      await sleep(200);
      screenshotIdx++; // avoid infinite loop
    } else {
      screenshotIdx = newIdx;
    }
    await sleep(200);
  }

  console.log(`\n=== Done! Generated ${screenshotIdx} screenshots in ${OUT_DIR} ===`);

  ws.close();
  await browser.close();
  process.exit(0);
})().catch(err => {
  console.error("Fatal error:", err);
  process.exit(1);
});
