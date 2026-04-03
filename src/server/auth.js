const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const DEFAULT_KEYS_PATH = path.join(__dirname, "..", "..", "data", "api-keys.json");

// Commands spectators can execute (read-only)
const SPECTATOR_COMMANDS = new Set([
  "GET_STATE", "GET_EVENT_LOG", "GET_HAND_LIST", "GET_HAND_EVENTS",
  "LIST_TABLES", "GET_SESSION_LIST", "LIST_ACTORS", "GET_ACTOR",
  "GET_ACTOR_STATS", "QUERY_HANDS",
  "GET_BOT_SCORES", "GET_ANNOTATIONS", "GET_ANNOTATION_COUNTS",
]);

// Commands only admins can execute
const ADMIN_ONLY_COMMANDS = new Set([
  "ARCHIVE_SESSION", "CREATE_ACTOR", "UPDATE_ACTOR",
  "ADD_ANNOTATION", "DELETE_ANNOTATION",
]);

class Auth {
  constructor(keysPath) {
    this.keysPath = keysPath || DEFAULT_KEYS_PATH;
    this.keys = new Map(); // key string -> {id, name, role}
    this.load();
  }

  load() {
    this.keys.clear();
    try {
      const data = JSON.parse(fs.readFileSync(this.keysPath, "utf8"));
      for (const entry of data.keys || []) {
        this.keys.set(entry.key, { id: entry.id, name: entry.name, role: entry.role });
      }
    } catch (e) {
      // No keys file = auth disabled (local dev mode)
    }
  }

  isEnabled() {
    return this.keys.size > 0;
  }

  validate(apiKey) {
    if (!this.isEnabled()) return { valid: true, role: "admin", name: "local" };
    if (!apiKey) return { valid: false };
    const entry = this.keys.get(apiKey);
    if (!entry) return { valid: false };
    return { valid: true, ...entry };
  }

  canExecute(role, cmd) {
    if (role === "admin") return true;
    if (role === "spectator") return SPECTATOR_COMMANDS.has(cmd);
    // bot: everything except admin-only
    return !ADMIN_ONLY_COMMANDS.has(cmd);
  }

  static isLocalConnection(req) {
    const addr = req.socket.remoteAddress;
    return addr === "127.0.0.1" || addr === "::1" || addr === "::ffff:127.0.0.1";
  }

  static generateKey() {
    return "pk_live_" + crypto.randomBytes(24).toString("hex");
  }

  static generateId() {
    return "k_" + crypto.randomBytes(6).toString("hex");
  }
}

module.exports = { Auth };
