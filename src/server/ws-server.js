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
const { BotDetector } = require("../detection/analyzer");

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
  let wasRecovered = false;
  let voidedHands = [];

  // ── Recovery or fresh start ──────────────────────────────────────────
  if (config.session) {
    session = config.session;
  } else {
    const active = storage.findActive();
    if (active) {
      console.log(`Recovering session ${active.sessionId}...`);
      session = Session.load(active.meta.config, active.sessionId, active.eventsPath, {
        status: active.meta.status,
      });
      wasRecovered = true;
      // Find voided hands in the event log
      voidedHands = session.getEventLog()
        .filter((e) => e.type === "HAND_END" && e.void === true)
        .map((e) => e.handId);
      const eventCount = session.getEventLog().length;
      console.log(`Recovered: ${eventCount} events, ${session.getState().handsPlayed} hands`);
      if (voidedHands.length > 0) console.log(`Voided hands: ${voidedHands.join(", ")}`);
    } else {
      const sessionId = config.sessionId || `session-${Date.now()}`;
      const info = storage.create(sessionId, tableConfig);
      session = new Session(tableConfig, { sessionId, logPath: info.eventsPath });
      console.log(`Created new session ${sessionId}`);
    }
  }

  // ── HTTP server ──────────────────────────────────────────────────────
  const clientDir = path.join(__dirname, "..", "..", "client");
  const MIME = { ".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".png": "image/png" };

  const httpServer = http.createServer((req, res) => {
    const url = req.url === "/" ? "/index.html" : req.url;
    const filePath = path.join(clientDir, url);
    const ext = path.extname(filePath);
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(404); res.end("Not found"); return; }
      res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
      res.end(data);
    });
  });

  httpServer.listen(port);

  // ── Bot detection ────────────────────────────────────────────────────
  const detector = new BotDetector({
    onWarn: (seat, score, signals) => {
      const top = Object.entries(signals)
        .filter(([_, v]) => v > 0.3)
        .map(([k, v]) => `${k}=${v.toFixed(2)}`)
        .join(", ");
      console.log(`\x1b[33m[BOT-DETECT] WARN seat ${seat}: score=${score.toFixed(2)} (${top})\x1b[0m`);
    },
    onFlag: (seat, score, signals) => {
      const top = Object.entries(signals)
        .filter(([_, v]) => v > 0.3)
        .map(([k, v]) => `${k}=${v.toFixed(2)}`)
        .join(", ");
      console.log(`\x1b[31m[BOT-DETECT] FLAG seat ${seat}: score=${score.toFixed(2)} (${top})\x1b[0m`);
    },
  });

  // ── WebSocket server ─────────────────────────────────────────────────
  const wss = new WebSocketServer({ server: httpServer });
  const clients = new Set();

  function makeRecoveryInfo() {
    return { recovered: wasRecovered, voidedHands };
  }

  function sendWelcome(ws) {
    const state = session.getState();
    const eventCount = session.getEventLog().length;
    ws.send(formatWelcome(session.sessionId, state, eventCount, makeRecoveryInfo()));
  }

  console.log(`Poker Lab server listening on http://localhost:${port}`);
  const st = session.getState();
  console.log(`Table: ${st.tableName} (${st.sb}/${st.bb}) | Hands: ${st.handsPlayed}`);
  console.log();

  wss.on("connection", (ws) => {
    clients.add(ws);
    console.log(`Client connected (${clients.size} total)`);
    sendWelcome(ws);

    ws.on("message", (raw) => {
      const parsed = parseClientMessage(raw.toString());
      if (!parsed.valid) { ws.send(formatError(null, parsed.error)); return; }

      const { id, cmd: cmdName, payload } = parsed;

      // ── Server-level commands ────────────────────────────────────────
      if (cmdName === "GET_BOT_DETECTION") {
        const profiles = detector.getAllProfiles();
        ws.send(formatResponse(id, { ok: true, events: [], state: { botDetection: profiles }, error: null }));
        return;
      }

      if (cmdName === "GET_SESSION_LIST") {
        const list = storage.list();
        ws.send(formatResponse(id, { ok: true, events: [], state: { sessions: list }, error: null }));
        return;
      }

      if (cmdName === "ARCHIVE_SESSION") {
        const handsPlayed = session.getState().handsPlayed;
        storage.archive(session.sessionId, handsPlayed);
        session.status = "complete";

        const newId = `session-${Date.now()}`;
        const info = storage.create(newId, tableConfig);
        session = new Session(tableConfig, { sessionId: newId, logPath: info.eventsPath });
        wasRecovered = false;
        voidedHands = [];
        console.log(`Archived old session. New session: ${newId}`);

        ws.send(formatResponse(id, { ok: true, events: [], state: null, error: null }));
        for (const c of clients) {
          if (c.readyState === 1) sendWelcome(c);
        }
        return;
      }

      // ── Archived session hand browsing ───────────────────────────────
      if (cmdName === "GET_HAND_LIST" && payload.sessionId && payload.sessionId !== session.sessionId) {
        const info = storage.load(payload.sessionId);
        if (!info) {
          ws.send(formatError(id, `Session not found: ${payload.sessionId}`));
          return;
        }
        // Read events from disk, scan for HAND_SUMMARY + voided HAND_END
        try {
          const content = fs.readFileSync(info.eventsPath, "utf8").trim();
          const events = content ? content.split("\n").filter(Boolean).map(JSON.parse) : [];
          const hands = [];
          const voids = new Set(events.filter((e) => e.type === "HAND_END" && e.void).map((e) => e.handId));
          for (const e of events) {
            if (e.type === "HAND_SUMMARY") {
              hands.push({
                handId: e.handId, winner: e.winPlayer, pot: e.totalPot,
                showdown: e.showdown, voided: false,
              });
            }
          }
          // Add voided hands that had no HAND_SUMMARY
          for (const vid of voids) {
            if (!hands.find((h) => h.handId === vid)) {
              hands.push({ handId: vid, winner: null, pot: 0, showdown: false, voided: true });
            }
          }
          // Mark hands that were voided
          for (const h of hands) {
            if (voids.has(h.handId)) h.voided = true;
          }
          ws.send(formatResponse(id, { ok: true, events: [], state: { hands }, error: null }));
        } catch (e) {
          ws.send(formatError(id, `Error reading session: ${e.message}`));
        }
        return;
      }

      if (cmdName === "GET_HAND_EVENTS" && payload.sessionId && payload.sessionId !== session.sessionId) {
        const info = storage.load(payload.sessionId);
        if (!info) {
          ws.send(formatError(id, `Session not found: ${payload.sessionId}`));
          return;
        }
        try {
          const content = fs.readFileSync(info.eventsPath, "utf8").trim();
          const events = content ? content.split("\n").filter(Boolean).map(JSON.parse) : [];
          const handEvents = events.filter((e) => e.handId === String(payload.handId));
          ws.send(formatResponse(id, { ok: true, events: handEvents, state: null, error: null }));
        } catch (e) {
          ws.send(formatError(id, `Error reading session: ${e.message}`));
        }
        return;
      }

      // ── Session-level commands ───────────────────────────────────────
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
      if (!internalCmd) { ws.send(formatError(id, `Unknown command: ${cmdName}`)); return; }

      // Extract and strip telemetry before dispatching to engine
      let telemetry = null;
      if (cmdName === "PLAYER_ACTION" && payload._telemetry) {
        telemetry = payload._telemetry;
        delete payload._telemetry;
      }

      const result = session.dispatch(command(internalCmd, payload));
      ws.send(formatResponse(id, result));

      // Run bot detection on player actions
      if (cmdName === "PLAYER_ACTION" && result.ok && payload.seat != null) {
        detector.analyze(payload.seat, telemetry);
      }

      if (result.ok && result.events.length > 0) {
        const broadcast = formatBroadcast(result.events);
        for (const client of clients) {
          if (client !== ws && client.readyState === 1) client.send(broadcast);
        }
        const handsPlayed = session.getState().handsPlayed;
        if (handsPlayed > 0 && handsPlayed % 5 === 0) {
          storage.updateMeta(session.sessionId, { handsPlayed, lastEventAt: new Date().toISOString() });
        }
      }

      // Reset detection profile when player leaves
      if (cmdName === "LEAVE_TABLE" && result.ok && payload.seat != null) {
        detector.resetSeat(payload.seat);
      }

      if (result.ok && result.events.length > 0) {
        console.log(`[${cmdName}] seat=${payload.seat ?? "-"} → ${result.events.map((e) => e.type).join(", ")}`);
      } else if (!result.ok) {
        console.log(`[${cmdName}] ERROR: ${result.error}`);
      }
    });

    ws.on("close", () => { clients.delete(ws); console.log(`Client disconnected (${clients.size} remaining)`); });
    ws.on("error", (err) => { console.error("WS error:", err.message); clients.delete(ws); });
  });

  return {
    wss, httpServer, session, storage, detector,
    get wasRecovered() { return wasRecovered; },
    get voidedHands() { return voidedHands; },
    close() {
      try { storage.updateMeta(session.sessionId, { handsPlayed: session.getState().handsPlayed, lastEventAt: new Date().toISOString() }); } catch {}
      wss.close(); httpServer.close();
    },
  };
}

if (require.main === module) {
  const config = {};
  const portArg = process.argv.find((a) => a.startsWith("--port="));
  if (portArg) config.port = parseInt(portArg.split("=")[1]);
  startServer(config);
}

module.exports = { startServer };
