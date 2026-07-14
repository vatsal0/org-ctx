"""Per-subcommand argument dataclasses.

Per CLAUDE.md: experiment/CLI scripts should "accept arbitrary command line
arguments and match these against fields in an Arguments dataclass. No middleman
yaml config." So each subcommand gets one `@dataclass` here, and cli.py contains a
single generic reflector that turns `--flag value` tokens into an instance of the
right dataclass. Adding a flag is therefore just adding a field — no argparse
boilerplate to touch.

The `Subcommand` enum is the closed set of verbs the CLI exposes (CLAUDE.md's
"string choice -> enum" rule applied to the command name itself). `SUBCOMMAND_ARGS`
maps each verb to its dataclass so __main__ can dispatch generically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Subcommand(str, Enum):
    """The six verbs from plan.md §5, plus their one-line intent."""

    EXTRACT = "extract"            # parse repo -> entities + outbound edges
    INGEST = "ingest"             # walk git log -> change events on entities
    COMPACT = "compact"           # apply compression policy -> state_summary
    IMPACT = "impact"             # touched entities -> downstream/upstream digest
    SYNC = "sync"                 # regenerate a repo's .orgcontext/UPSTREAM.md
    RENDER_NOTES = "render-notes"  # scoped release notes (byproduct)


# The shared graph store location. A single default keeps every subcommand
# pointing at the same central/ store without the user repeating it. Overridable
# per-invocation with --central.
DEFAULT_CENTRAL = "central"


@dataclass
class ExtractArgs:
    """`orgctx extract <repo>` — build the entity + edge inventory for one repo.

    `repo` is the path to the service's working tree. `service` names the entity
    namespace; if omitted we default it to the repo directory name so ids stay
    stable and human-readable.
    """

    repo: str
    service: str | None = None
    central: str = DEFAULT_CENTRAL
    verbose: bool = False


@dataclass
class IngestArgs:
    """`orgctx ingest <repo> <commit_range>` — map commit diffs to change events.

    `commit_range` is any git log range ("A..B", "HEAD"). We default to the full
    history so a first ingest over the seeded dummy-org "just works".
    """

    repo: str
    commit_range: str = "HEAD"
    service: str | None = None
    central: str = DEFAULT_CENTRAL
    verbose: bool = False


@dataclass
class CompactArgs:
    """`orgctx compact [target]` — recompact state_summary under the policy.

    `target` is an entity id or the sentinel "all". `policy` points at the tunable
    compression policy file (defaults to the packaged policy.yaml).
    """

    target: str = "all"
    policy: str | None = None  # None -> packaged orgctx/policy.yaml
    central: str = DEFAULT_CENTRAL
    verbose: bool = False


@dataclass
class ImpactArgs:
    """`orgctx impact <repo> [--diff <ref>]` — the "affects you" digest.

    `diff` is a ref/range whose changed files define the "touched" set; default
    HEAD (the last commit). `hops` overrides the policy's transitive-impact depth.
    """

    repo: str
    diff: str = "HEAD"
    service: str | None = None
    hops: int | None = None  # None -> use policy.impact_hops
    central: str = DEFAULT_CENTRAL
    policy: str | None = None
    verbose: bool = False


@dataclass
class SyncArgs:
    """`orgctx sync <repo>` — write .orgcontext/UPSTREAM.md + OWNED.md."""

    repo: str
    service: str | None = None
    central: str = DEFAULT_CENTRAL
    verbose: bool = False


@dataclass
class RenderNotesArgs:
    """`orgctx render-notes [--since <ref>]` — scoped release notes.

    `since` bounds the changelog window; default None means "everything on record".
    `service` optionally scopes the notes to a single affected service. `repo` is
    the org monorepo whose history bounds the `--since` window (only consulted when
    `since` is set); it defaults to the demo's dummy-org.
    """

    since: str | None = None
    service: str | None = None
    repo: str = "dummy-org"
    central: str = DEFAULT_CENTRAL
    verbose: bool = False


# Dispatch table: which dataclass backs each subcommand. __main__ uses this both
# to build the parser and to route to the handler.
SUBCOMMAND_ARGS: dict[Subcommand, type] = {
    Subcommand.EXTRACT: ExtractArgs,
    Subcommand.INGEST: IngestArgs,
    Subcommand.COMPACT: CompactArgs,
    Subcommand.IMPACT: ImpactArgs,
    Subcommand.SYNC: SyncArgs,
    Subcommand.RENDER_NOTES: RenderNotesArgs,
}

# Which fields of each dataclass are *positional* (consumed in order before any
# --flags). Everything else is a keyword flag. Keeping this explicit — rather than
# inferring from "fields without defaults" — means a positional with a default
# (like IngestArgs.commit_range) still parses positionally.
POSITIONAL_FIELDS: dict[Subcommand, list[str]] = {
    Subcommand.EXTRACT: ["repo"],
    Subcommand.INGEST: ["repo", "commit_range"],
    Subcommand.COMPACT: ["target"],
    Subcommand.IMPACT: ["repo"],
    Subcommand.SYNC: ["repo"],
    Subcommand.RENDER_NOTES: [],
}
