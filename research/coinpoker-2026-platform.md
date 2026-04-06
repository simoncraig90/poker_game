# CoinPoker 2026 Platform Research

**Date:** 2026-04-05
**Platform:** CoinPoker (rebuilt March 2, 2026 on PokerBaazi technology)
**Play URL:** https://play.coinpoker.com

---

## 1. Browser Play URL & App Type

- **URL:** `https://play.coinpoker.com`
- **PWA:** Yes. Has `site.webmanifest` with `"display": "standalone"`, 192x192 and 512x512 maskable icons
- **Title:** `coinpoker-web-app`
- **Also available:** Windows desktop client, macOS client, Android app, iOS via browser
- **Landscape blocker:** Mobile web blocks landscape orientation (max-height: 600px)

## 2. Technology Stack (CONFIRMED from source)

### Frontend
- **Framework:** React Native for Web (Expo SDK)
  - Expo app slug: `coinpoker-web-app`
  - `registerRootComponent` from Expo
  - React 19.x (`react.version.startsWith('19.')`)
  - `react-native-web` (2,285 `Text` components, 1,114 `View`, 77 `Pressable`, 77 `TouchableOpacity`)
  - `react-native-gesture-handler`, `react-native-reanimated`, `react-native-worklets` v0.7.1
  - Expo Router for navigation
  - Metro bundler (single 12MB JS bundle at `/_expo/static/js/web/`)

### Rendering
- **DOM-based** via React Native Web (NOT canvas-rendered for UI)
- Minimal canvas usage: only 3x `getContext("2d")`, 5x `drawImage` (likely card/avatar images)
- CSS styling, standard DOM elements
- Body has `overflow: hidden`, `user-select: none`

### Auth
- **Firebase Authentication** (Google Identity Toolkit)
  - Preconnects to `googleapis.com`, `identitytoolkit.googleapis.com`
  - Firebase config with `apiKey`, `authIdToken`, device tokens
  - Auth module: `@pb-auth-module` (PokerBaazi auth module)
  - OAuth endpoint: `https://authapi.educationsacademy.com/coin/oauth/user`

### Analytics
- Google Tag Manager (`GTM-KWBG6NG`)
- DataFast analytics (`datafa.st`)
- Facebook Pixel (`950573196594703`)

## 3. Communication Protocol

### Game Server: SmartFoxServer 2X (SFS2X)
- **Protocol:** WebSocket with SFS2X binary protocol
- **Connection:** `new WebSocket(ws[s]://HOST:PORT/BlueBox/websocket)` with `binaryType = "arraybuffer"`
- **BlueBox:** SFS2X HTTP tunneling fallback (WebSocket primary, HTTP polling fallback)
- **Socket.io** also present (v2.3.0) - possibly for lobby/chat
- **Message format:** SFSObject (binary serialized JSON-like objects)
- **Game commands via:** `ExtensionRequest(eventName, sfsObject, room)`

### API Backend (PokerBaazi "educationsacademy.com" infrastructure)
| Service | URL |
|---|---|
| Auth API | `https://authapi.educationsacademy.com` |
| OAuth | `https://authapi.educationsacademy.com/coin/oauth/user` |
| Game API | `https://game-api.educationsacademy.com` |
| Next-gen API | `https://nxtgenapi.educationsacademy.com` |
| PBShots | `https://nxtgenapi.educationsacademy.com/pbshots` |
| Promotions | `https://nxtgenapi.educationsacademy.com/promotion` |
| Cash Listing | `https://public-cash-listing.educationsacademy.com` |
| Main Lobby | `https://poker-mainlobby.educationsacademy.com` |
| Public API | `https://public-api.educationsacademy.com` |
| Config CDN | `https://d12n4sdz9sv2g7.cloudfront.net/config` |
| Assets CDN | `https://d12n4sdz9sv2g7.cloudfront.net/assets/release` |
| Config CDN 2 | `https://d13rv7s1l7ao12.cloudfront.net/config` |
| Mobile Config | `https://mobile.coinpoker.ai/config/` |
| Casino FE | `https://casino2-fe.coinpoker.com` |
| Casino FE (biz) | `https://casino2-fe.coinpoker.biz` |

### SFS2X Event Protocol (complete TABLE_EVENT map)

**Game Flow Events:**
- `TABLE_INIT` - Initial table state
- `PRE_HAND_START` - Pre-hand setup
- `GAME_START` - Hand begins
- `GAME_READY` - Table ready
- `GAME_DYNAMIC_PROPERTIES` - Dynamic game config

**Player Action Events:**
- `USER_ACTION` - Player action (fold/check/call/raise)
- `USER_TURN` - It's player's turn
- `ADVANCE_PLAYER_ACTION` - Pre-select actions
- `EXTRA_TIME_PREFLOP` - Extra time bank

**Card Events:**
- `HOLE_CARDS` - Player receives hole cards
- `DEALER_CARDS` - Community cards dealt
- `SHOW_CARDS_REQUEST` - Show cards at showdown
- `REVEAL_CARDS_REQUEST` - Reveal cards

**Table Management:**
- `SEAT` / `TAKE_SEAT` / `RESERVE_SEAT` - Seating
- `LEAVE_SEAT` / `QUIT_TABLE` - Leaving
- `SIT_OUT` - Sit out
- `AUTO_POST_BB` - Auto-post big blind
- `STRADDLE` - Straddle option
- `TOP_UP` / `TOP_UP_LOAD` - Buy-in/reload
- `WAITING_LIST` / `WAIT_LIST_STATUS` - Wait list
- `JOIN_SIMILAR_TABLE` - Table change

**Game Info Events:**
- `POT_INFO` - Pot information
- `WINNER_INFO` / `CUMULATIVE_WINNER_INFO` / `TRANSACTION_WINNINGS` - Winner data
- `HAND_STRENGTH` - Hand strength indicator
- `POKER_ODDS` / `POKER_ODDS_DOUBLE_BOARD` - Built-in odds calculator
- `HAND_HISTORY` - Hand history
- `PLAYER_INFO` / `SEAT_INFO` - Player data
- `USER_BALANCE` / `USERS_PROFIT_LOSS` / `USER_STATISTICS` - Stats
- `PLAYER_NOTES` - Player notes

**Special Features:**
- `RIT` / `RIT_INFO` / `INTERACTIVE_RIT_OPTIONS` - Run It Twice
- `RABBIT` - Rabbit hunt (see undealt cards)
- `BOMB_POT_INFO` - Bomb pot
- `SPLASH_THE_POT` / `SHARED_SPLASH` / `MEGA_SPLASH` - Splash promotions
- `EV_CHOP_ACTION` / `EV_CHOP_OPTED_ACTION` - EV chop
- `OFFLOAD_CHIPS` / `RETURN_CHIPS` - Chip management
- `AOF_REMOVE_CHIPS` / `AOF_REMOVE_CHIPS_NEW` - All-In or Fold
- `EMOJI` / `THROW_OBJECT` / `CHATING` - Social features
- `DEALER_CHAT` / `DEALER_CHAT_ACTION` / `MESSAGE` - Chat

**Lobby Events:**
- `JOIN_GAME` / `JOIN_TABLE` / `JOIN_GAME_TABLE` / `JOIN_PRIVATE_TABLE` - Join tables
- `TABLE_LIST` / `LOBBY_STATUS` / `LOBBY_MENU` - Lobby data
- `REGISTER` / `TOURNAMENT_SUBSCRIPTION_DATA` / `TOURNEY_DETAILS` - Tournaments
- `BALANCE_REFRESH` / `USER_BALANCE_INFO` / `USER_DATA_REFRESH` - Account
- `USER_INFO` / `USER_STATISTICS` / `PLAYER_STATUS` - Player data
- `THEME_ENGINE` - Client themes
- `RESTORE_ZOOM_LOBBY` - Zoom poker

**Zoom Poker Events:**
- `ZOOM_GAME_EVENT.LEAVE_GAME` - Leave zoom
- `ZOOM_LOBBY_EVENT.SIT_OUT` - Zoom sit out

## 4. Game State: DOM vs Canvas

**DOM-based rendering** confirmed:
- React Native Web renders to standard DOM elements (`<div>`, `<span>`, etc.)
- Cards, buttons, bet sliders are all DOM components (View, Text, Pressable, TouchableOpacity)
- Very minimal canvas usage (3 instances of `getContext("2d")`) - likely for specific image operations
- Game state is accessible via DOM inspection, React DevTools, and component tree
- **Key implication:** Screen reading can use DOM scraping, not just OCR/vision
- SFS2X events deliver all game data (cards, pots, actions) as structured data through WebSocket

## 5. Anti-Cheat & Blockchain RNG

### Detection Systems
- Dedicated Game Integrity team using ML/AI analysis
- Analyzes every hand for non-human patterns:
  - Decision timing analysis
  - Unnatural action patterns
  - Perfect GTO correlation detection (caught RTA user "LazyAss" via GTOWizard correlation)
- Zero-tolerance for bots, RTA, solvers, colluders, seat-scripters
- Proactive year-round investigation

### Blockchain RNG
- **KECCAK-256 hash function** (Ethereum's hash)
- Decentralized: uses input from ALL players at table to shuffle deck
- Provably fair: every shuffle verifiable on public blockchain
- Validation tools at `coinpoker.com/validation-tools/`:
  - RNG Verification Tool (paste hand ID, see cryptographic proof)
  - Card Validation Tool (see exact deck shuffle for any hand)
  - Can view undealt cards after hand completes
- RNG code is open-source

### Impact on Interception
- Blockchain RNG does NOT affect game data interception - cards still delivered via SFS2X events
- The blockchain only ensures fair dealing, not encrypted transport
- `HOLE_CARDS` event delivers your cards as SFSObject data
- `DEALER_CARDS` delivers community cards
- Standard WebSocket/SFS2X binary protocol - interceptable at transport layer
- The blockchain verification is post-hoc (after the hand), not real-time encryption

### No Client-Side Anti-Cheat
- No process monitoring or integrity checks detected in web client
- No anti-debugging measures in HTML source
- `meta name="robots" content="noindex, nofollow"` (doesn't want search engines indexing play client)
- Web app cannot detect other browser extensions or tabs

## 6. Existing Tools & Open Source

### Official/Built-in Tools (NEW in 2026)
- **Built-in HUD** - Integrated player statistics
- **PokerIntel** - Built-in analytics
- **Showdown Meter** - Shows showdown frequency
- **Skill Score** - Player ranking system
- **Hand Replayer** - Advanced hand review

### Third-Party HUDs (ALLOWED)
- **Hand2Note** - Works directly, no converter needed. Official CoinPoker support.
- **CoinHUD** (coinhud.com) - Overlay tool, compliant with TOS
- **DriveHUD** - Works directly with CoinPoker
- **PokerTracker 4** / **Holdem Manager 3** - Work via converter
- **Advanced CoinPoker Converter** - Converts hand histories for PT4/HM3

### Bot Products (AGAINST TOS)
- **PokerBotAI** (pokerbotai.com) - Advertises CoinPoker bot, uses emulator/device + neural network
- **CrownAI** (crownaipoker.com) - Advertises CoinPoker bot
- Both operate via screen reading (not protocol-level)

### No Open-Source CoinPoker Tools
- No GitHub projects found specifically for CoinPoker
- IgnitionHUD Chrome extension exists for Ignition (potential architecture reference)
- The DOM-based rendering makes a Chrome extension HUD feasible

## 7. Stakes & Player Pool

### Stakes Available
- **NLHE:** $0.01/$0.02 up to high-stakes/nosebleed
- **PLO:** Various stakes including PLO6
- **Special formats:** All-In or Fold, Double Board Bomb Pots, Zoom

### Traffic
- **Peak:** ~5,000 players online
- **Evening (EU):** ~2,800 players
- **Cash game peak:** ~400 concurrent players
- Strongest at micro/low stakes ($0.50/$1 and below)
- Mid/high stakes are player-driven (don't run continuously)
- Best liquidity during European evenings and weekends

### Rewards
- **CoinRewards:** $1.5M weekly rewards pool (launched April 2026)
- Up to 62% effective rakeback (tested by VIP-Grinders)
- 150% deposit bonus

## 8. KYC Requirements for UK Players

- **No mandatory KYC** for playing or depositing
- Only phone number verification required to start playing
- **First withdrawal** may require identity verification (passport + source of funds)
- High-stakes players or suspicious activity may trigger additional verification
- UK gambling winnings are tax-free
- CoinPoker operates without a UKGC license (crypto-based, offshore)
- Curacao/Anjouan gaming license

## 9. Deposit/Withdrawal

### Deposits
- **Crypto:** USDT, USDC, BTC, ETH, SOL, BNB, POL, TRX
- **Fiat:** Credit cards, Apple Pay, Google Pay, PIX
- All deposits converted to USDT at prevailing rate
- Balances denominated in USDT

### Withdrawals
- Crypto wallets only (direct to personal wallet)
- Processing: near-instant (blockchain dependent, up to 24h for large amounts)
- **Limits:** 100,000 USDT per transaction, $500,000/month (casino winnings only, poker unlimited)
- Higher limits available via support

### On-Chain Proof of Reserves
- CoinPoker publishes proof of reserves on blockchain

## 10. Bot Incidents & Bans

### January 2026: 98 Bots Banned, $156K Refunded
- Triggered by Martin Zamani's viral video of Ignition/Bovada bot farm
- CoinPoker proactively investigated and found 98 bot accounts on their platform
- $156,446 redistributed to affected players
- Statement: "Waiting for problems to grow is not our style"

### November 2025: RTA User "LazyAss" Banned
- Account frozen for RTA/solver use
- CoinPoker ambassador publicly released hand data
- Demonstrated correlation between player's decisions and GTOWizard solver output
- Timing analysis showed suspicious alignment with solver queries
- Funds confiscated, account permanently closed

### Affiliate Dispute (2025)
- YourPokerDream published critical article alleging unprofessional handling
- Unresolved commission disputes

---

## Bot/Tool Development Implications

### Best Approach: Chrome Extension + WebSocket Interception
1. **DOM scraping** - React Native Web renders to DOM. Cards, bets, pots, player info all in DOM tree.
2. **WebSocket interception** - SFS2X binary protocol over WebSocket. All game state delivered as structured SFSObject data:
   - `HOLE_CARDS` = your cards
   - `DEALER_CARDS` = community cards
   - `USER_TURN` = action prompt
   - `POT_INFO` = pot size
   - `PLAYER_INFO` = opponent data
   - `WINNER_INFO` = results
3. **Action injection** - `ExtensionRequest(TABLE_EVENT.USER_ACTION, sfsObject, room)` sends actions
4. **No client anti-cheat** in web version - no process monitoring, no debugger detection

### Architecture Comparison
| Feature | CoinPoker | Ignition |
|---|---|---|
| Rendering | React Native Web (DOM) | React (DOM) |
| Protocol | SFS2X binary WebSocket | REST + WebSocket |
| Data access | DOM + WS intercept | DOM data-qa attrs + WS |
| Auth | Firebase + OAuth | Session cookies |
| Anti-cheat | Server-side ML only | Server-side |
| HUD allowed | Yes (officially) | No |
| Built-in HUD | Yes | No |

### PokerBaazi Heritage
- 487+ references to "Baazi/PokerBaazi/pokerbaazi" in bundle
- Auth module: `@pb-auth-module` (PokerBaazi auth)
- API domain: `educationsacademy.com` (PokerBaazi's backend domain)
- Complete PokerBaazi poker engine running under CoinPoker branding
- Same tech stack as India's largest poker platform

### Risk Assessment
- CoinPoker takes integrity seriously (98 bots banned, $156K refunded)
- Server-side ML detection is active and sophisticated
- RTA detection via timing analysis + solver correlation
- BUT: web client has no client-side detection
- HUDs are officially allowed - provides cover for browser extensions
- Key detection vectors: timing patterns, action patterns, GTO correlation
