"""Markdown rendering for the agent-facing payloads and human-facing digests.

Kept as pure functions (data in, string out) so they are trivially testable and so
the command modules (sync, impact, notes) stay focused on *what* to surface while
this module owns *how* it reads. Every renderer is deterministic — no timestamps
from the wall clock — so the eval can assert on exact output.

The single most important payload is UPSTREAM.md: the file a coding agent reads
alongside CLAUDE.md to learn what upstream contract changes affect the repo it is
working in.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ids import parse_id


@dataclass(frozen=True)
class UpstreamEntry:
    """One line item in an UPSTREAM.md / impact digest.

    `producer_service` + `local` name the changed contract; `summary` is the
    one-liner; `origin`/`latest` are the attribution SHAs; `consume_file`/
    `consume_line` cite where the reading repo depends on it (may be None for a
    manifest-only edge with no code location).
    """

    entity_id: str
    producer_service: str
    local: str
    summary: str
    origin: str | None
    latest: str | None
    breaking: bool
    change_kind: str
    consume_file: str | None = None
    consume_line: int | None = None
    event_ts: str | None = None  # ISO-8601 timestamp of the surfaced event, for recency ranking


def _short(sha: str | None) -> str:
    return sha[:7] if sha else "?"


def _entry_line(e: UpstreamEntry) -> str:
    """Render one entry as a two-line markdown bullet (headline + provenance)."""
    head = f"- **{e.producer_service} `{e.local}`** — {e.summary or '(no summary)'}"
    prov = f"  Introduced: {_short(e.origin)} · Latest: {_short(e.latest)}"
    if e.consume_file:
        loc = f"{e.consume_file}:{e.consume_line}" if e.consume_line else e.consume_file
        prov += f" · You consume this in `{loc}`."
    return f"{head}\n{prov}"


def render_upstream(service: str, entries: list[UpstreamEntry], last_synced: str | None) -> str:
    """Render `.orgcontext/UPSTREAM.md` for `service`.

    Entries are split into a Breaking section (⚠️) and a Changed section. The
    caller supplies them already ranked; we preserve that order within each
    section. An empty result still renders a header + "No upstream changes" so a
    reader can tell sync ran and found nothing (vs. never running).
    """
    lines = [f"# Upstream changes that affect {service}", ""]
    lines.append(f"_Last synced: {_short(last_synced)}_")
    lines.append("")

    breaking = [e for e in entries if e.breaking]
    changed = [e for e in entries if not e.breaking]

    if not entries:
        lines.append("_No upstream changes affect this service._")
        return "\n".join(lines) + "\n"

    if breaking:
        lines.append("## ⚠️ Breaking")
        for e in breaking:
            lines.append(_entry_line(e))
        lines.append("")
    if changed:
        lines.append("## Changed")
        for e in changed:
            lines.append(_entry_line(e))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_owned(service: str, owned: list[tuple[str, str, str | None]]) -> str:
    """Render `.orgcontext/OWNED.md`: the contracts this service owns + current
    state. `owned` is a list of (entity_id, signature, state_summary). This is the
    file OTHER services read to understand what this one provides."""
    lines = [f"# Contracts owned by {service}", ""]
    if not owned:
        lines.append("_This service owns no tracked contracts._")
        return "\n".join(lines) + "\n"
    for entity_id, signature, state_summary in owned:
        _, _, local = parse_id(entity_id)
        lines.append(f"## `{local}`")
        lines.append(f"- Signature: `{signature}`")
        if state_summary:
            lines.append("- History:")
            for hist in state_summary.splitlines():
                lines.append(f"    {hist}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_impact(service: str, groups: dict[str, list[UpstreamEntry]]) -> str:
    """Render the `impact` digest: affected services, each with the changed
    contracts. `groups` maps an affected consuming service -> its entries. Empty
    groups mean "nobody affected" — rendered explicitly so silence is legible."""
    lines = [f"# Downstream impact of changes in {service}", ""]
    if not groups:
        lines.append("_No downstream services are affected by this diff._")
        return "\n".join(lines) + "\n"
    for consumer in sorted(groups):
        lines.append(f"## {consumer}")
        for e in groups[consumer]:
            lines.append(_entry_line(e))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
