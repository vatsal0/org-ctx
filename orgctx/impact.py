"""`orgctx impact <repo> [--diff <ref>]` — the "affects you" / "who you break" digest.

Given the files a diff touches, this resolves the *entities* touched and traverses
the interface graph in BOTH directions relative to the queried service R (this
dual behavior resolves the one genuine spec ambiguity — see plan.md §5.4 vs the
Scenario A step-4 assertion):

  - **Upstream ("what affects you"):** touched entities that R *consumes* — surface
    their recent contract changes, citing R's consuming line. This is the case when
    R edits its own consuming code (Scenario A step 4) or when an upstream service's
    change lands on an entity R depends on.

  - **Downstream ("who you break"):** touched entities that R *owns* — traverse
    inbound edges to find the consuming services R may break, grouped by service.

The noise-control property (Scenario C): a service with NO edge to any touched
entity produces ZERO flags. Silence for the unaffected is the whole point — so we
only ever emit a section when there is something to say, and `impact` returns a
flag count of 0 for a genuinely-unaffected service.
"""

from __future__ import annotations

from pathlib import Path

from . import gitutil
from .db import Graph
from .ids import parse_id
from .models import ChangeKind, Edge
from .policy import load_policy
from .render import UpstreamEntry, render_impact, render_upstream


# Which change kinds are worth surfacing to a human/agent (same as sync).
_SURFACE_KINDS = {ChangeKind.REMOVED, ChangeKind.SIGNATURE_CHANGE, ChangeKind.MODIFIED, ChangeKind.BEHAVIOR_NOTE}


def _diff_files(repo: str, ref: str) -> list[str]:
    """Org-relative files touched by `ref`.

    A range ("a..b" / "a...b") is diffed directly; a single ref ("HEAD", a sha) is
    treated as one commit's change set. This lets `impact` take either a PR range or
    a single commit interchangeably.
    """
    if ".." in ref:
        out = gitutil._git(repo, "diff", "--name-only", ref)
        return [line for line in out.splitlines() if line]
    return gitutil.changed_files(repo, ref)


def _touched_entity_ids(graph: Graph, changed_files: set[str]) -> set[str]:
    """Entities implicated by a change set.

    An entity is touched if it is DEFINED in a changed file, OR if it is the
    producer end of an edge whose CONSUMING file was changed (so editing a call
    site implicates the route it calls — the Scenario A step-4 path).
    """
    touched: set[str] = set()
    for e in graph.all_entities():
        if e.def_file in changed_files:
            touched.add(e.entity_id)
    for edge in graph.all_edges():
        if edge.from_file in changed_files:
            touched.add(edge.to_entity)
    return touched


def _latest_surface_event(graph: Graph, entity_id: str):
    """The most recent surface-worthy change event on an entity (or None)."""
    surfaced = [ev for ev in graph.events_for(entity_id) if ev.change_kind in _SURFACE_KINDS]
    return surfaced[-1] if surfaced else None


def _entry_for(graph: Graph, entity_id: str, edge: Edge | None) -> UpstreamEntry | None:
    """Build an UpstreamEntry for a touched entity from its latest change, citing
    `edge`'s consuming location if provided. Returns None if the entity has no
    surface-worthy change (so untouched-but-adjacent entities stay silent)."""
    ev = _latest_surface_event(graph, entity_id)
    if ev is None:
        return None
    producer = graph.get_entity(entity_id)
    if producer is None:
        return None
    producer_service, _, local = parse_id(entity_id)
    return UpstreamEntry(
        entity_id=entity_id,
        producer_service=producer_service,
        local=local,
        summary=ev.summary or "",
        origin=producer.origin_commit,
        latest=producer.latest_commit,
        breaking=ev.breaking,
        change_kind=ev.change_kind.value,
        consume_file=edge.from_file if edge else None,
        consume_line=edge.from_line if edge else None,
    )


def compute_impact(graph: Graph, repo: str, service: str, diff_ref: str, hops: int):
    """Return (upstream_entries, downstream_groups) for `service` given a diff.

    `hops` is the policy's transitive-depth knob. Only depth 1 (direct consumers)
    is implemented today; the parameter is threaded through for forward-compat and
    documented as a future extension (see the downstream section below).
    """
    del hops  # accepted for API stability; only depth-1 traversal is implemented
    changed = set(_diff_files(repo, diff_ref))
    touched = _touched_entity_ids(graph, changed)

    # -- Upstream: touched entities that `service` consumes.
    upstream: list[UpstreamEntry] = []
    my_edges = {e.to_entity: e for e in graph.outbound_edges_for_service(service)}
    for entity_id in sorted(touched):
        if entity_id in my_edges:
            entry = _entry_for(graph, entity_id, my_edges[entity_id])
            if entry:
                upstream.append(entry)

    # -- Downstream: touched entities that `service` owns -> direct consumers,
    # grouped by consuming service. This is a single hop: direct consumers of the
    # changed entity. Transitive (multi-hop) propagation is a documented future
    # extension (plan.md §10 step 4 / §11) — it needs a graph with a real
    # C->B->A chain to be meaningful, which the demo org does not have. `hops` is
    # accepted for forward-compatibility but only depth 1 is implemented today.
    downstream: dict[str, list[UpstreamEntry]] = {}
    owned_touched = [eid for eid in touched if getattr(graph.get_entity(eid), "service", None) == service]
    for entity_id in owned_touched:
        for inbound in graph.inbound_edges(entity_id):
            consumer_service = parse_id(inbound.from_entity)[0]
            if consumer_service == service:
                continue  # do not report yourself
            entry = _entry_for(graph, entity_id, inbound)
            if entry:
                downstream.setdefault(consumer_service, []).append(entry)
    return upstream, downstream


def render(service: str, upstream: list[UpstreamEntry], downstream: dict[str, list[UpstreamEntry]]) -> str:
    """Combined digest: an upstream ("affects you") block and a downstream
    ("who you break") block. Emits an explicit 'nothing' line when both are empty
    so silence is legible."""
    if not upstream and not downstream:
        return f"# Impact for {service}\n\n_No changes in this diff affect or originate from {service}._\n"
    parts = [f"# Impact for {service}", ""]
    if upstream:
        parts.append(render_upstream(f"{service} (upstream)", upstream, None).rstrip())
        parts.append("")
    if downstream:
        parts.append(render_impact(service, downstream).rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def run(args) -> int:
    """CLI handler for `orgctx impact <repo> [--diff <ref>]`."""
    graph = Graph(Path(args.central) / "graph.db")
    policy = load_policy(args.policy)
    hops = args.hops if args.hops is not None else policy.impact_hops
    service = args.service or Path(args.repo).resolve().name
    upstream, downstream = compute_impact(graph, args.repo, service, args.diff, hops)
    flags = len(upstream) + sum(len(v) for v in downstream.values())
    print(render(service, upstream, downstream))
    print(f"[impact] {flags} flag(s) for {service}", flush=True)
    graph.close()
    return 0
