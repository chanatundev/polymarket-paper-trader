from __future__ import annotations

import http.client
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from pm_trader.cli import run
from pm_trader.storage import SQLiteStore
from test_history_gui import _seed_canonical, _seed_legacy

ROOT = Path(__file__).parents[1]


def test_parser_without_dashboard_still_requires_a_command(tmp_path: Path):
    with pytest.raises(SystemExit) as no_args:
        run([])
    assert no_args.value.code == 2

    with pytest.raises(SystemExit) as data_dir_only:
        run(["--data-dir", str(tmp_path / "x")])
    assert data_dir_only.value.code == 2


def test_dashboard_flag_rejects_a_subcommand(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        run(["--data-dir", str(tmp_path / "x"), "--dashboard", "safety"])
    assert exc.value.code == 2


def test_dashboard_descriptor_does_not_open_or_create_a_store(tmp_path: Path):
    data_dir = tmp_path / "not-created"
    code, payload = run(
        ["--data-dir", str(data_dir), "--dashboard", "--dashboard-port", "0"]
    )
    assert code == 0
    assert payload["result"]["mode"] == "local_read_only_dashboard"
    assert payload["result"]["host"] == "127.0.0.1"
    assert payload["result"]["port"] == 0
    assert payload["assumptions"]["provider_calls"] is False
    assert payload["assumptions"]["read_only"] is True
    assert not data_dir.exists()


def test_dashboard_default_port_is_separate_from_gui(tmp_path: Path):
    code, payload = run(["--data-dir", str(tmp_path / "x"), "--dashboard"])
    assert code == 0
    assert payload["result"]["port"] == 8766


def test_dashboard_package_is_not_imported_without_the_flag():
    script = (
        "import sys\n"
        "from pm_trader.cli import run\n"
        "run(['safety'])\n"
        "loaded = [m for m in sys.modules if m == 'dashboard' or m.startswith('dashboard.')]\n"
        "assert not loaded, loaded\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


def test_missing_store_snapshot_is_an_empty_state_and_creates_nothing(tmp_path: Path):
    from dashboard.app import build_snapshot, render_dashboard_html

    data_dir = tmp_path / "missing-data"
    snapshot = build_snapshot(data_dir)
    assert snapshot["state"] == "missing"
    assert snapshot["activity"] == []
    assert snapshot["transactions"] == []
    html = render_dashboard_html(snapshot)
    assert "No paper trades yet" in html
    assert "PAPER ONLY · READ ONLY" in html
    assert not data_dir.exists()


def test_empty_store_snapshot_is_an_empty_state(tmp_path: Path):
    from dashboard.app import build_snapshot, render_dashboard_html

    data_dir = tmp_path / "empty-data"
    SQLiteStore(data_dir).close()
    snapshot = build_snapshot(data_dir)
    assert snapshot["state"] == "empty"
    assert "No paper trades yet" in render_dashboard_html(snapshot)


def test_activity_makes_an_empty_existing_store_ready(tmp_path: Path):
    from dashboard.app import build_snapshot
    from pm_trader.activity import append_activity

    data_dir = tmp_path / "activity-only"
    SQLiteStore(data_dir).close()
    append_activity(data_dir, {"kind": "command", "command": "search"})

    assert build_snapshot(data_dir)["state"] == "ready"


def test_trades_render_newest_first_with_report_and_history(tmp_path: Path, market, book):
    from dashboard.app import build_snapshot, render_dashboard_html

    data_dir = tmp_path / "paper-data"
    _seed_canonical(data_dir, market, book)
    _seed_legacy(data_dir, created_at="2026-08-26T20:00:11Z")
    snapshot = build_snapshot(data_dir)

    assert snapshot["state"] == "ready"
    assert snapshot["transaction_total"] == 3
    assert len(snapshot["transactions"]) == 3
    assert snapshot["transactions"][0]["time"] >= snapshot["transactions"][1]["time"]
    assert snapshot["report"]

    html = render_dashboard_html(snapshot)
    assert "No paper trades yet" not in html
    assert market.question in html
    assert "buy" in html and "sell" in html


def test_dashboard_shows_activity_and_history_beyond_twenty(tmp_path: Path):
    from dashboard.app import build_snapshot
    from pm_trader.activity import append_activity

    data_dir = tmp_path / "many"
    data_dir.mkdir()
    for index in range(25):
        _seed_legacy(data_dir, created_at=f"2026-08-26T20:{index:02d}:00Z")
        append_activity(data_dir, {"kind": "command", "command": f"event-{index}"})
    snapshot = build_snapshot(data_dir)
    assert snapshot["transaction_total"] == 25
    assert len(snapshot["transactions"]) == 25
    assert len(snapshot["activity"]) == 25


def test_stored_script_text_is_escaped_in_html(tmp_path: Path):
    from dashboard.app import build_snapshot, render_dashboard_html

    data_dir = tmp_path / "legacy-data"
    _seed_legacy(data_dir)
    html = render_dashboard_html(build_snapshot(data_dir))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert '<script src="/live.js" defer></script>' in html


def test_cash_chart_escapes_untrusted_ledger_sequence():
    from dashboard.app import _cash_chart

    chart = _cash_chart(
        {
            "cash_history": {
                "points": [
                    {"step": "1</title><script>alert(1)</script>", "cash": "10.00"}
                ]
            }
        }
    )

    assert "<script>" not in chart
    assert "&lt;script&gt;" in chart


def test_dashboard_poll_only_rescans_history_when_its_marker_changes(
    tmp_path: Path, monkeypatch
):
    from dashboard.app import create_dashboard_server
    from pm_trader.activity import append_activity
    from pm_trader.history import TradeHistoryReader

    data_dir = tmp_path / "cached-dashboard"
    _seed_legacy(data_dir, created_at="2026-08-26T20:00:00Z")

    calls = 0
    original = TradeHistoryReader.list_history

    def counted(self, query=None):
        nonlocal calls
        calls += 1
        return original(self, query)

    monkeypatch.setattr(TradeHistoryReader, "list_history", counted)
    server = create_dashboard_server(data_dir, port=0)
    try:
        first = server.get_snapshot()
        second = server.get_snapshot()
        assert calls == 1
        assert second["transaction_total"] == first["transaction_total"]

        append_activity(data_dir, {"kind": "command", "command": "activity-only"})
        activity_refresh = server.get_snapshot()
        assert calls == 1
        assert activity_refresh["activity"][0]["label"].endswith("activity-only")

        _seed_legacy(data_dir, created_at="2026-08-26T21:00:00Z")
        history_refresh = server.get_snapshot()
        assert calls == 2
        assert history_refresh["transaction_total"] == first["transaction_total"] + 1
    finally:
        server.server_close()


def test_server_binds_loopback_and_is_read_only(tmp_path: Path):
    from dashboard.app import create_dashboard_server

    server = create_dashboard_server(tmp_path / "missing", port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "No paper trades yet" in body
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("POST", "/", body=b"x", headers={"Host": f"127.0.0.1:{port}"})
        assert connection.getresponse().status == 501
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
    assert not (tmp_path / "missing").exists()


def test_transactions_endpoint_rejects_bad_cursors_with_400_and_serves_valid_ones(tmp_path: Path):
    from dashboard.app import create_dashboard_server
    from pm_trader.history import encode_cursor

    data_dir = tmp_path / "cursors"
    data_dir.mkdir()
    for index in range(3):
        _seed_legacy(data_dir, created_at=f"2026-08-26T20:{index:02d}:00Z")
    server = create_dashboard_server(data_dir, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def fetch(query: str) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request(
                "GET", f"/transactions{query}", headers={"Host": f"127.0.0.1:{port}"}
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    try:
        for query in (
            "",
            "?cursor=",
            "?cursor=" + "A" * 33,
            "?cursor=!!!!",
            "?cursor=bm90LWFuLW9mZnNldA",
            "?cursor=LTE",  # base64("-1"): a negative offset
        ):
            status, payload = fetch(query)
            assert (status, payload) == (400, {"error": "invalid_cursor"}), query

        status, payload = fetch(f"?cursor={encode_cursor(1)}")
        assert status == 200
        assert "error" not in payload
        assert payload["rows"].count("<tr ") == 2
        assert payload["next_cursor"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_sees_committed_wal_rows_while_writer_is_open_without_writing(tmp_path: Path):
    from dashboard.app import build_snapshot
    from pm_trader.db import Database
    from pm_trader.history import resolve_database_path

    data_dir = tmp_path / "live"
    data_dir.mkdir()
    writer = Database(data_dir)
    writer.init_schema()
    writer.conn.execute("PRAGMA journal_mode=WAL")
    writer.conn.execute("PRAGMA wal_autocheckpoint=0")
    writer.insert_trade(
        market_condition_id="wal-market",
        market_slug="wal-market-slug",
        market_question="Committed WAL market",
        outcome="Yes",
        side="buy",
        order_type="fak",
        avg_price=0.5,
        amount_usd=5.0,
        shares=10,
        fee_rate_bps=5,
        fee=0.01,
        slippage=0.0,
        levels_filled=1,
        is_partial=False,
    )
    writer.conn.commit()
    try:
        database_path = resolve_database_path(data_dir)
        wal_path = database_path.with_name(database_path.name + "-wal")
        assert wal_path.stat().st_size > 0, "the committed row must still sit in the WAL"
        before = (database_path.read_bytes(), wal_path.read_bytes())

        snapshot = build_snapshot(data_dir)

        assert snapshot["state"] == "ready"
        assert snapshot["transaction_total"] == 1
        assert snapshot["transactions"][0]["label"] == "Committed WAL market"
        assert (database_path.read_bytes(), wal_path.read_bytes()) == before
    finally:
        writer.close()
