#!/usr/bin/env node
"use strict";

/**
 * Browser Bot — plays poker through real mouse and keyboard input.
 *
 * Each bot gets its own Chrome window, navigates to the table page,
 * and interacts exactly like a human:
 *   - Moves the mouse cursor to buttons and clicks them
 *   - Types into the bet input field with the keyboard
 *   - Reads the screen by looking at element positions and text
 *
 * You watch the browser windows to see cursors moving and clicking.
 *
 * Usage:
 *   node src/bot/browser-bot.js [options]
 *
 * Options:
 *   --url=http://localhost:9100   Server URL
 *   --bots=3                     Number of bots (2-6)
 *   --style=TAG                  Play style: TAG, LAG, ROCK, FISH, MIXED
 *   --delay=1500                 Think delay in ms
 *   --hands=50                   Max hands to play (0 = unlimited)
 *   --headless                   Run without visible browser (no cursor to watch)
 *   --chrome=/path/to/chrome     Chrome executable path
 */

const puppeteer = require("puppeteer-core");
const { decide } = require("./strategy");

// ── Config ────────────────────────────────────────────────────────────────

function parseArgs() {
  const args = process.argv.slice(2);
  const config = {
    url: "http://localhost:9100",
    bots: 3,
    style: "MIXED",
    delay: 1500,
    hands: 0,
    headless: false,
    chrome: null,
  };

  for (const arg of args) {
    const [key, ...rest] = arg.replace(/^--/, "").split("=");
    const val = rest.join("=");
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
  const fs = require("fs");
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
      ]
    : [
        "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser", "/usr/bin/chromium", "/snap/bin/chromium",
      ];

  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch {}
  }
  if (process.platform !== "win32") {
    for (const name of ["google-chrome-stable", "google-chrome", "chromium-browser", "chromium"]) {
      try { return execSync(`which ${name}`, { encoding: "utf8" }).trim(); } catch {}
    }
  }
  return null;
}

// ── Card parsing ──────────────────────────────────────────────────────────

const RANK_MAP = { "2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"T":10,"J":11,"Q":12,"K":13,"A":14 };
const SUIT_MAP = { "c":1,"d":2,"h":3,"s":4 };
function parseCard(display) {
  if (!display || display.length < 2) return null;
  const r = RANK_MAP[display[0]], s = SUIT_MAP[display[1]];
  return (r && s) ? { rank: r, suit: s, display } : null;
}

// ── Read visible screen state from the DOM ────────────────────────────────

const READ_STATE_FN = function (botSeatIdx) {
  const result = {
    connected: false, handActive: false, phase: null, pot: 0,
    board: [], myCards: [], myStack: 0, mySeatOccupied: false,
    actionOnMe: false, legalButtons: [], callAmount: 0,
    minBet: 0, minRaise: 0, maxRaise: 0, canDeal: false,
    seats: {}, button: -1, bb: 10,
  };

  const statusEl = document.getElementById("status");
  result.connected = statusEl && statusEl.classList.contains("connected");

  const phaseEl = document.getElementById("phase");
  result.phase = phaseEl ? phaseEl.textContent.trim() : null;
  result.handActive = !!result.phase && result.phase !== "";

  const potEl = document.getElementById("pot");
  if (potEl) { const m = potEl.textContent.match(/(\d+)/); if (m) result.pot = parseInt(m[1]); }

  document.querySelectorAll("#board .board-card:not(.empty)").forEach(el => {
    const t = el.textContent.trim(); if (t && t.length >= 2) result.board.push(t);
  });

  document.querySelectorAll(".seat[data-seat]").forEach(el => {
    const idx = parseInt(el.getAttribute("data-seat"));
    const seat = {
      empty: el.classList.contains("empty"), active: el.classList.contains("active"),
      folded: el.classList.contains("folded"), name: null, stack: 0, cards: [],
      isButton: false, allIn: false,
    };
    const nameEl = el.querySelector(".seat-name");
    if (nameEl) seat.name = nameEl.textContent.trim();
    const stackEl = el.querySelector(".seat-stack");
    if (stackEl) { const m = stackEl.textContent.match(/(\d+)/); if (m) seat.stack = parseInt(m[1]); }
    const cardsEl = el.querySelector(".seat-cards");
    if (cardsEl) {
      const t = cardsEl.textContent.trim();
      if (t && t !== "[**]") seat.cards = t.split(/\s+/).filter(c => c.length >= 2 && c !== "[**]");
    }
    const badgeEl = el.querySelector(".seat-badge");
    if (badgeEl) { const b = badgeEl.textContent; if (b.includes("BTN")) seat.isButton = true; if (b.includes("ALL-IN")) seat.allIn = true; }
    result.seats[idx] = seat;
    if (seat.isButton) result.button = idx;
    if (idx === botSeatIdx) {
      result.mySeatOccupied = !seat.empty; result.myCards = seat.cards;
      result.myStack = seat.stack; result.actionOnMe = seat.active;
    }
  });

  const btnMap = { FOLD: "fold-btn", CHECK: "check-btn", CALL: "call-btn", BET: "bet-btn", RAISE: "raise-btn" };
  for (const [action, id] of Object.entries(btnMap)) {
    const b = document.getElementById(id); if (b && !b.disabled) result.legalButtons.push(action);
  }

  const callBtn = document.getElementById("call-btn");
  if (callBtn) { const m = callBtn.textContent.match(/(\d+)/); if (m) result.callAmount = parseInt(m[1]); }

  const betInput = document.getElementById("bet-input");
  if (betInput) {
    result.minBet = parseInt(betInput.min) || 0;
    result.maxRaise = parseInt(betInput.max) || 0;
    result.minRaise = parseInt(betInput.value) || 0;
  }

  const startBtn = document.getElementById("start-btn");
  result.canDeal = startBtn && !startBtn.disabled;

  const infoEl = document.getElementById("table-info");
  if (infoEl) { const m = infoEl.textContent.match(/(\d+)c?\/(\d+)c?\b/); if (m) result.bb = parseInt(m[2]); }

  return result;
};

// ── Get element center coordinates for mouse targeting ────────────────────

async function getElementCenter(page, selector) {
  const box = await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, w: rect.width, h: rect.height };
  }, selector);
  return box;
}

// ── Human-like mouse movement (not instant teleport) ──────────────────────

async function humanMove(page, targetX, targetY) {
  const mouse = page.mouse;

  // Get current position (or start from center-ish)
  const vp = page.viewport();
  const startX = vp ? vp.width / 2 : 600;
  const startY = vp ? vp.height / 2 : 400;

  // Move in a few steps with slight jitter to look natural
  const steps = 8 + Math.floor(Math.random() * 6);
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    // Ease-out curve
    const ease = 1 - Math.pow(1 - t, 2);
    const jitterX = (Math.random() - 0.5) * 3;
    const jitterY = (Math.random() - 0.5) * 3;
    const x = startX + (targetX - startX) * ease + (i < steps ? jitterX : 0);
    const y = startY + (targetY - startY) * ease + (i < steps ? jitterY : 0);
    await mouse.move(x, y);
    await sleep(15 + Math.random() * 20);
  }

  // Final precise move to target
  await mouse.move(targetX, targetY);
}

async function humanClick(page, selector) {
  const box = await getElementCenter(page, selector);
  if (!box) throw new Error(`Element not found: ${selector}`);

  await humanMove(page, box.x, box.y);
  await sleep(50 + Math.random() * 100);
  await page.mouse.click(box.x, box.y);
}

async function humanType(page, selector, text) {
  // Click into the field first
  await humanClick(page, selector);
  await sleep(100);

  // Select all existing text
  await page.keyboard.down("Control");
  await page.keyboard.press("a");
  await page.keyboard.up("Control");
  await sleep(50);

  // Type each character with human-like delays
  for (const char of text) {
    await page.keyboard.type(char, { delay: 40 + Math.random() * 60 });
  }
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Bot Names & Styles ────────────────────────────────────────────────────

const BOT_NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"];
const MIXED_STYLES = ["TAG", "LAG", "ROCK", "TAG", "FISH", "TAG"];

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
  }

  log(msg) {
    const ts = new Date().toLocaleTimeString();
    console.log(`  [${ts}] \x1b[36m${this.name}\x1b[0m ${msg}`);
  }

  async waitForConnection() {
    await this.page.waitForFunction(() => {
      const el = document.getElementById("status");
      return el && el.classList.contains("connected");
    }, { timeout: 10000 });
    this.log("connected to server");
  }

  async sitDown() {
    // Set up dialog handlers before clicking
    const dialogPromise = new Promise((resolve) => {
      let dialogCount = 0;
      const handler = async (dialog) => {
        dialogCount++;
        if (dialogCount === 1) {
          // "Player name:" prompt
          await dialog.accept(this.name);
        } else if (dialogCount === 2) {
          // "Buy-in (cents):" prompt
          await dialog.accept(String(this.buyIn));
          this.page.off("dialog", handler);
          resolve();
        }
      };
      this.page.on("dialog", handler);
    });

    // Click the empty seat with real mouse movement
    this.log(`clicking seat ${this.seatIdx}...`);
    await humanClick(this.page, `.seat[data-seat="${this.seatIdx}"]`);

    // Wait for both dialogs to be handled
    await dialogPromise;
    await sleep(500);

    const state = await this.readState();
    if (state.mySeatOccupied) {
      this.seated = true;
      this.log(`sat down at seat ${this.seatIdx} (${this.buyIn} chips)`);
    } else {
      this.log("ERROR: failed to sit down");
    }
  }

  async readState() {
    return await this.page.evaluate(READ_STATE_FN, this.seatIdx);
  }

  async act() {
    const state = await this.readState();
    if (!state.connected || !state.handActive || !state.actionOnMe || state.legalButtons.length === 0) {
      return false;
    }

    // Read cards and board from the screen
    const holeCards = state.myCards.map(c => parseCard(c)).filter(Boolean);
    const board = state.board.map(c => parseCard(c)).filter(Boolean);
    const numPlayers = Object.values(state.seats).filter(s => !s.empty).length;

    // Run strategy
    const decision = decide({
      hand: { phase: state.phase, pot: state.pot, board, actions: [] },
      seat: { seat: this.seatIdx, stack: state.myStack, holeCards, bet: 0 },
      legalActions: {
        actions: state.legalButtons, callAmount: state.callAmount,
        minBet: state.minBet, minRaise: state.minRaise, maxRaise: state.maxRaise,
      },
      bb: state.bb,
      button: state.button,
      numPlayers,
      maxSeats: 6,
    }, this.style);

    // Think time — the bot pauses like a human would
    const thinkMs = this.delay + Math.floor(Math.random() * 800);
    await sleep(thinkMs);

    // Re-read the screen to make sure it's still our turn
    const fresh = await this.readState();
    if (!fresh.actionOnMe || fresh.legalButtons.length === 0) return false;

    // Execute the action with real mouse + keyboard
    await this.executeAction(decision, fresh);
    this.actionsTaken++;
    return true;
  }

  async executeAction(decision, state) {
    switch (decision.action) {
      case "FOLD":
        this.log(`\x1b[31mFOLD\x1b[0m (${state.phase})`);
        await humanClick(this.page, "#fold-btn");
        break;

      case "CHECK":
        this.log(`\x1b[33mCHECK\x1b[0m (${state.phase})`);
        await humanClick(this.page, "#check-btn");
        break;

      case "CALL":
        this.log(`\x1b[32mCALL ${state.callAmount}\x1b[0m (${state.phase})`);
        await humanClick(this.page, "#call-btn");
        break;

      case "BET":
        this.log(`\x1b[35mBET ${decision.amount}\x1b[0m (${state.phase})`);
        await humanType(this.page, "#bet-input", String(decision.amount));
        await sleep(200);
        await humanClick(this.page, "#bet-btn");
        break;

      case "RAISE":
        this.log(`\x1b[35mRAISE to ${decision.amount}\x1b[0m (${state.phase})`);
        await humanType(this.page, "#bet-input", String(decision.amount));
        await sleep(200);
        await humanClick(this.page, "#raise-btn");
        break;

      default:
        this.log(`unknown action ${decision.action}, checking/folding`);
        if (state.legalButtons.includes("CHECK")) await humanClick(this.page, "#check-btn");
        else await humanClick(this.page, "#fold-btn");
    }
  }

  async clickDeal() {
    this.log("dealing next hand...");
    await humanClick(this.page, "#start-btn");
    this.handsPlayed++;
  }
}

// ── Main ──────────────────────────────────────────────────────────────────

async function main() {
  const config = parseArgs();
  const chromePath = config.chrome || findChrome();

  if (!chromePath) {
    console.error("\nNo Chrome/Chromium found. Provide the path with --chrome=");
    console.error("  Windows:  --chrome=\"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\"");
    console.error("  macOS:    --chrome=\"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\"");
    console.error("  Linux:    --chrome=/usr/bin/google-chrome-stable");
    process.exit(1);
  }

  console.log();
  console.log("  \x1b[36mPoker Bot — Browser Automation\x1b[0m");
  console.log("  " + "─".repeat(40));
  console.log(`  Server:  ${config.url}`);
  console.log(`  Bots:    ${config.bots}`);
  console.log(`  Style:   ${config.style}`);
  console.log(`  Delay:   ${config.delay}ms`);
  console.log(`  Hands:   ${config.hands || "unlimited"}`);
  console.log(`  Chrome:  ${chromePath}`);
  console.log();

  // Launch a separate browser instance for each bot — each gets its own window + cursor
  const browsers = [];
  const bots = [];

  for (let i = 0; i < config.bots; i++) {
    const style = config.style === "MIXED" ? MIXED_STYLES[i % MIXED_STYLES.length] : config.style;

    const browser = await puppeteer.launch({
      executablePath: chromePath,
      headless: config.headless,
      defaultViewport: { width: 1100, height: 750 },
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        `--window-size=1100,750`,
        `--window-position=${150 + i * 60},${80 + i * 50}`,
      ],
    });
    browsers.push(browser);

    const page = (await browser.pages())[0];
    const bot = new BrowserBot(page, i, BOT_NAMES[i], 1000, style, config.delay);
    bots.push(bot);

    console.log(`  Launched window for \x1b[36m${bot.name}\x1b[0m (seat ${i}, ${style})`);
  }

  // Navigate all windows to the table
  console.log("\n  Navigating to table...");
  await Promise.all(bots.map(b => b.page.goto(config.url, { waitUntil: "domcontentloaded" })));
  await Promise.all(bots.map(b => b.waitForConnection()));
  console.log("  All connected.\n");

  // Each bot clicks on its seat to sit down
  for (const bot of bots) {
    await bot.sitDown();
    await sleep(400);
  }

  console.log("\n  All bots seated. Starting play.\n");
  console.log("  " + "═".repeat(50));

  // ── Game loop ───────────────────────────────────────────────────────────

  const dealer = bots[0];
  let totalHands = 0;
  let running = true;

  process.on("SIGINT", () => { console.log("\n  Stopping..."); running = false; });

  while (running) {
    if (config.hands > 0 && totalHands >= config.hands) {
      console.log(`\n  Hand limit reached (${config.hands}).`);
      break;
    }

    // Dealer bot checks if Deal button is available and clicks it
    const dealerState = await dealer.readState();
    if (dealerState.canDeal && !dealerState.handActive) {
      totalHands++;
      console.log(`\n  ── Hand #${totalHands} ${"─".repeat(38)}`);
      await dealer.clickDeal();
      await sleep(600);
    }

    // Each bot checks if it's their turn and acts
    let anyActed = false;
    for (const bot of bots) {
      if (!running) break;
      try {
        const acted = await bot.act();
        if (acted) { anyActed = true; await sleep(300); }
      } catch (err) {
        if (err.message && err.message.includes("SHOWDOWN")) {
          console.log("  [!] Showdown reached (GAP-1). Waiting...");
          await sleep(3000);
          break;
        }
        bot.log(`error: ${err.message}`);
      }
    }

    if (!anyActed) await sleep(250);
  }

  // ── Summary ─────────────────────────────────────────────────────────────
  console.log("\n  " + "═".repeat(50));
  console.log("  Summary");
  console.log("  " + "═".repeat(50));
  console.log(`  Hands dealt: ${totalHands}`);
  for (const bot of bots) {
    const st = await bot.readState();
    console.log(`  ${bot.name} (${bot.style}): ${st.myStack}c | ${bot.actionsTaken} actions`);
  }

  if (!config.headless) {
    console.log("\n  Browser windows left open. Ctrl+C to close.\n");
    await new Promise(resolve => process.on("SIGINT", resolve));
  }

  for (const b of browsers) await b.close();
  process.exit(0);
}

main().catch(err => { console.error("Fatal:", err.message); process.exit(1); });
