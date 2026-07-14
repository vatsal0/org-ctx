"""payments-svc: owns the POST /v1/charge endpoint.

This is a *producer*. It defines the charge route whose response is a `Charge` from
the shared contracts package. Two contract entities matter here:
  - the route `payments-svc::http:POST /v1/charge` (with Charge's fields embedded
    in its signature), and
  - the depends_on_schema edge to `contracts::schema:Charge` (from the import).

orders-svc consumes the charge route over HTTP, so any signature change here —
including one induced by renaming a field inside Charge — must reach orders-svc.

It also defines a `GET /v1/health` route that NOBODY consumes. That unconsumed
route is where the demo's internal-churn commits land: churn on it produces
`internal` change events on a no-inbound entity, which the compression policy must
keep OUT of state_summary. It is the concrete thing the compression assertion
checks.

NOTE: this file is the CLEAN BASELINE. `scripts/seed-history.sh` mutates it (adds
an idempotency_key param, then churns health) as it builds the git history; running
that script leaves a working copy modified. Keep the checked-in version clean.
"""

from fastapi import FastAPI

from contracts import Charge

app = FastAPI()


@app.post("/v1/charge", response_model=Charge)
def create_charge(order_id: str, amount: int, currency: str) -> Charge:
    # Construct the charge for the given order. The response shape is Charge, so
    # the route's contract is exactly Charge's field set.
    return Charge(id=order_id, amount=amount, currency=currency)


@app.get("/v1/health")
def health() -> dict:
    # Liveness probe. No other service depends on this route — it exists so the
    # demo has an entity that receives internal churn but has no inbound edge.
    return {"status": "ok"}
