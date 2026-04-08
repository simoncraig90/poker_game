#!/usr/bin/env node
"use strict";

/**
 * Export a JSON CFR strategy to binary mmap-compatible format.
 *
 * Binary format (CFR1):
 *   HEADER (32 bytes):
 *     magic:        4 bytes  "CFR1"
 *     version:      4 bytes  uint32 = 1
 *     num_entries:  4 bytes  uint32
 *     num_actions:  4 bytes  uint32
 *     bucket_count: 4 bytes  uint32
 *     reserved:     12 bytes (zero)
 *
 *   INDEX (num_entries x 12 bytes, sorted by key_hash):
 *     key_hash:     8 bytes  uint64 (FNV-1a)
 *     data_offset:  4 bytes  uint32 (byte offset into DATA)
 *
 *   DATA (num_entries x num_actions x 4 bytes):
 *     float32[num_actions] probabilities in fixed action order
 *
 * Usage:
 *   node scripts/cfr/export-binary.js vision/models/cfr_strategy_flop.json
 *   node scripts/cfr/export-binary.js --input vision/models/cfr_strategy_flop.json --output vision/models/flop.bin
 */

const fs = require("fs");
const path = require("path");

// Fixed action order — all strategies use this indexing
const ACTION_ORDER = [
  "FOLD", "CHECK", "CALL",
  "BET_33", "BET_66", "BET_POT", "BET_ALLIN",
  "RAISE_HALF", "RAISE_POT", "RAISE_ALLIN",
  // Legacy names from HU model
  "BET_HALF",
];

const NUM_ACTIONS = ACTION_ORDER.length;

// ── FNV-1a 64-bit hash ─────────────────────────────────────────────────

function fnv1a64(str) {
  // FNV-1a 64-bit implemented with BigInt
  let h = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  const mask = 0xFFFFFFFFFFFFFFFFn;
  for (let i = 0; i < str.length; i++) {
    h ^= BigInt(str.charCodeAt(i));
    h = (h * prime) & mask;
  }
  return h;
}

// ── Main ────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
let inputPath, outputPath;

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--input" && args[i + 1]) { inputPath = args[++i]; }
  else if (args[i] === "--output" && args[i + 1]) { outputPath = args[++i]; }
  else if (!inputPath) { inputPath = args[i]; }
}

if (!inputPath) {
  console.error("Usage: node export-binary.js <strategy.json> [--output <output.bin>]");
  process.exit(1);
}

if (!outputPath) {
  outputPath = inputPath.replace(/\.json$/, ".bin");
}
const idxPath = outputPath.replace(/\.bin$/, ".idx");

console.log(`Reading ${inputPath}...`);
const strategy = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const keys = Object.keys(strategy);
console.log(`  ${keys.length} entries loaded.`);

// Build index entries: [hash, key, probs]
console.log("Hashing keys...");
const entries = keys.map(key => {
  const hash = fnv1a64(key);
  const probs = new Float32Array(NUM_ACTIONS);
  const strat = strategy[key];
  for (let i = 0; i < ACTION_ORDER.length; i++) {
    probs[i] = strat[ACTION_ORDER[i]] || 0;
  }
  return { hash, key, probs };
});

// Sort by hash for binary search
entries.sort((a, b) => (a.hash < b.hash ? -1 : a.hash > b.hash ? 1 : 0));

// Check for hash collisions
let collisions = 0;
for (let i = 1; i < entries.length; i++) {
  if (entries[i].hash === entries[i - 1].hash) {
    collisions++;
    console.warn(`  COLLISION: "${entries[i - 1].key}" and "${entries[i].key}" have same hash`);
  }
}
if (collisions > 0) {
  console.warn(`  WARNING: ${collisions} hash collisions found!`);
} else {
  console.log("  No hash collisions.");
}

// Detect bucket count from keys
let maxBucket = 0;
for (const key of keys) {
  const m = key.match(/:(\d+):/);
  if (m) maxBucket = Math.max(maxBucket, parseInt(m[1]));
}
const bucketCount = maxBucket + 1;

// ── Write binary file ───────────────────────────────────────────────────

const numEntries = entries.length;
const headerSize = 32;
const indexEntrySize = 12; // 8 (hash) + 4 (offset)
const indexSize = numEntries * indexEntrySize;
const dataEntrySize = NUM_ACTIONS * 4; // float32 per action
const dataSize = numEntries * dataEntrySize;
const totalSize = headerSize + indexSize + dataSize;

console.log(`\nWriting binary format...`);
console.log(`  Entries:      ${numEntries}`);
console.log(`  Actions:      ${NUM_ACTIONS}`);
console.log(`  Buckets:      ${bucketCount}`);
console.log(`  Header:       ${headerSize} bytes`);
console.log(`  Index:        ${indexSize} bytes`);
console.log(`  Data:         ${dataSize} bytes`);
console.log(`  Total:        ${totalSize} bytes (${(totalSize / 1024 / 1024).toFixed(2)} MB)`);

const buf = Buffer.alloc(totalSize);

// Header
buf.write("CFR1", 0, 4, "ascii");
buf.writeUInt32LE(1, 4);              // version
buf.writeUInt32LE(numEntries, 8);
buf.writeUInt32LE(NUM_ACTIONS, 12);
buf.writeUInt32LE(bucketCount, 16);
// 12 bytes reserved (already zero)

// Index + Data
for (let i = 0; i < numEntries; i++) {
  const entry = entries[i];

  // Index entry
  const indexOffset = headerSize + i * indexEntrySize;
  buf.writeBigUInt64LE(entry.hash, indexOffset);
  buf.writeUInt32LE(i * dataEntrySize, indexOffset + 8); // data offset relative to data section start

  // Data entry
  const dataOffset = headerSize + indexSize + i * dataEntrySize;
  for (let a = 0; a < NUM_ACTIONS; a++) {
    buf.writeFloatLE(entry.probs[a], dataOffset + a * 4);
  }
}

fs.writeFileSync(outputPath, buf);
console.log(`\nBinary written to: ${outputPath}`);

// Also write a human-readable index for debugging
const idxLines = entries.map(e => `${e.hash.toString(16).padStart(16, "0")} ${e.key}`);
fs.writeFileSync(idxPath, idxLines.join("\n"));
console.log(`Index written to:  ${idxPath}`);

// Verify: read back and check a few entries
console.log("\nVerification:");
const readBuf = fs.readFileSync(outputPath);
const magic = readBuf.toString("ascii", 0, 4);
const version = readBuf.readUInt32LE(4);
const n = readBuf.readUInt32LE(8);
console.log(`  Magic: ${magic}, Version: ${version}, Entries: ${n}`);

// Spot check 3 random entries
for (let t = 0; t < Math.min(3, numEntries); t++) {
  const idx = Math.floor(Math.random() * numEntries);
  const entry = entries[idx];
  const iOff = headerSize + idx * indexEntrySize;
  const readHash = readBuf.readBigUInt64LE(iOff);
  const dOff = headerSize + indexSize + idx * dataEntrySize;
  const readProb0 = readBuf.readFloatLE(dOff);

  const match = readHash === entry.hash && Math.abs(readProb0 - entry.probs[0]) < 0.0001;
  console.log(`  Entry ${idx}: hash=${match ? "OK" : "MISMATCH"} prob[0]=${readProb0.toFixed(4)} (expected ${entry.probs[0].toFixed(4)})`);
}

console.log("\nDone.");
