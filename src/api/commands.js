"use strict";

const CMD = {
  CREATE_TABLE: "CREATE_TABLE",
  SEAT_PLAYER: "SEAT_PLAYER",
  LEAVE_TABLE: "LEAVE_TABLE",
  START_HAND: "START_HAND",
  PLAYER_ACTION: "PLAYER_ACTION",
  GET_STATE: "GET_STATE",
  GET_EVENT_LOG: "GET_EVENT_LOG",
};

/**
 * Build a command envelope.
 */
function command(type, payload) {
  return { type, payload: payload || {}, ts: Date.now() };
}

/**
 * Successful result.
 */
function ok(events, state) {
  return { ok: true, events: events || [], state: state || null, error: null };
}

/**
 * Failed result.
 */
function fail(error) {
  return { ok: false, events: [], state: null, error: String(error) };
}

module.exports = { CMD, command, ok, fail };
