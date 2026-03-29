# Engine Phase 5 Plan

Harden the playable loop and add minimal review/replay capability.

---

## Objective

Make the browser table client stable enough for repeated local play sessions, add hand history review, and surface hand-end summaries so a player can understand what happened without reading raw JSON.

---

## What Phase 4 Has

Working but rough:
- Seats, stacks, cards, pot, board render correctly
- Action buttons enable/disable from legalActions
- Event log panel shows raw events
- Single hand can be played start to finish

What's missing or broken for repeated play:
- No hand-end summary visible in the table area (winner/pot just disappears)
- No way to review completed hands
- No hand counter or hand ID visible
- No "clear log" or "new session" flow
- Board cards persist after hand ends until next deal
- No keyboard shortcuts for common actions
- No error toast (errors only appear in event log)
- Server has no GET_HAND_EVENTS command over WS
- No way to list completed hands

---

## Phased Deliverables

### 5A: Play Loop Hardening (server + client)

1. Add `GET_HAND_EVENTS` and `GET_HAND_LIST` commands to server
2. Show hand result summary in table area after settlement (winner, pot, result text)
3. Clear board/bets/cards properly at hand end, show summary briefly
4. Show hand number in header
5. Add error toast (brief overlay, auto-dismiss)
6. Add keyboard shortcuts: F=fold, C=call, X=check, Enter=deal

### 5B: Hand History Panel (client)

1. Add "History" tab next to "Events" in the right panel
2. List completed hands (hand ID, winner, pot amount)
3. Click a hand to see its action timeline (like replay-timeline.txt but in browser)
4. Use GET_HAND_EVENTS to fetch per-hand events on demand

### 5C: End-to-End Browser Test

1. Automated test using the WS client that plays a full multi-hand session
2. Verifies: hand count, stack accounting, event log completeness
3. Verifies: GET_HAND_LIST and GET_HAND_EVENTS return correct data

---

## What Stays Debug-First

- All hole cards visible to all clients (no seat-based filtering)
- Raw event log panel stays as-is (developers need it)
- No animations, sounds, or card images
- No responsive/mobile layout
- Monospace font everywhere
- No player avatars

## What Stays Intentionally Simple

- CSS-positioned seats (no canvas)
- Full GET_STATE refresh after every event (no incremental apply)
- Single table per server
- No auth — anyone can control any seat
- prompt() dialogs for seat/buy-in

## What Must Be True Before Moving Beyond Phase 5

Before visual polish, solver integration, or training features:

1. Repeated multi-hand sessions run without errors or state corruption
2. Hand history is reviewable for any completed hand
3. Stack accounting is correct across 20+ consecutive hands
4. Event log from any browser session replays through replay-normalized-hand.js
5. The GET_STATE → render cycle has no stale-state bugs
6. Keyboard-driven play is possible (no mouse required for actions)
