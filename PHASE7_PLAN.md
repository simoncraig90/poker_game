# Phase 7 Plan

**Status: COMPLETE** — Sessions tab, recovery UX, archive flow (22/22).

Make persistence and recovery visible and trustworthy to a human operator.

---

## Objective

Phase 6 built the persistence machinery — sessions survive restarts, mid-hand crashes are handled, archives are read-only. Phase 7 makes all of this visible in the browser so an operator can see it working, trust it, and use it.

No new engine work. No new persistence logic. This phase is entirely client + light server additions.

---

## Deliverables

### 7A: Session Browser

A "Sessions" tab in the right panel (alongside Events and History). Lists all sessions from disk. Shows status (active/complete), hands played, created date. Active session is highlighted. Completed sessions are clickable to view their hand history.

### 7B: Recovery Indicators

When the server recovered a session on startup, surface this in the client:
- Welcome message includes `recovered: true` flag when applicable
- Event log shows "Session recovered" entry
- Voided hands marked visibly in the hand list ("Hand #3 VOIDED")
- Recovery banner at top of table on first render after recovery

### 7C: Session Controls

- "Archive & New" button in the Sessions tab or header: sends ARCHIVE_SESSION, shows fresh table
- Session info in header: shows session ID and whether recovered

### 7D: Manual Recovery Demo

A scripted demo flow that a human operator can follow to prove persistence/recovery works end-to-end in the browser.

---

## Implementation Tasks

### Task 1: Server — recovery flag in welcome

Add `recovered: bool` and `voidedHands: string[]` to the welcome message. The server already knows if it recovered (it printed to console); just pass it to the client.

### Task 2: Server — voided hands in hand list

GET_HAND_LIST should include voided hands (they have HAND_END events with `void: true`). Mark them distinctly.

### Task 3: Client — Sessions tab

Third tab in right panel. Fetches GET_SESSION_LIST on tab switch. Renders list with status badges.

### Task 4: Client — Recovery indicators

On welcome with `recovered: true`: show a brief info banner ("Recovered session {id}"). Show voided hands in History tab with a "VOIDED" badge.

### Task 5: Client — Archive button

Button in Sessions tab: calls ARCHIVE_SESSION, receives new welcome, re-renders.

---

## What Does NOT Change

- Engine modules (src/engine/*)
- Session dispatch logic (src/api/session.js)
- Storage module (src/api/storage.js)
- Event log format
- Recovery logic
- Existing Phase 1-6 tests
