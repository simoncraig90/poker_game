# Session 001 Summary

> **Date:** 2026-03-29
> **Time:** 17:15 – 17:55 UTC (18:15 – 18:55 local)
> **Platform:** PokerStars UK (www.pokerstars.uk) — real-money site
> **Entry URL:** `/poker/play-now/?standalonetable=true&args=<base64>`
> **Username observed:** Skurj_poker

---

## 1. Observed Flow: Table Load Through Idle

### What actually happened

The session captured a **direct table load** (standalone table mode, bypassing the lobby) through approximately 40 minutes of idle/observation. The session notes record a "minimum play requirements modal" appearing at 18:20.

**No hand play, buy-in, or leave-table actions were captured in this session.** The metro WebSocket traffic is >99% heartbeat after the initial 3 seconds of setup. There are no traffic spikes that would indicate hand dealing, betting actions, or chip movement.

### Startup sequence (first 3 seconds)

1. **t+0.0s** — HTML page loads from `www.pokerstars.uk` (887KB).
2. **t+0.0s** — Parallel asset fetch begins: jQuery 3.3.1, Angular, WebPoker client bundle.
3. **t+0.0s–0.2s** — Client bundle loads from `webpoker.rationalcdn.com`:
   - `bootstrap.js` (605KB) — client loader
   - `vendors.js` (2.9MB) — third-party dependencies
   - `app.js` (2.5MB) — application code
   - `analytics.js` (103KB), `sweep.js` (106KB)
   - Build: **20260224 / master / build 2977**
4. **t+0.0s** — Config files fetched in parallel:
   - `config.json` — metro WS endpoint config
   - `config_common.json` (34KB) — card dimensions, animation timings, offsets
   - `config_site.json` (19KB) — multi-brand/domain configuration
   - `settingsData.json` — user preference schema
   - `base.json` — asset preload manifest
   - 17 theme skin configs (default, wireframe, video, ring, zoom, spin variants, etc.)
5. **t+0.0s** — Metro WebSocket opens: `/poker/play-now/metro/websockets`
6. **t+0.1s** — Session info fetched: `GET /api/v0/session/info` → `{"country":"GB","site":32768,"legalAge":18,"domain":"www.pokerstars.uk"}`
7. **t+0.1s** — Auth session: `GET /api/v1-preview/auth/session` → JWT token (HS256)
8. **t+1.0s** — Card sprite sheets and table canvas images load (~30 assets).
9. **t+2.0s** — STOMP WebSocket #1 (`/api/v0/websocket/`) connects — session, account, notifications.
10. **t+3.0s** — STOMP WebSocket #2 (`/api/v1-preview/websocket/`) connects — loyalty/CVL bar.
11. **t+3.0s** — Setup complete. Heartbeat-only traffic from here.

### After setup

- **Heartbeat cadence:** ~8-second ping/pong on metro WS (10-byte binary messages).
- **STOMP heartbeat:** 25-second interval (`h` frames).
- **Feature flag poll:** `GET /api/v0/features/check` every ~4 minutes checking `WebMPCNotificationEnabled` and `WebNotificationEnabled`.
- **GeoComply:** Geolocation verification pushes arrive via STOMP notifications (~3 observed).
- **Periodic lobby-like updates:** Small bursts of ~275-byte binary messages on metro WS at ~5min, ~20min, ~35min intervals — likely table list or player count refreshes even in standalone mode.

---

## 2. Confirmed Architecture

### Three-WebSocket Architecture

| Connection | Endpoint | Protocol | Purpose | Messages |
|------------|----------|----------|---------|----------|
| **Metro** | `/poker/play-now/metro/websockets` | Binary (Thrift) | Game engine — table state, hands, actions | 672 (40 min) |
| **STOMP v0** | `/api/v0/websocket/<id>/<session>/websocket` | STOMP over SockJS | Session, account info, notifications, geocomply | 106 |
| **STOMP v1** | `/api/v1-preview/websocket/<id>/<session>/websocket` | STOMP over SockJS | Loyalty program (CVL bar/config) | 103 |

### CDN and Domain Map

| Domain | Role |
|--------|------|
| `www.pokerstars.uk` | Origin — HTML, APIs, WebSockets |
| `webpoker.rationalcdn.com` | Client code, configs, images, sprites |
| `cashier.rationalcdn.com` | Rewards widget, StarsCRM bundle |
| `s1.rationalcdn.com` | Shared cross-UX scripts (Angular, casino wrapper) |
| `fonts.googleapis.com` / `fonts.gstatic.com` | Roboto, Roboto Condensed |
| `cdnjs.cloudflare.com` | jQuery 3.3.1 |
| `api.rum.obs.flutterint.com` | Real User Monitoring (Flutter International / PokerStars parent) |
| `collector.pokerstars.uk` | Snowplow analytics |
| `cdn.geocomply.com` | Geolocation compliance |

### Rendering: Cocos2d + Canvas

Evidence from asset paths:
- `img/cocos_stud/*` — Cocos2d studio export files (multiple)
- `img/table_canvas/*` — canvas rendering assets (felt, floor, lights)
- `img/cards/card*` — card sprite sheets (PNG, multiple decks)
- `img/card_match*` — card matching assets
- Theme configs reference `.plist` files (Cocos2d sprite sheet format)

**Conclusion:** Table rendering uses **Cocos2d-JS** on an HTML5 Canvas element, not DOM-based rendering. UI chrome (menus, dialogs, settings) is likely DOM/Angular.

### Auth Model

- **Token type:** JWT (HS256), issued via `GET /api/v1-preview/auth/session`
- **Token usage:** Sent as `StarsWeb-Session <jwt>` in STOMP `authorization` header and as `token` field in v1-preview STOMP
- **Session info:** Separate `GET /api/v0/session/info` returns country, site ID, legal age, domain

### Client Configuration

From `config_common.json` (34KB) — confirmed card rendering parameters:
- `closedCardWidth: 53`, `openCardWidth: 71`
- `animationTimeForDiscardOperation: 250ms`
- Card offsets defined per seat count (2-10 players)
- Card opacity states: open, closed, transparent

From `settingsData.json` — user-facing settings:
- `shortcuts`, `audio`, `animation`, `dealermessages`, `playermessages`
- `fourcolor` (four-color deck)
- `timeout` (default: 20 minutes)
- `popup-disconnectprotectdontshow`

### Skin/Theme System

17 themes discovered, each with its own config JSON:
`default`, `wireframe`, `video`, `ring`, `zoom`, `stealth`, `sixplus`, `sunday-million-seasons`, `spin`, `spinflash`, `spinlive`, `spinsixmax`, `spinmax`, `all-in-poker`, `fpt-ny`, `kopoker`, `jetset-season`

Each theme defines: table background assets, felt color, text color, logo position, portrait/landscape support, feature flags.

---

## 3. Evidence Captured Successfully

| Evidence | Status | Quality | Notes |
|----------|--------|---------|-------|
| HAR file (full network) | Captured | Good | 119 requests, 31MB, includes WS messages |
| HTTP request waterfall | Captured | Good | Complete startup sequence with timing |
| API endpoints | Captured | Good | 5 distinct API routes documented |
| WebSocket connections (3) | Captured | Partial | Connections and message counts visible; binary metro protocol not decoded |
| Metro WS messages (672) | Captured | Raw | Base64-encoded Thrift binary — needs deserialization tooling |
| STOMP messages | Captured | Good | Readable text — session, account, notifications fully visible |
| Config JSONs (20+) | Captured | Good | Full content embedded in HAR responses |
| Asset manifest | Captured | Good | All image/font/script URLs with sizes |
| Auth token format | Captured | Good | JWT structure and usage pattern clear |
| Session notes | Captured | Minimal | Only 4 lines — timestamps and one observation |
| Screenshots | **Not captured** | — | No visual evidence of UI states |
| Storage snapshots | **Not captured** | — | No localStorage/sessionStorage/cookie dumps |
| Performance profile | **Not captured** | — | No flame chart or timing data |
| DOM structure | **Not captured** | — | No element inspection data |
| Hand play WS traffic | **Not captured** | — | Session was idle — no game actions occurred |

---

## 4. Key Gaps and What Session 002 Should Focus On

### Critical gaps to fill

1. **Capture actual hand play.** This session has zero game action. We need metro WS traffic during deal → bet → flop → bet → turn → bet → river → showdown. This is the single most important missing piece.

2. **Decode the metro binary protocol.** The 672 metro WS messages are Apache Thrift binary. We need to:
   - Identify the Thrift IDL (may be inferable from the `app.js` bundle — search for struct definitions).
   - Write a decoder script to parse the binary frames into readable message types.
   - Without this, we cannot understand the game protocol.

3. **Capture screenshots at each UI state:**
   - Table with empty seats
   - Minimum play requirements modal
   - Buy-in dialog
   - Seated at table waiting for hand
   - Hole cards dealt
   - Each betting round
   - Showdown
   - Action buttons (fold/check/call/raise) with bet slider

4. **Capture storage state.** Dump localStorage, sessionStorage, and cookies before and after key actions.

5. **Write detailed session notes.** The 4-line notes from this session are not sufficient. Use the `NOTES_TEMPLATE.md` template.

### Recommended Session 002 plan

| Priority | Task | Method |
|----------|------|--------|
| **P0** | Play at least one full hand, capture metro WS during play | HAR + WS inspection |
| **P0** | Screenshot every distinct UI state | ShareX / Win+Shift+S |
| **P1** | Extract Thrift struct definitions from `app.js` bundle | Download and search the 2.5MB JS |
| **P1** | Capture buy-in dialog flow and API calls | HAR |
| **P1** | Dump storage before/after table join | Console snippets from RESEARCH_CAPTURE_PLAN.md |
| **P2** | Capture leave-table flow and WS close sequence | HAR + WS |
| **P2** | Test reconnect behavior (kill network mid-hand) | Manual + HAR |
| **P2** | Record deal animation for frame timing | OBS/ShareX screen recording |

### Open questions for next session

- The entry URL uses `standalonetable=true` with base64-encoded args — what's in those args? (table ID? game type? stakes?)
- Is there a lobby view in the browser client, or is it always direct-to-table?
- The metro protocol is Thrift binary — is the IDL embedded in the client JS, or is it compiled in?
- What triggers the "minimum play requirements modal"? Is it buy-in related or regulatory?
- The ~5-minute periodic updates on metro WS during idle — are these lobby refreshes or keep-alive state pushes?
