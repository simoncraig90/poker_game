# Phase 12 Frozen Baseline — 2026-04-11

## Headline Numbers
- Guided:       1097/1136 = 96.6%
- EXACT:        502
- EMERGENCY:    39 (all 4bp, deliberately excluded)
- Postflop EXACT: 502/541 = 92.8%
- Mean trust:   0.876
- Trust >= 0.7: 1099/1136 = 96.7%
- Artifacts:    406
- Errors:       0

## Checksums
- advisor-cli.exe SHA256: 9b07805708ae9f98918cb1347efe5396d5a8e10a540b9aaeddbd589e11e197c8
- advisor-cli.exe built:  2026-04-11 15:57
- replay_full_v3.jsonl SHA256: 352fcc0b40b732071b5eb0e3e9c4593f6a061d1450eba3711ef554370d45c8ae
- results_phase12.jsonl SHA256: 85935206fe80c90280d76bcea22fa7b7449a5f262fda3cd26d8b92cc8581b0d2

## Files
- Replay input:  python/eval_lab/replay_full_v3.jsonl (1136 decisions)
- Results:       python/eval_lab/baselines/phase12_3bp_exact.jsonl
- Binary:        rust/target/release/advisor-cli.exe

## Coverage by Family
- SRP:    100% (flop/turn/river all covered)
- Limped: 100% (flop/turn/river all covered)
- 3bp:    100% (flop/turn/river all covered)
- 4bp:    0%   (39 decisions, deliberately excluded — wrong strategy/menu for low-SPR)

## Changes Since V10
- Phase 11a: +15 SRP turn artifacts (35 decisions recovered)
- Phase 11b: +17 SRP river artifacts + 8 stragglers (71 decisions recovered)
- Phase 12:  bbunk fix (-7 garbage rows), first-aggressor fix in classify_pot,
             3bp classification gate widened, +26 3bp artifacts (26 decisions recovered)
- Total:     +66 artifacts, +132 EXACT decisions, -139 EMERGENCY, +0.064 mean trust

## Reproduction
```bash
cargo build --release
python session_to_replay.py --input ../vision/data/session_*.jsonl --output replay_full_v3.jsonl
python baseline_replay_runner.py --replay replay_full_v3.jsonl --output results.jsonl
```
Verify results.jsonl SHA256 matches above.
