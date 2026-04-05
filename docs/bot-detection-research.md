# Bot Detection Methods — Research Summary

## Sources
- [How poker rooms catch bots: detection methods 2026](https://pokerbotai.com/docs/how-poker-rooms-catch-bots/)
- [GGPoker Security Ecology Policy](https://ggpoker.com/network/security-ecology-policy/)
- [AI's Role in Preventing Online Poker Cheating](https://downnews.co.uk/ais-role-in-identifying-and-preventing-online-poker-cheating/)
- [Poker Bots in 2025: Threats, Detection, Strategies](https://thepokeroffer.com/poker-bots-2025-detection-threats-strategies/)
- [PartyPoker Bot Crackdown](https://rakerace.com/news/poker-rooms/2025/04/14/behind-the-scenes-partypoker-s-biggest-crackdown-on-bots-in-recent-years)
- [GGPoker client-side vulnerability exploit](https://www.poker.org/latest-news/ggpoker-bans-player-confiscates-funds-after-discovering-exploitation-of-client-side-vulnerability-am4fB7L0nTOf/)
- [Dickreuter/Poker - open source bot for PS/GG/Party](https://github.com/dickreuter/Poker)
- [PokerBotAI documentation](https://pokerbotai.com/docs/)

---

## 1. Three-Level Detection Architecture

Poker rooms detect bots at three levels:

### Level 1: Technical (Environment)
- **Hardware fingerprinting**: MAC address, serial numbers, hardware IDs
- **Virtualization detection**: VirtualBox, VMware, Hyper-V, emulators
- **Process scanning**: GGPoker scans running processes for known bot software, solvers, RTA tools
- **Device fingerprinting**: Browser/client fingerprint, TLS fingerprint
- **IP analysis**: Datacenter IPs flagged, VPN detection, geo-mismatch
- **Network protocol analysis**: Sequence numbers, timing, missing client telemetry

### Level 2: Behavioral (Gameplay)
- **Timing patterns**: Decision speed, variance, consistency across sessions
- **Mouse movement**: Trajectory curves, acceleration, click precision
- **Bet sizing analysis**: Exact pot fractions vs human rounding
- **Action distribution**: VPIP/PFR/AF consistency (bots are too stable)
- **Session patterns**: Play duration, break frequency, time of day
- **Tilt resistance**: No change after bad beats = bot signal
- **Solver correlation**: Stats compared against GTO solver output profiles
- **Sequence analysis**: Not individual actions but patterns across hands

### Level 3: Manual Review
- Player complaints trigger investigation
- Admin reviews hand history
- Cross-referencing with known bot profiles
- Account sharing detection

---

## 2. GGPoker-Specific Measures

### Client-Side Scanning
- GGPoker reserves right to **scan user's machine** during play
- Scans for prohibited software: Mouse without Borders, ShareMouse, Synergy, Input Director
- Compiles **composite mesh** of file signatures to match against known bot profiles
- Bans propagate across entire GGNetwork (Natural8, 7XL, etc.)

### Prohibited Tools
- RTA, bots, solvers, charts, HUDs
- Multi-device control software
- Screen sharing tools during play

### Anti-Cheat Features
- **No Turn, No Show (NTNS)**: Cards not revealed until it's your turn — breaks pre-loading bots
- **GTO Wizard partnership**: Server-side stat comparison against solver outputs
- **Fair Play Check system**: Automated behavioral profiling

### Notable Actions
- 40+ accounts banned in 2020, $1.2M confiscated
- Player exploited client-side vulnerability to intercept game packets — detected and banned
- Ongoing "war on solvers and RTA"

---

## 3. Detection Signals We Should Test Against

### Timing
| Signal | Human | Bot |
|--------|-------|-----|
| Decision time mean | 3-8 seconds | 0.5-2 seconds |
| Decision time variance | High (σ > 2s) | Low (σ < 0.5s) |
| Pre-action mouse movement | Yes | Often none |
| Time to first action after deal | Variable | Consistent |

### Mouse/Input
| Signal | Human | Bot |
|--------|-------|-----|
| Mouse trajectory | Curved, with corrections | Straight line or jump |
| Click position | Varies within button | Exact center or fixed offset |
| Mouse idle during wait | Random movements | Frozen or absent |
| Scroll/hover behavior | Present | Absent |

### Betting
| Signal | Human | Bot |
|--------|-------|-----|
| Bet sizes used | 5-15 distinct | 1-3 |
| Pot fraction precision | Approximate | Exact |
| Amount rounding | To $0.05/$0.10 | Exact cents |
| Sizing patterns | Context-dependent | Formula-based |

### Session
| Signal | Human | Bot |
|--------|-------|-----|
| Session length | 30-120 min | 4-24 hours |
| Break frequency | Every 30-60 min | Never or exact intervals |
| Tables played | 1-4 | 6-24 |
| Time of day | Varies | 24/7 |

---

## 4. Implications for Our Systems

### For Our Bot (universal_bot.py)
- [x] Varied bet sizing (implemented — score dropped 92→59)
- [x] Tilt simulation (implemented)
- [x] VPIP within human range (implemented — 18.8%)
- [ ] Mouse movement curves (pyautogui.moveTo with duration)
- [ ] Session length limits (stop after 45-90 min, take breaks)
- [ ] Click position variance within button area
- [ ] Pre-action mouse hover over cards
- [ ] Occasional "misclick" or cancel
- [ ] Don't play 24/7 — realistic schedule

### For Our Detection System (bot-detector.js)
- [x] Bet size precision/entropy (implemented)
- [x] VPIP/PFR stability (implemented)
- [x] Tilt resistance (implemented)
- [ ] Timing entropy (need real timing data)
- [ ] Mouse trajectory analysis (need input telemetry)
- [ ] Session pattern analysis (duration, breaks, schedule)
- [ ] Solver correlation score (compare against GTO baselines)
- [ ] Cross-session consistency (same player over multiple sessions)

### For Our Lab Client (visual parity)
- The NTNS feature is interesting — our lab could implement this for testing
- Client scanning means our bot should NOT run on the same machine as the poker client
- VM isolation (Proxmox) is the right approach for running bots

---

## 5. Known Bot Software (for detection profiling)

### Open Source
- **Dickreuter/Poker** (GitHub): OpenCV screen reading, genetic algorithm + Monte Carlo, works on PS/GG/Party
- **PokerBotAI**: Commercial, documents detection evasion techniques

### Common Architectures
1. **Screen reading + click**: MSS/PIL capture → OpenCV detection → pyautogui click
2. **Memory reading**: Direct process memory access (most detectable)
3. **API injection**: Hook into client's network protocol (GGPoker caught this)
4. **VM-based**: Run poker client in VM, bot reads VM screen from host (hardest to detect)

### Evasion Techniques Claimed
- Random delays between 1-8 seconds
- Bezier curve mouse movements
- Click position randomization ±5px
- Session limits with random break times
- Multiple hardware IDs via VM rotation
- IP rotation via residential proxies
