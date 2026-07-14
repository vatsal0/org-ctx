# `dummy-org/scripts/` — deterministic history generators

- `seed-history.sh` — initializes the dummy-org git repo and lays down a known
  15-commit timeline: scaffold (all entities `added`), a `/v1/charge` signature
  change (idempotency key), a `Charge.currency` ISO-4217 validation tweak (foldable
  modification), then 12 internal-churn commits on the unconsumed `GET /v1/health`
  route (droppable `internal` events). Fixed author + fixed dates → reproducible.
- `scenario-break.sh` — one commit renaming `Charge.amount → amount_cents` in
  contracts and the payments handler: the known breaking change for Scenario A.

Both resolve paths relative to themselves (run from anywhere) and mutate the
working tree. They assume the service source is at its **clean baseline** — the
checked-in state — so re-running from a clean checkout is deterministic.

A `churn_fn` bash helper inserts a comment as the first line of a named function's
body via the AST, guaranteeing the edit lands *inside* that entity's line span (a
true body-touch that ingest classifies as `internal`).
