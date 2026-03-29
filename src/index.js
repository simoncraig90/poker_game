"use strict";

const { createTable, sitDown, leave } = require("./engine/table");
const { HandOrchestrator } = require("./engine/orchestrator");
const { EventLog } = require("./engine/event-log");
const { ACTION } = require("./engine/types");
const ev = require("./engine/events");

/**
 * Create a poker table with the full common-path API.
 *
 * Usage:
 *   const game = createGame({ tableId: "t1", tableName: "Test", sb: 5, bb: 10, ... });
 *   game.sitDown(0, "Alice", 1000, "US");
 *   game.sitDown(1, "Bob", 1000, "GB");
 *   game.startHand();
 *   game.act(2, "CALL");     // seat 2 calls
 *   game.act(0, "FOLD");     // seat 0 folds
 *   // ... hand settles automatically when only 1 player remains
 */
function createGame(config, options = {}) {
  const table = createTable(config);
  const sessionId = options.sessionId || `engine-${Date.now()}`;
  const logPath = options.logPath || null;
  const log = new EventLog(logPath);
  const rng = options.rng || null;
  let orch = null;

  // Emit initial snapshot
  log.append(ev.tableSnapshot(sessionId, table));

  return {
    // ── Seat Management ──────────────────────────────────────────────

    sitDown(seatIndex, playerName, buyIn, country) {
      sitDown(table, seatIndex, playerName, buyIn, country);
    },

    leave(seatIndex) {
      leave(table, seatIndex);
    },

    // ── Hand Lifecycle ───────────────────────────────────────────────

    startHand() {
      orch = new HandOrchestrator(table, log, sessionId, rng);
      orch.startHand();
    },

    act(seatIndex, action, amount) {
      if (!orch) throw new Error("No hand in progress");
      orch.act(seatIndex, action, amount);
    },

    // ── Query ────────────────────────────────────────────────────────

    getActionSeat() {
      return orch ? orch.getActionSeat() : null;
    },

    isHandComplete() {
      return orch ? orch.isHandComplete() : true;
    },

    getState() {
      return {
        table: { ...table, seats: { ...table.seats } },
        hand: table.hand,
      };
    },

    getEvents() {
      return log.getEvents();
    },

    getHandEvents(handId) {
      return log.getHandEvents(handId);
    },

    // ── Constants ────────────────────────────────────────────────────
    ACTION,
  };
}

module.exports = { createGame };
