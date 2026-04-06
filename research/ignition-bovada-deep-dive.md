# Ignition / Bovada / Bodog Deep Dive (PaiWangLuo Network)

**Date:** 2026-04-05
**Purpose:** Technical architecture, open-source tooling, anti-cheat analysis, and bot/tool development feasibility

---

## 1. Technical Architecture

### Browser Poker URL
- **Ignition:** `https://www.ignitioncasino.eu/poker` (lobby), games load at `*.ignitioncasino.eu/static/poker-game/*`
- **Bovada:** `https://www.bovada.lv/poker`
- **Bodog:** `https://www.bodog.eu/poker` (varies by region)
- Mobile shortcut: `m.ignitioncasino.eu/poker/`
- All three share identical client codebase ("PokerAPI" licensed software)
- **No download required** -- full instant play in browser

### Rendering: DOM-Based (NOT Canvas)
- **HTML5 DOM rendering** -- confirmed by both PokerEye+ and IgnitionHUD source code
- Game elements use `data-qa` attributes for semantic identification:
  - Cards: `data-qa="card-As"` (Ace of spades), `data-qa="card-Kh"` (King of hearts)
  - Players: `data-qa="playerContainer-${seatID}"`
  - Player tags: `data-qa="playerTag"`
  - Balances: `span[data-qa="playerBalance"]`
- CSS classes are also used: `.f1so0fyt`, `.f1qy5s7k`, `.f34l2e8` (IgnitionHUD selectors)
- Card format: `[Rank][suit]` where suit = `c/d/h/s` (clubs/diamonds/hearts/spades)
- **This is extremely favorable for our tools** -- DOM scraping is trivial compared to canvas/WebGL

### Communication Protocol
- **Transport:** WebSocket (WSS)
- **Message format:** Reportedly JSON-based and human-readable
- **However:** Both major open-source tools (PokerEye+ and IgnitionHUD) use **DOM scraping, not WebSocket interception**
  - PokerEye+ polls DOM at 100ms tick rate
  - IgnitionHUD uses MutationObserver on DOM elements
- This suggests either:
  1. The WebSocket messages ARE JSON but DOM scraping was easier to implement
  2. The WebSocket format is less clean than reported, and DOM is more reliable
  3. DOM approach avoids any potential WebSocket interception detection
- **Action item:** Open DevTools Network tab on live Ignition session, inspect WebSocket frames to confirm format

### Card Format (from DOM)
```
data-qa="card-As"  -> Ace of spades
data-qa="card-Kh"  -> King of hearts
data-qa="card-2c"  -> 2 of clubs
data-qa="card-Td"  -> 10 of diamonds
```
Standard poker notation: `{A,K,Q,J,T,9,8,7,6,5,4,3,2}{s,h,d,c}`

### Player Action Detection (from DOM)
- Actions read from `data-qa="playerTag"` elements
- Types: `FOLD`, `CHECK`, `CALL`, `BET`, `RAISE`, `ALL-IN`, `SITTING OUT`
- Bet amounts: parsed from `span[data-qa="playerBalance"]` via `parseCurrency()`, tracking balance diffs
- Street detection: MutationObserver on flop/turn/river DOM elements (IgnitionHUD) or polling (PokerEye+)

---

## 2. Open-Source Tools Analysis

### Tool 1: PokerEye+ (vuolo/PokerEye-Plus-for-Ignition-Casino)

| Attribute | Detail |
|---|---|
| **GitHub** | github.com/vuolo/PokerEye-Plus-for-Ignition-Casino |
| **Language** | 64.3% JavaScript, 35.7% TypeScript |
| **Architecture** | Chrome extension + Next.js/tRPC backend API |
| **Interception method** | DOM polling at 100ms tick rate (NOT WebSocket) |
| **Card detection** | SVG `data-qa` attribute parsing (e.g., `data-qa="card-As"`) |
| **Action detection** | `data-qa="playerTag"` element text |
| **Bet tracking** | Balance diff via `span[data-qa="playerBalance"]` + `parseCurrency()` |
| **Stats** | Hand recording + pre-flop GTO recommendation (Jonathan Little charts) |
| **Compatibility** | Ignition, Bovada, Bodog (same PokerAPI client) |
| **Maintained?** | 68 commits total, MIT licensed. Last activity check needed. |
| **Installation** | Paste `chrome-extension/main.js` into browser console (not packaged extension) |

**Key insight:** Despite being called a "Chrome extension," the primary usage is pasting JS into the console. The `chrome-extension/` directory exists but the README directs users to console injection. The backend API handles pre-flop chart lookups via tRPC.

### Tool 2: IgnitionHUD (CaseRegan/IgnitionHUD)

| Attribute | Detail |
|---|---|
| **GitHub** | github.com/CaseRegan/IgnitionHUD |
| **Language** | JavaScript |
| **Architecture** | Proper Chrome extension (Manifest V2, content script) |
| **Interception method** | MutationObserver on DOM elements (NOT WebSocket) |
| **Card detection** | Hole card visibility via CSS opacity changes |
| **Action detection** | Style attribute changes on player seat containers |
| **Bet tracking** | `this.bet.innerHTML.replace(/\D/, '')` (strip non-digits) |
| **Street detection** | MutationObserver on flop/turn/river DOM containers |
| **Stats tracked** | VPIP, PFR, 3bet frequency, hand count |
| **Display** | Draggable stats popups per player |
| **Manifest match** | `*://*.ignitioncasino.eu/static/poker-game/*` |
| **Permissions** | `activeTab` only |
| **Maintained?** | Small repo (4 files), Manifest V2 (deprecated). Likely not maintained. |

**Known bugs:** Blind position tracking breaks when players leave/join (small blind skipped edge case).

### Tool 3: Ignition Poker Screen Reader (wimmeldj/ignition_poker_screen_reader)

| Attribute | Detail |
|---|---|
| **GitHub** | github.com/wimmeldj/ignition_poker_screen_reader |
| **Language** | Python 3.6+ |
| **Architecture** | Standalone Windows application (not browser extension) |
| **Method** | Screen capture + OpenCV pattern matching + Tesseract OCR |
| **Libraries** | OpenCV, NumPy, Tesseract, PyWin32, Pillow |
| **Data extracted** | Game detection, blind sizes, table format (HU/6max/9max), seat positions, stack sizes |
| **Performance** | 1.5s init for 1 table, 3s for 3 tables, 5s for 6 tables |
| **Target** | Desktop client (not browser) |
| **Relevance** | Similar approach to our screen_bot.py -- validates OCR approach for Ignition |

### Tool 4: Ignition Poker Odds (Chrome Web Store)

| Attribute | Detail |
|---|---|
| **Chrome Store ID** | jpjgkfmfbmaahogcicdgooeomcbljgak |
| **Developer** | yoggi1100 |
| **Version** | 1.0.1 |
| **Last updated** | ~2023 (3+ years old) |
| **Features** | Real-time pot odds + hand equity display |
| **Hotkeys** | 'o' = show odds, 'k' = check, 'c' = call, 'f' = fold |
| **Status** | Published on Chrome Web Store (unlike the others) |

### Tool 5: Commercial Tools

- **Ace Poker Solutions "Ignition Card Catcher"** -- captures hand histories from desktop client, converts to PT4/HM format
- **DriveHUD** -- supports Bovada/Ignition HUD with Zone Poker support
- **PokerTracker 4** -- has "Ignition Hand Grabber App" for auto-import + HUD overlay
- **Ignition Hand Converter** -- converts Ignition hand histories to standard PokerStars format for import into tracking software

All commercial tools target the **desktop client** and work by intercepting hand history files, not WebSocket traffic.

---

## 3. Key Features

### Anonymous Tables
- **Every table is anonymous** -- no persistent player IDs
- Players shown as "Player 1", "Player 2", etc. by seat number
- New random identity every time you sit down
- **Impact on opponent profiling:**
  - Cross-session tracking is **impossible** through normal means
  - Within-session tracking works (both PokerEye+ and IgnitionHUD do this)
  - Population-level stats (e.g., average VPIP at $0.10/$0.25) still useful
  - Our player-profiler.js approach (real-time behavioral clustering) is actually MORE valuable here since you can't look up historical stats
  - **Workaround:** Hand history files (downloadable 24h after play) show all hole cards at showdown -- allows offline population analysis

### Multi-Table Support
- **Cash games:** Up to 4 tables simultaneously
- **Zone Poker:** Up to 2 entries (fast-fold, same player pool)
- **Tournaments:** Up to 20 MTTs simultaneously
- **Combined:** Can run 2 Zone + 2 cash = 4 total tables
- **Comparison:** Unibet allows 4 tables, PokerStars browser is single-table only

### Stakes Available (Cash Games)
| Game | Stakes Range |
|---|---|
| NL Hold'em | $0.02/$0.05 to $10/$20 |
| Limit Hold'em | $0.05/$0.10 to $40/$80 |
| Omaha Hi | Available at multiple stakes |
| Omaha Hi/Lo | Available at multiple stakes |
| Zone Poker (fast-fold) | $0.01/$0.02 (2NL) to $5/$10 (1000NL) |

Table sizes: 2-max (heads-up), 6-max, 9-max (full ring)

### Geographic Availability
- **Primary markets:** United States, Australia
- **US restrictions:** NOT available in Delaware, Maryland, Nevada, New Jersey, New York, Washington state
- **Latin America (via Bodog brand):** Argentina, Bolivia, Brazil, Chile, Ecuador, Guatemala, Honduras, Mexico, Nicaragua, Peru, Paraguay, El Salvador, Venezuela
- **UK/EU:** NOT available (use PokerStars or Unibet instead)
- **Offshore/unregulated** -- operates from Costa Rica, Kahnawake Gaming Commission license

### Deposit & Withdrawal
| Method | Deposit | Withdrawal | Speed |
|---|---|---|---|
| Bitcoin (BTC) | Yes ($20 min) | Yes (unlimited max) | ~15 min deposit, ~24h withdrawal |
| Bitcoin Cash (BCH) | Yes | Yes | ~1h withdrawal |
| Litecoin (LTC) | Yes | Yes | ~1h withdrawal |
| Ethereum (ETH) | Yes | Yes | ~1h withdrawal |
| USDT (Tether) | Yes | Yes | ~1h withdrawal |
| Bitcoin SV (BSV) | Yes | Yes | ~1h withdrawal |
| Bitcoin Lightning | Yes | Yes | Fastest |
| Credit/Debit Card | Yes | No | Instant deposit |
| Voucher (P2P) | Yes | Yes | Variable |
| Wire Transfer | No | Yes | 5-10 business days |
| Check by Courier | No | Yes | 10-15 business days |

- **No fees** on crypto transactions (Ignition side)
- One crypto withdrawal per 15 minutes
- **Crypto is strongly preferred** -- fastest, no limits, no fees

---

## 4. Anti-Cheat Analysis

### Browser-Side Protection: MINIMAL

**Evidence of weak client-side security:**
1. Chrome extensions (PokerEye+, IgnitionHUD, Poker Odds) freely inject scripts and read DOM
2. One extension is literally "paste JS into console" -- no Content Security Policy blocking eval
3. DOM uses semantic `data-qa` attributes -- designed for testing, trivially scrapable
4. MutationObserver works without restriction -- no anti-tamper on DOM
5. No evidence of `navigator.webdriver` detection
6. No evidence of CDP/DevTools detection
7. No process scanning (impossible from browser context)
8. Manifest V2 extension with just `activeTab` permission works fine

### Server-Side Protection: WEAK-TO-MODERATE

**What they claim:**
- Behavioral pattern analysis on backend
- Bot account termination (retroactive)

**What the evidence shows:**
- **January 2026 bot farm scandal:** Martin Zamani exposed a massive bot farm (dozens of coordinated accounts) operating on Ignition/Bovada
- The bot farm video showed rows of computers running automated poker tables with no human players
- Ignition claimed the accounts were "already terminated" and the video was "from 2022"
- Zamani countered that tournament dates in the video showed 2024
- **Ignition's response was widely criticized** -- they blamed the whistleblower, not the bots
- A security team member reportedly "berated" pro player Todd Witteles for "exploiting a glitch" when he exposed a bot, while the bot continued to play
- **CoinPoker** (a competitor) responded by banning 98 accounts and refunding $156,446. Ignition offered no refunds.

### HUD Policy
- Ignition's anonymous tables are their primary anti-HUD measure
- They do NOT actively block Chrome extensions or HUD software
- Third-party HUD tools (DriveHUD, PokerTracker) work on the desktop client
- Browser extensions work without issue
- Hand histories are downloadable (with 24h delay) and convertible to standard formats

### Extension Scanning: NONE DETECTED
- No evidence of extension enumeration via `chrome.management` API
- No Content Security Policy blocking inline scripts
- No anti-debugging JavaScript (unlike PokerStars with Jscrambler-style protections)
- No WebSocket protocol obfuscation

### Bot Detection Summary
| Vector | Status |
|---|---|
| Process scanning | None (browser) |
| Extension detection | None detected |
| CDP/DevTools detection | None detected |
| navigator.webdriver check | None detected |
| DOM anti-tamper | None (data-qa attributes wide open) |
| CSP blocking injection | None (console paste works) |
| Behavioral analysis | Minimal/retroactive |
| Bot bans | Retroactive, slow, controversial |
| Timing analysis | Unknown, probably basic |
| Mouse/click analysis | Unknown |

### Known Bot Operations
- **BonusBots / Shanky Holdem Bot:** Commercial poker bot that explicitly supports Ignition/Bovada/Bodog. Version 9.0.1 and 9.6.8 both include Ignition fixes. Uses screen reading (pixel detection) on the desktop client.
- **Bot farm (Zamani exposure):** Coordinated multi-account operation with dozens of simultaneous tables, likely from China
- **Forum discussions (WarbotPoker.com):** Active threads about botting on Ignition/Bovada, including discussions about "topping up money" and the platform being "a great place to play" (for bots)

---

## 5. Comparison to Unibet

| Dimension | Ignition/Bovada | Unibet |
|---|---|---|
| **Interception approach** | DOM scraping (data-qa attrs) | WebSocket JSON parsing |
| **Message format** | DOM is primary; WS reportedly JSON | Clean JSON WebSocket messages |
| **Rendering** | HTML5 DOM elements | HTML5 DOM + Canvas elements |
| **Anti-tamper** | None detected | Minimal |
| **Anonymous tables** | Yes (all tables) | No (persistent usernames) |
| **Extension blocking** | None | None |
| **CDP detection** | None detected | None detected |
| **Multi-table** | 4 cash + 2 Zone | 4 tables |
| **Difficulty** | VERY LOW | LOW |
| **Market** | US + Australia | EU/UK |
| **Stakes** | 2NL to 1000NL | 2NL to 200NL |

### Key Differences for Our Tool Development

1. **DOM vs WebSocket:** Our Unibet tool uses WebSocket message parsing. For Ignition, DOM scraping is the proven approach (all 3 open-source tools use it). This means:
   - We need a content script or console injection, not just WS interception
   - DOM approach is actually MORE reliable -- we see exactly what the player sees
   - But it's slower (polling/MutationObserver vs real-time message stream)

2. **Anonymous tables change our profiling strategy:**
   - On Unibet: track opponents by username across sessions
   - On Ignition: real-time behavioral clustering only (our player-profiler.js is perfect for this)
   - Within-session VPIP/PFR/AF still works
   - Population-level defaults by stake/position are more important

3. **Much weaker anti-cheat:**
   - No need for anti-detect browser (Camoufox etc.)
   - Standard Chrome extension with `activeTab` is sufficient
   - Console injection works as a fallback
   - The site has an active, unpunished bot population -- detection is clearly weak

4. **Hand history availability:**
   - Ignition provides downloadable hand histories (24h delay) with all showdown cards
   - Can be converted to PokerStars format via Ace Poker Solutions converter
   - Useful for offline analysis and strategy backtesting

---

## 6. Recommended Approach for Our Platform

### Phase 1: Browser Extension (Content Script)
```
manifest.json:
  matches: ["*://*.ignitioncasino.eu/static/poker-game/*",
            "*://*.bovada.lv/static/poker-game/*",
            "*://*.bodog.eu/static/poker-game/*"]
  permissions: ["activeTab"]

content_script.js:
  - MutationObserver on player containers + card elements
  - Parse data-qa="card-{rank}{suit}" for hole cards + board
  - Parse data-qa="playerTag" for action tracking
  - Parse data-qa="playerBalance" for stack/bet tracking
  - Track VPIP/PFR/3bet per seat within session
  - Forward game state to our advisor (equity + preflop chart)
```

### Phase 2: WebSocket Inspection
- Open DevTools on live session, examine WebSocket frames
- If JSON: build dual-mode reader (WS primary, DOM fallback)
- If binary: stick with DOM approach (proven, reliable)

### Phase 3: Screen Bot (Alternative)
- Our existing screen_bot.py approach already works for Ignition desktop client
- The wimmeldj/ignition_poker_screen_reader validates this approach
- For browser: DOM scraping is superior to screen reading

### Phase 4: Multi-Table Bot
- 4 cash tables + 2 Zone tables = 6 simultaneous games
- DOM scraping per table via content script
- CFR50 strategy + real-time profiling per seat
- Anonymous tables actually help us -- opponents can't track OUR patterns either

---

## 7. Sources

- [PokerEye+ for Ignition Casino (GitHub)](https://github.com/vuolo/PokerEye-Plus-for-Ignition-Casino)
- [IgnitionHUD (GitHub)](https://github.com/CaseRegan/IgnitionHUD)
- [Ignition Poker Screen Reader (GitHub)](https://github.com/wimmeldj/ignition_poker_screen_reader)
- [Ignition Poker Features](https://www.ignitioncasino.eu/poker/features)
- [Ignition Anonymous Tables](https://www.ignitioncasino.eu/poker/incognito-poker)
- [Ignition Multi-Table Guide](https://www.ignitioncasino.eu/poker/strategy/how-multi-table-poker-6-tips)
- [Ignition Crypto FAQ](https://www.ignitioncasino.eu/help/cryptocurrency-faq)
- [Ignition Restricted States](https://www.ignitioncasino.eu/help/common-faq/what-states-are-restricted)
- [Ignition Bot Farm Exposed (Primedope)](https://www.primedope.com/ignition-bots-finally-exposed/)
- [Bot Farm Video Goes Viral ($156K refunded by CoinPoker)](https://www.poker.org/latest-news/massive-bot-farm-video-goes-viral-as-one-site-repays-156k-to-players-aWqvk2n1dOJE/)
- [Martin Zamani Exposes Bot Farm (CardPlayer)](https://www.cardplayer.com/poker-news/1633558-poker-pro-martin-zamani-exposes-massive-online-poker-bot-farm)
- [Ignition Bot Response (GipsyTeam)](https://www.gipsyteam.com/news/21-01-2026/ignition-poker-bots)
- [BonusBots Holdem Bot 9.6.8 Ignition Support](https://bonusbots.com/updates/holdem-bot-9-6-8-re-supports-ignition-bovada-and-bodog/)
- [PokerTracker Ignition Hand Grabber](https://www.pokertracker.com/guides/PT4/third-party-apps/ignition-hand-grabber-guide)
- [DriveHUD Bovada/Ignition Support](https://drivehud.com/bovada-ignition-poker-hud-support-for-zone-poker/)
- [Ignition Poker Odds (Chrome Web Store)](https://chromewebstore.google.com/detail/ignition-poker-odds/jpjgkfmfbmaahogcicdgooeomcbljgak)
- [Bodog Network Countries Guide](https://worldpokerdeals.com/blog/bodog-poker-countries-guide)
- [PokerTracker Ignition Config Guide](https://www.pokertracker.com/guides/PT4/site-configuration/ignition-configuration-guide-bovadabodog)
- [Pokerfuse Bot Discussion Thread](https://pokerfuse.com/the-rail/2026/4/?post=3892)
- [WarbotPoker Forum - Ignition Discussions](https://www.forum.warbotpoker.com/viewtopic.php?t=368)
