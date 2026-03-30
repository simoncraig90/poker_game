#!/usr/bin/env node
"use strict";

/**
 * Adversarial Bot vs Detector runner.
 *
 * Plays hands against the server via the REST API, using configurable
 * evasion levels. Returns detection results for each round.
 *
 * Can be called:
 *   1. Directly:     node src/detection/adversarial.js --url=http://localhost:9100
 *   2. From n8n:     HTTP Request node POST to /api/adversarial/run
 *   3. Programmatic: require("./adversarial").runRound(config)
 *
 * Evasion levels:
 *   0 — No evasion: no telemetry at all (raw WS bot)
 *   1 — Basic: sends fake telemetry with fixed values
 *   2 — Moderate: randomized telemetry, but still pattern-detectable
 *   3 — Advanced: realistic mouse trails, varied timing, imprecise clicks
 *   4 — Expert: statistically modeled human behavior
 */

const http = require("http");
const { decide } = require("../bot/strategy");

// ── Card parsing ──────────────────────────────────────────────────────────

const RANK_MAP = { "2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"T":10,"J":11,"Q":12,"K":13,"A":14 };
const SUIT_MAP = { "c":1,"d":2,"h":3,"s":4 };
function parseCard(d) {
  if (typeof d === "object" && d.rank) return d;
  return { rank: RANK_MAP[d[0]] || 0, suit: SUIT_MAP[d[1]] || 0, display: d };
}
function parseCards(a) { return a ? a.map(c => typeof c === "string" ? parseCard(c) : c) : []; }

// ── HTTP helpers ──────────────────────────────────────────────────────────

function apiCall(baseUrl, method, path, body) {
  return new Promise((resolve, reject) => {
    const url = new URL(path, baseUrl);
    const options = {
      hostname: url.hostname, port: url.port, path: url.pathname,
      method, headers: { "Content-Type": "application/json" },
    };
    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => { data += chunk; });
      res.on("end", () => {
        try { resolve(JSON.parse(data)); }
        catch { resolve({ ok: false, error: "Invalid JSON response" }); }
      });
    });
    req.on("error", reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

// ── Fake telemetry generators by evasion level ────────────────────────────

function generateTelemetry(evasionLevel) {
  switch (evasionLevel) {
    case 0: return null; // No telemetry

    case 1: // Basic: fixed fake values
      return {
        reactionMs: 1000, timeSinceMouseMove: 50, trailLength: 20,
        trailDistance: 300, trailStraightness: 0.5, trailAvgSpeed: 0.5,
        trailSpeedVariance: 0.1, trailDirectionChanges: 5, trailPauses: 0,
        clickOffsetX: 3, clickOffsetY: 2, clickDistFromCenter: 3.6,
        keystrokeCount: 0, keystrokeAvgInterval: null, keystrokeVariance: null,
        tabFocused: true, tabBlurCount: 0, scrollCount: 0, idleStreak: 0,
        ts: Date.now(),
      };

    case 2: // Moderate: randomized but detectable patterns
      return {
        reactionMs: 800 + Math.random() * 400,
        timeSinceMouseMove: 20 + Math.random() * 100,
        trailLength: 15 + Math.floor(Math.random() * 20),
        trailDistance: 200 + Math.random() * 200,
        trailStraightness: 0.7 + Math.random() * 0.2,  // still too straight
        trailAvgSpeed: 0.4 + Math.random() * 0.3,
        trailSpeedVariance: 0.03 + Math.random() * 0.05, // still too consistent
        trailDirectionChanges: 2 + Math.floor(Math.random() * 4),
        trailPauses: 0,
        clickOffsetX: (Math.random() - 0.5) * 8,
        clickOffsetY: (Math.random() - 0.5) * 6,
        clickDistFromCenter: 1 + Math.random() * 5,
        keystrokeCount: 0, keystrokeAvgInterval: null, keystrokeVariance: null,
        tabFocused: true, tabBlurCount: 0, scrollCount: 0, idleStreak: 0,
        ts: Date.now(),
      };

    case 3: // Advanced: realistic-looking
      return {
        reactionMs: 1200 + Math.random() * 6000,  // wide human-like variance
        timeSinceMouseMove: 30 + Math.random() * 300,
        trailLength: 25 + Math.floor(Math.random() * 60),
        trailDistance: 150 + Math.random() * 500,
        trailStraightness: 0.2 + Math.random() * 0.5,  // curved paths
        trailAvgSpeed: 0.2 + Math.random() * 0.7,
        trailSpeedVariance: 0.08 + Math.random() * 0.4,  // variable speed
        trailDirectionChanges: 4 + Math.floor(Math.random() * 12),
        trailPauses: Math.floor(Math.random() * 3),
        clickOffsetX: (Math.random() - 0.5) * 20,
        clickOffsetY: (Math.random() - 0.5) * 14,
        clickDistFromCenter: 2 + Math.random() * 15,
        keystrokeCount: Math.random() > 0.7 ? 2 + Math.floor(Math.random() * 3) : 0,
        keystrokeAvgInterval: 100 + Math.random() * 100,
        keystrokeVariance: 150 + Math.random() * 400,
        tabFocused: Math.random() > 0.05,
        tabBlurCount: Math.floor(Math.random() * 2),
        scrollCount: Math.floor(Math.random() * 4),
        idleStreak: 0,
        ts: Date.now(),
      };

    case 4: // Expert: statistically modeled
    default: {
      // Log-normal reaction time (right-skewed like real humans)
      const mu = 7.5, sigma = 0.8; // median ~1800ms
      const z = Math.sqrt(-2 * Math.log(Math.random())) * Math.cos(2 * Math.PI * Math.random());
      const reactionMs = Math.exp(mu + sigma * z);

      // Brownian-motion-style trail variance
      const trailLen = 30 + Math.floor(Math.random() * 80);
      const baseSpeed = 0.3 + Math.random() * 0.5;

      return {
        reactionMs: Math.max(400, reactionMs),
        timeSinceMouseMove: 20 + Math.random() * 500,
        trailLength: trailLen,
        trailDistance: trailLen * (10 + Math.random() * 15),
        trailStraightness: 0.15 + Math.random() * 0.45,
        trailAvgSpeed: baseSpeed,
        trailSpeedVariance: baseSpeed * (0.3 + Math.random() * 0.7),
        trailDirectionChanges: Math.floor(trailLen * (0.1 + Math.random() * 0.2)),
        trailPauses: Math.floor(Math.random() * 4),
        clickOffsetX: (Math.random() - 0.5) * 24,
        clickOffsetY: (Math.random() - 0.5) * 16,
        clickDistFromCenter: Math.abs(3 + (Math.random() - 0.5) * 20),
        keystrokeCount: Math.random() > 0.65 ? 2 + Math.floor(Math.random() * 4) : 0,
        keystrokeAvgInterval: 90 + Math.random() * 120,
        keystrokeVariance: 100 + Math.random() * 600,
        tabFocused: Math.random() > 0.08,
        tabBlurCount: Math.floor(Math.random() * 3),
        scrollCount: Math.floor(Math.random() * 6),
        idleStreak: 0,
        ts: Date.now(),
      };
    }
  }
}

// ── Run a single round ────────────────────────────────────────────────────

async function runRound(config) {
  const {
    url = "http://localhost:9100",
    hands = 10,
    bots = 3,
    evasionLevel = 0,
    style = "TAG",
    buyIn = 1000,
  } = config;

  const api = (method, path, body) => apiCall(url, method, path, body);

  // Reset detection profiles
  await api("POST", "/api/detection/reset");

  // Seat bots
  const botNames = ["Bot-A", "Bot-B", "Bot-C", "Bot-D", "Bot-E", "Bot-F"];
  for (let i = 0; i < bots; i++) {
    await api("POST", "/api/seat", { seat: i, name: botNames[i], buyIn });
  }

  // Track cards per seat
  const holeCards = {};
  let handsPlayed = 0;
  let totalActions = 0;
  let errors = [];

  for (let h = 0; h < hands; h++) {
    // Deal
    const dealResult = await api("POST", "/api/deal");
    if (!dealResult.ok) {
      errors.push({ hand: h + 1, error: dealResult.error });
      continue;
    }

    // Capture hole cards from deal events
    for (const e of (dealResult.events || [])) {
      if (e.type === "HERO_CARDS") holeCards[e.seat] = parseCards(e.cards);
      if (e.type === "HAND_START") { for (const k of Object.keys(holeCards)) delete holeCards[k]; }
    }
    for (const e of (dealResult.events || [])) {
      if (e.type === "HERO_CARDS") holeCards[e.seat] = parseCards(e.cards);
    }

    // Play the hand
    let safety = 0;
    while (safety++ < 50) {
      const stateRes = await api("GET", "/api/state");
      if (!stateRes.ok) break;
      const state = stateRes.state;
      const hand = state.hand;
      if (!hand || hand.phase === "COMPLETE" || hand.actionSeat == null) break;

      const seat = hand.actionSeat;
      const seatState = state.seats[seat];
      const legal = hand.legalActions;
      if (!legal || legal.actions.length === 0) break;

      const decision = decide({
        hand: { ...hand, board: parseCards(hand.board) },
        seat: { ...seatState, seat, holeCards: holeCards[seat] || parseCards(seatState.holeCards) },
        legalActions: legal, bb: state.bb,
        button: hand.button != null ? hand.button : state.button,
        numPlayers: Object.values(state.seats).filter(s => s.status === "OCCUPIED").length,
        maxSeats: state.maxSeats,
      });

      const telemetry = generateTelemetry(evasionLevel);
      const payload = { seat, action: decision.action, _telemetry: telemetry };
      if (decision.amount != null) payload.amount = decision.amount;

      const actResult = await api("POST", "/api/action", payload);
      if (!actResult.ok) {
        if (actResult.error && actResult.error.includes("SHOWDOWN")) {
          errors.push({ hand: h + 1, error: "showdown_gap" });
          break;
        }
        errors.push({ hand: h + 1, seat, error: actResult.error });
        break;
      }
      totalActions++;

      // Capture cards from action events
      for (const e of (actResult.events || [])) {
        if (e.type === "HERO_CARDS") holeCards[e.seat] = parseCards(e.cards);
      }
    }

    handsPlayed++;
  }

  // Get detection results
  const detectionRes = await api("GET", "/api/detection");
  const profiles = detectionRes.ok ? detectionRes.profiles : [];

  // Clean up: remove bots
  for (let i = 0; i < bots; i++) {
    try { await api("POST", "/api/leave", { seat: i }); } catch {}
  }

  // Build report
  const report = {
    config: { hands, bots, evasionLevel, style },
    results: {
      handsPlayed,
      totalActions,
      errors: errors.length,
      errorDetails: errors.slice(0, 5),
    },
    detection: profiles.map(p => ({
      seat: p.seat,
      actions: p.actionCount,
      avgScore: parseFloat((p.avgScore || 0).toFixed(3)),
      currentScore: parseFloat((p.currentScore || 0).toFixed(3)),
      flags: p.flagCount,
      warns: p.warnCount,
      topSignals: p.signals ? Object.entries(p.signals)
        .filter(([_, v]) => v > 0.1)
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => ({ signal: k, score: parseFloat(v.toFixed(3)) }))
        : [],
    })),
    summary: {
      detected: profiles.filter(p => p.flagCount > 0).length,
      warned: profiles.filter(p => p.warnCount > 0 && p.flagCount === 0).length,
      evaded: profiles.filter(p => p.flagCount === 0 && p.warnCount === 0 && p.actionCount >= 5).length,
      avgScore: profiles.length > 0
        ? parseFloat((profiles.reduce((s, p) => s + (p.avgScore || 0), 0) / profiles.length).toFixed(3))
        : 0,
    },
    ts: new Date().toISOString(),
  };

  return report;
}

// ── CLI mode ──────────────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);
  const config = {
    url: "http://localhost:9100",
    hands: 10,
    bots: 3,
    rounds: 5,
    style: "TAG",
  };

  for (const arg of args) {
    const [key, ...rest] = arg.replace(/^--/, "").split("=");
    const val = rest.join("=");
    if (key === "url") config.url = val;
    if (key === "hands") config.hands = parseInt(val);
    if (key === "bots") config.bots = parseInt(val);
    if (key === "rounds") config.rounds = parseInt(val);
    if (key === "style") config.style = val;
  }

  console.log("\n  Adversarial Bot vs Detector");
  console.log("  " + "─".repeat(40));
  console.log(`  Server: ${config.url}`);
  console.log(`  Hands/round: ${config.hands}`);
  console.log(`  Bots: ${config.bots}`);
  console.log(`  Rounds: ${config.rounds} (evasion 0 → ${config.rounds - 1})`);
  console.log();

  const allReports = [];

  for (let round = 0; round < config.rounds; round++) {
    const evasionLevel = Math.min(round, 4);
    console.log(`  ── Round ${round + 1} (evasion level ${evasionLevel}) ──`);

    const report = await runRound({
      ...config,
      evasionLevel,
    });
    allReports.push(report);

    const s = report.summary;
    console.log(`     Hands: ${report.results.handsPlayed} | Actions: ${report.results.totalActions}`);
    console.log(`     Detected: ${s.detected} | Warned: ${s.warned} | Evaded: ${s.evaded} | Avg score: ${s.avgScore}`);
    if (report.results.errors > 0) {
      console.log(`     Errors: ${report.results.errors}`);
    }
    console.log();
  }

  // Final scorecard
  console.log("  " + "═".repeat(50));
  console.log("  Scorecard");
  console.log("  " + "═".repeat(50));
  console.log("  Level  │ Detected │ Warned │ Evaded │ Avg Score");
  console.log("  " + "─".repeat(50));
  for (let i = 0; i < allReports.length; i++) {
    const r = allReports[i];
    const lvl = Math.min(i, 4);
    const labels = ["None", "Basic", "Moderate", "Advanced", "Expert"];
    console.log(`  ${lvl} ${labels[lvl].padEnd(9)}│ ${String(r.summary.detected).padEnd(9)}│ ${String(r.summary.warned).padEnd(7)}│ ${String(r.summary.evaded).padEnd(7)}│ ${r.summary.avgScore}`);
  }
  console.log();

  // Output JSON for n8n consumption
  if (args.includes("--json")) {
    console.log(JSON.stringify(allReports, null, 2));
  }
}

if (require.main === module) {
  main().catch(err => { console.error("Fatal:", err.message); process.exit(1); });
}

module.exports = { runRound, generateTelemetry };
