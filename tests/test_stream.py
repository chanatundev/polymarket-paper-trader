from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pm_trader.normalizers import normalize_book
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
    snapshot = normalize_book(
        {
            "market": "0xgeneral-event-001",
            "asset_id": "token-general-yes",
            "timestamp": "2026-08-26T20:00:14Z",
            "hash": "resync-hash",
            "bids": [{"price": "0.63", "size": "10"}],
            "asks": [{"price": "0.66", "size": "20"}],
            "min_order_size": "5",
            "tick_size": "0.01",
            "neg_risk": False,
            "sequence": 20,
        }
    )
    update = processor.resync(snapshot)
    assert update.needs_resync is False
    assert update.event.resynchronized is True
    assert update.snapshot.continuity == "resynchronized"
    assert (
        processor.apply(
            {
                "event_type": "price_change",
                "asset_id": "token-general-yes",
                "timestamp": "2026-08-26T20:00:15Z",
                "sequence": 21,
                "side": "ask",
                "price": "0.66",
                "size": "15",
            }
        ).needs_resync
        is False
    )


def test_incremental_stream_requires_an_initial_authoritative_snapshot(book):
    processor = MarketStreamProcessor(asset_id=book.asset_id)
    update = processor.apply(
        {
            "event_type": "price_change",
            "asset_id": book.asset_id,
            "timestamp": "2026-08-26T20:00:01Z",
            "sequence": 1,
            "changes": [{"side": "buy", "price": "0.60", "size": "2"}],
        }
    )

    assert update.needs_resync is True
    assert update.snapshot is None
    assert update.event.gap_detected is True
    assert "initial_authoritative_snapshot" in update.warnings[0]
