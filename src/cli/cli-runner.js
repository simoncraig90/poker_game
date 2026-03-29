#!/usr/bin/env node
"use strict";

const readline = require("readline");
const path = require("path");
const { Session } = require("../api/session");
const { CMD, command } = require("../api/commands");

// ── Configuration ──────────────────────────────────────────────────────────

const logPath = path.join(process.cwd(), "cli-session-events.jsonl");
const session = new Session(
  { tableId: "cli-1", tableName: "CLI Table", maxSeats: 6, sb: 5, bb: 10, minBuyIn: 400, maxBuyIn: 1000 },
  { sessionId: `cli-${Date.now()}`, logPath }
);

const rl = readline.createInterface({ input: process.stdin, output: process.stdout, prompt: "poker> " });

// ── Helpers ────────────────────────────────────────────────────────────────

function c$(v) {
  return Math.abs(v) >= 100 ? "$" + (v / 100).toFixed(2) : v + "c";
}

function printState() {
  const state = session.getState();
  console.log();
  console.log(`Table: ${state.tableName} | ${c$(state.sb)}/${c$(state.bb)} | Button: ${state.button}`);

  for (let i = 0; i < state.maxSeats; i++) {
    const s = state.seats[i];
    if (s.status === "EMPTY") {
      console.log(`  Seat ${i}: [empty]`);
    } else {
      const flags = [];
      if (s.inHand && !s.folded) flags.push("active");
      if (s.folded) flags.push("folded");
      if (s.allIn) flags.push("all-in");
      if (s.bet > 0) flags.push(`bet=${c$(s.bet)}`);
      const cards = s.holeCards ? ` [${s.holeCards.join(" ")}]` : "";
      console.log(`  Seat ${i}: ${s.player.name} ${c$(s.stack)}${cards} ${flags.join(" ")}`);
    }
  }

  if (state.hand) {
    const h = state.hand;
    console.log(`  Hand #${h.handId} | ${h.phase} | Pot: ${c$(h.pot)} | Board: ${h.board.join(" ") || "-"}`);
    if (h.actionSeat != null) {
      const seat = state.seats[h.actionSeat];
      const name = seat.player ? seat.player.name : `Seat ${h.actionSeat}`;
      const actions = h.legalActions ? h.legalActions.actions.join("/") : "?";
      console.log(`  >>> ${name} to act (${actions})`);
    }
  }
  console.log();
}

function dispatch(cmd) {
  const result = session.dispatch(cmd);
  if (!result.ok) {
    console.log(`Error: ${result.error}`);
  } else if (result.events.length > 0) {
    for (const e of result.events) {
      const parts = [e.type];
      if (e.player) parts.push(e.player);
      if (e.action) parts.push(e.action);
      if (e.amount != null && e.amount > 0) parts.push(c$(e.amount));
      if (e.blindType) parts.push(e.blindType);
      if (e.street && e.type === "DEAL_COMMUNITY") parts.push(e.newCards.join(" "));
      console.log(`  >> ${parts.join(" ")}`);
    }
  }
  return result;
}

// ── Command Parser ─────────────────────────────────────────────────────────

function parseInput(line) {
  const parts = line.trim().split(/\s+/);
  const cmd = parts[0].toLowerCase();

  switch (cmd) {
    case "seat":
      // seat <index> <name> <buyin>
      return command(CMD.SEAT_PLAYER, {
        seat: parseInt(parts[1]),
        name: parts[2],
        buyIn: parseInt(parts[3]),
        country: parts[4] || "XX",
      });

    case "leave":
      return command(CMD.LEAVE_TABLE, { seat: parseInt(parts[1]) });

    case "start":
    case "deal":
      return command(CMD.START_HAND);

    case "fold":
    case "f":
      return command(CMD.PLAYER_ACTION, { seat: getActionSeat(), action: "FOLD" });

    case "check":
    case "x":
      return command(CMD.PLAYER_ACTION, { seat: getActionSeat(), action: "CHECK" });

    case "call":
    case "c":
      return command(CMD.PLAYER_ACTION, { seat: getActionSeat(), action: "CALL" });

    case "bet":
    case "b":
      return command(CMD.PLAYER_ACTION, { seat: getActionSeat(), action: "BET", amount: parseInt(parts[1]) });

    case "raise":
    case "r":
      return command(CMD.PLAYER_ACTION, { seat: getActionSeat(), action: "RAISE", amount: parseInt(parts[1]) });

    case "state":
    case "s":
      printState();
      return null;

    case "log":
      return command(CMD.GET_EVENT_LOG);

    case "help":
    case "h":
      console.log("Commands:");
      console.log("  seat <n> <name> <buyin>  Seat a player");
      console.log("  leave <n>                Remove player");
      console.log("  start / deal             Start new hand");
      console.log("  fold / f                 Fold");
      console.log("  check / x                Check");
      console.log("  call / c                 Call");
      console.log("  bet <amount> / b         Bet");
      console.log("  raise <amount> / r       Raise to");
      console.log("  state / s                Show table");
      console.log("  log                      Show event log");
      console.log("  quit / q                 Exit");
      return null;

    case "quit":
    case "q":
    case "exit":
      console.log("Event log:", logPath);
      process.exit(0);

    default:
      // Try JSON command
      try {
        return JSON.parse(line);
      } catch {
        console.log(`Unknown command: ${cmd}. Type 'help' for commands.`);
        return null;
      }
  }
}

function getActionSeat() {
  const state = session.getState();
  return state.hand ? state.hand.actionSeat : null;
}

// ── Main Loop ──────────────────────────────────────────────────────────────

console.log("Poker Lab CLI");
console.log("Type 'help' for commands.\n");

rl.prompt();

rl.on("line", (line) => {
  if (!line.trim()) {
    rl.prompt();
    return;
  }

  const cmd = parseInput(line);
  if (cmd) {
    const result = dispatch(cmd);
    // Auto-show state after mutations
    if (result && result.ok && result.events.length > 0) {
      printState();
    }
    if (cmd.type === CMD.GET_EVENT_LOG && result.ok) {
      console.log(`${result.events.length} events in log`);
    }
  }

  rl.prompt();
});

rl.on("close", () => {
  console.log("\nEvent log:", logPath);
  process.exit(0);
});
