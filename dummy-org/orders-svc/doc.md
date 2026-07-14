# `orders-svc/` — the charge consumer + event producer

- `app.py` — `POST /v1/orders` (builds an `Order`, `depends_on_schema` →
  `contracts.Order`) and publishes the `order.created` topic (producing
  `orders-svc::topic:order.created`, which notifications-svc subscribes to).
- `src/pay.py` — the single **consuming line** the demo cites: `httpx.post(
  f"{base}/v1/charge", ...)` produces the `consumes` edge to payments' route with
  this file/line as the citation, and reads `resp.json()["amount"]` — the exact
  contract that breaks when payments renames the field.

Also reads `PAYMENTS_BASE_URL` (a `config_key` entity). `manifest.yaml` declares
the consumes + depends_on_schema edges and the `order.created` publish. This is the
service whose `.orgcontext/UPSTREAM.md` proves Scenario A end to end.
