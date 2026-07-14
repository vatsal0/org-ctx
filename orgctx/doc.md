# `orgctx/` — the CLI package

This directory is the tool. It implements the six-subcommand `orgctx` CLI over
three primitives — **entities**, **interface edges**, and per-entity **change-event
timelines** — persisted in a small SQLite index (`central/graph.db`) plus markdown.

## The data model (see `models.py`)

- **Entity** — a thing with a contract others depend on: an HTTP route, an exported
  symbol, a schema model/field, a config key, a queue topic. Named by a canonical
  id `"{service}::{tag}:{local}"` (see `ids.py`).
- **Change event** — one entry on an entity's timeline, created per commit that
  touches the entity. Carries a one-line summary, a `change_kind`, a `breaking`
  flag, and a `diff_ref` pointer (never the diff body).
- **Interface edge** — a consumer→producer link (`consumes`, `depends_on_schema`,
  `subscribes`, …). This is the graph impact analysis walks.

All string-choice values are enums (`Kind`, `ChangeKind`, `EdgeKind`, `Confidence`,
`EdgeSource`) per the project convention.

## Files and how they interact

| File | Role |
|------|------|
| `__main__.py` | CLI entry; picks the subcommand (Subcommand enum), parses args, prints the run config, dispatches. |
| `cli.py` | Generic `--flag value` → Arguments-dataclass parser (no argparse boilerplate). |
| `args.py` | One `@dataclass` per subcommand + the Subcommand enum + positional-field map. |
| `models.py` | Enums + row dataclasses (Entity, ChangeEvent, Edge). The shared vocabulary. |
| `ids.py` | Canonical id construction/parsing, `normalize_route`, `slugify`. Load-bearing: producer and consumer sides must agree. |
| `db.py` | SQLite schema + idempotent CRUD (`Graph`). |
| `gitutil.py` | Read-only `git` subprocess wrappers (log, changed files, blob-at-rev, changed-line-ranges). |
| `llm.py` | The single mockable LLM boundary. Rule-based mock by default; Anthropic when `ANTHROPIC_API_KEY` is set. Also the shared token counter. |
| `policy.py` / `policy.yaml` | The tunable compaction + impact policy and its loader. |
| `extract.py` | Static AST extraction of producer entities + consumer edges; manifest seeding. |
| `ingest.py` | Git walk → extract-at-both-revisions diff → change events. |
| `compact.py` | Applies the compression policy → `state_summary` + per-entity markdown files. |
| `impact.py` | Dual-direction "affects you" / "who you break" digest with noise control. |
| `sync.py` | Renders a repo's `.orgcontext/UPSTREAM.md` + `OWNED.md`. |
| `notes.py` | Release notes scoped by affected service. |
| `render.py` | Pure markdown templates shared by sync/impact/notes. |

## Command dataflow

```
extract  (working tree)  ─┐
                          ├─►  central/graph.db  ─►  compact  ─►  entities/*.md
ingest   (git history)  ─┘                        │
                                                   ├─►  sync    ─►  <repo>/.orgcontext/UPSTREAM.md
                                                   ├─►  impact  ─►  digest (review)
                                                   └─►  render-notes ─► release notes
```

Run `extract` before `ingest`: the breaking-change guard in `ingest` consults the
edge graph (built by `extract`) to decide whether a signature change is actionable.

## Key design decisions

- **Extract-at-both-revisions** (ingest): to detect what a commit changed, we
  extract the entity table at the parent and at the commit and diff the two tables
  by id+signature — avoiding fragile line-drift hunk mapping.
- **Route signatures embed response-model fields**: so renaming a schema field
  registers as a *signature change on every route that returns it*, which lets the
  break reach a downstream that consumes the route without importing the schema.
- **Manifest = recall floor, heuristics = confidence + citation**: manifest.yaml
  seeds edge existence; the AST extractors upgrade edges with a code location. This
  makes all-source edge recall 1.0 while still measuring heuristic-only recall.
- **Compression is policy-driven**: `policy.yaml` decides promote/fold/drop and the
  token cap. Internal churn is dropped from the hot `state_summary` (kept in the
  cold event log), which is what makes state size plateau instead of grow.
