"""Paper-only Polymarket data normalization, simulation, and replay.

The package intentionally has no authenticated client, signing dependency, or
live-write operation. Public observations can feed the simulator; they never
become an order-submission capability.
"""

from .models import (
    BookLevel,
    BookSnapshot,
    Decision,
    FeeSchedule,
    Fill,
    Freshness,
    MarketState,
    PaperOrder,
    Provenance,
)

__all__ = [
    "BookLevel",
    "BookSnapshot",
    "Decision",
    "FeeSchedule",
    "Fill",
    "Freshness",
    "MarketState",
    "PaperOrder",
    "Provenance",
]
