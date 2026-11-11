from __future__ import annotations

import json
from pathlib import Path

from pm_trader.activity import append_activity, read_activity
from pm_trader.cli import run

ROOT = Path(__file__).parents[1]


def test_activity_log_is_newest_first_and_does_not_create_a_missing_directory(tmp_path: Path):
    missing = tmp_path / "absent"
    assert read_activity(missing) == []
    assert not missing.exists()

    data_dir = tmp_path / "paper"
    append_activity(data_dir, {"kind": "use", "command": "discover", "ok": True})
    append_activity(
        data_dir,
        {
            "kind": "trade",
            "command": "simulate",
            "market": "Example <script>",
            "side": "buy",
            "price": "0.66",
            "size": "10",
            "status": "filled",
            "cash": "999.10",
        },
    )
    events = read_activity(data_dir)
    assert events[0]["kind"] == "trade"
    assert events[0]["market"] == "Example <script>"
    assert events[1]["command"] == "discover"
    text = (data_dir / "activity.jsonl").read_text(encoding="utf-8")
    assert text.count("\n") == 2
    from dashboard.app import build_snapshot, render_dashboard_html

    html = render_dashboard_html(build_snapshot(data_dir))
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_activity_log_keeps_only_the_tail(tmp_path: Path):
    data_dir = tmp_path / "paper"
    for index in range(205):
        append_activity(data_dir, {"kind": "use", "command": "discover", "n": index})
    lines = (data_dir / "activity.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200
    assert json.loads(lines[-1])["n"] == 204
    assert json.loads(lines[0])["n"] == 5


def test_cli_usage_is_logged_and_safety_creates_nothing(tmp_path: Path):
    safety_dir = tmp_path / "safety"
    code, _payload = run(["--data-dir", str(safety_dir), "safety"])
    assert code == 0
    assert not safety_dir.exists()

    data_dir = tmp_path / "used"
    code, _payload = run(["--data-dir", str(data_dir), "explain"])
    assert code == 0
    events = read_activity(data_dir)
    assert events[0]["kind"] == "command"
    assert events[0]["command"] == "explain"
    assert events[0]["source"] == "local"
    assert events[0]["ok"] is True


def test_discovery_lookups_and_failures_are_compact(tmp_path: Path):
    from pm_trader.api import CLOB_BASE, CLOBAdapter, FixtureTransport
    from pm_trader.mcp_server import dispatch_tool

    data_dir = tmp_path / "lookups"
    code, _payload = run(
        [
            "--data-dir",
            str(data_dir),
            "discover",
            "--fixture",
            str(ROOT / "fixtures/gamma_markets_keyset_page1.json"),
            "--limit",
            "5",
        ]
    )
    assert code == 0
    code, _payload = run(
        [
            "--data-dir",
            str(data_dir),
            "markets",
            "search",
            "--fixture",
            str(ROOT / "fixtures/gamma_public_search.json"),
            "example",
        ]
    )
    assert code == 0
    code, _payload = run(
        [
            "--data-dir",
            str(data_dir),
            "inspect",
            "book",
            "--fixture",
            str(ROOT / "fixtures/clob_book.json"),
        ]
    )
    assert code == 0
    code, _payload = run(["--data-dir", str(data_dir), "report", "--run-id", "missing-run"])
    assert code == 1

    fee_dir = tmp_path / "fees"
    quiet = CLOBAdapter(
        transport=FixtureTransport({f"{CLOB_BASE}/fee-rate": {"base_fee": 500}})
    )
    quiet.get_fee_rate("token-general-yes")
    assert not fee_dir.exists()
    logging_adapter = CLOBAdapter(
        transport=FixtureTransport(
            {
                f"{CLOB_BASE}/fee-rate": {"base_fee": 500},
                f"{CLOB_BASE}/prices-history": {"history": [{"t": 1787774400, "p": 0.61}]},
            }
        )
    )
    logging_adapter.activity_dir = fee_dir
    logging_adapter.get_fee_rate("token-general-yes")
    logging_adapter.get_price_history("token-general-yes", interval="1h")

    book = dispatch_tool(
        "inspect_book",
        {"fixture": str(ROOT / "fixtures/clob_book.json"), "data_dir": str(data_dir)},
    )
    assert book["ok"] is True

    events = read_activity(data_dir, limit=20)
    kinds = [event["kind"] for event in events]
    assert "keyset" in kinds
    assert "search" in kinds
    assert kinds.count("lookup") >= 2
    book_events = [event for event in events if event.get("target") == "book"]
    assert book_events[0]["token_id"] == "token-general-yes"
    assert "bids" not in book_events[0]
    report = next(event for event in events if event.get("target") == "report")
    assert report["ok"] is False
    assert report["error"] == "REPORT_NOT_FOUND"
    search = next(event for event in events if event["kind"] == "search")
    assert search["query"] == "example"
    assert search["source"] == "fixture"
    assert "events" not in search
    fee_events = read_activity(fee_dir)
    assert {event["target"] for event in fee_events} == {"fee", "prices"}
    assert {event["source"] for event in fee_events} == {"fixture"}
    assert fee_events[0]["count"] == 1
    assert fee_events[1]["bps"] == 500


def test_simulate_appends_order_lines_the_dashboard_can_render(tmp_path: Path):
    from dashboard.app import build_snapshot, render_dashboard_html

    data_dir = tmp_path / "sim"
    code, _payload = run(
        [
            "--data-dir",
            str(data_dir),
            "simulate",
            "--market-fixture",
            str(ROOT / "fixtures/gamma_markets_keyset_page1.json"),
            "--book-fixture",
            str(ROOT / "fixtures/clob_book.json"),
            "--price",
            "0.66",
            "--size",
            "10",
        ]
    )
    assert code == 0
    events = read_activity(data_dir, limit=20)
    kinds = [event["kind"] for event in events]
    assert kinds[0] == "cash"
    assert "order" in kinds
    assert "decision" in kinds
    assert "fill" in kinds
    order = next(event for event in events if event["kind"] == "order")
    assert order["side"] == "buy"
    assert order["price"] == "0.66"
    assert order["size"] == "10"
    assert order["tif"] == "fok"
    assert "example" in order["market"]
    snapshot = build_snapshot(data_dir)
    assert snapshot["state"] == "ready"
    assert snapshot["rows"][0]["kind"] == "cash"
    html = render_dashboard_html(snapshot)
    assert 'http-equiv="refresh"' in html
    assert "(model)" in html
    assert "Activity" in html
    assert snapshot["cash"]


def test_library_search_market_book_and_report_lookups_are_logged(tmp_path: Path):
    from pm_trader.api import CLOB_BASE, GAMMA_BASE, CLOBAdapter, FixtureTransport, GammaAdapter
    from pm_trader.storage import SQLiteStore
    from pm_trader.workflows import ResearchService, load_market_fixture

    gamma_payload = json.loads(
        (ROOT / "fixtures/gamma_markets_keyset_page1.json").read_text(encoding="utf-8")
    )
    book_payload = json.loads((ROOT / "fixtures/clob_book.json").read_text(encoding="utf-8"))
    data_dir = tmp_path / "library"
    service = ResearchService(
        SQLiteStore(data_dir),
        gamma=GammaAdapter(
            transport=FixtureTransport({f"{GAMMA_BASE}/markets/keyset": gamma_payload})
        ),
        clob=CLOBAdapter(
            transport=FixtureTransport({f"{CLOB_BASE}/book": book_payload})
        ),
    )

    service.discover(limit=5)
    service.inspect_market("will-the-example-event-happen-by-2030")
    market = load_market_fixture(ROOT / "fixtures/gamma_markets_keyset_page1.json")
    service.inspect_book(market, "Yes")
    service.portfolio_report("missing-report")

    events = read_activity(data_dir)
    keyset = next(event for event in events if event["kind"] == "keyset")
    assert keyset["source"] == "fixture"
    market_event = next(event for event in events if event.get("target") == "market")
    assert market_event["source"] == "fixture"
    book_event = next(event for event in events if event.get("target") == "book")
    assert book_event["token_id"] == "token-general-yes"
    assert book_event["source"] == "fixture"
    report_event = next(event for event in events if event.get("target") == "report")
    assert report_event["ok"] is False
    assert report_event["error"] == "REPORT_NOT_FOUND"

    from pm_trader.mcp_server import dispatch_tool

    before = sum(event["kind"] == "keyset" for event in events)
    assert dispatch_tool("discover_markets", {"limit": 5}, service=service)["ok"] is True
    after = sum(event["kind"] == "keyset" for event in read_activity(data_dir))
    assert after == before + 1


def test_library_simulation_survives_activity_log_io_failure(tmp_path: Path, monkeypatch):
    import pm_trader.activity as activity
    from pm_trader.models import PaperOrder
    from pm_trader.storage import SQLiteStore
    from pm_trader.workflows import ResearchService, load_book_fixture, load_market_fixture

    def fail_to_write(*_args, **_kwargs):
        raise OSError("activity log unavailable")

    monkeypatch.setattr(activity, "append_activity", fail_to_write)
    store = SQLiteStore(tmp_path / "paper")
    service = ResearchService(store)
    market = load_market_fixture(ROOT / "fixtures/gamma_markets_keyset_page1.json")
    book = load_book_fixture(ROOT / "fixtures/clob_book.json")
    token = market.token_for("Yes")
    result = service.simulate(
        market=market,
        book=book,
        orders=(
            PaperOrder(
                order_id="library-order",
                market_id=market.condition_id,
                asset_id=token.token_id,
                side="buy",
                price="0.66",
                quantity="10",
                time_in_force="fok",
            ),
        ),
    )

    assert result["ok"] is True
