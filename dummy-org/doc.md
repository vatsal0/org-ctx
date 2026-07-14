# `dummy-org/` — the demo monorepo + eval oracle

A tiny monorepo of independent services whose cross-service edges are **known**, so
the tool can be measured against ground truth. It is its own git repo (created by
`scripts/seed-history.sh`); the parent repo `.gitignore`s `dummy-org/.git`.

The point is the **edges**, not the code — every service is a few lines.

## Services

| Service | Produces | Consumes |
|---------|----------|----------|
| `contracts/` | schema models `Charge`, `Order`, `Event` | nothing |
| `payments-svc/` | route `POST /v1/charge` (returns `Charge`); unconsumed `GET /v1/health` | `contracts.Charge` |
| `orders-svc/` | route `POST /v1/orders`; topic `order.created` | payments `/v1/charge` (in `src/pay.py`), `contracts.Order` |
| `notifications-svc/` | — | topic `order.created`, `contracts.Event` (NOT charge) |

`notifications-svc` deliberately depends on *neither* the charge route *nor*
`Charge` — that absence is what the noise-control test checks (it must stay silent
when the charge contract breaks).

## Ground-truth edges (`expected_edges.yaml`)

The five cross-service edges the extractor must recover. Written first (before the
extractor) — it is the eval oracle for the edge-recall metric.

## Scripts (`scripts/`)

- `seed-history.sh` — `git init` + a deterministic commit sequence: scaffold
  (everything `added`), a `/v1/charge` signature change (idempotency key), a
  `Charge.currency` ISO-4217 validation tweak (foldable), then 12 internal-churn
  commits on the unconsumed `GET /v1/health` route (droppable).
- `scenario-break.sh` — one commit renaming `Charge.amount → amount_cents` (in
  contracts and the payments handler), the known breaking change for Scenario A.

Both are deterministic (fixed author + fixed dates) so the eval never depends on
the wall clock. They mutate the working tree; the checked-in service source is the
**clean baseline** they start from — do not commit a post-seed version.

## Generated at runtime (git-ignored)

- `<service>/.orgcontext/UPSTREAM.md` + `OWNED.md` — written by `orgctx sync`.
