from __future__ import annotations

from pathlib import Path

from pm_trader.cli import run
from pm_trader.mcp_server import dispatch_tool

ROOT = Path(__file__).parents[1]
MARKET = str(ROOT / "fixtures/gamma_markets_keyset_page1.json")
BOOK = str(ROOT / "fixtures/clob_book.json")


def simulate(data_dir: Path, *extra: str):
    return run(
        ["--data-dir", str(data_dir), "simulate", "--market-fixture", MARKET, "--book-fixture", BOOK, *extra]
    )


def test_account_carries_cash_and_positions_across_runs(tmp_path: Path):
    code, bought = simulate(tmp_path, "--order-id", "b1", "--price", "0.67", "--size", "40", "--tif", "fak")
    assert code == 0
    assert bought["result"]["summary"][0]["outcome"] == "filled"
    cash_after_buy = bought["result"]["account"]["cash"]
    assert cash_after_buy == bought["result"]["report"]["cash"]

    code, sold = simulate(
        tmp_path, "--order-id", "s1", "--side", "sell", "--price", "0.62", "--size", "10", "--tif", "fak"
    )
    assert code == 0, sold["errors"]
    summary = sold["result"]["summary"][0]
    assert summary["outcome"] == "filled"
    assert summary["average_price"] == "0.62"
    account = sold["result"]["account"]
    [position] = account["open_positions"]
    assert position["outcome"] == "Yes"
    assert position["market_slug"] == "will-the-example-event-happen-by-2030"
    assert position["quantity"] == "30"
    assert account["runs"] == 2
    # The second run opened with the first run's ending cash, not a fresh deposit.
    assert sold["result"]["report"]["starting_cash"] == cash_after_buy
    assert account["cash"] == sold["result"]["report"]["cash"]
    assert sold["assumptions"]["mode"] == "account"


def test_rejected_order_is_not_ok_and_exits_nonzero(tmp_path: Path):
    code, payload = simulate(
        tmp_path, "--order-id", "s0", "--side", "sell", "--price", "0.62", "--size", "10", "--tif", "fak"
    )
    assert code == 1
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "INSUFFICIENT_POSITION"
    assert payload["errors"][0]["order_id"] == "s0"
    assert payload["result"]["summary"][0]["outcome"] == "rejected"

    code, payload = simulate(tmp_path, "--order-id", "t0", "--price", "0.665", "--size", "10")
    assert code == 1
    assert payload["errors"][0]["code"] == "TICK_SIZE_VIOLATION"


def test_isolated_runs_ignore_the_account(tmp_path: Path):
    simulate(tmp_path, "--order-id", "b1", "--price", "0.67", "--size", "40", "--tif", "fak")
    code, payload = simulate(
        tmp_path, "--isolated", "--order-id", "s1", "--side", "sell", "--price", "0.62", "--size", "10", "--tif", "fak"
    )
    assert code == 1
    assert payload["assumptions"]["mode"] == "isolated_run"
    assert "account" not in payload["result"]
    # The isolated attempt did not change the account.
    code, again = simulate(
        tmp_path, "--order-id", "s2", "--side", "sell", "--price", "0.62", "--size", "40", "--tif", "fak"
    )
    assert code == 0, again["errors"]
    assert again["result"]["account"]["open_positions"] == []


def test_mcp_simulate_uses_account_by_default(tmp_path: Path):
    args = {"market_fixture": MARKET, "book_fixture": BOOK, "data_dir": str(tmp_path), "time_in_force": "fak"}
    first = dispatch_tool("simulate", {**args, "order_id": "m1", "price": "0.67", "size": "20"})
    assert first["ok"] is True
    second = dispatch_tool(
        "simulate", {**args, "order_id": "m2", "side": "sell", "price": "0.62", "size": "20"}
    )
    assert second["ok"] is True, second["errors"]
    assert second["result"]["account"]["open_positions"] == []


def test_portfolio_reports_account_cash(tmp_path: Path):
    _, bought = simulate(tmp_path, "--order-id", "b1", "--price", "0.67", "--size", "40", "--tif", "fak")
    code, payload = run(["--data-dir", str(tmp_path), "portfolio"])
    assert code == 0
    assert payload["result"]["cash"] == bought["result"]["account"]["cash"]


def _resolved_fixture(tmp_path: Path, prices: list[str], closed: bool = True) -> str:
    import json

    raw = json.loads(Path(MARKET).read_text(encoding="utf-8"))
    raw["markets"][0]["closed"] = closed
    raw["markets"][0]["outcomePrices"] = prices
    path = tmp_path / f"resolved-{'-'.join(prices)}-{closed}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return str(path)


def test_settle_pays_out_winning_position_once(tmp_path: Path):
    from decimal import Decimal

    data = tmp_path / "db"
    _, bought = simulate(data, "--order-id", "b1", "--price", "0.67", "--size", "40", "--tif", "fak")
    cash_before = Decimal(bought["result"]["account"]["cash"])
    [held] = bought["result"]["account"]["open_positions"]

    code, open_market = run(
        ["--data-dir", str(data), "settle", "--market-fixture", _resolved_fixture(tmp_path, ["0.6", "0.4"], closed=False)]
    )
    assert code == 1 and open_market["errors"][0]["code"] == "MARKET_NOT_RESOLVED"

    code, settled = run(
        ["--data-dir", str(data), "settle", "--market-fixture", _resolved_fixture(tmp_path, ["1", "0"])]
    )
    assert code == 0, settled["errors"]
    [row] = settled["result"]["settled"]
    assert row["outcome"] == "Yes" and row["payout_per_share"] == "1" and row["proceeds"] == "40"
    assert Decimal(row["realized_pnl"]) == Decimal(40) - Decimal(held["cost_basis"])
    account = settled["result"]["account"]
    assert account["open_positions"] == []
    assert Decimal(account["cash"]) == cash_before + 40

    code, again = run(
        ["--data-dir", str(data), "settle", "--market-fixture", _resolved_fixture(tmp_path, ["1", "0"])]
    )
    assert code == 0 and again["result"]["settled"] == []
    assert again["warnings"] == ["no_open_account_position_in_market"]

    code, portfolio = run(["--data-dir", str(data), "portfolio"])
    [position] = portfolio["result"]["positions"]
    assert position["status"] == "settled" and position["quantity"] == "0"
    assert Decimal(position["realized_pnl"]) == Decimal(row["realized_pnl"])
    assert Decimal(portfolio["result"]["cash"]) == cash_before + 40


def test_losing_settlement_and_rejected_runs_do_not_count(tmp_path: Path):
    from decimal import Decimal

    data = tmp_path / "db"
    _, bought = simulate(data, "--order-id", "b1", "--price", "0.67", "--size", "10", "--tif", "fak")
    code, _ = simulate(data, "--order-id", "x1", "--side", "sell", "--price", "0.62", "--size", "99", "--tif", "fak")
    assert code == 1
    code, settled = run(
        ["--data-dir", str(data), "settle", "--market-fixture", _resolved_fixture(tmp_path, ["0", "1"])]
    )
    assert code == 0
    [row] = settled["result"]["settled"]
    assert row["proceeds"] == "0"
    account = settled["result"]["account"]
    # The rejected oversell changed nothing, so only the buy and the settlement count.
    assert account["runs"] == 2
    assert Decimal(account["cash"]) == Decimal(bought["result"]["account"]["cash"])


def test_market_lookup_falls_back_to_closed_markets(tmp_path: Path):
    import json

    from pm_trader.api import GAMMA_BASE, CLOBAdapter, FixtureTransport, GammaAdapter, TransportResponse
    from pm_trader.storage import SQLiteStore
    from pm_trader.workflows import ResearchService

    page = json.loads(Path(MARKET).read_text(encoding="utf-8"))
    page["markets"][0].update(closed=True, outcomePrices=["1", "0"])

    class ClosedOnly(FixtureTransport):
        def get(self, url, *, params=None, timeout=10.0):
            self.requests.append((url, dict(params or {})))
            data = page if (params or {}).get("closed") == "true" else {"markets": []}
            return TransportResponse(status_code=200, data=data, url=url)

    transport = ClosedOnly({})
    service = ResearchService(
        SQLiteStore(tmp_path / "db"), gamma=GammaAdapter(transport=transport), clob=CLOBAdapter(transport=FixtureTransport({}))
    )
    market = service._market("will-the-example-event-happen-by-2030")
    assert market.closed is True
    assert [params.get("closed") for _, params in transport.requests] == [None, "true"]
    settled = service.settle(market)
    assert settled["assumptions"]["payouts"] == {"Yes": "1", "No": "0"}


def test_account_report_states_coverage_instead_of_warning_for_untouched_positions(tmp_path: Path):
    import json

    raw = json.loads(Path(MARKET).read_text(encoding="utf-8"))
    other = json.loads(json.dumps(raw))
    market = other["markets"][0]
    market["slug"] = "other-market"
    market["conditionId"] = "0xother-001"
    market["clobTokenIds"] = ["111", "222"]
    other_path = tmp_path / "other.json"
    other_path.write_text(json.dumps(other), encoding="utf-8")
    book = json.loads(Path(BOOK).read_text(encoding="utf-8"))
    book["asset_id"] = "111"
    book["market"] = "0xother-001"
    book_path = tmp_path / "other-book.json"
    book_path.write_text(json.dumps(book), encoding="utf-8")

    data = tmp_path / "db"
    simulate(data, "--order-id", "b1", "--price", "0.67", "--size", "40", "--tif", "fak")
    code, second = run(
        ["--data-dir", str(data), "simulate", "--market-fixture", str(other_path),
         "--book-fixture", str(book_path), "--order-id", "b2", "--price", "0.67", "--size", "10", "--tif", "fak"]
    )
    assert code == 0, second["errors"]
    report = second["result"]["report"]
    assert not [w for w in report["warnings"] if w.startswith("unrealized_pnl_unknown")]
    coverage = report["data_coverage"]
    assert coverage["unrealized_pnl_covers"] == "marked_positions_only"
    assert len(coverage["unmarked_carried_positions"]) == 1
    assert coverage["full_account_view"] == "portfolio --mark"
    assert report["uncertainty"]["unrealized_pnl"] == "marked_positions_only"

    # Trading the first position again marks it; only the other one stays unmarked.
    _, third = simulate(data, "--order-id", "b3", "--price", "0.67", "--size", "5", "--tif", "fak")
    assert third["result"]["report"]["data_coverage"]["unmarked_carried_positions"] == ["111"]
    assert not [w for w in third["result"]["report"]["warnings"] if w.startswith("unrealized_pnl_unknown")]


def test_isolated_report_has_no_coverage_fields(tmp_path: Path):
    _, payload = simulate(tmp_path, "--isolated", "--order-id", "i1", "--price", "0.67", "--size", "5", "--tif", "fak")
    assert "unmarked_carried_positions" not in payload["result"]["report"]["data_coverage"]
