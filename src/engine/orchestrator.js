"use strict";

const { PHASE, ACTION, SEAT_STATUS } = require("./types");
const { createDeck, dealCards } = require("./deck");
const { nextButton, assignBlinds, preflopActionOrder, postflopActionOrder, nextOccupied } = require("./dealer");
const { validateAction } = require("./betting");
const { BettingRound } = require("./round");
const { settleNoShowdown, settleShowdown } = require("./settle");
const { checkInvariants, checkAccountingClosure } = require("./invariants");
const { resetHandState } = require("./table");
const ev = require("./events");

class HandOrchestrator {
  constructor(table, eventLog, sessionId, rng) {
    this.table = table;
    this.log = eventLog;
    this.sessionId = sessionId;
    this.rng = rng || null; // optional deterministic RNG
    this.round = null;      // current BettingRound
    this.deck = null;
    this.handIdCounter = table.handsPlayed || 0;
    this.startStacks = {};  // for accounting check
  }

  emit(event) {
    this.log.append(event);
    return event;
  }

  _getActivePlayers() {
    return Object.values(this.table.seats).filter(
      (s) => s.inHand && !s.folded && s.status === SEAT_STATUS.OCCUPIED
    );
  }

  _getActiveNonAllIn() {
    return this._getActivePlayers().filter((s) => !s.allIn);
  }

  // ── Start Hand ─────────────────────────────────────────────────────────

  startHand() {
    const table = this.table;
    const occupied = Object.values(table.seats).filter((s) => s.status === SEAT_STATUS.OCCUPIED);
    if (occupied.length < 2) throw new Error("Need at least 2 players");

    // Rotate button
    table.button = nextButton(table.button, table.seats, table.maxSeats);
    const { sbSeat, bbSeat } = assignBlinds(table.button, table.seats, table.maxSeats);

    // New hand
    this.handIdCounter++;
    const handId = String(this.handIdCounter);
    table.hand = {
      handId,
      button: table.button,
      sbSeat,
      bbSeat,
      phase: PHASE.PREFLOP,
      board: [],
      pot: 0,
      rake: 0,
      actions: [],
      actionSeat: null,
      winners: [],
      resultText: [],
      showdown: false,
      handRank: null,
      winCards: null,
    };

    // Reset per-hand state
    for (const seat of occupied) {
      seat.inHand = true;
      seat.folded = false;
      seat.allIn = false;
      seat.bet = 0;
      seat.totalInvested = 0;
      seat.holeCards = null;
    }

    // Snapshot starting stacks
    this.startStacks = {};
    const playerMap = {};
    for (const seat of occupied) {
      this.startStacks[seat.seat] = seat.stack;
      playerMap[seat.seat] = { name: seat.player.name, stack: seat.stack, country: seat.player.country, actorId: seat.player.actorId || null };
    }

    table.handsPlayed++;

    // Emit HAND_START
    this.emit(ev.handStart(this.sessionId, handId, table, playerMap));

    // Post blinds
    this._postBlind(sbSeat, table.sb, "SB");
    this._postBlind(bbSeat, table.bb, "BB");

    // Deal hole cards
    this.deck = createDeck(this.rng);
    for (const seat of occupied) {
      seat.holeCards = dealCards(this.deck, 2);
      this.emit(ev.heroCards(this.sessionId, handId, seat.seat, seat.holeCards));
    }

    // Set up preflop betting round
    const order = preflopActionOrder(table.button, sbSeat, bbSeat, table.seats, table.maxSeats);
    this.round = new BettingRound(order, table.seats, true);
    this.round.initPreflop(table.bb, bbSeat);

    // Set action seat
    const nextSeat = this.round.getNextToAct();
    table.hand.actionSeat = nextSeat;

    this._checkInv();
  }

  _postBlind(seatIdx, amount, type) {
    const seat = this.table.seats[seatIdx];
    const posted = Math.min(amount, seat.stack); // short stack posts what they can
    seat.stack -= posted;
    seat.bet += posted;
    seat.totalInvested += posted;
    this.table.hand.pot += posted;

    if (seat.stack === 0) seat.allIn = true;
    if (type === "SB") this.table.hand.sbSeat = seatIdx;
    if (type === "BB") this.table.hand.bbSeat = seatIdx;

    this.table.hand.actions.push({
      seat: seatIdx, type: type === "SB" ? ACTION.BLIND_SB : ACTION.BLIND_BB,
      amount: posted, delta: posted, street: PHASE.PREFLOP, inferred: false,
    });

    this.emit(ev.blindPost(this.sessionId, this.table.hand.handId, seatIdx, seat.player.name, posted, type));
  }

  // ── Player Action ──────────────────────────────────────────────────────

  act(seatIdx, action, amount) {
    const table = this.table;
    const hand = table.hand;
    if (!hand || hand.phase === PHASE.COMPLETE || hand.phase === PHASE.SETTLING) {
      throw new Error("No active hand");
    }
    if (hand.actionSeat !== seatIdx) {
      throw new Error(`Not seat ${seatIdx}'s turn (expected seat ${hand.actionSeat})`);
    }

    const seat = table.seats[seatIdx];
    const handState = this.round.getHandState();
    const result = validateAction(seat, action, amount, handState, table.bb);
    if (!result.valid) throw new Error(result.error);

    // Apply to state
    const { action: validAction, amount: totalBet, delta } = result;

    if (validAction === ACTION.FOLD) {
      seat.folded = true;
    } else if (delta > 0) {
      seat.stack -= delta;
      seat.bet += delta;
      seat.totalInvested += delta;
      hand.pot += delta;
      if (seat.stack === 0) seat.allIn = true;
    }

    // Record action
    hand.actions.push({
      seat: seatIdx, type: validAction,
      amount: totalBet, delta, street: hand.phase, inferred: false,
    });

    // Emit event
    this.emit(ev.playerAction(
      this.sessionId, hand.handId, seatIdx, seat.player.name,
      validAction, totalBet, delta, hand.phase, false
    ));

    // Update round
    this.round.applyAction(seatIdx, validAction, totalBet, delta);

    this._checkInv();

    // Check if round is complete
    this._advanceIfRoundComplete();
  }

  // ── Round + Street Advancement ─────────────────────────────────────────

  _advanceIfRoundComplete() {
    if (!this.round.isComplete()) {
      // Set next to act
      this.table.hand.actionSeat = this.round.getNextToAct();
      return;
    }

    const hand = this.table.hand;
    const active = this._getActivePlayers();

    // BET_RETURN for uncalled bet
    const ret = this.round.getUncalledReturn();
    if (ret && ret.amount > 0) {
      const seat = this.table.seats[ret.seat];
      seat.stack += ret.amount;
      seat.totalInvested -= ret.amount;
      hand.pot -= ret.amount;
      this.emit(ev.betReturn(this.sessionId, hand.handId, ret.seat, seat.player.name, ret.amount));
    }

    // Reset bets for street transition
    for (const s of Object.values(this.table.seats)) {
      s.bet = 0;
    }

    // Check if only 1 player remains → settle
    if (active.length <= 1) {
      this._settle();
      return;
    }

    // Check if all remaining are all-in → run out board and settle
    const canAct = this._getActiveNonAllIn();
    if (canAct.length <= 1 && active.length >= 2) {
      // Everyone all-in (or 1 player vs all-ins) — run out remaining streets
      this._runOutBoard();
      return;
    }

    // Advance to next street
    this._nextStreet();
  }

  _nextStreet() {
    const hand = this.table.hand;
    const table = this.table;

    let newPhase;
    let cardCount;

    switch (hand.phase) {
      case PHASE.PREFLOP: newPhase = PHASE.FLOP; cardCount = 3; break;
      case PHASE.FLOP:    newPhase = PHASE.TURN; cardCount = 1; break;
      case PHASE.TURN:    newPhase = PHASE.RIVER; cardCount = 1; break;
      case PHASE.RIVER:
        hand.phase = PHASE.SHOWDOWN;
        hand.actionSeat = null;
        this._showdown();
        return;
      default:
        throw new Error(`Cannot advance from phase ${hand.phase}`);
    }

    // Deal community cards
    const newCards = dealCards(this.deck, cardCount);
    hand.board.push(...newCards);
    hand.phase = newPhase;

    this.emit(ev.dealCommunity(this.sessionId, hand.handId, newPhase, newCards, hand.board));

    // New betting round
    const order = postflopActionOrder(table.button, table.seats, table.maxSeats);
    // Filter to only active non-folded seats
    const activeOrder = order.filter((idx) => {
      const s = table.seats[idx];
      return s && s.inHand && !s.folded;
    });

    this.round = new BettingRound(activeOrder, table.seats, false);

    // Set next to act
    hand.actionSeat = this.round.getNextToAct();

    // If only all-in players remain after dealing, round completes immediately
    if (this.round.isComplete()) {
      this._advanceIfRoundComplete();
    }
  }

  _runOutBoard() {
    const hand = this.table.hand;
    // Deal remaining streets without action
    while (hand.board.length < 5) {
      const needed = hand.board.length === 0 ? 3 : 1;
      const newCards = dealCards(this.deck, needed);
      hand.board.push(...newCards);
      const street = hand.board.length === 3 ? PHASE.FLOP : hand.board.length === 4 ? PHASE.TURN : PHASE.RIVER;
      hand.phase = street;
      this.emit(ev.dealCommunity(this.sessionId, hand.handId, street, newCards, hand.board));
    }
    hand.phase = PHASE.SHOWDOWN;
    hand.actionSeat = null;
    this._showdown();
  }

  // ── Settlement ─────────────────────────────────────────────────────────

  _settle() {
    const hand = this.table.hand;
    const active = this._getActivePlayers();

    if (active.length !== 1) {
      throw new Error("Settlement requires exactly 1 player remaining (showdown deferred)");
    }

    hand.phase = PHASE.SETTLING;
    hand.actionSeat = null;

    const winner = active[0].seat;
    const settleEvents = settleNoShowdown(this.sessionId, hand.handId, this.table, hand, winner);
    for (const e of settleEvents) {
      this.emit(e);
    }

    hand.phase = PHASE.COMPLETE;
    hand.rake = 0; // no rake for now

    // Clear per-hand state
    for (const seat of Object.values(this.table.seats)) {
      if (seat.inHand) resetHandState(seat);
    }

    // Accounting check
    const check = checkAccountingClosure(this.table, this.startStacks, hand.rake);
    if (!check.passed) {
      console.error("ACCOUNTING VIOLATION:", check.violations);
    }
  }

  // ── Showdown ───────────────────────────────────────────────────────────

  _showdown() {
    const hand = this.table.hand;
    const table = this.table;

    hand.phase = PHASE.SETTLING;
    hand.showdown = true;

    // Build seat order: clockwise from button (for odd-chip allocation)
    const seatOrder = postflopActionOrder(table.button, table.seats, table.maxSeats);

    const settleEvents = settleShowdown(this.sessionId, hand.handId, table, hand, seatOrder);
    for (const e of settleEvents) {
      this.emit(e);
    }

    hand.phase = PHASE.COMPLETE;
    hand.rake = 0;

    // Clear per-hand state
    for (const seat of Object.values(table.seats)) {
      if (seat.inHand) resetHandState(seat);
    }

    // Accounting check
    const acctCheck = checkAccountingClosure(table, this.startStacks, hand.rake);
    if (!acctCheck.passed) {
      console.error("ACCOUNTING VIOLATION:", acctCheck.violations);
    }
  }

  // ── Query ──────────────────────────────────────────────────────────────

  getLegalActions(seatIdx) {
    const seat = this.table.seats[seatIdx];
    if (!seat || !seat.inHand || seat.folded || seat.allIn) {
      return { actions: [], callAmount: 0, minBet: 0, minRaise: 0, maxRaise: 0 };
    }
    const handState = this.round ? this.round.getHandState() : { currentBet: 0, lastRaiseSize: 0 };
    const { getLegalActions: gla } = require("./betting");
    return gla(seat, handState, this.table.bb);
  }

  getActionSeat() {
    return this.table.hand ? this.table.hand.actionSeat : null;
  }

  isHandComplete() {
    return !this.table.hand || this.table.hand.phase === PHASE.COMPLETE;
  }

  // ── Invariant Checks ──────────────────────────────────────────────────

  _checkInv() {
    const result = checkInvariants(this.table);
    if (!result.passed) {
      console.error("INVARIANT VIOLATION:", result.violations);
    }
  }
}

module.exports = { HandOrchestrator };
