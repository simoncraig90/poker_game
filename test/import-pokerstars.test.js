#!/usr/bin/env node
"use strict";

/**
 * PokerStars Hand History Import Tests
 */

const path = require("path");
const fs = require("fs");
const { parsePokerStarsText, handToEvents, importPokerStars, parseCard, parseCards } = require("../src/import/pokerstars");
const { SessionStorage } = require("../src/api/storage");
const { queryHands, getActorStats } = require("../src/api/query");

const testDir = path.join(__dirname, "..", "test-output", "import-" + Date.now());
fs.mkdirSync(testDir, { recursive: true });

let checks = 0, passed = 0, failed = 0;
function check(label, cond) { checks++; if (cond) passed++; else { failed++; console.log(`  FAIL: ${label}`); } }

// ── Sample PokerStars Hand Histories ──────────────────────────────────────

const SAMPLE_HAND_1 = `PokerStars Hand #220851545772: Hold'em No Limit ($0.05/$0.10 USD) - 2021/03/15 14:30:00 ET
Table 'Procyon III' 6-max Seat #2 is the button
Seat 1: Alice ($10.00 in chips)
Seat 2: Bob ($9.50 in chips)
Seat 3: Charlie ($11.20 in chips)
Alice: posts small blind $0.05
Bob: posts big blind $0.10
*** HOLE CARDS ***
Dealt to Alice [Ah Ks]
Charlie: raises $0.10 to $0.20
Alice: calls $0.15
Bob: folds
*** FLOP *** [9s 7h 3d]
Alice: checks
Charlie: bets $0.30
Alice: calls $0.30
*** TURN *** [9s 7h 3d] [Jc]
Alice: checks
Charlie: checks
*** RIVER *** [9s 7h 3d Jc] [2h]
Alice: bets $0.40
Charlie: folds
Uncalled bet ($0.40) returned to Alice
Alice collected $1.05 from pot
*** SUMMARY ***
Total pot $1.05 | Rake $0.00`;

const SAMPLE_HAND_2 = `PokerStars Hand #220851545773: Hold'em No Limit ($0.05/$0.10 USD) - 2021/03/15 14:35:00 ET
Table 'Procyon III' 6-max Seat #3 is the button
Seat 1: Alice ($10.55 in chips)
Seat 2: Bob ($9.40 in chips)
Seat 3: Charlie ($10.90 in chips)
Bob: posts small blind $0.05
Charlie: posts big blind $0.10
*** HOLE CARDS ***
Dealt to Alice [Qd Qh]
Alice: raises $0.10 to $0.20
Bob: calls $0.15
Charlie: calls $0.10
*** FLOP *** [Qs 7c 2d]
Bob: checks
Charlie: checks
Alice: bets $0.40
Bob: folds
Charlie: calls $0.40
*** TURN *** [Qs 7c 2d] [8h]
Charlie: checks
Alice: bets $0.80
Charlie: calls $0.80
*** RIVER *** [Qs 7c 2d 8h] [4s]
Charlie: checks
Alice: bets $1.50
Charlie: folds
Uncalled bet ($1.50) returned to Alice
Alice collected $2.85 from pot
*** SUMMARY ***
Total pot $2.85 | Rake $0.00`;

const SAMPLE_SHOWDOWN = `PokerStars Hand #220851545774: Hold'em No Limit ($0.05/$0.10 USD) - 2021/03/15 14:40:00 ET
Table 'Procyon III' 6-max Seat #1 is the button
Seat 1: Alice ($12.40 in chips)
Seat 2: Bob ($9.40 in chips)
Alice: posts small blind $0.05
Bob: posts big blind $0.10
*** HOLE CARDS ***
Dealt to Alice [Kh Kd]
Alice: raises $0.10 to $0.20
Bob: calls $0.10
*** FLOP *** [9s 5h 2c]
Bob: checks
Alice: bets $0.20
Bob: calls $0.20
*** TURN *** [9s 5h 2c] [7d]
Bob: checks
Alice: bets $0.40
Bob: calls $0.40
*** RIVER *** [9s 5h 2c 7d] [3s]
Bob: checks
Alice: bets $0.60
Bob: calls $0.60
*** SHOW DOWN ***
Alice: shows [Kh Kd] (a pair of Kings)
Bob: shows [Ts Td] (a pair of Tens)
Alice collected $2.80 from pot
*** SUMMARY ***
Total pot $2.80 | Rake $0.00`;

const MULTI_HAND = SAMPLE_HAND_1 + "\n\n\n" + SAMPLE_HAND_2;

// ═══════════════════════════════════════════════════════════════════════════
//  Test 1: Card Parsing
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 1: Card Parsing ===");
{
  check("T1: Ah → Ah", parseCard("Ah") === "Ah");
  check("T1: Ts → Ts", parseCard("Ts") === "Ts");
  check("T1: 2c → 2c", parseCard("2c") === "2c");
  check("T1: parseCards", JSON.stringify(parseCards("[Ah Ks]")) === '["Ah","Ks"]');
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 2: Single Hand Parsing
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 2: Single Hand Parse ===");
{
  const hands = parsePokerStarsText(SAMPLE_HAND_1);
  check("T2: 1 hand parsed", hands.length === 1);
  const h = hands[0];

  check("T2: hand num", h.handNum === "220851545772");
  check("T2: sb = 5 cents", h.sb === 5);
  check("T2: bb = 10 cents", h.bb === 10);
  check("T2: button seat 2", h.buttonSeat === 2);
  check("T2: 3 players", Object.keys(h.players).length === 3);
  check("T2: Alice stack 1000c", h.players[1].stack === 1000);
  check("T2: Bob stack 950c", h.players[2].stack === 950);
  check("T2: hero cards dealt", h.holeCards[1] && h.holeCards[1].length === 2);
  check("T2: hero has Ah Ks", h.holeCards[1][0] === "Ah" && h.holeCards[1][1] === "Ks");
  check("T2: board 5 cards", h.board.length === 5);
  check("T2: has actions", h.actions.length > 0);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 3: Multi-Hand Parsing
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 3: Multi-Hand Parse ===");
{
  const hands = parsePokerStarsText(MULTI_HAND);
  check("T3: 2 hands parsed", hands.length === 2);
  check("T3: different hand nums", hands[0].handNum !== hands[1].handNum);
  check("T3: both have players", Object.keys(hands[0].players).length >= 2 && Object.keys(hands[1].players).length >= 2);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 4: Showdown Hand Parsing
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 4: Showdown Parse ===");
{
  const hands = parsePokerStarsText(SAMPLE_SHOWDOWN);
  check("T4: 1 hand parsed", hands.length === 1);
  const h = hands[0];
  check("T4: showdown players", h.showdownPlayers.length === 2);
  check("T4: Alice shows Kh Kd", h.showdownPlayers[0].cards[0] === "Kh");
  check("T4: hand name present", h.showdownPlayers[0].handName.includes("Kings"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 5: Event Generation
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 5: Event Generation ===");
{
  const hands = parsePokerStarsText(SAMPLE_HAND_1);
  const events = handToEvents(hands[0], "test-session", "1");

  const types = events.map((e) => e.type);
  check("T5: has HAND_START", types.includes("HAND_START"));
  check("T5: has BLIND_POST", types.includes("BLIND_POST"));
  check("T5: has HERO_CARDS", types.includes("HERO_CARDS"));
  check("T5: has PLAYER_ACTION", types.includes("PLAYER_ACTION"));
  check("T5: has DEAL_COMMUNITY", types.includes("DEAL_COMMUNITY"));
  check("T5: has BET_RETURN", types.includes("BET_RETURN"));
  check("T5: has POT_AWARD", types.includes("POT_AWARD"));
  check("T5: has HAND_SUMMARY", types.includes("HAND_SUMMARY"));
  check("T5: has HAND_RESULT", types.includes("HAND_RESULT"));
  check("T5: has HAND_END", types.includes("HAND_END"));

  // HAND_START has players
  const hs = events.find((e) => e.type === "HAND_START");
  check("T5: HAND_START has players", Object.keys(hs.players).length === 3);
  check("T5: players have name+stack", hs.players["1"].name === "Alice" && hs.players["1"].stack === 1000);

  // Source marked as import
  check("T5: source origin = import", hs._source.origin === "import");

  // HAND_END is last
  check("T5: HAND_END is last", types[types.length - 1] === "HAND_END");
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 6: Showdown Event Generation
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 6: Showdown Events ===");
{
  const hands = parsePokerStarsText(SAMPLE_SHOWDOWN);
  const events = handToEvents(hands[0], "test-session", "1");
  const types = events.map((e) => e.type);

  check("T6: has SHOWDOWN_REVEAL", types.includes("SHOWDOWN_REVEAL"));
  const reveal = events.find((e) => e.type === "SHOWDOWN_REVEAL");
  check("T6: 2 reveals", reveal.reveals.length === 2);
  check("T6: reveal has cards", reveal.reveals[0].cards.length === 2);

  const summary = events.find((e) => e.type === "HAND_SUMMARY");
  check("T6: summary showdown=true", summary.showdown === true);
  check("T6: summary handRank present", summary.handRank && summary.handRank.includes("Kings"));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 7: Full Import to Session Store
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 7: Full Import ===");
{
  const storage = new SessionStorage(path.join(testDir, "sessions"));

  // Write sample to a temp file
  const samplePath = path.join(testDir, "sample.txt");
  fs.writeFileSync(samplePath, MULTI_HAND);

  const result = importPokerStars(samplePath, storage);
  check("T7: sessionId exists", !!result.sessionId);
  check("T7: 2 hands imported", result.handsImported === 2);
  check("T7: no errors", result.errors.length === 0);

  // Verify session structure
  const info = storage.load(result.sessionId);
  check("T7: session loadable", !!info);
  check("T7: status is complete", info.meta.status === "complete");
  check("T7: handsPlayed = 2", info.meta.handsPlayed === 2);

  // Verify events file exists and is valid JSONL
  const content = fs.readFileSync(info.eventsPath, "utf8").trim();
  const lines = content.split("\n").filter(Boolean);
  check("T7: events file non-empty", lines.length > 0);
  const allEvents = lines.map((l) => JSON.parse(l));
  check("T7: events are valid JSON", allEvents.length > 0);

  // Verify queryable
  const hands = queryHands(storage, { sessionId: result.sessionId });
  check("T7: queryable", hands.length > 0);
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 8: Imported Hands Replayable (Event Structure)
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 8: Replay Compatibility ===");
{
  const storage = new SessionStorage(path.join(testDir, "sessions-t8"));
  const samplePath = path.join(testDir, "sample-t8.txt");
  fs.writeFileSync(samplePath, SAMPLE_SHOWDOWN);

  const result = importPokerStars(samplePath, storage);
  const info = storage.load(result.sessionId);
  const content = fs.readFileSync(info.eventsPath, "utf8").trim();
  const allEvents = content.split("\n").filter(Boolean).map((l) => JSON.parse(l));

  // Hand 1 events
  const h1Events = allEvents.filter((e) => e.handId === "1");
  check("T8: hand events found", h1Events.length > 0);

  // First event is HAND_START
  check("T8: starts with HAND_START", h1Events[0].type === "HAND_START");

  // Has the required replay fields
  const hs = h1Events[0];
  check("T8: HAND_START has button", typeof hs.button === "number");
  check("T8: HAND_START has players", typeof hs.players === "object");

  // BLIND_POST events have required fields
  const blinds = h1Events.filter((e) => e.type === "BLIND_POST");
  check("T8: has blind posts", blinds.length >= 1);
  check("T8: blind has seat+amount", typeof blinds[0].seat === "number" && typeof blinds[0].amount === "number");

  // PLAYER_ACTION events have required fields
  const actions = h1Events.filter((e) => e.type === "PLAYER_ACTION");
  check("T8: has actions", actions.length > 0);
  check("T8: action has seat+action+street", actions[0].seat != null && actions[0].action && actions[0].street);

  // DEAL_COMMUNITY events
  const deals = h1Events.filter((e) => e.type === "DEAL_COMMUNITY");
  check("T8: has deal events", deals.length >= 1);
  check("T8: deal has board", Array.isArray(deals[0].board));
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 9: Import Isolation from Native Sessions
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 9: Session Isolation ===");
{
  const storage = new SessionStorage(path.join(testDir, "sessions-t9"));

  // Create a native session
  storage.create("native-session", { tableId: "t", tableName: "Native", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 });

  // Import
  const samplePath = path.join(testDir, "sample-t9.txt");
  fs.writeFileSync(samplePath, SAMPLE_HAND_1);
  const result = importPokerStars(samplePath, storage);

  // Both sessions exist
  const all = storage.list();
  check("T9: 2 sessions", all.length === 2);
  check("T9: different IDs", all[0].sessionId !== all[1].sessionId);

  // Import session is complete
  const importMeta = all.find((s) => s.sessionId === result.sessionId);
  check("T9: import is complete", importMeta.status === "complete");

  // Import is distinguishable by sessionId prefix
  check("T9: import ID has prefix", result.sessionId.startsWith("import-"));

  // Source origin in events
  const info = storage.load(result.sessionId);
  const content = fs.readFileSync(info.eventsPath, "utf8").trim();
  const firstEvent = JSON.parse(content.split("\n")[0]);
  check("T9: source origin = import", firstEvent._source.origin === "import");
}

// ═══════════════════════════════════════════════════════════════════════════
//  Test 10: No Regression
// ═══════════════════════════════════════════════════════════════════════════

console.log("=== Test 10: No Regression ===");
{
  // Verify existing storage operations still work
  const storage = new SessionStorage(path.join(testDir, "sessions-t10"));
  const info = storage.create("reg-test", { tableId: "t", tableName: "T", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 100, maxBuyIn: 1000 });
  check("T10: native create works", !!info);
  check("T10: native load works", !!storage.load("reg-test"));

  storage.archive("reg-test", 0);
  const meta = storage.load("reg-test").meta;
  check("T10: archive works", meta.status === "complete");
}

// ═══════════════════════════════════════════════════════════════════════════

console.log(`\n*** IMPORT TESTS: ${passed}/${checks} passed, ${failed} failed ***`);
if (failed > 0) process.exit(1);
