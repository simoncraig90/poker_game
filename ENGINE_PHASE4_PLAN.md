# Engine Phase 4 Plan

Build the thinnest browser table client against the existing WebSocket server.

---

## Architecture: Plain HTML + Vanilla JS

No framework. The state shape from GET_STATE is a flat JSON object that maps directly to DOM updates. A framework would add build tooling, bundling, and dependency management for zero benefit at this scope.

```
client/
  index.html     Single HTML file: layout + styles + script
  table.js       Client-side state management + WS connection + DOM rendering
```

Served as static files from the WS server (add express-static or inline HTTP handler).

---

## How It Works

```
Browser                          WS Server
  │                                  │
  ├── ws.connect ──────────────────► │
  │ ◄───────────── welcome(state) ───┤
  │                                  │
  │   render(state)                  │
  │                                  │
  ├── cmd(SEAT_PLAYER) ───────────► │
  │ ◄───────── response(events) ────┤
  │   applyEvents → re-render       │
  │                                  │
  │ ◄─────── broadcast(events) ─────┤  (another client acted)
  │   applyEvents → re-render       │
```

The client holds a local mirror of the table state. On welcome, it sets the full state. On response/broadcast, it applies events incrementally (or re-fetches state via GET_STATE for simplicity in v1).

---

## Implementation Tasks

### Task 1: Static file serving from WS server

Add an HTTP server on the same port that serves `client/` files. The `ws` package can share an HTTP server.

### Task 2: index.html — Layout

Minimal HTML structure:
- Table name + blind info header
- 6 seat boxes arranged visually (no canvas, just CSS grid/flex)
- Board area (5 card slots)
- Pot display
- Action buttons (Fold / Check / Call / Bet / Raise)
- Bet slider or input for amount
- Event log panel (scrolling debug feed)
- Status bar (connected/disconnected, hand phase)

### Task 3: table.js — Client Logic

- WebSocket connection to `ws://localhost:9100`
- Message handler: welcome → set state, response → apply, broadcast → apply
- State-to-DOM rendering (full re-render on every state change for v1 simplicity)
- Command sender: builds wire protocol JSON, sends via WS
- Action button handlers: read active seat, build PLAYER_ACTION command
- Seat click: prompt for name/buyin, send SEAT_PLAYER
- Event log: append each event to a scrolling debug panel

### Task 4: State Sync Strategy

**V1 approach**: After every response or broadcast that contains events, send GET_STATE to refresh full state. This is simple and eliminates incremental-apply bugs. Overhead is negligible for a single-table dev client.

**Future**: Apply events incrementally (the client already has the state shape from welcome).

---

## Scope

### In Scope

| Feature | Renders | Interacts |
|---------|---------|-----------|
| Seat boxes (name, stack, status) | Yes | Click empty seat to join |
| Hero cards | Yes (in seat box) | — |
| Board cards | Yes (center area) | — |
| Pot | Yes (center) | — |
| Action buttons | Yes | Fold/Check/Call/Bet/Raise |
| Bet amount input | Yes | Number input for Bet/Raise |
| Legal action highlighting | Yes (disable illegal buttons) | — |
| Event log panel | Yes (scrolling text) | — |
| Hand phase indicator | Yes | — |
| Start Hand button | Yes | Sends START_HAND |
| Connection status | Yes | Auto-reconnect not required |

### Out of Scope

- Card images (use text: "Ah", "Tc")
- Animations
- Sound
- Seat-based visibility filtering (all cards visible in dev mode)
- Multiple tables
- Auth / player identity
- Mobile layout
- Chat
