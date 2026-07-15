# OrgContext → Product: Verdict, Moats, Indexing Architecture, and the Demo Plan

## Context

The founding story: a maintainer of a 3-deep microservice chain returns from 2 weeks out to a massive git log and a production break — AI-generated code has raised change volume past what release notes, code review, and tribal knowledge were built for. Two needs: (1) living, compressed service documentation + "what actually shipped," (2) pre-merge impact awareness — especially for coding agents, which grep and simply miss cross-repo consumers. The existing repo (`orgctx` CLI: entity timelines, interface edges, compression policy, eval harness) is the working skeleton. Constraints: no internal employer names anywhere; validation must come from **public repos**, no employer pilot.

## Part 1 — Verdict

**Yes, build it. Don't pivot.**

1. The problem is new, growing, and has no incumbent owner. Nobody maintains a *persistent cross-service contract graph with a change timeline*.
2. **Agents are the forcing function.** Humans have hunches; agents have grep. As agent-written code grows, a machine-readable interface graph injected at write time becomes required infrastructure.
3. The existing eval harness already de-risks the two hardest claims (compression plateaus; impact is precise and silent for the unaffected) — rare at this stage.

**Should we raise?** Not yet — raise from evidence, not story:
- **Now → +8–10 weeks (bootstrap):** polished demo + a public **validation matrix**: OrgContext producing verifiably correct output on 3 different repo *shapes* (below). That replaces the employer pilot as proof.
- **Then pre-seed** ($500k–1.5M) on: working product, multi-repo-shape evidence, the agent-infrastructure narrative, vendor neutrality (works with GitHub, GitLab, Bitbucket — see GitHub Next below).

**Kill criteria** (week 10): if edge recall on the polyglot public benchmark can't reach ~0.85 with heuristics+manifests+LLM-fallback, or the historical-break replay (below) surfaces mostly noise, false silence is fatal for this shape — pivot to the catch-up-digest/what-shipped product alone (standalone value, doesn't depend on edge recall).

## Part 2 — Product shape and moats

One substrate, four surfaces (priority order):
1. **Agent context injection** — `sync` → UPSTREAM.md + an MCP server. The wedge.
2. **Pre-merge impact** — `impact` as pre-commit hook + CI check comment.
3. **Catch-up digest** — new `orgctx digest --since` ("I was out 2 weeks" page). Strongest demo beat, thin render over existing timelines.
4. **Living service docs / what-shipped feed** — OWNED.md + entity pages + affected-scope release notes replacing ignored commit feeds.

**Positioning:** "The forge is the artifact store; OrgContext is the day-to-day interaction layer — the org's contract memory that humans and agents read before touching code." Open, git-native, **forge-neutral**.

### Moats (ranked)
1. **Accumulated graph + timelines** — a system of record; switching means losing the org's memory. Grows more defensible every month installed.
2. **The tuned compression policy + eval loop** — "what earns a permanent line" tuned against thousands of real commits is the research-hard part incumbents get wrong first try; the plateau/no-lost-breaking eval harness is the tuning loop they don't have.
3. **Forge and vendor neutrality** — GitHub Next has prototyped adjacent ideas, but anything GitHub ships is GitHub-only. Orgs on GitLab/Bitbucket/self-hosted (a huge slice of the enterprise) are structurally unaddressable by them. Being git-native (not forge-API-native) makes every forge a supported target for free.
4. **Workflow embedding** — once every agent session reads UPSTREAM.md and every PR runs `impact`, removal breaks habits.

### Competitive map

| Player | What they do | Why we're different |
|---|---|---|
| **GitHub Next** (prototypes in this space) | Repo-intelligence experiments | GitHub-only by construction; we're git-native and forge-neutral (GitLab, Bitbucket, self-hosted). They also ship prototypes, not products, and single-repo bias is structural for them. |
| CodeRabbit / Greptile / Qodo | PR-time review, some cross-repo | Review-moment only, closed, no persistent timeline; human-reviewer product. We feed agents pre-write and persist the graph. |
| Sourcegraph (SCIP) | Code search/nav graph | Search-time symbol precision, not change-time contract semantics; no timeline/compression. Closest infra analog — and a *component* we can consume (see Part 3). |
| Swimm / living docs | Human prose docs | No machine-readable timeline, no impact traversal. |
| git-cliff / commit feeds | Commit-title changelogs | The ignored-feed problem itself; no affected-scope routing. |
| Graphiti/Zep, Letta, Mem0 | Agent memory graphs | LLM-extracted facts from conversation, generic ontology; ours is deterministically derived from code with provenance (see Part 3). |
| **Graphify** (graphify.net, YC S26, 87k★) | Tree-sitter AST → JSON knowledge graph (calls/imports/inherits/mixes_in) per repo, MCP + slash-command skill across 15+ agent platforms incl. Claude Code/Cursor/Copilot | **The closest real threat, not hypothetical.** Overlaps hard on surface #1 (agent context injection) and has already shipped the multi-platform skill distribution we planned for Phase C. But it is a **structural**, single-snapshot graph — full call/import graph (broad, noisier), no git history/timeline, no change-event log, no cross-service contract semantics, no breaking-change classification, no compression policy. It answers "what does this code call/import" at a point in time; we answer "what changed in a contract you depend on, and did it break you." See differentiation note below — this is the comparison the pitch must open with, not bury. |

## Part 3 — Indexing architecture: what's commodity, what's novel

Direct answer to "merkle trees, LiveGraph, Graphify, Graphiti, LSPs — what's our novel change?"

**The storage/graph layer is commodity and should stay boring.** SQLite (current) is correct up to ~10⁶ entities/events; the novelty is not in graph storage, and adopting a graph DB now would be résumé-driven engineering. The novelty lives in four specific mechanisms, all already present in embryo in this repo:

1. **A contract-level ontology, not a symbol graph.** LSP/SCIP indexes answer "who references symbol X" — precise, but intra-language/intra-repo, and drowning in true-but-irrelevant edges. Our entity kinds (route, schema, export, topic, config) + interface-only edges are a deliberately *lossy, high-signal* projection of the codebase that maps 1:1 to "will this break someone." No indexing tool ships this ontology.
2. **Extract-at-both-revisions semantic diffing** (`ingest.py:96`). Instead of mapping diff hunks to symbols (line-drift hell), we extract the entity table at parent and child and diff the *tables* — added/removed/signature-changed at contract granularity. This is the mechanism that makes a Pydantic field rename register as a signature change on the HTTP route that returns it. Genuinely uncommon; the closest analog is API-diff tools (e.g. buf breaking for protobuf), which are single-format and timeline-less.
3. **The temporal layer with a never-drop-breaking guarantee.** Graphiti (getzep/graphiti, 28.7k★, Neo4j/FalkorDB/Neptune-backed, Zep-backed) proves bi-temporal graphs — facts with validity windows, invalidated not deleted — are already commodity infrastructure. But its facts come from LLM extraction over text/conversations/enterprise data, with no ground truth to check against. Ours is *deterministically derived from git* with commit provenance, and the compression policy carries a hard invariant (breaking changes are never compacted away — `compact.py:85`). The novelty isn't "we built a temporal graph" — Graphiti shows that's solved — it's "our facts are derived, not inferred." "Provable contract memory" vs. "plausible memory" is the differentiating sentence. If SQLite ever becomes a real bottleneck for the temporal layer (unlikely before Phase C), adopting Graphiti's engine as the storage backend is more credible than building bi-temporal semantics from scratch — same logic as the Graphify/SCIP pluggable-backend idea below.
4. **Delivery at the agent write-moment** (UPSTREAM.md / MCP), not at search or review time.

**How each named technology actually fits:**

- **Merkle trees:** git *is* a merkle tree — use it rather than building one. Concretely: cache extraction results keyed by git blob/tree OIDs, so re-extraction after a commit only parses changed files (`git diff-tree` gives them directly; `gitutil.py` already walks this). This turns full-org re-extraction into O(changed files) incremental updates. Adopt in Phase B; it's an optimization, not a differentiator.
- **LSP / SCIP:** don't build on LSP servers (stateful, slow, per-editor). But **SCIP indexes** (Sourcegraph's protobuf format, indexers exist for ~10 languages) are a strong *optional backend* for the import/export edge class: parse `.scip` files to resolve cross-file symbol references precisely instead of our name-matching `ProducerIndex` (`extract.py:509`). Design the extractor as pluggable backends: `heuristic-ast` (now) → `tree-sitter` (multi-language syntax) → `scip` (symbol precision where an indexer exists) → `llm` (the semantic residue: HTTP URLs, queue topics, computed strings — the edges *no* static tool can see, which is exactly where our value concentrates). Each backend emits the same `Edge` with its own `EdgeSource`/confidence; the existing confidence-upgrade merge (`db.py:214`) already composes them.
- **Tree-sitter:** yes, as the multi-language syntax substrate replacing Python's `ast` when we add language #2. Extraction queries per (language, framework) pattern; keep `extract_entities`/`extract_edges` signatures.
- **Graphiti / LiveGraph / Graphify:** wrong layer. Graphiti solves LLM-fact temporal graphs (our provenance story is stronger for code); LiveGraph-class systems solve concurrent transactional graph workloads we don't have (single-writer batch ingest). Revisit storage only if/when a hosted multi-tenant version needs concurrent writers — and even then Postgres, not a graph DB, is the likely answer.

**The one-line novelty claim for the pitch:** *a deterministic, git-derived, temporally-compressed contract graph with provenance — delivered to coding agents before they write.* Every existing tool has at most two of those five properties.

### Head-to-head: Graphify, specifically

Graphify is the single biggest reason to sharpen rather than restate the plan. It is well-funded, already distributed as a multi-agent-platform skill, and structurally adjacent — a team evaluating "should we use OrgContext" will have Graphify in the room. The honest comparison:

| Property | Graphify | OrgContext |
|---|---|---|
| Graph scope | Full structural graph (calls/imports/inherits) — broad, high-recall, noisier | Interface-only (routes/schemas/exports/topics/config) — narrow, lossy by design, high-signal |
| Temporal | **None** — a snapshot, incrementally re-extracted on change, old state discarded | Change-event timeline per entity; origin + latest always retained |
| Cross-*service* semantics | Cross-file within one indexed folder; no producer/consumer/breaking-change model | First-class: consumes/imports/depends_on_schema/subscribes edges, breaking-change classification |
| Compression | None needed (no history to compress) | The hard, tuned part — never-drop-breaking guarantee, plateau under real churn |
| Delivery | MCP + slash commands, broad agent-platform coverage (ahead of us today) | UPSTREAM.md + planned MCP (Phase C) — behind on distribution, must catch up |
| Git-native | Git hooks for rebuild triggers only; not git-derived data | Every fact traces to a commit SHA; the timeline *is* git history |

**Net:** Graphify answers "what does this code do and touch, right now." OrgContext answers "what changed in a contract I depend on, when, and did it break me." These are complementary questions, not the same product — but the danger is a buyer conflating them because Graphify got to agent-context distribution first. Two implications for the plan:
1. **Don't compete on breadth or distribution speed** — Graphify's 36-language tree-sitter coverage and 15-platform skill footprint took real capital; racing there is a losing move. Phase C's MCP server should ship *fast* (weeks, not a differentiator to polish) purely to close the credibility gap, not to out-feature them.
2. **Consider integration over rivalry as a hedge**: Graphify's JSON graph could be a candidate *structural backend* (replacing/supplementing our AST layer) the same way SCIP was scoped in Part 3 — we'd add the temporal/contract/compression layer on top rather than duplicate tree-sitter integration. Worth a technical spike in Phase B if their JSON schema is stable and licensable; do not build this dependency before validating it's wanted.

## Part 4 — Build roadmap

### Phase A (weeks 1–3): Demo hardening + story arc — neutral names throughout

**A1.** Reshape `dummy-org` into a 3-deep chain with neutral names (e.g. `gateway-svc → billing-svc → ledger-svc` + `contracts`) so transitive impact is testable. Update `expected_edges.yaml`, `eval/labels.yaml`, seed scripts; keep an amount→amount_cents-style break in the middle service.

**A2. Transitive impact.** `compute_impact` currently discards its hops parameter (`impact.py:99`, `del hops`; `policy.yaml` knob exists). Implement BFS over `Graph.inbound_edges`: hop 1 = entity-level (current behavior); hops ≥2 expand at *service* granularity ("transitively affected via billing-svc"), confidence decayed one level per hop, capped at `policy.impact_hops` (default 2). Add transitive rows to `labels.yaml`; assert propagation stops at cap.

**A3. `orgctx digest --since <ref|date> [--service S] [--author A]`.** New `orgctx/digest.py` + wiring in `args.py`/`cli.py`. Reuse `sync.compute_upstream_entries` window logic widened to all events (owned + consumed), grouped: (1) ⚠️ breaking on consumed entities (reuse `render_upstream` entry shape), (2) changes to entities you own made by others (`event.author != --author`) — the "system changed under me" section, (3) folded internal-churn one-liner (fold lines already computed by `compact.py:44`). No new LLM calls on the mock path.

**A4. Demo walkthrough**: scripted 3 beats — (1) digest after "2 weeks out," (2) Claude Code in the downstream service reading UPSTREAM.md via the existing `orgcontext-inject` skill and avoiding the break, (3) `impact --diff` on a mid-chain PR showing 1-hop + 2-hop blast radius with the unaffected service silent. Plus an HTML artifact walkthrough for sharing.

### Phase B (weeks 3–7): Public-repo validation matrix (replaces the pilot)

Three repo shapes, three claims proven:

| Target | Shape | What it proves | Method |
|---|---|---|---|
| **GoogleCloudPlatform/microservices-demo** ("Online Boutique") | Polyglot polyrepo-style monorepo, 11 services, real gRPC/HTTP contracts | Cross-service edges + impact on real service boundaries | Hand-label the true edge set (it's documented); measure edge recall (heuristics on the Python services, manifests + LLM-fallback for the rest) |
| **A high-churn Python monorepo** (e.g. Home Assistant core — hundreds of loosely-coupled integrations) or vLLM | Scale + compression | `state_size_series` plateaus over ≥500–1000 real commits; ingest throughput acceptable | Run the existing eval harness pointed at real history |
| **Historical-break replay** on a repo with documented breaking changes (e.g. a versioned API service; pick from changelogs) | The money claim | Checkout N commits before a known breaking release, build graph, replay forward: does `impact` flag the break at the introducing commit, and stay silent elsewhere? | Precision/recall vs. the changelog's own breaking-change list |

Enablers to build in this phase:
- **B1. Polyrepo mode:** `orgcontext.yaml` at the central store (`repos: [{path, service}]`); `extract_org` loops roots with fixed service per root; `ingest_range` takes root+service (already single-repo — just needs the service override). No schema change; ids already encode service.
- **B2. Extraction hardening:** configurable `client_names` per org (fixed set at `extract.py:437` is the main real-code recall gap); emit `DEPENDS_ON_CONFIG` consumer edges (kind exists, no extractor); **LLM edge-inference fallback** only over zero-outbound-edge files that mention known producer services (`EdgeSource.LLM` enum exists) — LOW confidence, upgraded later by heuristic hits via `db.py:214`.
- **B3. Merkle-style incremental extraction:** cache per-file extraction keyed by git blob OID; re-extract only `git diff-tree` files. Needed for the scale target.
- **B4. Delivery hooks:** `orgctx hooks install` (post-merge/post-checkout) + CI snippet posting `impact --diff` as a PR comment via `gh`/`glab` (forge-neutral from day one).

### Phase C (weeks 7–10, overlapping): moat + raise material
- **MCP server** (`orgctx serve-mcp`): `get_upstream_context(service)`, `check_impact(diff)`, `query_entity(id)`, `digest(since)` — thin layer over existing pure functions; makes the graph agent-vendor-neutral.
- **Tree-sitter backend** for language #2 (whichever the validation matrix demands — likely Go for Online Boutique).
- **Adversarial eval org** in CI: indirect client wrapper, computed topic string, squash merge — forces false-silence failures into the eval where the LLM fallback must earn its recall.
- Pitch deck: the Part 3 novelty claim + validation-matrix numbers.

### Deferred (roadmap slide only)
Observability integration (logs→code, traces), forge apps/marketplace, web dashboard, hosted multi-tenant (and any storage migration), field-read schema edges, SCIP backend (design the plug point in Phase B, implement post-raise).

## Part 5 — Hard-problem register

| Risk | Severity | De-risk |
|---|---|---|
| False silence (missed edge → wrong "you're safe") | Fatal if unmanaged | Manifest floor + configurable client names + LLM fallback on zero-edge files + adversarial eval + historical-break replay; always report per-service *coverage confidence*, never bare silence |
| Compression at real churn | High | Plateau eval over ≥500 real commits (Phase B target 2) |
| Manifest rot | Medium | Heuristics upgrade manifests (`db.py:214`); add `orgctx doctor` warning for manifest edges unconfirmed after N commits |
| GitHub absorption | Medium | Forge neutrality (GitLab/Bitbucket unaddressable by GitHub Next) + accumulated-timeline moat + openness |
| **Graphify (or similar) adds a temporal/contract layer** | High | This is the real existential risk, higher than GitHub absorption — a funded, distributed incumbent bolting git-history + interface semantics onto an existing 87k-star install base is more likely than GitHub building this from scratch. Mitigate by moving fast on the compression-policy moat (Part 2 #2) — that's the part that takes real tuning time even for a well-resourced team — and by keeping the option to integrate rather than compete (see Part 3 head-to-head) |
| Adoption friction | Medium | Single-PR value; hooks auto-install; useful before org-wide adoption |
| LLM cost | Low | Haiku for summaries, Opus only for classification + edge fallback; batchable |

## Verification

- **Phase A:** `pytest tests/ -q` green including new scenario D (transitive stops at hop cap) and E (digest sections correct); `python -m eval.run_eval` — edge recall 1.0 on the reshaped org, plateau holds; run the 3-beat demo script end-to-end and inspect UPSTREAM.md + digest output by eye.
- **Phase B:** edge recall ≥ 0.85 on the hand-labeled Online Boutique oracle; plateau over ≥500 real commits on the scale target; historical-break replay flags the documented break at its introducing commit with zero flags on unaffected services.
- Every claim in the pitch traces to a number produced by `eval/` — no vibes.
