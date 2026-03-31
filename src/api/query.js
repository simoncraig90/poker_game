"use strict";

const fs = require("fs");
const path = require("path");

/**
 * Hand query and actor stats computation.
 *
 * Full-scan approach: reads event logs from disk for each session.
 * Acceptable for < 50 sessions × 200 hands. No index required at this scale.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 *  IDENTITY EDGE SEMANTICS
 * ═══════════════════════════════════════════════════════════════════════════
 *
 *  Name resolution:
 *    - Case-sensitive. "Alice" ≠ "alice".
 *    - Whitespace: trimmed, internal runs collapsed to single space.
 *    - Duplicate names: allowed. Different actorIds.
 *    - Renamed actor: name change updates the actor file. Old events keep
 *      the old name in their payload, but actorId links them to the current
 *      actor. Queries use actorId, not name.
 *
 *  Dual-seat:
 *    - The same actorId MAY NOT occupy two seats in one hand simultaneously.
 *      The engine enforces one seat per player via SEAT_PLAYER validation.
 *      However, in theory two SEAT_PLAYER events could reference the same
 *      actorId if the operator does it intentionally. The query layer handles
 *      this gracefully: each seat is an independent participation. Stats
 *      would double-count that hand. This is the operator's responsibility.
 *
 *  Anonymous (actorId: null):
 *    - Hands with null actorId are excluded from actor-filtered queries.
 *    - They appear in unfiltered queryHands results.
 *    - getActorStats for null returns zeroed stats (no crash).
 *    - Anonymous hands cannot be linked to any actor retroactively.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 *  METRIC DEFINITIONS
 * ═══════════════════════════════════════════════════════════════════════════
 *
 *  VPIP (Voluntarily Put Money In Pot):
 *    Numerator: hands where actor made at least one voluntary preflop
 *              action that puts chips in (CALL, BET, or RAISE).
 *              Posting blinds does NOT count.
 *    Denominator: total hands dealt to actor.
 *    Range: 0.0 – 1.0.
 *    A walk (everyone folds to BB) counts as a hand dealt but NOT vpip.
 *
 *  PFR (Preflop Raise):
 *    Numerator: hands where actor made any RAISE (or open-BET) preflop.
 *              A CALL followed by a RAISE still counts — any preflop
 *              raise action is sufficient. This matches standard poker
 *              tracking tool conventions (PokerTracker, HEM).
 *    Denominator: total hands dealt to actor.
 *    Range: 0.0 – 1.0. Always ≤ VPIP.
 *
 *  WTSD (Went To Showdown):
 *    Numerator: hands where actor did NOT fold preflop AND the hand
 *              reached showdown (SHOWDOWN_REVEAL event exists) AND
 *              the actor was still in (not folded) at showdown.
 *    Denominator: hands where actor did not fold preflop.
 *    Range: 0.0 – 1.0.
 *    Hands where actor only posted blinds and folded preflop are excluded
 *    from the denominator.
 *
 *  WSD (Won at Showdown):
 *    Numerator: showdowns where actor received a POT_AWARD (any pot).
 *    Denominator: showdowns reached (same as WTSD numerator).
 *    Range: 0.0 – 1.0.
 *
 *  aggFactor (Aggression Factor):
 *    Formula: (count of BET + RAISE actions) / (count of CALL actions).
 *    Scope: postflop only (FLOP + TURN + RIVER). Preflop excluded per
 *           standard convention — preflop calls are structurally different.
 *    If postflop calls = 0: null (undefined, not infinity).
 *
 *  netResult:
 *    totalWon - totalInvested across all queried hands.
 *    totalInvested = sum of HAND_START stack - endStack for losing hands,
 *                    or equivalently sum of all chips put in per hand.
 *    totalWon = sum of POT_AWARD amounts received.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 */

// ── Position Labels ───────────────────────────────────────────────────────

/**
 * Derive position label for a seat given the button and occupied seats.
 * Standard 6-max labels: BTN, SB, BB, UTG, MP, CO.
 */
function derivePosition(seat, buttonSeat, occupiedSeats) {
  // Sort occupied seats in clockwise order starting after button
  const n = occupiedSeats.length;
  if (n < 2) return "BTN";

  // Build order: clockwise from button
  const sorted = [...occupiedSeats].sort((a, b) => a - b);
  const btnIdx = sorted.indexOf(buttonSeat);
  const order = [];
  for (let i = 0; i < n; i++) {
    order.push(sorted[(btnIdx + i) % n]);
  }
  // order[0] = BTN, order[1] = SB, order[2] = BB, ...

  const posIdx = order.indexOf(seat);

  if (n === 2) {
    // Heads-up: BTN = SB, other = BB
    return posIdx === 0 ? "SB" : "BB";
  }

  const labels6 = ["BTN", "SB", "BB", "UTG", "MP", "CO"];
  // For < 6 players, drop middle positions
  if (n <= 6) {
    // Map position index to label
    if (posIdx === 0) return "BTN";
    if (posIdx === 1) return "SB";
    if (posIdx === 2) return "BB";
    if (posIdx === n - 1) return "CO";
    if (n >= 5 && posIdx === n - 2) return "MP";
    return "UTG";
  }
  return labels6[Math.min(posIdx, 5)] || "UTG";
}

// ── Event Log Loading ─────────────────────────────────────────────────────

function loadSessionEvents(storage, sessionId) {
  const info = storage.load(sessionId);
  if (!info || !fs.existsSync(info.eventsPath)) return [];
  const content = fs.readFileSync(info.eventsPath, "utf8").trim();
  if (!content) return [];
  return content.split("\n").filter(Boolean).map((line) => JSON.parse(line));
}

// ── Hand Parsing ──────────────────────────────────────────────────────────

/**
 * Parse a session's event log into structured hand records.
 * Returns an array of HandRecord objects.
 */
function parseHands(events, sessionId) {
  const hands = [];
  let current = null;

  for (const e of events) {
    if (e.type === "HAND_START") {
      current = {
        sessionId: sessionId || e.sessionId,
        handId: e.handId,
        button: e.button,
        players: e.players || {},
        blinds: [],
        actions: [],
        awards: [],
        showdown: false,
        reveals: [],
        summary: null,
        results: [],
        voided: false,
        timestamp: e._source ? e._source.ts : null,
      };
      continue;
    }

    if (!current) continue;
    if (e.handId && e.handId !== current.handId) continue;

    switch (e.type) {
      case "BLIND_POST":
        current.blinds.push({ seat: e.seat, amount: e.amount, blindType: e.blindType });
        break;
      case "PLAYER_ACTION":
        current.actions.push({ seat: e.seat, action: e.action, amount: e.totalBet, delta: e.delta, street: e.street });
        break;
      case "BET_RETURN":
        current.actions.push({ seat: e.seat, action: "BET_RETURN", amount: 0, delta: -(e.amount), street: null });
        break;
      case "POT_AWARD":
        for (const a of e.awards || []) {
          current.awards.push({ seat: a.seat, amount: a.amount, potIndex: e.potIndex });
        }
        break;
      case "SHOWDOWN_REVEAL":
        current.showdown = true;
        current.reveals = e.reveals || [];
        break;
      case "HAND_SUMMARY":
        current.summary = { winSeat: e.winSeat, winPlayer: e.winPlayer, totalPot: e.totalPot, showdown: e.showdown, handRank: e.handRank };
        break;
      case "HAND_RESULT":
        current.results.push(...(e.results || []));
        break;
      case "HAND_END":
        if (e.void) { current.voided = true; }
        hands.push(current);
        current = null;
        break;
    }
  }

  return hands;
}

/**
 * Build a participation record for one actor in one hand.
 */
function buildParticipation(hand, seat, actorId) {
  const playerInfo = hand.players[String(seat)];
  const startStack = playerInfo ? playerInfo.stack : 0;

  // Occupied seats for position derivation
  const occupiedSeats = Object.keys(hand.players).map(Number);

  // Actions by this seat (real actions only for behavioral analysis)
  const myActions = hand.actions.filter((a) => a.seat === seat);
  const realActions = myActions.filter((a) => a.action !== "BET_RETURN");
  const preflopActions = realActions.filter((a) => a.street === "PREFLOP");
  const voluntaryPreflop = preflopActions.filter((a) => a.action === "CALL" || a.action === "BET" || a.action === "RAISE");
  const preflopRaised = preflopActions.some((a) => a.action === "RAISE" || a.action === "BET");

  // Did actor fold preflop?
  const foldedPreflop = preflopActions.some((a) => a.action === "FOLD");

  // Did actor fold at all?
  const folded = realActions.some((a) => a.action === "FOLD");

  // Awards received
  const myAwards = hand.awards.filter((a) => a.seat === seat);
  const totalWon = myAwards.reduce((s, a) => s + a.amount, 0);

  // Total invested: blinds + action deltas
  const myBlinds = hand.blinds.filter((b) => b.seat === seat);
  const blindAmount = myBlinds.reduce((s, b) => s + b.amount, 0);
  const actionAmount = myActions.reduce((s, a) => s + (a.delta || 0), 0);
  const totalInvested = blindAmount + actionAmount;

  // Was actor at showdown (not folded and hand reached showdown)?
  const atShowdown = hand.showdown && !folded;

  // Reveal info
  const reveal = hand.reveals.find((r) => r.seat === seat);

  // Result classification
  let result = "lost";
  if (totalWon > 0 && totalWon > totalInvested) result = "won";
  else if (totalWon > 0 && totalWon === totalInvested) result = "split";
  else if (totalWon > 0) result = "won"; // partial win from side pot still counts

  // Aggression counts — postflop only (standard convention)
  const postflopActions = realActions.filter((a) => a.street && a.street !== "PREFLOP");
  const betsRaises = postflopActions.filter((a) => a.action === "BET" || a.action === "RAISE").length;
  const calls = postflopActions.filter((a) => a.action === "CALL").length;

  return {
    actorId,
    sessionId: hand.sessionId,
    handId: hand.handId,
    seat,
    position: derivePosition(seat, hand.button, occupiedSeats),
    startStack,
    totalInvested,
    totalWon,
    netResult: totalWon - totalInvested,
    actions: myActions,
    result,
    foldedPreflop,
    folded,
    wentToShowdown: atShowdown,
    wonAtShowdown: atShowdown && totalWon > 0,
    handRank: reveal ? reveal.handName : null,
    vpipHand: voluntaryPreflop.length > 0,
    pfrHand: preflopRaised,
    betsRaises,
    calls,
    showdown: hand.showdown,
    totalPot: hand.summary ? hand.summary.totalPot : 0,
    voided: hand.voided,
    timestamp: hand.timestamp,
  };
}

// ── Query Hands ───────────────────────────────────────────────────────────

/**
 * Query hands across sessions with filters.
 *
 * @param {SessionStorage} storage
 * @param {object} filters
 * @param {string} [filters.actorId]
 * @param {string} [filters.sessionId]
 * @param {boolean} [filters.showdown] — true = showdown only, false = fold-out only
 * @param {string} [filters.position] — "BTN", "SB", "BB", "UTG", "MP", "CO"
 * @param {string} [filters.result] — "won", "lost", "split"
 * @param {number} [filters.after] — timestamp ms, hands after this time
 * @param {number} [filters.before] — timestamp ms, hands before this time
 * @returns {Participation[]}
 */
function queryHands(storage, filters = {}) {
  const sessionList = filters.sessionId
    ? [{ sessionId: filters.sessionId }]
    : storage.list();

  const results = [];

  for (const meta of sessionList) {
    const events = loadSessionEvents(storage, meta.sessionId);
    const hands = parseHands(events, meta.sessionId);

    for (const hand of hands) {
      if (hand.voided) continue;

      // Showdown filter
      if (filters.showdown === true && !hand.showdown) continue;
      if (filters.showdown === false && hand.showdown) continue;

      // Date range
      if (filters.after && hand.timestamp && hand.timestamp < filters.after) continue;
      if (filters.before && hand.timestamp && hand.timestamp > filters.before) continue;

      // Find matching participants
      for (const [seatStr, playerInfo] of Object.entries(hand.players)) {
        const seat = parseInt(seatStr);
        const actorId = playerInfo.actorId || null;

        // Actor filter
        if (filters.actorId && actorId !== filters.actorId) continue;
        // Exclude anonymous from actor-specific queries
        if (filters.actorId && !actorId) continue;

        const p = buildParticipation(hand, seat, actorId);

        // Position filter
        if (filters.position && p.position !== filters.position) continue;

        // Result filter
        if (filters.result && p.result !== filters.result) continue;

        results.push(p);
      }
    }
  }

  return results;
}

// ── Actor Stats ───────────────────────────────────────────────────────────

/**
 * Compute aggregate stats for one actor.
 *
 * @param {SessionStorage} storage
 * @param {string} actorId — null returns zeroed stats
 * @param {string} [sessionId] — scope to one session
 * @returns {ActorStats}
 */
function getActorStats(storage, actorId, sessionId) {
  if (!actorId) {
    return zeroStats(null);
  }

  const filters = { actorId };
  if (sessionId) filters.sessionId = sessionId;
  const participations = queryHands(storage, filters);

  if (participations.length === 0) return zeroStats(actorId);

  const handsDealt = participations.length;
  const vpipCount = participations.filter((p) => p.vpipHand).length;
  const pfrCount = participations.filter((p) => p.pfrHand).length;

  // WTSD denominator: hands where actor did not fold preflop
  const didNotFoldPreflop = participations.filter((p) => !p.foldedPreflop);
  const wtsdNumer = didNotFoldPreflop.filter((p) => p.wentToShowdown).length;
  const wtsdDenom = didNotFoldPreflop.length;

  // WSD
  const wsdNumer = participations.filter((p) => p.wonAtShowdown).length;
  const wsdDenom = wtsdNumer;

  // Aggression
  const totalBetsRaises = participations.reduce((s, p) => s + p.betsRaises, 0);
  const totalCalls = participations.reduce((s, p) => s + p.calls, 0);

  // Totals
  const totalInvested = participations.reduce((s, p) => s + p.totalInvested, 0);
  const totalWon = participations.reduce((s, p) => s + p.totalWon, 0);
  const handsWon = participations.filter((p) => p.result === "won").length;
  const avgPotWon = handsWon > 0
    ? participations.filter((p) => p.result === "won").reduce((s, p) => s + p.totalWon, 0) / handsWon
    : 0;

  // Position breakdown
  const handsByPosition = {};
  for (const p of participations) {
    handsByPosition[p.position] = (handsByPosition[p.position] || 0) + 1;
  }

  return {
    actorId,
    handsDealt,
    handsWon,
    vpip: handsDealt > 0 ? vpipCount / handsDealt : 0,
    pfr: handsDealt > 0 ? pfrCount / handsDealt : 0,
    wtsd: wtsdDenom > 0 ? wtsdNumer / wtsdDenom : 0,
    wsd: wsdDenom > 0 ? wsdNumer / wsdDenom : 0,
    aggFactor: totalCalls > 0 ? totalBetsRaises / totalCalls : null,
    totalInvested,
    totalWon,
    netResult: totalWon - totalInvested,
    avgPotWon: Math.round(avgPotWon),
    handsByPosition,
  };
}

function zeroStats(actorId) {
  return {
    actorId,
    handsDealt: 0, handsWon: 0,
    vpip: 0, pfr: 0, wtsd: 0, wsd: 0,
    aggFactor: null,
    totalInvested: 0, totalWon: 0, netResult: 0, avgPotWon: 0,
    handsByPosition: {},
  };
}

module.exports = { queryHands, getActorStats, derivePosition, parseHands, buildParticipation, loadSessionEvents };
