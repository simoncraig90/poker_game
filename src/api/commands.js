"use strict";

const CMD = {
  CREATE_TABLE: "CREATE_TABLE",
  SEAT_PLAYER: "SEAT_PLAYER",
  LEAVE_TABLE: "LEAVE_TABLE",
  START_HAND: "START_HAND",
  PLAYER_ACTION: "PLAYER_ACTION",
  GET_STATE: "GET_STATE",
  GET_EVENT_LOG: "GET_EVENT_LOG",
  GET_HAND_EVENTS: "GET_HAND_EVENTS",
  GET_HAND_LIST: "GET_HAND_LIST",
  GET_SESSION_LIST: "GET_SESSION_LIST",
  ARCHIVE_SESSION: "ARCHIVE_SESSION",
  CREATE_ACTOR: "CREATE_ACTOR",
  GET_ACTOR: "GET_ACTOR",
  LIST_ACTORS: "LIST_ACTORS",
  UPDATE_ACTOR: "UPDATE_ACTOR",
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
