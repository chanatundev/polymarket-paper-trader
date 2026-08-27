from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from pm_trader.api import ResourceLimits
from pm_trader.errors import DatasetLimitError
from pm_trader.models import PaperOrder, SimulationConfig
from pm_trader.normalizers import normalize_book
from pm_trader.recording import Recorder, ReplayEngine, load_stream_metadata
from pm_trader.simulation import SimulationEngine
from pm_trader.storage import SQLiteStore
from pm_trader.stream import MarketStreamProcessor

ROOT = Path(__file__).parents[1]


def make_order(market):
    return PaperOrder(
        order_id="replay-order",
        market_id=market.condition_id,
        asset_id=market.tokens[0].token_id,
        side="buy",
        price=Decimal("0.67"),
        quantity=Decimal(40),
        time_in_force="fak",
    )


def test_recorder_stores_sanitized_raw_and_normalized_book(book, tmp_path: Path):
    store = SQLiteStore(tmp_path / "store")
    recorder = Recorder(store, dataset_id="dataset-1")
    recorder.record_book(
        book, raw_payload={"asset_id": book.asset_id, "private_key": "must-not-appear"}
    )
    rows = store.get_observations("dataset-1")
    assert len(rows) == 1
    assert "must-not-appear" not in rows[0]["raw_payload"]
    destination = recorder.export_jsonl(tmp_path / "dataset.jsonl")
    assert destination.exists()
    assert destination.read_text().count("\n") == 1
    store.close()


def test_replay_is_deterministic_across_two_stores(market, book, tmp_path: Path):
    config = SimulationConfig(starting_cash=Decimal(1000), seed=42, latency_ms=0)
    checksums = []
    for index in range(2):
        store = SQLiteStore(tmp_path / f"store-{index}")
        result = ReplayEngine(store).replay(
            dataset_id="recorded-dataset-v1",
            market=market,
            snapshots=(book,),
            orders=(make_order(market),),
            config=config,
        )
        checksums.append(result.report.checksum)
        entries = store.get_ledger_entries(result.run_id)
        assert entries[0]["kind"] == "initial_cash"
        assert all(entry["run_id"] == result.run_id for entry in entries)
        store.close()
    assert checksums[0] == checksums[1]


def test_ledger_separates_fee_and_rebate_entries(market, book, tmp_path: Path):
    store = SQLiteStore(tmp_path / "store")
    result = SimulationEngine(store).run(
        market=market,
        book=book,
        orders=(make_order(market),),
        config=SimulationConfig(starting_cash=Decimal(1000)),
    )
    kinds = [entry["kind"] for entry in store.get_ledger_entries(result.run_id)]
    assert "fill" in kinds
    assert "fee" in kinds
    assert "rebate_estimate" in kinds
    assert result.report.rebate_estimates == Decimal(0)
    assert result.report.cash < Decimal(1000)
    store.close()


def test_recorded_stream_persists_gap_and_resync_evidence(tmp_path: Path):
    messages = json.loads((ROOT / "fixtures/websocket_messages.json").read_text(encoding="utf-8"))
    processor = MarketStreamProcessor(asset_id="token-general-yes")
    resync_book = normalize_book(
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
    updates = processor.ingest(messages, snapshot_loader=lambda: resync_book)
    store = SQLiteStore(tmp_path / "store")
    recorder = Recorder(store, dataset_id="stream-dataset")
    recorder.record_stream_updates(updates)

    stream_events = store.get_stream_events("stream-dataset")
    assert any(event["gap_detected"] for event in stream_events)
    assert any(event["resynchronized"] for event in stream_events)
    destination = recorder.export_jsonl(tmp_path / "stream.jsonl")
    metadata = load_stream_metadata(destination)
    assert metadata["gap_events"] >= 1
    assert metadata["resynchronizations"] == 1
    store.close()


def test_recorder_enforces_configured_retention_bounds(book, tmp_path: Path):
    store = SQLiteStore(tmp_path / "store")
    recorder = Recorder(
        store,
        dataset_id="tiny-dataset",
        limits=ResourceLimits(max_recorded_bytes=10, max_recorded_observations=1),
    )
    with pytest.raises(DatasetLimitError):
        recorder.record_book(book)
    store.close()
