# OrgContext (demo)

A git-native tool that tracks contract-level changes across an org's repos and
injects **"this upstream change affects you"** context into coding agents and code
review at the moment a diff is written. This is the working demo; the full spec is
in [`plan.md`](plan.md) and an overview in [`doc.md`](doc.md).

## Setup (one env, all deps)

```bash
conda env create -f environment.yml
conda activate orgctx
pip install -e .
```

That's it — `git` is assumed on PATH; everything else is in the conda env. The real
Anthropic API is used only if `ANTHROPIC_API_KEY` is set; otherwise a deterministic
rule-based mock runs (so the demo and tests work fully offline).

## Run the eval (build the graph + all metrics + the plateau plot)

```bash
python -m eval.run_eval
```

Expected: edge recall 1.0 (all-source and heuristic-only), impact precision/recall
1.0, `notifications-svc` silent (0 flags), and a long flat run in the compacted
state-size series (the churn plateau) while the naive baseline grows every commit.
The plot lands at `eval/out/state_size.png`.

## Run the golden-scenario tests

```bash
pytest tests/ -q
```

Scenario A (breaking-change propagation), B (origin-vs-latest attribution), and C
(compression + noise control) — all deterministic under the LLM mock.

## Try the end-to-end pipeline by hand

```bash
# 1. Seed a known git history into the dummy org, then introduce the break.
bash dummy-org/scripts/seed-history.sh
bash dummy-org/scripts/scenario-break.sh

# 2. Build the graph: extract entities+edges, ingest history, compact.
python -m orgctx extract dummy-org
python -m orgctx ingest  dummy-org HEAD
python -m orgctx compact all

# 3. The payoff: what upstream changes affect orders-svc?
python -m orgctx sync dummy-org/orders-svc --service orders-svc
cat dummy-org/orders-svc/.orgcontext/UPSTREAM.md
#   -> the amount->amount_cents break, under ⚠️ Breaking, citing src/pay.py,
#      with both the origin and latest SHAs.

# 4. Noise control: notifications-svc consumes neither the route nor Charge.
python -m orgctx impact dummy-org/notifications-svc --service notifications-svc --diff HEAD
#   -> "No changes in this diff affect ... notifications-svc." (0 flags)

# 5. Release notes as a byproduct, scoped by affected service.
python -m orgctx render-notes
```

> After running the seed scripts, the `dummy-org/` working tree is left modified
> (that's how the history is built). `git -C dummy-org stash` or re-checkout the
> clean source to reset.

## Quickstart: try it on your own repo

**Read this first — what's supported today.** This is a demo, and the extractors
are deliberately heuristic and **Python-only**. OrgContext will find real signal in
your code to the extent it looks like the demo:

- **HTTP routes** — FastAPI-style decorators: `@app.post("/path")`, `@router.get(...)`.
- **Schemas** — Pydantic `BaseModel` subclasses (or any class under a `contracts/` dir).
- **HTTP client calls** — `httpx`/`requests`/`client.post(url, ...)` (incl. f-strings).
- **Imports** — `from <service> import Thing` where `Thing` is a known schema/export.
- **Queue topics** — `broker.publish("topic")` / `.subscribe("topic")` string literals.
- **Config** — `os.environ["X"]` / `os.getenv("X")`.

It assumes a **monorepo layout**: services live in subdirectories of one git repo
(like `dummy-org/`), and each entity's *service* is the first path component of its
file. Point the CLI at the **monorepo root**. (Polyrepo — one service per separate
git repo — is a documented follow-up, not wired yet: `ingest` walks a single git
history and `extract` derives the service from the path.)

### 1. Add a `manifest.yaml` to each service directory

Heuristics alone recover a cross-service edge only when *both* sides match a
pattern. The manifest is the reliable floor: it declares dependencies explicitly
(and the heuristics then upgrade them with the exact consuming file/line). Entity
ids have the form `"{service}::{tag}:{local}"` where `tag` ∈
`http | schema | export | topic | env`.

```yaml
# my-svc/manifest.yaml
service: my-svc            # must match the directory name
consumes:
  - { edge_kind: consumes,          target: "payments-svc::http:POST /v1/charge" }
  - { edge_kind: depends_on_schema, target: "contracts::schema:Order" }
subscribes:
  - { target: "orders-svc::topic:order.created" }
publishes:                 # ownership only; the topic entity comes from your .publish() call
  - { target: "my-svc::topic:my.event" }
```

Don't know the exact ids? Run steps 2–3 first with no manifests, then browse
`central/entities/*.md` (written by `compact`) — every discovered entity id is a
file there. Copy the ones you depend on into your manifests and re-run.

### 2. Build the graph over your repo

```bash
conda activate orgctx
cd /path/to/your/monorepo        # the git repo whose subdirs are services

# From the org-ctx checkout, or with `orgctx` on PATH:
orgctx extract .                 # entities + edges (heuristics + manifests)
orgctx ingest  . HEAD            # or a range like origin/main..HEAD
orgctx compact all               # apply the compression policy
```

Use `--central <dir>` to choose where the shared graph store lives (defaults to
`./central`); point every subcommand at the same one.

### 3. See what affects a service, and review a PR

```bash
# The agent payload: upstream contract changes that affect one service
orgctx sync ./my-svc --service my-svc
cat my-svc/.orgcontext/UPSTREAM.md

# Review a PR: what does this diff break downstream / what upstream affects it?
orgctx impact ./my-svc --service my-svc --diff origin/main...HEAD

# Release notes scoped by affected service
orgctx render-notes --since <ref> --repo .
```

### 4. Use real LLM summaries (recommended for real repos)

The default rule-based mock produces mechanical summaries. For readable,
human-quality one-liners, set your key — the same commands then route through the
Anthropic API automatically:

```bash
export ANTHROPIC_API_KEY=sk-...
orgctx ingest . HEAD             # summaries via claude-haiku / classification via claude-opus
```

### 5. Keep `UPSTREAM.md` fresh + wire it into Claude Code

Add `orgctx sync` to a git hook so the payload regenerates as history moves, and
the [`orgcontext-inject` skill](.claude/skills/orgcontext-inject/) will read it at
the start of a session (see [Claude Code integration](#claude-code-integration)
below).

> **If your repo isn't Python / FastAPI-shaped**, extraction will find little on
> its own — but the manifest floor still works: declare your cross-service edges by
> hand and `sync`/`impact`/`render-notes` operate off those. Extending the
> extractors to new languages/frameworks is the natural next step (plan.md §10).

## The six subcommands

| Command | What it does |
|---------|--------------|
| `extract <repo>` | parse the org into entities + interface edges |
| `ingest <repo> [range]` | walk git log → per-entity change events |
| `compact [entity\|all]` | apply the compression policy → `state_summary` |
| `impact <repo> --diff <ref>` | "affects you" / "who you break" digest |
| `sync <repo>` | write `<repo>/.orgcontext/UPSTREAM.md` (the agent payload) |
| `render-notes [--since <ref>]` | release notes scoped by affected service |

## Claude Code integration

The skill in `.claude/skills/orgcontext-inject/` reads `.orgcontext/UPSTREAM.md` at
the start of work in a repo, so an agent knows about upstream contract changes
before it writes code. Wire `orgctx sync` into a `post-checkout`/`pre-commit` hook
to keep the file fresh:

```
# .git/hooks/post-checkout (illustrative)
orgctx sync "$PWD" && true
```
