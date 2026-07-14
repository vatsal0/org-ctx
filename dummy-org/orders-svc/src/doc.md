# `orders-svc/src/`

- `pay.py` — the payment client. Issues `httpx.post(f"{base}/v1/charge", ...)` (the
  `consumes` edge to payments' route, with this file/line as the cited consuming
  location) and reads `resp.json()["amount"]` — the exact field that breaks when
  payments renames `Charge.amount → amount_cents`. This is the "You consume this in
  `orders-svc/src/pay.py:<line>`" pointer that `UPSTREAM.md` surfaces. It also reads
  the `PAYMENTS_BASE_URL` env var (a `config_key` entity).
