"use strict";

const { createTable, sitDown, leave, getOccupiedSeats, resetHandState } = require("../engine/table");
const { HandOrchestrator } = require("../engine/orchestrator");
const { EventLog } = require("../engine/event-log");
const { getLegalActions } = require("../engine/betting");
const { PHASE, SEAT_STATUS, ACTION } = require("../engine/types");
const { reconstructState } = require("./reconstruct");
const ev = require("../engine/events");
const { CMD, ok, fail } = require("./commands");

class Session {
  constructor(config, options = {}) {
    this.config = config;
    this.sessionId = options.sessionId || `session-${Date.now()}`;
    this.logPath = options.logPath || null;
    this.rng = options.rng || null;
    this.status = "active"; // "active" or "complete"
    this.actors = options.actors || null; // ActorRegistry instance (optional)

    this.table = createTable(config);
    this.log = new EventLog(this.logPath);
    this.orch = null;
    this.commandLog = [];

    // Emit initial snapshot
    this.log.append(ev.tableSnapshot(this.sessionId, this.table));
  }

  /**
   * Load a session from an existing event log on disk.
   * Reconstructs state, handles mid-hand recovery.
   */
  static load(config, sessionId, eventsPath, options = {}) {
    const session = Object.create(Session.prototype);
    session.config = config;
    session.sessionId = sessionId;
    session.logPath = eventsPath;
    session.rng = options.rng || null;
    session.status = options.status || "active";
    session.actors = options.actors || null;
    session.commandLog = [];
    session.orch = null;

    // Load existing events
    session.log = new EventLog(eventsPath, true);
    const events = session.log.getEvents();

    // Reconstruct state from events
    const rebuilt = reconstructState(events);
    if (!rebuilt) {
      throw new Error("Cannot reconstruct state from event log");
    }

    // Rebuild table from reconstructed state
    session.table = createTable(config);
    session.table.button = rebuilt.button;
    session.table.handsPlayed = rebuilt.handsPlayed;

    // Restore seats directly (bypass buy-in validation — stacks may exceed maxBuyIn from winnings)
    for (let i = 0; i < config.maxSeats; i++) {
      const rs = rebuilt.seats[i];
      if (rs && rs.status === SEAT_STATUS.OCCUPIED && rs.player) {
        const seat = session.table.seats[i];
        seat.status = SEAT_STATUS.OCCUPIED;
        seat.player = { name: rs.player.name, country: rs.player.country, actorId: rs.player.actorId || null };
        seat.stack = rs.stack;
      }
    }

    // Check for incomplete hand (HAND_START without HAND_END)
    const hasIncompleteHand = session._detectIncompleteHand(events);
    if (hasIncompleteHand) {
      session._voidIncompleteHand(events);
    }

    return session;
  }

  /**
   * Detect if the event log ends mid-hand.
   */
  _detectIncompleteHand(events) {
    let lastHandStart = null;
    let lastHandEnd = null;
    for (const e of events) {
      if (e.type === "HAND_START") lastHandStart = e;
      if (e.type === "HAND_END") lastHandEnd = e;
    }
    if (!lastHandStart) return false;
    if (!lastHandEnd) return true;
    // If the last HAND_START is after the last HAND_END, hand is incomplete
    const startIdx = events.indexOf(lastHandStart);
    const endIdx = events.indexOf(lastHandEnd);
    return startIdx > endIdx;
  }

  /**
   * Void an incomplete hand: restore stacks to pre-hand values, emit void HAND_END.
   */
  _voidIncompleteHand(events) {
    // Find the last HAND_START to get pre-hand stacks
    let lastHandStart = null;
    for (const e of events) {
      if (e.type === "HAND_START") lastHandStart = e;
    }
    if (!lastHandStart) return;

    // Restore stacks from HAND_START.players
    for (const [seatStr, p] of Object.entries(lastHandStart.players || {})) {
      const idx = parseInt(seatStr);
      const seat = this.table.seats[idx];
      if (seat && seat.status === SEAT_STATUS.OCCUPIED) {
        seat.stack = p.stack;
      }
    }

    // Clear per-hand state
    for (const seat of Object.values(this.table.seats)) {
      resetHandState(seat);
    }

    // Don't count the voided hand
    this.table.handsPlayed = Math.max(0, this.table.handsPlayed - 1);
    this.table.hand = null;

    // Emit void HAND_END
    this.log.append({
      sessionId: this.sessionId,
      handId: lastHandStart.handId,
      seq: -1,
      type: "HAND_END",
      tableId: this.table.tableId,
      void: true,
      voidReason: "mid-hand recovery",
      _source: { origin: "recovery", ts: Date.now() },
    });

    console.log(`Recovery: voided incomplete hand #${lastHandStart.handId}`);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  Dispatch
  // ═══════════════════════════════════════════════════════════════════════

  dispatch(command) {
    if (this.status === "complete") {
      // Read-only commands allowed on archived sessions
      const readOnly = [CMD.GET_STATE, CMD.GET_EVENT_LOG, CMD.GET_HAND_EVENTS, CMD.GET_HAND_LIST, CMD.GET_SESSION_LIST];
      if (!readOnly.includes(command.type)) {
        return fail("Session is archived (complete). Read-only access.");
      }
    }

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
        case CMD.GET_HAND_EVENTS:
          return this._getHandEvents(command.payload);
        case CMD.GET_HAND_LIST:
          return this._getHandList();
        case CMD.CREATE_ACTOR:
          return this._createActor(command.payload);
        case CMD.GET_ACTOR:
          return this._getActor(command.payload);
        case CMD.LIST_ACTORS:
          return this._listActors();
        case CMD.UPDATE_ACTOR:
          return this._updateActor(command.payload);
        default:
          return fail(`Unknown command: ${command.type}`);
      }
    } catch (e) {
      return fail(e.message);
    }
  }

  // ── Command Handlers ───────────────────────────────────────────────────

  _createTable(_payload) {
    return ok([this.log.getEvents()[0]]);
  }

  _seatPlayer({ seat, name, buyIn, country, actorId }) {
    if (seat == null || !name || buyIn == null) {
      return fail("SEAT_PLAYER requires seat, name, buyIn");
    }

    // Resolve actorId via registry if available
    let resolvedActorId = actorId || null;
    if (this.actors) {
      const result = this.actors.resolve(name, actorId);
      resolvedActorId = result.actorId;
    }

    sitDown(this.table, seat, name, buyIn, country, resolvedActorId);
    const event = this.log.append(ev.seatPlayer(this.sessionId, seat, name, buyIn, country, resolvedActorId));
    return ok([event]);
  }

  _leaveTable({ seat }) {
    if (seat == null) return fail("LEAVE_TABLE requires seat");
    const s = this.table.seats[seat];
    const playerName = s && s.player ? s.player.name : null;
    leave(this.table, seat);
    const event = this.log.append(ev.leaveTable(this.sessionId, seat, playerName));
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
        player: s.player ? { name: s.player.name, country: s.player.country, actorId: s.player.actorId || null } : null,
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

  _getHandEvents({ handId }) {
    if (!handId) return fail("GET_HAND_EVENTS requires handId");
    return ok(this.log.getHandEvents(String(handId)));
  }

  _getHandList() {
    const allEvents = this.log.getEvents();
    const hands = [];
    for (const e of allEvents) {
      if (e.type === "HAND_SUMMARY") {
        hands.push({ handId: e.handId, winner: e.winPlayer, pot: e.totalPot, showdown: e.showdown });
      }
    }
    return ok([], { hands });
  }

  // ── Actor Commands ─────────────────────────────────────────────────────

  _createActor({ name, notes }) {
    if (!this.actors) return fail("No actor registry configured");
    if (!name) return fail("CREATE_ACTOR requires name");
    const actor = this.actors.create(name, notes);
    return ok([], { actor });
  }

  _getActor({ actorId }) {
    if (!this.actors) return fail("No actor registry configured");
    if (!actorId) return fail("GET_ACTOR requires actorId");
    const actor = this.actors.get(actorId);
    if (!actor) return fail(`Actor not found: ${actorId}`);
    return ok([], { actor });
  }

  _listActors() {
    if (!this.actors) return fail("No actor registry configured");
    return ok([], { actors: this.actors.list() });
  }

  _updateActor({ actorId, name, notes }) {
    if (!this.actors) return fail("No actor registry configured");
    if (!actorId) return fail("UPDATE_ACTOR requires actorId");
    const actor = this.actors.update(actorId, { name, notes });
    if (!actor) return fail(`Actor not found: ${actorId}`);
    return ok([], { actor });
  }

  // ── Direct Accessors ───────────────────────────────────────────────────

  getState() { return this._getState().state; }
  getEventLog() { return this.log.getEvents(); }
  getHandEvents(handId) { return this.log.getHandEvents(handId); }
  getCommandLog() { return this.commandLog; }
}

module.exports = { Session };
