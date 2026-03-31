"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

/**
 * Actor Registry — persistent identity for study participants.
 *
 * Storage: data/actors/{actorId}.json
 * MVP: JSON files on disk, one per actor. Abstraction boundary
 * allows swapping to SQLite later without API changes.
 */
class ActorRegistry {
  constructor(dataDir) {
    this.dataDir = dataDir;
    fs.mkdirSync(this.dataDir, { recursive: true });
  }

  /**
   * Create a new actor. Returns the Actor object.
   */
  create(name, notes) {
    const actorId = "act-" + crypto.randomUUID().slice(0, 12);
    const actor = {
      actorId,
      name: ActorRegistry.normalizeName(name) || "Unknown",
      createdAt: new Date().toISOString(),
      notes: notes || "",
    };
    this._write(actorId, actor);
    return actor;
  }

  /**
   * Get actor by ID. Returns Actor or null.
   */
  get(actorId) {
    const filePath = this._path(actorId);
    if (!fs.existsSync(filePath)) return null;
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  }

  /**
   * List all actors. Returns Actor[].
   */
  list() {
    if (!fs.existsSync(this.dataDir)) return [];
    const files = fs.readdirSync(this.dataDir).filter((f) => f.endsWith(".json"));
    return files.map((f) => {
      return JSON.parse(fs.readFileSync(path.join(this.dataDir, f), "utf8"));
    });
  }

  /**
   * Update actor fields. Returns updated Actor or null if not found.
   */
  update(actorId, fields) {
    const actor = this.get(actorId);
    if (!actor) return null;
    if (fields.name !== undefined) actor.name = ActorRegistry.normalizeName(fields.name);
    if (fields.notes !== undefined) actor.notes = fields.notes;
    this._write(actorId, actor);
    return actor;
  }

  /**
   * Find actors by normalized name match. Returns Actor[] (name is not unique).
   *
   * Name normalization: trim whitespace, collapse internal runs to single space.
   * Comparison is case-sensitive. "Alice" and "alice" are different actors.
   * This is intentional — the operator controls naming, and case may carry meaning
   * (e.g., "Alice" vs "ALICE" for different study conditions).
   */
  findByName(name) {
    const normalized = ActorRegistry.normalizeName(name);
    return this.list().filter((a) => ActorRegistry.normalizeName(a.name) === normalized);
  }

  /**
   * Normalize a name: trim, collapse internal whitespace.
   * Case is preserved (comparison is case-sensitive).
   */
  static normalizeName(name) {
    return (name || "").trim().replace(/\s+/g, " ");
  }

  /**
   * Resolve an actorId for a SEAT_PLAYER command.
   *
   * Rules:
   *   - If actorId provided and valid → return it
   *   - If actorId omitted: find by name
   *     - Exactly 1 match → return that actorId
   *     - 0 matches → create new actor, return new actorId
   *     - 2+ matches → ambiguous, create new actor (don't guess)
   *
   * @returns {{ actorId: string, created: boolean }}
   */
  resolve(name, actorId) {
    // Explicit actorId provided
    if (actorId) {
      const existing = this.get(actorId);
      if (existing) return { actorId, created: false };
      // actorId doesn't exist — create with that ID? No, that's suspicious.
      // Treat as "create new" to avoid dangling references.
    }

    // Name-based resolution
    const matches = this.findByName(name);
    if (matches.length === 1) {
      return { actorId: matches[0].actorId, created: false };
    }

    // 0 matches or 2+ matches (ambiguous) → create new
    const actor = this.create(name);
    return { actorId: actor.actorId, created: true };
  }

  _path(actorId) {
    return path.join(this.dataDir, actorId + ".json");
  }

  _write(actorId, actor) {
    fs.writeFileSync(this._path(actorId), JSON.stringify(actor, null, 2));
  }
}

module.exports = { ActorRegistry };
