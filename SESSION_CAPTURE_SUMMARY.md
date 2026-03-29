# Session Capture Summary

> **Date:** 2026-03-29
> **Session folder:** `captures/2026-03-29_1817_lobby_join_buyin_onehand_leave/`
> **Platform:** PokerStars UK (www.pokerstars.uk)
> **Username:** Skurj_poker

---

## Artifacts Collected

| Artifact | Size | Time Range (UTC) | Content |
|----------|------|-------------------|---------|
| `01-table-load.har` | 32MB | 17:15–17:26 (11 min) | Idle table load — startup + 40min heartbeat, no gameplay |
| `02-table-load-obs-twohands.har` | 32MB | 18:36–18:38 (2 min) | **Live gameplay** — startup + 3 complete hands |
| `02-table-load-obs-twohands_obs_.mkv` | 18MB | ~matches HAR #2 | OBS screen recording of gameplay |
| `notes/session_notes.txt` | 10 lines | — | Minimal session log |
| `har/`, `screenshots/`, `storage/`, `websocket/` | Empty | — | Subfolders created but unused |

**70-minute gap** between HAR #1 (ends 17:26) and HAR #2 (starts 18:36). These are two separate page loads, not one continuous session.

---

## Part 1: Startup / Load Evidence (both HARs)

Startup sequence is fully captured and consistent across both HARs. This evidence is **solid**.

### Network Architecture

- **119–142 HTTP requests** per page load, all 200/101 (plus 5x 403 on avatar fetches)
- **3 WebSocket connections** per session:

| Connection | Protocol | Role |
|------------|----------|------|
| `/poker/play-now/metro/websockets` | Binary (Thrift) | Game engine — all hand/table state |
| `/api/v0/websocket/<id>/<session>/websocket` | STOMP over SockJS | Account, session, notifications, GeoComply |
| `/api/v1-preview/websocket/<id>/<session>/websocket` | STOMP over SockJS | Loyalty program (CVL bar/config) |

### Startup Timeline (confirmed in both HARs)

| Phase | Time | What Happens |
|-------|------|-------------|
| t+0.0s | Page + parallel asset fetch | HTML (887KB), jQuery, Angular, WebPoker bundle |
| t+0.0s | Client bundle loads | `bootstrap.js` (605KB), `vendors.js` (2.9MB), `app.js` (2.5MB) |
| t+0.0s | Config fetch (parallel) | 20+ JSON configs — game params, skins, settings, site config |
| t+0.0s | Metro WS opens | Binary Thrift connection to game engine |
| t+0.1s | Auth | `GET /api/v0/session/info` + `GET /api/v1-preview/auth/session` → JWT (HS256) |
| t+1.0s | Assets load | Card sprites, table canvas images, theme PNGs (~30 assets) |
| t+2.0s | STOMP WS #1 connects | Session info, account data, notification subscriptions |
| t+3.0s | STOMP WS #2 connects | Loyalty/CVL bar subscription |
| t+3.0s | **Ready** | All connections established, UI rendered |

### Confirmed Infrastructure

| Component | Evidence |
|-----------|----------|
| CDN: `webpoker.rationalcdn.com` | All client code, configs, images, sprites |
| CDN: `cashier.rationalcdn.com` | Rewards widget, StarsCRM |
| Rendering: **Cocos2d-JS on Canvas** | Asset paths (`img/cocos_stud/*`), `.plist` sprite sheets, `table_canvas/*` |
| Auth: JWT in `StarsWeb-Session` header | HS256 signed, used in STOMP `authorization` headers |
| Analytics: Snowplow | `collector.pokerstars.uk/com.snowplowanalytics.snowplow/tp2` |
| RUM: Flutter International | `api.rum.obs.flutterint.com/events` |
| Geolocation: GeoComply | `cdn.geocomply.com` config + STOMP notification pushes |
| Build: `20260224 / master / build 2977` | Versioned path in all CDN URLs |
| 17 table themes | Full config JSONs captured for each |
| Client settings schema | `settingsData.json` — shortcuts, audio, animation, fourcolor, timeout |
| Entry mode: `standalonetable=true` | Direct-to-table, no lobby view captured |

---

## Part 2: Live Gameplay Evidence (HAR #2 only)

HAR #2 contains **584 metro WS messages in ~3.5 minutes** with **3 complete hands** and **8 player actions by Skurj_poker**. This is real gameplay.

### Evidence of Gameplay

**Sound assets loaded (confirms hand activity):**
CardDealt.mp3, Bet.mp3, Raise.mp3, Fold.mp3, Check.mp3, ChipsMovedToPot.mp3, ChipsMovedFromPot.mp3, Attention.mp3, TimeWarning.mp3

**Players at table:** Aristaeus, BStouts, Skurj_poker, williamlhunt, BigGameSally, Klukwa, sScums (7 players)

**Table ID pattern:** `6R.6440271570.393e711` — embedded in every game message as a routing key

### Three Complete Hands Identified

| Hand | Showdown Time | Duration | Result Text | User Actions |
|------|--------------|----------|-------------|-------------|
| **Hand 1** | t+42.2s | ~33s (from t+9s) | "Takes down main pot" / "Loses main pot and mucks cards" (×4) | 1 action (0x7a at t+42.5s) |
| **Hand 2** | t+110.8s | ~69s | "Takes down main pot" (×1) / "Loses main pot and mucks cards" (×5) | 2 actions (0x7a at t+52.9s, t+118.9s) |
| **Hand 3** | t+175.8s | ~65s | "Takes down main pot" (×1) / "Loses main pot and mucks cards" (×5) | 5 actions (0x7a at t+135–171s) |

### Metro WS Protocol Patterns (Binary Thrift)

**Repeating hand sequence** (byte[1] = message type):

```
0x72 → 0x78 → 0x76 → 0x77 → 0x6c → 0x6f    ← "betting round" pattern (repeats per street)
0x79 → 0x78 → 0x77 × N → 0x5a → 0x71 → 0x6f ← "contested pot" variant (multiple bets)
0x7d                                           ← SHOWDOWN (contains "Takes down main pot" / "Loses" text)
0x6c × N → 0x6d                               ← player stack updates (all player names)
0x70                                           ← hand summary (142B)
0x48                                           ← hand complete / cleanup
0x8b, 0x8f                                     ← post-hand state (timers? next hand prep?)
```

**Client-sent message types:**

| Type | Size | Count | Meaning |
|------|------|-------|---------|
| `0xff` | 1703B | 1 | Initial handshake (contains OS, browser, version) |
| `0x68` | 2216B | 1 | Authentication (contains auth token) |
| `0xc7` | 40-52B | 3 | Service requests (poker rules, starsrewards widget) |
| `0xb5` | 131B | 1 | Config/capability negotiation (contains "GB") |
| `0x73` | 26B | 1 | Table/seat join request |
| `0x64` | 50-75B | 2 | Table join confirmation (two-step) |
| `0xb2` | 324B | 1 | Game subscription (contains "RING", player name) |
| `0xc2` | 51B | 4 | Post-join setup (4 identical messages) |
| **`0x7a`** | **61B** | **8** | **Player game action (fold/check/call/raise)** |

**Heartbeat:** 8-second ping/pong cycle, 10-byte messages

**Message density by phase:**
- Startup (t+0–3s): 50 messages, 24KB — heavy setup burst
- Active hand play (t+9–42s): 40-50 messages per 10s window — dense game state updates
- Between hands: drops to 1-3 messages per 10s

### Table Join Sequence (t+6–9s)

```
t+6.3s  client → 0x73 (26B)     seat request
t+7.2s  server → 0x88           acknowledgment
t+7.2s  client → 0x64 (50B)     join step 1
t+7.2s  server → 0x4b, 0x60     seat assignment + player list
t+7.2s  client → 0x64 (75B)     join step 2
t+8.5s  server → 0x6a (1886B)   full table state dump (all 7 player names + stacks)
t+8.5s  client → 0xb2 (324B)    game subscription ("RING", player name)
t+9.1s  hand play begins
```

---

## Part 3: What Is Still Missing

### Not Captured At All

| Gap | Impact | Why It Matters |
|-----|--------|---------------|
| **Screenshots** | No visual reference for any UI state | Can't verify layout, component structure, or animation appearance |
| **Storage snapshots** | No localStorage/sessionStorage/cookie data | Can't model client-side persistence |
| **DOM inspection** | No element structure captured | Can't determine UI component boundaries |
| **Performance profiles** | No flame charts | Can't measure animation timing or render performance |
| **Console output** | No client-side logs | Missing error patterns, feature flags, debug output |
| **Lobby flow** | Entry was `standalonetable=true` | No lobby view, table list, or lobby-to-table transition captured |
| **Buy-in flow** | Not isolated | If it happened, it's buried in the binary metro WS — not separately identified |
| **Leave table flow** | Not captured | No WS close sequence or leave confirmation |
| **Reconnect behavior** | Not tested | No disconnect/reconnect data |

### Captured But Not Decoded

| Gap | What We Have | What We Need |
|-----|-------------|-------------|
| **Metro binary protocol** | 584 raw Thrift messages with type bytes and readable string fragments | Thrift IDL to fully decode every field — message types 0x72–0x8f are identified by pattern but not by field content |
| **Player actions** | 8× type-0x7a messages (61B each) from client | Can't distinguish fold vs check vs call vs raise — payload not decoded |
| **Hand state per street** | Repeating 0x72→0x78→0x76→0x77→0x6c→0x6f pattern | Can't identify which cluster = preflop/flop/turn/river |
| **OBS video** | 18MB MKV file exists | Not analyzed (no ffprobe available) — need to review manually for UI state confirmation |

---

## Part 4: Corrected Workflow for Next Clean Session

The session notes already identify the lesson learned: *"start OBS, open DevTools Network with Preserve log, then join table and play."* Building on that:

### Pre-Session Setup (do before opening the poker site)

```
1. Open dedicated Chrome profile ("Poker Research")
2. Open DevTools (F12) → Network tab
   - ✅ Preserve log
   - ✅ Disable cache
3. Open DevTools → Console tab
   - Paste the WebSocket logging snippet from RESEARCH_CAPTURE_PLAN.md
4. Open DevTools → Application tab
   - Screenshot current storage state (or run localStorage dump snippet)
5. Start OBS recording
6. Open a text editor with NOTES_TEMPLATE.md copy for timestamped notes
7. THEN navigate to the poker site
```

### During Session — Capture One Flow At A Time

**Flow A: Lobby (if accessible)**
- Navigate to lobby URL (not standalone table)
- Screenshot lobby layout
- Export HAR → `har/01-lobby-load.har`
- Note: table list structure, filter controls, player counts

**Flow B: Join Table + Buy-In**
- Click a table from lobby (or use standalone URL)
- Screenshot: buy-in dialog, table loading state, seated state
- Note timestamps for each transition
- Export HAR → `har/02-join-buyin.har`

**Flow C: Play 3+ Hands**
- Play through at least 3 hands with different actions (fold, call, raise)
- Screenshot: hole cards, each street, showdown, pot award
- Keep OBS running throughout
- Export HAR → `har/03-hands.har`

**Flow D: Leave Table**
- Click leave/stand up
- Screenshot: confirmation dialog (if any), transition back
- Export HAR → `har/04-leave.har`

**Flow E: Storage Dump**
- After all flows, run storage dump snippets in Console
- Save to `storage/post-session.json`

### Post-Session

```
1. Stop OBS → save to `video/session-002.mkv`
2. Export final HAR if not already saved
3. Copy Console output → `console/session-002.log`
4. Screenshot Application tab storage → `storage/`
5. Fill in session notes with all timestamps and observations
6. Update CAPTURE_MATRIX.md status columns
```

### Key Improvement: Isolate Flows in Separate HARs

Session 001's main problem: two HARs captured the same startup twice, with a 70-minute gap and no clear flow isolation. Next session should:
- Clear network log between flows (right-click → Clear) then export per-flow
- Or at minimum, note exact timestamps when each flow starts/ends
- Use the notes template actively, not after the fact

### Priority Captures for Next Session

| Priority | What | Why |
|----------|------|-----|
| **P0** | Screenshots of every UI state | Zero visual evidence exists |
| **P0** | Storage dump (localStorage, cookies) | Zero persistence data exists |
| **P1** | Lobby flow (if accessible without standalone mode) | Completely missing |
| **P1** | Leave table flow | Completely missing |
| **P1** | Extract Thrift IDL from `app.js` | Required to decode the 584 binary game messages we already have |
| **P2** | Reconnect test (kill network mid-hand) | Completely missing |
| **P2** | DOM inspection of key components | Zero structural data |
