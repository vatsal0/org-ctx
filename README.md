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
