# Derived Stack Specification

> **Source platform:** _(e.g., PokerStars Play)_
> **Capture dates:** _(e.g., 2026-03-29 through 2026-04-05)_
> **Status:** Draft | Review | Final

This specification is derived entirely from observable behavior. Nothing here is reverse-engineered from proprietary source code. All data comes from browser DevTools captures, network traffic inspection, and visual observation.

---

## 1. Routes and Surfaces

Define every distinct screen/view the client presents, the URL pattern (if any), and transitions between them.

### 1.1 Route Map

| Route / View | URL Pattern | Entry Points | Exit Points | Auth Required |
|--------------|-------------|--------------|-------------|---------------|
| Landing / Login | `/` | Direct navigation | Lobby (on auth) | No |
| Lobby | `/lobby` | Login, Leave Table | Table (on join) | Yes |
| Table | `/table/:id` | Lobby (join) | Lobby (leave), Reconnect | Yes |
| Cashier / Buy-In | Modal on Table | Seat selection | Table (on confirm) | Yes |
| Settings | Modal / Panel | Any view | Return to caller | Yes |
| Hand History | `/history` or Modal | Table, Lobby | Return to caller | Yes |
| _Add rows as discovered_ | | | | |

### 1.2 Surface Inventory

For each view, document the major UI regions:

**Lobby Surface:**
- Header: logo, user info, balance, navigation
- Filter bar: game type, stakes, speed, table size
- Table list: columns, sort controls, live indicators
- Footer: links, status
- _Document actual observed layout_

**Table Surface:**
- Table felt: shape, color, texture approach
- Seats: count, position mapping (clock positions), player info displayed
- Community cards: position, deal animation origin
- Pot display: position, format
- Action panel: button layout, bet slider, preset buttons
- Chat: position, behavior
- Player cards: position relative to seat, reveal animation
- _Document actual observed layout_

### 1.3 Navigation Transitions

| From | To | Trigger | Transition Type | Duration (ms) |
|------|----|---------|-----------------|---------------|
| Lobby | Table | Click table + confirm | _fade/slide/instant_ | |
| Table | Lobby | Click leave + confirm | | |
| _Add rows_ | | | | |

---

## 2. API Contracts

Document every observed REST/HTTP endpoint.

### 2.1 Endpoint Inventory

For each endpoint:

```
### [METHOD] /path/to/endpoint

**Purpose:** What this endpoint does

**Request:**
- Headers:
  - Authorization: Bearer <token>
  - Content-Type: application/json
- Query params: (if GET)
  - param1: type — description
- Body: (if POST/PUT)
  ```json
  {
    "field": "type — description"
  }
  ```

**Response:**
- Status: 200
- Body:
  ```json
  {
    "field": "type — description"
  }
  ```

**Observed behavior:**
- Called when: [trigger]
- Response time: ~Xms (observed range)
- Caching: [cache headers observed]
- Error cases observed: [list]
```

### 2.2 Authentication Flow

| Step | Method | Endpoint | Purpose |
|------|--------|----------|---------|
| 1 | | | Initial auth |
| 2 | | | Token refresh |
| _Add steps_ | | | |

- Token format: _(JWT? opaque? length?)_
- Token location: _(header? cookie? both?)_
- Token lifetime: _(observed expiry)_
- Refresh mechanism: _(silent refresh? redirect?)_

### 2.3 Error Response Patterns

```json
{
  "observed_error_format": "document the actual shape"
}
```

- HTTP status codes observed: _list_
- Error code enumeration: _list if discoverable_

---

## 3. WebSocket Contract

### 3.1 Connection Lifecycle

| Phase | Details |
|-------|---------|
| Endpoint | `wss://...` |
| Handshake params | _(query string, headers)_ |
| Auth on connect | _(first message? token in URL? cookie?)_ |
| Heartbeat | _(interval, who initiates, ping/pong or custom)_ |
| Reconnect | _(strategy, backoff, max retries)_ |
| Close codes | _(observed codes and meanings)_ |

### 3.2 Message Format

```
Frame type: text | binary
Encoding: JSON | protobuf | msgpack | custom
Envelope structure:
{
  "type": "MESSAGE_TYPE",
  "seq": 123,
  "payload": { ... }
}
```

### 3.3 Message Type Catalog

Group by flow phase:

#### Table Setup
| Type | Direction | Payload | When |
|------|-----------|---------|------|
| `TABLE_STATE` | S→C | _full table snapshot_ | On join / reconnect |
| `PLAYER_JOIN` | S→C | _player info_ | When someone sits |
| `PLAYER_LEAVE` | S→C | _seat number_ | When someone leaves |
| _Add rows_ | | | |

#### Hand Flow
| Type | Direction | Payload | When |
|------|-----------|---------|------|
| `HAND_START` | S→C | _hand #, dealer seat, blinds_ | New hand begins |
| `HOLE_CARDS` | S→C | _cards for this player_ | After deal |
| `ACTION_REQUEST` | S→C | _available actions, time limit_ | Your turn |
| `PLAYER_ACTION` | C→S | _action type, amount_ | Player acts |
| `ACTION_RESULT` | S→C | _who acted, what, new pot_ | After each action |
| `FLOP` | S→C | _3 cards_ | Flop dealt |
| `TURN` | S→C | _1 card_ | Turn dealt |
| `RIVER` | S→C | _1 card_ | River dealt |
| `SHOWDOWN` | S→C | _hands revealed, winner(s)_ | Hand complete |
| `POT_AWARD` | S→C | _who won, amounts_ | Chips distributed |
| _Add rows_ | | | |

#### System
| Type | Direction | Payload | When |
|------|-----------|---------|------|
| `PING` | C→S | | Heartbeat |
| `PONG` | S→C | | Heartbeat response |
| `ERROR` | S→C | _code, message_ | Server error |
| _Add rows_ | | | |

### 3.4 Sequence Diagram: One Complete Hand

```
Client                          Server
  |                               |
  |  <--- HAND_START ------------ |
  |  <--- HOLE_CARDS ------------ |
  |  <--- ACTION_REQUEST -------- |  (preflop, first to act)
  |  --- PLAYER_ACTION ---------> |  (fold/call/raise)
  |  <--- ACTION_RESULT --------- |
  |  ... (repeat for each player) |
  |  <--- FLOP ------------------- |
  |  <--- ACTION_REQUEST --------- |
  |  ... (betting round) --------- |
  |  <--- TURN -------------------- |
  |  ... (betting round) ---------- |
  |  <--- RIVER ------------------- |
  |  ... (betting round) ---------- |
  |  <--- SHOWDOWN ---------------- |
  |  <--- POT_AWARD --------------- |
  |                                 |
```

_Replace with actual observed sequence._

---

## 4. Storage Model

### 4.1 Cookies

| Name | Domain | Path | Secure | HttpOnly | SameSite | Purpose | TTL |
|------|--------|------|--------|----------|----------|---------|-----|
| | | | | | | | |

### 4.2 localStorage

| Key | Value Shape | Set When | Read When | Purpose |
|-----|-------------|----------|-----------|---------|
| | | | | |

### 4.3 sessionStorage

| Key | Value Shape | Set When | Read When | Purpose |
|-----|-------------|----------|-----------|---------|
| | | | | |

### 4.4 IndexedDB

| Database | Object Store | Key Path | Indices | Purpose |
|----------|-------------|----------|---------|---------|
| | | | | |

### 4.5 Cache API / Service Workers

| Cache Name | URL Patterns | Strategy | Purpose |
|------------|-------------|----------|---------|
| | | | |

---

## 5. UX Timing

All timings measured from observable behavior (Performance panel, frame analysis).

### 5.1 Page Load Timing

| Metric | Observed Value | Notes |
|--------|---------------|-------|
| Time to first paint | ms | |
| Time to interactive (lobby usable) | ms | |
| Time to full table list render | ms | |

### 5.2 Transition Timing

| Transition | Duration (ms) | Animation Type | Notes |
|------------|--------------|----------------|-------|
| Lobby → Table load | | | |
| Table → Lobby return | | | |
| Buy-in dialog open | | | |
| Buy-in dialog close | | | |

### 5.3 In-Game Animation Timing

| Animation | Duration (ms) | Easing | Notes |
|-----------|--------------|--------|-------|
| Card deal (per card) | | | |
| Card flip / reveal | | | |
| Chip slide to pot | | | |
| Pot slide to winner | | | |
| Community card reveal | | | |
| Fold card animation | | | |
| Timer countdown total | | | |
| Timer warning threshold | | | At what remaining time does visual warning appear? |

### 5.4 Polling / Update Intervals

| What | Interval (ms) | Method | Notes |
|------|--------------|--------|-------|
| Lobby table list refresh | | WS push / polling | |
| Player count update | | | |
| Balance refresh | | | |

---

## 6. Rendering Model

### 6.1 Rendering Technology

| Component | Technology | Evidence |
|-----------|-----------|----------|
| Table felt | Canvas / DOM / SVG / WebGL | |
| Cards | Canvas / DOM / SVG / sprite sheet | |
| Chips | Canvas / DOM / SVG | |
| Animations | CSS transitions / JS / Canvas / requestAnimationFrame | |
| UI chrome (buttons, menus) | DOM | |
| Chat | DOM | |

### 6.2 Asset Inventory

| Asset Type | Format | Count | Total Size | CDN? | Notes |
|------------|--------|-------|------------|------|-------|
| Card images | PNG / SVG / sprite | | | | |
| Table background | | | | | |
| Chip graphics | | | | | |
| Avatars | | | | | |
| Sounds | MP3 / OGG / WAV | | | | |
| Fonts | WOFF2 / WOFF | | | | |

### 6.3 Responsive Behavior

| Breakpoint | Layout Changes | Notes |
|------------|---------------|-------|
| > 1200px | | |
| 768-1200px | | |
| < 768px | | |

### 6.4 Accessibility

| Feature | Present? | Notes |
|---------|----------|-------|
| Keyboard navigation | | |
| Screen reader labels | | |
| High contrast mode | | |
| Reduced motion support | | |

---

## 7. Backend Service Boundaries

Inferred from observed API patterns, WebSocket behavior, and timing analysis.

### 7.1 Inferred Services

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Web/CDN   │     │   API Gate   │     │   Auth       │
│   (static)  │────▶│   (REST)     │────▶│   Service    │
└─────────────┘     └──────────────┘     └──────────────┘
                           │
                    ┌──────┴──────┐
                    │             │
              ┌─────▼─────┐ ┌────▼──────┐
              │   Lobby   │ │  Table /  │
              │  Service  │ │  Game     │
              └───────────┘ │  Engine   │
                            └───────────┘
```

_Replace with actual inferred architecture._

### 7.2 Service Evidence

For each inferred service, document what evidence supports its existence:

| Service | Evidence | Confidence |
|---------|----------|------------|
| Auth | Separate `/auth/*` endpoints, distinct token refresh flow | High / Medium / Low |
| Lobby | `/lobby/*` endpoints, independent update cycle | |
| Game Engine | WebSocket-based, stateful hand management | |
| _Add rows_ | | |

### 7.3 Communication Patterns

| From | To | Protocol | Pattern | Notes |
|------|----|----------|---------|-------|
| Client | API Gateway | HTTPS | Request/Response | REST endpoints |
| Client | Game Engine | WSS | Bidirectional streaming | Per-table connection |
| _Add rows_ | | | | |

### 7.4 State Management Observations

| State | Owner | Persistence | Evidence |
|-------|-------|-------------|----------|
| User session | Auth service | Token-based | |
| Lobby table list | Lobby service | Server-authoritative, pushed/polled | |
| Table/hand state | Game engine | Server-authoritative, pushed via WS | |
| User preferences | Client + API | Dual-stored | |
| _Add rows_ | | | |

---

## Appendix: Open Questions

_Track unresolved questions here. Move to the relevant section once answered._

1. _Question_
2. _Question_
