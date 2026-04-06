# Next Session: Unibet WS Advisor

## STATUS: Live and working via WebSocket. Cards 100% accurate.

## Kanban

### Done
- [x] WebSocket game state reader (100% card accuracy)
- [x] Subprocess overlay (DPI-aware, stays on top)
- [x] Preflop chart with position detection
- [x] Equity neural net (board-texture aware)
- [x] Facing bet detection (raise above BB)
- [x] BB CHECK override (only BB can check preflop)
- [x] Fold clears overlay
- [x] Board danger adjustment (cautious on scary boards)
- [x] Bet sizing with stack cap
- [x] BB/hr tracking
- [x] CHECK/CALL and CHECK/FOLD postflop advice

### In Progress
- [ ] Fix CHECK when need to CALL (preflop: base advisor returns CHECK)
- [ ] Position detection stability (locks per hand but initial detection may be wrong)

### Todo — Required for Profitable Bot
- [ ] Pot odds calculation — compare call price vs pot to determine +EV calls
- [ ] Opponent action weighting — discount equity 20-30% when opponent bets big (they don't bet big with random hands)
- [ ] CFR strategy integration — use trained 6-max CFR (3M info sets) for action decisions instead of static thresholds
- [ ] Dynamic preflop ranges — adjust open/call ranges based on table dynamics (tight table = wider, loose table = tighter)

### Todo — Opponent Modeling
- [ ] Opponent profiling — track VPIP/PFR/AF per player across sessions (scripts/opponent-profiles.json exists)
- [ ] Opponent range estimation — weight opponent's likely holdings based on their actions this hand
- [ ] Player type classification — FISH/NIT/TAG/LAG/WHALE (scripts/player-profiler.js exists)
- [ ] Nit detection — fold more vs nit bets, bluff more vs nits

### Done — Bot Improvement
- [x] CFR strategy integration — 50-bucket, 1.3M info sets, fuzzy matching (13/14 test hands)
- [x] Pot odds calculation — +EV/-EV display
- [x] Opponent action weighting — discount equity on big bets
- [x] Board danger adjustment — cautious on scary boards

### Todo — Bot Improvement (Priority)
- [ ] Train BIG CFR — 100-bucket, 100M+ iterations on cloud/Proxmox (~€8 Hetzner or 3hr Proxmox)
- [ ] Opponent profiling — track VPIP/PFR/AF per player, classify FISH/NIT/TAG/LAG
- [ ] Move up stakes — validate profitability at 2NL, then 5NL → 10NL → 25NL
- [ ] Fix remaining CHECK/CALL timing issue

### Todo — Infrastructure
- [ ] Multi-table support — one WS reader + overlay per table
- [ ] Hand history logging — save all hands for post-session review + leak analysis
- [ ] Session review — flag bad advisor recommendations post-session
- [ ] Bet sizing optimization — size bets based on opponent tendencies (bigger vs calling stations, smaller vs nits)

### Todo — Multi-Site Expansion

#### CoinPoker — PRIORITY (HUDs officially allowed, UK-friendly, lowest risk)
- [ ] Create CoinPoker account (phone only, no KYC to play)
- [ ] Deposit via USDT
- [ ] Intercept SFS2X WebSocket (binary over ws, `BlueBox/websocket` endpoint)
- [ ] Parse 58 TABLE_EVENTs: HOLE_CARDS, DEALER_CARDS, USER_ACTION, USER_TURN, POT_INFO, etc.
- [ ] DOM scraping fallback (React Native Web = standard DOM elements)
- [ ] Build CoinPoker game state reader (same interface as UnibetWSReader)
- [ ] Wire to existing strategy engine (preflop chart + equity + CFR)
- NOTE: HUDs officially allowed (Hand2Note, DriveHUD, PT4). Bots banned but detection is server-side ML only. No client-side anti-cheat. $156K refunded to players after bot ban = they take it seriously but detection is behavioral only.

#### Ignition/Bovada — EASY (DOM scraping, proven tools)
- [ ] Set up VPN (US exit node)
- [ ] Create account + crypto deposit (BTC/USDT)
- [ ] Chrome extension with MutationObserver on data-qa attributes
- [ ] Read cards directly from DOM: `data-qa="card-As"` = Ace of spades
- [ ] Port PokerEye+/IgnitionHUD approach to our strategy engine
- [ ] Population-based profiling (anonymous tables, no cross-session tracking)

#### BetOnline/TigerGaming — HARD (binary protocol, canvas rendering)
- [ ] Create TigerGaming account (UK-friendly skin, same Chico network)
- [ ] Investigate binary WebSocket protocol (no public RE exists)
- [ ] Fallback: screen reading via YOLO + CNN (canvas-rendered)
- [ ] Or: monkey-patch WebSocket constructor via CDP to intercept raw frames
- [ ] Low priority — defer until Unibet + CoinPoker + Ignition working

#### PokerStars Browser — HARDEST (defer)
- [ ] Binary/obfuscated protocol, Flutter/CanvasKit rendering
- [ ] 95%+ proactive bot detection rate
- [ ] Defer until all other sites operational

#### Cross-Site Infrastructure
- [ ] Universal game state interface — abstract layer so strategy engine works across all sites
- [ ] VPN management — auto-rotate for Ignition sessions
- [ ] Crypto bankroll — USDT wallet for cross-site deposits/withdrawals
- [ ] Site-specific humanization profiles (different timing patterns per site)

### Todo — Hive Mind Controller
- [ ] Central orchestrator that manages bot instances across multiple venues simultaneously
- [ ] Spin up/down bot sessions based on: table availability, player pool quality, time-of-day EV
- [ ] Bankroll management across sites — auto-distribute funds to highest-EV venue
- [ ] Session scheduling — optimal hours per site (fish are on evenings/weekends)
- [ ] Risk management — stop-loss per session, per site, per day
- [ ] Multi-identity management — different accounts/profiles per venue
- [ ] Telemetry dashboard — live P&L, bb/hr per table, per site, aggregate
- [ ] Auto table selection — join tables with highest fish-to-reg ratio
- [ ] Load balancing — spread across sites to avoid detection patterns
- [ ] Proxmox deployment — run bot instances on the server, not desktop

### Todo — 24/7 Operations (Anonymous Sites)
- [ ] **Account rotation** — 3+ Ignition accounts, one per 8hr shift, never overlap
- [ ] **VPN rotation** — different US region per shift (East/West/Central), auto-switch
- [ ] **Browser fingerprint isolation** — fresh Chrome profile per shift (user-data-dir rotation)
- [ ] **Session randomization** — vary session length (4-10hr), start time (±2hr), break frequency
- [ ] **Crypto wallet rotation** — separate wallets per account, tumbler for withdrawal aggregation
- [ ] **Headless Chrome on Proxmox** — run 24/7 without desktop, xvfb for virtual display
- [ ] **Health monitoring** — auto-restart crashed bots, alert on stop-loss, alert on detection signals
- [ ] **Shift scheduler** — cron-based: start shift → launch Chrome + VPN → join tables → play → cash out → rotate
- [ ] **Earnings aggregation** — daily/weekly P&L across all shifts/sites/accounts
- [ ] **Detection avoidance patterns**:
  - Random fold timing (2-8s, not constant)
  - Occasional "mistake" plays (1-2% frequency)
  - Vary table count per session (2-4, not always max)
  - Skip some +EV hands to look human
  - Take random breaks (stand up from table, rejoin after 5-15 min)

### Revenue Projections
```
Phase 1 — Manual advisor (NOW):
  Unibet 4 tables × 2NL = ~$2.80/hr = ~$22/day (8hr session)

Phase 2 — Semi-auto multi-site:
  Unibet 4 tables + CoinPoker 4 tables = ~$5.60/hr = ~$45/day

Phase 3 — 24/7 Ignition bot farm:
  3 shifts × 4 tables × 10NL = ~$8.40/hr × 24hr = ~$200/day
  Plus Unibet + CoinPoker = ~$250/day total

Phase 4 — Scale stakes:
  Move profitable bots to 25NL-50NL
  3 shifts × 4 tables × 25NL = ~$500/day
  Target: £100/day achieved at Phase 3, exceeded at Phase 4
```

## End Goal
**£100/day combining poker + HL across multiple sites and tables.**
At 10bb/100 win rate:
- 25NL × 6 tables = ~£10.50/hr = £84/8hr session
- 50NL × 3 tables = ~£10.50/hr = £84/8hr session
- Multi-site (Unibet + PS + Ignition) spreads risk and increases table availability

## Launch
```bash
taskkill /F /IM python.exe
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe -u vision/advisor_ws.py
```

## Architecture
```
Chrome CDP → Node bridge (cdp-ws-bridge.js) → XMPP WebSocket messages
  → UnibetWSReader (unibet_ws.py) → parses cards, bets, position
  → advisor_ws.py → preflop chart + equity + board danger
  → subprocess overlay (overlay_process.py)
```

## Key Files
- vision/advisor_ws.py — WS-based advisor (main)
- vision/unibet_ws.py — WebSocket game state reader
- vision/overlay_process.py — subprocess overlay
- scripts/cdp-ws-bridge.js — Node CDP bridge
- vision/preflop_chart.py — preflop ranges
- vision/advisor.py — base advisor (equity, CFR, board danger)
