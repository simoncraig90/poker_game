#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const { Auth } = require("../src/server/auth");

const KEYS_PATH = path.join(__dirname, "..", "data", "api-keys.json");

function loadKeys() {
  try {
    return JSON.parse(fs.readFileSync(KEYS_PATH, "utf8"));
  } catch (e) {
    return { keys: [] };
  }
}

function saveKeys(data) {
  fs.writeFileSync(KEYS_PATH, JSON.stringify(data, null, 2) + "\n");
}

function addKey(name, role) {
  if (!name) { console.error("Usage: manage-keys.js add --name NAME --role ROLE"); process.exit(1); }
  if (!["admin", "bot", "spectator"].includes(role)) {
    console.error("Role must be: admin, bot, or spectator"); process.exit(1);
  }
  const data = loadKeys();
  const entry = {
    id: Auth.generateId(),
    name,
    key: Auth.generateKey(),
    role,
    created: new Date().toISOString(),
  };
  data.keys.push(entry);
  saveKeys(data);
  console.log(`\nCreated ${role} key for "${name}":`);
  console.log(`  ID:   ${entry.id}`);
  console.log(`  Key:  ${entry.key}`);
  console.log(`  Role: ${entry.role}`);
  console.log(`\nSave this key — it cannot be retrieved later.\n`);
}

function listKeys() {
  const data = loadKeys();
  if (data.keys.length === 0) {
    console.log("No API keys configured. Use: manage-keys.js add --name NAME --role ROLE");
    return;
  }
  console.log(`\n${data.keys.length} API key(s):\n`);
  for (const k of data.keys) {
    const preview = k.key.slice(0, 12) + "..." + k.key.slice(-4);
    console.log(`  ${k.id}  ${k.role.padEnd(10)} ${k.name.padEnd(20)} ${preview}  (${k.created.slice(0, 10)})`);
  }
  console.log();
}

function revokeKey(id) {
  if (!id) { console.error("Usage: manage-keys.js revoke KEY_ID"); process.exit(1); }
  const data = loadKeys();
  const idx = data.keys.findIndex(k => k.id === id);
  if (idx === -1) { console.error(`Key ${id} not found`); process.exit(1); }
  const removed = data.keys.splice(idx, 1)[0];
  saveKeys(data);
  console.log(`Revoked key ${removed.id} ("${removed.name}", ${removed.role})`);
}

// Parse args
const args = process.argv.slice(2);
const command = args[0];

function getArg(flag) {
  const idx = args.indexOf(flag);
  return idx >= 0 && args[idx + 1] ? args[idx + 1] : null;
}

switch (command) {
  case "add":
    addKey(getArg("--name"), getArg("--role") || "bot");
    break;
  case "list":
    listKeys();
    break;
  case "revoke":
    revokeKey(args[1]);
    break;
  default:
    console.log("Usage:");
    console.log("  manage-keys.js add --name NAME --role ROLE   Create a new API key");
    console.log("  manage-keys.js list                          List all keys");
    console.log("  manage-keys.js revoke KEY_ID                 Revoke a key");
    console.log("\nRoles: admin, bot, spectator");
}
