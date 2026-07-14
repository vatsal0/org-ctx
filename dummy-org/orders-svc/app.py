"""orders-svc: creates orders and emits an `order.created` event.

Producer side of this file:
  - it builds an `Order` (depends_on_schema edge to `contracts::schema:Order`), and
  - it publishes the `order.created` topic via `broker.publish("order.created", ...)`,
    producing the entity `orders-svc::topic:order.created`, which
    notifications-svc subscribes to.

The HTTP call to payments lives in src/pay.py (the consuming line the demo cites);
this module holds the order-creation flow and the publish.
"""

from fastapi import FastAPI

from contracts import Order

app = FastAPI()

# A stand-in message broker. Only the ".publish(\"order.created\")" call below
# matters to the extractor — it becomes a queue-topic producer entity.
broker = object()


@app.post("/v1/orders", response_model=Order)
def create_order(order_id: str, items: list[str], total: int) -> Order:
    # Record the order and announce it on the bus for downstream consumers.
    order = Order(id=order_id, items=items, total=total)
    broker.publish("order.created", {"id": order_id})
    return order
