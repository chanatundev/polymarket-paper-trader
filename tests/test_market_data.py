from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from pm_trader.api import CLOB_BASE, GAMMA_BASE, CLOBAdapter, FixtureTransport, GammaAdapter
from pm_trader.cli import run
from pm_trader.errors import LocalValidationError
from pm_trader.market_data import aggregate_positions, book_summary, price_summary
from pm_trader.mcp_server import TOOL_DEFINITIONS, dispatch_tool
from pm_trader.models import PricePoint
from pm_trader.storage import SQLiteStore
from pm_trader.workflows import ResearchService, load_book_fixture

ROOT = Path(__file__).parents[1]


def fixture(name: str):
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def offline_service(tmp_path: Path, **gamma_extra) -> tuple[ResearchService, FixtureTransport]:
    page = fixture("gamma_markets_keyset_page1.json")
    page["markets"][0].update(gamma_extra)
    gamma = FixtureTransport({f"{GAMMA_BASE}/markets/keyset": page})
    clob = FixtureTransport(
        {
            f"{CLOB_BASE}/book": fixture("clob_book.json"),
            f"{CLOB_BASE}/prices-history": fixture("clob_price_history.json"),
        }
    )
    service = ResearchService(
        SQLiteStore(tmp_path / "db"),
        gamma=GammaAdapter(transport=gamma),
        clob=CLOBAdapter(transport=clob),
    )
    return service, gamma


def test_book_summary_is_best_first_with_spread_and_depth():
    summary = book_summary(load_book_fixture(ROOT / "fixtures/clob_book.json"), depth=2)
    assert summary["best_bid"] == "0.62"
    assert summary["best_ask"] == "0.66"
    assert summary["midpoint"] == "0.64"
    assert summary["spread"] == "0.04"
    assert [level["price"] for level in summary["bids"]] == ["0.62", "0.61"]
    assert [level["price"] for level in summary["asks"]] == ["0.66", "0.67"]
    assert summary["bid_levels"] == 3
    # Within 5c of 0.64: bids 0.62, 0.61, 0.60 and asks 0.66, 0.67, 0.69.
    assert summary["depth_near_mid"]["within_5c"]["bids"]["shares"] == "220"
    assert summary["depth_near_mid"]["within_5c"]["asks"]["shares"] == "165"


def test_price_summary_downsamples_and_keeps_last_point():
    points = [PricePoint(timestamp=str(1787774400 + i * 60), price=Decimal("0.5") + Decimal(i) / 100) for i in range(10)]
    summary = price_summary(points, max_points=3)
    assert summary["count"] == 10
    assert summary["open"] == "0.5"
    assert summary["close"] == "0.59"
    assert summary["change"] == "0.09"
    assert summary["points"][-1]["p"] == "0.59"
    assert summary["start"].endswith("Z")
    assert len(summary["points"]) <= 4


def test_aggregate_positions_uses_average_cost():
    orders = {("r1", "o1"): {"market_id": "m", "metadata": {"outcome": "Yes"}}}
    fills = [
        {"run_id": "r1", "order_id": "o1", "asset_id": "a", "side": "buy", "quantity": "10", "price": "0.4", "fee": "0.1"},
        {"run_id": "r1", "order_id": "o1", "asset_id": "a", "side": "buy", "quantity": "10", "price": "0.6", "fee": "0.1"},
        {"run_id": "r1", "order_id": "o1", "asset_id": "a", "side": "sell", "quantity": "5", "price": "0.7", "fee": "0"},
    ]
    [row] = aggregate_positions(fills, orders)
    assert row["quantity"] == "15"
    assert row["average_price"] == "0.5"
    assert row["realized_pnl"] == "1"
    assert row["fees"] == "0.2"
    assert row["outcome"] == "Yes"


def test_discovery_rows_carry_outcomes_and_stats(tmp_path: Path):
    service, gamma = offline_service(tmp_path, volume24hr=1234.567, liquidityClob="99", bestBid="0.62")
    payload = service.discover(limit=5, order="volume_24hr", open_only=True)
    row = payload["result"]["markets"][0]
    assert row["outcomes"][0] == {"outcome": "Yes", "price": "0.64"}
    assert row["stats"] == {"volume_24hr": "1234.57", "liquidity": "99", "best_bid": "0.62"}
    params = gamma.requests[0][1]
    assert params["order"] == "volume24hr"
    assert params["ascending"] == "false"
    assert params["closed"] == "false"
    with pytest.raises(LocalValidationError):
        service.discover(order="bogus")


def test_quote_and_price_history_are_compact(tmp_path: Path):
    service, _ = offline_service(tmp_path)
    quote = service.quote("will-the-example-event-happen-by-2030", outcome="Yes", depth=1)
    assert quote["ok"] is True
    market = quote["result"]["market"]
    assert market["outcomes"][0]["token_id"] == "token-general-yes"
    assert market["tick_size"] == "0.01"
    assert market["min_order_size"] == "5"
    [book] = quote["result"]["books"]
    assert book["outcome"] == "Yes"
    assert book["best_ask"] == "0.66"
    assert len(book["asks"]) == 1

    history = service.price_history("will-the-example-event-happen-by-2030")
    assert history["result"]["token_id"] == "token-general-yes"
    assert history["result"]["open"] == "0.61"
    assert history["result"]["close"] == "0.65"


def test_cli_portfolio_aggregates_stored_runs(tmp_path: Path):
    data_dir = str(tmp_path / "db")
    for order_id in ("p1", "p2"):
        code, _ = run(
            [
                "--data-dir", data_dir, "simulate",
                "--market-fixture", str(ROOT / "fixtures/gamma_markets_keyset_page1.json"),
                "--book-fixture", str(ROOT / "fixtures/clob_book.json"),
                "--order-id", order_id, "--price", "0.66", "--size", "10", "--tif", "fak",
            ]
        )
        assert code == 0
    code, payload = run(["--data-dir", data_dir, "portfolio"])
    assert code == 0
    [position] = payload["result"]["positions"]
    assert position["quantity"] == "20"
    assert position["average_price"] == "0.66"
    assert position["outcome"] == "Yes"
    assert payload["assumptions"]["aggregates_independent_runs"] is True


def test_cli_simulate_requires_a_market_source(tmp_path: Path):
    code, payload = run(["--data-dir", str(tmp_path / "db"), "simulate"])
    assert code == 2
    assert "--market" in payload["errors"][0]["message"]


def test_mcp_exposes_market_data_tools(tmp_path: Path):
    names = {tool["name"] for tool in TOOL_DEFINITIONS}
    assert {"quote_market", "price_history", "paper_portfolio"} <= names
    service, _ = offline_service(tmp_path)
    quote = dispatch_tool(
        "quote_market", {"market": "will-the-example-event-happen-by-2030"}, service=service
    )
    assert [book["outcome"] for book in quote["result"]["books"]] == ["Yes", "No"]
    portfolio = dispatch_tool("paper_portfolio", {}, service=service)
    assert portfolio["ok"] is True
    assert portfolio["result"]["positions"] == []
