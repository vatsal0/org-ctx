# `notifications-svc/` — the deliberately-unaffected subscriber

`worker.py` subscribes to the `order.created` topic (a `subscribes` edge to
orders-svc's topic) and depends on `contracts.Event` (a `depends_on_schema` edge).

It depends on **neither** the charge route **nor** `Charge`. That absence is
load-bearing: when payments' charge contract breaks, this service must receive
**zero** flags. Silence for the unaffected is the noise-control assertion — as
important as signal for the affected. `manifest.yaml` declares only the Event +
order.created dependencies.
