"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

/**
 * PokerStars Hand History Importer
 *
 * Parses PokerStars .txt hand history files and converts them into
 * the poker-lab event format for the Study pipeline.
 *
 * Supports: NL Hold'em cash game hand histories.
 * Does not support: tournaments, Omaha, mixed games, play money.
 *
 * Input: raw text from a PokerStars hand history file (one or more hands)
 * Output: array of poker-lab events per hand, ready for events.jsonl
 */

// ── Parsing ───────────────────────────────────────────────────────────────

const RANK_MAP = { "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7", "8": "8", "9": "9", "T": "T", "J": "J", "Q": "Q", "K": "K", "A": "A" };
const SUIT_MAP = { "c": "c", "d": "d", "h": "h", "s": "s" };

function parseCard(str) {
  // PokerStars: "Ah", "Ts", "2c" etc. — same format as poker-lab
  if (!str || str.length < 2) return null;
  const rank = str[0].toUpperCase();
  const suit = str[1].toLowerCase();
  if (!RANK_MAP[rank] || !SUIT_MAP[suit]) return null;
  return RANK_MAP[rank] + SUIT_MAP[suit];
}

function parseCards(str) {
  return str.trim().replace(/[\[\]]/g, "").split(/\s+/).map(parseCard).filter(Boolean);
}

/**
 * Parse a PokerStars hand history text into structured hand data.
 * Returns array of parsed hands.
 */
function parsePokerStarsText(text) {
  const hands = [];
  // Split on double newlines or "PokerStars" header lines
  const blocks = text.split(/\n(?=PokerStars )/);

  for (const block of blocks) {
    const trimmed = block.trim();
    if (!trimmed || !trimmed.startsWith("PokerStars")) continue;
    try {
      const hand = parseSingleHand(trimmed);
      if (hand) hands.push(hand);
    } catch (e) {
      // Skip unparseable hands
      console.error("Import: skipping unparseable hand:", e.message);
    }
  }

  return hands;
}

function parseSingleHand(text) {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length < 3) return null;

  // Line 1: PokerStars Hand #220851545772: Hold'em No Limit ($0.05/$0.10 USD) - 2021/03/15 ...
  const headerMatch = lines[0].match(/Hand #(\d+).*\([\$€]?([\d.]+)\/[\$€]?([\d.]+)/);
  if (!headerMatch) return null;
  const handNum = headerMatch[1];
  const sb = Math.round(parseFloat(headerMatch[2]) * 100); // convert to cents
  const bb = Math.round(parseFloat(headerMatch[3]) * 100);

  // Line 2: Table 'Procyon III' 6-max Seat #2 is the button
  const tableMatch = lines[1].match(/Table '([^']+)'.*Seat #(\d+) is the button/);
  const tableName = tableMatch ? tableMatch[1] : "Imported";
  const buttonSeat = tableMatch ? parseInt(tableMatch[2]) : 1;

  // Seat lines: Seat 1: PlayerName ($10.00 in chips)
  const players = {};
  const seatNames = {};
  let lineIdx = 2;
  while (lineIdx < lines.length && lines[lineIdx].startsWith("Seat ")) {
    const seatMatch = lines[lineIdx].match(/Seat (\d+): (.+?) \([\$€]?([\d.]+)/);
    if (seatMatch) {
      const seat = parseInt(seatMatch[1]);
      const name = seatMatch[2];
      const stack = Math.round(parseFloat(seatMatch[3]) * 100);
      players[seat] = { name, stack };
      seatNames[name] = seat;
    }
    lineIdx++;
  }

  if (Object.keys(players).length < 2) return null;

  // Parse remaining lines into actions
  const actions = [];
  let street = "PREFLOP";
  let board = [];
  const holeCards = {}; // seat → [card, card]
  let showdownPlayers = []; // { seat, cards, handName }
  let summary = null;

  for (let i = lineIdx; i < lines.length; i++) {
    const line = lines[i];

    // Hole cards: Dealt to PlayerName [Ah Ks]
    const dealtMatch = line.match(/Dealt to (.+?) \[(.+?)\]/);
    if (dealtMatch) {
      const seat = seatNames[dealtMatch[1]];
      if (seat != null) holeCards[seat] = parseCards(dealtMatch[2]);
      continue;
    }

    // Street markers
    if (line.startsWith("*** FLOP ***")) {
      const boardMatch = line.match(/\[(.+?)\]/);
      if (boardMatch) board = parseCards(boardMatch[1]);
      street = "FLOP";
      actions.push({ type: "STREET", street: "FLOP", board: [...board] });
      continue;
    }
    if (line.startsWith("*** TURN ***")) {
      const cardMatch = line.match(/\] \[(.+?)\]/);
      if (cardMatch) board.push(...parseCards(cardMatch[1]));
      street = "TURN";
      actions.push({ type: "STREET", street: "TURN", board: [...board] });
      continue;
    }
    if (line.startsWith("*** RIVER ***")) {
      const cardMatch = line.match(/\] \[(.+?)\]/);
      if (cardMatch) board.push(...parseCards(cardMatch[1]));
      street = "RIVER";
      actions.push({ type: "STREET", street: "RIVER", board: [...board] });
      continue;
    }
    if (line.startsWith("*** SHOW DOWN ***") || line.startsWith("*** SHOWDOWN ***")) {
      street = "SHOWDOWN";
      continue;
    }
    if (line.startsWith("*** SUMMARY ***")) break;

    // Blind posts: PlayerName: posts small blind $0.05
    const blindMatch = line.match(/^(.+?): posts (small|big) blind [\$€]?([\d.]+)/);
    if (blindMatch) {
      const seat = seatNames[blindMatch[1]];
      if (seat != null) {
        actions.push({
          type: "BLIND", seat, amount: Math.round(parseFloat(blindMatch[3]) * 100),
          blindType: blindMatch[2] === "small" ? "SB" : "BB",
        });
      }
      continue;
    }

    // Player actions: PlayerName: folds / calls $0.10 / raises $0.20 to $0.30 / bets $0.10 / checks
    const foldMatch = line.match(/^(.+?): folds/);
    if (foldMatch) {
      const seat = seatNames[foldMatch[1]];
      if (seat != null) actions.push({ type: "ACTION", seat, action: "FOLD", amount: 0, street });
      continue;
    }
    const checkMatch = line.match(/^(.+?): checks/);
    if (checkMatch) {
      const seat = seatNames[checkMatch[1]];
      if (seat != null) actions.push({ type: "ACTION", seat, action: "CHECK", amount: 0, street });
      continue;
    }
    const callMatch = line.match(/^(.+?): calls [\$€]?([\d.]+)/);
    if (callMatch) {
      const seat = seatNames[callMatch[1]];
      if (seat != null) actions.push({ type: "ACTION", seat, action: "CALL", amount: Math.round(parseFloat(callMatch[2]) * 100), street });
      continue;
    }
    const betMatch = line.match(/^(.+?): bets [\$€]?([\d.]+)/);
    if (betMatch) {
      const seat = seatNames[betMatch[1]];
      if (seat != null) actions.push({ type: "ACTION", seat, action: "BET", amount: Math.round(parseFloat(betMatch[2]) * 100), street });
      continue;
    }
    const raiseMatch = line.match(/^(.+?): raises [\$€]?[\d.]+ to [\$€]?([\d.]+)/);
    if (raiseMatch) {
      const seat = seatNames[raiseMatch[1]];
      if (seat != null) actions.push({ type: "ACTION", seat, action: "RAISE", amount: Math.round(parseFloat(raiseMatch[2]) * 100), street });
      continue;
    }

    // All-in variants
    const allInMatch = line.match(/^(.+?): (calls|bets|raises) [\$€]?[\d.]+(?:.*to [\$€]?([\d.]+))? and is all-in/);
    if (allInMatch) {
      const seat = seatNames[allInMatch[1]];
      const act = allInMatch[2] === "calls" ? "CALL" : allInMatch[2] === "bets" ? "BET" : "RAISE";
      const amt = allInMatch[3] ? Math.round(parseFloat(allInMatch[3]) * 100) : Math.round(parseFloat(line.match(/[\$€]?([\d.]+)/)?.[1] || "0") * 100);
      if (seat != null) actions.push({ type: "ACTION", seat, action: act, amount: amt, street });
      continue;
    }

    // Uncalled bet returned: Uncalled bet ($0.10) returned to PlayerName
    const returnMatch = line.match(/Uncalled bet \([\$€]?([\d.]+)\) returned to (.+)/);
    if (returnMatch) {
      const seat = seatNames[returnMatch[2]];
      if (seat != null) actions.push({ type: "RETURN", seat, amount: Math.round(parseFloat(returnMatch[1]) * 100) });
      continue;
    }

    // Showdown: PlayerName: shows [Ah Ks] (a pair of Aces)
    const showMatch = line.match(/^(.+?): shows \[(.+?)\](?: \((.+?)\))?/);
    if (showMatch) {
      const seat = seatNames[showMatch[1]];
      if (seat != null) {
        showdownPlayers.push({ seat, cards: parseCards(showMatch[2]), handName: showMatch[3] || "" });
        holeCards[seat] = parseCards(showMatch[2]);
      }
      continue;
    }

    // Collected pot: PlayerName collected $0.30 from pot / from main pot / from side pot
    const collectMatch = line.match(/^(.+?) collected [\$€]?([\d.]+) from/);
    if (collectMatch) {
      const seat = seatNames[collectMatch[1]];
      if (seat != null) {
        actions.push({ type: "COLLECT", seat, amount: Math.round(parseFloat(collectMatch[2]) * 100) });
        if (!summary) summary = { winSeat: seat, winPlayer: collectMatch[1] };
      }
      continue;
    }
  }

  return {
    handNum, tableName, sb, bb, buttonSeat,
    players, seatNames, holeCards,
    actions, board, showdownPlayers, summary,
  };
}

// ── Event Generation ──────────────────────────────────────────────────────

let eventSeq = 0;

function makeEvent(sessionId, handId, type, fields) {
  return {
    sessionId, handId, seq: eventSeq++, type,
    _source: { origin: "import", ts: Date.now() },
    ...fields,
  };
}

/**
 * Convert a parsed hand into poker-lab events.
 */
function handToEvents(parsed, sessionId, handId) {
  eventSeq = 0;
  const events = [];
  const playerMap = {};
  for (const [s, p] of Object.entries(parsed.players)) {
    playerMap[s] = { name: p.name, stack: p.stack, country: "XX", actorId: null };
  }

  // HAND_START
  events.push(makeEvent(sessionId, handId, "HAND_START", {
    tableId: "imported", tableName: parsed.tableName,
    button: parsed.buttonSeat, sb: parsed.sb, bb: parsed.bb,
    players: playerMap,
  }));

  // BLIND_POST
  let pot = 0;
  const stacks = {};
  const invested = {};
  for (const [s, p] of Object.entries(parsed.players)) {
    stacks[s] = p.stack;
    invested[s] = 0;
  }

  for (const a of parsed.actions) {
    if (a.type !== "BLIND") continue;
    const posted = Math.min(a.amount, stacks[a.seat] || 0);
    stacks[a.seat] -= posted;
    invested[a.seat] += posted;
    pot += posted;
    events.push(makeEvent(sessionId, handId, "BLIND_POST", {
      seat: a.seat, player: parsed.players[a.seat]?.name || "", amount: posted, blindType: a.blindType, street: "PREFLOP",
    }));
  }

  // HERO_CARDS
  for (const [s, cards] of Object.entries(parsed.holeCards)) {
    events.push(makeEvent(sessionId, handId, "HERO_CARDS", {
      seat: parseInt(s), player: parsed.players[s]?.name || "", cards,
    }));
  }

  // Actions + streets
  for (const a of parsed.actions) {
    if (a.type === "BLIND") continue; // already handled

    if (a.type === "STREET") {
      events.push(makeEvent(sessionId, handId, "DEAL_COMMUNITY", {
        street: a.street, newCards: a.board.slice(-( a.street === "FLOP" ? 3 : 1)), board: a.board,
      }));
      // Reset per-street bets
      for (const s of Object.keys(stacks)) invested[s] = invested[s]; // no reset needed for invested (cumulative)
      continue;
    }

    if (a.type === "ACTION") {
      // Calculate delta
      let delta = 0;
      if (a.action === "CALL") {
        delta = a.amount; // PS shows the call amount directly
      } else if (a.action === "BET") {
        delta = a.amount;
      } else if (a.action === "RAISE") {
        // PS shows "raises to X" — delta is the additional amount beyond what's already committed this street
        // Approximate: use the amount as totalBet for the event
        delta = a.amount; // simplified: treat as total commitment this action
      }
      if (a.action !== "FOLD" && a.action !== "CHECK") {
        stacks[a.seat] = Math.max(0, (stacks[a.seat] || 0) - delta);
        invested[a.seat] = (invested[a.seat] || 0) + delta;
        pot += delta;
      }
      events.push(makeEvent(sessionId, handId, "PLAYER_ACTION", {
        seat: a.seat, player: parsed.players[a.seat]?.name || "",
        action: a.action, totalBet: a.amount, delta, street: a.street, inferred: false,
      }));
      continue;
    }

    if (a.type === "RETURN") {
      stacks[a.seat] = (stacks[a.seat] || 0) + a.amount;
      invested[a.seat] = (invested[a.seat] || 0) - a.amount;
      pot -= a.amount;
      events.push(makeEvent(sessionId, handId, "BET_RETURN", {
        seat: a.seat, player: parsed.players[a.seat]?.name || "", amount: a.amount,
      }));
      continue;
    }

    if (a.type === "COLLECT") {
      stacks[a.seat] = (stacks[a.seat] || 0) + a.amount;
      // Will be emitted as POT_AWARD below
      continue;
    }
  }

  // SHOWDOWN_REVEAL
  if (parsed.showdownPlayers.length > 0) {
    events.push(makeEvent(sessionId, handId, "SHOWDOWN_REVEAL", {
      reveals: parsed.showdownPlayers.map((r) => ({
        seat: r.seat, player: parsed.players[r.seat]?.name || "",
        cards: r.cards, handName: r.handName, bestFive: r.cards, // best five not available from PS
      })),
    }));
  }

  // POT_AWARD (from COLLECT actions)
  const collects = parsed.actions.filter((a) => a.type === "COLLECT");
  if (collects.length > 0) {
    events.push(makeEvent(sessionId, handId, "POT_AWARD", {
      potIndex: 0,
      awards: collects.map((c) => ({ seat: c.seat, player: parsed.players[c.seat]?.name || "", amount: c.amount })),
    }));
  }

  // HAND_SUMMARY
  const totalPot = collects.reduce((s, c) => s + c.amount, 0);
  const winner = parsed.summary || (collects.length > 0 ? { winSeat: collects[0].seat, winPlayer: parsed.players[collects[0].seat]?.name || "" } : null);
  if (winner) {
    const winReveal = parsed.showdownPlayers.find((r) => r.seat === winner.winSeat);
    events.push(makeEvent(sessionId, handId, "HAND_SUMMARY", {
      winSeat: winner.winSeat, winPlayer: winner.winPlayer,
      showdown: parsed.showdownPlayers.length > 0,
      totalPot,
      handRank: winReveal ? winReveal.handName : null,
      winCards: winReveal ? winReveal.cards : null,
      board: parsed.board.length > 0 ? parsed.board : null,
    }));
  }

  // HAND_RESULT
  const results = [];
  for (const [s, p] of Object.entries(parsed.players)) {
    const seat = parseInt(s);
    const won = collects.some((c) => c.seat === seat);
    const amount = collects.filter((c) => c.seat === seat).reduce((sum, c) => sum + c.amount, 0);
    results.push({ seat, player: p.name, won, amount, text: won ? "Wins pot." : "Loses." });
  }
  events.push(makeEvent(sessionId, handId, "HAND_RESULT", { potIndex: 0, results }));

  // HAND_END
  events.push(makeEvent(sessionId, handId, "HAND_END", { tableId: "imported" }));

  return events;
}

// ── Import ────────────────────────────────────────────────────────────────

/**
 * Import a PokerStars hand history file into the Study pipeline.
 *
 * @param {string} filePath — path to .txt hand history file
 * @param {SessionStorage} storage — the session storage instance
 * @param {object} [opts]
 * @param {string} [opts.sessionId] — custom session ID (default: auto-generated)
 * @returns {{ sessionId, handsImported, errors }}
 */
function importPokerStars(filePath, storage, opts = {}) {
  const text = fs.readFileSync(filePath, "utf8");
  const parsed = parsePokerStarsText(text);

  if (parsed.length === 0) {
    return { sessionId: null, handsImported: 0, errors: ["No parseable hands found"] };
  }

  const sessionId = opts.sessionId || "import-" + crypto.randomUUID().slice(0, 12);
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
  const errors = [];

  // Write events
  let handsImported = 0;
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

  // Mark as complete (archived) and update metadata
  storage.updateMeta(sessionId, {
    status: "complete",
    handsPlayed: handsImported,
    lastEventAt: new Date().toISOString(),
  });

  return { sessionId, handsImported, errors };
}

module.exports = { parsePokerStarsText, parseSingleHand, handToEvents, importPokerStars, parseCard, parseCards };
