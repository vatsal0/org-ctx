# `tests/` — the golden-scenario assertions

Encodes plan.md §7's three golden scenarios as pytest assertions plus id/extraction
unit tests. Deterministic (LLM mock forced on via the shared harness).

## Files

- `conftest.py` — session-scoped fixtures `broken_graph` and `seeded_graph`, built
  once via `eval.harness.build_graph` (isolated scratch dirs; the checked-in
  dummy-org is never mutated).
- `test_ids.py` — the id/route-normalization invariants (producer and consumer
  sides must normalize routes identically or edges silently fail to resolve).
- `test_extract.py` — edge recall = 1.0 (all-source AND heuristic-only); core
  entities present; route signature embeds response fields.
- `test_scenario_a.py` — breaking-change propagation: `UPSTREAM.md` flags the
  breaking route change, cites `src/pay.py`, includes both origin and latest SHAs;
  `impact` surfaces it for orders-svc.
- `test_scenario_b.py` — attribution: the `/v1/charge` timeline retains both the
  originating `added` and the latest `signature_change`; both survive compaction.
- `test_scenario_c.py` — compression + noise: internal churn on the unconsumed
  health route leaves no `state_summary` line; every summary ≤ token cap; the
  breaking change is not dropped; `notifications-svc` gets zero flags; the compacted
  state-size series plateaus over the churn stretch.

## Run

```
conda activate orgctx
pytest tests/ -q
```
