from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pm_trader.api import CLOB_BASE, GAMMA_BASE, CLOBAdapter, FixtureTransport, GammaAdapter
from pm_trader.cli import run
from pm_trader.errors import LocalValidationError
from pm_trader.market_data import (
    book_age_seconds,
    book_summary,
    estimate_fill,
    hours_to_end,
    mark_position,
    portfolio_totals,
    price_summary,
    time_to_end,
    worst_freshness,
)
from pm_trader.mcp_server import TOOL_DEFINITIONS, dispatch_tool
from pm_trader.models import PricePoint
from pm_trader.storage import SQLiteStore
from pm_trader.workflows import ResearchService, load_book_fixture, load_market_fixture

ROOT = Path(__file__).parents[1]


def fixture(name: str):
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def offline_service(
    tmp_path: Path, *, book: dict | None = None, **gamma_extra
) -> tuple[ResearchService, FixtureTransport]:
    page = fixture("gamma_markets_keyset_page1.json")
    page["markets"][0].update(gamma_extra)
    gamma = FixtureTransport({f"{GAMMA_BASE}/markets/keyset": page})
    clob = FixtureTransport(
        {
            f"{CLOB_BASE}/book": book or fixture("clob_book.json"),
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


def test_discovery_rows_carry_outcomes_and_stats(tmp_path: Path):
    service, gamma = offline_service(tmp_path, volume24hr=1234.567, liquidityClob="99", bestBid="0.62")
    payload = service.discover(limit=5, order="volume_24hr", open_only=True)
    row = payload["result"]["markets"][0]
    assert row["outcomes"][0] == {"outcome": "Yes", "indicative_price": "0.64"}
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
    # Fee-inclusive break-even: above the 0.66 fill price by the per-share fee.
    assert Decimal(position["average_price"]) > Decimal("0.66")
    assert Decimal(position["average_price"]) * 20 == Decimal(position["cost_basis"])
    assert position["outcome"] == "Yes"
    assert position["status"] == "open"
    assert payload["assumptions"]["scope"] == "persistent_account"


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


# --- agent-feedback improvements: estimate, fee formula, book age, time to end, compact, totals ---

MARKET_FIXTURE = ROOT / "fixtures/gamma_markets_keyset_page1.json"
SLUG = "will-the-example-event-happen-by-2030"


def live_book(age_seconds: float) -> dict:
    """The fixture book with a unix-millisecond provider timestamp ``age_seconds`` ago."""

    book = fixture("clob_book.json")
    book["timestamp"] = int((time.time() - age_seconds) * 1000)
    return book


def fixture_estimate(market_overrides=None, **kwargs):
    market = load_market_fixture(MARKET_FIXTURE)
    if market_overrides:
        market = replace(market, **market_overrides)
    return estimate_fill(load_book_fixture(ROOT / "fixtures/clob_book.json"), market, **kwargs)


def test_estimate_buy_walks_asks_with_per_level_fees():
    est = fixture_estimate(side="buy", size=Decimal(50))
    # 25 @ 0.66 + 25 @ 0.67; midpoint 0.64.
    assert est["fillable_size"] == "50" and est["fully_fillable"] is True
    assert est["notional"] == "33.25" and est["vwap"] == "0.665"
    assert est["worst_price"] == "0.67" and est["levels_used"] == 2
    assert est["slippage_vs_mid"] == "0.025"
    # fee = 0.05 * shares * p * (1 - p) per level, rounded to 5dp per level like the matcher.
    assert est["estimated_fee"] == "0.55688"
    assert est["immediate_mark_loss"] == "1.80688"  # 50 * 0.025 + fee
    assert est["warnings"] == []


def test_estimate_sell_walks_bids_and_respects_limit():
    est = fixture_estimate(side="sell", size=Decimal(100))
    # 40 @ 0.62 + 60 @ 0.61; slippage is mid - vwap for sells.
    assert est["vwap"] == "0.614" and est["worst_price"] == "0.61"
    assert est["slippage_vs_mid"] == "0.026"
    limited = fixture_estimate(side="sell", size=Decimal(100), price=Decimal("0.62"))
    assert limited["fillable_size"] == "40" and limited["fully_fillable"] is False
    assert limited["levels_used"] == 1 and "partial_fill" in limited["warnings"]
    none = fixture_estimate(side="buy", size=Decimal(10), price=Decimal("0.60"))
    assert none["fillable_size"] == "0" and none["vwap"] is None
    assert none["estimated_fee"] == "0" and "no_fill" in none["warnings"]


def test_estimate_validates_size_and_tick_and_fee_availability():
    small = fixture_estimate(side="buy", size=Decimal(2), price=Decimal("0.655"))
    assert {"below_min_order_size", "price_not_on_tick"} <= set(small["warnings"])
    free = fixture_estimate({"fees_enabled": False}, side="buy", size=Decimal(10))
    assert free["estimated_fee"] == "0" and "fees_disabled" in free["warnings"]
    unknown = fixture_estimate({"fee_schedule": None}, side="buy", size=Decimal(10))
    assert unknown["estimated_fee"] is None and "fee_schedule_unknown" in unknown["warnings"]
    assert "immediate_mark_loss_excludes_fee" in unknown["warnings"]
    with pytest.raises(LocalValidationError):
        fixture_estimate(side="hold", size=Decimal(1))
    with pytest.raises(LocalValidationError):
        fixture_estimate(side="buy", size=Decimal(0))
    with pytest.raises(LocalValidationError):
        fixture_estimate(side="buy", size=Decimal(1), price=Decimal("1.5"))


def test_quote_estimate_through_service_and_mcp(tmp_path: Path):
    service, _ = offline_service(tmp_path)
    quote = service.quote(SLUG, outcome="Yes", side="buy", size="50", price="0.67")
    assert quote["result"]["estimate"]["vwap"] == "0.665"
    assert quote["result"]["estimate"]["requested_size"] == "50"
    assert quote["result"]["estimate"]["limit_price"] == "0.67"
    assert "estimate" not in service.quote(SLUG, outcome="Yes")["result"]
    for kwargs in (
        {"side": "buy", "size": "5"},  # no outcome
        {"outcome": "Yes", "side": "buy"},  # no size
        {"outcome": "Yes", "size": "5"},  # no side
        {"outcome": "Yes", "side": "buy", "size": "abc"},
    ):
        with pytest.raises(LocalValidationError):
            service.quote(SLUG, **kwargs)
    mcp = dispatch_tool(
        "quote_market",
        {"market": SLUG, "outcome": "Yes", "side": "sell", "size": "10", "compact": True},
        service=service,
    )
    assert mcp["result"]["estimate"]["side"] == "sell"
    properties = next(t for t in TOOL_DEFINITIONS if t["name"] == "quote_market")["inputSchema"][
        "properties"
    ]
    assert {"side", "size", "price", "compact"} <= set(properties)


def test_cli_quote_accepts_estimate_and_compact_flags():
    from pm_trader.cli import _parser

    args = _parser().parse_args(
        ["quote", SLUG, "--outcome", "Yes", "--side", "buy", "--size", "50", "--price", "0.7",
         "--compact"]
    )
    assert (args.side, args.size, args.price, args.compact) == ("buy", Decimal(50), Decimal("0.7"), True)


def test_quote_exposes_fee_formula(tmp_path: Path):
    service, _ = offline_service(tmp_path)
    schedule = service.quote(SLUG)["result"]["market"]["fee_schedule"]
    assert "shares * rate * (price * (1 - price)) ** exponent" in schedule["formula"]
    assert "taker only" in schedule["formula"]
    legacy = replace(
        load_market_fixture(MARKET_FIXTURE).fee_schedule, formula="legacy_shares_rate_price_product"
    )
    from pm_trader.market_data import fee_formula

    assert fee_formula(legacy).startswith("fee = shares * rate * price * (1 - price);")


def test_book_age_and_freshness_in_quotes(tmp_path: Path):
    assert book_age_seconds("1000000", "1000030") == Decimal(30)
    assert book_age_seconds("1000000000000", "1000000030") == Decimal(30)  # unix milliseconds
    assert book_age_seconds("2026-01-01T00:00:00Z", "2026-01-01T00:00:05Z") == Decimal(5)
    assert book_age_seconds(None, "2026-01-01T00:00:05Z") is None
    assert book_age_seconds("garbage", "2026-01-01T00:00:05Z") is None
    assert worst_freshness(["fresh", "fresh"]) == "fresh"
    assert worst_freshness(["fresh", "stale"]) == "stale"
    assert worst_freshness(["stale", "unknown", "fresh"]) == "unknown"
    assert worst_freshness([]) == "unknown"

    fresh, _ = offline_service(tmp_path / "a", book=live_book(5))
    quote = fresh.quote(SLUG)
    assert quote["freshness"] == "fresh"
    for book in quote["result"]["books"]:
        assert book["freshness"] == "fresh"
        assert Decimal("4") < Decimal(book["age_seconds"]) < Decimal("20")
    stale, _ = offline_service(tmp_path / "b", book=live_book(120))
    assert stale.quote(SLUG)["freshness"] == "stale"
    assert stale.quote(SLUG, outcome="Yes")["result"]["books"][0]["freshness"] == "stale"
    # The checked-in fixture book is from 2026-08-26, so it is long stale by now.
    old, _ = offline_service(tmp_path / "c")
    assert old.quote(SLUG)["freshness"] == "stale"
    broken = replace(load_book_fixture(ROOT / "fixtures/clob_book.json"), provider_timestamp="n/a")
    summary = book_summary(broken)
    assert summary["age_seconds"] is None and summary["freshness"] == "unknown"


def test_hours_to_end_handles_dates_timestamps_and_unknowns():
    now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    assert hours_to_end("2026-11-14", now) == Decimal("12.0")  # end of that day, UTC
    assert hours_to_end("2026-11-15T12:00:00Z", now) == Decimal("24.0")
    assert hours_to_end("2026-11-14T11:30:00Z", now) == Decimal("-0.5")
    assert hours_to_end(str(int(now.timestamp()) + 7200), now) == Decimal("2.0")
    assert hours_to_end(str(int(now.timestamp()) * 1000 + 7_200_000), now) == Decimal("2.0")
    assert hours_to_end(None, now) is None and hours_to_end("soon", now) is None
    assert time_to_end("2026-11-14", now) == {"hours_to_end": "12.0", "ends_within_24h": True}
    assert time_to_end("2026-11-16", now)["ends_within_24h"] is False
    assert time_to_end("2026-11-14T11:30:00Z", now)["ends_within_24h"] is False  # already ended
    assert time_to_end(None, now) == {"hours_to_end": None, "ends_within_24h": False}


def test_discover_and_quote_rows_carry_time_to_resolution(tmp_path: Path):
    soon = (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    service, _ = offline_service(tmp_path / "a", endDateIso=soon)
    row = service.discover(limit=1)["result"]["markets"][0]
    assert "ends_within_24h" not in row  # scan rows stay compact; quote carries the flag
    assert Decimal("4.5") <= Decimal(row["hours_to_end"]) <= Decimal("5.0")
    quoted = service.quote(SLUG)["result"]["market"]
    assert quoted["ends_within_24h"] is True
    assert Decimal("4.5") <= Decimal(quoted["hours_to_end"]) <= Decimal("5.0")
    assert service.discover(limit=1)["assumptions"]["date_only_end_time_is_end_of_day_utc"] is True
    past, _ = offline_service(tmp_path / "b", endDateIso="2020-01-01")
    row = past.discover(limit=1)["result"]["markets"][0]
    assert Decimal(row["hours_to_end"]) < 0
    far, _ = offline_service(tmp_path / "c")
    row = far.quote(SLUG)["result"]["market"]
    assert Decimal(row["hours_to_end"]) > 24 and row["ends_within_24h"] is False


def test_compact_quote_drops_level_lists(tmp_path: Path):
    service, _ = offline_service(tmp_path, book=live_book(5))
    full = service.quote(SLUG)
    compact = service.quote(SLUG, compact=True)
    assert compact["freshness"] == full["freshness"] == "fresh"
    assert len(compact["result"]["books"]) == 2
    for book, source in zip(compact["result"]["books"], full["result"]["books"]):
        assert set(book) == {
            "outcome", "best_bid", "best_ask", "midpoint", "spread", "age_seconds", "depth_near_mid"
        }
        assert set(book["depth_near_mid"]) == {"within_1c"}
        assert book["depth_near_mid"]["within_1c"] == source["depth_near_mid"]["within_1c"]
        assert book["best_ask"] == "0.66" and book["spread"] == "0.04"
    assert compact["result"]["market"]["tick_size"] == "0.01"


def test_net_pnl_and_totals_for_unmarked_and_marked_positions():
    # Rows follow the account convention: fees inside cost basis, realized net of fees.
    a = {"asset_id": "a", "status": "open", "quantity": "6", "cost_basis": "2.46",
         "realized_pnl": "0.31", "fees": "0.15"}
    b = {"asset_id": "b", "status": "open", "quantity": "10", "cost_basis": "5.2",
         "realized_pnl": "0", "fees": "0.2"}
    unmarked = portfolio_totals([mark_position(a, None), mark_position(b, None)], marked=False)
    assert unmarked == {
        "cost_basis": "7.66", "market_value": None, "realized_pnl": "0.31",
        "unrealized_pnl": None, "fees": "0.35", "net_pnl": "0.31",
    }
    marked = [mark_position(a, Decimal("0.6")), mark_position(b, None)]
    assert marked[0]["unrealized_pnl"] == "1.14"  # 6 * 0.6 - 2.46
    assert marked[0]["net_pnl"] == "1.45"  # 0.31 + 1.14; fees already inside both
    assert marked[1]["net_pnl"] == "0"
    totals = portfolio_totals(marked, marked=True, cash=Decimal("100"))
    assert totals["market_value"] == "3.6" and totals["unrealized_pnl"] == "1.14"
    assert totals["net_pnl"] == "1.45" and totals["equity"] == "103.6"


def test_portfolio_totals_and_mark_freshness_end_to_end(tmp_path: Path):
    data_dir = tmp_path / "db"
    code, bought = run(
        [
            "--data-dir", str(data_dir), "simulate",
            "--market-fixture", str(MARKET_FIXTURE),
            "--book-fixture", str(ROOT / "fixtures/clob_book.json"),
            "--order-id", "t1", "--price", "0.66", "--size", "10", "--tif", "fak",
        ]
    )
    assert code == 0
    cost = Decimal("6.6") + Decimal(bought["result"]["summary"][0]["fees"])
    code, payload = run(["--data-dir", str(data_dir), "portfolio"])
    assert code == 0
    totals = payload["result"]["totals"]
    assert Decimal(totals["cost_basis"]) == cost and totals["market_value"] is None
    assert totals["net_pnl"] == payload["result"]["positions"][0]["net_pnl"]
    assert payload["assumptions"]["unrealized_pnl_is_midpoint_based_and_excludes_exit_fees"] is True
    assert payload["freshness"] == "unknown"

    for age, expected in ((5, "fresh"), (300, "stale")):
        clob = FixtureTransport({f"{CLOB_BASE}/book": live_book(age)})
        service = ResearchService(
            SQLiteStore(data_dir), gamma=GammaAdapter(transport=FixtureTransport({})),
            clob=CLOBAdapter(transport=clob),
        )
        marked = service.portfolio(mark=True)
        assert marked["freshness"] == expected
        [position] = marked["result"]["positions"]
        assert position["mark_price"] == "0.64"
        assert Decimal(position["unrealized_pnl"]) == Decimal("6.4") - cost
        assert Decimal(marked["result"]["totals"]["unrealized_pnl"]) == Decimal("6.4") - cost
        assert marked["result"]["totals"]["market_value"] == "6.4"
