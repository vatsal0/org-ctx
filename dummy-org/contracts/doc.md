# `contracts/` — shared schemas (pure producer)

Owns the org's shared Pydantic models: `Charge`, `Order`, `Event` (`schemas.py`),
re-exported from `__init__.py`. Consumes nothing.

These are the highest-leverage entities in the demo: because a route's signature
embeds its response model's fields, renaming `Charge.amount` here propagates as a
*signature change on the payments route*, reaching orders-svc downstream.

`manifest.yaml` declares the service identity with empty dependency lists (it is a
pure producer). The `Charge.amount → amount_cents` rename is applied by
`scripts/scenario-break.sh`; the checked-in file is the clean baseline.
