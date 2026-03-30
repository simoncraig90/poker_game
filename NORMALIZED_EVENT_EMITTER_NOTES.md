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

### Detection Rule (Updated — F2 Fix Applied)

`roundId=10` universally means "this seat is skipped." The `betToCall` field is always 0 regardless of context. The emitter always emits **FOLD** for `roundId=10` — never CHECK. This was validated: every inferred FOLD player shows "Loses main pot and mucks cards" in HAND_RESULT, confirming they folded.

> Original rule (pre-fix): `betToCall > 0 → FOLD, betToCall = 0 → CHECK`. This was incorrect — betToCall is always 0 for roundId=10. See REPLAY_FIX_VALIDATION.md for details.

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

### 1. ~~Inferred fold/check ambiguity~~ (RESOLVED)
Fixed: roundId=10 always emits FOLD. See REPLAY_FIX_VALIDATION.md.

### 2. Showdown path (blocked)
No showdown hands in this session. `HAND_SUMMARY.showdown` is always `false`, `handRank` and `winCards` are always null. The emitter passes these through as-is.

### 3. Side pot routing
One hand produced multi-entry `POT_UPDATE` events. The emitter passes all pot entries through. `POT_AWARD.potIndex` could route to specific pots, but only index 0 was observed.

### 4. Street labeling for inferred actions
Inferred actions inherit the `street` value at the time of emission. If the inferred fold happened during a collect sweep (between streets), the street label may be the old street rather than the new one. This is cosmetic — the `_source.frameIdx` is authoritative.

### 5. Hero action response (0x7a)
The client sends `0x7a` when the hero acts. This is not decoded or emitted. It could be used to confirm which actions were hero's vs. observed opponents'. Low priority.
