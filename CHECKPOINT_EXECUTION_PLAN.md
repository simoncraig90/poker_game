# Checkpoint Execution Plan

Consolidated operator document. Covers demo execution, evidence collection, verdict rubric, go/no-go rule, and contingency.

---

## A. Demo Execution Plan

### Pre-Flight (2 min)

```bash
# 1. Run all automated suites
node --test test/accounting.test.js
node --test test/conformance.test.js
node --test test/ws-conformance.test.js
node --test test/e2e-session.test.js
node --test test/recovery.test.js
node --test test/session-browser.test.js
```

All 168 checks must pass. Any failure: stop, fix, re-run. Do not proceed to manual demos with red suites.

```bash
# 2. Clean slate
# Stop server if running
rm -rf data/sessions/
```

```bash
# 3. Open a scratch file for evidence notes
```

### Demo 1: Restart Recovery (5 min)

| Step | Command / Action | Expected |
|------|-----------------|----------|
| 1 | `node src/server/ws-server.js` | `Created new session session-{id}` |
| 2 | Browser → `http://localhost:9100` | Status: Connected |
| 3 | Seat Alice/1000, Bob/800, Charlie/600 | 3 players visible |
| 4 | Deal + play 3 hands (D key, Fold/Call) | 3 hands complete |
| 5 | **RECORD** stacks, names, hand count | Written to scratch file |
| 6 | History tab → 3 hands listed | Confirm |
| 7 | Ctrl+C server | Browser: Disconnected |
| 8 | `node src/server/ws-server.js` | `Recovering session... Recovered: N events, 3 hands` |
| 9 | Browser auto-reconnects | Status: Connected |
| 10 | Verify stacks, players, history, banner | See checklist below |
| 11 | Deal hand 4 | Completes normally |

### Demo 2: Mid-Hand Crash (4 min)

| Step | Command / Action | Expected |
|------|-----------------|----------|
| 1 | Deal new hand | Hand starts |
| 2 | One player folds. Stop. Do NOT finish hand. | Mid-action state |
| 3 | **RECORD** hand #, pre-hand stacks (Events tab → HAND_START) | Written |
| 4 | Ctrl+C server | Hard kill |
| 5 | `node src/server/ws-server.js` | `Recovery: voided incomplete hand #N` |
| 6 | Browser reconnects | Between-hands state |
| 7 | Verify stacks = pre-hand, void marker, chip sum | See checklist |
| 8 | Deal next hand | Completes normally |

### Demo 3: Archive + New Session (3 min)

| Step | Command / Action | Expected |
|------|-----------------|----------|
| 1 | Sessions tab | Active session (green dot) |
| 2 | "Archive & New Session" → Confirm | `Archived old session. New session: session-{newId}` |
| 3 | Table is empty, new session ID in header | No players |
| 4 | Sessions tab: old = complete (grey), new = active (green) | Confirm |
| 5 | Click completed session → hand list | Old hands visible |
| 6 | Click a hand → detail/timeline | Events load |
| 7 | Seat 2 players, deal 1 hand | Hand #1 completes |

### Demo 4: Post-Archive Restart (2 min)

| Step | Command / Action | Expected |
|------|-----------------|----------|
| 1 | **RECORD** active session ID, players, stacks | Written |
| 2 | Ctrl+C server | |
| 3 | `node src/server/ws-server.js` | Recovers active session only |
| 4 | Browser reconnects | Correct session, correct state |
| 5 | Sessions tab: both sessions, correct statuses | Archived still browsable |

---

## B. Evidence Checklist

Collect this during demos. Minimum viable evidence is notes; screenshots strengthen the record.

| Demo | Evidence Item | Format |
|------|--------------|--------|
| 1 | Pre-kill stacks (Alice, Bob, Charlie) | Note |
| 1 | Post-recovery stacks (must match) | Note |
| 1 | History tab showing 3 hands post-recovery | Screenshot or note |
| 1 | Hand 4 completed (yes/no) | Note |
| 2 | Pre-hand stacks from HAND_START event | Note |
| 2 | Post-recovery stacks (must match pre-hand) | Note |
| 2 | Chip sum: `sum(stacks) == sum(buy-ins)` (2400) | Calculation |
| 2 | Voided hand marker visible | Screenshot or note |
| 3 | Sessions tab: 2 sessions, correct statuses | Screenshot |
| 3 | Archived session hand browsable | Screenshot or note |
| 3 | New session hand starts at #1 | Note |
| 4 | Recovered session ID matches active session | Note |
| 4 | Archived session still browsable after restart | Note |

**13 evidence items total.** All must be recorded to consider the checkpoint complete.

---

## C. Checkpoint Verdict Rubric

Single table. One row per gate. All must pass.

| Gate | Check | Pass | Fail |
|------|-------|------|------|
| G0: Automated | 168/168 checks green | All pass | Any red |
| G1: Restart recovery | Stacks + history identical across restart; hand 4 works | Exact match | Any delta |
| G2: Mid-hand void | Pre-hand stacks restored; chip sum unchanged; void marked; next hand works | Zero leak | Any leak or missing marker |
| G3: Archive lifecycle | Old session read-only + browsable; new session clean; no cross-contamination | Clean separation | Any leakage |
| G4: Post-archive restart | Active session recovered; archived untouched | Correct session | Wrong session or data loss |

---

## D. Go / No-Go Rule

**GO**: G0 + G1 + G2 + G3 + G4 all pass. All 13 evidence items recorded.

**NO-GO**: Any gate fails.

There is no partial pass. The checkpoint is binary.

---

## E. Branch Recommendation After Pass

**Next branch: Option A — Showdown + Side-Pot Closure.**

Justification:
1. The game loop is incomplete. Every hand currently resolves only by fold-out (last player standing). This is not poker; it's a folding simulator.
2. Study features (Option C) have nothing meaningful to study without showdown outcomes. Hand evaluation data is the raw material for research.
3. Polish (Option B) makes a broken game prettier. If the demos complete without UX blockers, polish is not the bottleneck.
4. Showdown + side pots are the last hard engine work. Everything after builds on a complete game loop.
5. The persistence and recovery infrastructure (Phases 6-7) already handles arbitrary event sequences. Showdown events will slot into the existing event log without architectural changes.

Scope estimate: 1-2 phases. Phase 8 = hand evaluator + single-pot showdown. Phase 9 = side pots + split pots + odd chips.

**Do not start identity, study, or polish work until showdown is correct and tested.**

---

## F. Recovery Plan After Fail

If any gate fails:

### Step 1: Classify the failure

| Failure type | Example | Severity |
|-------------|---------|----------|
| Accounting error | Chip sum changes across void | Critical — engine bug |
| State mismatch | Stacks differ after recovery | Critical — persistence bug |
| UI-only | Recovery banner missing, void not visually marked | Non-critical — cosmetic |
| Lifecycle error | Wrong session recovered, archive data lost | Critical — storage bug |

### Step 2: Respond by severity

**Critical failure**:
1. Write a failing automated test that reproduces the exact scenario
2. Fix the root cause in the engine, persistence, or recovery code
3. Re-run ALL automated suites (not just the new test)
4. Re-run the failed demo AND the demo after it (since demos are sequential)

**Non-critical failure**:
1. Log the cosmetic issue
2. Fix if it takes <30 min
3. If >30 min: note it, pass the demo with an annotation, move on
4. Non-critical failures do NOT block the checkpoint unless they prevent evidence collection

### Step 3: Re-run scope

| Failed Gate | Re-run |
|-------------|--------|
| G0 (automated) | Fix, re-run all suites. Do not start demos. |
| G1 (restart) | Fix, re-run G0 + Demo 1 + Demo 2 |
| G2 (mid-hand) | Fix, re-run G0 + Demo 2 |
| G3 (archive) | Fix, re-run G0 + Demo 3 + Demo 4 |
| G4 (post-archive) | Fix, re-run G0 + Demo 4 |

Clean slate (`rm -rf data/sessions/`) before every re-run.

### Step 3: Ceiling

If the same gate fails 3 times after distinct fixes, escalate: the problem is architectural, not a point bug. Write a root-cause analysis before attempting a fourth fix.
