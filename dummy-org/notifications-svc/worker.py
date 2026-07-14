"""notifications-svc: subscribes to `order.created` and reads `Event`.

This service is deliberately WIRED ONLY to the order.created topic and the Event
schema. It has NO dependency on payments' /v1/charge. That absence is load-bearing
for the demo's noise-control test (Scenario C): when payments' charge contract
breaks, notifications-svc must receive ZERO flags. Silence for the unaffected is
as important as signal for the affected.
"""

from contracts import Event

# Stand-in broker; the extractor recognizes the ".subscribe(\"order.created\")"
# call as a subscribes edge to orders-svc's topic producer.
broker = object()


def start() -> None:
    # Register the handler for order.created events.
    broker.subscribe("order.created", handle_order_created)


def handle_order_created(event: Event) -> None:
    # Send a notification for the newly created order.
    print(f"notifying about {event.name}")
