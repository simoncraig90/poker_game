# Phase 9 Plan: Identity + Study Features

**Status: PLANNING**

**Motivation**: The engine game loop is complete (Phase 8 closed). The event log is event-sourced, reconstructable, and crash-safe. The research value of this platform depends on tracking who did what across sessions and studying the decisions made. Without durable identity, every "Alice" is a new person each session. Without study tools, the event log is write-only data.

---

## A. Revised Identity Model

### Terminology Decision

The JS engine will adopt the canonical actor/participation vocabulary directly in the data model. No aliases.

| Canonical Term | JS Field Name | What It Is |
|---------------|--------------|------------|
| actor_id | `actorId` | Persistent identity across sessions. UUID. |
| participation | (derived) | An actor's involvement in one hand. Not stored — computed from events by joining HAND_START players map with downstream events. |
| session_id | `sessionId` | Already exists. Unchanged. |
| hand_id | `handId` | Already exists. Unchanged. |

**Why `actorId` and not `playerId`**: The canonical model uses "actor" because the platform is a research tool, not a game client. The entity being tracked is an actor in a study, not a "player" in the casual sense. Using `actorId` makes the research framing explicit and avoids collisions with the existing `seat.player` field (which is a display object, not an identity).

### Actor Entity

```javascript
Actor {
  actorId: string,        // UUID, stable across all sessions
  name: string,           // display name (mutable, not unique)
  createdAt: string,      // ISO timestamp
  notes: string,          // operator notes ("aggressive preflop", "new to poker")
}
```

### Participation (Derived, Not Stored)

A participation is an actor's presence in one hand. It is computed on-demand from the event log by:

1. Read HAND_START → get `players[seat].actorId`, `players[seat].stack`
2. Read PLAYER_ACTION events for that handId and seat → action sequence
3. Read HAND_SUMMARY / HAND_RESULT → outcome
4. Read SHOWDOWN_REVEAL → hand shown (if applicable)

```javascript
Participation {
  actorId: string,
  sessionId: string,
  handId: string,
  seat: number,
  position: string,         // "BTN", "SB", "BB", "UTG", "MP", "CO" (derived from button + seat)
  startStack: number,
  endStack: number,
  actions: [{
    street: string,
    action: string,
    amount: number,
  }],
  result: "won" | "lost" | "split",
  wentToShowdown: boolean,
  handRank: string | null,  // from SHOWDOWN_REVEAL
  potWon: number,           // 0 if lost
  voluntaryPutMoney: boolean, // true if acted beyond forced blinds preflop
  preflopRaised: boolean,
}
```

This is a query result. The event log is the sole source of truth. No denormalized storage.

### Linkage Hierarchy

```
Actor (persistent, disk)
  └── Session participation (derived: which sessions did this actor join?)
       └── Hand participation (derived: HAND_START.players[seat].actorId)
            └── Actions (derived: PLAYER_ACTION events by seat + handId)
            └── Outcome (derived: HAND_RESULT, SHOWDOWN_REVEAL)
```

---

## B. Event Linkage Design

### Additive Changes Only

No existing event fields are modified or removed. New fields are added.

**SEAT_PLAYER event** — add `actorId`:
```javascript
// Before:
{ type: "SEAT_PLAYER", seat, player: "Alice", buyIn: 1000, country: "XX" }
// After:
{ type: "SEAT_PLAYER", seat, player: "Alice", buyIn: 1000, country: "XX", actorId: "act-abc123" }
```

**HAND_START event** — add `actorId` to each player in the players map:
```javascript
// Before:
players: { 0: { name: "Alice", stack: 1000, country: "XX" } }
// After:
players: { 0: { name: "Alice", stack: 1000, country: "XX", actorId: "act-abc123" } }
```

**All other events**: Unchanged. PLAYER_ACTION, POT_AWARD, HAND_RESULT, SHOWDOWN_REVEAL carry `seat`, which resolves to `actorId` via the HAND_START snapshot for that hand. No redundant actorId on every event.

**Backwards compatibility**: Events without `actorId` are treated as anonymous. The query layer handles null actorId gracefully. Existing sessions remain readable.

### Engine Integration Points

| File | Change |
|------|--------|
| `src/engine/table.js` | `seat.player` gains `actorId` field (alongside existing `name`, `country`, `avatarId`) |
| `src/engine/orchestrator.js` | `playerMap` construction includes `actorId` from `seat.player.actorId` |
| `src/engine/events.js` | `seatPlayer()` factory accepts and emits `actorId` |
| `src/api/session.js` | `_seatPlayer()` resolves actorId before seating |
| `src/api/reconstruct.js` | SEAT_PLAYER handler stores `actorId` on reconstructed seat |

---

## C. MVP Storage Choice and Migration Note

### MVP: JSON-on-Disk

```
data/
  sessions/{sessionId}/     (existing, unchanged)
    meta.json
    events.jsonl
  actors/                   (new)
    {actorId}.json
```

Each actor is one file:
```json
{
  "actorId": "act-abc123",
  "name": "Alice",
  "createdAt": "2026-03-30T12:00:00Z",
  "notes": "Aggressive preflop, tightens up postflop"
}
```

**Why JSON files**: The actor registry is a small, infrequently-written dataset (tens of actors, not thousands). Directory listing is the index. No query joins across actors and events are needed at write time — joins happen at read time when computing participation. This is sufficient for a local research tool.

**Performance bounds**: Actor list read = readdir + N file reads. Fast for N < 200. Cross-session hand query = scan all session event logs. Fast for < 50 sessions × 200 hands = 10k events. Stats computation = single event log scan. No index needed.

### Future Migration Path

If the project outgrows JSON files:
1. SQLite is the natural next step. Single file, no server, embedded.
2. Actor table + events table with indexes on actorId, sessionId, handId.
3. Migration script: read JSON files → INSERT into SQLite.
4. The event log format (JSONL) can remain as the write-ahead log; SQLite becomes the query index.

This migration is not needed now. The plan is structured so the query interface (`ActorRegistry.get()`, `queryHands()`, `getActorStats()`) is an abstraction boundary. Swapping the storage backend doesn't change the API.

---

## D. Revised Phase 9 Slice Plan

### Slice 9A: Actor Registry + Event Linkage

**Goal**: Establish durable actor identity. Every seated player has an actorId. Every HAND_START snapshot includes actorId per participant. Existing sessions without actorId continue to work.

**New files**:
- `src/api/actors.js` — ActorRegistry class

**Modified files**:
- `src/engine/table.js` — `sitDown()` accepts and stores `actorId` on `seat.player`
- `src/engine/events.js` — `seatPlayer()` emits `actorId`; `handStart()` player map construction updated in orchestrator
- `src/engine/orchestrator.js` — includes `seat.player.actorId` in playerMap
- `src/api/session.js` — `_seatPlayer()` resolves or creates actor before seating
- `src/api/reconstruct.js` — SEAT_PLAYER handler stores actorId
- `src/api/commands.js` — add CREATE_ACTOR, GET_ACTOR, LIST_ACTORS, UPDATE_ACTOR

**ActorRegistry interface**:
```javascript
class ActorRegistry {
  constructor(dataDir)                 // data/actors/
  create(name, notes?)                 // → Actor { actorId, name, createdAt, notes }
  get(actorId)                         // → Actor | null
  list()                               // → Actor[]
  update(actorId, { name?, notes? })   // → updated Actor
  findByName(name)                     // → Actor[] (name not unique)
}
```

**SEAT_PLAYER command resolution**:
```
payload: { seat, name, buyIn, country, actorId? }

if actorId provided and valid → use it
if actorId omitted:
  exact = registry.findByName(name)
  if exact.length === 1 → use exact[0].actorId
  if exact.length === 0 → auto-create actor, use new actorId
  if exact.length > 1 → auto-create (ambiguous name, don't guess)
```

**Test plan (test/identity.test.js)**:

| # | Test | What It Proves |
|---|------|----------------|
| T1 | Create actor → get → fields match | Registry CRUD works |
| T2 | List actors → all appear | Directory listing works |
| T3 | Update name → persists on re-read | Mutation writes to disk |
| T4 | findByName → correct matches | Name lookup works |
| T5 | findByName ambiguous → multiple results | Doesn't silently pick one |
| T6 | SEAT_PLAYER with actorId → event has actorId | Explicit linkage |
| T7 | SEAT_PLAYER without actorId, unique name → auto-resolves | Name-based lookup |
| T8 | SEAT_PLAYER without actorId, new name → auto-creates | Auto-registration |
| T9 | HAND_START.players[seat] has actorId | Hand snapshot includes identity |
| T10 | reconstructState with actorId events → actorId on seats | Reconstruct compatibility |
| T11 | reconstructState without actorId events → null, no crash | Backwards compat |
| T12 | Same actorId across 2 sessions → consistent | Cross-session durability |
| T13 | CREATE_ACTOR / GET_ACTOR / LIST_ACTORS commands work | Command dispatch |

**Done when**: T1–T13 pass. Actors persist on disk. SEAT_PLAYER and HAND_START events carry actorId. Existing event logs without actorId don't break.

---

### Slice 9B: Hand Query + Actor Stats

**Goal**: Cross-session hand search and per-actor statistics. The operator can ask "show me Alice's showdown hands" and "what is Alice's VPIP".

**New files**:
- `src/api/query.js` — hand query and stats computation

**Modified files**:
- `src/api/commands.js` — add QUERY_HANDS, GET_ACTOR_STATS
- `src/server/ws-server.js` — dispatch new commands (read-only, no session mutation)

**Query interface**:
```javascript
/**
 * @param {SessionStorage} storage — to enumerate and read sessions
 * @param {object} filters
 * @param {string} [filters.actorId] — only hands with this actor
 * @param {string} [filters.sessionId] — only this session
 * @param {boolean} [filters.showdown] — only showdown hands
 * @param {string} [filters.position] — "BTN", "SB", "BB", etc.
 * @param {number} [filters.minPot] — minimum pot size
 * @returns {HandSummary[]}
 */
function queryHands(storage, filters) { ... }
```

**HandSummary (query result)**:
```javascript
{
  sessionId, handId, actorId, seat, position,
  startStack, potWon, result, wentToShowdown, handRank,
  totalPot, showdown, winner, board,
}
```

**Actor stats interface**:
```javascript
/**
 * @param {SessionStorage} storage
 * @param {string} actorId
 * @param {string} [sessionId] — scope to one session (optional)
 * @returns {ActorStats}
 */
function getActorStats(storage, actorId, sessionId?) { ... }
```

**ActorStats shape — study-useful metrics**:

| Metric | Semantic Definition | Why It Matters for Study |
|--------|-------------------|------------------------|
| `handsDealt` | Hands where actor was in HAND_START players map | Sample size |
| `vpip` | % of hands where actor voluntarily put chips in preflop (excludes walks and blind-only) | Measures looseness — how often they choose to play |
| `pfr` | % of hands where actor raised or re-raised preflop | Measures aggression preflop |
| `wtsd` | % of non-folded-preflop hands that reached showdown | Measures postflop commitment |
| `wsd` | % of showdowns won | Measures showdown quality |
| `aggFactor` | (bets + raises) / calls across all streets | Overall aggression |
| `totalInvested` | Sum of all chips put in across all hands | Volume |
| `totalWon` | Sum of all POT_AWARD amounts received | Volume |
| `netResult` | totalWon - totalInvested | Bottom line |
| `avgPotWon` | Mean pot size when won (0 if never won) | Scale of wins |
| `handsByPosition` | `{ BTN: n, SB: n, BB: n, ... }` | Position distribution |

**Metric computation rules**:
- **VPIP**: A hand counts as "voluntarily put money in" if the actor made any CALL, BET, or RAISE action preflop (not just posting blinds). Walking the blinds (everyone folds to BB) does NOT count as VPIP.
- **PFR**: A hand counts as "preflop raised" if the actor's first voluntary preflop action is RAISE (or BET in heads-up where that's the open). Calling does not count.
- **WTSD**: Denominator is hands where actor did not fold preflop. Numerator is those that reached a SHOWDOWN_REVEAL with the actor still in.
- **WSD**: Denominator is showdowns reached. Numerator is showdowns where actor received a POT_AWARD.
- **aggFactor**: Count of (BET + RAISE) actions / count of CALL actions. If calls = 0, return Infinity or null.
- **Position**: Derived from button seat and actor seat using standard 6-max position labels.

**Hand filters — minimum useful set**:

| Filter | Type | What |
|--------|------|------|
| `actorId` | string | Hands with this actor |
| `sessionId` | string | Hands from this session |
| `showdown` | boolean | Only showdown / only no-showdown |
| `position` | string | Actor was in this position |
| `minPot` | number | Pot ≥ this amount |
| `result` | "won" \| "lost" \| "split" | Actor's outcome |

**Test plan (test/query.test.js)**:

| # | Test | What |
|---|------|------|
| T14 | queryHands no filter → all hands from all sessions | Full scan works |
| T15 | queryHands by actorId → only that actor's hands | Actor filter |
| T16 | queryHands by sessionId → only that session | Session filter |
| T17 | queryHands showdown=true → only showdown hands | Showdown filter |
| T18 | queryHands combined filters → intersection | Multi-filter |
| T19 | getActorStats → correct handsDealt | Basic counting |
| T20 | VPIP: actor calls preflop → counts as VPIP | VPIP semantics |
| T21 | VPIP: actor only posts blind, folds → NOT VPIP | VPIP exclusion |
| T22 | PFR: actor raises preflop → counts | PFR semantics |
| T23 | PFR: actor calls preflop → does NOT count | PFR exclusion |
| T24 | WTSD: actor folds flop → not counted | WTSD denominator |
| T25 | WTSD: actor reaches showdown → counted | WTSD numerator |
| T26 | WSD: actor wins at showdown → counted | WSD semantics |
| T27 | aggFactor: 3 raises, 1 call → 3.0 | aggFactor computation |
| T28 | netResult: cross-session aggregation correct | Multi-session |
| T29 | Stats for anonymous actor → empty/zero | Backwards compat |
| T30 | Position derivation: 6-max positions correct | Position labels |

**Done when**: T14–T30 pass. queryHands returns correct filtered results. getActorStats computes correct metrics with precise semantics.

---

### Slice 9C: Study UI (Minimal)

**Goal**: Operator can pick actors, browse filtered hands, and view stats. No animation, no graphical charts. Text-oriented, study-focused.

**Modified files**:
- `client/index.html` — add Actors tab (fourth tab in right panel), filter controls on History tab
- `client/table.js` — actor picker on seat click, actors tab rendering, filtered hand list, stats display
- `src/server/ws-server.js` — dispatch QUERY_HANDS, GET_ACTOR_STATS, actor CRUD commands

**UI additions**:

1. **Actors tab**: List all registered actors. Click to view profile (name, notes, stats summary). Edit button for name/notes.

2. **Seat dialog**: When clicking empty seat, show list of existing actors + "New actor" option. Selecting an actor fills in name and actorId.

3. **History tab filter bar**: Dropdowns for actor and session. Toggle for showdown-only. Applied filters call QUERY_HANDS.

4. **Actor stats panel**: When viewing an actor profile, show the stats table (handsDealt, VPIP, PFR, WTSD, WSD, aggFactor, netResult). One row per stat, plain text.

**Test plan (test/study-client.test.js)**:

| # | Test | What |
|---|------|------|
| T31 | CREATE_ACTOR over WS → actor created | WS dispatch |
| T32 | LIST_ACTORS over WS → all actors returned | WS dispatch |
| T33 | QUERY_HANDS over WS → filtered results | WS dispatch |
| T34 | GET_ACTOR_STATS over WS → correct stats | WS dispatch |
| T35 | Seat with actorId over WS → event has actorId | End-to-end |

**Done when**: T31–T35 pass. Actor management, hand query, and stats are accessible via WS protocol. Browser has actor picker, filter bar, and stats display.

---

## E. Risks Introduced by the Identity Layer

| Risk | Severity | Mitigation |
|------|----------|------------|
| actorId in events is a new coupling point — if actor files are deleted, events reference a missing actor | Low | Query layer treats missing actor as anonymous. Display shows actorId as fallback. No crash. |
| Auto-create on SEAT_PLAYER creates orphan actors if operator misspells name | Low | LIST_ACTORS + UPDATE_ACTOR let operator merge/rename. Could add DELETE_ACTOR later. |
| Event log size grows slightly (actorId per SEAT_PLAYER + HAND_START) | Negligible | UUID is ~36 bytes per event. Irrelevant at this scale. |
| Name-based auto-resolve picks wrong actor on ambiguous names | Medium | When ambiguous (multiple actors with same name), auto-create instead of guessing. Operator can provide explicit actorId. |
| Cross-session query scans all event logs — slow for large session counts | Low | Acceptable for < 50 sessions. If needed later, add SQLite index (migration path documented above). |
| Backwards-incompatible if we later change actorId format | Low | UUID format is stable. No reason to change. |

No risk blocks implementation. The highest-severity risk (ambiguous name resolution) is handled by the "create on ambiguity" rule.

---

## What This Phase Does NOT Include

- Authentication or authorization
- Network/remote identity
- HUD (real-time stats overlay during play)
- Advanced derived stats (3-bet%, fold-to-cbet%, c-bet%, etc.)
- Hand range analysis or equity calculations
- Training mode or coaching features
- Graphical charts or visualizations

These build on the identity + query foundation established here.
