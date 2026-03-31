#!/usr/bin/env node
"use strict";

/**
 * Generate training data for hand-strength neural network.
 *
 * Runs N random hands (2-6 players) through the engine with all players
 * calling to showdown (no folding). For each player in each hand, records:
 *   - hero's 2 hole cards (encoded 0-51)
 *   - board cards (0-5 cards, padded to 5 with 52 = empty)
 *   - num_opponents
 *   - won (0 or 1) — did hero win any share of the pot?
 *   - equity — fraction of pot won (handles splits)
 *
 * Card encoding: (rank - 2) * 4 + (suit - 1), giving 0..51.
 *
 * Usage:
 *   node scripts/generate-hand-data.js                  # 1M hands
 *   node scripts/generate-hand-data.js --hands 500000
 */

const { createGame, ACTION, PHASE } = require("../src/index");
const fs = require("fs");
const path = require("path");

// ── Card Encoding ───────────────────────────────────────────────────────

const RANK_MAP = { "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14 };
const SUIT_MAP = { "c": 1, "d": 2, "h": 3, "s": 4 };

function encodeCardObj(card) {
  // card = {rank: 2-14, suit: 1-4}
  return (card.rank - 2) * 4 + (card.suit - 1);
}

function encodeCardStr(s) {
  // card display string like "As", "Th", "2c"
  const rank = RANK_MAP[s[0]];
  const suit = SUIT_MAP[s[1]];
  return (rank - 2) * 4 + (suit - 1);
}

// ── RNG ─────────────────────────────────────────────────────────────────

function createRng(seed) {
  let s = seed | 0 || 1;
  return function () {
    s ^= s << 13;
    s ^= s >> 17;
    s ^= s << 5;
    return (s >>> 0) / 0x100000000;
  };
}

// ── Main ────────────────────────────────────────────────────────────────

function main() {
  const args = process.argv.slice(2);
  let numHands = 1000000;
  for (let i = 0; i < args.length; i += 2) {
    if (args[i] === "--hands") numHands = parseInt(args[i + 1]);
  }

  const outPath = path.resolve(__dirname, "..", "vision", "data", "hand_strength_data.jsonl");
  const ws = fs.createWriteStream(outPath, { flags: "w" });

  const rng = createRng(12345);
  const startTime = Date.now();
  let rowsWritten = 0;
  let handsCompleted = 0;
  let errors = 0;

  console.log(`Generating hand-strength data: ${numHands} hands -> ${outPath}`);

  for (let h = 0; h < numHands; h++) {
    // Random number of players 2-6
    const numSeats = 2 + Math.floor(rng() * 5); // 2..6

    const game = createGame(
      {
        tableId: "datagen",
        tableName: "DataGen",
        maxSeats: numSeats,
        sb: 5,
        bb: 10,
        minBuyIn: 100,
        maxBuyIn: 50000,
      },
      { sessionId: `dg-${h}`, logPath: null, rng }
    );

    // Seat everyone with equal stacks
    for (let i = 0; i < numSeats; i++) {
      game.sitDown(i, `P${i}`, 1000);
    }

    // Start hand
    try {
      game.startHand();
    } catch (e) {
      errors++;
      continue;
    }

    // Capture hero cards before play (these are card objects with rank/suit)
    const preState = game.getState();
    const heroCards = [];
    for (let i = 0; i < numSeats; i++) {
      const s = preState.table.seats[i];
      if (s && s.holeCards) {
        heroCards[i] = s.holeCards.map(encodeCardObj);
      }
    }

    // Play to showdown: everyone calls or checks — never fold, never raise.
    // Skip getLegalActions to avoid overhead; just try CALL then CHECK then FOLD.
    let actionCount = 0;
    const maxActions = 200;
    let handOk = true;

    while (!game.isHandComplete() && actionCount < maxActions) {
      const actionSeat = game.getActionSeat();
      if (actionSeat === null) break;

      try {
        game.act(actionSeat, ACTION.CALL);
      } catch (e1) {
        try {
          game.act(actionSeat, ACTION.CHECK);
        } catch (e2) {
          try {
            game.act(actionSeat, ACTION.FOLD);
          } catch (e3) {
            handOk = false;
            break;
          }
        }
      }
      actionCount++;
    }

    if (!game.isHandComplete() || !handOk) {
      errors++;
      continue;
    }

    handsCompleted++;

    // Extract results from events
    const events = game.getEvents();

    // Collect total won per seat from POT_AWARD events
    const wonAmount = {};
    for (const ev of events) {
      if (ev.type === "POT_AWARD" && ev.awards) {
        for (const a of ev.awards) {
          wonAmount[a.seat] = (wonAmount[a.seat] || 0) + a.amount;
        }
      }
    }

    // Get the final board from DEAL_COMMUNITY events.
    // Event cards are display strings like "As", not objects.
    const boardCards = [];
    for (const ev of events) {
      if (ev.type === "DEAL_COMMUNITY") {
        // ev.board contains the full board as display strings after this street
        // ev.cards (or the street-specific new cards) may also exist
        // Use the last DEAL_COMMUNITY event's board for the final state
      }
    }
    // Get from last DEAL_COMMUNITY event
    const dealEvents = events.filter((e) => e.type === "DEAL_COMMUNITY");
    let finalBoard = [];
    if (dealEvents.length > 0) {
      const lastDeal = dealEvents[dealEvents.length - 1];
      // lastDeal.board is an array of display strings
      if (lastDeal.board) {
        finalBoard = lastDeal.board.map((c) =>
          typeof c === "string" ? encodeCardStr(c) : encodeCardObj(c)
        );
      }
    }

    // Pad board to 5 with 52 (empty marker)
    while (finalBoard.length < 5) finalBoard.push(52);

    // Total pot awarded
    const totalPot = Object.values(wonAmount).reduce((a, b) => a + b, 0) || 0;

    // Write one row per player
    for (let i = 0; i < numSeats; i++) {
      if (!heroCards[i]) continue;
      const won = wonAmount[i] ? 1 : 0;
      const equity = totalPot > 0 ? (wonAmount[i] || 0) / totalPot : 0;

      const row = {
        hero: heroCards[i],          // [int, int]
        board: finalBoard,            // [int, int, int, int, int]
        num_opponents: numSeats - 1,
        won: won,
        equity: Math.round(equity * 10000) / 10000,
      };

      ws.write(JSON.stringify(row) + "\n");
      rowsWritten++;
    }

    // Progress
    if ((h + 1) % 10000 === 0) {
      const elapsed = (Date.now() - startTime) / 1000;
      const hps = handsCompleted / elapsed;
      process.stdout.write(
        `\r  ${handsCompleted}/${numHands} hands (${Math.round(hps)} h/s) | ${rowsWritten} rows`
      );
    }
  }

  ws.end(() => {
    const elapsed = (Date.now() - startTime) / 1000;
    console.log(`\nDone. ${handsCompleted} hands, ${rowsWritten} rows in ${elapsed.toFixed(1)}s`);
    console.log(`Errors: ${errors} | Output: ${outPath}`);
  });
}

main();
