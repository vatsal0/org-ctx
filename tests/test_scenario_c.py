"""Scenario C — compression + noise control (plan.md §7).

Compression:
  (i)   no internal, no-inbound-edge change created a line in any state_summary,
  (ii)  every state_summary is within the token cap,
  (iii) no unresolved breaking change was dropped.
Plus the state-size plateau: internal churn must not grow the compacted state.

Noise:
  running `impact` for notifications-svc after the /v1/charge break yields ZERO
  flags — it consumes neither the route nor Charge.
"""

import tempfile
from pathlib import Path

from orgctx.impact import compute_impact
from orgctx.llm import count_tokens
from orgctx.models import ChangeKind
from orgctx.policy import load_policy

HEALTH = "payments-svc::http:GET /v1/health"
ROUTE = "payments-svc::http:POST /v1/charge"


def test_internal_churn_leaves_no_summary_line(broken_graph):
    # The unconsumed health route received a dozen internal churn events; none may
    # appear in its state_summary.
    graph, _, _ = broken_graph
    health = graph.get_entity(HEALTH)
    # It genuinely has internal events (the churn) but an empty hot state.
    kinds = {ev.change_kind for ev in graph.events_for(HEALTH)}
    assert ChangeKind.INTERNAL in kinds, "expected internal churn on the health route"
    assert not graph.has_inbound_edge(HEALTH)
    assert not (health.state_summary or "").strip(), "internal churn leaked into state_summary"


def test_every_state_summary_within_token_cap(broken_graph):
    graph, _, _ = broken_graph
    cap = load_policy(None).token_cap
    for e in graph.all_entities():
        assert count_tokens(e.state_summary or "") <= cap, f"{e.entity_id} exceeds token cap"


def test_no_breaking_change_dropped(broken_graph):
    # The route's breaking signature change must survive in its state_summary.
    graph, _, _ = broken_graph
    summary = graph.get_entity(ROUTE).state_summary or ""
    assert "BREAKING" in summary, "an unresolved breaking change was dropped from state_summary"


def test_noise_notifications_gets_zero_flags(broken_graph):
    graph, org, _ = broken_graph
    policy = load_policy(None)
    upstream, downstream = compute_impact(graph, str(org), "notifications-svc", "HEAD", policy.impact_hops)
    flags = len(upstream) + sum(len(v) for v in downstream.values())
    assert flags == 0, f"notifications-svc should be silent, got {flags} flags"


def test_state_size_plateaus_over_churn():
    # Building the per-commit series is a bit heavy, so this test stands alone. The
    # compacted state must have a long flat run (the churn stretch) that the naive
    # per-event baseline never has.
    from eval.harness import state_size_series

    with tempfile.TemporaryDirectory() as tmp:
        compacted, naive = state_size_series(Path(tmp), break_change=True)

    def longest_flat(series):
        best = run = 1
        for a, b in zip(series, series[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        return best

    assert longest_flat(compacted) >= 10, "compacted state did not plateau over churn"
    # The naive baseline grows essentially every commit (no long flat run).
    assert longest_flat(naive) < 5, "naive baseline unexpectedly flat"
