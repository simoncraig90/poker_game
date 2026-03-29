# Replay UI Minimum Scope

Thinnest hand history review capability in the browser client.

---

## What It Is

A "History" tab in the right panel (alongside the existing "Events" log) that lists completed hands and lets you click one to see its action timeline.

This is NOT a full replay player with scrubbing. It is a read-only text timeline — the browser equivalent of `replay-timeline-{id}.txt`.

---

## UI Layout

The right panel gets two tabs:

```
┌─────────────────┐
│ [Events] [History] │
├─────────────────┤
│                 │  ← tab content area
│                 │
└─────────────────┘
```

### Events Tab (existing)

Unchanged. Shows live event feed.

### History Tab (new)

Two states:

**Hand list** (default):
```
Hand #1  Alice wins 10c
Hand #2  Bob wins $3.09
Hand #3  Charlie wins 25c
```

Each row is clickable.

**Hand detail** (after clicking):
```
← Back to list

Hand #2 | Button: Seat 0 (Alice)
Stacks: Alice $20 | Bob $8 | Charlie $10

Bob posts SB 5c
Charlie posts BB 10c
Alice raises to 50c
Bob calls 50c
Charlie folds

--- FLOP [9h 2h 4s] ---
Alice bets 30c
Bob folds

Alice wins $1.15
Takes down main pot.
```

This is built from GET_HAND_EVENTS: the client formats the events into a readable timeline, same logic as `replay-normalized-hand.js` but in JS in the browser.

---

## Server Support

Uses the two new commands from PLAY_LOOP_HARDENING_SCOPE.md:
- `GET_HAND_LIST` → renders the hand list
- `GET_HAND_EVENTS` → renders the hand detail

No new server endpoints beyond what hardening already adds.

---

## Implementation

### Client-Side Timeline Formatter

```javascript
function formatHandTimeline(events) → string[]
```

Takes an array of normalized events for one hand. Returns an array of text lines. Logic mirrors `replay-normalized-hand.js` processHandStart/processBlindPost/etc but produces strings instead of writing files.

Reuses the same `c$()` helper already in `table.js`.

### Tab Switching

Two divs in the log panel, toggled by tab buttons. Active tab has a highlighted border-bottom. Simple CSS class toggle — no router.

---

## What This Is NOT

- Not a visual replay with card animations
- Not a hand replayer with forward/back scrubbing
- Not searchable or filterable
- Not exportable (the JSONL file on disk serves that purpose)
- Not showing hands from previous sessions (only current server session)

---

## What Stays Debug-First

- Text-only hand display (no cards, no table rendering)
- Monospace font
- No formatting beyond indentation and line breaks
- Inferred events shown with `{inferred}` tag, same as replay-timeline.txt
