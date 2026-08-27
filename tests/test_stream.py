from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal

from pm_trader.models import BookSnapshot
from pm_trader.normalizers import make_provenance, normalize_book
from pm_trader.stream import MarketStreamProcessor


ROOT = Path(__file__).parents[1]


def messages():
    return json.loads((ROOT / "fixtures/websocket_messages.json").read_text())


def test_stream_gap_requires_snapshot_resync():
    first, increment, gap = messages()
    processor = MarketStreamProcessor(asset_id="token-general-yes")
    assert processor.apply(first).needs_resync is False
    assert processor.apply(increment).snapshot.bids[0].price == Decimal("0.62")
    update = processor.apply(gap)
    assert update.needs_resync is True
    assert update.event.gap_detected is True
    ignored = processor.apply({**increment, "sequence": 14})
    assert ignored.needs_resync is True
    assert "incremental_event_ignored_until_resync" in ignored.warnings


def test_stream_resync_clears_gap_and_marks_evidence():
    first, _, gap = messages()
    processor = MarketStreamProcessor(asset_id="token-general-yes")
    processor.apply(first)
    processor.apply(gap)
    snapshot = normalize_book({
        "market": "0xgeneral-event-001", "asset_id": "token-general-yes", "timestamp": "2026-08-26T20:00:14Z", "hash": "resync-hash", "bids": [{"price": "0.63", "size": "10"}], "asks": [{"price": "0.66", "size": "20"}], "min_order_size": "5", "tick_size": "0.01", "neg_risk": False, "sequence": 20,
    })
    update = processor.resync(snapshot)
    assert update.needs_resync is False
    assert update.event.resynchronized is True
    assert update.snapshot.continuity == "resynchronized"
    assert processor.apply({"event_type": "price_change", "asset_id": "token-general-yes", "timestamp": "2026-08-26T20:00:15Z", "sequence": 21, "side": "ask", "price": "0.66", "size": "15"}).needs_resync is False
