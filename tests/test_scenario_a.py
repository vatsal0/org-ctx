"""Scenario A — breaking-change propagation (plan.md §7).

After the breaking rename, `sync orders-svc` must produce an UPSTREAM.md that:
  (i)   lists the change under Breaking,
  (ii)  names the payments route entity,
  (iii) cites the consuming line in orders-svc/src/pay.py,
  (iv)  includes BOTH the origin and the latest SHA (attribution).
And `impact orders-svc --diff HEAD` must surface the payments change.
"""

from orgctx.impact import compute_impact
from orgctx.policy import load_policy
from orgctx.sync import sync_service

ROUTE = "payments-svc::http:POST /v1/charge"


def test_upstream_md_flags_breaking_with_attribution(broken_graph):
    graph, org, _ = broken_graph
    upstream_path = sync_service(graph, str(org), "orders-svc", str(org / "orders-svc"))
    text = upstream_path.read_text()

    # (i) Breaking section present.
    assert "## ⚠️ Breaking" in text
    # (ii) names the route.
    assert "POST /v1/charge" in text
    # (iii) cites the consuming line.
    assert "orders-svc/src/pay.py" in text

    # (iv) both origin AND latest SHAs, and they differ (real attribution).
    route = graph.get_entity(ROUTE)
    assert route.origin_commit[:7] in text
    assert route.latest_commit[:7] in text
    assert route.origin_commit != route.latest_commit


def test_impact_surfaces_payments_change_for_orders(broken_graph):
    graph, org, _ = broken_graph
    policy = load_policy(None)
    upstream, downstream = compute_impact(graph, str(org), "orders-svc", "HEAD", policy.impact_hops)
    surfaced = {e.entity_id for e in upstream}
    assert ROUTE in surfaced, "impact did not surface the payments route change to orders-svc"
    # The surfaced change is marked breaking.
    assert any(e.breaking for e in upstream if e.entity_id == ROUTE)
