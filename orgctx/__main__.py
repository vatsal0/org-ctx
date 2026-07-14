"""CLI entry point: `python -m orgctx <subcommand> ...` and the `orgctx` script.

This module is intentionally thin. Its only jobs are:
  1. pick the subcommand (validated against the Subcommand enum),
  2. parse the rest of argv into that subcommand's Arguments dataclass (cli.py),
  3. pretty-print the resolved config (CLAUDE.md rule: ALWAYS log the run config),
  4. dispatch to the handler module.

Handlers live one-per-verb (extract.py, ingest.py, ...) and each exposes a
`run(args)` function taking its dataclass. We import them lazily inside dispatch so
that `orgctx --help` works even before every handler exists, and so a heavy import
in one command never slows another.
"""

from __future__ import annotations

import dataclasses
import sys

from .args import SUBCOMMAND_ARGS, POSITIONAL_FIELDS, Subcommand
from .cli import parse_into


USAGE = """orgctx — git-native contract-change tracking (demo)

Usage: orgctx <subcommand> [args]

Subcommands:
  extract       <repo>                 parse a repo into entities + outbound edges
  ingest        <repo> [commit_range]  walk git log -> change events (default HEAD)
  compact       [target]               apply the compression policy (default: all)
  impact        <repo> [--diff <ref>]  "affects you" downstream/upstream digest
  sync          <repo>                 regenerate .orgcontext/UPSTREAM.md
  render-notes  [--since <ref>]        scoped release notes (byproduct)

Common flags: --central <dir> (graph store, default 'central'), --verbose,
              --service <name> (override the service namespace).
Run any subcommand with no required args to see its error.
"""


def _print_config(sub: Subcommand, args) -> None:
    """Pretty-print the fully-resolved run configuration.

    CLAUDE.md is emphatic: always log the config for each running script. For a CLI
    that means echoing the parsed Arguments so a run is reproducible from its
    stderr alone. We print to stderr so stdout stays clean for machine-readable
    command output (digests, notes)."""
    print(f"[orgctx {sub.value}] config:", file=sys.stderr)
    for f in dataclasses.fields(args):
        print(f"    {f.name} = {getattr(args, f.name)!r}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0

    verb, rest = argv[0], argv[1:]
    try:
        sub = Subcommand(verb)
    except ValueError:
        print(f"unknown subcommand: {verb!r}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    args_cls = SUBCOMMAND_ARGS[sub]
    args = parse_into(args_cls, POSITIONAL_FIELDS[sub], rest)
    _print_config(sub, args)

    # Lazy dispatch: import the handler only when its verb is invoked.
    if sub is Subcommand.EXTRACT:
        from . import extract

        return extract.run(args)
    if sub is Subcommand.INGEST:
        from . import ingest

        return ingest.run(args)
    if sub is Subcommand.COMPACT:
        from . import compact

        return compact.run(args)
    if sub is Subcommand.IMPACT:
        from . import impact

        return impact.run(args)
    if sub is Subcommand.SYNC:
        from . import sync

        return sync.run(args)
    if sub is Subcommand.RENDER_NOTES:
        from . import notes

        return notes.run(args)

    raise AssertionError(f"unhandled subcommand {sub}")  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
