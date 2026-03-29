"use strict";

const { createTable, sitDown, leave, getOccupiedSeats } = require("../engine/table");
const { HandOrchestrator } = require("../engine/orchestrator");
const { EventLog } = require("../engine/event-log");
const { getLegalActions } = require("../engine/betting");
const { PHASE, SEAT_STATUS, ACTION } = require("../engine/types");
const ev = require("../engine/events");
const { CMD, ok, fail } = require("./commands");

class Session {
  constructor(config, options = {}) {
    this.config = config;
    this.sessionId = options.sessionId || `session-${Date.now()}`;
    this.logPath = options.logPath || null;
    this.rng = options.rng || null;

    this.table = createTable(config);
    this.log = new EventLog(this.logPath);
    this.orch = null;
    this.commandLog = []; // append-only command history

    // Emit initial snapshot
    this.log.append(ev.tableSnapshot(this.sessionId, this.table));
  }

  /**
   * Single entry point. All mutations go through here.
   * Returns { ok, events, error, state }
   */
  dispatch(command) {
    this.commandLog.push(command);

    try {
      switch (command.type) {
        case CMD.CREATE_TABLE:
          return this._createTable(command.payload);
        case CMD.SEAT_PLAYER:
          return this._seatPlayer(command.payload);
        case CMD.LEAVE_TABLE:
          return this._leaveTable(command.payload);
        case CMD.START_HAND:
          return this._startHand(command.payload);
        case CMD.PLAYER_ACTION:
          return this._playerAction(command.payload);
        case CMD.GET_STATE:
          return this._getState();
        case CMD.GET_EVENT_LOG:
          return this._getEventLog();
        default:
          return fail(`Unknown command: ${command.type}`);
      }
    } catch (e) {
      return fail(e.message);
    }
  }

  // ── Command Handlers ───────────────────────────────────────────────────

  _createTable(_payload) {
    // Table already created in constructor. This is a no-op for re-dispatch.
    // Return the snapshot event that was already emitted.
    return ok([this.log.getEvents()[0]]);
  }

  _seatPlayer({ seat, name, buyIn, country }) {
    if (seat == null || !name || buyIn == null) {
      return fail("SEAT_PLAYER requires seat, name, buyIn");
    }
    sitDown(this.table, seat, name, buyIn, country);

    const event = this.log.append(
      ev.seatPlayer(this.sessionId, seat, name, buyIn, country)
    );
    return ok([event]);
  }

  _leaveTable({ seat }) {
    if (seat == null) return fail("LEAVE_TABLE requires seat");

    const s = this.table.seats[seat];
    const playerName = s && s.player ? s.player.name : null;
    leave(this.table, seat);

    const event = this.log.append(
      ev.leaveTable(this.sessionId, seat, playerName)
    );
    return ok([event]);
  }

  _startHand(_payload) {
    const beforeLen = this.log.getEvents().length;
    this.orch = new HandOrchestrator(this.table, this.log, this.sessionId, this.rng);
    this.orch.startHand();
    const newEvents = this.log.getEvents().slice(beforeLen);
    return ok(newEvents);
  }

  _playerAction({ seat, action, amount }) {
    if (seat == null || !action) return fail("PLAYER_ACTION requires seat, action");
    if (!this.orch) return fail("No hand in progress");

    const beforeLen = this.log.getEvents().length;
    this.orch.act(seat, action, amount);
    const newEvents = this.log.getEvents().slice(beforeLen);
    return ok(newEvents);
  }

  _getState() {
    const hand = this.table.hand;
    const actionSeat = this.orch ? this.orch.getActionSeat() : null;

    // Compute legal actions for the active seat
    let legalActions = null;
    if (actionSeat != null && hand && this.orch && this.orch.round) {
      const seat = this.table.seats[actionSeat];
      const handState = this.orch.round.getHandState();
      legalActions = getLegalActions(seat, handState, this.table.bb);
    }

    const seats = {};
    for (let i = 0; i < this.table.maxSeats; i++) {
      const s = this.table.seats[i];
      seats[i] = {
        seat: i,
        status: s.status,
        player: s.player ? { name: s.player.name, country: s.player.country } : null,
        stack: s.stack,
        inHand: s.inHand,
        folded: s.folded,
        allIn: s.allIn,
        bet: s.bet,
        totalInvested: s.totalInvested,
        holeCards: s.holeCards ? s.holeCards.map((c) => c.display) : null,
      };
    }

    return ok([], {
      tableId: this.table.tableId,
      tableName: this.table.tableName,
      maxSeats: this.table.maxSeats,
      sb: this.table.sb,
      bb: this.table.bb,
      button: this.table.button,
      handsPlayed: this.table.handsPlayed,
      seats,
      hand: hand ? {
        handId: hand.handId,
        phase: hand.phase,
        pot: hand.pot,
        board: hand.board.map((c) => c.display),
        actionSeat,
        legalActions,
      } : null,
    });
  }

  _getEventLog() {
    return ok(this.log.getEvents());
  }

  // ── Direct Accessors (for tests) ──────────────────────────────────────

  getState() {
    return this._getState().state;
  }

  getEventLog() {
    return this.log.getEvents();
  }

  getHandEvents(handId) {
    return this.log.getHandEvents(handId);
  }

  getCommandLog() {
    return this.commandLog;
  }
}

module.exports = { Session };
