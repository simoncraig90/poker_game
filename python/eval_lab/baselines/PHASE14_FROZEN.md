# Phase 14 Frozen Baseline — 2026-04-11

## Headline Numbers
- Guided:       1136/1136 = 100.0%
- EXACT:        541
- EMERGENCY:    0
- Preflop chart: 595
- Mean trust:   0.8944
- Trust >= 0.7: 1136/1136 = 100.0%
- Artifacts:    445 (406 Phase 12 + 39 4bp)
- Errors:       0

## Phase 12 -> Phase 14 Delta
- +39 4bp EXACT decisions (was 39 EMERGENCY)
- +0.0185 mean trust (0.8758 -> 0.8944)
- 0 action flips in SRP/Limped/3bp families
- 0 snap distribution changes in existing families

## 4bp Family Stats
- 39 decisions, 100% EXACT
- Trust: 0.950 (uniform, all 4bp artifacts have high confidence)
- Snap distribution: 21 no_snap, 11 kind_not_legal, 7 near_jam
- Action distribution: 23 check (59%), 9 call (23%), 7 jam (18%)
- Pre-legalize: 28 check (72%), 11 jam (28%)
- Hand buckets: 16 air, 7 weak_pair, 5 overpair, 3 top_pair_weak, 2 strong_two_pair, 2 weak_draw, 2 monster, 1 tpgk, 1 strong
- IP/OOP split: 36 OOP, 3 IP (matches design analysis)
- 0 illegal outputs

## Checksums
- advisor-cli.exe SHA256: a7f6b57b34444d007dc0812af2b21ff301db4ce4ec21dfe7692ef5f7841e9b2c
- advisor-cli.exe built:  2026-04-11 17:10
- replay_full_v3.jsonl SHA256: 352fcc0b40b732071b5eb0e3e9c4593f6a061d1450eba3711ef554370d45c8ae
- results_phase14.jsonl SHA256: 2abd5b0744c834368f84e8cdb549f222750f187160190fad6b73e377767ac606

## Files
- Replay input:  python/eval_lab/replay_full_v3.jsonl (1136 decisions)
- Results:       python/eval_lab/results_phase14.jsonl
- Manifest:      python/eval_lab/phase14_4bp_manifest.jsonl (39 entries)
- Binary:        rust/target/release/advisor-cli.exe
- Design doc:    docs/4BP_FAMILY_DESIGN.md

## Coverage by Family
- SRP:    100% (217 decisions, all EXACT)
- Limped: 100% (259 decisions, all EXACT)
- 3bp:    100% (26 decisions, all EXACT)
- 4bp:    100% (39 decisions, all EXACT)

## Changes Since Phase 12
- classify.rs: FourBp added to Exact quality gate (2-way, norake only)
- legalizer.rs: Jam fallback now tries BetTo/RaiseTo entries before fallback chain
  (prevents Jam -> Check degradation when no explicit Jam in legal actions)
- build_exact_artifact.py: FOURBP_ACTIONS, FOURBP_OOP_MATRIX, FOURBP_IP_MATRIX added
- mode.rs: Updated test for non-exact quality (was 4bp, now 5-way); added fourbp_2way_norake_uses_exact test
- 128 Rust tests passing (was 127 in Phase 12 + 1 new)
- 6 new legalizer tests for 4bp snap paths
- 3 new classify tests for 4bp quality gate

## Reproduction
```bash
cd /c/poker-research/rust && cargo build --release
cd /c/poker-research && python python/scripts/build_exact_artifact.py --from-manifest python/eval_lab/phase14_4bp_manifest.jsonl --output-dir artifacts/solver
cd /c/poker-research && python python/eval_lab/baseline_replay_runner.py --replay python/eval_lab/replay_full_v3.jsonl --output results.jsonl
```
Verify results.jsonl SHA256 matches above.
