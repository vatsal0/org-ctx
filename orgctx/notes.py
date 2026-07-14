"""`orgctx render-notes [--since <ref>]` — release notes as a byproduct of the graph.

Unlike a commit-title changelog, these notes are *scoped by who is affected*: for
each changed producer contract we resolve its downstream consumers (inbound edges)
and file the change under each affected service, plus a global changelog. This is
the "targeted notes instead of an ignored Slack feed" idea from plan.md §1/§10 —
the same graph that powers impact/sync produces the notes for free.

Deterministic ordering throughout (service name, then severity, then commit) so the
output is stable and testable.
"""

from __future__ import annotations

from pathlib import Path

from . import gitutil
from .db import Graph
from .ids import parse_id
from .models import ChangeKind

_SURFACE_KINDS = {ChangeKind.REMOVED, ChangeKind.SIGNATURE_CHANGE, ChangeKind.MODIFIED, ChangeKind.BEHAVIOR_NOTE}
_SEVERITY = {ChangeKind.REMOVED: 4, ChangeKind.SIGNATURE_CHANGE: 3, ChangeKind.MODIFIED: 2, ChangeKind.BEHAVIOR_NOTE: 2}


def _window(repo: str, since: str | None) -> set[str] | None:
    """Commit shas in (since..HEAD], or None to mean 'all of history'."""
    if since is None:
        return None
    return {c.sha for c in gitutil.list_commits(repo, f"{since}..HEAD")}


def compute_notes(graph: Graph, repo: str, since: str | None, only_service: str | None):
    """Return (per_service, changelog).

    per_service: {affected_service: [ (severity, sha, producer_service, local, summary, breaking) ]}
    changelog:   the same tuples across the whole org (deduped), globally.
    """
    window = _window(repo, since)
    per_service: dict[str, list[tuple]] = {}
    changelog: list[tuple] = []

    for entity in graph.all_entities():
        producer_service, _, local = parse_id(entity.entity_id)
        for ev in graph.events_for(entity.entity_id):
            if ev.change_kind not in _SURFACE_KINDS:
                continue
            if window is not None and ev.commit_sha not in window:
                continue
            row = (_SEVERITY.get(ev.change_kind, 0), ev.commit_sha, producer_service, local, ev.summary or "", ev.breaking)
            changelog.append(row)
            # File under each downstream consumer service.
            for inbound in graph.inbound_edges(entity.entity_id):
                consumer = parse_id(inbound.from_entity)[0]
                if consumer == producer_service:
                    continue
                if only_service and consumer != only_service:
                    continue
                per_service.setdefault(consumer, []).append(row)

    return per_service, changelog


def render_notes(per_service: dict[str, list[tuple]], changelog: list[tuple], only_service: str | None) -> str:
    """Render the scoped release notes markdown."""
    lines = ["# Release notes", ""]

    def _fmt(row: tuple) -> str:
        _sev, sha, producer_service, local, summary, breaking = row
        flag = "⚠️ " if breaking else ""
        return f"- {flag}**{producer_service} `{local}`** ({sha[:7]}) — {summary}"

    lines.append("## Affected services")
    lines.append("")
    services = [only_service] if only_service else sorted(per_service)
    any_service = False
    for svc in services:
        rows = per_service.get(svc, [])
        if not rows:
            continue
        any_service = True
        lines.append(f"### {svc}")
        for row in sorted(rows, key=lambda r: (-r[0], r[1])):
            lines.append(_fmt(row))
        lines.append("")
    if not any_service:
        lines.append("_No services affected in this window._")
        lines.append("")

    if not only_service:
        lines.append("## Full changelog")
        lines.append("")
        # Dedupe rows (an event consumed by multiple services appears once here).
        for row in sorted(set(changelog), key=lambda r: (-r[0], r[1])):
            lines.append(_fmt(row))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run(args) -> int:
    """CLI handler for `orgctx render-notes [--since <ref>]`.

    `--repo` (default dummy-org) is the org monorepo whose history bounds a
    `--since` window; with no `--since` the whole recorded timeline is used and the
    repo is not consulted.
    """
    graph = Graph(Path(args.central) / "graph.db")
    per_service, changelog = compute_notes(graph, args.repo, args.since, args.service)
    print(render_notes(per_service, changelog, args.service))
    graph.close()
    return 0
