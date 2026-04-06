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

### Todo — Infrastructure
- [ ] Multi-table support — one WS reader + overlay per table
- [ ] Hand history logging — save all hands for post-session review + leak analysis
- [ ] Session review — flag bad advisor recommendations post-session
- [ ] Bet sizing optimization — size bets based on opponent tendencies (bigger vs calling stations, smaller vs nits)

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
