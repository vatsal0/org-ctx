"""Scenario B — attribution (origin vs latest) (plan.md §7).

For /v1/charge — added at the scaffold commit, mutated by a later signature change
— the entity timeline must retain and surface BOTH the originating feature commit
and the latest mutation, not collapse to a single answer.
"""

from orgctx.models import ChangeKind

ROUTE = "payments-svc::http:POST /v1/charge"


def test_timeline_retains_origin_and_latest(broken_graph):
    graph, _, _ = broken_graph
    events = graph.events_for(ROUTE)
    kinds = [ev.change_kind for ev in events]

    # Origin (added) and a later signature change both survive on the timeline.
    assert ChangeKind.ADDED in kinds, "originating feature commit was lost"
    assert ChangeKind.SIGNATURE_CHANGE in kinds, "latest signature change was lost"

    # The entity's commit bounds point at distinct commits: origin != latest.
    entity = graph.get_entity(ROUTE)
    assert entity.origin_commit != entity.latest_commit

    # The origin bound is the ADDED event's commit; the latest bound is a later one.
    added = next(ev for ev in events if ev.change_kind is ChangeKind.ADDED)
    assert entity.origin_commit == added.commit_sha
    assert entity.latest_commit != added.commit_sha


def test_state_summary_surfaces_both(broken_graph):
    # After compaction, the promoted state_summary keeps both the origin line and
    # the breaking signature-change line — attribution survives compression.
    graph, _, _ = broken_graph
    summary = graph.get_entity(ROUTE).state_summary or ""
    assert "added" in summary
    assert "signature_change" in summary
