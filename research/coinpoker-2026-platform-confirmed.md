# CoinPoker 2026 Platform — Confirmed Findings

Captured 2026-04-07 from live CoinPoker desktop client v1.0.41.

## Client Architecture

**Confirmed: Electron app, Chromium-based**
- Install path: `C:\Program Files\CoinPoker\CoinPoker.exe`
- Files: `chrome_100_percent.pak`, `app.asar`, `LICENSE.electron.txt`, `v8_context_snapshot.bin`
- Launch with: `--remote-debugging-port=9223`
- CDP works fully — DevTools Protocol accessible

**Page structure:**
- Main page: `file:///C:/Program Files/CoinPoker/resources/app.asar/dist/lobby.html`
- Game iframe: `https://d2df0cv2coy003.cloudfront.net/?token=<jwt>`
- Iframe is "PB Desktop Home" (PokerBaazi platform)

## DOM Architecture

- React app, **CSS Modules** with hashed class names (e.g., `_button_ysem3_15`)
- **NO canvas** — pure DOM rendering
- Standard HTML buttons (`<button>`)
- 600+ divs in main lobby

**Cash games lobby DOM contents (visible without API calls):**
- Player count: "2546 Online"
- USDT balance: "₮0"
- Filters: NLH/PLO/Micro/Low/Mid/High/VIP
- Table rows showing blinds, players, min buy-in
- Quick Join buttons per row
- Tournament cards with prize pools

## Backend Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `https://public-cash-listing.thecloudinfra.com/get-cash-lobby-list` | POST | Fetch lobby summary (player counts) |
| `https://public-cash-listing.thecloudinfra.com/get-cash-table-list` | POST | Fetch table list with stakes/players |
| `https://firestore.googleapis.com/google.firestore.v1.Firestore/Listen/channel?VER=8&database=projects/coinpoker-prod/databases` | POST | Real-time Firestore subscription |
| Same URL | GET | Long-poll fallback for Firestore stream |

**Firebase project**: `coinpoker-prod`
**Auth**: Firebase Authentication (JWT in iframe URL)
**Game state transport**: Google Firestore via gRPC-over-HTTP/2

## What we still don't know

- [ ] How tables/seats are subscribed to in Firestore (collection paths)
- [ ] Document structure for game state (cards, bets, actions)
- [ ] How actions are submitted (Firestore write? REST?)
- [ ] WebSocket purpose (only saw `{}` heartbeats — likely lobby presence only)
- [ ] Whether actions need balance > 0 (suspected yes — "Please Wait" popup loops without table loading)

## CRITICAL UPDATE: Game servers are NOT browser-based

The real-time gameplay does NOT happen in the Chromium iframe.
Each table connects directly to a TCP server:

```
poker-cash-1.coinpoker.ai:443  (with NLB at poker-nlb.coinpoker.ai:7001)
poker-cash-2.coinpoker.ai:443  (NLB :7002)
poker-cash-3.coinpoker.ai:443  (NLB :7003)
```

Server address from REST table list:
```
"serverAddress": "poker-cash-2.coinpoker.ai:443;HELM_REVERSION1;<uuid>;poker-nlb.coinpoker.ai:7002"
```

This means CDP **cannot see** the actual game protocol. The Electron client opens a separate TCP/TLS socket to these servers when you join a table. That socket carries the SFS2X-style binary protocol.

**To intercept gameplay traffic we need:**
1. **mitmproxy or similar** to MITM the TLS connection to `poker-cash-N.coinpoker.ai:443`
2. OR **process injection** to hook the Electron main process's socket calls
3. OR **scrape the rendered DOM** in the iframe (which renders the table state from the TCP socket data)

Option 3 is easiest since the data is already rendered into DOM elements by the iframe.

## REST Endpoints Confirmed

### Lobby
| URL | Method | Purpose |
|-----|--------|---------|
| `https://public-cash-listing.thecloudinfra.com/get-cash-lobby-list` | POST | Lobby summary by category |
| `https://public-cash-listing.thecloudinfra.com/get-cash-table-list` | POST | Cash table list (returns serverAddress) |
| `https://public-cash-listing.thecloudinfra.com/get-table-list` | POST | All tables (cash + AOF + bomb) |
| `https://public-cash-listing.thecloudinfra.com/get-lobby-list` | POST | Lobby summary by stake group |

### Promotions
| URL | Method | Purpose |
|-----|--------|---------|
| `https://nxtgenapi.thecloudinfra.com/promotion/v3/leaderboard/active-game-table-lb` | GET | Active leaderboards (CoinRaces) |

### Firestore subscriptions (real-time)
- `projects/coinpoker-prod/databases/(default)/documents/global_data/game_engine`
- `projects/coinpoker-prod/databases/(default)/documents/global_data/game`

These are GLOBAL config docs (not per-table). Probably maintenance status, server health, jackpot pots.

### Analytics
- `https://www.google-analytics.com/mp/collect?measurement_id=G-L6Z4B7KBGZ`

CoinPoker tracks via GA4. Worth noting for fingerprinting.

## API Version
- Lobby API: `3.2.5-rc1`
- Promotion API: `4.0.0`
- Client version: `v1.0.41`

## Sample table response data
```json
{
  "roomName": "NL 0.01-0.02 EV-INRIT-(A) 238215",
  "tableId": 238215,
  "configId": 200588,
  "serverAddress": "poker-cash-2.coinpoker.ai:443;HELM_REVERSION1;<uuid>;poker-nlb.coinpoker.ai:7002",
  "maxSize": 2000,
  "minSize": 2,
  "coinTypeId": 1,
  "gameType": "Ring",  // "Ring" | "Allinfold" | etc
  "miniGameTypeId": 1,
  "isAnonymous": false,
  "isRit": false,
  "joinedUserCount": <int>,
  ...
}
```

`coinTypeId: 1` = USDT main currency
`gameType: "Ring"` = cash game
`gameType: "Allinfold"` = AOF format

## Critical insight

**This is NOT SFS2X anymore.** Earlier research mentioned SFS2X over WebSocket via BlueBox. The 2026 PokerBaazi migration replaced this entirely with Firebase/Firestore. Existing parsers (Hand2Note, Advanced Converter) likely scrape hand history files on disk, not the live protocol.

## Strategy for integration

1. **Lobby reading**: Pure DOM scraping. Element queries on the main page.
2. **Table list**: Either DOM scrape OR call `get-cash-table-list` REST endpoint directly with auth token.
3. **Game state**: Monkey-patch Firestore SDK in the page context to capture `onSnapshot` callbacks. The SDK is exposed in window globals or accessible via React refs.
4. **Actions**: Likely Firestore writes (`addDoc`/`updateDoc`) — same approach: monkey-patch the SDK.
5. **Clicking**: JS `.click()` on standard buttons OR Firestore writes directly bypassing UI.

## Detection profile (per earlier research)

- **Client-side**: Almost nothing. No process scanning, no DLL hooks, no kernel driver.
- **Server-side**: ML on decision-timing variance, GTO correlation (caught "LazyAss" Nov 2025).
- **HUDs**: Officially allowed.
- **Bot enforcement**: 98 accounts banned + $156K refunded Jan 2026 after viral video.

## Files for protocol mapping

After capturing more traffic during real gameplay:
- Hand history file location (need to find)
- Firestore document IDs for active tables
- Action POST/write structure
