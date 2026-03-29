# Research Capture Plan

## Purpose

Systematically observe and document the observable behavior of browser-based poker platforms (PokerStars Play, 888poker, etc.) to derive a functional specification for a private recreation. No proprietary code is extracted — only publicly observable network traffic, DOM structure, timing, and UX flows.

---

## Capture Environment

| Component | Details |
|-----------|---------|
| **Workstation** | Windows desktop, Chrome (latest stable) |
| **Browser profile** | Dedicated Chrome profile (`poker-research`) — clean state, no extensions that interfere |
| **DevTools** | Network, Console, Application, Performance, Elements panels |
| **Recording** | OBS or ShareX for screen capture; optional FFmpeg for headless later |
| **Automation (later)** | Playwright on Node.js for scripted replay and timing measurement |
| **Storage** | All captures saved under `C:\poker-research\captures\<platform>\<session-date>\` |

---

## Capture Toolchain

### 1. Chrome DevTools — Network Panel (HAR Export)

**What it captures:** Every HTTP/HTTPS request and response, including headers, payloads, timing waterfall, and status codes.

**Workflow:**
1. Open DevTools (`F12`) > Network tab.
2. Check **Preserve log** (survives navigations).
3. Check **Disable cache** (forces full loads, reveals actual payload sizes).
4. Perform the target flow (e.g., lobby load).
5. Right-click the request list > **Save all as HAR with content**.
6. Save to `captures/<platform>/<date>/har/<flow-name>.har`.

**What to look for:**
- API base URLs and versioning patterns (e.g., `/api/v2/lobby`).
- Authentication headers (token format, refresh patterns).
- Polling intervals vs. long-lived connections.
- Content types (JSON, protobuf, msgpack).
- Response sizes and compression.
- CDN vs. origin requests.
- Cache headers (`Cache-Control`, `ETag`, `Last-Modified`).

---

### 2. Chrome DevTools — WebSocket Inspection

**What it captures:** WebSocket handshake, frames (sent/received), and close events.

**Workflow:**
1. Network tab > filter by `WS`.
2. Click the WebSocket connection to open the **Messages** sub-panel.
3. Observe message flow during the target action.
4. Copy messages manually or use Console snippet to log them:

```javascript
// Paste in Console before starting the flow
(function() {
  const origSend = WebSocket.prototype.send;
  WebSocket.prototype.send = function(data) {
    console.log('[WS SEND]', new Date().toISOString(), data);
    return origSend.call(this, data);
  };

  const origWS = window.WebSocket;
  window.WebSocket = function(...args) {
    const ws = new origWS(...args);
    ws.addEventListener('message', (e) => {
      console.log('[WS RECV]', new Date().toISOString(), e.data);
    });
    ws.addEventListener('close', (e) => {
      console.log('[WS CLOSE]', new Date().toISOString(), e.code, e.reason);
    });
    return ws;
  };
  window.WebSocket.prototype = origWS.prototype;
})();
```

5. Save Console output to `captures/<platform>/<date>/ws/<flow-name>.log`.

**What to look for:**
- Message format (JSON, binary, custom framing).
- Message types / opcodes (e.g., `{"type": "DEAL", ...}`).
- Heartbeat / ping-pong intervals.
- Sequence numbers or acknowledgment patterns.
- Reconnection behavior (does the client re-subscribe? resume from sequence?).
- Latency between send and server echo.

---

### 3. Chrome DevTools — Application Panel (Storage Inspection)

**What it captures:** Cookies, localStorage, sessionStorage, IndexedDB, Cache API entries.

**Workflow:**
1. Application tab > Storage section in the left sidebar.
2. Before starting a flow: screenshot or export current state.
3. Perform the flow.
4. After the flow: screenshot or export new state, diff against prior.
5. Save snapshots to `captures/<platform>/<date>/storage/<flow-name>-before.json` and `-after.json`.

**Console helpers:**
```javascript
// Dump localStorage
JSON.stringify(Object.fromEntries(Object.entries(localStorage)), null, 2);

// Dump sessionStorage
JSON.stringify(Object.fromEntries(Object.entries(sessionStorage)), null, 2);

// Dump cookies
document.cookie;
```

**What to look for:**
- Session tokens and their TTL.
- User preferences stored client-side.
- Table state or hand history cached locally.
- Service worker registrations and cached assets.

---

### 4. Chrome DevTools — Performance Panel (Timing)

**What it captures:** Frame rendering, scripting, layout, paint events with millisecond precision.

**Workflow:**
1. Performance tab > click Record.
2. Perform the target flow (keep it short — 5-15 seconds max per recording).
3. Stop recording.
4. Analyze the flame chart for:
   - Time from click to first visible response.
   - Time from server message to DOM update.
   - Animation durations (card deal, chip movement, timer countdown).
5. Screenshot the relevant flame chart section.
6. Save to `captures/<platform>/<date>/perf/<flow-name>.png` (and `.json` profile if exported).

---

### 5. Screenshots and Screen Recording

**What it captures:** Visual layout, animations, transitions, responsive breakpoints.

**Workflow:**
1. Use ShareX or `Win+Shift+S` for still screenshots.
2. Use OBS (or ShareX GIF/MP4) for animations and transitions.
3. Capture at standard breakpoints: 1920x1080, 1366x768, 1024x768.
4. Save to `captures/<platform>/<date>/screenshots/<flow-name>-<description>.png`.

**What to document per screenshot:**
- Viewport size.
- What action triggered this state.
- Any visible timers, counters, or status indicators.
- Layout grid (how many tables visible, sidebar width, chat panel, etc.).

---

### 6. DOM / Element Inspection

**What it captures:** Component structure, CSS class naming conventions, data attributes, accessibility markup.

**Workflow:**
1. Elements tab > inspect key UI components.
2. Note: tag structure, class naming convention (BEM? utility? CSS modules hash?).
3. Look for `data-*` attributes that reveal state (e.g., `data-seat="3"`, `data-card="Ah"`).
4. Check for canvas/WebGL vs. DOM rendering.
5. Copy outer HTML of key sections to `captures/<platform>/<date>/dom/<component-name>.html`.

---

### 7. Console Log Inspection

**What it captures:** Client-side errors, debug output left in production, feature flags.

**Workflow:**
1. Console tab > set filter to **All levels** (including Verbose).
2. Perform the flow.
3. Note any errors, warnings, or info-level logs.
4. Save to `captures/<platform>/<date>/console/<flow-name>.log`.

---

## Optional / Phase 2 Tooling

### Playwright Scripted Capture

For repeatable, automated captures once initial manual passes are done.

```
npm init -y
npm install playwright
```

Use Playwright to:
- Automate login and flow navigation.
- Intercept and log all network requests programmatically.
- Capture HAR files via `page.routeFromHAR()` / `browserContext.routeFromHAR()`.
- Take timed screenshots at each state transition.
- Measure performance marks and timing.

### FFmpeg Screen Capture

For frame-accurate timing analysis of animations:
```bash
ffmpeg -f gdigrab -framerate 60 -i desktop -t 30 output.mp4
```
Then step through frames to measure animation durations.

---

## Session Protocol

Each capture session follows this checklist:

1. **Pre-session**
   - [ ] Create session folder: `captures/<platform>/<YYYY-MM-DD>/`
   - [ ] Open dedicated Chrome profile.
   - [ ] Clear cache and storage (if doing a fresh-state capture).
   - [ ] Open DevTools, enable Preserve Log, Disable Cache.
   - [ ] Start screen recording (if needed).
   - [ ] Paste WebSocket logging snippet in Console.
   - [ ] Open `NOTES_TEMPLATE.md`, copy to session folder as `session-notes.md`.

2. **During session**
   - [ ] Perform one flow at a time.
   - [ ] Export HAR after each flow.
   - [ ] Screenshot key states.
   - [ ] Note timestamps and observations in session notes.

3. **Post-session**
   - [ ] Stop screen recording.
   - [ ] Save all Console output.
   - [ ] Export storage snapshots.
   - [ ] Review and label all saved files.
   - [ ] Update `CAPTURE_MATRIX.md` with completion status.
   - [ ] Write preliminary observations in session notes.

---

## Folder Structure

```
C:\poker-research\
├── RESEARCH_CAPTURE_PLAN.md        (this file)
├── CAPTURE_MATRIX.md               (flow tracking)
├── DERIVED_STACK_SPEC_TEMPLATE.md  (spec template)
├── NOTES_TEMPLATE.md               (session notes template)
├── captures/
│   └── <platform>/
│       └── <YYYY-MM-DD>/
│           ├── har/
│           ├── ws/
│           ├── storage/
│           ├── perf/
│           ├── screenshots/
│           ├── dom/
│           ├── console/
│           └── session-notes.md
└── specs/                          (filled-in specs, derived from captures)
```

---

## Platforms to Observe

| Platform | Type | Priority | Notes |
|----------|------|----------|-------|
| PokerStars Play (play money) | Browser | **Primary** | Closest to target UX |
| 888poker browser client | Browser | Secondary | Alternative flow comparison |
| WSOP.com (if accessible) | Browser | Tertiary | Different lobby paradigm |
| Open-source clients (e.g., PokerTH web) | Browser | Reference | See how open implementations handle the same flows |

Focus on **one platform at a time**. Complete all flows for the primary platform before moving to secondary.
