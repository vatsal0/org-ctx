"""`orgctx compact [entity|all]` — apply the compression policy to build state_summary.

This is the mechanism plan.md argues *is* the product: deciding what earns a
permanent line in an entity's living history, what folds into a one-liner, and what
gets dropped. The policy lives in policy.yaml (tunable against the eval); this
module applies it:

  - **Promote** (permanent line): `signature_change`, `removed`, and `added` on a
    CONSUMED entity. These are contract facts a downstream reader must never lose.
  - **Fold** (one running line): `modified` / `behavior_note` collapse into a
    single "changes: [sha] ...; [sha] ..." line — churn that matters but shouldn't
    each get a line.
  - **Drop** (never in state_summary): `internal`. Retained in the full event log
    (the cold store), but kept out of the hot summary. This is what makes internal
    churn on an unconsumed entity (the /v1/health route) leave no trace in the
    summary — the compression assertion.

If a summary exceeds the per-entity token cap, we front-load the must-keep lines
(origin, latest, every unresolved breaking change) and ask the (mockable) LLM
boundary to recompact the rest, so the cap can never silently drop a breaking
change.

`compact` also writes one markdown file per entity under central/entities/, the
human/agent-readable "current truth + timeline pointers" view.
"""

from __future__ import annotations

from pathlib import Path

from . import llm
from .db import Graph
from .ids import parse_id, slugify
from .models import ChangeEvent, ChangeKind, Entity
from .policy import Policy, load_policy


def _line(ev: ChangeEvent) -> str:
    """One permanent-history line for a promoted event."""
    flag = " ⚠️BREAKING" if ev.breaking else ""
    return f"[{ev.commit_sha[:7]}] {ev.change_kind.value}{flag}: {ev.summary or '(no summary)'}"


def compact_entity(graph: Graph, policy: Policy, entity: Entity) -> str:
    """Compute and store the compacted state_summary for one entity; return it."""
    events = graph.events_for(entity.entity_id)
    has_inbound = graph.has_inbound_edge(entity.entity_id)

    promoted: list[ChangeEvent] = []
    folded: list[ChangeEvent] = []
    for ev in events:
        kind = ev.change_kind
        if kind in policy.promote_kinds:
            promoted.append(ev)
        elif kind is ChangeKind.ADDED and policy.promote_added_if_inbound and has_inbound:
            promoted.append(ev)
        elif kind in policy.fold_kinds:
            folded.append(ev)
        elif kind in policy.drop_kinds:
            # Drop from the hot state — but only when the policy allows. With
            # require_no_inbound_to_drop set, churn on a CONSUMED entity is folded
            # instead of dropped (a downstream reader may care that it changed at
            # all); churn on an entity nobody consumes is dropped outright. Dropped
            # events are excluded from state_summary but remain in the cold event log.
            if policy.require_no_inbound_to_drop and has_inbound:
                folded.append(ev)
        # else: an ADDED on a no-inbound entity, or any residual kind -> excluded.

    lines = [_line(ev) for ev in promoted]
    if folded:
        # Collapse all foldable changes into a single running one-liner.
        parts = "; ".join(f"[{ev.commit_sha[:7]}] {ev.summary}" for ev in folded)
        lines.append(f"changes: {parts}")

    summary = "\n".join(lines)

    # Token cap: if over, front-load must-keep lines and recompact the rest.
    if llm.count_tokens(summary) > policy.token_cap:
        summary = _recompact(policy, entity, promoted, folded, lines)

    graph.set_state_summary(entity.entity_id, summary)
    return summary


def _recompact(policy: Policy, entity: Entity, promoted, folded, lines) -> str:
    """Order lines so the must-keep set (origin, latest, unresolved breaking) leads,
    then delegate shortening to the LLM boundary (mock = keep-leading-then-truncate).
    """
    must_keep: list[str] = []
    if "origin" in policy.keep_on_overflow and promoted:
        must_keep.append(_line(promoted[0]))          # earliest promoted = origin
    if "unresolved_breaking" in policy.keep_on_overflow:
        must_keep.extend(_line(ev) for ev in promoted if ev.breaking)
    if "latest" in policy.keep_on_overflow and promoted:
        must_keep.append(_line(promoted[-1]))         # most recent promoted = latest
    # De-duplicate while preserving order, then append the remaining lines.
    seen = set()
    ordered = []
    for ln in must_keep + lines:
        if ln not in seen:
            seen.add(ln)
            ordered.append(ln)
    return llm.recompact(entity_id=entity.entity_id, lines=ordered, token_cap=policy.token_cap)


def _write_entity_file(central: Path, graph: Graph, entity: Entity) -> None:
    """Write central/entities/<slug>.md: signature + state_summary + timeline pointers."""
    _, _, local = parse_id(entity.entity_id)
    out = central / "entities"
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {entity.entity_id}", "",
        f"- Kind: `{entity.kind.value}`",
        f"- Service: `{entity.service}`",
        f"- Signature: `{entity.signature}`",
        f"- Origin: `{entity.origin_commit}` · Latest: `{entity.latest_commit}`",
        "", "## Current state", "",
        entity.state_summary or "_(no promoted history)_",
        "", "## Timeline (pointers)", "",
    ]
    for ev in graph.events_for(entity.entity_id):
        lines.append(f"- [{ev.commit_sha[:7]}] {ev.change_kind.value}"
                     f"{' ⚠️' if ev.breaking else ''} — {ev.diff_ref}")
    (out / f"{slugify(entity.entity_id)}.md").write_text("\n".join(lines).rstrip() + "\n")


def compact_all(graph: Graph, policy: Policy, central: Path, target: str = "all") -> int:
    """Compact `target` ("all" or a single entity id). Returns entities compacted."""
    if target == "all":
        entities = graph.all_entities()
    else:
        e = graph.get_entity(target)
        entities = [e] if e else []
    for entity in entities:
        compact_entity(graph, policy, entity)
        # Re-read to capture the freshly-written state_summary in the entity file.
        _write_entity_file(central, graph, graph.get_entity(entity.entity_id))
    return len(entities)


def run(args) -> int:
    """CLI handler for `orgctx compact [target]`."""
    central = Path(args.central)
    graph = Graph(central / "graph.db")
    policy = load_policy(args.policy)
    n = compact_all(graph, policy, central, args.target)
    print(f"compacted {n} entities under token_cap={policy.token_cap}")
    graph.close()
    return 0
