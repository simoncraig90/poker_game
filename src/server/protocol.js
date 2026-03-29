"use strict";

/**
 * Wire protocol for WebSocket communication.
 *
 * Client → Server:
 *   { id: "msg-1", cmd: "PLAYER_ACTION", payload: { seat: 0, action: "FOLD" } }
 *
 * Server → Client (response):
 *   { id: "msg-1", ok: true, events: [...], state: null, error: null }
 *
 * Server → Client (broadcast):
 *   { broadcast: true, events: [...] }
 *
 * Server → Client (welcome):
 *   { welcome: true, sessionId: "...", state: {...}, eventCount: N }
 */

function parseClientMessage(raw) {
  try {
    const msg = JSON.parse(raw);
    if (!msg.cmd) return { valid: false, error: "Missing cmd field" };
    return {
      valid: true,
      id: msg.id || null,
      cmd: msg.cmd,
      payload: msg.payload || {},
    };
  } catch (e) {
    return { valid: false, error: `Invalid JSON: ${e.message}` };
  }
}

function formatResponse(id, result) {
  return JSON.stringify({
    id,
    ok: result.ok,
    events: result.events || [],
    state: result.state || null,
    error: result.error || null,
  });
}

function formatBroadcast(events) {
  return JSON.stringify({ broadcast: true, events });
}

function formatWelcome(sessionId, state, eventCount) {
  return JSON.stringify({ welcome: true, sessionId, state, eventCount });
}

function formatError(id, error) {
  return JSON.stringify({ id, ok: false, events: [], state: null, error });
}

module.exports = { parseClientMessage, formatResponse, formatBroadcast, formatWelcome, formatError };
