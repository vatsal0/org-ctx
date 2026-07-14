"""Shared data contracts for the dummy org.

These Pydantic models are the *schemas* other services depend on. They are the
single most important source of cross-service coupling in the demo: payments-svc
returns a `Charge`, orders-svc builds an `Order`, notifications-svc reads an
`Event`. Because our extractor embeds a route's response-model fields into the
route signature, a change here (e.g. renaming `Charge.amount`) propagates outward
as a *signature change on the payments route*, which orders-svc consumes.

Keep these tiny and obvious — the point of the demo is the edges between services,
not the richness of the models.

NOTE: this file is the CLEAN BASELINE. `scripts/seed-history.sh` adds an ISO-4217
validator to `Charge.currency`, and `scripts/scenario-break.sh` renames
`Charge.amount` -> `amount_cents`. Keep the checked-in version clean so those
scripts apply cleanly.
"""

from pydantic import BaseModel


class Charge(BaseModel):
    """A payment charge. Returned by payments-svc POST /v1/charge."""

    id: str
    amount: int
    currency: str


class Order(BaseModel):
    """A customer order. Produced by orders-svc."""

    id: str
    items: list[str]
    total: int


class Event(BaseModel):
    """A domain event carried on the message bus (e.g. order.created)."""

    name: str
    payload: dict
