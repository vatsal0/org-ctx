"""Quantitative metrics for the OrgContext eval (plan.md §7).

These are the numbers that tell us whether the tool is doing its job on the
known-ground-truth dummies, BEFORE we ever trust it on a real repo:

  - **edge_recall** — did we recover the ground-truth interface edges? Reported
    twice: all-source (manifest + heuristic; should be 1.0) and heuristic-only
    (manifest-declared edges stripped; the honest measure of how good the static
    extractors are on their own).
  - **impact_precision / impact_recall** — for a labeled diff, did `impact` flag
    exactly the services that should be flagged (recall) and no others (precision)?
  - **noise_ratio** — flags per PR; an unaffected service must contribute 0.
  - **state_size_series** — total state_summary tokens across all entities after
    each commit. The load-bearing plot: it must PLATEAU, not grow linearly. A
    rising line means the compression policy is failing.

All functions are pure (graph/data in, numbers out) so run_eval.py can log them.
"""

from __future__ import annotations

from dataclasses import dataclass

from orgctx.db import Graph
from orgctx.ids import parse_id
from orgctx.llm import count_tokens
from orgctx.models import EdgeSource


@dataclass(frozen=True)
class Recall:
    total: int
    recovered: int

    @property
    def value(self) -> float:
        return self.recovered / self.total if self.total else 1.0


def edge_recall(graph: Graph, expected_edges: list[dict], *, heuristic_only: bool = False) -> Recall:
    """Recall of ground-truth edges. `expected_edges` are {from,to,kind} dicts.

    When `heuristic_only`, edges whose ONLY provenance is the manifest are removed
    from the recovered set first — so we measure what the static extractors found
    without the manifest recall floor. The comparison includes the edge KIND, so a
    right-endpoints/wrong-kind edge does not count as recovered.
    """
    got = set()
    for e in graph.all_edges():
        if heuristic_only and e.source is EdgeSource.MANIFEST:
            continue
        got.add((e.from_entity, e.to_entity, e.edge_kind.value))
    expected = {(e["from"], e["to"], e["kind"]) for e in expected_edges}
    return Recall(total=len(expected), recovered=len(expected & got))


@dataclass(frozen=True)
class PrecisionRecall:
    expected: set
    flagged: set

    @property
    def precision(self) -> float:
        return len(self.expected & self.flagged) / len(self.flagged) if self.flagged else 1.0

    @property
    def recall(self) -> float:
        return len(self.expected & self.flagged) / len(self.expected) if self.expected else 1.0


def impact_precision_recall(flagged_services: set[str], expected_services: set[str]) -> PrecisionRecall:
    """Compare the services `impact` flagged against the hand-labeled truth set."""
    return PrecisionRecall(expected=set(expected_services), flagged=set(flagged_services))


def noise_ratio(flag_counts: dict[str, int]) -> float:
    """Average flags per service across a PR. Lower is better; unaffected services
    must contribute 0 (that is asserted separately — this is the aggregate view)."""
    if not flag_counts:
        return 0.0
    return sum(flag_counts.values()) / len(flag_counts)


def total_state_size(graph: Graph) -> int:
    """Total state_summary tokens across all entities — one point on the plateau
    curve. Uses the same token proxy as the compaction cap so the two agree."""
    return sum(count_tokens(e.state_summary or "") for e in graph.all_entities())


def naive_state_size(graph: Graph) -> int:
    """Counterfactual: total tokens if EVERY change event got its own summary line
    (no compression). The gap between this and total_state_size is the compression
    win — and its linear growth is what the policy is preventing."""
    total = 0
    for e in graph.all_entities():
        for ev in graph.events_for(e.entity_id):
            total += count_tokens(ev.summary or "")
    return total
