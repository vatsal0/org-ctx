# OrgContext — top-level documentation

OrgContext is a git-native tool that tracks **contract-level changes across an
org's repos**, keeps a **compressed living history** per interface entity, and
injects **"this upstream change affects you"** context into coding agents and code
review at the moment a diff is written. This repo is the working demo of that idea
(build spec: `plan.md`).

## The thesis in one line

Summarizing a diff is free; the value is in **compression** (what earns a permanent
line in an entity's history vs. folds vs. drops) and **cross-service relevance**
(knowing repo B consumes repo A's endpoint and surfacing A's breaking change
*specifically to B*, when B's agent or reviewer touches the relevant code).

## Repository layout

```
orgctx/       the `orgctx` CLI package (the tool). See orgctx/doc.md.
central/       the shared graph store (graph.db + entities/*.md), generated. See central/doc.md.
dummy-org/     the demo monorepo + eval oracle. See dummy-org/doc.md.
eval/          metrics, build harness, and the plateau plot. See eval/doc.md.
tests/         the golden-scenario assertions. See tests/doc.md.
.claude/skills/orgcontext-inject/   the Claude Code context-injection skill.
environment.yml, pyproject.toml     conda + editable-install setup.
```

## The three primitives

1. **Entities** — things with a contract others depend on (routes, exports, schema
   models/fields, config keys, queue topics), each with a stable id
   `"{service}::{tag}:{local}"`.
2. **Interface edges** — consumer→producer links restricted to *interfaces* (not a
   full call graph), which is a narrower, higher-signal graph for "will my change
   break something downstream".
3. **Change-event timelines** — every touching commit appends an event to the
   entity it touched, so "what affects your diff" becomes "what's the recent history
   of the entities your diff depends on", with both origin and latest surfaced.

## The pipeline (six subcommands)

```
extract  parse repos → entities + edges          (heuristic AST + manifest seeding)
ingest   git log → change events per entity       (extract-at-both-revisions diff)
compact  compression policy → state_summary       (promote / fold / drop, token cap)
impact   touched entities → who's affected         (dual-direction, noise-controlled)
sync     → <repo>/.orgcontext/UPSTREAM.md          (the agent payload)
render-notes  release notes scoped by affected service
```

## How the demo proves itself

The eval (`eval/`) and tests (`tests/`) run the whole pipeline over `dummy-org`,
whose cross-service edges are known, and assert the three golden scenarios:
breaking-change propagation (A), origin-vs-latest attribution (B), and compression
+ noise control (C). See `README.md` for the commands.

## Where the demo deliberately cheats (named risks)

- Schema **field-read** edges (e.g. `charge.currency` access) are out of scope;
  field changes propagate at *model* granularity via imports + producer-side field
  events. A real gap, noted.
- The token cap uses `tiktoken` as an offline proxy, not Claude's tokenizer — the
  eval gates on the plateau *shape*, not absolute token counts.
- `impact` is dual-direction (owned vs. referenced) — the one genuine ambiguity in
  the spec, resolved here.
- Concurrent writers are not handled (SQLite single-writer, last-write-wins).
- Dynamic queue-topic strings and squash-merge ingest are known heuristic misses,
  exposed as knobs rather than solved.
