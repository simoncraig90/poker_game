#!/usr/bin/env node
"use strict";

/**
 * Test the advisor against known bad hands from today's session.
 * Spawns the advisor in test mode and checks recommendations.
 *
 * Usage: node scripts/test-advisor.js
 */

const { execSync } = require("child_process");
const PYTHON = "C:\\Users\\Simon\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";

// Hands that cost money today — what the advisor SHOULD recommend
const TEST_HANDS = [
  {
    desc: "J9o on 3-5-7-9-T straight board (LOST to straight)",
    hero: ["Js", "9d"],
    board: ["3c", "5h", "7d", "9s", "Tc"],
    old_rec: "RAISE 50%",
    expected: "FOLD or CALL",  // pair of 9s on 4-to-straight board
    max_equity: 0.40,
  },
  {
    desc: "JQo on 2-7-4 (no pair, just overcards)",
    hero: ["Jd", "Qs"],
    board: ["2c", "7s", "4d"],
    old_rec: "RAISE 77%",
    expected: "FOLD or CHECK",
    max_equity: 0.35,
  },
  {
    desc: "Q9 on 7-J-8-7-8 double paired board (Q high)",
    hero: ["9h", "Qc"],
    board: ["7h", "Jd", "8c", "7s", "8s"],
    old_rec: "CALL 55%",
    expected: "FOLD",
    max_equity: 0.25,
  },
  {
    desc: "78 on 6-2-7-9 straight possible (middle pair)",
    hero: ["7s", "8h"],
    board: ["6c", "2c", "7d", "9d"],
    old_rec: "RAISE 45%",
    expected: "CHECK or CALL",
    max_equity: 0.45,
  },
  {
    desc: "K3 on A-T-T-2-9 paired board (K high)",
    hero: ["3h", "Kc"],
    board: ["9d", "Ad", "2h", "Ts", "Tc"],
    old_rec: "CALL 48%",
    expected: "FOLD",
    max_equity: 0.25,
  },
  // Good hands that SHOULD get aggressive advice
  {
    desc: "AA preflop (should raise)",
    hero: ["As", "Ah"],
    board: [],
    old_rec: "n/a",
    expected: "RAISE",
    min_equity: 0.80,
  },
  {
    desc: "Set of 7s on 7-2-6 board (should bet/raise)",
    hero: ["7s", "7h"],
    board: ["7d", "2c", "6s"],
    old_rec: "n/a",
    expected: "BET or RAISE",
    min_equity: 0.70,
  },
];

// Run Python to get equity predictions
const script = `
import sys, json
sys.path.insert(0, "vision")
from advisor import equity_model_predict, evaluate_hand_strength, card_str_to_dict, _load_equity_model

# Force load
_load_equity_model()

hands = json.loads('''${JSON.stringify(TEST_HANDS)}''')

for h in hands:
    eq = equity_model_predict(h["hero"], h["board"])
    # Also get heuristic for comparison
    hero_dicts = [card_str_to_dict(c) for c in h["hero"]]
    hero_dicts = [c for c in hero_dicts if c is not None]
    board_dicts = [card_str_to_dict(c) for c in h["board"]]
    board_dicts = [c for c in board_dicts if c is not None]
    phase = "PREFLOP" if not h["board"] else ("FLOP" if len(h["board"]) == 3 else ("TURN" if len(h["board"]) == 4 else "RIVER"))
    heuristic = evaluate_hand_strength(hero_dicts, board_dicts, phase)
    print(json.dumps({"eq": eq, "heuristic": heuristic}))
`;

console.log("=".repeat(65));
console.log("  ADVISOR TEST — Replaying Bad Hands");
console.log("=".repeat(65));
console.log();

try {
  const result = execSync(`${PYTHON} -c "${script.replace(/"/g, '\\"').replace(/\n/g, '\\n')}"`, {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 30000,
  });

  const lines = result.trim().split("\n").filter(l => l.startsWith("{"));
  let pass = 0, fail = 0;

  for (let i = 0; i < TEST_HANDS.length; i++) {
    const hand = TEST_HANDS[i];
    const heroStr = hand.hero.join(" ");
    const boardStr = hand.board.length > 0 ? hand.board.join(" ") : "(preflop)";

    let eq = null, heuristic = null;
    if (i < lines.length) {
      const parsed = JSON.parse(lines[i]);
      eq = parsed.eq;
      heuristic = parsed.heuristic;
    }

    const eqPct = eq !== null ? `${(eq * 100).toFixed(0)}%` : "N/A";
    const heuPct = heuristic !== null ? `${(heuristic * 100).toFixed(0)}%` : "N/A";

    let status = "?";
    if (hand.max_equity !== undefined && eq !== null) {
      status = eq <= hand.max_equity ? "PASS" : "FAIL";
    } else if (hand.min_equity !== undefined && eq !== null) {
      status = eq >= hand.min_equity ? "PASS" : "FAIL";
    }

    if (status === "PASS") pass++;
    else fail++;

    const icon = status === "PASS" ? "✓" : "✗";
    console.log(`  ${icon} ${hand.desc}`);
    console.log(`    ${heroStr}  |  ${boardStr}`);
    console.log(`    Old heuristic: ${heuPct}  →  New model: ${eqPct}`);
    console.log(`    Old advice: ${hand.old_rec}  →  Expected: ${hand.expected}`);
    if (status === "FAIL") {
      const threshold = hand.max_equity !== undefined ? `should be ≤${hand.max_equity * 100}%` : `should be ≥${hand.min_equity * 100}%`;
      console.log(`    FAIL: equity ${eqPct} ${threshold}`);
    }
    console.log();
  }

  console.log("-".repeat(65));
  console.log(`  Results: ${pass} PASS, ${fail} FAIL out of ${TEST_HANDS.length} tests`);
  console.log("=".repeat(65));

} catch (e) {
  console.error("Error running test:", e.message);
  if (e.stderr) console.error(e.stderr);
}
