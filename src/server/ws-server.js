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
const { ActorRegistry } = require("../api/actors");
const { queryHands, getActorStats } = require("../api/query");
const { AnnotationStore } = require("../api/annotations");
const { BotDetector } = require("../engine/bot-detector");
const { TableManager } = require("./table-manager");
const { Auth } = require("./auth");

const DEFAULT_CONFIG = {
  port: 9100,
  table: {
    tableId: "table-1",
    tableName: "Poker Lab",
    maxSeats: 6,
    sb: 5,
    bb: 10,
    minBuyIn: 100,
    maxBuyIn: 50000,
  },
};

function startServer(userConfig = {}) {
  const config = { ...DEFAULT_CONFIG, ...userConfig };
  const port = config.port;
  const tableConfig = config.table;
  const dataDir = config.dataDir || path.join(process.cwd(), "data", "sessions");

  const storage = new SessionStorage(dataDir);
  const actorsDir = config.actorsDir || path.join(path.dirname(dataDir), "actors");
  const actors = new ActorRegistry(actorsDir);
  const annotations = new AnnotationStore(storage);
  const botDetector = new BotDetector();
  const actionPromptTimes = {}; // seat -> timestamp when action was requested
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
        status: active.meta.status, actors,
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
      session = new Session(tableConfig, { sessionId, logPath: info.eventsPath, actors });
      console.log(`Created new session ${sessionId}`);
    }
  }

  // ── HTTP server ──────────────────────────────────────────────────────
  const clientDir = path.join(__dirname, "..", "..", "client");
  const MIME = { ".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml" };

  const httpServer = http.createServer((req, res) => {
    // Strip query string for file serving (e.g., /?table=2 -> /index.html)
    const urlPath = req.url.split("?")[0];
    const url = urlPath === "/" ? "/index.html" : urlPath;
    const filePath = path.join(clientDir, url);
    const ext = path.extname(filePath);
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(404); res.end("Not found"); return; }
      res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
      res.end(data);
    });
  });

  // ── Authentication ───────────────────────────────────────────────────
  const auth = new Auth(config.keysPath);
  if (auth.isEnabled()) {
    console.log(`Auth enabled: ${auth.keys.size} API key(s) loaded`);
  } else {
    console.log("Auth disabled (no api-keys.json) — local dev mode");
  }

  // ── Multi-table manager ────────────────────────────────────────────────
  const tableManager = new TableManager(storage, actors, tableConfig);
  // Register the primary table (table 1) with the recovered/new session
  const primaryTable = tableManager.getOrCreate("1");
  // Replace its session with the recovered one
  primaryTable.session = session;

  httpServer.listen(port);

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

  wss.on("connection", (ws, req) => {
    // Parse query string: ws://host:port?table=2&key=pk_live_...
    const url = new URL(req.url, `http://localhost:${port}`);
    const tableId = url.searchParams.get("table") || "1";
    const apiKey = url.searchParams.get("key");

    // ── Auth check (bypass for localhost) ──────────────────────────────
    const isLocal = Auth.isLocalConnection(req);
    let role = "admin";
    let authName = "local";

    if (!isLocal) {
      const authResult = auth.validate(apiKey);
      if (!authResult.valid) {
        console.log(`Auth rejected: ${req.socket.remoteAddress} (invalid key)`);
        ws.send(JSON.stringify({ error: "Invalid API key", code: "AUTH_FAILED" }));
        ws.close(4001, "Unauthorized");
        return;
      }
      role = authResult.role;
      authName = authResult.name;
    }

    ws._role = role;
    ws._authName = authName;
    clients.add(ws);

    const table = tableManager.getOrCreate(tableId);
    table.addClient(ws);
    ws._tableId = tableId; // stash for cleanup

    const source = isLocal ? "local" : `${authName} (${role})`;
    console.log(`Client connected to table ${tableId} — ${source} (${clients.size} total, ${table.clients.size} on table)`);
    sendWelcome(ws);

    ws.on("message", (raw) => {
      const parsed = parseClientMessage(raw.toString());
      if (!parsed.valid) { ws.send(formatError(null, parsed.error)); return; }

      const { id, cmd: cmdName, payload } = parsed;

      // ── Permission check ────────────────────────────────────────────
      if (!auth.canExecute(ws._role, cmdName)) {
        ws.send(formatError(id, `Permission denied: ${cmdName} requires higher role (current: ${ws._role})`));
        return;
      }

      // ── Multi-table commands ──────────────────────────────────────────
      if (cmdName === "LIST_TABLES") {
        ws.send(formatResponse(id, { ok: true, events: [], state: { tables: tableManager.list() }, error: null }));
        return;
      }

      // ── Server-level commands ────────────────────────────────────────
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
        session = new Session(tableConfig, { sessionId: newId, logPath: info.eventsPath, actors });
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

      // ── Actor + Query commands (server-level, cross-session) ──────────
      if (cmdName === "CREATE_ACTOR") {
        if (!payload.name) { ws.send(formatError(id, "CREATE_ACTOR requires name")); return; }
        const actor = actors.create(payload.name, payload.notes);
        ws.send(formatResponse(id, { ok: true, events: [], state: { actor }, error: null }));
        return;
      }
      if (cmdName === "GET_ACTOR") {
        if (!payload.actorId) { ws.send(formatError(id, "GET_ACTOR requires actorId")); return; }
        const actor = actors.get(payload.actorId);
        if (!actor) { ws.send(formatError(id, `Actor not found: ${payload.actorId}`)); return; }
        ws.send(formatResponse(id, { ok: true, events: [], state: { actor }, error: null }));
        return;
      }
      if (cmdName === "LIST_ACTORS") {
        ws.send(formatResponse(id, { ok: true, events: [], state: { actors: actors.list() }, error: null }));
        return;
      }
      if (cmdName === "UPDATE_ACTOR") {
        if (!payload.actorId) { ws.send(formatError(id, "UPDATE_ACTOR requires actorId")); return; }
        const actor = actors.update(payload.actorId, { name: payload.name, notes: payload.notes });
        if (!actor) { ws.send(formatError(id, `Actor not found: ${payload.actorId}`)); return; }
        ws.send(formatResponse(id, { ok: true, events: [], state: { actor }, error: null }));
        return;
      }
      if (cmdName === "QUERY_HANDS") {
        const results = queryHands(storage, payload || {});
        ws.send(formatResponse(id, { ok: true, events: [], state: { hands: results }, error: null }));
        return;
      }
      if (cmdName === "GET_ACTOR_STATS") {
        if (!payload.actorId) { ws.send(formatError(id, "GET_ACTOR_STATS requires actorId")); return; }
        const stats = getActorStats(storage, payload.actorId, payload.sessionId);
        ws.send(formatResponse(id, { ok: true, events: [], state: { stats }, error: null }));
        return;
      }
      if (cmdName === "ADD_ANNOTATION") {
        if (!payload.sessionId || !payload.handId) { ws.send(formatError(id, "ADD_ANNOTATION requires sessionId, handId")); return; }
        try {
          const ann = annotations.add(payload.sessionId, payload.handId, payload);
          ws.send(formatResponse(id, { ok: true, events: [], state: { annotation: ann }, error: null }));
        } catch (e) { ws.send(formatError(id, e.message)); }
        return;
      }
      if (cmdName === "GET_ANNOTATIONS") {
        if (!payload.sessionId || !payload.handId) { ws.send(formatError(id, "GET_ANNOTATIONS requires sessionId, handId")); return; }
        const anns = annotations.getForHand(payload.sessionId, payload.handId);
        ws.send(formatResponse(id, { ok: true, events: [], state: { annotations: anns }, error: null }));
        return;
      }
      if (cmdName === "GET_ANNOTATION_COUNTS") {
        if (!payload.sessionId) { ws.send(formatError(id, "GET_ANNOTATION_COUNTS requires sessionId")); return; }
        const counts = annotations.getCountsByHand(payload.sessionId);
        ws.send(formatResponse(id, { ok: true, events: [], state: { counts }, error: null }));
        return;
      }
      if (cmdName === "DELETE_ANNOTATION") {
        if (!payload.sessionId || !payload.annotationId) { ws.send(formatError(id, "DELETE_ANNOTATION requires sessionId, annotationId")); return; }
        annotations.delete(payload.sessionId, payload.annotationId);
        ws.send(formatResponse(id, { ok: true, events: [], state: null, error: null }));
        return;
      }

      // ── Bot detection commands ────────────────────────────────────────
      if (cmdName === "GET_BOT_SCORES") {
        const scores = payload.player
          ? [{ player: payload.player, ...botDetector.getScore(payload.player) }]
          : botDetector.getSummary();
        ws.send(formatResponse(id, { ok: true, events: [], state: { botScores: scores }, error: null }));
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

      // ── Bot detection: record response time for PLAYER_ACTION ──────
      let botResponseTimeMs = null;
      if (cmdName === "PLAYER_ACTION" && payload.seat != null && actionPromptTimes[payload.seat]) {
        botResponseTimeMs = Date.now() - actionPromptTimes[payload.seat];
        delete actionPromptTimes[payload.seat];
      }

      const result = session.dispatch(command(internalCmd, payload));
      ws.send(formatResponse(id, result));

      // ── Bot detection: feed action data ──────────────────────────────
      if (cmdName === "PLAYER_ACTION" && result.ok && botResponseTimeMs !== null) {
        const actionEvent = result.events.find((e) => e.type === "PLAYER_ACTION");
        if (actionEvent) {
          const state = session.getState();
          const potSize = state.hand ? state.hand.pot : 0;
          botDetector.recordAction(
            actionEvent.player,
            actionEvent.action,
            actionEvent.delta || 0,
            botResponseTimeMs,
            payload.handStrength || null,
            potSize,
            actionEvent.street
          );
        }
      }

      // ── Bot detection: record when next player needs to act ──────────
      if (result.ok && result.events.length > 0) {
        const state = session.getState();
        if (state.hand && state.hand.actionSeat != null) {
          actionPromptTimes[state.hand.actionSeat] = Date.now();
        }
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

        // ── Server-side auto-deal (like PokerStars) ────────────────────
        // If hand just ended, auto-deal next hand after 3 seconds
        const hasHandEnd = result.events.some((e) => e.type === "HAND_END");
        if (hasHandEnd) {
          setTimeout(() => {
            try {
              const state = session.getState();
              const occupied = Object.values(state.seats).filter((s) => s.status === "OCCUPIED").length;
              if (occupied >= 2 && (!state.hand || state.hand.phase === "COMPLETE")) {
                const dealResult = session.dispatch(command(CMD.START_HAND, {}));
                if (dealResult.ok && dealResult.events.length > 0) {
                  const dealBroadcast = formatBroadcast(dealResult.events);
                  for (const client of clients) {
                    if (client.readyState === 1) client.send(dealBroadcast);
                  }
                  // Record action prompt time for bot detection
                  const newState = session.getState();
                  if (newState.hand && newState.hand.actionSeat != null) {
                    actionPromptTimes[newState.hand.actionSeat] = Date.now();
                  }
                }
              }
            } catch (e) { /* auto-deal failure is benign */ }
          }, 3000);
        }
      }

      if (result.ok && result.events.length > 0) {
        console.log(`[${cmdName}] seat=${payload.seat ?? "-"} → ${result.events.map((e) => e.type).join(", ")}`);
      } else if (!result.ok) {
        console.log(`[${cmdName}] ERROR: ${result.error}`);
      }
    });

    ws.on("close", () => {
      clients.delete(ws);
      const t = tableManager.get(ws._tableId);
      if (t) t.removeClient(ws);
      console.log(`Client disconnected from table ${ws._tableId} (${clients.size} remaining)`);
    });
    ws.on("error", (err) => { console.error("WS error:", err.message); clients.delete(ws); });
  });

  return {
    wss, httpServer, session, storage, botDetector,
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
