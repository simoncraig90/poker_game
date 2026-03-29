#!/usr/bin/env node
"use strict";

const { WebSocketServer } = require("ws");
const path = require("path");
const { Session } = require("../api/session");
const { CMD, command } = require("../api/commands");
const { parseClientMessage, formatResponse, formatBroadcast, formatWelcome, formatError } = require("./protocol");

// ── Configuration ──────────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  port: 9100,
  table: {
    tableId: "table-1",
    tableName: "Poker Lab",
    maxSeats: 6,
    sb: 5,
    bb: 10,
    minBuyIn: 400,
    maxBuyIn: 1000,
  },
};

function startServer(userConfig = {}) {
  const config = { ...DEFAULT_CONFIG, ...userConfig };
  const port = config.port;
  const tableConfig = config.table;

  // Session setup
  const logPath = config.logPath || path.join(process.cwd(), "session-events.jsonl");
  const session = new Session(tableConfig, {
    sessionId: config.sessionId || `ws-${Date.now()}`,
    logPath,
  });

  // WebSocket server
  const wss = new WebSocketServer({ port });
  const clients = new Set();

  console.log(`Poker Lab WS server listening on ws://localhost:${port}`);
  console.log(`Table: ${tableConfig.tableName} (${tableConfig.sb}/${tableConfig.bb})`);
  console.log(`Event log: ${logPath}`);
  console.log();

  wss.on("connection", (ws) => {
    clients.add(ws);
    console.log(`Client connected (${clients.size} total)`);

    // Send welcome with current state
    const state = session.getState();
    const eventCount = session.getEventLog().length;
    ws.send(formatWelcome(session.sessionId, state, eventCount));

    ws.on("message", (raw) => {
      const parsed = parseClientMessage(raw.toString());

      if (!parsed.valid) {
        ws.send(formatError(null, parsed.error));
        return;
      }

      const { id, cmd, payload } = parsed;

      // Map wire command to internal CMD
      const cmdMap = {
        CREATE_TABLE: CMD.CREATE_TABLE,
        SEAT_PLAYER: CMD.SEAT_PLAYER,
        LEAVE_TABLE: CMD.LEAVE_TABLE,
        START_HAND: CMD.START_HAND,
        PLAYER_ACTION: CMD.PLAYER_ACTION,
        GET_STATE: CMD.GET_STATE,
        GET_EVENT_LOG: CMD.GET_EVENT_LOG,
      };

      const internalCmd = cmdMap[cmd];
      if (!internalCmd) {
        ws.send(formatError(id, `Unknown command: ${cmd}`));
        return;
      }

      // Dispatch
      const result = session.dispatch(command(internalCmd, payload));

      // Send response to sender
      ws.send(formatResponse(id, result));

      // Broadcast events to all OTHER clients (if any events produced)
      if (result.ok && result.events.length > 0) {
        const broadcast = formatBroadcast(result.events);
        for (const client of clients) {
          if (client !== ws && client.readyState === 1) {
            client.send(broadcast);
          }
        }
      }

      // Log
      if (result.ok && result.events.length > 0) {
        const types = result.events.map((e) => e.type).join(", ");
        console.log(`[${cmd}] seat=${payload.seat ?? "-"} → ${types}`);
      } else if (!result.ok) {
        console.log(`[${cmd}] ERROR: ${result.error}`);
      }
    });

    ws.on("close", () => {
      clients.delete(ws);
      console.log(`Client disconnected (${clients.size} remaining)`);
    });

    ws.on("error", (err) => {
      console.error("WS error:", err.message);
      clients.delete(ws);
    });
  });

  // Return server handle for testing
  return {
    wss,
    session,
    close() {
      wss.close();
    },
  };
}

// ── Run as standalone ──────────────────────────────────────────────────────

if (require.main === module) {
  const config = {};
  // Parse --port from args
  const portArg = process.argv.find((a) => a.startsWith("--port="));
  if (portArg) config.port = parseInt(portArg.split("=")[1]);

  startServer(config);
}

module.exports = { startServer };
