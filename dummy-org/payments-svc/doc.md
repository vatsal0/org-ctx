# `payments-svc/` — the charge producer

`app.py` defines two routes:
- `POST /v1/charge` (returns `Charge`) — consumed by orders-svc; its signature
  embeds `Charge`'s fields, so a field rename in contracts makes it a breaking
  signature change.
- `GET /v1/health` — consumed by **nobody**. It exists to receive the demo's
  internal-churn commits, producing `internal` change events on a no-inbound entity
  that the compression policy must drop from `state_summary`.

Consumes `contracts.Charge` (a `depends_on_schema` edge). `manifest.yaml` declares
that dependency (recall floor); the extractor also finds it from the import and
attaches a code location. `scripts/seed-history.sh` adds an `idempotency_key` param
to the charge route (a mid-life signature change) and churns `health`.
