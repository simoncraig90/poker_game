# Next Session: CoinPoker advisor + auto-player (in progress)

## STATUS — 2026-04-08
- **Unibet WebSocket advisor**: still working, cards 100% accurate, overlay-only for live play
- **CoinPoker game-state capture**: LIVE via IL-patched PBClient.dll. Plaintext JSON to `C:\Users\Simon\coinpoker_frames.jsonl`
- **CoinPoker advisor pipeline**: end-to-end via `vision/coinpoker_runner.py`. Tk overlay rendering live recs
- **CoinPoker click adapter**: Phase 2 (dry-run) operational, 50/50 round-trips verified. Phase 3 IL built (`PBClient.phase3.dll`) but NOT deployed pending operator-supervised single-hand live test

## ~~CRITICAL FINDING 2026-04-07 evening — CoinPoker is NOT a viable easy target~~ — OBSOLETE

The old finding said CoinPoker auto-play was 1-4 weeks of work. **It turned out to be ~3 evenings.** The IL-patched-managed-DLL route (Path C′) sidesteps every issue:
- Game state: read directly from `ClientEventTransformer.HandlePipeMessage` Dictionary param via inlined `JsonConvert.SerializeObject` → file. Plaintext JSON, no encryption, no protocol RE.
- Click injection: call `_PROJECT_NEW.Scripts.TableEventHandlers.UserActionHandler.UserAction(ActionId, float?)` directly from inside the same managed assembly. No cursor movement, no Win32 input, no focus stealing, no JIT-time cross-assembly resolution.
- Hand parser: 22+ event types fully decoded (`game.hole_cards`, `game.dealer_cards`, `game.user_turn`, `game.advance_player_action`, etc.) — see `vision/coinpoker_adapter.py`.
- Strategy: existing `AdvisorStateMachine` slots in unchanged via the adapter.

The original three "hard paths" (screen automation, TCP RE, BepInEx) were **all wrong choices**. The simple managed-DLL IL patch was Path C′, identified by reading `PBClient.dll` symbols and discovering the canonical `HandlePipeMessage` dispatcher was static + took a Dictionary directly.

See `project_coinpoker_unity.md` in memory for the full session-by-session log of how this was done.

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
- [x] **CoinPoker game-state capture via IL-patched PBClient.dll** (2026-04-07 session 5) — read-only, plaintext JSON, hole cards + board + actions + pot streamed to `C:\Users\Simon\coinpoker_frames.jsonl`. Patch + deploy tooling at `C:\Users\Simon\coinpoker_patcher\`.
- [x] **CoinPoker → AdvisorStateMachine adapter + runner** (2026-04-07 session 6) — `vision/coinpoker_adapter.py` + `vision/coinpoker_runner.py`. End-to-end pipeline produces real recommendations from real frames. 41 new tests (24 adapter + 17 runner incl. real-Advisor regression). Replay: `python vision/coinpoker_runner.py --replay`. Live: `python vision/coinpoker_runner.py --follow`. Print-only: add `--print-only`.
- [x] **CoinPoker Tk overlay LIVE** (2026-04-08 session 7) — runner spawns the existing `vision/overlay_process.py` and pushes `table_update` messages on every state change. Warmup-from-EOF + spectator-mode overlay bugs fixed and regression-tested. 34/34 runner tests. User-confirmed live recommendations during real CoinPoker play.
- [x] **Bouncing-rec bug fixed** (2026-04-08 session 8) — `whose_turn_seat` cleared on any seat action; runner gates advisor on `hero_turn`; sticky-cache last rec for display. Fixes the "RAISE 350 → CALL 2360 → RAISE 250" oscillation seen during the AcQh hand. 6 new tests, all green.
- [x] **CoinPoker click adapter Phase 1 + 2 (dry-run)** (2026-04-08 sessions 9-10) — Click target: `_PROJECT_NEW.Scripts.TableEventHandlers.UserActionHandler.UserAction(ActionId, Nullable<float>)` static. Phase 2 IL inlined into HandlePipeMessage Prologue 2 (file check + log). Python writer at `vision/coinpoker_clicker.py` (atomic write, pause flag, queue dedup, hand-id staleness check). 20 clicker tests. **Gauntlet: 50/50 round-trips zero drops** in periodic mode. Two real bugs found and locked in (Cecil leave-rewire, Unity Mono cross-assembly resolution failure). See `tools/phase2_gauntlet.py`.
- [x] **CoinPoker click adapter Phase 3 IL BUILT** (2026-04-08 session 11) — `--enable-phase3` flag in patcher. `PBClient.phase3.dll` ready (130 instructions, 3 EHs, 5 locals). Sentinel-file format (one file per ActionId). NOT deployed — gated on operator-supervised single-hand live test.

### In Progress
- [ ] Fix CHECK when need to CALL (preflop: base advisor returns CHECK)
- [x] **Position detection stability — FIXED 2026-04-08 session 13** — `vision/coinpoker_adapter.py` now derives bb_seat from dealer + dealt-in lineup when game.game_alldata isn't in the live stream (which is always, post-join). Was causing pos="MP" on every hand. 39 adapter tests pass, replay verified rotating SB→BTN→CO→MP→UTG→BB across 17 hands. See `derive_blinds_from_dealer` + `_dealt_in_seat_ids` + `TestPositionDerivationFromLiveEvents`.

### CRITICAL — Strategy validation gates (before any more real-money play)
**Status as of 2026-04-08 evening:** User lost 2 buy-ins tonight (Unibet + CoinPoker NL10) following advisor recommendations with passing unit tests. Two named leaks identified, both in `tests/test_strategy_regressions.py` as `expectedFailure`. The lesson: **passing unit tests ≠ validated strategy.** See memory entry `feedback_passing_tests_not_validation.md`. No real-money play (any stakes, any site) until both items below are done AND replay validation gate passes.

- [ ] **(P0) Equity-vs-action-range** — equity model currently computes hero vs random hand. Catastrophic class is now caught by `_apply_danger_overrides`, but the underlying gap remains: equity is honest only against random hands, not against action-narrowed ranges. Spec for the proper fix:
  - **(a)** Add `hero_bet` and `villain_last_aggression: "RAISE"|"BET"|"PASSIVE"` to `CoinPokerStateBuilder.snapshot` output. `hero_bet` already computed internally, just expose it. `villain_last_aggression` is derived from `players[*].last_action` excluding hero.
  - **(b)** Add per-hand action-history accumulator to `AdvisorStateMachine`. Reset on hand_id change. On each snapshot, diff against last seen state to detect new actions and append.
  - **(c)** Implement `narrow_range_by_actions(action_history) -> set[hand_key]` (or `-> equity_multiplier` as a v0). v0 is fastest: each raise multiplies discount, river raise hits hardest, raise-after-passive-streets hits even harder.
  - **(d)** Compose with existing `adjusted_eq` bet-ratio discount, don't replace it.
  - **(e)** Regression test seeds (all currently leaks, all should FAIL until the fix lands):
    - Hand `2460830707` — KK on 4-straight river facing raise (CoinPoker, FIXED via danger filter as a stopgap)
    - Hand `2379414698` — KK on 3-flush board (9h 6h 2h) with hero holding Kh, facing ~9x pot bet → NEW advisor still recommends CALL 7.52. Should FOLD. Equity model overweights the K-of-hearts blocker without accounting for villain's action-narrowed range on a flush-completing board. Discovered 2026-04-08 via Unibet replay test. Danger filter only handles 4-flush; this is 3-flush + 1-blocker.
    - Hand `2379447781` — QcJc BTN on Kc-Th-Td-3c-8c facing 2.81 river bet (Q-high, busted gutshot AND busted flush draw) → NEW advisor recommends CALL. Should FOLD. Equity model treating Q overcards as live equity post-river even though hand is just Q-high. Discovered 2026-04-08 via Unibet replay test.
    - Set on a 4-flush facing turn raise — synthetic case for the test suite, no real seed hand yet.
  - **NOTE:** flat-multiplier v0 was scoped 2026-04-08 but rejected — a 15% extra discount is too gentle to flip decisions in any of the spots that matter. Either commit to the proper action-history accumulator OR add more danger filters for additional spot patterns. The two approaches are complementary, not redundant. Cheapest interim path: add danger filter cases for "3-flush board with overpair facing big bet" and "all draws busted, facing river bet, no pair" — both would catch tonight's two new Unibet leaks without needing the full action-history work.
- [ ] **(P0) Danger-spot override filters** — hard-coded fold rules for known catastrophic spots that bypass the equity model entirely. Seed pattern: "overpair facing a raise on a 4-straight or 4-flush board → FOLD." Add to `AdvisorStateMachine.process_state` as a post-recommendation override. Cheap, fast, would have prevented the KK loss tonight.
- [ ] **(P0) Replace hand-coded preflop chart with published 6-max NL10 chart** — current chart is too tight on premium hands OOP (AQo SB flat-called a 2.5x open at 4-handed in hand 2460830661, standard play is 3-bet). Source a published GTO/practical 6-max chart and encode it. Until done, the AQo regression test stays red.
- [x] **Strategy regression suite seeded** — `tests/test_strategy_regressions.py` (2 expectedFailure tests for the 2026-04-08 losses). NEVER delete tests from this file even if engine is rewritten.
- [ ] **(P1) Replay validation gate** — before any session, require: replay_whatif.py shows ≥+5 bb/100 over ≥1000 captured hands AND all named regression tests are green. Wire this as a `tools/check_ready_for_live.py` that exits non-zero if any gate fails.
- [ ] **(P1) Wire `OpponentTracker` into `coinpoker_runner.py`** — currently `tracker=None`. Code already exists in advisor_ws/coinpoker_player. This is the first step toward fish/reg classification feeding the recs.
- [ ] **(P2) CoinPoker built-in HUD stats sniffer** — `tools/coinpoker_stats_sniffer.py` exists but unverified (CDP-attached, watches /v2/stats/ — needs an opponent hover to trigger a request). Built 2026-04-08, untested at runtime. CoinPoker has built-in VPIP/PFR/3-bet display via REST endpoint, used for accuracy comparison vs our OpponentTracker.
- [ ] **(P3, longer term) 6-max CFR training** — would replace HU CFR for 6-max strategy, but: (a) NOT what's broken — KK loss was equity-estimation, not strategy selection; (b) CFR runs ON TOP of equity, GIGO; (c) GTO underperforms exploit at micros anyway; (d) months of compute. Defer until P0/P1 items are landed and we're capped.

### Data capture decision (2026-04-08)
**Practice tables are NOT viable for validation data.** Fake chips → players play randomly → opponent profiles get poisoned. Two viable paths:
- **Observer mode** at real-money tables (if CoinPoker supports it — needs check) — free, large volume, no hero hole cards (so no replay validation, only opponent profiling)
- **Bounded-cost NL2 capture**: $15-20 budget, hero plays manually with overlay-only, frames give us replay-quality data with hero cards. User confirmed budget is acceptable.

### BLOCKED — Auto-player on Unibet (canvas focus problem, 8 approaches failed)
**Status as of 2026-04-07 evening:** Demoted from active work. ~70% click reliability is the ceiling with current approaches. Lost real money to timeouts again today. Use overlay-only (`advisor_ws.py`) for live play. Returning to this only if a fundamentally new approach is identified — see "research" item below.
- [ ] *(deferred)* Chrome extension click adapter — only theoretically reliable approach not yet tried
- [ ] *(deferred)* Bet sizing fix — bot clicks RAISE without setting input first, ends up with default 2× BB instead of advised size. Documented in memory but never fixed because it requires more canvas clicks (each unreliable). Compounds the focus issue.
- [ ] *(deferred)* Click verification, hero_turn check, PLAY button auto-click — none worth touching until a reliable click primitive exists

### CoinPoker auto-player — Phase 3 PENDING DEPLOYMENT (operator-supervised)
**Status as of 2026-04-08 session 11.** All the original "1-4 week" estimates were wrong — the IL-patched-managed-DLL route delivered game-state capture, advisor wiring, overlay, dry-run click adapter, and Phase 3 IL build in ~3 evenings. The remaining gap is the operator-supervised first live click.

Phase 3 IL artifact: `C:\Users\Simon\coinpoker_patcher\PBClient.phase3.dll` (130 instructions, 3 EHs, 5 locals). Built by `patch_pbclient.py --enable-phase3`. Sentinel-file format (one per ActionId: FOLD/CHECK/CALL/ALLIN/RAISE). NOT deployed.

**Phase 3 deployment checklist (next session):**
- [x] Phase 2 round-trip reliable (50/50, 0 drops, p50 926ms)
- [x] Hand-id staleness check available
- [x] Pause flag default-paused on startup
- [x] Click target identified, signature confirmed
- [x] Phase 3 IL emitted, structurally verified
- [ ] **50+ hand dry-run with hero actually playing** — needs operator to sit in. Run `python tools/phase2_gauntlet.py --target-rounds 50 --mode hero-turn`
- [ ] All tests green at moment of deploy (re-run before)
- [ ] Operator-supervised single-hand live test on practice table — write one `coinpoker_live_FOLD.flag` manually, watch the table, verify the action fires
- [ ] Click verification: tail JSONL for matching `game.seat` event with expected caption within 2s of fire
- [ ] Humanizer wired to randomize click timing (vision/humanizer.py exists from Unibet work)
- [ ] **First 1 hand auto-folded under operator supervision on practice table** before any unattended runs

### Todo — Hand data analysis (372 hands collected 2026-04-07, +€16.45)
- [x] **Opponent tracker persistence** — wired in 2026-04-07 session, 4/4 tests pass
- [x] Build leak detection: scripts/analyze_leaks.py — identified river bleeding (€8.28/€10.78), BB defense too wide
- [x] Tightened: river thresholds, BB defend range, postflop _eval_strength
- [x] **Replay simulator with what-if** (DONE 2026-04-07) — scripts/replay_whatif.py + 3 tests
  - Tests 5 strategy variants on 527 captured hands
  - Initial findings: nit_assume +€0.98, looser_bb -€0.12, fish_assume -€0.12
  - Use to backtest strategy changes BEFORE shipping live
  - TODO: add real Monte Carlo for divergent spots (current EV est is heuristic)
  - TODO: change default opponent_type for unclassified villains from UNKNOWN→NIT at micro stakes (validate with bigger sample first)
- [ ] Train data-driven preflop ranges from showdown results
- [ ] Detect bots in opponent pool — cluster villains by behavior, avoid suspicious tables
- [x] **Wire opponent type → postflop engine adjustments** (DONE 2026-04-07, 4/4 tests pass, classify_villain picks last aggressor, OPPONENT_ADJUSTMENTS apply per type)

### PRIORITY (after Unity discovery) — Strategy + HUD work that doesn't need a clicker
The clicker is broken across all viable targets. Stop investing in click reliability. Invest in the things that make the user a better manual player and that grow the moat (strategy + opponent data).
- [ ] **Bet-sizing display fix on overlay** — currently overlay shows the engine's recommendation as text. Make sure it's prominent and unambiguous so manual play is fast. Already mostly there.
- [ ] **HUD-style stats per villain on the panel** — VPIP/PFR/Aggression numbers per profile, not just the type label. Surface alongside `vs TAG | FISHY table`.
- [ ] **Hand history review tool** — `tools/review_session.py` to walk through a session and show the rec vs the actual outcome. Helps validate strategy fixes.
- [ ] **Strategy regression test on real session JSONLs** — every strategy change should re-run against captured sessions to verify no EV regression.
- [ ] **Auto-flag suspicious tables** — surface bot/collusion warnings on the overlay (already in collusion section, elevate priority)
- [ ] **Decision-needed: target site choice** — once strategy + HUD are tight, decide whether to ever revisit auto-clicking, on which site, and with what new approach.

### Todo — Find a different auto-clickable poker target (research, low priority)
The two sites we know — Unibet (Emscripten canvas) and CoinPoker (Unity client) — both resist DOM automation. Before investing in either of the hard paths (TCP protocol RE, screen automation), spend a few hours surveying:
- [ ] **888poker web client** — does it use canvas or DOM? Quick test: load in Chrome, devtools, inspect.
- [ ] **partypoker web client** — same check
- [ ] **GGPoker web client** — same check (note: native client is C++, web may be different)
- [ ] **Pokerstars web client** — already in research notes as DOM-based, but anti-bot is the strongest in industry
- [ ] **Smaller crypto sites** — Nitrobetting, BetOnline, ACR — many smaller operations use plain HTML
- Goal: find ONE site that's pure DOM. If found, the existing CoinPoker replica adapter (which works against DOM) becomes the bones of a real auto-player. If not found after a few hours, accept that auto-clicking is dead and focus on advisor/HUD work indefinitely.

### Todo — Venue-wide player database & profiling (added 2026-04-07 evening)
- [x] **Wire opponent_type + table_summary to overlay + log + session JSONL** (DONE 2026-04-07) — `vs TAG | FISHY table` line on overlay, `villain=...` and `players=...` in `[REC]` log lines
- [ ] **`tools/dump_player_db.py`** — CLI to inspect existing HandDB player rows. First step before designing new schema. Output: name, site, hands seen, VPIP/PFR/aggression, classification, last seen
- [ ] **Site column on player rows** in HandDB / OpponentTracker — current schema doesn't differentiate Unibet vs CoinPoker vs future sites. Add `site` column so "tom96" on Unibet doesn't collide with "tom96" on CoinPoker
- [ ] **Manual notes/tags column** per player — free text like "shortstack 3bet bot", "calls down with TPNK". Surface on overlay opponent line when villain identified
- [ ] **`tools/show_player.py "name" [--site unibet]`** — print full known profile + recent hand history snippets for any tracked regular
- [ ] **Cross-table aggregation overlay view** — when multi-tabling, single dashboard panel showing all current opponents + profiles
- [ ] **Multi-site player matching (research)** — same person playing different sites with different aliases. Hard problem; skip until DB has volume. Approach: stack-size + timing-pattern fingerprinting

### Todo — Defensive collusion + bot detection (added 2026-04-07)
- [x] **CollusionDetector** — 5 signals, 8/8 tests pass, persistent via HandDB
- [x] **BotDetector** — 5 signals, 5/5 tests pass, scores bot behavior 0-1
- [x] **WSActionInferrer** — converts WS state diffs to action events, 6/6 tests pass
- [x] Wire into auto_player + coinpoker_player: hand_started, record_action, flush at cleanup
- [ ] Defensive adjustments in state machine: tighten ranges 30% when suspect pair at table
- [ ] Auto-table-leave when collusion score > 0.75 OR bot density > 50%
- [ ] Persistent table blacklist across sessions
- [ ] Add whipsaw pattern detection (A-target-B sandwich raises)
- [ ] Validate detection thresholds against real CoinPoker data once we have hands
- [ ] Add reaction-time-based bot detection for Unibet WS (mark prompts via WS bet changes)
- [ ] Surface bot/collusion warnings on the overlay

### Todo — Equity model
- [x] **Option A: Unify equity on existing NN** (DONE 2026-04-07 — NN MAE 0.056 vs heuristic 0.209)
  - Wire postflop_engine `_eval_strength()` to call EquityNet from train_equity.py
  - Validate NN equity matches reality on the 372 captured hands
  - Retrain only if MAE > threshold
- [ ] **Option B: Train expected-value model from real hand outcomes** (research, deferred)
  - Label = profit_cents / pot_cents per hand
  - Predict EV directly, not equity
  - Inputs: hero + board + position + facing + pot_odds + opponent_type
  - Needs 5k+ hands minimum — collect more data first
  - Will let bot learn "what wins money for me" vs "what wins all-ins theoretically"
- [ ] Option C: Pre-computed lookup table — only if NN proves too slow at runtime

### Todo — Required for Profitable Bot
- [ ] Pot odds calculation — compare call price vs pot to determine +EV calls
- [ ] Opponent action weighting — discount equity 20-30% when opponent bets big (they don't bet big with random hands)
- [ ] CFR strategy integration — use trained 6-max CFR (3M info sets) for action decisions instead of static thresholds
- [ ] Dynamic preflop ranges — adjust open/call ranges based on table dynamics (tight table = wider, loose table = tighter)

### Todo — Evaluation & Testing
- [x] Replay simulator — built, tested: bot saves +$35.43 over 371 PS hands (+95.5 bb/100 improvement)
- [x] Fix stratified bot eval performance — improved to +23.3 bb/100 with better turn barrel, aggression tuning
- [x] Fix WS live test — partial, protocol issues remain in test-stratified-live.js
- [x] Tier 1: Fix existing bots — added 3-betting to TAG/LAG, c-bet frequency, sizing-aware FISH, NIT folds to 3-bets
- [x] Tier 2: FloatBot, SqueezeBot, CheckRaiseBot, ProbingBot, TrapBot — all built and tested
- [x] Turn barrel fix — delayed c-bet after checked flop, 50% barrel rate
- [ ] **CRITICAL: Refactor `on_state` into testable AdvisorStateMachine class** — all live bugs were in this function
- [ ] **CRITICAL: Test with full WS message replay** — record complete session, verify every recommendation
- [ ] Remove legacy HU CFR loading (saves 1GB RAM)
- [ ] Wire opponent tracker to SQLite DB for cross-session persistence
- [ ] Tier 3: AdaptiveBot — tracks STRAT tendencies, adjusts frequencies to exploit
- [ ] Tier 4: Wire real opponent profiles into distinct behavioral logic
- [ ] Exploitative counter-bots:
  - [x] Tier 1: Fix existing bots — added 3-betting to TAG/LAG, c-bet frequency, sizing-aware FISH, NIT folds to 3-bets
  - [x] Tier 2: FloatBot, SqueezeBot, CheckRaiseBot, ProbingBot, TrapBot — all built and tested
  - [x] Turn barrel fix — delayed c-bet after checked flop, 50% barrel rate. STRAT now crushes FLOAT (+10.7 bb/100)
  - [x] Turn/river aggression tuned — value bet threshold lowered 0.70→0.60, medium hand thin value 40%
  - [ ] Tier 3: AdaptiveBot — tracks STRAT tendencies, adjusts frequencies to exploit (the real test)
  - [ ] Tier 4: Wire real opponent profiles (67 loaded) into distinct behavioral logic using actual VPIP/PFR/AF
- [ ] Self-play tournament — stratified bot vs policy NN (fast_selfplay.py) long match
- [ ] Live session A/B — run advisor on Unibet, track bb/hr with vs without new postflop engine

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
- [x] Train BIG CFR — 100-bucket, 10M iterations on Proxmox (completed 2026-04-06, 1.33M info sets, 139MB)
- [ ] Train 6-MAX CFR — train CFR natively for 6-max (not HU). Much larger game tree but eliminates HU→6max range mismatch. Current HU CFR plays way too wide preflop for multiway.
- [x] Flop-only 2-player CFR (50-bucket) — trained 50M iter locally in ~15min. 15K info sets, 700KB binary. Covers SRP/3BP/LP pot types, IP/OOP, 3 stack depths.
- [x] Mmap binary strategy format — CFR1 binary with FNV-1a hash index, <1us lookup, <1MB resident
- [x] PostflopEngine — stratified: flop→CFR, turn/river→equity+opponent rules
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
