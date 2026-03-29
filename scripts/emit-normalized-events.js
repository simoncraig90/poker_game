#!/usr/bin/env node
"use strict";

// ═══════════════════════════════════════════════════════════════════════════
//  Normalized Hand Event Emitter
//
//  Consumes decoded-events.jsonl (output of decode-session.js)
//  Produces normalized-hand-events.jsonl per NORMALIZED_HAND_EVENT_SCHEMA.md
//
//  Does NOT re-parse raw websocket frames — works entirely from decoded events.
// ═══════════════════════════════════════════════════════════════════════════

const fs = require("fs");
const path = require("path");

const sessionDir = process.argv[2];
if (!sessionDir) {
  console.error("Usage: node emit-normalized-events.js <session-folder>");
  process.exit(1);
}

const inputFile = path.join(sessionDir, "decoded-events.jsonl");
if (!fs.existsSync(inputFile)) {
  console.error("decoded-events.jsonl not found in " + sessionDir);
  console.error("Run decode-session.js first.");
  process.exit(1);
}

// Parse session ID from folder name
const sessionId = path.basename(sessionDir);

const raw = fs.readFileSync(inputFile, "utf8").trim().split("\n").map((l) => JSON.parse(l));

// ═══════════════════════════════════════════════════════════════════════════
//  State Machine
// ═══════════════════════════════════════════════════════════════════════════

let tableId = null;
let tableName = null;
let sb = null;
let bb = null;
let maxSeats = null;

let curHandId = null;
let button = null;
let street = null; // PREFLOP | FLOP | TURN | RIVER
let boardCards = [];
let heroCardsEmitted = null; // dedup key
let handActive = false;
let handSummarySeen = false;

// seat → { name, stack, country }
const players = new Map();
// seat → most recent roundId from ROUND_TRANSITION
const lastRoundId = new Map();
// Track ROUND_TRANSITION events for inferred fold detection
// Each entry: { seat, roundId, betToCall, frameIdx, consumed: bool }
let pendingRounds = [];
// Deferred HAND_END (because HAND_RESULT arrives after HAND_BOUNDARY in wire)
let pendingHandEnd = null;
// Buffer for negative-delta ACTIONs to distinguish collect sweeps from returns
let negDeltaBuffer = [];

// All normalized events (combined file)
const allEvents = [];
// Per-hand events, keyed by handId
const handEvents = new Map();

// Sequence counter within each hand
let handSeq = 0;

// ═══════════════════════════════════════════════════════════════════════════
//  Emit Helpers
// ═══════════════════════════════════════════════════════════════════════════

function emit(type, fields, source) {
  const evt = {
    sessionId,
    handId: curHandId,
    seq: handSeq++,
    type,
    ...fields,
    _source: source, // traceability
  };

  allEvents.push(evt);

  if (curHandId) {
    if (!handEvents.has(curHandId)) handEvents.set(curHandId, []);
    handEvents.get(curHandId).push(evt);
  }
}

function sourceRef(e) {
  return { frameIdx: e.idx, opcode: e.op, ts: e.ts };
}

function cardStr(c) {
  if (!c) return null;
  return c.replace(/[\[\]]/g, "");
}

function cleanCards(arr) {
  if (!arr || !Array.isArray(arr)) return null;
  const cleaned = arr.map(cardStr).filter(Boolean);
  return cleaned.length > 0 ? cleaned : null;
}

// ═══════════════════════════════════════════════════════════════════════════
//  Inferred Fold Detection
//
//  ROUND_TRANSITION with roundId=10 and no following ACTION for that seat
//  means the player folded or was already out. The server uses roundId=10
//  universally for "this seat is skipped" — betToCall is always 0 regardless
//  of whether the player owes chips. We always emit FOLD for roundId=10.
// ═══════════════════════════════════════════════════════════════════════════

function flushInferredActions() {
  for (const pr of pendingRounds) {
    if (pr.consumed) continue;

    if (pr.roundId === 10) {
      emit(
        "PLAYER_ACTION",
        {
          seat: pr.seat,
          player: playerName(pr.seat),
          action: "FOLD",
          totalBet: 0,
          delta: 0,
          street,
          inferred: true,
          inferredReason: "roundId=10, no following ACTION (fold or already out)",
        },
        { frameIdx: pr.frameIdx, opcode: "0x72", ts: pr.ts, inferred: true }
      );
    }
  }
  pendingRounds = [];
}

// ═══════════════════════════════════════════════════════════════════════════
//  Negative-Delta ACTION Buffer
//
//  Collect sweeps arrive as batches of negative-delta ACTIONs (one per seated
//  player). Uncalled bet returns arrive as single negative-delta ACTIONs.
//  We buffer all negative-delta ACTIONs and classify on flush:
//    - Single entry (1 seat): uncalled bet return → emit BET_RETURN
//    - Batch (2+ seats): collect sweep → skip
//    - Any entry with amount > 0: always a return regardless of batch size
// ═══════════════════════════════════════════════════════════════════════════

function flushNegDeltaBuffer() {
  if (negDeltaBuffer.length === 0) return;

  const isBatch = negDeltaBuffer.length >= 2;

  for (const e of negDeltaBuffer) {
    // amount > 0 means partial return (matched portion stays, excess returned)
    // Single entry means full return (entire uncalled bet returned)
    const isReturn = e.amount > 0 || !isBatch;

    if (isReturn) {
      const returnAmt = Math.abs(e.delta);
      emit(
        "BET_RETURN",
        {
          seat: e.seat,
          player: playerName(e.seat),
          amount: returnAmt,
        },
        sourceRef(e)
      );
    }
  }

  lastRoundId.clear();
  flushInferredActions();
  negDeltaBuffer = [];
}

function markRoundConsumed(seat) {
  // Mark the most recent pending round for this seat as consumed
  for (let i = pendingRounds.length - 1; i >= 0; i--) {
    if (pendingRounds[i].seat === seat && !pendingRounds[i].consumed) {
      pendingRounds[i].consumed = true;
      return;
    }
  }
}

function playerName(seat) {
  const p = players.get(seat);
  return p ? p.name : null;
}

// ═══════════════════════════════════════════════════════════════════════════
//  Action Classification (mirrors decode-session.js logic)
// ═══════════════════════════════════════════════════════════════════════════

function classifyAction(seat, amount, delta, options) {
  const rid = lastRoundId.get(seat);

  // Collect sweep or blind return
  if (delta != null && delta < 0) return null;

  // Fold
  if (amount === 0 && (delta === 0 || delta == null)) return "FOLD";
  if (amount === 0) return "FOLD";

  // Blind posts
  if (rid === 3) return "POST_SB";
  if (rid === 4) return "POST_BB";

  // Call: roundId=12 or delta < amount
  if (rid === 12 || (delta != null && delta < amount)) return "CALL";

  // Raise
  if (rid === 15 || (options && options.length > 1)) return "RAISE";

  // Bet
  if (rid === 13) return "BET";

  // New street first action
  if (rid === 11) return amount === 0 ? "CHECK" : "BET";

  // Preflop voluntary
  if (rid != null && rid >= 5 && rid < 10) {
    if (options && options.length > 1) return "RAISE";
    return "CALL";
  }

  // Fallback
  if (options && options.length > 1) return "RAISE";
  return "CALL";
}

// ═══════════════════════════════════════════════════════════════════════════
//  Main Loop
// ═══════════════════════════════════════════════════════════════════════════

for (const e of raw) {
  // Flush buffered negative-delta ACTIONs when any non-ACTION event arrives
  if (e.type !== "ACTION" && negDeltaBuffer.length > 0) {
    flushNegDeltaBuffer();
  }

  switch (e.type) {
    // ── TABLE_SNAPSHOT ────────────────────────────────────────────────
    case "TABLE_SNAPSHOT": {
      tableId = e.tableId;
      tableName = e.tableName;
      sb = e.sb;
      bb = e.bb;
      maxSeats = e.maxSeats;

      for (const s of e.seats || []) {
        if (s.player && s.player.name) {
          players.set(s.seat, {
            name: s.player.name,
            stack: s.player.stack,
            country: s.player.country,
          });
        }
      }

      emit(
        "TABLE_SNAPSHOT",
        {
          tableId: e.tableId,
          tableName: e.tableName,
          gameType: e.gameType,
          maxSeats: e.maxSeats,
          sb: e.sb,
          bb: e.bb,
          minBuyIn: e.minBuy,
          maxBuyIn: e.maxBuy,
          seats: e.seats,
          handId: e.handId,
          prevHandId: e.prevHandId,
          button: e.button,
          board: e.board,
          features: e.features,
        },
        sourceRef(e)
      );
      break;
    }

    // ── HAND_START (NEW_HAND) ────────────────────────────────────────
    case "NEW_HAND": {
      // Flush deferred HAND_END from previous hand
      if (pendingHandEnd) {
        emit("HAND_END", { tableId: pendingHandEnd.tableId }, pendingHandEnd.source);
        pendingHandEnd = null;
        handActive = false;
      }
      // Flush any pending inferred actions from previous hand
      flushInferredActions();

      curHandId = e.handId;
      button = e.button;
      street = "PREFLOP";
      boardCards = [];
      heroCardsEmitted = null;
      handActive = true;
      handSummarySeen = false;
      handSeq = 0;
      lastRoundId.clear();
      pendingRounds = [];

      emit(
        "HAND_START",
        {
          handId: e.handId,
          tableId: e.tableId || tableId,
          tableName,
          button: e.button,
          sb,
          bb,
          players: Object.fromEntries(
            [...players.entries()]
              .sort(([a], [b]) => a - b)
              .map(([seat, p]) => [seat, { name: p.name, stack: p.stack, country: p.country }])
          ),
        },
        sourceRef(e)
      );
      break;
    }

    // ── PLAYER_STATE (PLAYER_UPDATE) ─────────────────────────────────
    case "PLAYER_UPDATE": {
      const s = e.seat;
      if (!s) break;
      if (s.player && s.player.name) {
        players.set(s.seat, {
          name: s.player.name,
          stack: s.player.stack,
          country: s.player.country,
        });
      }

      // Only emit PLAYER_STATE for hand-start resets (bet=0 batch after HAND_START)
      // and significant state changes. Skip the noisy mid-action updates.
      // Schema says these are informational, so we emit sparingly.
      break;
    }

    // ── ROUND_TRANSITION ─────────────────────────────────────────────
    case "ROUND_TRANSITION": {
      // Before recording new RT, flush pending ones that won't get an ACTION
      // (only flush roundId=10 entries for the SAME seat being overwritten)
      const prevForSeat = pendingRounds.find(
        (pr) => pr.seat === e.activeSeat && !pr.consumed && pr.roundId === 10
      );
      if (prevForSeat) {
        // This seat got a new RT before consuming the old one — flush as FOLD
        emit(
          "PLAYER_ACTION",
          {
            seat: prevForSeat.seat,
            player: playerName(prevForSeat.seat),
            action: "FOLD",
            totalBet: 0,
            delta: 0,
            street,
            inferred: true,
            inferredReason: "roundId=10, superseded by new RT (fold or already out)",
          },
          { frameIdx: prevForSeat.frameIdx, opcode: "0x72", ts: prevForSeat.ts, inferred: true }
        );
        prevForSeat.consumed = true;
      }

      lastRoundId.set(e.activeSeat, e.roundId);
      pendingRounds.push({
        seat: e.activeSeat,
        roundId: e.roundId,
        betToCall: e.betToCall,
        frameIdx: e.idx,
        ts: e.ts,
        consumed: false,
      });
      break;
    }

    // ── ACTION ───────────────────────────────────────────────────────
    case "ACTION": {
      if (e.seat == null) break;
      if (!handActive) break;

      // Negative delta: buffer it. We classify as return vs collect sweep
      // when the buffer is flushed (next non-negative-delta event).
      if (e.delta != null && e.delta < 0) {
        negDeltaBuffer.push(e);
        break;
      }

      // A positive/zero-delta ACTION means the buffer (if any) is done
      flushNegDeltaBuffer();

      if (handSummarySeen) break;

      markRoundConsumed(e.seat);

      const action = classifyAction(e.seat, e.amount, e.delta, e.options);
      if (action === null) break;

      if (action === "POST_SB" || action === "POST_BB") {
        emit(
          "BLIND_POST",
          {
            seat: e.seat,
            player: playerName(e.seat),
            amount: e.amount,
            blindType: action === "POST_SB" ? "SB" : "BB",
            street: "PREFLOP",
          },
          sourceRef(e)
        );
      } else {
        emit(
          "PLAYER_ACTION",
          {
            seat: e.seat,
            player: playerName(e.seat),
            action,
            totalBet: e.amount,
            delta: e.delta,
            options: e.options && e.options.length > 0 ? e.options : undefined,
            street,
            inferred: false,
          },
          sourceRef(e)
        );
      }
      break;
    }

    // ── HERO_CARDS ───────────────────────────────────────────────────
    case "HERO_CARDS": {
      if (!e.heroCards || e.heroCards.length === 0) break;
      const cards = cleanCards(e.heroCards);
      if (!cards) break;
      const key = cards.join(" ");
      if (key === heroCardsEmitted) break; // dedup
      heroCardsEmitted = key;

      emit("HERO_CARDS", { cards }, sourceRef(e));
      break;
    }

    // ── DEAL_COMMUNITY (DEAL_BOARD from 0x71) ────────────────────────
    case "DEAL_BOARD": {
      if (!e.cards || e.cards.length === 0) break;
      const cards = cleanCards(e.cards);
      if (!cards) break;

      // Flush inferred folds from the preceding street
      flushInferredActions();

      boardCards.push(...cards);

      let newStreet;
      if (cards.length === 3) newStreet = "FLOP";
      else if (street === "FLOP" || (street === "PREFLOP" && boardCards.length === 4))
        newStreet = "TURN";
      else newStreet = "RIVER";

      street = newStreet;

      emit(
        "DEAL_COMMUNITY",
        { street, newCards: cards, board: [...boardCards] },
        sourceRef(e)
      );
      break;
    }

    // ── DEAL_COMMUNITY (DEAL_NOTIFY from 0x5a) ──────────────────────
    case "DEAL_NOTIFY": {
      if (!e.cards || e.cards.length === 0) break;
      const cards = cleanCards(e.cards);
      if (!cards) break;
      // Only emit if cards are visible (not bracketed hidden cards)
      if (cards.every((c) => c === "??")) break;

      flushInferredActions();

      boardCards.push(...cards);

      let newStreet;
      if (cards.length === 3) newStreet = "FLOP";
      else if (street === "PREFLOP") newStreet = "FLOP";
      else if (street === "FLOP") newStreet = "TURN";
      else newStreet = "RIVER";

      street = newStreet;

      emit(
        "DEAL_COMMUNITY",
        { street, newCards: cards, board: [...boardCards] },
        sourceRef(e)
      );
      break;
    }

    // ── POT_UPDATE ───────────────────────────────────────────────────
    case "POT_UPDATE": {
      if (!handActive) break;
      // Only emit meaningful pot updates (non-zero)
      const mainPot = e.pots && e.pots[0];
      if (!mainPot) break;
      const total = (mainPot.amount || 0) + (mainPot.pending || 0);
      if (total === 0) break;

      emit("POT_UPDATE", { pots: e.pots }, sourceRef(e));
      break;
    }

    // ── POT_AWARD ────────────────────────────────────────────────────
    case "POT_AWARD": {
      flushInferredActions();

      const awards = (e.awards || []).map((a) => ({
        seat: a.seat,
        player: playerName(a.seat),
        amount: a.amount,
      }));

      emit(
        "POT_AWARD",
        { potIndex: e.potIndex, awards },
        sourceRef(e)
      );
      break;
    }

    // ── HAND_SUMMARY ─────────────────────────────────────────────────
    case "HAND_SUMMARY": {
      handSummarySeen = true;
      const winSeatNum = e.winSeat != null ? parseInt(e.winSeat) : null;

      emit(
        "HAND_SUMMARY",
        {
          winSeat: winSeatNum,
          winPlayer: winSeatNum != null ? playerName(winSeatNum) : null,
          showdown: e.showdown,
          totalPot: e.totalPot,
          handRank: e.handRank || null,
          winCards: e.winCards || null,
          board: boardCards.length > 0 ? [...boardCards] : null,
        },
        sourceRef(e)
      );
      break;
    }

    // ── HAND_RESULT ──────────────────────────────────────────────────
    case "HAND_RESULT": {
      const results = (e.results || []).map((r) => ({
        seat: r.seat,
        player: playerName(r.seat),
        won: r.won,
        amount: r.amount,
        text: r.text,
      }));

      emit(
        "HAND_RESULT",
        { potIndex: e.potIndex, results },
        sourceRef(e)
      );
      break;
    }

    // ── HAND_END (HAND_BOUNDARY) ─────────────────────────────────────
    // In the wire protocol, HAND_RESULT (0x7d) arrives AFTER HAND_BOUNDARY
    // (0x6e). We defer HAND_END emission so it becomes the last event for
    // the hand. It gets emitted when the next HAND_START fires, or at
    // end-of-stream.
    case "HAND_BOUNDARY": {
      flushInferredActions();
      // Don't emit yet — defer until next hand or end of stream
      pendingHandEnd = { tableId: e.tableId || tableId, source: sourceRef(e) };
      break;
    }

    // ── STACK_UPDATE ─────────────────────────────────────────────────
    case "STACK_UPDATE": {
      // Absorbed: update internal state only.
      const p = players.get(e.seat);
      if (p) p.stack = e.stack;
      break;
    }

    // ── Everything else: not emitted ─────────────────────────────────
    default:
      break;
  }
}

// Flush any trailing buffers
flushNegDeltaBuffer();
flushInferredActions();
if (pendingHandEnd) {
  emit("HAND_END", { tableId: pendingHandEnd.tableId }, pendingHandEnd.source);
  pendingHandEnd = null;
}

// ═══════════════════════════════════════════════════════════════════════════
//  Output
// ═══════════════════════════════════════════════════════════════════════════

// Combined file
const combinedPath = path.join(sessionDir, "normalized-hand-events.jsonl");
fs.writeFileSync(combinedPath, allEvents.map((e) => JSON.stringify(e)).join("\n") + "\n");

// Per-hand files
const handsDir = path.join(sessionDir, "hands");
fs.mkdirSync(handsDir, { recursive: true });

for (const [handId, events] of handEvents) {
  const handPath = path.join(handsDir, `hand-${handId}.jsonl`);
  fs.writeFileSync(handPath, events.map((e) => JSON.stringify(e)).join("\n") + "\n");
}

// ── Stats ────────────────────────────────────────────────────────────────
const typeCounts = new Map();
let inferredCount = 0;
for (const e of allEvents) {
  typeCounts.set(e.type, (typeCounts.get(e.type) || 0) + 1);
  if (e.inferred) inferredCount++;
}

console.log("Normalized event emission complete.");
console.log(`  Input:    ${raw.length} decoded events`);
console.log(`  Output:   ${allEvents.length} normalized events`);
console.log(`  Hands:    ${handEvents.size}`);
console.log(`  Inferred: ${inferredCount}`);
console.log(`  Files:`);
console.log(`    ${combinedPath}`);
console.log(`    ${handsDir}/hand-*.jsonl (${handEvents.size} files)`);
console.log();
console.log("  Event counts:");
for (const [type, count] of [...typeCounts.entries()].sort()) {
  console.log(`    ${type.padEnd(20)} ${count}`);
}
