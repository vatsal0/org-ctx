"""Walk git history and turn each commit's diffs into per-entity change events.

The central trick (plan.md §5.2, §11 risk 1) is **extract-at-both-revisions**:
for each changed file we extract the entity table at the commit's parent and at the
commit itself, then *diff the two tables*. This sidesteps fragile line-drift hunk
mapping — we compare canonical entities, not line numbers:

    id present only after  -> ADDED
    id present only before -> REMOVED
    id present in both, signature differs -> SIGNATURE_CHANGE (or MODIFIED for fields)
    id present in both, signature same, but body region was touched -> INTERNAL

The schema-model index is rebuilt at each revision from the whole org tree so a
route's embedded response-model fields resolve consistently — that is what lets a
`Charge.amount` rename register as a *signature change on the payments route*.

Each detected change becomes one LLM call (mockable) that writes the human-readable
`summary` and refines the `change_kind`. The `breaking` flag is then gated on a
hard rule: a signature change or removal is only "breaking" if the entity actually
has an inbound consumer — so an unconsumed route's churn can never masquerade as a
breaking change. Entities are upserted as they are encountered (so historical,
since-removed entities like `Charge.amount` still satisfy the event foreign key),
and each entity's origin/latest commit pointers are stamped as we go.

Ordering note: run `extract` (over the working tree) BEFORE `ingest`, so the edge
graph exists when the breaking-change guard consults it.
"""

from __future__ import annotations

from pathlib import Path

from . import extract, gitutil, llm
from .db import Graph
from .models import ChangeEvent, ChangeKind, Entity, Kind


def _org_sources_at(repo: str, rev: str) -> dict[str, str]:
    """All *.py files present at `rev`, keyed by org-relative path.

    Used to build the schema-model index at a specific revision (so route response
    fields resolve as they were at that point in history)."""
    out: dict[str, str] = {}
    for path in gitutil.list_tree_files(repo, rev, ".py"):
        content = gitutil.file_at_rev(repo, rev, path)
        if content is not None:
            out[path] = content
    return out


def _entities_at(content: str | None, service: str, path: str, model_index: dict[str, str]) -> dict[str, Entity]:
    """Entity table for one file at one revision, keyed by id (empty if absent)."""
    if content is None:
        return {}
    return {e.entity_id: e for e in extract.extract_entities(content, service, path, model_index)}


def _regions(entities: dict[str, Entity]) -> dict[str, tuple[int, int]]:
    """Approximate each entity's line region as [its def_line, next def_line).

    We sort entities in a file by def_line and treat each one's region as spanning
    up to the next entity's definition (the last one runs to +inf). This is a cheap,
    robust proxy for "was this entity's body touched?" without tracking end lines:
    a churn edit inside `health()` falls in health's region but not in the earlier
    route's. Entities without a def_line (rare) are skipped for body-touch.
    """
    located = sorted(
        ((e.def_line, eid) for eid, e in entities.items() if e.def_line is not None),
    )
    regions: dict[str, tuple[int, int]] = {}
    for i, (line, eid) in enumerate(located):
        end = located[i + 1][0] - 1 if i + 1 < len(located) else 10**9
        regions[eid] = (line, end)
    return regions


def _overlaps(region: tuple[int, int], ranges: list[tuple[int, int]]) -> bool:
    """Does an entity region intersect any changed line range?"""
    lo, hi = region
    return any(not (r_hi < lo or r_lo > hi) for r_lo, r_hi in ranges)


def _classify(kind: Kind) -> ChangeKind:
    """Structural classification of a same-id signature change, from the entity kind.

    A schema FIELD whose declaration changed but was not renamed (same id) is a
    foldable MODIFIED (e.g. adding an ISO-4217 validator). Any other same-id
    signature change is a SIGNATURE_CHANGE (route param added, model field set
    changed, export signature changed).
    """
    if kind is Kind.SCHEMA_FIELD:
        return ChangeKind.MODIFIED
    return ChangeKind.SIGNATURE_CHANGE


def _process_commit(graph: Graph, repo: str, commit: gitutil.Commit) -> int:
    """Ingest a single commit; return the number of change events created."""
    parent = gitutil.parent_sha(repo, commit.sha)
    # Model indexes at both revisions (empty parent index for a root commit).
    new_model_index = extract.build_model_index(_org_sources_at(repo, commit.sha))
    old_model_index = extract.build_model_index(_org_sources_at(repo, parent)) if parent else {}

    events = 0
    for path in gitutil.changed_files(repo, commit.sha):
        if not path.endswith(".py"):
            continue
        service = extract.service_of(path)
        old_content = gitutil.file_at_rev(repo, parent, path) if parent else None
        new_content = gitutil.file_at_rev(repo, commit.sha, path)

        old_entities = _entities_at(old_content, service, path, old_model_index)
        new_entities = _entities_at(new_content, service, path, new_model_index)

        changed_ranges = gitutil.changed_line_ranges(repo, commit.sha, path)
        regions = _regions(new_entities)

        # Union of ids seen at either revision; classify each.
        for eid in set(old_entities) | set(new_entities):
            old_e = old_entities.get(eid)
            new_e = new_entities.get(eid)

            if new_e and not old_e:
                structural = ChangeKind.ADDED
            elif old_e and not new_e:
                structural = ChangeKind.REMOVED
            elif old_e.signature != new_e.signature:
                structural = _classify(new_e.kind)
            elif eid in regions and _overlaps(regions[eid], changed_ranges):
                structural = ChangeKind.INTERNAL   # body touched, contract intact
            else:
                continue  # entity present but untouched by this commit

            # Make sure the entity exists (for the FK) with its current-revision
            # descriptive fields; stamp commit bounds. For a REMOVED entity we keep
            # the pre-removal descriptor so its row remains valid.
            descriptor = new_e or old_e
            graph.upsert_entity(
                Entity(**{**descriptor.__dict__, "origin_commit": commit.sha, "latest_commit": commit.sha})
            )
            graph.set_commit_bounds(eid, origin=commit.sha, latest=commit.sha)

            # Summarize + refine via the (mockable) LLM boundary.
            verdict = llm.summarize_change(
                entity_id=eid,
                kind=descriptor.kind.value,
                old_signature=old_e.signature if old_e else None,
                new_signature=new_e.signature if new_e else None,
                structural_kind=structural,
            )
            # Hard breaking guard: only a signature change / removal on a CONSUMED
            # entity is actionable-breaking. The LLM's opinion cannot invent a
            # breaking flag on an entity nobody depends on.
            breaking = (
                verdict.change_kind in (ChangeKind.SIGNATURE_CHANGE, ChangeKind.REMOVED)
                and graph.has_inbound_edge(eid)
            )

            graph.add_event(
                ChangeEvent(
                    entity_id=eid,
                    commit_sha=commit.sha,
                    author=commit.author,
                    timestamp=commit.date,
                    change_kind=verdict.change_kind,
                    breaking=breaking,
                    summary=verdict.summary,
                    diff_ref=f"{commit.sha}:{path}",
                )
            )
            events += 1
    return events


def ingest_range(graph: Graph, repo: str, commit_range: str) -> int:
    """Ingest every commit in `commit_range` (oldest first). Returns event count."""
    total = 0
    for commit in gitutil.list_commits(repo, commit_range):
        total += _process_commit(graph, repo, commit)
    return total


def run(args) -> int:
    """CLI handler for `orgctx ingest <repo> <commit_range>`."""
    graph = Graph(Path(args.central) / "graph.db")
    n = ingest_range(graph, args.repo, args.commit_range)
    print(f"ingested {args.commit_range}: created {n} change events")
    graph.close()
    return 0
