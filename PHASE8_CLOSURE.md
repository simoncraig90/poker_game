# Phase 8 Closure: Showdown + Side-Pot

**Status: CLOSED** — Core correctness and primary operator flow complete.

---

## Done / Accepted

| Slice | What | Evidence |
|-------|------|----------|
| 1: Hand evaluator | Pure function, all 10 hand types, ace-low, kicker tie-breaking | 113 checks (evaluate.test.js) |
| 2: Side-pot calculator | Tier-based algorithm, folded dead money, odd-chip rule | 134 checks (pots.test.js) |
| 3A: Settlement assembly | computeShowdown pure function, per-pot winners, uncontested separation | 106 checks (showdown.test.js) |
| 3B: Orchestrator integration | GAP-1 removed, settleShowdown wired, SHOWDOWN_REVEAL event, reconstruct | 47 checks (showdown-integration.test.js) |
| 5: Recovery | Mid-showdown crash at any event point voids correctly, no corruption | 40 checks (showdown-recovery.test.js) |
| 6A: Client rendering | SHOWDOWN_REVEAL display, multi-pot banners, enriched timeline, event log | 43 checks (showdown-client.test.js) |
| 6B: Reveal persistence | Cards + board hold through render/GET_STATE until next HAND_START | 41 checks (showdown-persist.test.js) |

**Reconstruct fix**: void HAND_END now restores stacks and decrements handsPlayed (was a latent bug).

**Event contract**: POT_AWARD (authoritative chip transfer) and HAND_RESULT (display narrative) confirmed as both necessary with distinct roles. Documented in PHASE8_SLICE5_NOTES.md.

**Total Phase 8 checks**: 524 new (across 7 suites). Grand total: 692 across 15 suites.

---

## Known Minor Debt

These are accepted gaps. None block correctness, operator trust, or the next branch.

| # | Item | Impact | Effort |
|---|------|--------|--------|
| SD-1 | Hand list doesn't show winning hand rank | Cosmetic — click into detail shows it | Low: add handRank to GET_HAND_LIST response |
| SD-2 | Archived hand detail is plain text | Cosmetic — data is correct, layout is unformatted | Low: HTML formatting in formatTimeline |
| SD-3 | Late-joining client mid-showdown misses live reveals | Edge case — data in event log for replay | Medium: include last reveal in welcome message |

---

## Non-Blocking Future Polish

- Card suit icons/unicode instead of letter codes
- Animated card reveal sequence
- Pot award animation
- Side pot visualization on the table felt
- Showdown order (first-to-show rules) — currently all revealed simultaneously
- Muck/show choice for losing hands

None of these affect engine correctness or data integrity.

---

## Branch is Closed

Do not reopen for core correctness. Backlog items SD-1 through SD-3 may be addressed opportunistically or as part of a future polish pass. Next branch: identity/study per POST_DEMO_DECISION_FRAME.md.
