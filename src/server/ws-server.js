#!/usr/bin/env node
"use strict";

const http = require("http");
const fs = require("fs");
const { WebSocketServer } = require("ws");
const path = require("path");
const { Session } = require("../api/session");
const { SessionStorage } = require("../api/storage");
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
  const dataDir = config.dataDir || path.join(process.cwd(), "data", "sessions");

  const storage = new SessionStorage(dataDir);
  let session;

  // ── Recovery or fresh start ──────────────────────────────────────────
  if (config.session) {
    // Directly provided session (for tests)
    session = config.session;
  } else {
    const active = storage.findActive();
    if (active) {
      // Recover from disk
      console.log(`Recovering session ${active.sessionId}...`);
      session = Session.load(active.meta.config, active.sessionId, active.eventsPath, {
        status: active.meta.status,
      });
      const eventCount = session.getEventLog().length;
      console.log(`Recovered: ${eventCount} events, ${session.getState().handsPlayed} hands`);
    } else {
      // Fresh session
      const sessionId = config.sessionId || `session-${Date.now()}`;
      const info = storage.create(sessionId, tableConfig);
      session = new Session(tableConfig, {
        sessionId,
        logPath: info.eventsPath,
      });
      console.log(`Created new session ${sessionId}`);
    }
  }

  // ── HTTP server for static files ─────────────────────────────────────
  const clientDir = path.join(__dirname, "..", "..", "client");
  const MIME = { ".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".png": "image/png" };

  const httpServer = http.createServer((req, res) => {
    const url = req.url === "/" ? "/index.html" : req.url;
    const filePath = path.join(clientDir, url);
    const ext = path.extname(filePath);

    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(404, { "Content-Type": "text/plain" });
        res.end("Not found");
        return;
      }
      res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
      res.end(data);
    });
  });

  httpServer.listen(port);

  // ── WebSocket server ─────────────────────────────────────────────────
  const wss = new WebSocketServer({ server: httpServer });
  const clients = new Set();

  console.log(`Poker Lab server listening on http://localhost:${port}`);
  const st = session.getState();
  console.log(`Table: ${st.tableName} (${st.sb}/${st.bb}) | Hands: ${st.handsPlayed}`);
  console.log();

  wss.on("connection", (ws) => {
    clients.add(ws);
    console.log(`Client connected (${clients.size} total)`);

    const state = session.getState();
    const eventCount = session.getEventLog().length;
    ws.send(formatWelcome(session.sessionId, state, eventCount));

    ws.on("message", (raw) => {
      const parsed = parseClientMessage(raw.toString());

      if (!parsed.valid) {
        ws.send(formatError(null, parsed.error));
        return;
      }

      const { id, cmd: cmdName, payload } = parsed;

      // ── Server-level commands (not routed to session) ──────────────
      if (cmdName === "GET_SESSION_LIST") {
        const list = storage.list();
        ws.send(formatResponse(id, { ok: true, events: [], state: { sessions: list }, error: null }));
        return;
      }

      if (cmdName === "ARCHIVE_SESSION") {
        const handsPlayed = session.getState().handsPlayed;
        storage.archive(session.sessionId, handsPlayed);
        session.status = "complete";

        // Create a new active session
        const newId = `session-${Date.now()}`;
        const info = storage.create(newId, tableConfig);
        session = new Session(tableConfig, { sessionId: newId, logPath: info.eventsPath });
        console.log(`Archived old session. New session: ${newId}`);

        ws.send(formatResponse(id, { ok: true, events: [], state: null, error: null }));
        // All clients get welcome with new session state
        const newState = session.getState();
        const newCount = session.getEventLog().length;
        const welcomeMsg = formatWelcome(session.sessionId, newState, newCount);
        for (const c of clients) {
          if (c.readyState === 1) c.send(welcomeMsg);
        }
        return;
      }

      // ── Session-level commands ─────────────────────────────────────
      const cmdMap = {
        CREATE_TABLE: CMD.CREATE_TABLE,
        SEAT_PLAYER: CMD.SEAT_PLAYER,
        LEAVE_TABLE: CMD.LEAVE_TABLE,
        START_HAND: CMD.START_HAND,
        PLAYER_ACTION: CMD.PLAYER_ACTION,
        GET_STATE: CMD.GET_STATE,
        GET_EVENT_LOG: CMD.GET_EVENT_LOG,
        GET_HAND_EVENTS: CMD.GET_HAND_EVENTS,
        GET_HAND_LIST: CMD.GET_HAND_LIST,
      };

      const internalCmd = cmdMap[cmdName];
      if (!internalCmd) {
        ws.send(formatError(id, `Unknown command: ${cmdName}`));
        return;
      }

      const result = session.dispatch(command(internalCmd, payload));
      ws.send(formatResponse(id, result));

      if (result.ok && result.events.length > 0) {
        const broadcast = formatBroadcast(result.events);
        for (const client of clients) {
          if (client !== ws && client.readyState === 1) {
            client.send(broadcast);
          }
        }

        // Update meta periodically
        const handsPlayed = session.getState().handsPlayed;
        if (handsPlayed > 0 && handsPlayed % 5 === 0) {
          storage.updateMeta(session.sessionId, {
            handsPlayed,
            lastEventAt: new Date().toISOString(),
          });
        }
      }

      // Log
      if (result.ok && result.events.length > 0) {
        const types = result.events.map((e) => e.type).join(", ");
        console.log(`[${cmdName}] seat=${payload.seat ?? "-"} → ${types}`);
      } else if (!result.ok) {
        console.log(`[${cmdName}] ERROR: ${result.error}`);
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

  return {
    wss, httpServer, session, storage,
    close() {
      // Update meta on shutdown
      try {
        storage.updateMeta(session.sessionId, {
          handsPlayed: session.getState().handsPlayed,
          lastEventAt: new Date().toISOString(),
        });
      } catch {}
      wss.close();
      httpServer.close();
    },
  };
}

// ── Run as standalone ──────────────────────────────────────────────────────

if (require.main === module) {
  const config = {};
  const portArg = process.argv.find((a) => a.startsWith("--port="));
  if (portArg) config.port = parseInt(portArg.split("=")[1]);

  startServer(config);
}

module.exports = { startServer };
