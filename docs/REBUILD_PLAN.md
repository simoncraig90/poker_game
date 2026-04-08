# Rebuild Plan — Foundation rebuild for full-autopilot 6-max cash

**Decision date:** 2026-04-08
**Branch:** `rebuild-foundation-20260408` (forked from `main`)
**Approved by:** Simon
**Trigger:** Live grind session 2026-04-08 caught nine new leak shapes in ~2 hours of NL10 6-max play. Each fix was a sandbag against the same root cause: the equity model returns hand-vs-RANDOM equity, not equity vs the range villain actually holds when they take an action. The structural gap is large enough that no patch frequency will close it. Decision is to rebuild the strategy core on top of a real validation gate.

**Objective:** A 6-max cash advisor that the operator does not analyse. It reads tables, decides actions, executes clicks, manages bankroll, climbs stakes from NL2 → NL5 → NL10 → NL25 → NL50 with a per-stake validation gate at each move-up. Full autopilot, hands-off.

**Live play during build:** NOT paused. The current `coinpoker-strategy-fixes-20260408` branch with today's nine stop-loss filters remains the deployed runner. The rebuild branch is non-load-bearing for real money until Phase 7 burn-in completes. The bankroll watchdog (`tools/bankroll_watch.py`) stays running on any live session.

---

## Capability inventory

Status legend:
- **HAVE** — works, keep, do not rebuild
- **PARTIAL** — works for limited cases, needs extension
- **GAP** — does not exist, must build

### Game state ingestion

| Capability | Status | Notes |
|---|---|---|
| Per-snapshot game state (cards, board, pot, stacks, position) | HAVE | IL-injected `PBClient.dll` writes every `cmd_bean` to `coinpoker_frames.jsonl`. Server-invisible, reliable, multi-table. |
| Hand-id race protection | HAVE | Stale-hand check in clicker. |
| Per-table state isolation | HAVE | `MultiTableCoinPokerSession`, per-room SMs. |
| Action timer / button-set detection | GAP | Required for autopilot timer management. |
| Sit-out detection | GAP | No detection of "table set me to sit out." |

### Per-hand action history

| Capability | Status | Notes |
|---|---|---|
| Per-street villain action log (who bet, who raised, in what order) | GAP | `action_history` and `flop_action_history` are placeholder/empty. Required for range narrowing. |
| Last aggressor identification | PARTIAL | `_last_aggressor_user_id` finds highest current bettor only. |

### Hand evaluation

| Capability | Status | Notes |
|---|---|---|
| Hero hand class (high-card → straight-flush) | HAVE | `_evaluate_hand_class`. Accurate. |
| Board texture (paired, monotone, n-flush, n-straight, dynamic) | HAVE | `assess_board_danger`. Accurate. |
| Blocker awareness | GAP | We don't reason about which villain hands our cards block. |
| Nut-relative hand strength | GAP | We know hand class, not how that compares to the best possible hand on this board. |

### Range modeling — the foundational gap

| Capability | Status | Notes |
|---|---|---|
| Starting range per villain by position | GAP | None. The chart only knows hero ranges. |
| Range narrowing by action sequence | GAP | None. |
| Range conditioned on villain classification (NIT/TAG/LAG/FISH/UNKNOWN) | GAP | None. The HUD knows the player type, but the engine doesn't translate that into a hand range. |
| Multi-way intersection of ranges | GAP | None. |

### Equity computation

| Capability | Status | Notes |
|---|---|---|
| Equity vs random hand (hot-cold) | HAVE | EquityNet model. Fast, accurate at what it computes. |
| Equity vs villain range | GAP | The whole structural fix. Requires Range modeling first. |
| Multi-way equity | GAP | Engine is 2-player only. |

### Decision engine

| Capability | Status | Notes |
|---|---|---|
| Preflop chart (6-max, position-aware) | HAVE | After today's RFI + BB fixes (on `coinpoker-strategy-fixes-20260408`). |
| 2D preflop chart (hero × opener position) | GAP | Currently single-dimension; defends BB at "average" opener. |
| Squeeze ranges | GAP | No squeeze detection or response. |
| Postflop CFR engine | HAVE | 13K info sets, 50 buckets. **But calibrated for hand-vs-random equity** — needs retune after equity rebuild. |
| Turn / river rules | PARTIAL | Equity-threshold rules, leaky vs aggression. |
| EV-maximizing decision | GAP | Engine picks via thresholds, not EV math. |
| Bet sizing (positional, board-texture aware, blocker aware) | PARTIAL | Naive 3x raise sizing, 0.66-pot c-bets, no blocker logic. |

### Opponent modeling

| Capability | Status | Notes |
|---|---|---|
| Server-side HUD (VPIP/PFR/AF/3bet) for known players | HAVE | 40 ground-truth profiles via `coinpoker_stats_sniffer.py`. |
| OpponentTracker (per-player stats accumulated from frames) | HAVE | HandDB-persisted. |
| Classification (NIT/TAG/LAG/FISH/MANIAC/UNKNOWN) | HAVE | HUD-first, tracker-fallback. |
| Population reads at this stake | GAP | Hard-coded NIT default for unknowns; no population study. |
| Dynamic adjustments (tilted, scared money) | GAP | No state per villain beyond aggregate stats. |

### Click execution

| Capability | Status | Notes |
|---|---|---|
| Phase 2 dry-run clicker | HAVE | 50/50 verified, 0 drops. |
| Phase 3 IL DLL (real `UserAction` call) | PARTIAL | Built (`PBClient.phase3.dll`), **not deployed**. Never live-fired. |
| Auto-folder | PARTIAL | Wire-up not written; depends on Phase 3 deploy. |
| Auto-clicker for all actions | GAP | Same dependency. |
| Humanizer integration | PARTIAL | `humanizer.py` exists, not wired to Phase 3 path. |

### Multi-table coordination

| Capability | Status | Notes |
|---|---|---|
| Per-table state isolation | HAVE | |
| Cross-table action prioritizer | GAP | |
| Cross-table timing decorrelation (no 4 actions in same 100ms) | GAP | Bot-detection signal. |
| Sit-out prevention (auto-act before timer expiry) | GAP | Required for unattended play. |
| Table-quality scoring (auto-leave bad tables) | GAP | |
| Auto-buy-in to good tables | PARTIAL | `coinpoker_open_practice.py` handles practice tables only. |

### Bankroll & stake progression

| Capability | Status | Notes |
|---|---|---|
| Bankroll watchdog (stop-loss in buy-ins) | HAVE | `bankroll_watch.py` auto-pauses. |
| Per-stake hand counter and BB/hr tracker | PARTIAL | BB/hr in overlay; no per-stake aggregation. |
| Stake-up rule | GAP | |
| Stake-down rule | GAP | |
| Auto-table-selection at the new stake | GAP | |

### Validation harness — THE missing gate

| Capability | Status | Notes |
|---|---|---|
| Code-level unit tests (state machine determinism) | HAVE | 213 tests. Necessary, not sufficient. |
| Replay-vs-real-outcomes harness | GAP | The gate Simon's memory has been asking for. **No new code ships live without this.** |
| BB/hr replay benchmark across captured corpus | GAP | |
| Per-shape leak detection | GAP | |
| Pre-stake-up validation gate | GAP | |

### Bot-detection avoidance

| Capability | Status | Notes |
|---|---|---|
| Read-only frame capture invisible to server | HAVE | IL-injected, no network signature. |
| Humanized think time | PARTIAL | `humanizer.py` exists, not wired. |
| Mistake injection | PARTIAL | `PlayVariation` exists, currently disabled. |
| Cross-table action decorrelation | GAP | |
| Session length variation | PARTIAL | `SessionManager` exists. |

### Telemetry & live monitoring

| Capability | Status | Notes |
|---|---|---|
| BB/hr in overlay | HAVE | |
| Per-stake winrate persistence | GAP | |
| Per-spot leak telemetry | GAP | |
| Hand-by-hand archive | HAVE | The frame log is the archive. |
| Auto-pause on detected leak streak | GAP | |

### Summary

Roughly 40% HAVE, 25% PARTIAL, 35% GAP. Infrastructure is solid (frame capture, multi-table, HUD, hand evaluation, click adapter). The strategy core (range modeling, EV-based decisions, validation) is the missing 35%.

This is a foundation rebuild, not a rewrite from scratch. Replace the equity model and validation gate, retune what depends on them, then build out the missing autopilot pieces.

---

## Phased rebuild

### Phase 0 — Stop the bleeding (immediate)

- Archive today's session for the regression corpus.
- Today's nine filters stay on `coinpoker-strategy-fixes-20260408` as a separate branch — that's the deployed runner if Simon plays live during the build.
- All rebuild work happens on `rebuild-foundation-20260408` and never touches the deployed runner until Phase 7 graduation.

### Phase 1 — Validation harness + action history (week 1)

The gate. Nothing else ships until it exists.

**Deliverables:**
1. **Replay-vs-outcome harness.** Reads each captured hand from the frame log, runs the advisor on each decision point, records what the advisor would have done, then compares to the actual outcome (what the player did and what happened next). For each hand: did the advisor's path beat the actual path in EV terms?
2. **BB/100 replay benchmark.** Run the harness across the entire captured corpus (~75K frames). Output: "advisor would have earned X BB/100 vs the corpus baseline." This becomes the deployment gate. If a code change moves this number down, it doesn't ship.
3. **Action history accumulator (real version).** Per-hand, per-street log of every villain action with sizing, in order. Replaces the empty placeholder. Required by everything in Phase 2.
4. **Per-spot leak telemetry.** Tag each hand with the shape it belongs to (river call facing big bet, turn barrel, etc). Aggregate losses by shape. This lets us see which leaks are biggest before we patch them.

**Exit criteria:** harness reproduces actual session results within ±2 BB/100. We can run `python -m harness --corpus all` and get a number we trust.

### Phase 2 — Range model + range-aware equity (weeks 2-3)

The structural fix.

**Deliverables:**
1. **Starting range tables.** For each (villain_classification × position × first action), define the range. Sourced from population studies + the captured corpus.
2. **Range narrowing engine.** Given a starting range and a sequence of board cards + actions, return the narrowed range.
3. **Range vs hero equity calculator.** Given a hero hand, board, and villain range, return hero's exact equity. Replaces the EquityNet hot-cold call inside the SM.
4. **Multi-way equity (3-player and 4-player).** Equity computed against the intersection of multiple ranges. Engine becomes multi-way capable.

**Exit criteria:** harness BB/100 increases by at least +5 vs the Phase 1 baseline, run across the full corpus.

### Phase 3 — Decision engine retune (week 4)

**Deliverables:**
1. **Postflop engine recalibration.** CFR engine's call/raise/fold thresholds were tuned for hand-vs-random equity. Retune to consume the new range-aware equity.
2. **EV-based decision output.** Engine returns EV(fold), EV(call), EV(raise), and picks the max. Currently it picks via threshold rules.
3. **Bet sizing module.** Postflop sizing aware of: board texture, position, range advantage, stack depth.
4. **2D preflop chart.** Hero position × opener position. Eliminates the "single average opener" leak in BB defense.
5. **Squeeze logic.** Detect open-then-call situations and respond.
6. **Retire most danger filters.** With range equity the leaks they patch should not appear. Filters stay as belt-and-braces but stop being load-bearing.

**Exit criteria:** harness BB/100 ≥ +10 above Phase 1 baseline.

### Phase 4 — Phase 3 IL deployment + auto-fold (week 5)

**Deliverables:**
1. **Phase 3 DLL deploy** under operator supervision. Single supervised first-fold on a play-money table.
2. **Auto-fold path.** When advisor says FOLD AND `hero_turn` AND humanized delay → write sentinel file.
3. **Humanizer wired into the live path.** Per-table independent timing. Cross-table decorrelation enforced.
4. **Leak telemetry on auto-fold actions.** Confirm we never auto-fold a hand that should have continued.

**Exit criteria:** 200 supervised auto-folds at NL2, zero misfires.

### Phase 5 — Full auto-click (week 6)

**Deliverables:**
1. Auto-call / auto-check / auto-raise / auto-bet with sizing inputs.
2. Action timer detection. Don't act in the first 800ms (looks robotic), don't let it run within 2s of expiry.
3. Cross-table action prioritizer.
4. Sit-out prevention. Force-act when timer is near expiry.

**Exit criteria:** 1,000 unattended auto-clicks at NL2 across multiple tables, zero timeouts, zero misfires.

### Phase 6 — Bankroll & stake progression (week 7)

**Deliverables:**
1. Per-stake hand counter and BB/100 persistence.
2. Stake-up rule. Default: 5,000+ hands at the current stake at >+5 BB/100, with bankroll ≥ 30 buy-ins of the next stake → move up.
3. Stake-down rule. After dropping 10 buy-ins at the current stake → move down.
4. Auto-table-selection. Open tables matching the current stake, prefer tables with at least one HUD-flagged FISH.
5. Bankroll dashboard. One screen showing: bankroll, current stake, hands at this stake, current BB/100, distance to next stake-up / stake-down.

**Exit criteria:** the system can sit at NL2, play 5,000+ hands, and decide on its own whether to move to NL5.

### Phase 7 — NL2 burn-in (week 8)

**Deliverables:**
1. **5,000 hands at NL2 with full autopilot.** Operator monitors but does not intervene unless something is broken.
2. **Per-shape leak audit.** Any shape that loses more than -2 BB/100 over the burn-in becomes a Phase 8 fix.
3. **Move-up decision.** If burn-in shows ≥ +5 BB/100, go to NL5. If not, identify the leak and patch before promoting.

**Exit criteria:** sustained +5 BB/100 over 5,000 NL2 hands.

### Beyond Phase 7

Each subsequent stake (NL5 → NL10 → NL25 → NL50) gates on its own burn-in:
- 5,000+ hands at the current stake
- Sustained +5 BB/100 (or stake-appropriate threshold)
- Validation harness re-run against captured hands at that stake
- Bankroll ≥ 30 buy-ins of the next stake
- Then auto-promote.

---

## Total scope

**~7-8 weeks** of focused work to get from current state to "click the stake-up button at NL2 → autopilot grinds to NL5 → repeats." That is the honest estimate. The reason today's session felt like learning basic things is because the foundation skipped Phase 1 entirely — the strategy code was built without the validation gate, so we never knew what we had until real money was in front of it. Phase 1 must come first this time.

---

## Why today's nine filters do not solve the problem

Each filter patches one specific shape of leak. Catalogued for archival:

1. **Filter 5** (existing) — one-pair-or-less on coordinated river facing >75% pot
2. **Filter 6** (added 2026-04-08) — counterfeited two-pair on paired board, river big bet
3. **Filter 7** (added 2026-04-08) — one-pair facing raise after our postflop bet
4. **Filter 8** (added 2026-04-08) — pocket underpair to all board cards on river
5. **Preflop shove gate** (added 2026-04-08) — `call_amt > 15 BB` requires premium
6. **BB exclusion from RFI re-route** (added 2026-04-08) — BB facing min-raise no longer routes through `facing_raise=False`
7. **State-change call_amount + pot tracking** (added earlier 2026-04-08) — SM re-fires on sizing changes
8. **RFI re-route for SB/BTN folded-to** (added earlier 2026-04-08)
9. **KK-river-overpair-on-4-straight** (existing Filter 1, AQo SB 3-bet hardening)

All nine compensate for the same architectural fact: the equity model returns hand-vs-random equity. After Phase 2 (range-aware equity) most of these become unnecessary because the underlying equity numbers are correct. They stay as belt-and-braces — never load-bearing.

---

## Living document

This file is the source of truth for the rebuild. Edit it as scope shifts. Each phase exit must be marked with a date and the harness BB/100 number that gated it. New leak shapes discovered during burn-in get appended below as numbered entries.

### Phase exit log

| Phase | Exit date | Harness BB/100 | Notes |
|---|---|---|---|
| Phase 0 | 2026-04-08 | n/a | Plan approved, branch created. |
| Phase 1 — partial | 2026-04-08 | +8.7 BB/100 (NL10 corpus, all-time, no advisor scoring) | Action history accumulator + replay harness skeleton landed. 22 tests passing. Open calibration items below before formal Phase 1 exit. |
| Phase 1 — wired | 2026-04-08 | last-50: -37.4 BB/100 / corpus: +8.7 BB/100 | Live SM wired into harness end-to-end. All three of today's named loss spots (KK on 4-straight, AhJs on 4c Jh 5d, 6s6c on QA8KT) reproduce deterministically and the matching danger-override filters fire in replay. JSON-per-decision output added (`--output`, `--filters-only`). Boundary stack tracking + last-N-hands slicing in. |
| Phase 1 — leak ranking | 2026-04-08 | corpus: 90% agreement, top leak class = PAIR turn/river | Per-shape leak telemetry landed. Each decision tagged with `STREET:HANDCLASS:facing|noface:adv/hero` shape. Aggregate ranking shows the four worst leak shapes are all PAIR class on flop/turn/river — confirms the equity-vs-range structural gap is the right Phase 2 target. 15 unit tests passing. |
| Phase 1 — exit | — | — | Awaiting rebuy detection refinement + reproducibility check vs single-session ground truth. |
| Phase 2 | — | — | |
| Phase 3 | — | — | |
| Phase 4 | — | — | |
| Phase 5 | — | — | |
| Phase 6 | — | — | |
| Phase 7 | — | — | |

### Phase 1 calibration TODOs (before formal Phase 1 exit)

1. **Stack-delta accuracy at hand boundaries.** Current MVP uses
   first-snapshot `hero_stack` as starting and last-snapshot as
   ending. This is wrong when (a) hand-end payouts arrive a few
   snapshots late, (b) the next hand starts before the previous
   one's payout settles, (c) re-buys / top-ups happen mid-session.
   Fix: track hero_stack at hand boundaries (start of next hand =
   end of previous hand) instead of within-hand snapshots.
2. **Date-range filter.** The corpus is multi-day. The Phase 1
   exit criterion (reproduce session result within ±2 BB/100)
   needs to scope to a single session. Add `--from / --to` flags
   reading frame timestamps.
3. **Per-shape leak telemetry.** Tag each decision with its
   shape (river call facing big bet, turn barrel, etc) so
   aggregate losses can be attributed to leak classes.
4. **Reproducibility check against today's session.** Run
   harness against today's date range; the result must match
   the in-session running EUR within ±2 BB/100. This is the
   formal Phase 1 exit criterion.
5. **CLI tightening.** Currently the CLI has hardcoded paths to
   `C:\Users\Simon\coinpoker_frames.jsonl`. Move to a config file
   or env var so the harness is portable.

### New leak shapes discovered during build

(Append numbered entries here as found.)
