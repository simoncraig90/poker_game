#!/usr/bin/env node
"use strict";

/**
 * Browser Bot — plays poker through the actual browser UI.
 *
 * Each bot is a real browser tab on the table page. It reads cards, pot,
 * and button states from the DOM, makes strategy decisions, and clicks
 * the UI buttons — exactly like a human would.
 *
 * Usage:
 *   node src/bot/browser-bot.js [options]
 *
 * Options:
 *   --url=http://localhost:9100   Server URL
 *   --bots=3                     Number of bots (2-6)
 *   --style=TAG                  Play style: TAG, LAG, ROCK, FISH
 *   --delay=1000                 Think delay in ms (how long to "think")
 *   --hands=50                   Max hands to play (0 = unlimited)
 *   --headless                   Run without visible browser
 *   --chrome=/path/to/chrome     Chrome executable path
 */

const puppeteer = require("puppeteer-core");
const { decide } = require("./strategy");
const { preflopScore } = require("./hand-strength");

// ── Config ────────────────────────────────────────────────────────────────

function parseArgs() {
  const args = process.argv.slice(2);
  const config = {
    url: "http://localhost:9100",
    bots: 3,
    style: "TAG",
    delay: 1000,
    hands: 0,
    headless: false,
    chrome: null,
  };

  for (const arg of args) {
    const [key, val] = arg.replace(/^--/, "").split("=");
    if (key === "url") config.url = val;
    if (key === "bots") config.bots = Math.min(6, Math.max(2, parseInt(val)));
    if (key === "style") config.style = val.toUpperCase();
    if (key === "delay") config.delay = parseInt(val);
    if (key === "hands") config.hands = parseInt(val);
    if (key === "headless") config.headless = true;
    if (key === "chrome") config.chrome = val;
  }

  return config;
}

// ── Chrome finder ─────────────────────────────────────────────────────────

function findChrome() {
  const { execSync } = require("child_process");
  const candidates = process.platform === "win32"
    ? [
        process.env["PROGRAMFILES(X86)"] + "\\Google\\Chrome\\Application\\chrome.exe",
        process.env["PROGRAMFILES"] + "\\Google\\Chrome\\Application\\chrome.exe",
        process.env.LOCALAPPDATA + "\\Google\\Chrome\\Application\\chrome.exe",
        process.env["PROGRAMFILES(X86)"] + "\\Microsoft\\Edge\\Application\\msedge.exe",
        process.env["PROGRAMFILES"] + "\\Microsoft\\Edge\\Application\\msedge.exe",
      ]
    : process.platform === "darwin"
    ? [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
      ]
    : [
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
      ];

  const fs = require("fs");
  for (const path of candidates) {
    try { if (fs.existsSync(path)) return path; } catch {}
  }

  // Try `which` on unix
  if (process.platform !== "win32") {
    for (const name of ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium"]) {
      try {
        const result = execSync(`which ${name}`, { encoding: "utf8" }).trim();
        if (result) return result;
      } catch {}
    }
  }

  return null;
}

// ── Card parsing ──────────────────────────────────────────────────────────

const RANK_MAP = { "2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"T":10,"J":11,"Q":12,"K":13,"A":14 };
const SUIT_MAP = { "c":1,"d":2,"h":3,"s":4 };

function parseCard(display) {
  if (!display || display.length < 2) return null;
  const r = RANK_MAP[display[0]];
  const s = SUIT_MAP[display[1]];
  if (!r || !s) return null;
  return { rank: r, suit: s, display };
}

// ── DOM State Reader ──────────────────────────────────────────────────────
// Runs inside the browser page via page.evaluate()

const READ_STATE_FN = function (botSeatIdx) {
  // Read game state from the DOM exactly as a human would see it

  const result = {
    connected: false,
    handActive: false,
    phase: null,
    pot: 0,
    board: [],
    myCards: [],
    myStack: 0,
    mySeatOccupied: false,
    actionOnMe: false,
    legalButtons: [],
    callAmount: null,
    minBet: null,
    minRaise: null,
    maxRaise: null,
    betInputValue: null,
    canDeal: false,
    seats: {},
    button: -1,
    bb: 10,
  };

  // Connection status
  const statusEl = document.getElementById("status");
  result.connected = statusEl && statusEl.classList.contains("connected");

  // Phase
  const phaseEl = document.getElementById("phase");
  result.phase = phaseEl ? phaseEl.textContent.trim() : null;
  result.handActive = !!result.phase && result.phase !== "";

  // Pot
  const potEl = document.getElementById("pot");
  if (potEl) {
    const potText = potEl.textContent.trim();
    const match = potText.match(/(\d+)/);
    if (match) result.pot = parseInt(match[1]);
  }

  // Board cards
  const boardCards = document.querySelectorAll("#board .board-card:not(.empty)");
  boardCards.forEach(el => {
    const text = el.textContent.trim();
    if (text && text.length >= 2) result.board.push(text);
  });

  // Read all seats
  const seatEls = document.querySelectorAll(".seat[data-seat]");
  seatEls.forEach(el => {
    const idx = parseInt(el.getAttribute("data-seat"));
    const isEmpty = el.classList.contains("empty");
    const isActive = el.classList.contains("active");
    const isFolded = el.classList.contains("folded");
    const nameEl = el.querySelector(".seat-name");
    const stackEl = el.querySelector(".seat-stack");
    const cardsEl = el.querySelector(".seat-cards");
    const badgeEl = el.querySelector(".seat-badge");

    const seat = {
      empty: isEmpty,
      active: isActive,
      folded: isFolded,
      name: nameEl ? nameEl.textContent.trim() : null,
      stack: 0,
      cards: [],
      isButton: false,
      allIn: false,
    };

    if (stackEl) {
      const stackText = stackEl.textContent.trim();
      const m = stackText.match(/(\d+)/);
      if (m) seat.stack = parseInt(m[1]);
    }

    if (cardsEl) {
      const cardText = cardsEl.textContent.trim();
      if (cardText && cardText !== "[**]") {
        // Cards are like "As 2h" or "Td Kc"
        seat.cards = cardText.split(/\s+/).filter(c => c.length >= 2 && c !== "[**]");
      }
    }

    if (badgeEl) {
      const badge = badgeEl.textContent;
      if (badge.includes("BTN")) seat.isButton = true;
      if (badge.includes("ALL-IN")) seat.allIn = true;
    }

    result.seats[idx] = seat;

    if (idx === botSeatIdx) {
      result.mySeatOccupied = !isEmpty;
      result.myCards = seat.cards;
      result.myStack = seat.stack;
      result.actionOnMe = isActive;
      if (seat.isButton) result.button = idx;
    }

    if (seat.isButton) result.button = idx;
  });

  // Legal action buttons
  const buttons = {
    FOLD: document.getElementById("fold-btn"),
    CHECK: document.getElementById("check-btn"),
    CALL: document.getElementById("call-btn"),
    BET: document.getElementById("bet-btn"),
    RAISE: document.getElementById("raise-btn"),
  };

  for (const [action, btn] of Object.entries(buttons)) {
    if (btn && !btn.disabled) result.legalButtons.push(action);
  }

  // Call amount from button text
  const callBtn = document.getElementById("call-btn");
  if (callBtn) {
    const callText = callBtn.textContent;
    const m = callText.match(/(\d+)/);
    if (m) result.callAmount = parseInt(m[1]);
  }

  // Bet input
  const betInput = document.getElementById("bet-input");
  if (betInput) {
    result.betInputValue = parseInt(betInput.value) || 0;
    result.minBet = parseInt(betInput.min) || 0;
    result.maxRaise = parseInt(betInput.max) || 0;
    result.minRaise = result.betInputValue; // default value is set to min raise by table.js
  }

  // Deal button
  const startBtn = document.getElementById("start-btn");
  result.canDeal = startBtn && !startBtn.disabled;

  // BB from table info (format: "tableName | 5c/10c | ...")
  const infoEl = document.getElementById("table-info");
  if (infoEl) {
    const m = infoEl.textContent.match(/(\d+)c?\/(\d+)c?\b/);
    if (m) result.bb = parseInt(m[2]);
  }

  return result;
};

// ── Bot Names ─────────────────────────────────────────────────────────────

const BOT_NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"];

// ── Single Bot Controller ─────────────────────────────────────────────────

class BrowserBot {
  constructor(page, seatIdx, name, buyIn, style, delay) {
    this.page = page;
    this.seatIdx = seatIdx;
    this.name = name;
    this.buyIn = buyIn;
    this.style = style;
    this.delay = delay;
    this.handsPlayed = 0;
    this.actionsTaken = 0;
    this.seated = false;
    this.running = false;
  }

  log(msg) {
    const ts = new Date().toLocaleTimeString();
    console.log(`  [${ts}] [${this.name}] ${msg}`);
  }

  async waitForConnection() {
    this.log("Waiting for connection...");
    await this.page.waitForFunction(
      () => {
        const el = document.getElementById("status");
        return el && el.classList.contains("connected");
      },
      { timeout: 10000 }
    );
    this.log("Connected");
  }

  async sitDown() {
    // Handle the prompt() dialogs that seatClick() triggers
    this.page.once("dialog", async (dialog) => {
      // First prompt: "Player name:"
      await dialog.accept(this.name);
    });

    // Click the empty seat
    const seatSelector = `.seat[data-seat="${this.seatIdx}"]`;
    await this.page.click(seatSelector);

    // Second prompt: "Buy-in (cents):"
    await new Promise(resolve => {
      this.page.once("dialog", async (dialog) => {
        await dialog.accept(String(this.buyIn));
        resolve();
      });
    });

    // Wait a moment for the server to process
    await this.page.waitForTimeout(500);

    // Verify we're seated
    const state = await this.readState();
    if (state.mySeatOccupied) {
      this.seated = true;
      this.log(`Seated at seat ${this.seatIdx} with ${this.buyIn} chips`);
    } else {
      this.log("ERROR: Failed to seat");
    }
  }

  async readState() {
    return await this.page.evaluate(READ_STATE_FN, this.seatIdx);
  }

  async act() {
    const state = await this.readState();

    if (!state.connected || !state.handActive || !state.actionOnMe) {
      return false;
    }

    if (state.legalButtons.length === 0) {
      return false;
    }

    // Parse our hole cards
    const holeCards = state.myCards.map(c => parseCard(c)).filter(Boolean);

    // Parse board
    const board = state.board.map(c => parseCard(c)).filter(Boolean);

    // Count players and build minimal hand state for strategy
    const occupiedSeats = Object.entries(state.seats)
      .filter(([_, s]) => !s.empty)
      .map(([idx, s]) => ({ seat: parseInt(idx), ...s }));
    const numPlayers = occupiedSeats.length;

    // Build legalActions object matching what strategy expects
    const legalActions = {
      actions: state.legalButtons,
      callAmount: state.callAmount || 0,
      minBet: state.minBet || 0,
      minRaise: state.minRaise || 0,
      maxRaise: state.maxRaise || state.myStack,
    };

    // Build hand state for strategy
    const handState = {
      phase: state.phase,
      pot: state.pot,
      board: board,
      actions: [], // We can't easily reconstruct the full action history from DOM
    };

    const seatState = {
      seat: this.seatIdx,
      stack: state.myStack,
      holeCards: holeCards,
      bet: 0, // Can't read individual bet easily from DOM, strategy handles this
    };

    // Make decision
    const decision = decide({
      hand: handState,
      seat: seatState,
      legalActions,
      bb: state.bb,
      button: state.button,
      numPlayers,
      maxSeats: 6,
    }, this.style);

    // Simulate human think time
    const thinkTime = this.delay + Math.floor(Math.random() * 500);
    await this.page.waitForTimeout(thinkTime);

    // Re-check state (might have changed during think time)
    const freshState = await this.readState();
    if (!freshState.actionOnMe || freshState.legalButtons.length === 0) {
      return false;
    }

    // Execute the action by clicking the actual UI buttons
    await this.clickAction(decision, freshState);
    this.actionsTaken++;
    return true;
  }

  async clickAction(decision, state) {
    const action = decision.action;

    switch (action) {
      case "FOLD":
        this.log(`FOLD (${state.phase})`);
        await this.page.click("#fold-btn");
        break;

      case "CHECK":
        this.log(`CHECK (${state.phase})`);
        await this.page.click("#check-btn");
        break;

      case "CALL":
        this.log(`CALL ${state.callAmount || "?"} (${state.phase})`);
        await this.page.click("#call-btn");
        break;

      case "BET":
        this.log(`BET ${decision.amount} (${state.phase})`);
        // Clear input, type amount, click bet
        await this.page.click("#bet-input", { clickCount: 3 });
        await this.page.type("#bet-input", String(decision.amount));
        await this.page.click("#bet-btn");
        break;

      case "RAISE":
        this.log(`RAISE to ${decision.amount} (${state.phase})`);
        // Clear input, type amount, click raise
        await this.page.click("#bet-input", { clickCount: 3 });
        await this.page.type("#bet-input", String(decision.amount));
        await this.page.click("#raise-btn");
        break;

      default:
        this.log(`Unknown action: ${action}, falling back to FOLD`);
        if (state.legalButtons.includes("CHECK")) {
          await this.page.click("#check-btn");
        } else {
          await this.page.click("#fold-btn");
        }
    }
  }

  async clickDeal() {
    this.log("Dealing next hand...");
    await this.page.click("#start-btn");
    this.handsPlayed++;
  }
}

// ── Main Orchestrator ─────────────────────────────────────────────────────

async function main() {
  const config = parseArgs();
  const chromePath = config.chrome || findChrome();

  if (!chromePath) {
    console.error("\nNo Chrome/Chromium found. Specify the path:");
    console.error("  node src/bot/browser-bot.js --chrome=\"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\"");
    console.error("\nCommon paths:");
    console.error("  Windows: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe");
    console.error("  macOS:   /Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
    console.error("  Linux:   /usr/bin/google-chrome-stable");
    process.exit(1);
  }

  console.log("╔══════════════════════════════════════════════════════════════╗");
  console.log("║  Poker Bot — Browser Automation                            ║");
  console.log("╚══════════════════════════════════════════════════════════════╝");
  console.log(`  Server:  ${config.url}`);
  console.log(`  Bots:    ${config.bots}`);
  console.log(`  Style:   ${config.style}`);
  console.log(`  Delay:   ${config.delay}ms`);
  console.log(`  Hands:   ${config.hands || "unlimited"}`);
  console.log(`  Chrome:  ${chromePath}`);
  console.log(`  Mode:    ${config.headless ? "headless" : "visible"}`);
  console.log();

  // Launch browser
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    headless: config.headless,
    defaultViewport: { width: 1200, height: 800 },
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--window-size=1200,800",
    ],
  });

  // Create a page (tab) for each bot
  const bots = [];
  for (let i = 0; i < config.bots; i++) {
    const page = i === 0 ? (await browser.pages())[0] : await browser.newPage();
    const bot = new BrowserBot(
      page, i, BOT_NAMES[i], 1000, config.style, config.delay
    );
    bots.push(bot);
  }

  // Navigate all tabs to the table
  console.log("  Opening table in browser tabs...");
  await Promise.all(bots.map(bot => bot.page.goto(config.url, { waitUntil: "domcontentloaded" })));

  // Wait for WebSocket connections
  await Promise.all(bots.map(bot => bot.waitForConnection()));
  console.log(`  All ${bots.length} bots connected.\n`);

  // Seat all bots (stagger to avoid races)
  for (const bot of bots) {
    await bot.sitDown();
    await bot.page.waitForTimeout(300);
  }

  console.log(`\n  All bots seated. Starting play loop...\n`);
  console.log("─".repeat(60));

  // ── Main game loop ────────────────────────────────────────────────────
  // The "dealer bot" (bot 0) is responsible for clicking Deal.
  // All bots watch for their turn and click action buttons.

  const dealer = bots[0];
  let totalHands = 0;
  let running = true;

  // Handle Ctrl+C gracefully
  process.on("SIGINT", () => {
    console.log("\n\n  Stopping bots...");
    running = false;
  });

  while (running) {
    // Check if we've hit the hand limit
    if (config.hands > 0 && totalHands >= config.hands) {
      console.log(`\n  Reached hand limit (${config.hands}). Stopping.`);
      break;
    }

    // Dealer checks if a new hand can be started
    const dealerState = await dealer.readState();

    if (dealerState.canDeal && !dealerState.handActive) {
      totalHands++;
      console.log(`\n── Hand #${totalHands} ${"─".repeat(45)}`);
      await dealer.clickDeal();
      await dealer.page.waitForTimeout(500);
    }

    // Poll all bots for action opportunities
    let anyActed = false;
    for (const bot of bots) {
      if (!running) break;
      try {
        const acted = await bot.act();
        if (acted) anyActed = true;
      } catch (err) {
        // Check for showdown error (broadcast via error toast)
        const toastText = await bot.page.evaluate(() => {
          const toast = document.getElementById("error-toast");
          return toast ? toast.textContent : "";
        });
        if (toastText.includes("SHOWDOWN")) {
          console.log(`  [!] Hand reached showdown (GAP-1 — not implemented)`);
          await bot.page.waitForTimeout(2000);
          break;
        }
        bot.log(`Error: ${err.message}`);
      }
    }

    // Small poll interval to avoid hammering CPU
    if (!anyActed) {
      await bots[0].page.waitForTimeout(200);
    }
  }

  // ── Summary ─────────────────────────────────────────────────────────────
  console.log("\n" + "═".repeat(60));
  console.log("  Session Summary");
  console.log("═".repeat(60));
  console.log(`  Total hands dealt: ${totalHands}`);
  for (const bot of bots) {
    const state = await bot.readState();
    console.log(`  ${bot.name} (Seat ${bot.seatIdx}): ${state.myStack}c | ${bot.actionsTaken} actions`);
  }
  console.log();

  // Keep browser open in visible mode so user can inspect
  if (!config.headless) {
    console.log("  Browser left open for inspection. Press Ctrl+C to close.\n");
    await new Promise(resolve => process.on("SIGINT", resolve));
  }

  await browser.close();
  process.exit(0);
}

main().catch(err => {
  console.error("Fatal:", err.message);
  process.exit(1);
});
