"""Extraction + edge-recall tests (plan.md §9 step 2 milestone: graph == truth).

The headline assertion: edge recall against the hand-written oracle is 1.0, for
BOTH the all-source graph and the heuristic-only graph (manifest edges stripped).
Reaching heuristic-only 1.0 is what proves the static extractors — not just the
manifest — actually recover the cross-service graph.
"""

import yaml

from eval.metrics import edge_recall
from eval.harness import REPO_ROOT


def _expected():
    return yaml.safe_load((REPO_ROOT / "dummy-org" / "expected_edges.yaml").read_text())["edges"]


def test_edge_recall_all_source_is_one(broken_graph):
    graph, _, _ = broken_graph
    r = edge_recall(graph, _expected())
    assert r.value == 1.0, f"missing edges; recovered {r.recovered}/{r.total}"


def test_edge_recall_heuristic_only_is_one(broken_graph):
    # The static extractors alone (no manifest floor) must recover every edge.
    graph, _, _ = broken_graph
    r = edge_recall(graph, _expected(), heuristic_only=True)
    assert r.value == 1.0, f"heuristic missed edges; recovered {r.recovered}/{r.total}"


def test_core_entities_present(broken_graph):
    graph, _, _ = broken_graph
    ids = {e.entity_id for e in graph.all_entities()}
    for expected in [
        "payments-svc::http:POST /v1/charge",
        "contracts::schema:Charge",
        "orders-svc::topic:order.created",
        "orders-svc::env:PAYMENTS_BASE_URL",
    ]:
        assert expected in ids, f"missing entity {expected}"


def test_route_signature_embeds_response_fields(broken_graph):
    # The charge route's signature must embed Charge's field set, so a field rename
    # registers as a route signature change (the propagation mechanism).
    graph, _, _ = broken_graph
    route = graph.get_entity("payments-svc::http:POST /v1/charge")
    assert "amount_cents" in route.signature  # post-break field is embedded
