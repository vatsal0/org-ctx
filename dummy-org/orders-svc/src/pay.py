"""orders-svc payment client: the single consuming line the demo cites.

This module issues the outbound HTTP call to payments-svc's POST /v1/charge. The
extractor recognizes the `httpx.post(f"{base}/v1/charge", ...)` call and records a
`consumes` edge (orders-svc::call:POST /v1/charge -> payments-svc::http:POST
/v1/charge) with THIS file and line as the citation. When payments' charge
contract changes, "you consume this in orders-svc/src/pay.py:<line>" points here.

It also reads the `PAYMENTS_BASE_URL` env var, which the extractor records as a
config-key entity owned by orders-svc.
"""

import os

import httpx


def charge_order(order_id: str, amount: int, currency: str) -> int:
    # Resolve the payments base URL from the environment (a config-key entity).
    base = os.environ["PAYMENTS_BASE_URL"]
    # Call payments-svc to charge the order. This is the cited consuming line.
    resp = httpx.post(f"{base}/v1/charge", json={"order_id": order_id, "amount": amount, "currency": currency})
    # We depend on the response field `amount` — the exact contract that breaks
    # when payments renames it to `amount_cents`.
    return resp.json()["amount"]
