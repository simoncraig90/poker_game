"use strict";

const fs = require("fs");
const path = require("path");

const DEFAULT_DATA_DIR = path.join(process.cwd(), "data", "sessions");

/**
 * Manage session directories on disk.
 *
 * Layout:
 *   data/sessions/{sessionId}/
 *     meta.json
 *     events.jsonl
 */
class SessionStorage {
  constructor(dataDir) {
    this.dataDir = dataDir || DEFAULT_DATA_DIR;
    fs.mkdirSync(this.dataDir, { recursive: true });
  }

  /**
   * Create a new session directory. Returns { sessionId, dir, eventsPath, metaPath }.
   */
  create(sessionId, config) {
    const dir = path.join(this.dataDir, sessionId);
    fs.mkdirSync(dir, { recursive: true });

    const meta = {
      sessionId,
      config,
      createdAt: new Date().toISOString(),
      status: "active",
      handsPlayed: 0,
      lastEventAt: null,
    };
    const metaPath = path.join(dir, "meta.json");
    fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2));

    return {
      sessionId,
      dir,
      eventsPath: path.join(dir, "events.jsonl"),
      metaPath,
      meta,
    };
  }

  /**
   * Load a session's metadata and file paths.
   */
  load(sessionId) {
    const dir = path.join(this.dataDir, sessionId);
    const metaPath = path.join(dir, "meta.json");
    const eventsPath = path.join(dir, "events.jsonl");

    if (!fs.existsSync(metaPath)) return null;

    const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
    return { sessionId, dir, eventsPath, metaPath, meta };
  }

  /**
   * List all sessions sorted by createdAt descending.
   */
  list() {
    if (!fs.existsSync(this.dataDir)) return [];

    const entries = fs.readdirSync(this.dataDir, { withFileTypes: true });
    const sessions = [];

    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const metaPath = path.join(this.dataDir, entry.name, "meta.json");
      if (!fs.existsSync(metaPath)) continue;
      try {
        const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
        sessions.push(meta);
      } catch {
        // Skip corrupt meta files
      }
    }

    return sessions.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
  }

  /**
   * Find the active session (status="active"). Returns null if none.
   */
  findActive() {
    const all = this.list();
    const active = all.find((m) => m.status === "active");
    return active ? this.load(active.sessionId) : null;
  }

  /**
   * Update a session's metadata.
   */
  updateMeta(sessionId, updates) {
    const info = this.load(sessionId);
    if (!info) return;
    const meta = { ...info.meta, ...updates };
    fs.writeFileSync(info.metaPath, JSON.stringify(meta, null, 2));
  }

  /**
   * Archive a session (set status="complete").
   */
  archive(sessionId, handsPlayed) {
    this.updateMeta(sessionId, {
      status: "complete",
      handsPlayed: handsPlayed || 0,
      lastEventAt: new Date().toISOString(),
    });
  }
}

module.exports = { SessionStorage, DEFAULT_DATA_DIR };
