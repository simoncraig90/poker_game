# Backlog: Showdown Polish (SD-1 through SD-3)

Source: PHASE8_CLOSURE.md. Non-blocking. Address opportunistically.

---

## SD-1: Hand list winning hand rank

**What**: Hand list (History tab) shows `[SD]` tag but not the winning hand name.
**Fix**: Add `handRank` field to GET_HAND_LIST response (read from HAND_SUMMARY events). Render in `renderHandList()`.
**Files**: `src/api/session.js` (_getHandList), `client/table.js` (renderHandList).
**Effort**: ~30 min.

## SD-2: Archived hand detail formatting

**What**: Archived hand timeline is plain monospace text. No card coloring, no structured layout.
**Fix**: Render `formatTimeline()` output as HTML instead of preformatted text. Add card suit coloring.
**Files**: `client/table.js` (loadSessionHandDetail, renderHandDetail, formatTimeline → formatTimelineHtml).
**Effort**: ~1 hr.

## SD-3: Late-joining client mid-showdown

**What**: A client connecting after SHOWDOWN_REVEAL but before HAND_END doesn't see revealed cards.
**Fix**: Cache last SHOWDOWN_REVEAL in the server's session state. Include in welcome message when a hand is in SHOWDOWN/SETTLING phase.
**Files**: `src/server/ws-server.js` (cache reveal, include in welcome), `client/table.js` (read reveal from welcome).
**Effort**: ~1 hr.
