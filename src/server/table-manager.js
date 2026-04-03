"use strict";

/**
 * Multi-table manager.
 *
 * Creates and manages multiple independent poker table instances.
 * Each table has its own Session, client set, bot detector, and auto-deal timer.
 *
 * Used by ws-server.js for multi-table support:
 *   ws://localhost:9100?table=1   → Table 1
 *   ws://localhost:9100?table=2   → Table 2
 *   ws://localhost:9100            → Table 1 (default)
 */

const { Session } = require("../api/session");
const { CMD, command } = require("../api/commands");
const { BotDetector } = require("../engine/bot-detector");
const { formatBroadcast } = require("./protocol");

class Table {
  constructor(id, session, storage) {
    this.id = id;
    this.session = session;
    this.storage = storage;
    this.clients = new Set();
    this.botDetector = new BotDetector();
    this.actionPromptTimes = {};
    this.autoDealTimer = null;
  }

  addClient(ws) {
    this.clients.add(ws);
  }

  removeClient(ws) {
    this.clients.delete(ws);
  }

  broadcast(events, exclude = null) {
    const msg = formatBroadcast(events);
    for (const client of this.clients) {
      if (client !== exclude && client.readyState === 1) {
        client.send(msg);
      }
    }
  }

  broadcastAll(events) {
    const msg = formatBroadcast(events);
    for (const client of this.clients) {
      if (client.readyState === 1) client.send(msg);
    }
  }

  getState() {
    return this.session.getState();
  }

  dispatch(cmd, payload) {
    return this.session.dispatch(command(cmd, payload));
  }

  scheduleAutoDeal(delayMs = 3000) {
    if (this.autoDealTimer) clearTimeout(this.autoDealTimer);
    this.autoDealTimer = setTimeout(() => {
      try {
        const state = this.getState();
        const occupied = Object.values(state.seats).filter(s => s.status === "OCCUPIED").length;
        if (occupied >= 2 && (!state.hand || state.hand.phase === "COMPLETE")) {
          const result = this.dispatch(CMD.START_HAND, {});
          if (result.ok && result.events.length > 0) {
            this.broadcastAll(result.events);
            const newState = this.getState();
            if (newState.hand && newState.hand.actionSeat != null) {
              this.actionPromptTimes[newState.hand.actionSeat] = Date.now();
            }
          }
        }
      } catch (e) { /* auto-deal failure is benign */ }
    }, delayMs);
  }

  destroy() {
    if (this.autoDealTimer) clearTimeout(this.autoDealTimer);
    for (const client of this.clients) {
      try { client.close(); } catch (e) {}
    }
    this.clients.clear();
  }
}

class TableManager {
  constructor(storage, actors, baseTableConfig) {
    this.storage = storage;
    this.actors = actors;
    this.baseTableConfig = baseTableConfig;
    this.tables = new Map(); // tableId -> Table
  }

  /**
   * Get or create a table by ID.
   * Table 1 uses recovery (existing session). Others are fresh.
   */
  getOrCreate(tableId) {
    if (this.tables.has(tableId)) return this.tables.get(tableId);

    const tableConfig = {
      ...this.baseTableConfig,
      tableId: `table-${tableId}`,
      tableName: `Poker Lab ${tableId}`,
    };

    const sessionId = `session-table${tableId}-${Date.now()}`;
    const info = this.storage.create(sessionId, tableConfig);
    const session = new Session(tableConfig, {
      sessionId,
      logPath: info.eventsPath,
      actors: this.actors,
    });

    const table = new Table(tableId, session, this.storage);
    this.tables.set(tableId, table);
    console.log(`[TableManager] Created table ${tableId}: ${tableConfig.tableName} (${tableConfig.sb}/${tableConfig.bb})`);
    return table;
  }

  get(tableId) {
    return this.tables.get(tableId);
  }

  list() {
    return Array.from(this.tables.entries()).map(([id, t]) => ({
      id,
      name: t.getState().tableName,
      seats: Object.values(t.getState().seats).filter(s => s.status === "OCCUPIED").length,
      hands: t.getState().handsPlayed,
      clients: t.clients.size,
    }));
  }

  destroyAll() {
    for (const table of this.tables.values()) {
      table.destroy();
    }
    this.tables.clear();
  }
}

module.exports = { TableManager, Table };
