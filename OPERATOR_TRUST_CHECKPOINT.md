# Operator Trust Checkpoint

Status: **Phase 7 complete. Ready for manual verification.**

---

## What This Checkpoint Proves

After seven phases of implementation, the system claims:

1. **Accounting integrity** -- no chips created or destroyed across any hand
2. **Persistence** -- table state survives server restarts
3. **Mid-hand crash safety** -- incomplete hands are voided, stacks restored
4. **Session lifecycle** -- archive, new session, and browsing all work
5. **Operator visibility** -- recovery status, voided hands, and session history are visible in the browser

Automated tests cover all of these (168/168 checks pass). This checkpoint asks a human operator to verify the claims independently using the browser client.

---

## Automated Evidence (Pre-Demo)

| Suite | Checks | What It Proves |
|-------|--------|----------------|
| Phase 1: accounting | PASS | No chip leaks across hand types |
| Phase 2: conformance | 25/25 | No hidden state, deterministic |
| Phase 3: WS conformance | 31/31 | Protocol contract honored |
| Phase 5: E2E session | 38/38 | Multi-hand session flow |
| Phase 6: recovery | 52/52 | Persistence + recovery correctness |
| Phase 7: session browser | 22/22 | Recovery UX + archive browsing |

Run all suites:
```
npm test
```

All must be green before proceeding to manual demos.

---

## Manual Demo Set

See **MANUAL_DEMO_RUNBOOK.md** for the full operator runbook.

| Demo | Scenario | Time |
|------|----------|------|
| 1 | Normal play + restart recovery | ~5 min |
| 2 | Mid-hand crash + void recovery | ~4 min |
| 3 | Archive + new session | ~3 min |
| 4 | Post-archive restart | ~2 min |

Total: ~15 minutes.

---

## Pass / Fail Summary

The checkpoint passes when:

- [ ] All automated suites green (168/168)
- [ ] Demo 1: PASS -- stacks and history survive restart
- [ ] Demo 2: PASS -- mid-hand void, no chip leak, play resumes
- [ ] Demo 3: PASS -- old session archived and browsable, new session clean
- [ ] Demo 4: PASS -- only active session recovered after restart

Any single demo failure blocks the checkpoint. Fix, re-run automated suites, re-run the failed demo.

---

## What "Ready to Move On" Means

All four boxes above are checked. The operator has recorded evidence (screenshots or written notes per the runbook). No chip accounting anomalies were observed. The system behaves as a human would expect a persistent poker table to behave.

At that point, proceed to **POST_DEMO_DECISION_FRAME.md** to choose the next development branch.
