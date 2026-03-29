# Protocol Baseline Freeze Plan

How to freeze the current protocol understanding before starting implementation, so future changes are measurable diffs against a known-good state.

---

## What Constitutes the Baseline

### Tier 1: Specification (the contract)

These documents define what the engine implements. Changes here are design decisions.

| File | Content |
|------|---------|
| `LIVE_TABLE_REDUCER_SPEC.md` | State shapes, event handlers, invariants |
| `NORMALIZED_HAND_EVENT_SCHEMA.md` | Event types, fields, ordering constraints |
| `BACKEND_COMMON_PATH_SCOPE.md` | What's in/out for Phase 1 |
| `STATE_REDUCER_GAP_LIST.md` | Known unknowns and their severity |

### Tier 2: Toolchain (the pipeline)

These scripts produce the data that validates the spec. Changes here are implementation improvements.

| File | Content |
|------|---------|
| `scripts/cdp-capture.js` | Live capture tool |
| `scripts/decode-session.js` | Thrift decoder |
| `scripts/emit-normalized-events.js` | Normalized event emitter |
| `scripts/replay-normalized-hand.js` | Replay consumer + accounting check |

### Tier 3: Evidence (the proof)

These files prove the spec is correct. They are read-only after freeze.

| Path | Content |
|------|---------|
| `captures/20260329_202750/websocket.jsonl` | Raw captured frames |
| `captures/20260329_202750/decoded-events.jsonl` | Decoded event stream |
| `captures/20260329_202750/normalized-hand-events.jsonl` | Normalized events |
| `captures/20260329_202750/hands/hand-*.jsonl` | Per-hand events |
| `captures/20260329_202750/hands/replay-*.txt` | Replay timelines |
| `captures/20260329_202750/opcode_catalog.md` | Opcode reference |

### Tier 4: Process (how we got here)

These document the journey. Useful for context but not normative.

| File | Content |
|------|---------|
| `REPLAY_FIX_VALIDATION.md` | Before/after for F1 and F2 fixes |
| `REPLAY_PROOF_NOTES.md` | Initial replay findings (pre-fix) |
| `NORMALIZED_EVENT_EMITTER_NOTES.md` | Emitter design decisions |
| `DECODER_VALIDATION_PLAN.md` | Planned validation approach |
| `CAPTURE_CDP_PLAN.md` | CDP capture architecture |

---

## Freeze Method: Git Tag

Use a lightweight git tag. No branch — the baseline is a point-in-time reference on `main`.

```bash
# Run regression checks first (see below)
# Then tag
git tag -a protocol-baseline-v1 -m "Protocol baseline: 8 hands validated, common path proven, reducer spec complete"
git push origin protocol-baseline-v1
```

### Why Tag, Not Branch

- A branch implies ongoing parallel work. The baseline is frozen; work continues on `main`.
- A tag is immutable. It marks exactly what was proven.
- To compare future state against baseline: `git diff protocol-baseline-v1..HEAD`

---

## Pre-Freeze Regression Checks

Run these before tagging. All must pass.

### Check 1: Decode Pipeline

```bash
node scripts/decode-session.js captures/20260329_202750
# Expected: "Decode complete. Events: 733, Hands: 8"
```

### Check 2: Normalized Event Emission

```bash
node scripts/emit-normalized-events.js captures/20260329_202750
# Expected: "Output: 169 normalized events, Hands: 8, Inferred: 28"
# Expected: BET_RETURN count = 7
```

### Check 3: Replay Accounting

```bash
for h in 260272188638 260272208552 260272235570; do
  node scripts/replay-normalized-hand.js captures/20260329_202750/hands/hand-$h.jsonl
done
grep "Stack check" captures/20260329_202750/hands/replay-timeline-*.txt
# Expected: "PASS" for all three hands
```

### Check 4: No Decode Errors

```bash
grep -c "ERR_\|DECODE_ERROR" captures/20260329_202750/decoded-events.jsonl
# Expected: 0
```

### Check 5: Event Type Counts

```bash
node -e "
const fs = require('fs');
const events = fs.readFileSync('captures/20260329_202750/normalized-hand-events.jsonl','utf8').trim().split('\n').map(l => JSON.parse(l));
const counts = {};
events.forEach(e => counts[e.type] = (counts[e.type]||0) + 1);
console.log(JSON.stringify(counts, null, 2));
"
```

Expected:
```json
{
  "TABLE_SNAPSHOT": 1,
  "HAND_START": 8,
  "BLIND_POST": 16,
  "HERO_CARDS": 7,
  "PLAYER_ACTION": 42,
  "BET_RETURN": 7,
  "DEAL_COMMUNITY": 5,
  "POT_UPDATE": 51,
  "POT_AWARD": 8,
  "HAND_SUMMARY": 8,
  "HAND_RESULT": 7,
  "HAND_END": 9
}
```

---

## Comparing Future Changes Against Baseline

### After a New Capture Session

```bash
# Decode + emit + replay new session
node scripts/decode-session.js captures/<new-session>
node scripts/emit-normalized-events.js captures/<new-session>

# Compare event type distribution
diff <(grep -o '"type":"[^"]*"' captures/20260329_202750/normalized-hand-events.jsonl | sort | uniq -c | sort -rn) \
     <(grep -o '"type":"[^"]*"' captures/<new-session>/normalized-hand-events.jsonl | sort | uniq -c | sort -rn)

# Check for new/unknown event types
grep "RAW_\|ERR_\|UNKNOWN_" captures/<new-session>/decoded-events.jsonl | head

# Replay and check accounting
for f in captures/<new-session>/hands/hand-*.jsonl; do
  node scripts/replay-normalized-hand.js "$f"
done
grep "Stack check" captures/<new-session>/hands/replay-timeline-*.txt
```

### After Modifying the Decoder or Emitter

```bash
# Re-run baseline session through modified pipeline
node scripts/decode-session.js captures/20260329_202750
node scripts/emit-normalized-events.js captures/20260329_202750

# Diff normalized output against baseline
git diff protocol-baseline-v1 -- captures/20260329_202750/normalized-hand-events.jsonl

# Re-run replay checks
for h in 260272188638 260272208552 260272235570; do
  node scripts/replay-normalized-hand.js captures/20260329_202750/hands/hand-$h.jsonl
done
grep "Stack check" captures/20260329_202750/hands/replay-timeline-*.txt
# Must still pass
```

### After Modifying the Spec

Any change to `LIVE_TABLE_REDUCER_SPEC.md` or `NORMALIZED_HAND_EVENT_SCHEMA.md` should:
1. Reference which gap it closes (e.g., "Closes GAP-1: showdown").
2. Include the capture evidence that justified the change.
3. Update `STATE_REDUCER_GAP_LIST.md` to mark the gap as closed.
4. Re-run the full regression suite.

---

## Post-Freeze Protocol

1. **Tag the baseline** (one-time, today).
2. **Continue implementation on `main`**. The tag is a reference point, not a branch.
3. **After the 45-minute validation capture** (closes GAP-1/2/4/6):
   - Run the new session through the pipeline.
   - Update spec docs for any new findings.
   - Tag again: `protocol-baseline-v2`.
4. **After side-pot capture** (closes GAP-3):
   - Same process.
   - Tag: `protocol-baseline-v3`.
5. **Each tag is a complete, self-consistent state** of spec + toolchain + evidence.
