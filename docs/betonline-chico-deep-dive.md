# BetOnline / Chico Network / Connective Games — Deep Dive (April 2026)

## 1. Browser Play URL & Instant Play

- **Direct URL**: `poker.betonline.ag/mobilepoker/index.htm`
- BetOnline hides the no-download option from desktop users — you must type the URL directly
- Full game access: cash games, tournaments, up to **4 tables simultaneously** in browser
- No client download required; registration and deposits can be done in-browser
- Same URL pattern works for TigerGaming and SportsBetting (same Connective Games platform)

## 2. Technology Stack

- **Connective Games PWA** (Progressive Web App) — browser-based, app-like experience
- **HTML5 Canvas rendering** — the game table, cards, chips, and animations are drawn on `<canvas>`
- The client is a compiled JavaScript SPA that loads a bundled JS payload
- No Flash — fully HTML5, mobile-first design
- **UI chrome** (lobby, cashier, menus) uses standard DOM elements
- **Game table** is canvas-only — card values, pot size, bet amounts, player names are all pixel-rendered
- Connective Games won "Mobile Poker Product of the Year" (IGA) and "Best Poker Software Supplier" (EGR B2B)
- The PWA can be "installed" as a home-screen app on mobile

## 3. Communication Protocol

- **WebSocket** for real-time game state (cards, actions, pot updates)
- **REST/HTTP** for authentication, lobby, cashier, session management
- WebSocket messages use a **custom binary protocol** (not plain JSON)
- Message types: hand start, deal, action prompt, player action, showdown, chat
- The binary format is proprietary Connective Games — no public documentation exists
- **Interception approach**: Chrome DevTools Network tab shows WS frames; Fiddler Everywhere can capture WS traffic; monkey-patching `WebSocket` constructor before page load can intercept all messages

## 4. Game State: DOM vs Canvas

- **Canvas-rendered** — you CANNOT read game state from the DOM
- Card values, pot size, bet amounts, player names, action buttons are all drawn pixels
- For tool development, two viable approaches:
  1. **Screen reading** (OCR/template matching on canvas pixels) — our existing pipeline works here
  2. **WebSocket interception** (decode binary protocol for direct game state access) — higher effort, higher reward
  3. **Canvas API injection** (`getImageData()` via injected JS) — middle ground
- The lobby/table list IS in the DOM and can be scraped for table selection automation

## 5. Anti-Cheat Measures

### What they DO:
- **Poker Fraud Team** with dedicated gaming professionals
- **Behavioral analysis**: monitor betting patterns, timing, session length
- **Cross-account detection**: shared player pool across BetOnline/TigerGaming/SportsBetting means bans propagate
- **Account blocking**: Warbot users report accounts blocked "the next day" after connecting bot to desktop client
- **Proprietary hand history format**: non-standard format prevents direct PT4/HM3 integration without converters
- **Browser client restrictions**: no hand history export, session-level logs only (erased on close)
- **Anonymous tables**: at many stakes, players shown as "Player 1", "Player 2" — prevents cross-session tracking

### What they DO NOT do (based on available evidence):
- **No process scanning** reported (unlike GGPoker)
- **No browser extension detection** reported
- **No CDP detection** reported
- **No canvas fingerprinting** reported
- **No known window enumeration** or registry scanning
- Detection appears primarily **server-side behavioral** rather than client-side intrusive

### Risk assessment: **LOW-MEDIUM**
- Desktop client: MEDIUM risk (Warbot users banned, likely process-level detection or behavioral)
- Browser client: LOW risk (no client-side detection reported, screen reading is invisible to server)

## 6. Existing Open-Source Tools & HUDs

### Commercial tools:
| Tool | Type | How it works |
|---|---|---|
| **BetOnline Card Catcher** (Ace Poker Solutions) | Hand grabber + HUD | Captures hands in real-time from desktop client, converts to PT4/HM3 format. 7-day free trial. |
| **Advanced Chico Converter** (Pokerenergy) | Hand history converter | Batch converts Chico format hand histories to standard format |
| **Holdem Indicator** | HUD | Only HUD officially working on BetOnline (no HM3/PT4 native support) |
| **DriveHUD BetOnline Card Catcher** | Hand grabber | Similar to Ace Poker Solutions version |

### Bot tools (from forum research):
| Tool | Status | Notes |
|---|---|---|
| **Warbot** | Supports BetOnline/Chico | Screen-reading bot. Users report bans within 24 hours on desktop client |
| **BonusBots (Shanky)** | Supports BetOnline | Screen-reading bot since 2006. Requires 100% display scaling, single monitor |
| **BonusBots** | Announced BetOnline support | Commercial screen-reading poker bot |

### GitHub:
- **HHSmithy/PokerHandHistoryParser** — parses hand history text logs including some Chico support
- **uoftcprg/phh-dataset** — poker hand history dataset (may include Chico hands)
- No public WebSocket protocol decoders or Connective Games RE projects found

## 7. HUD Blocking

Yes, BetOnline **intentionally blocks HUDs** in the browser client:
- Browser client provides **no hand history access** — logs are session-only and erased on close
- Neither HoldemManager nor PokerTracker natively support BetOnline
- The proprietary hand history format requires third-party converters
- HUD data is only stored at session level and resets when you leave the table
- **Only Holdem Indicator** is listed as a working HUD (uses screen reading, not hand history parsing)
- Card catchers work by intercepting the desktop client — they do NOT work on the browser client

**Implication for us**: Screen reading is the only viable approach for the browser client. Our YOLO + template matching pipeline is well-suited.

## 8. Stakes & Player Pool

### Cash games:
- Range: **$0.01/$0.02 to $10/$20+** (up to $1,000/$2,000 NL available at peak)
- Heaviest traffic at **$1/$2 and $2/$5 NLHE** and **$2/$5 PLO**
- Unlike most sites, traffic concentrates at medium stakes rather than micros

### Tournaments:
- Buy-ins: **$1.10 to $215**
- Guarantees up to **$150,000** (Sunday Showdown, Main Event)
- Mystery Bounty and turbo formats available

### Player pool:
- **2,500+ concurrent** during peak hours
- **4,000+** during major events
- Shared pool across BetOnline + TigerGaming + SportsBetting (up to 3,000 simultaneous connections)
- Games considered **soft** due to sportsbook/casino crossover traffic (recreational players)

## 9. KYC Requirements & UK Players

### KYC:
- **Not required to deposit or play**
- **Required to withdraw**: passport/driver's license + utility bill/bank statement
- Standard offshore KYC — less strict than regulated sites

### UK Players:
- **BetOnline: BLOCKED for UK residents**
- **TigerGaming: ACCEPTS UK players** (same network, same player pool, Curacao license OGL/2024/1132/0522)
- **SportsBetting: US-focused alternative**
- For UK access to the Chico network, use TigerGaming

## 10. Deposit/Withdrawal — Crypto Options

- **90%+ of transactions are cryptocurrency**
- Supported: **Bitcoin, Ethereum, Tether (USDT), USDC, Litecoin, Ripple, Stellar, Chainlink**, and 12+ other cryptos
- **Crypto deposits**: instant, no fees, high limits
- **Crypto withdrawals**: minimum $20 (most cryptos), $200 minimum for USDC/USDT/ETH
- **Maximum withdrawal**: $100,000
- **Fiat alternatives**: credit card, bank wire, money order (higher fees, slower)
- Crypto is clearly the preferred and fastest method

## 11. Known Bot Incidents & Bans

### Documented incidents:
- **Warbot users banned on TigerGaming/BetOnline**: Multiple forum reports of accounts blocked within 24 hours of connecting bot to desktop client. One user banned from TigerGaming without even running bot on that skin (cross-network detection).
- **Chico Network Anti-Bot Policy announcement**: Formal policy published prohibiting "any software, artificial intelligence or tools" used to gain unfair advantage. Penalties: account closure, funds confiscation, permanent ban.
- **Poker Fraud Team**: Dedicated team with "skilled gaming professionals" — detection practices "continually being updated."
- **No major public scandal** comparable to ACR's Venom bot incident (2024). BetOnline's bot problem appears smaller-scale or less publicized.

### Detection method analysis:
- Desktop client bans happen fast (next day) — suggests client-level detection on desktop
- Browser client bans not widely reported — suggests weaker detection in browser
- Behavioral analysis likely in play for both (bet sizing patterns, timing consistency, session patterns)

---

## Connective Games / Chico Network Platform Analysis

### Network skins (3 active):

| Skin | Market | License | UK Access |
|---|---|---|---|
| **BetOnline** | US-focused (flagship) | Panama | No |
| **TigerGaming** | Global (excl. US) | Curacao | **Yes** |
| **SportsBetting** | US (backup for BetOnline) | Panama | No |

### Is the protocol the same across all skins?
- **Yes** — all three skins run identical Connective Games software
- Same WebSocket protocol, same canvas rendering, same game engine
- Shared player pool means you play against all three skins' players simultaneously
- Card catchers and converters work across all three skins
- A tool built for BetOnline works on TigerGaming and SportsBetting with zero modification

### Desktop client vs browser:
| Feature | Desktop | Browser (PWA) |
|---|---|---|
| Hand histories | Yes (proprietary format) | No (session-only, erased on close) |
| HUD support | Via card catchers (Ace Poker, DriveHUD) | None — screen reading only |
| Multi-table | Yes | Yes (up to 4) |
| Detection risk | MEDIUM (Warbot bans reported) | LOW (no client-side detection reported) |
| Bot tool compatibility | Warbot, BonusBots, card catchers | Screen reading bots only |

### Reverse engineering write-ups:
- **No public RE write-ups found** for Connective Games protocol
- No published binary protocol specs or decoders on GitHub
- The proprietary hand history format has been reverse-engineered by commercial converters (Ace Poker Solutions, Pokerenergy) but these are closed-source
- Warbot forum discussions mention connecting to BetOnline but no protocol details shared
- **Best approach**: WebSocket frame capture via Chrome DevTools, then manual binary analysis

### Connective Games as a company:
- **Malta-based** (Connective Games Malta)
- Powers **5 of the top online poker rooms** (third biggest poker liquidity network globally after PokerStars and IDN)
- In-house development, mobile-first approach
- RNG certified by Gaming Labs International and iTech Labs
- New client launched 2016, replacing old Action Poker inherited client
- Exploring blockchain/crypto integration

---

## Actionable Technical Summary for Bot/Tool Development

### Recommended approach for BetOnline browser client:

1. **Screen reading via our existing pipeline** (YOLO + CNN + OCR)
   - Canvas rendering means all visual elements are pixel-accessible
   - Our template matching needs BetOnline card templates (capture session needed)
   - Table layout differs from PS — need new coordinate mapping
   - LOW detection risk in browser

2. **WebSocket interception** (higher effort, higher reward)
   - Monkey-patch `WebSocket` constructor via Chrome extension or CDP injection
   - Capture binary frames, reverse-engineer message types
   - Direct game state access without OCR latency/errors
   - No known CDP detection — safe to use DevTools protocol

3. **Multi-skin deployment**
   - TigerGaming for UK access (same protocol, same player pool)
   - One tool works across all 3 Chico skins
   - Browser client preferred over desktop (lower detection risk)

4. **Key advantages over PokerStars**:
   - No process scanning or client-side detection
   - No browser extension detection
   - Soft player pool (casino/sportsbook crossover)
   - Crypto deposits/withdrawals (fast, pseudonymous)
   - Anonymous tables at many stakes (less tracking risk)
   - Multi-table in browser (PS is single-table only)

5. **Key risks**:
   - Behavioral analysis is active (vary timing, bet sizing)
   - Cross-network bans (BetOnline ban = TigerGaming ban = SportsBetting ban)
   - KYC required for withdrawal
   - UK blocked on BetOnline specifically (use TigerGaming)

---

## Sources

- [BetOnline Poker](https://www.betonline.ag/poker)
- [BetOnline Instant Play URL](https://www.beatthefish.com/betonline-poker-review/)
- [BetOnline Card Catcher Manual](https://acepokersolutions.com/betonline-catcher-manual/)
- [BetOnline HUD Catcher](https://acepokersolutions.com/betonline-hud-catcher/)
- [BetOnline Deposit/Withdrawal Guide](https://worldpokerdeals.com/blog/betonline-withdrawals-rules)
- [BetOnline Restricted Countries](https://worldpokerdeals.com/blog/betonline-countries-guide)
- [BetOnline Help - Poker Settings](https://help.betonline.ag/en/articles/185273-poker-settings)
- [BetOnline Help - Hand History](https://help.betonline.ag/en/articles/185267-how-to-review-hand-history)
- [Chico Network Skins 2026](https://worldpokerdeals.com/online-poker-networks/chico-review)
- [Chico Network Review](https://professionalrakeback.com/chico-poker-network)
- [Chico Network Anti-Bot Policy](https://professionalrakeback.com/bot-policy-betonline-sportsbetting-tigergaming-chico-poker-network)
- [Chico Network Software Policy Changes](https://worldpokerdeals.com/blog/chico-network-software-policy-what-has-changed-recently)
- [Connective Games](https://www.connectivegames.com/)
- [Connective Games Poker](https://www.connectivegames.com/poker)
- [Poker Sites Without HUDs](https://www.beatthefish.com/poker-sites-without-huds/)
- [BetOnline Legitimacy Review](https://professionalrakeback.com/is-betonline-rigged-or-legit)
- [Warbot Forum - BetOnline/TigerGaming](https://forum.warbotpoker.com/viewtopic.php?t=1378)
- [Warbot Forum - TigerGaming Ban](https://forum.warbotpoker.com/viewtopic.php?t=179)
- [BonusBots BetOnline Support](https://bonusbots.com/updates/poker-bot-for-betonline-poker-is-here/)
- [HHSmithy/PokerHandHistoryParser (GitHub)](https://github.com/HHSmithy/PokerHandHistoryParser)
- [Anonymous Poker Sites 2026](https://gamingamerica.com/online-casinos/poker/anonymous)
- [BetOnline 2025 HUD Features](https://www.betonline.ag/2025-features)
