# Capture Session Notes

> **Copy this file to `captures/<platform>/<YYYY-MM-DD>/session-notes.md` at the start of each session.**

## Session Info

| Field | Value |
|-------|-------|
| **Date** | YYYY-MM-DD |
| **Time (start)** | HH:MM |
| **Time (end)** | HH:MM |
| **Platform** | |
| **Platform URL** | |
| **Chrome version** | |
| **Viewport** | e.g., 1920x1080 |
| **Network** | e.g., home Wi-Fi, ~50ms latency |
| **Session goal** | e.g., Capture lobby load + join table flows |

---

## Pre-Session Checklist

- [ ] Session folder created: `captures/<platform>/<date>/`
- [ ] Subfolders created: `har/`, `ws/`, `storage/`, `perf/`, `screenshots/`, `dom/`, `console/`
- [ ] Dedicated Chrome profile open
- [ ] Cache cleared (if fresh-state capture)
- [ ] DevTools open, Preserve Log enabled, Disable Cache checked
- [ ] WebSocket logging snippet pasted in Console
- [ ] Screen recording started (if needed)
- [ ] Logged into platform

---

## Capture Log

Record each capture action with a timestamp. One entry per action.

### Entry Template

```
### [HH:MM:SS] — <action description>

**Flow:** lobby-load | join-table | buy-in | first-hand | leave-table | reconnect | settings
**Files saved:**
- har/lobby-load.har
- screenshots/lobby-initial-state.png

**Observations:**
- What happened
- What was unexpected
- Specific values noticed (e.g., "table list returned 47 entries")
- Timing noted (e.g., "lobby rendered in ~1.2s")

**Questions raised:**
- Anything unclear or needing follow-up
```

---

### [HH:MM:SS] — _(describe action)_

**Flow:**
**Files saved:**
-

**Observations:**
-

**Questions raised:**
-

---

### [HH:MM:SS] — _(describe action)_

**Flow:**
**Files saved:**
-

**Observations:**
-

**Questions raised:**
-

---

### [HH:MM:SS] — _(describe action)_

**Flow:**
**Files saved:**
-

**Observations:**
-

**Questions raised:**
-

---

_(Copy more entry blocks as needed during the session.)_

---

## Post-Session Summary

### Flows Completed

| Flow | Status | Confidence | Notes |
|------|--------|------------|-------|
| Lobby load | | | |
| Join table | | | |
| Buy-in | | | |
| First hand | | | |
| Leave table | | | |
| Reconnect | | | |
| Settings/storage | | | |

### Key Findings

1. _Most important thing learned this session_
2. _Second most important_
3. _Third_

### Surprises / Unexpected Behavior

- _Anything that contradicted assumptions_

### Questions for Next Session

- _What to investigate next time_

### Files Inventory

List all files created this session:

```
captures/<platform>/<date>/
├── har/
│   └── ...
├── ws/
│   └── ...
├── storage/
│   └── ...
├── perf/
│   └── ...
├── screenshots/
│   └── ...
├── dom/
│   └── ...
├── console/
│   └── ...
└── session-notes.md
```

### Time Spent

| Activity | Duration |
|----------|----------|
| Setup | min |
| Captures | min |
| Review / notes | min |
| **Total** | **min** |
