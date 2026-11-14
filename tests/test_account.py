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
    [position] = account["open_positions"].values()
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
    assert again["result"]["account"]["open_positions"] == {}


def test_mcp_simulate_uses_account_by_default(tmp_path: Path):
    args = {"market_fixture": MARKET, "book_fixture": BOOK, "data_dir": str(tmp_path), "time_in_force": "fak"}
    first = dispatch_tool("simulate", {**args, "order_id": "m1", "price": "0.67", "size": "20"})
    assert first["ok"] is True
    second = dispatch_tool(
        "simulate", {**args, "order_id": "m2", "side": "sell", "price": "0.62", "size": "20"}
    )
    assert second["ok"] is True, second["errors"]
    assert second["result"]["account"]["open_positions"] == {}
