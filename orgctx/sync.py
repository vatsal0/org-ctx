"""`orgctx sync <repo>` — regenerate a repo's `.orgcontext/UPSTREAM.md` (+ OWNED.md).

This is the step that turns the shared graph into the payload a coding agent reads.
For the target service we:
  1. find every producer entity the service CONSUMES (the `to_entity` of its
     outbound edges),
  2. gather the change events on those entities that are newer than the service's
     last sync,
  3. rank them (breaking first, then by severity, then recency), and
  4. render UPSTREAM.md, citing — for each changed contract — the exact file/line
     where this service consumes it (from the edge).

We also emit OWNED.md (what this service provides) so other services can read it,
and advance the service's `last_synced_commit` so a subsequent sync only surfaces
what changed since.
"""

from __future__ import annotations

from pathlib import Path

from . import gitutil
from .db import Graph
from .ids import parse_id
from .models import ChangeKind
from .render import UpstreamEntry, render_owned, render_upstream


# Severity ladder for ranking. Higher = surfaced first. Breaking is handled
# separately (it always leads); within a tier we then sort by recency.
_SEVERITY = {
    ChangeKind.REMOVED: 4,
    ChangeKind.SIGNATURE_CHANGE: 3,
    ChangeKind.MODIFIED: 2,
    ChangeKind.BEHAVIOR_NOTE: 2,
    ChangeKind.ADDED: 1,
    ChangeKind.INTERNAL: 0,
}

# Change kinds worth showing a consumer. INTERNAL and ADDED churn on an upstream
# is not actionable for a downstream reader, so we omit them from the payload.
_SURFACE_KINDS = {ChangeKind.REMOVED, ChangeKind.SIGNATURE_CHANGE, ChangeKind.MODIFIED, ChangeKind.BEHAVIOR_NOTE}


def _commits_after(repo: str, last_synced: str | None) -> set[str] | None:
    """Set of commit shas newer than `last_synced` (None => 'everything')."""
    if last_synced is None:
        return None
    return {c.sha for c in gitutil.list_commits(repo, f"{last_synced}..HEAD")}


def compute_upstream_entries(graph: Graph, repo: str, service: str) -> tuple[list[UpstreamEntry], str | None]:
    """Build the ranked UpstreamEntry list for `service`. Returns (entries, head_sha).

    head_sha is the current HEAD of the repo — what we will record as the new
    last-synced marker once the file is written.
    """
    head = gitutil.rev_parse(repo, "HEAD")
    last_synced = graph.get_last_synced(service)
    window = _commits_after(repo, last_synced)

    entries: list[UpstreamEntry] = []
    for edge in graph.outbound_edges_for_service(service):
        producer = graph.get_entity(edge.to_entity)
        if producer is None:
            continue
        producer_service, _, local = parse_id(producer.entity_id)
        for ev in graph.events_for(producer.entity_id):
            if ev.change_kind not in _SURFACE_KINDS:
                continue
            if window is not None and ev.commit_sha not in window:
                continue
            entries.append(
                UpstreamEntry(
                    entity_id=producer.entity_id,
                    producer_service=producer_service,
                    local=local,
                    summary=ev.summary or "",
                    origin=producer.origin_commit,
                    latest=producer.latest_commit,
                    breaking=ev.breaking,
                    change_kind=ev.change_kind.value,
                    consume_file=edge.from_file,
                    consume_line=edge.from_line,
                    event_ts=ev.timestamp,
                )
            )

    # Rank: breaking first, then severity, then recency (newest event first). ISO-8601
    # timestamps sort lexicographically, so a plain string compare gives chronological
    # order; a missing timestamp sorts oldest. All three keys descend together.
    entries.sort(
        key=lambda e: (e.breaking, _SEVERITY.get(ChangeKind(e.change_kind), 0), e.event_ts or ""),
        reverse=True,
    )
    return entries, head


def owned_rows(graph: Graph, service: str) -> list[tuple[str, str, str | None]]:
    """(entity_id, signature, state_summary) for the contracts `service` owns."""
    return [
        (e.entity_id, e.signature or "", e.state_summary)
        for e in graph.all_entities()
        if e.service == service
    ]


def sync_service(graph: Graph, repo: str, service: str, repo_root: str) -> Path:
    """Write UPSTREAM.md + OWNED.md for `service` and advance its sync marker.

    `repo` is where we resolve git state (the org monorepo); `repo_root` is where
    the service's `.orgcontext/` directory lives (the service subdirectory).
    Returns the path to the written UPSTREAM.md.
    """
    entries, head = compute_upstream_entries(graph, repo, service)
    out_dir = Path(repo_root) / ".orgcontext"
    out_dir.mkdir(parents=True, exist_ok=True)

    upstream_path = out_dir / "UPSTREAM.md"
    upstream_path.write_text(render_upstream(service, entries, graph.get_last_synced(service)))
    (out_dir / "OWNED.md").write_text(render_owned(service, owned_rows(graph, service)))

    graph.set_last_synced(service, head)
    return upstream_path


def run(args) -> int:
    """CLI handler for `orgctx sync <repo>`.

    `<repo>` is a SERVICE directory inside the org (e.g. dummy-org/orders-svc). We
    resolve git state against the enclosing org repo (the service dir shares the
    monorepo's .git) and write the service's `.orgcontext/` payloads.
    """
    graph = Graph(Path(args.central) / "graph.db")
    service = args.service or Path(args.repo).resolve().name
    # Git state resolves against whichever ancestor holds the .git — for the demo
    # monorepo that is the org root; passing the service dir to git -C still works
    # because git walks up to the repo root.
    path = sync_service(graph, args.repo, service, args.repo)
    print(f"wrote {path}")
    graph.close()
    return 0
