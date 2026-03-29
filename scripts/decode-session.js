#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

// ── Usage ──────────────────────────────────────────────────────────────────
const sessionDir = process.argv[2];
if (!sessionDir) {
  console.error("Usage: node decode-session.js <session-folder>");
  console.error("  e.g. node decode-session.js captures/20260329_202750");
  process.exit(1);
}

const wsFile = path.join(sessionDir, "websocket.jsonl");
if (!fs.existsSync(wsFile)) {
  console.error("websocket.jsonl not found in " + sessionDir);
  process.exit(1);
}

// ═══════════════════════════════════════════════════════════════════════════
//  PHASE A: Thrift Binary Protocol Reader
// ═══════════════════════════════════════════════════════════════════════════

const T = { STOP: 0, BOOL: 2, BYTE: 3, I16: 6, I32: 8, I64: 10, STRING: 11, STRUCT: 12, MAP: 13, SET: 14, LIST: 15 };

class ThriftReader {
  constructor(buf, pos = 0) { this.buf = buf; this.pos = pos; }
  rem() { return this.buf.length - this.pos; }
  u8() { return this.buf.readUInt8(this.pos++); }
  i8() { return this.buf.readInt8(this.pos++); }
  i16() { const v = this.buf.readInt16BE(this.pos); this.pos += 2; return v; }
  i32() { const v = this.buf.readInt32BE(this.pos); this.pos += 4; return v; }
  i64() {
    const hi = this.buf.readInt32BE(this.pos);
    const lo = this.buf.readUInt32BE(this.pos + 4);
    this.pos += 8;
    if (hi === 0) return lo;
    if (hi === -1 && lo > 0x7fffffff) return -(0x100000000 - lo);
    return { hi, lo };
  }
  str() {
    const len = this.buf.readInt32BE(this.pos); this.pos += 4;
    if (len < 0 || len > 100000) return `[bad:${len}]`;
    const s = this.buf.toString("utf8", this.pos, this.pos + len);
    this.pos += len;
    return s;
  }
  val(type) {
    switch (type) {
      case T.BOOL: return this.i8() !== 0;
      case T.BYTE: return this.i8();
      case T.I16: return this.i16();
      case T.I32: return this.i32();
      case T.I64: return this.i64();
      case T.STRING: return this.str();
      case T.STRUCT: return this.struct();
      case T.LIST: case T.SET: return this.list();
      case T.MAP: return this.map();
      default: throw new Error(`type 0x${type.toString(16)} @${this.pos}`);
    }
  }
  struct() {
    const f = {};
    while (this.rem() > 0) {
      const t = this.u8();
      if (t === T.STOP) break;
      const id = this.i16();
      try { f[id] = this.val(t); } catch (e) { f[id] = `[err:${e.message}]`; break; }
    }
    return f;
  }
  list() {
    const et = this.u8(), n = this.i32();
    if (n < 0 || n > 10000) return [];
    const a = [];
    for (let i = 0; i < n; i++) {
      try { a.push(this.val(et)); } catch { break; }
    }
    return a;
  }
  map() {
    const kt = this.u8(), vt = this.u8(), n = this.i32();
    if (n < 0 || n > 10000) return {};
    const m = {};
    for (let i = 0; i < n; i++) {
      try { const k = this.val(kt); m[String(k)] = this.val(vt); } catch { break; }
    }
    return m;
  }
}

function parseFrame(b64) {
  const buf = Buffer.from(b64, "base64");
  if (buf.length < 3) return { op: buf.length > 1 ? buf[1] : 0, f: {} };
  const op = buf[1];
  const r = new ThriftReader(buf, 2);
  let f;
  try { f = r.struct(); } catch { f = {}; }
  return { op, f };
}

// ═══════════════════════════════════════════════════════════════════════════
//  Card Decoding
// ═══════════════════════════════════════════════════════════════════════════
// Card struct: {1: visibility (0=visible, 255=hidden), 2: suit, 3: rank}
// Suits: 1=c, 2=d, 3=h, 4=s
// Ranks: 2-10 numeric, 11=J, 12=Q, 13=K, 14=A

const SUITS = { 1: "c", 2: "d", 3: "h", 4: "s" };
const RANKS = { 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "T", 11: "J", 12: "Q", 13: "K", 14: "A" };

function decodeCard(c) {
  if (!c || typeof c !== "object") return "??";
  const vis = c[1]; // 0=visible, 255=hidden
  const suit = SUITS[c[2]] || "?";
  const rank = RANKS[c[3]] || "?";
  if (vis === 255) return "[" + rank + suit + "]"; // hidden (opponent's card)
  return rank + suit;
}

function decodeCards(arr) {
  if (!arr || !Array.isArray(arr) || arr.length === 0) return null;
  return arr.map(decodeCard);
}

// ═══════════════════════════════════════════════════════════════════════════
//  PHASE B: Event Extractors
// ═══════════════════════════════════════════════════════════════════════════

function mapPlayer(s) {
  if (!s || typeof s !== "object") return null;
  return {
    name: s[1] || null, country: s[2] || null,
    isActive: s[3] ?? null, hasCards: s[4] ?? null,
    sittingIn: s[5] ?? null, sittingOut: s[6] ?? null,
    stack: s[7] ?? null, holeCards: decodeCards(s[8]),
    avatarId: s[10] ?? null, roundBet: s[17] ?? null,
  };
}

function mapSeat(s) {
  if (!s || typeof s !== "object") return null;
  return { seat: s[1] ?? null, status: s[2] ?? null, player: s[3] ? mapPlayer(s[3]) : null, bet: s[4] ?? null };
}

const E = {}; // extractors

E[0x6a] = (f) => {
  const m = f[2] || {}, bl = m[7] || {};
  return {
    type: "TABLE_SNAPSHOT",
    tableId: f[1], tableName: m[1], gameType: m[2], maxSeats: m[4], playerCount: m[5],
    sb: bl[1] ?? null, bb: bl[2] ?? null, minBuy: m[9], maxBuy: m[10],
    seats: (f[4] || []).map(mapSeat).filter(Boolean),
    handId: f[5], prevHandId: f[6], button: f[7],
    board: decodeCards(f[11]), features: f[20] || [],
  };
};

E[0x6c] = (f) => ({ type: "PLAYER_UPDATE", tableId: f[1], seat: f[2] ? mapSeat(f[2]) : null });

E[0x6d] = (f) => {
  // Large: new hand. Small (just F1 int): heartbeat ack.
  if (f[2] && typeof f[2] === "string" && f[2].length > 5)
    return { type: "NEW_HAND", tableId: f[1], handId: f[2], button: f[5] };
  return { type: "HEARTBEAT_ACK", seq: f[1] };
};

E[0x83] = (f) => ({ type: "HEARTBEAT_PING", seq: f[1] });

E[0x77] = (f) => ({
  type: "ACTION", tableId: f[1], seat: f[2], amount: f[3] ?? null, delta: f[4] ?? null, options: f[5] || [],
});

E[0x72] = (f) => ({
  type: "ROUND_TRANSITION", tableId: f[1], activeSeat: f[2], roundId: f[3], betToCall: f[4],
});

E[0x73] = (f) => {
  if (typeof f[2] === "number" && f[2] > 100)
    return { type: "JOIN_REQUEST", sessionId: f[1], buyIn: f[2] };
  return { type: "ACTION_TIMER", tableId: f[1], seat: f[2], timerMs: f[3] };
};

E[0x76] = (f) => ({ type: "STACK_UPDATE", tableId: f[1], seat: f[2], stack: f[3] });

E[0x78] = (f) => ({
  type: "POT_UPDATE", tableId: f[1],
  pots: (f[2] || []).map(p => ({ amount: p[1], pending: p[2] })),
});

E[0x79] = (f) => ({ type: "SEAT_BETS", tableId: f[1], bets: f[3] || [] });

// 0x7b: Pot award
E[0x7b] = (f) => ({
  type: "POT_AWARD", tableId: f[1], potIndex: f[2],
  awards: (f[3] || []).map(a => ({ seat: a[1], amount: a[2] })),
});

E[0x7c] = (f) => ({ type: "ROUND_BET_SUMMARY", tableId: f[1], seat: f[2], amount: f[3] });

// 0x7d: Hand result with text
E[0x7d] = (f) => ({
  type: "HAND_RESULT", tableId: f[1], potIndex: f[2],
  results: (f[3] || []).map(r => ({ seat: r[1], won: r[2], amount: r[3], text: r[5] || null })),
});

// 0x34: Hand summary
E[0x34] = (f) => {
  const i = f[5] || [];
  return {
    type: "HAND_SUMMARY", tableId: f[1],
    winSeat: i[0] ?? null, showdown: i[3] === "true", totalPot: parseInt(i[4]) || 0,
    handRank: i[5] || null, winCards: i[6] || null,
  };
};

E[0x6f] = (f) => ({ type: "PLAYER_TIMER", tableId: f[1], seat: f[2], ms: f[3] });
E[0x74] = (f) => ({ type: "TIME_BANK", tableId: f[1], seat: f[2], ms: f[3] });
E[0x75] = (f) => ({ type: "HAND_COMPLETE", tableId: f[1] });
E[0x6e] = (f) => ({ type: "HAND_BOUNDARY", tableId: f[1] });
E[0x70] = (f) => ({ type: "BET_ORDER", tableId: f[1], entries: f[2] || [] });
E[0x48] = (f) => ({ type: "TABLE_CONFIG", tableId: f[1], fields: f });
E[0x51] = (f) => ({ type: "UNKNOWN_0x51", fields: f });

// 0x5a: Deal / street notification (sometimes has visible cards like river or hero cards)
E[0x5a] = (f) => ({
  type: "DEAL_NOTIFY", tableId: f[1],
  cards: decodeCards(f[2]), _f3: f[3], _f4: f[4],
});

// 0x71: Community card deal
E[0x71] = (f) => ({
  type: "DEAL_BOARD", tableId: f[1],
  cards: decodeCards(f[2]), _f3: f[3], _f4: f[4],
});

// 0x8b: Hero hole cards + hand info
E[0x8b] = (f) => ({
  type: "HERO_CARDS", tableId: f[1],
  heroCards: decodeCards(f[3]),
  _u2: f[2], _u4: f[4], _u5: f[5], _u6: f[6], _u10: f[10],
});

// 0x8d: Action prompt (what hero can do)
E[0x8d] = (f) => ({
  type: "ACTION_PROMPT", tableId: f[1], promptId: f[2],
  actions: f[3] || [], raise: f[4] || null,
});

// 0x8f: Unknown, appears near action prompts
E[0x8f] = (f) => ({ type: "UNKNOWN_0x8f", tableId: f[1], fields: f });

// Catch-all for less important opcodes
for (const op of [0x36, 0x37, 0x04, 0xc7, 0x59, 0xc6, 0x3f, 0x60, 0x4b, 0xba, 0x65, 0xad, 0x6b, 0xb2, 0xc2, 0x88, 0x87, 0x7a]) {
  if (!E[op]) E[op] = (f) => ({ type: `OTHER_0x${op.toString(16).padStart(2, "0")}`, fields: f });
}

// ═══════════════════════════════════════════════════════════════════════════
//  PHASE A+B: Parse all frames → events
// ═══════════════════════════════════════════════════════════════════════════

const lines = fs.readFileSync(wsFile, "utf8").trim().split("\n");
const events = [];
const opStats = new Map();

for (let i = 0; i < lines.length; i++) {
  const raw = JSON.parse(lines[i]);
  if (raw.opcode === 1) continue; // skip STOMP text frames

  const { op, f } = parseFrame(raw.payload);
  const hex = "0x" + op.toString(16).padStart(2, "0");

  if (!opStats.has(hex)) opStats.set(hex, { count: 0, dirs: new Set(), sizes: [] });
  const s = opStats.get(hex);
  s.count++; s.dirs.add(raw.direction); s.sizes.push(raw.payloadLength);

  const ext = E[op];
  let evt;
  try { evt = ext ? ext(f) : { type: `RAW_${hex}`, fields: f }; }
  catch { evt = { type: `ERR_${hex}`, fields: f }; }

  events.push({ idx: i, ts: raw.timestamp, dir: raw.direction, op: hex, len: raw.payloadLength, ...evt });
}

// ── Write decoded-events.jsonl ─────────────────────────────────────────────
const eventsPath = path.join(sessionDir, "decoded-events.jsonl");
fs.writeFileSync(eventsPath, events.map(e => JSON.stringify(e)).join("\n") + "\n");

// ═══════════════════════════════════════════════════════════════════════════
//  PHASE C+D: Hand Timeline Builder
// ═══════════════════════════════════════════════════════════════════════════

const out = []; // timeline lines
const players = new Map(); // seat → {name, stack, country}
let tblName = null, sb = null, bb = null;
let curHand = null, button = null;
let street = null;
let boardCards = [];
let heroCardsStr = null; // dedup string
let handCount = 0;
let lastPot = 0;
// Track the most recent ROUND_TRANSITION per seat for action classification
// roundId: 3=SB post, 4=BB post, 5+=preflop voluntary, 10=fold/check, 11=new street, 12=call, 13=bet, 15=raise
const lastRound = new Map();
let handSummarySeen = false;

function c$(v) {
  if (v == null) return "?";
  const abs = Math.abs(v);
  if (abs >= 100) return "$" + (v / 100).toFixed(2);
  return v + "c";
}

function pn(seat) {
  const p = players.get(seat);
  return p ? p.name : `Seat${seat}`;
}

function line(s) { out.push(s); }

function cardStr(c) {
  // Strip brackets from hidden cards for board display — board is always public
  return c.replace(/[\[\]]/g, "");
}

// Classify action using the preceding ROUND_TRANSITION's roundId
function actionLabel(e) {
  const { seat, amount, delta, options } = e;
  const rid = lastRound.get(seat);

  // Collect sweep: amount=0, delta<0
  if (amount === 0 && delta != null && delta < 0) return null; // skip

  // Fold: amount=0, roundId=10 or no options
  if (amount === 0) return "folds";

  // Blind posts: roundId 3 or 4
  if (rid === 3) return `posts SB ${c$(amount)}`;
  if (rid === 4) return `posts BB ${c$(amount)}`;

  // Call: roundId=12, or delta < amount (paying less than total = calling up to)
  if (rid === 12 || (delta != null && delta < amount)) return `calls ${c$(amount)}`;

  // Raise: roundId=15, or options has >1 entry
  if (rid === 15 || (options && options.length > 1)) return `raises to ${c$(amount)}`;

  // Bet: roundId=13
  if (rid === 13) return `bets ${c$(amount)}`;

  // New street first action (roundId=11) - could be bet or check
  if (rid === 11) {
    if (amount === 0) return "checks";
    return `bets ${c$(amount)}`;
  }

  // Preflop positions (roundId >= 5 and < 10): call or raise
  if (rid != null && rid >= 5 && rid < 10) {
    if (options && options.length > 1) return `raises to ${c$(amount)}`;
    return `calls ${c$(amount)}`;
  }

  // Fallback
  if (options && options.length > 1) return `raises to ${c$(amount)}`;
  return `calls ${c$(amount)}`;
}

function emitStreet(name, cards) {
  street = name;
  if (name !== "PREFLOP") {
    const cardLine = boardCards.length > 0 ? " [" + boardCards.map(cardStr).join(" ") + "]" : "";
    line(`  --- ${name}${cardLine} ---`);
  }
}

function startHand(handId, btn) {
  handCount++;
  curHand = handId;
  button = btn;
  street = "PREFLOP";
  boardCards = [];
  heroCardsStr = null;
  lastPot = 0;
  lastRound.clear();
  handSummarySeen = false;
  line(`\n--- Hand #${handId} | Button: Seat ${btn} (${pn(btn)}) ---`);

  const seated = [...players.entries()].sort((a, b) => a[0] - b[0]);
  const parts = seated.map(([s, p]) => `${p.name} ${c$(p.stack)}`);
  line(`  Stacks: ${parts.join(" | ")}`);
}

for (const e of events) {
  switch (e.type) {
    case "TABLE_SNAPSHOT": {
      tblName = e.tableName;
      sb = e.sb;
      bb = e.bb;
      button = e.button;
      for (const s of e.seats || []) {
        if (s.player && s.player.name) {
          players.set(s.seat, { name: s.player.name, stack: s.player.stack, country: s.player.country });
        }
      }
      line(`=== TABLE: ${tblName} | ${c$(sb)}/${c$(bb)} NL Hold'em | ${e.maxSeats}-max ===`);
      const seated = [...players.entries()].sort((a, b) => a[0] - b[0]);
      for (const [seat, p] of seated) {
        line(`  Seat ${seat}: ${p.name} (${p.country}) ${c$(p.stack)}`);
      }
      break;
    }

    case "NEW_HAND": {
      startHand(e.handId, e.button);
      break;
    }

    case "PLAYER_UPDATE": {
      const s = e.seat;
      if (s && s.player && s.player.name) {
        players.set(s.seat, { name: s.player.name, stack: s.player.stack, country: s.player.country });
      }
      break;
    }

    case "ROUND_TRANSITION": {
      // Record what kind of action this seat will take
      lastRound.set(e.activeSeat, e.roundId);
      break;
    }

    case "HERO_CARDS": {
      if (e.heroCards && e.heroCards.length > 0) {
        const str = e.heroCards.join(" ");
        if (str !== heroCardsStr) { // dedup
          heroCardsStr = str;
          line(`  ** Hero: ${str} **`);
        }
      }
      break;
    }

    case "DEAL_BOARD": {
      if (e.cards && e.cards.length > 0) {
        boardCards.push(...e.cards);
        if (e.cards.length === 3) emitStreet("FLOP");
        else if (street === "FLOP" || (street === "PREFLOP" && boardCards.length === 4)) emitStreet("TURN");
        else emitStreet("RIVER");
      }
      break;
    }

    case "DEAL_NOTIFY": {
      if (e.cards && e.cards.length === 1 && !e.cards[0].startsWith("[")) {
        boardCards.push(...e.cards);
        const next = street === "PREFLOP" ? "FLOP" : street === "FLOP" ? "TURN" : "RIVER";
        emitStreet(next);
      }
      break;
    }

    case "ACTION": {
      if (e.seat == null) break;
      // Any negative delta = collect sweep or blind return, not a player action
      if (e.delta != null && e.delta < 0) {
        lastRound.clear();
        break;
      }
      if (handSummarySeen) break;
      const label = actionLabel(e);
      if (label === null) break;
      line(`  ${pn(e.seat)} ${label}`);
      break;
    }

    case "POT_UPDATE": {
      const main = e.pots[0];
      if (main) lastPot = main.amount + (main.pending || 0);
      break;
    }

    case "POT_AWARD": {
      for (const a of e.awards || []) {
        line(`  ** ${pn(a.seat)} wins ${c$(a.amount)} **`);
      }
      break;
    }

    case "HAND_RESULT": {
      for (const r of e.results || []) {
        if (r.text) line(`  ${pn(r.seat)}: ${r.text}`);
      }
      break;
    }

    case "HAND_SUMMARY": {
      handSummarySeen = true;
      const winner = e.winSeat != null ? pn(parseInt(e.winSeat)) : "?";
      const sd = e.showdown ? "showdown" : "no showdown";
      line(`  Result: ${winner} wins ${c$(e.totalPot)} (${sd})`);
      if (e.handRank) line(`  Hand: ${e.handRank}`);
      if (e.winCards) line(`  Cards: ${e.winCards}`);
      if (boardCards.length > 0) line(`  Board: ${boardCards.map(cardStr).join(" ")}`);
      break;
    }

    case "STACK_UPDATE": {
      const p = players.get(e.seat);
      if (p) p.stack = e.stack;
      break;
    }

    default:
      break;
  }
}

// ── Write decoded-hand-timeline.txt ────────────────────────────────────────
const tlPath = path.join(sessionDir, "decoded-hand-timeline.txt");
const header = [
  "DECODED HAND TIMELINE",
  "=".repeat(60),
  `Session: ${sessionDir}`,
  `Decoded: ${new Date().toISOString()}`,
  `Frames:  ${events.length}`,
  `Hands:   ${handCount}`,
  "",
];
fs.writeFileSync(tlPath, header.concat(out).join("\n") + "\n");

// ═══════════════════════════════════════════════════════════════════════════
//  Opcode Catalog
// ═══════════════════════════════════════════════════════════════════════════

const catPath = path.join(sessionDir, "opcode_catalog.md");
const cat = [];

const NOTES = {
  "0x04": "Init data (lobby config)",
  "0x34": "Hand summary: winner seat, pot, showdown flag, hand rank",
  "0x36": "URL push / resource load",
  "0x37": "Config update",
  "0x3f": "Unknown (table join ack?)",
  "0x48": "Table config / bet increments",
  "0x4b": "Table state transition",
  "0x51": "Unknown (12-byte marker)",
  "0x59": "Unknown (ack?)",
  "0x5a": "Deal notification — visible cards (river, hero) in F2",
  "0x60": "Seat map / player layout",
  "0x64": "Client join / session setup",
  "0x65": "Client leave table",
  "0x6a": "Full table snapshot on join (seats, blinds, config)",
  "0x6b": "Table close notification",
  "0x6c": "Player state update (stack, status, bet, cards)",
  "0x6d": "Heartbeat ack (16B) or NEW HAND (>16B, has hand ID + button)",
  "0x6e": "Hand boundary marker (between hands)",
  "0x6f": "Per-player action countdown timer",
  "0x70": "Seat betting order for current round",
  "0x71": "Community cards dealt (F2 = card array: flop 3, turn/river 1)",
  "0x72": "Round transition: active seat, round ID, bet-to-call",
  "0x73": "Action timer tick / join request (if amount > 100)",
  "0x74": "Time bank activation",
  "0x75": "Hand processing complete signal",
  "0x76": "Stack size update after chip movement",
  "0x77": "Player action: fold/check/call/bet/raise (F2=seat, F3=amt, F4=delta)",
  "0x78": "Pot update: main + side pot amounts",
  "0x79": "Per-seat bet chip display (6 values)",
  "0x7a": "Client action response (sent by hero)",
  "0x7b": "Pot award: who wins how much from each pot",
  "0x7c": "Round bet summary: per-player investment",
  "0x7d": "Hand result with human-readable text descriptions",
  "0x83": "Server heartbeat ping (incrementing seq number)",
  "0x87": "Table init / first connection data",
  "0x88": "Unknown (post-join?)",
  "0x8b": "Hero hole cards in F3 (card structs with F1=0 = visible)",
  "0x8d": "Action prompt: available actions + raise min/max/stack",
  "0x8f": "Unknown (near action prompts)",
  "0xad": "Client message (post-action?)",
  "0xb2": "Join table confirmation / seat assignment",
  "0xba": "Unknown (rare)",
  "0xc2": "Client message (ack?)",
  "0xc6": "Client message (ack?)",
  "0xc7": "Client message (rewards widget?)",
};

cat.push("# Opcode Catalog");
cat.push("");
cat.push(`Session: ${sessionDir} | ${events.length} frames | ${handCount} hands`);
cat.push("");
cat.push("## Message Types");
cat.push("");
cat.push("| Opcode | Count | Dir | Size | Event Type | Description |");
cat.push("|--------|-------|-----|------|-----------|-------------|");

const typeForOp = new Map();
events.forEach(e => { if (!typeForOp.has(e.op)) typeForOp.set(e.op, e.type); });

for (const [op, s] of [...opStats.entries()].sort()) {
  const d = [...s.dirs].join("/");
  const mn = Math.min(...s.sizes), mx = Math.max(...s.sizes);
  const sz = mn === mx ? `${mn}` : `${mn}-${mx}`;
  cat.push(`| ${op} | ${s.count} | ${d} | ${sz} | ${typeForOp.get(op) || "?"} | ${NOTES[op] || ""} |`);
}

cat.push("");
cat.push("## Card Encoding");
cat.push("");
cat.push("Cards are Thrift structs: `{F1: visibility, F2: suit, F3: rank}`");
cat.push("");
cat.push("- **F1**: 0 = visible to hero, 255 = hidden (opponent's card)");
cat.push("- **F2 (suit)**: 1=clubs, 2=diamonds, 3=hearts, 4=spades");
cat.push("- **F3 (rank)**: 2-10 numeric, 11=J, 12=Q, 13=K, 14=A");
cat.push("");
cat.push("Sources:");
cat.push("- 0x8b F3: Hero hole cards (F1=0, visible)");
cat.push("- 0x71 F2: Community cards (flop=3, turn=1, river=1; F1=255 if opponent perspective)");
cat.push("- 0x5a F2: Additional visible cards (river, sometimes hero cards)");
cat.push("");

cat.push("## Key Field Map");
cat.push("");
cat.push("| Concept | Opcode | Field | Type | Unit |");
cat.push("|---------|--------|-------|------|------|");
cat.push("| Table ID | all game | F1 | string | `6R.{id}` |");
cat.push("| Table name | 0x6a | F2.F1 | string | |");
cat.push("| Blinds | 0x6a | F2.F7.F1 (SB), F2.F7.F2 (BB) | i32 | cents |");
cat.push("| Player name | 0x6a/6c | seat.F3.F1 | string | |");
cat.push("| Seat number | many | seat.F1, or F2 | byte | 0-5 |");
cat.push("| Stack | 0x6a/6c/76 | seat.F3.F7, or F3 | i32 | cents |");
cat.push("| Action seat | 0x77 | F2 | byte | 0-5 |");
cat.push("| Action amount | 0x77 | F3 | i32 | cents |");
cat.push("| Action delta | 0x77 | F4 | i32 | cents (neg=collect sweep) |");
cat.push("| Pot | 0x78 | F2[0].F1 | i32 | cents |");
cat.push("| Win amount | 0x7b/7d | F3[n].F2 or F3[n].F3 | i32 | cents |");
cat.push("| Hand ID | 0x6d(large) | F2 | string | numeric |");
cat.push("| Button | 0x6a/6d | F7 or F5 | byte | 0-5 |");
cat.push("| Round/street | 0x72 | F3 | i32 | see below |");
cat.push("| Showdown? | 0x34 | F5[3] | string | \"true\"/\"false\" |");
cat.push("| Result text | 0x7d | F3[n].F5 | string | |");
cat.push("");

cat.push("## Round ID Values (0x72 F3)");
cat.push("");
cat.push("| Value | Context |");
cat.push("|-------|---------|");
cat.push("| 3-5 | Preflop action positions (sequential seats) |");
cat.push("| 10 | Fold / check / pass (no action needed) |");
cat.push("| 11 | New street opens (first action on flop/turn/river) |");
cat.push("| 12 | Call (facing a bet) |");
cat.push("| 13 | Bet / all-in |");
cat.push("| 15 | Raise made |");
cat.push("| 22 | Hand setup / pre-deal |");
cat.push("");

cat.push("## Serialization");
cat.push("");
cat.push("Apache Thrift Binary Protocol. Frame: byte[0]=0x00 (framing), byte[1]=opcode, byte[2+]=fields.");
cat.push("Field header: 1 byte type + 2 byte field ID (big-endian). Struct ends with 0x00 stop byte.");
cat.push("Types: 0x02=bool, 0x03=byte, 0x06=i16, 0x08=i32, 0x0a=i64, 0x0b=string, 0x0c=struct, 0x0f=list, 0x0d=map.");

fs.writeFileSync(catPath, cat.join("\n") + "\n");

// ── Summary ────────────────────────────────────────────────────────────────
console.log("Decode complete.");
console.log(`  Events:  ${events.length}`);
console.log(`  Hands:   ${handCount}`);
console.log(`  Output:`);
console.log(`    ${eventsPath}`);
console.log(`    ${tlPath}`);
console.log(`    ${catPath}`);
