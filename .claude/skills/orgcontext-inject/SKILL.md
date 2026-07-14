---
name: orgcontext-inject
description: Inject upstream contract-change context before writing or reviewing code. Use at the start of work in any repo that has a .orgcontext/UPSTREAM.md — it lists breaking/changed upstream contracts (endpoints, schemas, topics) this repo consumes, so you know what changed in your dependencies before you touch code.
---

# OrgContext upstream-context injection

This repo may participate in an OrgContext graph that tracks contract-level changes
across the org's services. When it does, `orgctx sync` writes a
`.orgcontext/UPSTREAM.md` file at the repo root summarizing **the upstream contract
changes that affect this repo** — a breaking rename in an endpoint you call, a
field removed from a schema you import, a topic whose shape changed.

## What to do

1. **Read `.orgcontext/UPSTREAM.md`** at the repo root if it exists. Treat it as
   authoritative context about your dependencies, alongside `CLAUDE.md`. If it does
   not exist, this repo is not wired into OrgContext — skip silently.

2. **Refresh it first if it may be stale.** If the repo has an `orgctx` CLI
   available and a central graph, run `orgctx sync <this-repo> --service <name>`
   to regenerate the file before reading. (In a hook-wired repo this happens
   automatically on checkout/commit; only do it manually if the file is missing or
   old.)

3. **Surface the relevant items before writing code.** When your task touches a
   file that `UPSTREAM.md` cites (the "You consume this in `path:line`" pointers),
   call out the upstream change to the user and account for it — e.g. if
   `POST /v1/charge` renamed its response field `amount -> amount_cents`, update the
   call site rather than reproducing the old field name.

4. **In review mode**, run `orgctx impact <repo> --diff <range> --service <name>`
   on the PR to get the downstream-impact digest (who this change may break) and
   include it in the review.

## Why this matters

The value lands at the moment you write or review a diff: OrgContext tells you
"your dependency changed its contract, here's what and where you use it" exactly
when that information is actionable — before you ship code against a stale
assumption. Silence means nothing upstream affects your current work.
