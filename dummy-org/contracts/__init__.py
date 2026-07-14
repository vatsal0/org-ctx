"""The `contracts` package: the org's shared schema surface.

Re-exporting the models here means downstream services write `from contracts
import Charge` — a clean, stable import our extractor recognizes as a
depends_on_schema edge to `contracts::schema:Charge`. `__all__` doubles as the
explicit list of exported symbols the extractor reads.
"""

from .schemas import Charge, Order, Event

__all__ = ["Charge", "Order", "Event"]
