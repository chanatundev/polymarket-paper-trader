from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import pytest

from pm_trader.api import ResourceLimits
from pm_trader.db import Database
from pm_trader.gui import create_server
from pm_trader.history import HistoryQuery, HistoryStoreUnavailable, TradeHistoryReader
from pm_trader.models import PaperOrder, SimulationConfig
from pm_trader.simulation import SimulationEngine
from pm_trader.storage import SQLiteStore

ROOT = Path(__file__).parents[1]


def _seed_canonical(data_dir: Path, market, book) -> str:
    store = SQLiteStore(data_dir)
    order = PaperOrder(
        order_id="history-order",
        market_id=market.condition_id,
        asset_id=market.tokens[0].token_id,
        side="buy",
        price=Decimal("0.67"),
        quantity=Decimal(40),
        time_in_force="fak",
        metadata={
            "market_slug": market.slug,
            "market_question": market.question,
            "outcome": market.tokens[0].outcome,
        },
    )
    result = SimulationEngine(store).run(
        market=market,
        book=book,
        orders=(order,),
        config=SimulationConfig(starting_cash=Decimal(1000)),
        run_id="history-run",
    )
    store.close()
    return result.run_id


def _seed_legacy(data_dir: Path, *, created_at: str = "2026-08-26T20:00:11Z") -> int:
    database = Database(data_dir)
    database.init_schema()
    trade = database.insert_trade(
        market_condition_id="legacy-market",
        market_slug="legacy-market-slug",
        market_question="<script>alert(1)</script>",
        outcome="Yes",
        side="sell",
        order_type="fak",
        avg_price=0.61,
        amount_usd=12.2,
        shares=20,
        fee_rate_bps=5,
        fee=0.03,
        slippage=0.01,
        levels_filled=2,
        is_partial=True,
    )
    database.conn.execute("UPDATE trades SET created_at = ? WHERE id = ?", (created_at, trade.id))
    database.conn.commit()
    database.close()
    return trade.id


@contextmanager
def _running_server(data_dir: Path):
    server = create_server(data_dir, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(server, method: str, path: str) -> tuple[int, dict[str, str], bytes]:
    host, port = server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=3)
    try:
        connection.request(method, path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


@pytest.fixture
def seeded_store(tmp_path: Path, market, book) -> tuple[Path, str, int]:
    data_dir = tmp_path / "paper-data"
    run_id = _seed_canonical(data_dir, market, book)
    trade_id = _seed_legacy(data_dir)
    return data_dir, run_id, trade_id


def test_reader_merges_canonical_and_legacy_with_full_detail(seeded_store):
    data_dir, run_id, trade_id = seeded_store
    reader = TradeHistoryReader.open(data_dir)
    page = reader.list_history()

    assert {record["source_kind"] for record in page.records} == {
        "canonical_fill",
        "legacy_trade",
    }
    assert page.total == 3
    assert [record["occurred_at"] for record in page.records] == sorted(
        (record["occurred_at"] for record in page.records), reverse=True
    )

    canonical = next(record for record in page.records if record["source_kind"] == "canonical_fill")
    assert canonical["market"]["question"] == "Will the example general event happen by 2030?"
    assert canonical["execution"]["liquidity_role"] == "taker"
    assert canonical["warnings"]
    assert canonical["unknown_reasons"]["evidence.freshness"]

    detail = reader.get_history(canonical["history_id"])
    assert detail is not None
    assert detail["identity"]["run_id"] == run_id
    assert detail["identity"]["order_id"] == "history-order"
    assert detail["identity"]["fill_id"] == detail["fill"]["fill_id"]
    assert detail["accounting"]["ledger_entries"]
    assert detail["accounting"]["report"]["checksum"]
    assert detail["related"]["order_events"]

    legacy = reader.get_history(f"legacy:{trade_id}")
    assert legacy is not None
    assert legacy["market"]["question"] == "<script>alert(1)</script>"
    assert legacy["execution"]["status"] == "partially_filled"
    assert legacy["unknown_reasons"]["evidence.provenance"]
    reader.close()


def test_reader_filters_and_paginates_bounded_results(seeded_store):
    data_dir, run_id, _trade_id = seeded_store
    reader = TradeHistoryReader.open(data_dir, limits=ResourceLimits(max_page_size=2))

    first = reader.list_history(HistoryQuery(limit=1))
    assert len(first.records) == 1
    assert first.next_cursor is not None
    second = reader.list_history(HistoryQuery(limit=1, cursor=first.next_cursor))
    assert len(second.records) == 1
    assert second.records[0]["history_id"] != first.records[0]["history_id"]

    assert reader.list_history(HistoryQuery(limit=2, source_kind="legacy_trade")).total == 1
    assert reader.list_history(HistoryQuery(limit=2, side="sell")).total == 1
    assert reader.list_history(HistoryQuery(limit=2, run_id=run_id)).total == 2
    assert reader.list_history(HistoryQuery(limit=2, search="example general event")).total == 2
    with pytest.raises(ValueError, match="page-size"):
        reader.list_history(HistoryQuery(limit=3))
    with pytest.raises(ValueError, match="cursor"):
        reader.list_history(HistoryQuery(limit=1, cursor="not-a-cursor"))
    reader.close()


def test_reader_is_sqlite_read_only_and_missing_store_does_not_get_created(
    tmp_path: Path, market, book
):
    data_dir = tmp_path / "paper-data"
    _seed_canonical(data_dir, market, book)
    database_path = data_dir / "paper.db"
    before = database_path.read_bytes()

    reader = TradeHistoryReader.open(data_dir)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        reader.connection.execute("CREATE TABLE should_not_exist(id INTEGER)")
    reader.list_history()
    reader.close()
    assert database_path.read_bytes() == before
    assert not Path(str(database_path) + "-shm").exists()
    assert not Path(str(database_path) + "-wal").exists()

    missing_dir = tmp_path / "missing-data"
    missing_reader = TradeHistoryReader.open(missing_dir)
    assert not missing_dir.exists()
    with pytest.raises(HistoryStoreUnavailable):
        missing_reader.list_history()


def test_reader_supports_canonical_tables_without_order_events(tmp_path: Path):
    database_path = tmp_path / "minimal.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE simulation_runs (
            run_id TEXT PRIMARY KEY, dataset_id TEXT, created_at TEXT,
            config TEXT, status TEXT, model_versions TEXT
        );
        CREATE TABLE orders (run_id TEXT, order_id TEXT, payload TEXT, PRIMARY KEY (run_id, order_id));
        CREATE TABLE fills (
            run_id TEXT, fill_id TEXT, order_id TEXT, observation_id TEXT,
            payload TEXT, PRIMARY KEY (run_id, fill_id)
        );
        CREATE TABLE reports (run_id TEXT PRIMARY KEY, checksum TEXT, payload TEXT);
        INSERT INTO simulation_runs VALUES ('run-1', NULL, '2026-08-26T20:00:00Z', '{}', 'completed', '{}');
        INSERT INTO orders VALUES ('run-1', 'order-1', '{"market_id":"market-1","asset_id":"asset-1","side":"buy","status":"filled"}');
        INSERT INTO fills VALUES ('run-1', 'fill-1', 'order-1', NULL, '{"asset_id":"asset-1","side":"buy","quantity":"1","price":"0.5","notional":"0.5","maker_taker":"taker","fee":"0"}');
        """
    )
    connection.commit()
    connection.close()

    reader = TradeHistoryReader.open(database_path)
    page = reader.list_history()
    assert page.total == 1
    assert page.records[0]["execution"]["status"] == "filled"
    assert page.records[0]["market"]["slug"] is None
    assert page.records[0]["unknown_reasons"]["market.slug"]
    reader.close()


def test_gui_routes_are_bounded_local_gets_and_escape_stored_text(seeded_store, monkeypatch):
    data_dir, _run_id, _trade_id = seeded_store

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("provider calls are outside the GUI boundary")

    monkeypatch.setattr("pm_trader.api.UrllibJsonTransport.get", fail_provider)
    with _running_server(data_dir) as server:
        status, headers, body = _request(server, "GET", "/")
        assert status == 200
        assert headers["Content-Security-Policy"].startswith("default-src 'self'")
        assert b"PAPER ONLY" in body

        status, _headers, body = _request(server, "GET", "/api/trades?limit=3")
        assert status == 200
        page_payload = json.loads(body)
        assert page_payload["ok"] is True
        assert len(page_payload["result"]["records"]) == 3

        legacy_record = next(
            record
            for record in page_payload["result"]["records"]
            if record["source_kind"] == "legacy_trade"
        )
        history_id = legacy_record["history_id"]
        status, _headers, body = _request(
            server, "GET", "/api/trades/" + quote(history_id, safe="")
        )
        assert status == 200
        detail_payload = json.loads(body)
        assert detail_payload["result"]["market"]["question"] == "<script>alert(1)</script>"
        assert "<script>" in body.decode("utf-8")

        status, _headers, body = _request(server, "GET", "/api/trades/missing")
        assert status == 404
        assert json.loads(body)["errors"][0]["code"] == "HISTORY_RECORD_NOT_FOUND"

        for method in ("POST", "PUT", "PATCH", "DELETE"):
            status, headers, _body = _request(server, method, "/api/trades")
            assert status == 405
            assert headers["Allow"] == "GET"

    asset_source = (ROOT / "pm_trader" / "gui_assets" / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in asset_source
    assert "textContent" in asset_source


def test_gui_reports_missing_store_without_creating_it(tmp_path: Path):
    data_dir = tmp_path / "missing-data"
    with _running_server(data_dir) as server:
        status, _headers, body = _request(server, "GET", "/api/trades")
        assert status == 503
        payload = json.loads(body)
        assert payload["errors"][0]["code"] == "HISTORY_STORE_UNAVAILABLE"
        status, _headers, _body = _request(server, "GET", "/")
        assert status == 200
    assert not data_dir.exists()


def test_cli_gui_descriptor_does_not_open_or_create_a_store(tmp_path: Path):
    from pm_trader.cli import run

    data_dir = tmp_path / "not-created"
    code, payload = run(["--data-dir", str(data_dir), "gui", "--port", "0"])
    assert code == 0
    assert payload["result"]["mode"] == "local_read_only_gui"
    assert payload["result"]["host"] == "127.0.0.1"
    assert payload["result"]["port"] == 0
    assert payload["assumptions"]["provider_calls"] is False
    assert not data_dir.exists()
