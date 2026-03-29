# Normalized Event Emitter Notes

Implementation notes for `scripts/emit-normalized-events.js`.

---

## Architecture

```
decoded-events.jsonl  →  emit-normalized-events.js  →  normalized-hand-events.jsonl
(from decode-session.js)                                 + hands/hand-{id}.jsonl
```

The emitter is a pure consumer of decoded events. It does not re-parse raw WebSocket frames or modify the decoder. It applies the rules from `NORMALIZED_HAND_EVENT_SCHEMA.md` to produce a canonical event stream.

---

## Event Mapping

| Decoded Event Type | → Normalized Event | Condition |
|---|---|---|
| `TABLE_SNAPSHOT` | `TABLE_SNAPSHOT` | Always (once per session) |
| `NEW_HAND` | `HAND_START` | Always |
| `PLAYER_UPDATE` | — | Absorbed into internal state (player names, stacks) |
| `ROUND_TRANSITION` | — | Consumed internally for action classification + inferred fold detection |
| `ACTION` (delta >= 0, roundId 3/4) | `BLIND_POST` | SB or BB |
| `ACTION` (delta >= 0, other roundId) | `PLAYER_ACTION` | Voluntary action |
| `ACTION` (delta < 0) | — | Collect sweep / blind return, filtered |
| `HERO_CARDS` | `HERO_CARDS` | Deduplicated by card string |
| `DEAL_BOARD` | `DEAL_COMMUNITY` | When cards present |
| `DEAL_NOTIFY` | `DEAL_COMMUNITY` | When visible cards present |
| `POT_UPDATE` | `POT_UPDATE` | When non-zero pot |
| `POT_AWARD` | `POT_AWARD` | Always |
| `HAND_SUMMARY` | `HAND_SUMMARY` | Always |
| `HAND_RESULT` | `HAND_RESULT` | Always |
| `HAND_BOUNDARY` | `HAND_END` | Deferred (see below) |
| All others | — | Filtered (timers, heartbeats, config, prompts) |

---

## Inferred Events

28 inferred events were emitted for the current session. All are `PLAYER_ACTION` with `inferred: true`.

### Detection Rule

When `ROUND_TRANSITION` fires with `roundId=10` for a seat, and no `ACTION` event follows for that seat before the next `ROUND_TRANSITION` or collect sweep:

| `betToCall` | Inferred action |
|-------------|-----------------|
| > 0 | `FOLD` — player faced a bet and was skipped |
| = 0 | `CHECK` — player had no bet to call and was skipped |

### Known Ambiguity: CHECK vs Already-Folded

`roundId=10` with `betToCall=0` is ambiguous. It could mean:

1. **Check**: Player actively checked (no bet facing them).
2. **Already folded**: Player folded on a prior street; the server skips them with `roundId=10, betToCall=0`.
3. **Skipped**: Player is all-in and has no action available.

In hand #260272188638, three players received `roundId=10, betToCall=0` during preflop after a raise to 150c. They had already called or were expected to fold — the `betToCall=0` doesn't match the game state where facing a 150c raise. These were likely folds, but the emitter classified them as `CHECK` because `betToCall=0`.

**Current behavior**: Emits `CHECK` when `betToCall=0`, `FOLD` when `betToCall>0`. This is the conservative interpretation.

**To resolve**: Cross-reference with `HAND_RESULT` text. A player who "Loses main pot and mucks cards" must have folded (or lost at showdown). Comparing inferred CHECKs against HAND_RESULT text could reclassify them as FOLDs post-hoc.

---

## Event Ordering: HAND_END Deferral

In the wire protocol, `HAND_BOUNDARY` (0x6e) fires BEFORE `HAND_RESULT` (0x7d):

```
Wire order:   POT_AWARD → HAND_SUMMARY → HAND_BOUNDARY → [next hand setup] → HAND_RESULT
Emitted order: POT_AWARD → HAND_SUMMARY → HAND_RESULT → HAND_END
```

The emitter defers `HAND_END` emission until the next `HAND_START` arrives (or end of stream). This ensures `HAND_RESULT` is always grouped with its hand, and `HAND_END` is always last.

The `_source.frameIdx` on `HAND_END` still points to the original `HAND_BOUNDARY` frame for traceability.

---

## Traceability

Every emitted event includes `_source`:

```json
{
  "_source": {
    "frameIdx": 139,
    "opcode": "0x77",
    "ts": 886379.405364
  }
}
```

For inferred events:

```json
{
  "_source": {
    "frameIdx": 172,
    "opcode": "0x72",
    "ts": 886416.392345,
    "inferred": true
  }
}
```

`frameIdx` maps back to the line number (0-indexed) in `websocket.jsonl` and `decoded-events.jsonl`.

---

## HAND_START Enrichment

`HAND_START` includes a snapshot of all players and stacks at the moment the hand begins. This is derived from accumulated `PLAYER_UPDATE` state, not from a single source frame.

```json
{
  "type": "HAND_START",
  "players": {
    "0": { "name": "Bandifull", "stack": 2005, "country": "AT" },
    "1": { "name": "Skurj_poker", "stack": 800, "country": "GB" }
  }
}
```

---

## PLAYER_STATE Suppression

`PLAYER_UPDATE` events fire ~150 times per session (most frequent decoded event type). They are noisy — one fires after every action, blind post, stack change, and hand reset.

The emitter does NOT emit `PLAYER_STATE` normalized events. Instead:
- Player state is tracked internally for name/stack lookups.
- `HAND_START` includes a full player snapshot.
- `STACK_UPDATE` events update internal state silently.

If downstream consumers need per-action stack deltas, they can derive them from `PLAYER_ACTION.delta` and the starting stacks in `HAND_START`.

---

## POT_UPDATE Filtering

`POT_UPDATE` is emitted only when the main pot total (amount + pending) is non-zero. Zero-pot updates (post-collection resets) are filtered.

44–51 `POT_UPDATE` events are emitted per session. These are intentionally verbose — downstream consumers can subsample to street boundaries or final pot if they want less granularity.

---

## Per-Hand Files

Each hand is written to `hands/hand-{handId}.jsonl`. These are subsets of the combined `normalized-hand-events.jsonl` — same data, just pre-split for convenience.

The `TABLE_SNAPSHOT` event has `handId` matching the hand that was in progress when the observer joined. It appears in that hand's file.

Events emitted before the first `HAND_START` (like `TABLE_SNAPSHOT`) use the `handId` from the snapshot.

---

## Unresolved Items

### 1. Inferred fold/check ambiguity (active)
See the CHECK vs Already-Folded section above. Fixable with HAND_RESULT cross-reference — not yet implemented.

### 2. Showdown path (blocked)
No showdown hands in this session. `HAND_SUMMARY.showdown` is always `false`, `handRank` and `winCards` are always null. The emitter passes these through as-is.

### 3. Side pot routing
One hand produced multi-entry `POT_UPDATE` events. The emitter passes all pot entries through. `POT_AWARD.potIndex` could route to specific pots, but only index 0 was observed.

### 4. Street labeling for inferred actions
Inferred actions inherit the `street` value at the time of emission. If the inferred fold happened during a collect sweep (between streets), the street label may be the old street rather than the new one. This is cosmetic — the `_source.frameIdx` is authoritative.

### 5. Hero action response (0x7a)
The client sends `0x7a` when the hero acts. This is not decoded or emitted. It could be used to confirm which actions were hero's vs. observed opponents'. Low priority.
