from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pm_trader.models import PaperOrder, SimulationConfig
from pm_trader.recording import Recorder, ReplayEngine, load_book_dataset
from pm_trader.simulation import SimulationEngine
from pm_trader.storage import SQLiteStore


def make_order(market):
    return PaperOrder(
        order_id="replay-order",
        market_id=market.condition_id,
        asset_id=market.tokens[0].token_id,
        side="buy",
        price=Decimal("0.67"),
        quantity=Decimal("40"),
        time_in_force="fak",
    )


def test_recorder_stores_sanitized_raw_and_normalized_book(book, tmp_path: Path):
    store = SQLiteStore(tmp_path / "store")
    recorder = Recorder(store, dataset_id="dataset-1")
    recorder.record_book(book, raw_payload={"asset_id": book.asset_id, "private_key": "must-not-appear"})
    rows = store.get_observations("dataset-1")
    assert len(rows) == 1
    assert "must-not-appear" not in rows[0]["raw_payload"]
    destination = recorder.export_jsonl(tmp_path / "dataset.jsonl")
    assert destination.exists()
    assert destination.read_text().count("\n") == 1
    store.close()


def test_replay_is_deterministic_across_two_stores(market, book, tmp_path: Path):
    config = SimulationConfig(starting_cash=Decimal("1000"), seed=42, latency_ms=0)
    checksums = []
    for index in range(2):
        store = SQLiteStore(tmp_path / f"store-{index}")
        result = ReplayEngine(store).replay(dataset_id="recorded-dataset-v1", market=market, snapshots=(book,), orders=(make_order(market),), config=config)
        checksums.append(result.report.checksum)
        entries = store.get_ledger_entries(result.run_id)
        assert entries[0]["kind"] == "initial_cash"
        assert all(entry["run_id"] == result.run_id for entry in entries)
        store.close()
    assert checksums[0] == checksums[1]


def test_ledger_separates_fee_and_rebate_entries(market, book, tmp_path: Path):
    store = SQLiteStore(tmp_path / "store")
    result = SimulationEngine(store).run(market=market, book=book, orders=(make_order(market),), config=SimulationConfig(starting_cash=Decimal("1000")))
    kinds = [entry["kind"] for entry in store.get_ledger_entries(result.run_id)]
    assert "fill" in kinds
    assert "fee" in kinds
    assert "rebate_estimate" in kinds
    assert result.report.rebate_estimates == Decimal("0")
    assert result.report.cash < Decimal("1000")
    store.close()
