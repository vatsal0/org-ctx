# `eval/` — metrics, harness, and the plateau plot

Builds the dummy-org graph from scratch and measures whether the tool works on
known ground truth (plan.md §7). Everything runs under the deterministic LLM mock.

## Files

- `harness.py` — the shared build pipeline. `build_graph(workdir, break_change)`
  copies the clean dummy-org source into a scratch dir, runs the seed (and optional
  break) scripts, then extract → ingest → compact, returning an open `Graph`. Used
  by both `run_eval.py` and the pytest suite (via `tests/conftest.py`) so they can
  never drift. `state_size_series(...)` builds the graph one commit at a time to
  produce the plateau curve (and a naive no-compression baseline).
- `metrics.py` — pure metric functions: `edge_recall` (all-source + heuristic-only),
  `impact_precision_recall`, `noise_ratio`, `total_state_size` / `naive_state_size`.
- `labels.yaml` — hand-labeled impact ground truth for the break diff (which
  services are upstream-affected; which must stay silent).
- `run_eval.py` — `python -m eval.run_eval`: prints the metrics table and writes
  `eval/out/state_size.png`, the compacted-vs-naive plateau plot (the stop-and-
  evaluate gate — a rising compacted line means the compression policy is failing).

## What "good" looks like

- edge recall = 1.0 (both all-source and heuristic-only) on the dummies,
- impact precision = recall = 1.0 against `labels.yaml`,
- `notifications-svc` = 0 flags after the break,
- a long flat run in the compacted state-size series (the churn plateau) while the
  naive baseline grows every commit.
