# Phase 7 Implementation Notes

---

## What Changed

### src/server/protocol.js
- `formatWelcome()` now accepts a `recoveryInfo` parameter: `{ recovered: bool, voidedHands: string[] }`
- Welcome message includes `recovered` and `voidedHands` fields

### src/server/ws-server.js
- Tracks `wasRecovered` and `voidedHands` on startup
- Scans event log for void HAND_END events to populate `voidedHands`
- Passes recovery info to every welcome message
- `GET_HAND_LIST` with `payload.sessionId`: loads archived session from disk, returns hand summaries including voided hand markers
- `GET_HAND_EVENTS` with `payload.sessionId`: loads specific hand events from archived session on disk
- `ARCHIVE_SESSION`: sets up welcome listener before archiving to avoid race

### client/index.html
- Added Sessions tab (third tab in right panel)
- Added `#sessions-panel` with list and detail views
- Added `#recovery-banner` div for recovery indicator
- Added CSS for sessions tab, voided markers, recovery banner, archive button

### client/table.js
- Tracks `voidedHandIds` set from welcome message
- `showRecoveryBanner()`: teal banner showing recovered session info + voided hands, auto-dismiss 8s
- `logEvent()`: RECOVERY events styled distinctly, void events marked
- `switchTab('sessions')`: loads session list via GET_SESSION_LIST
- `renderSessionsList()`: shows active (green dot) / complete (grey dot) sessions with metadata
- `browseSession()`: loads hand list for a completed session
- `renderSessionHandList()`: shows hands with VOIDED markers, click to view detail
- `loadSessionHandDetail()`: fetches hand events from archived session, formats timeline
- `archiveSession()`: confirm dialog, sends ARCHIVE_SESSION
- `formatTimeline()`: handles void HAND_END events with "[HAND VOIDED]" message
- Session ID visible in header (last 10 chars)

---

## Zero Engine Changes

No files in `src/engine/` were modified. All changes are in:
- `src/server/protocol.js` (welcome format)
- `src/server/ws-server.js` (recovery metadata, archived session browsing)
- `client/index.html` (layout)
- `client/table.js` (rendering)

---

## Test Results

| Suite | Checks | Status |
|-------|--------|--------|
| Phase 1: accounting | PASS | All hand tests |
| Phase 2: conformance | 25/25 | No hidden state |
| Phase 3: WS conformance | 31/31 | External conformance |
| Phase 5: E2E session | 38/38 | Full session |
| Phase 6: recovery | 52/52 | Persistence/recovery |
| **Phase 7: session browser** | **22/22** | **Recovery UX + archive** |

### Phase 7 Test Coverage

| Test | Checks | What It Proves |
|------|--------|----------------|
| T1: Fresh welcome | 3 | recovered=false, voidedHands=[] on new session |
| T2: Recovery welcome | 6 | recovered=true, state matches, players/stacks preserved |
| T3: Mid-hand void | 4 | voidedHands populated, voided hand ID visible |
| T4: Session list + archive + browse | 9 | List, archive, browse archived hands, archived hand events |

---

## Feature Summary

| Feature | Where |
|---------|-------|
| Recovery banner on reconnect | Table area, teal, 8s auto-dismiss |
| Voided hand markers | History tab + Sessions tab hand lists |
| Session list | Sessions tab, active (green) / complete (grey) |
| Browse archived session | Click completed session → hand list → hand detail |
| Archive & New | Button in Sessions tab, creates new session |
| Session ID in header | Last 10 chars of session ID |
| Recovery event log entries | Event log panel, styled distinctly |
