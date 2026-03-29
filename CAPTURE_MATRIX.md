# Capture Matrix

Track completion of each observable flow across platforms and capture methods.

## Legend

| Symbol | Meaning |
|--------|---------|
| `-` | Not started |
| `~` | Partial (some data collected, needs more) |
| `X` | Complete |
| `N/A` | Not applicable to this flow |

---

## Primary Platform: PokerStars Play (Browser)

### Flow: Lobby Load

Initial page load through fully rendered lobby with table list.

| Capture Method | Status | File(s) | Notes |
|----------------|--------|---------|-------|
| HAR export | - | | Full page load waterfall, API calls for table list |
| WebSocket log | - | | Does lobby use WS or polling for live player counts? |
| Storage snapshot | - | | What's seeded on first load? Auth tokens, preferences? |
| Performance profile | - | | Time to interactive, time to first table list render |
| Screenshots | - | | Lobby layout at 1920x1080, loading states, empty states |
| DOM inspection | - | | Table list component structure, filter/sort controls |
| Console log | - | | Errors, feature flags, debug output |

**Key questions to answer:**
- [ ] How is the table list fetched? Single API call or paginated?
- [ ] Are player counts live-updated? Via WS or polling? What interval?
- [ ] What filters/sorts are available? Are they server-side or client-side?
- [ ] Is there a lobby-to-table transition animation?
- [ ] What data is in each table list entry (name, stakes, players, avg pot, etc.)?

---

### Flow: Join Table

From lobby table click through being seated at a table with cards visible.

| Capture Method | Status | File(s) | Notes |
|----------------|--------|---------|-------|
| HAR export | - | | What API calls happen on join? Seat reservation? |
| WebSocket log | - | | New WS connection per table or shared? Initial state dump? |
| Storage snapshot | - | | Table state cached locally? |
| Performance profile | - | | Transition time from lobby to table view |
| Screenshots | - | | Table layout, seat positions, empty seats, player info |
| DOM inspection | - | | Table rendering (canvas vs DOM), seat component structure |
| Console log | - | | |

**Key questions to answer:**
- [ ] Is joining a table a single request or multi-step (reserve seat, then confirm)?
- [ ] Does the client open a new WebSocket connection for the table?
- [ ] What initial state does the server push (table config, seated players, current hand state)?
- [ ] Is the table rendered in canvas/WebGL or DOM elements?
- [ ] How are seat positions mapped (fixed 2-10 seat layout)?

---

### Flow: Buy-In

The chip purchase / stack selection dialog and confirmation.

| Capture Method | Status | File(s) | Notes |
|----------------|--------|---------|-------|
| HAR export | - | | Buy-in API request/response, balance check |
| WebSocket log | - | | Is buy-in confirmed via WS or REST? |
| Storage snapshot | - | | Balance cached client-side? |
| Performance profile | - | | Dialog render time |
| Screenshots | - | | Buy-in dialog, min/max amounts, slider or input, error states |
| DOM inspection | - | | Dialog component, validation rules |
| Console log | - | | |

**Key questions to answer:**
- [ ] Is there a min/max buy-in? How is it communicated (in table metadata or separate call)?
- [ ] Is buy-in a REST call or WS message?
- [ ] Can you top up mid-session? When (between hands only)?
- [ ] What happens if balance is insufficient?
- [ ] Is there a confirmation step or immediate?

---

### Flow: First Hand

A complete hand from deal through showdown/muck, covering all betting rounds.

| Capture Method | Status | File(s) | Notes |
|----------------|--------|---------|-------|
| HAR export | - | | Any REST calls during hand play? |
| WebSocket log | - | | **Critical** — full message sequence for one hand |
| Storage snapshot | - | | Hand history written locally? |
| Performance profile | - | | Deal animation timing, action timer, pot animation |
| Screenshots | - | | Each street: preflop, flop, turn, river, showdown |
| DOM inspection | - | | Card elements, action buttons, pot display, timer |
| Console log | - | | |

**Key questions to answer:**
- [ ] What is the full WS message sequence for a hand? Document every message type.
- [ ] How are hole cards delivered? (Separate message per player or bundled?)
- [ ] How are community cards revealed? (One message per street or all at once?)
- [ ] What are the action options and how are they presented (fold/check/call/raise/all-in)?
- [ ] How does the bet slider work? Min/max/increments?
- [ ] What is the action timer duration? Is there a time bank?
- [ ] How is the pot calculated and displayed (main pot, side pots)?
- [ ] How are animations timed? (Deal: Xms per card, flip: Xms, chip slide: Xms)
- [ ] Is hand history available immediately? Where?

---

### Flow: Leave Table

Voluntary departure from a table back to the lobby.

| Capture Method | Status | File(s) | Notes |
|----------------|--------|---------|-------|
| HAR export | - | | Leave/unseat API call |
| WebSocket log | - | | Leave message, WS close sequence |
| Storage snapshot | - | | What's cleaned up on leave? |
| Performance profile | - | | Transition time back to lobby |
| Screenshots | - | | Leave confirmation dialog (if any), transition |
| DOM inspection | - | | |
| Console log | - | | |

**Key questions to answer:**
- [ ] Can you leave mid-hand? What happens to your action?
- [ ] Is there a confirmation dialog?
- [ ] Does the client close the WS connection or send a leave message?
- [ ] Is the lobby state refreshed on return or cached?
- [ ] Does balance update immediately?

---

### Flow: Reconnect

Simulate disconnect (kill network, close tab, etc.) and observe reconnection behavior.

| Capture Method | Status | File(s) | Notes |
|----------------|--------|---------|-------|
| HAR export | - | | Reconnect handshake, state recovery requests |
| WebSocket log | - | | Reconnect WS negotiation, state resync messages |
| Storage snapshot | - | | Is enough state stored locally to show something before resync? |
| Performance profile | - | | Time from reconnect to playable state |
| Screenshots | - | | Disconnected state UI, reconnecting indicator, restored state |
| DOM inspection | - | | Disconnect overlay/banner |
| Console log | - | | Reconnect retry logic, error handling |

**Key questions to answer:**
- [ ] What happens visually when connection drops? (Overlay? Banner? Freeze?)
- [ ] How quickly does the client detect the disconnect?
- [ ] What is the reconnect strategy? (Immediate retry, backoff, max attempts?)
- [ ] Does the server hold the seat? For how long?
- [ ] On reconnect, does the server push full table state or a delta?
- [ ] What if a hand completed during disconnect? Is it replayed or skipped?
- [ ] Is there a "sit out" auto-trigger on disconnect?

---

### Flow: Settings / Storage

Inspect client-side configuration, preferences, and persisted state.

| Capture Method | Status | File(s) | Notes |
|----------------|--------|---------|-------|
| HAR export | - | | Settings API (if server-stored) |
| WebSocket log | - | | N/A usually |
| Storage snapshot | - | | **Primary** — full dump of all storage mechanisms |
| Performance profile | - | | N/A |
| Screenshots | - | | Settings UI, available options |
| DOM inspection | - | | Settings panel structure |
| Console log | - | | |

**Key questions to answer:**
- [ ] What user preferences exist? (Sound, theme, card style, auto-actions, table size)
- [ ] Are preferences stored client-side, server-side, or both?
- [ ] What keys are used in localStorage/sessionStorage?
- [ ] Is there IndexedDB usage? For what?
- [ ] Are there service workers? What do they cache?
- [ ] What cookies are set? Purpose of each?

---

## Secondary Platforms

Repeat the matrix above for each secondary platform. Create separate sections as needed:

### 888poker Browser Client

*Status: Not started — complete primary platform first.*

### Other Platforms

*Add as identified.*

---

## Cross-Platform Comparison (fill after multiple platforms captured)

| Aspect | PokerStars Play | 888poker | Notes |
|--------|-----------------|----------|-------|
| Rendering approach | | | Canvas vs DOM |
| Real-time transport | | | WS vs polling vs SSE |
| Message format | | | JSON vs binary |
| Auth mechanism | | | Token type, refresh strategy |
| Lobby update method | | | Push vs pull, interval |
| Hand message count | | | Messages per complete hand |
| Reconnect strategy | | | Retry logic, seat hold duration |
| Client-side storage | | | What and how much |
