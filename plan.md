# OrgContext — Demo Spec

A git-native substrate that tracks **contract-level changes across an org's repos**, maintains a **compressed living history**, and injects **"this upstream change affects you"** context into coding agents and code review at the moment a diff is written.

This document is self-contained. It's written to be handed to Claude Code as a build brief. Build order, file layout, the dummy-repo test harness, eval assertions, and the iteration loop are all below.

---

## 1. The one-paragraph thesis

Summarizing a diff is now free; the value lives entirely in **(a) compression** — deciding what earns a permanent line in a top-level history vs. what folds into a one-liner vs. what gets dropped — and **(b) cross-service relevance** — knowing that repo B consumes repo A's endpoint and surfacing A's breaking change *specifically to B*, at the moment B's agent or reviewer touches the relevant code. Everyone else ships the easy 20% (commit summarizers, ignored release-note bots, diff-only review). This targets the 80%.

---

## 2. Why this framing has structural advantages

The design rests on three primitives: **entities**, an **interface graph**, and **entity timelines**. The advantages fall out of that choice.

1. **Entity-timelines sidestep the "which commit do I point at?" problem.** Don't attribute a change to a single commit. Attach every change to the *entity* it touched (an endpoint, an exported symbol, a schema field). "What affects your diff" becomes "what's the recent history of the entities your diff depends on" — and you can surface both the **originating** event and the **latest** mutation because both live on that entity's timeline. No forced single answer.

2. **Interface edges, not a full call graph.** A syntactic call graph (A calls B) drowns you in true-but-irrelevant edges and misses the ones that matter (B hits A's HTTP endpoint, reads a queue A writes, depends on a shared schema field). Restricting edges to **interfaces** — routes, schemas, exported package symbols, shared config, queue topics — is a narrower graph with far higher signal, and it maps directly to "will my change break something downstream."

3. **Compression has a principled promotion rule.** Contract/breaking changes are first-class and earn permanent state; internal churn folds into a one-liner or is dropped. This is the mechanism that stops "every commit generates a page → doc is useless." The compression policy *is* the product, not an afterthought.

4. **Value lands at the review/agent moment, where attention already exists.** "Your diff changes endpoint X; services B and C consume X; here's what changed in X's contract" is actionable exactly when someone is looking at the diff. This is demoable on a **single PR** and finesses the cold-start problem — it's useful before the whole org's history exists.

5. **One graph, three products.** The same entity graph powers (i) downstream-impact review, (ii) agent upstream-context injection, and (iii) release notes. That makes it a substrate, not a feature an incumbent absorbs in a sprint.

6. **Git-native + markdown store.** Rides the same "own your context in git" wave as current agent-memory tools, interoperates with `CLAUDE.md` / skills, and avoids lock-in. No cloud required for the demo.

**Where the demo deliberately cheats (name the risk):** the demo tests generate + inject (the easy part). The real product risk is (i) semantic edge extraction for edges heuristics can't see, (ii) the compression policy holding up over thousands of commits, (iii) cold-start at org scale, and (iv) incumbents (Qodo/CodeRabbit cross-repo review, GitHub, the agent vendors) absorbing the slice. The eval harness in §7 is built to make risks (i) and (ii) concrete early.

---

## 3. Competitive context (so the build stays differentiated)

- **Cross-repo review (Qodo, CodeRabbit multi-repo, Greptile):** already flag shared-lib/schema breaking changes to downstream repos at review time. Differentiator to hold: OrgContext is an **open, git-native living history + agent-context substrate**, not a closed review-moment-only product. Don't rebuild their reviewer; feed the agent and the reviewer from a persistent graph.
- **Agent memory (agent-memory, Letta, Mem0):** store facts/notes in git, some with a manual cross-repo "landscape" concept. Differentiator: OrgContext stores a **change-event timeline with provenance and interface-diff impact**, not static notes.
- **Release notes (git-cliff, semantic-release, Doc Holiday):** commit/PR-title level, no cross-service impact. OrgContext produces release notes as a **byproduct** of the graph, scoped by who's affected.
- **Living docs (Swimm):** human-narrative docs, auto-flagged stale. OrgContext produces a **machine-readable timeline for agents**, not prose docs for humans (though it can render prose too).

---

## 4. Core data model

Keep IDs stable and human-readable. Everything is markdown + a small SQLite index for the demo.

### 4.1 Entity

A thing that has a **contract** others can depend on.

```
entity_id           # canonical, stable. e.g. "payments-svc::http:POST /v1/charge"
                    #                          "contracts::schema:Charge.currency"
                    #                          "auth-lib::export:verifyToken"
                    #                          "orders-svc::env:PAYMENTS_BASE_URL"
kind                # http_route | export | schema_field | config_key | queue_topic | pkg_version
service             # owning repo/service
signature           # current shape (route+method+params, fn signature, field type, etc.)
state_summary       # compressed "current truth" about this entity (recompacted, token-capped)
origin_commit       # first commit that introduced it
latest_commit       # most recent commit that mutated it
```

### 4.2 Change event

Appended to an entity's timeline on every touching commit.

```
event_id
entity_id
commit_sha
author
timestamp
change_kind         # added | modified | removed | signature_change | behavior_note | internal
breaking            # bool (signature_change/removed on a consumed entity ⇒ candidate breaking)
summary             # one-line, LLM-generated, human-readable
diff_ref            # pointer to the hunk, not the full diff
```

### 4.3 Interface edge

```
from_entity         # consumer side, e.g. "orders-svc::call:POST /v1/charge"
to_entity           # producer side, e.g. "payments-svc::http:POST /v1/charge"
edge_kind           # consumes | imports | depends_on_schema | depends_on_config | subscribes
confidence          # heuristic=high for explicit, lower for inferred
source              # how it was detected (route-match, import-parse, manifest, llm)
```

### 4.4 Stores (demo)

```
central/graph.db            # SQLite: entities, events, edges
central/entities/*.md       # one markdown file per entity: signature + state_summary + timeline pointers
<each repo>/.orgcontext/
    UPSTREAM.md             # "changes upstream of you that matter" — injected next to CLAUDE.md
    OWNED.md               # entities this repo owns + current state (for other repos to read)
    manifest.yaml          # declared deps/consumed services (bootstraps the graph)
```

`.orgcontext/UPSTREAM.md` is the payload a coding agent reads. `central/` is the shared graph — a plain git repo or a single SQLite file for the demo. No cloud.

---

## 5. The tool: `orgctx` CLI

Language: **TypeScript (Node)** or **Python** — pick one, TS suggested for AST/route parsing ergonomics. Single binary/CLI with subcommands. LLM calls go through the Anthropic API; keep them behind one `llm.ts`/`llm.py` module so they're mockable in tests.

```
orgctx extract  <repo>                 # parse repo → entities + outbound edges → central store
orgctx ingest   <repo> <commit-range>  # walk git log → map diffs to entities → append change events (LLM summarize + classify)
orgctx compact  [entity|all]           # apply compression policy → recompact state_summary
orgctx impact   <repo> [--diff <ref>]  # resolve touched entities → traverse edges → downstream impact digest
orgctx sync     <repo>                  # regenerate that repo's .orgcontext/UPSTREAM.md from central store
orgctx render-notes [--since <ref>]     # release notes scoped by affected service (byproduct)
```

### 5.1 `extract` — build the entity + edge inventory

Heuristic-first. For each entity kind, a cheap deterministic extractor:

- **http_route:** framework route decorators / OpenAPI spec / router registrations (regex + AST).
- **export:** exported symbols from package entry points (`package.json` `main`/`exports`, `__init__.py`).
- **schema_field:** JSON Schema / protobuf / TS type / Pydantic model fields in shared `contracts/`.
- **config_key / env:** env var reads + config file keys.
- **queue_topic:** publish/subscribe string literals (heuristic; low confidence, flag for LLM pass later).

Outbound edges come from the consumer side: an HTTP client call to a known route, an `import` of a known export, a read of a known schema/config. Seed cross-service edges from each repo's `manifest.yaml` so the graph isn't empty on day one.

### 5.2 `ingest` — commits → change events

For a commit range: for each commit, diff → map changed hunks to entities (by file+symbol location) → for each touched entity, create a change event. **One LLM call per touched entity per commit** to (a) write the one-line `summary` and (b) classify `change_kind` + `breaking`. Cheap at 20 commits/week; batch if needed. Update `origin_commit`/`latest_commit`.

### 5.3 `compact` — the hard part, keep it explicit and tunable

Compression policy lives in one config file (`policy.yaml`) so it can be tuned against the eval. Starting rules:

- **Promote to permanent `state_summary`:** `signature_change`, `removed`, `added` on any entity that has ≥1 inbound edge (i.e., someone depends on it). These are the lines that must never be silently dropped.
- **Fold:** `modified` / `behavior_note` → collapse into a single running one-liner on the entity ("v3: added idempotency key; v5: currency now ISO-4217").
- **Drop from hot state:** `internal` churn with no inbound edges. Still retained in the full event log, just not in `state_summary`.
- **Token cap:** `state_summary` per entity ≤ N tokens (start 300). On overflow, LLM-recompact keeping (i) origin, (ii) latest, (iii) every unresolved breaking change; discard the rest to the cold log.
- **Pointers not payloads:** hot state stores commit SHAs, not diffs.

### 5.4 `impact` — the "affects you" digest

Given a PR diff in repo R: resolve touched entities → for each, traverse **inbound** edges to downstream consumers → group by consuming service → emit, per affected service, the changed entity, the nature of the change (from `change_kind`), and both the originating and latest timeline pointers. Suppress services with no edge to any touched entity (this is the noise-control test in §7).

### 5.5 `sync` — write the agent payload

For repo R: gather change events on entities that R **consumes** since R last synced → rank by (breaking > signature_change > modified) then recency → render `.orgcontext/UPSTREAM.md`:

```markdown
# Upstream changes that affect orders-svc
_Last synced: <sha>_

## ⚠️ Breaking
- **payments-svc `POST /v1/charge`** — response field `amount` renamed to `amount_cents` (int).
  Introduced: a1b2c3d · Latest: f4e5d6c · You consume this in `src/pay.ts:42`.

## Changed
- **contracts `Charge.currency`** — now validated as ISO-4217; previously free-form.
  Introduced: 9d8c7b6 · Latest: 9d8c7b6
```

This file is what Claude Code reads alongside `CLAUDE.md`.

---

## 6. Dummy org for the demo

Create a `dummy-org/` monorepo of independent services so cross-service edges are real and ground truth is known.

```
dummy-org/
  contracts/            # shared package: schemas (Charge, Order, Event)
  payments-svc/         # PRODUCES  http:POST /v1/charge ; imports contracts.Charge
  orders-svc/           # CONSUMES  payments POST /v1/charge ; imports contracts.Order
  notifications-svc/    # SUBSCRIBES to "order.created" topic ; imports contracts.Event
  scripts/
    seed-history.sh     # scripted commits that create a known change timeline
    scenario-break.sh   # introduces a KNOWN breaking change for the eval
```

Keep each service tiny (one route file, one client file, one schema import). The point is the **edges**, not the code. Ground-truth edges you should be able to recover:

- `orders-svc::call:POST /v1/charge` → `payments-svc::http:POST /v1/charge` (consumes)
- `orders-svc` → `contracts::schema:Order` (imports)
- `payments-svc` → `contracts::schema:Charge` (imports)
- `notifications-svc` → `contracts::schema:Event` (imports)
- `notifications-svc` → `"order.created"` topic (subscribes)

`seed-history.sh` should produce a timeline with, per entity, at least: one `added` (feature), one later `modified` (bugfix one-liner), and for `/v1/charge` one `signature_change`. This is what proves the origin-vs-latest attribution.

---

## 7. Test harness & eval (build this alongside the CLI, not after)

Three golden scenarios, each with hand-labeled expected output. Wire as assertions; run in CI.

### Scenario A — breaking change propagation
1. Run `extract` + `ingest` over seeded history.
2. Run `scenario-break.sh` (rename `amount` → `amount_cents` in `payments-svc /v1/charge`), commit, `ingest`.
3. `sync orders-svc`. **Assert:** `orders-svc/.orgcontext/UPSTREAM.md` lists the change under Breaking, names the entity, cites the consuming line, and includes **both** origin and latest SHAs.
4. `impact orders-svc --diff HEAD` on a PR that edits `src/pay.ts`. **Assert:** the digest surfaces the payments change.

### Scenario B — attribution (origin vs latest)
For `/v1/charge` (feature commit, later bugfix, later signature change): **assert** the entity timeline retains and surfaces the originating feature commit **and** the latest mutation — not just one.

### Scenario C — compression + noise control
- **Compression:** after ingesting 20+ commits including internal-only churn, **assert** (i) no `internal`, no-inbound-edge change created a line in any `state_summary`, and (ii) every `state_summary` is ≤ token cap, and (iii) no unresolved breaking change was dropped.
- **Noise:** run `impact` for `notifications-svc` after the `/v1/charge` break. Since notifications does **not** consume `/v1/charge`, **assert** it gets **zero** flags. (Silence for the unaffected is as important as signal for the affected.)

### Metrics to log every run
- **Edge recall** vs. the known ground-truth edge set (should approach 1.0 on dummies before you trust heuristics).
- **Impact precision / recall** vs. hand-labeled "affects you" set.
- **Noise ratio:** flags per PR (want low; unaffected services = 0).
- **State size over commits:** total `state_summary` tokens across entities — **must plateau, not grow linearly.** Plot it; a rising line means the compression policy is failing.

Keep an LLM mock (fixed summaries/classifications) for deterministic unit tests; run a small real-LLM pass in a separate, non-blocking CI job.

---

## 8. Claude Code integration

Two touchpoints, both thin:

1. **Context injection (skill/plugin):** a skill that, on session start in a repo, reads `.orgcontext/UPSTREAM.md` and includes it next to `CLAUDE.md`, so the agent knows about upstream contract changes before it writes code. Add a `orgctx sync` call to a `pre-commit`/`post-checkout` hook so `UPSTREAM.md` is fresh.
2. **Review command:** an `orgctx impact` invocation the reviewer agent runs on a PR to post the downstream-impact digest as a review comment. This is the single-PR demo that shows value without org-wide adoption.

Pipeline wiring for the demo:

```
post-commit hook:  orgctx ingest <repo> HEAD~1..HEAD && orgctx compact all && push central/
pre-PR / CI step:  orgctx impact <repo> --diff origin/main...HEAD  → post digest
session start:     orgctx sync <repo>  → .orgcontext/UPSTREAM.md   → read by skill
```

---

## 9. Build order (roadmap to a working demo)

Ship in this sequence; each step is independently demoable.

1. **Dummy org + ground-truth edge list.** `dummy-org/` with the 4 services and `seed-history.sh`. Write the expected-edges file first — it's your eval oracle.
2. **`extract` (heuristic) + edge-recall metric.** Iterate extractors until edge recall ≈ 1.0 on the dummies. *Milestone: the graph matches known truth.*
3. **`ingest` + entity timelines** (LLM summarize/classify behind a mockable module). *Milestone: Scenario B (attribution) passes.*
4. **`sync` → `UPSTREAM.md` + Claude Code skill.** *Milestone: Scenario A passes end-to-end; an agent in `orders-svc` "knows" about the payments break.*
5. **`impact` + noise control.** *Milestone: Scenario C noise assertion passes (notifications stays silent).*
6. **`compact` + `policy.yaml` + state-size plot.** *Milestone: Scenario C compression assertions pass and state size plateaus.*
7. **`render-notes`** (release notes as a byproduct, scoped by affected service). *Milestone: the ignored-Slack-feed problem is replaced by targeted notes.*

Stop-and-evaluate gate after step 6: if state size won't plateau or edge recall on the dummies is poor, that's the signal the real product is hard exactly where predicted — better to learn it here than at org scale.

---

## 10. Iteration loop (after the demo runs green)

1. **Heuristics → semantic edges.** Measure which ground-truth edges the heuristic extractor misses (e.g., a queue topic referenced by a computed string). Add a targeted LLM extraction pass *only* for the missed kinds; re-measure edge recall. Don't LLM-extract what regex already nails.
2. **Tune `policy.yaml` against the state-size plot + "no lost breaking change" assertion.** These two pull against each other; that tension is the actual research.
3. **Tune impact ranking against precision/noise.** Add features (edge confidence, change_kind, recency, distance in the graph) until precision is high without suppressing real breaks.
4. **Scale the dummy org.** Add a 5th service with a transitive dependency (C consumes B consumes A) and assert impact propagates the right number of hops (and stops).
5. **Only then** consider a real repo. Point `extract`/`ingest` at one real service pair you know well; compare `UPSTREAM.md` against your own mental model of what changed. That subjective check is the real-world eval.

---

## 11. Open questions to decide during the build

- **Transitive impact depth:** how many hops of downstream impact to surface before it's noise? (Make it a policy knob; default 1, test 2.)
- **Breaking-change ground truth:** signature/removal is a heuristic for "breaking." Some additive changes still break clients. Leave a `behavior_note` escape hatch and see how often it's needed.
- **Concurrent writers:** two PRs touching the same entity → concurrent writes to `central/entities/*.md`. For the demo, section-level merge or last-write-wins; note it as a real-product problem, don't solve it yet.
- **Squash vs. per-commit:** decide whether `ingest` operates on merge commits or every commit; squashed PRs hide intermediate signature changes.

---

*Scope reminder: this spec builds the demo, which proves generate + inject on known-ground-truth dummies. The compression policy holding at scale and semantic edge extraction are the parts that decide whether this is a weekend demo or a real product — the eval in §7 is designed to surface both early.*
