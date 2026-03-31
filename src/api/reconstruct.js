"use strict";

const { SEAT_STATUS, PHASE } = require("../engine/types");

/**
 * Reconstruct TableState from an event log.
 * Uses only data present in the events — no external state.
 *
 * This is the conformance oracle: if reconstructState(events) matches
 * the live session state, there is no hidden state outside the log.
 */
function reconstructState(events) {
  let table = null;
  let hand = null;

  for (const e of events) {
    switch (e.type) {
      case "TABLE_SNAPSHOT": {
        table = {
          tableId: e.tableId,
          tableName: e.tableName,
          gameType: e.gameType,
          maxSeats: e.maxSeats,
          sb: e.sb,
          bb: e.bb,
          minBuyIn: e.minBuyIn,
          maxBuyIn: e.maxBuyIn,
          button: e.button || -1,
          handsPlayed: 0,
          seats: {},
        };
        for (let i = 0; i < e.maxSeats; i++) {
          const se = e.seats && e.seats[i] ? e.seats[i] : null;
          table.seats[i] = {
            seat: i,
            status: se ? se.status : SEAT_STATUS.EMPTY,
            player: se && se.player ? { name: se.player.name, country: se.player.country } : null,
            stack: se && se.player ? se.player.stack || 0 : 0,
            inHand: false, folded: false, allIn: false,
            bet: 0, totalInvested: 0, holeCards: null,
          };
        }
        break;
      }

      case "SEAT_PLAYER": {
        if (!table) break;
        const s = table.seats[e.seat];
        s.status = SEAT_STATUS.OCCUPIED;
        s.player = { name: e.player, country: e.country || "XX", actorId: e.actorId || null };
        s.stack = e.buyIn;
        break;
      }

      case "LEAVE_TABLE": {
        if (!table) break;
        const s = table.seats[e.seat];
        s.status = SEAT_STATUS.EMPTY;
        s.player = null;
        s.stack = 0;
        break;
      }

      case "HAND_START": {
        if (!table) break;
        table.button = e.button;
        table.handsPlayed++;
        hand = {
          handId: e.handId,
          phase: PHASE.PREFLOP,
          pot: 0,
          board: [],
          actionSeat: null,
        };

        // Reset per-hand state and set stacks from event
        for (const [seatStr, p] of Object.entries(e.players || {})) {
          const idx = parseInt(seatStr);
          const s = table.seats[idx];
          if (!s) continue;
          s.stack = p.stack;
          s.inHand = true;
          s.folded = false;
          s.allIn = false;
          s.bet = 0;
          s.totalInvested = 0;
          s.holeCards = null;
        }
        break;
      }

      case "BLIND_POST": {
        if (!table || !hand) break;
        const s = table.seats[e.seat];
        s.stack -= e.amount;
        s.bet += e.amount;
        s.totalInvested += e.amount;
        hand.pot += e.amount;
        if (s.stack === 0) s.allIn = true;
        break;
      }

      case "HERO_CARDS": {
        if (!table) break;
        const s = table.seats[e.seat];
        if (s) s.holeCards = e.cards;
        break;
      }

      case "PLAYER_ACTION": {
        if (!table || !hand) break;
        const s = table.seats[e.seat];
        if (!s) break;

        if (e.action === "FOLD") {
          s.folded = true;
        } else if (e.delta > 0) {
          s.stack -= e.delta;
          s.bet += e.delta;
          s.totalInvested += e.delta;
          hand.pot += e.delta;
          if (s.stack === 0) s.allIn = true;
        }
        break;
      }

      case "BET_RETURN": {
        if (!table || !hand) break;
        const s = table.seats[e.seat];
        s.stack += e.amount;
        s.totalInvested -= e.amount;
        hand.pot -= e.amount;
        break;
      }

      case "DEAL_COMMUNITY": {
        if (!hand) break;
        hand.board = e.board; // full board from event
        hand.phase = e.street;
        // Reset bets
        for (const s of Object.values(table.seats)) {
          s.bet = 0;
        }
        break;
      }

      case "POT_UPDATE":
        // Informational, no state mutation
        break;

      case "SHOWDOWN_REVEAL":
        // Informational for replay — card reveals don't mutate reconstructed state.
        // POT_AWARD handles the stack changes.
        break;

      case "POT_AWARD": {
        if (!table) break;
        for (const a of e.awards || []) {
          const s = table.seats[a.seat];
          if (s) s.stack += a.amount;
        }
        if (hand) hand.phase = PHASE.SETTLING;
        break;
      }

      case "HAND_SUMMARY": {
        // Informational for reconstruction — phase already set by POT_AWARD
        break;
      }

      case "HAND_RESULT": {
        // Informational for reconstruction
        break;
      }

      case "HAND_END": {
        if (!table) break;

        // Void hand: restore stacks from HAND_START and decrement handsPlayed
        if (e.void) {
          // Find the matching HAND_START to restore stacks
          const matchingStart = events.find(
            (ev) => ev.type === "HAND_START" && ev.handId === e.handId
          );
          if (matchingStart && matchingStart.players) {
            for (const [seatStr, p] of Object.entries(matchingStart.players)) {
              const idx = parseInt(seatStr);
              const s = table.seats[idx];
              if (s) s.stack = p.stack;
            }
          }
          table.handsPlayed = Math.max(0, table.handsPlayed - 1);
        }

        if (hand) hand.phase = PHASE.COMPLETE;
        // Clear per-hand state
        for (const s of Object.values(table.seats)) {
          s.inHand = false;
          s.folded = false;
          s.allIn = false;
          s.bet = 0;
          s.totalInvested = 0;
          s.holeCards = null;
        }
        hand = null;
        break;
      }

      default:
        // Unknown event type — skip silently
        break;
    }
  }

  // Build output state matching session.getState() shape
  const seats = {};
  if (table) {
    for (let i = 0; i < table.maxSeats; i++) {
      const s = table.seats[i];
      seats[i] = {
        seat: i,
        status: s.status,
        player: s.player ? { name: s.player.name, country: s.player.country, actorId: s.player.actorId || null } : null,
        stack: s.stack,
        inHand: s.inHand,
        folded: s.folded,
        allIn: s.allIn,
        bet: s.bet,
        totalInvested: s.totalInvested,
        holeCards: s.holeCards,
      };
    }
  }

  return table ? {
    tableId: table.tableId,
    tableName: table.tableName,
    maxSeats: table.maxSeats,
    sb: table.sb,
    bb: table.bb,
    button: table.button,
    handsPlayed: table.handsPlayed,
    seats,
    hand: hand ? {
      handId: hand.handId,
      phase: hand.phase,
      pot: hand.pot,
      board: hand.board,
      actionSeat: null, // can't reconstruct action seat from events alone
      legalActions: null,
    } : null,
  } : null;
}

module.exports = { reconstructState };
