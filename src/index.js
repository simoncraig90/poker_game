"use strict";

// Phase 1: direct engine API
const { createTable, sitDown, leave } = require("./engine/table");
const { HandOrchestrator } = require("./engine/orchestrator");
const { EventLog } = require("./engine/event-log");
const { ACTION, EVENT, PHASE } = require("./engine/types");
const ev = require("./engine/events");

// Phase 2: command/session API
const { Session } = require("./api/session");
const { CMD, command } = require("./api/commands");
const { reconstructState } = require("./api/reconstruct");

/**
 * Phase 1 API — direct method calls (kept for backward compat with tests).
 */
function createGame(config, options = {}) {
  const table = createTable(config);
  const sessionId = options.sessionId || `engine-${Date.now()}`;
  const logPath = options.logPath || null;
  const log = new EventLog(logPath);
  const rng = options.rng || null;
  let orch = null;

  log.append(ev.tableSnapshot(sessionId, table));

  return {
    sitDown(seatIndex, playerName, buyIn, country) { sitDown(table, seatIndex, playerName, buyIn, country); },
    leave(seatIndex) { leave(table, seatIndex); },
    startHand() { orch = new HandOrchestrator(table, log, sessionId, rng); orch.startHand(); },
    act(seatIndex, action, amount) { if (!orch) throw new Error("No hand in progress"); orch.act(seatIndex, action, amount); },
    getActionSeat() { return orch ? orch.getActionSeat() : null; },
    isHandComplete() { return orch ? orch.isHandComplete() : true; },
    getState() { return { table: { ...table, seats: { ...table.seats } }, hand: table.hand }; },
    getLegalActions(seatIndex) {
      if (!orch) return { actions: [], callAmount: 0, minBet: 0, minRaise: 0, maxRaise: 0 };
      return orch.getLegalActions(seatIndex);
    },
    getEvents() { return log.getEvents(); },
    getHandEvents(handId) { return log.getHandEvents(handId); },
    ACTION,
  };
}

/**
 * Phase 2 API — command-driven session.
 */
function createSession(config, options) {
  return new Session(config, options);
}

module.exports = {
  // Phase 1
  createGame,
  // Phase 2
  createSession, Session, CMD, command, reconstructState,
  // Constants
  ACTION, EVENT, PHASE,
};
