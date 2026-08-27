from __future__ import annotations

from pathlib import Path

from pm_trader.engine import Engine
from pm_trader.models import Market, OrderBook, OrderBookLevel
from pm_trader.orderbook import simulate_buy_fill, simulate_sell_fill
from pm_trader.orders import create_order, expire_orders, init_orders_schema


def legacy_market() -> Market:
    return Market(
        condition_id="0xcompatibility",
        slug="compatibility-market",
        question="Will the compatibility path work?",
        description="offline fixture",
        outcomes=["yes", "no"],
        outcome_prices=[0.66, 0.34],
        tokens=[
            {"token_id": "compat-yes", "outcome": "yes"},
            {"token_id": "compat-no", "outcome": "no"},
        ],
        active=True,
        closed=False,
    )


def legacy_book() -> OrderBook:
    return OrderBook(
        bids=[OrderBookLevel(0.64, 100)],
        asks=[OrderBookLevel(0.66, 100)],
    )


def test_legacy_orderbook_helpers_remain_pure_and_usable():
    buy = simulate_buy_fill(legacy_book(), 50, fee_rate_bps=0)
    sell = simulate_sell_fill(legacy_book(), 50, fee_rate_bps=0)
    assert buy.filled is True
    assert buy.total_shares == 50 / 0.66
    assert sell.filled is True
    assert sell.total_cost == 32


def test_legacy_limit_order_helpers_preserve_gtd_lifecycle(tmp_path: Path):
    engine = Engine(tmp_path)
    try:
        init_orders_schema(engine.db.conn)
        order = create_order(
            engine.db.conn,
            market_slug="compatibility-market",
            market_condition_id="0xcompatibility",
            outcome="yes",
            side="buy",
            amount=20,
            limit_price=0.65,
            order_type="gtd",
            expires_at="2020-01-01T00:00:00Z",
        )
        expired = expire_orders(engine.db.conn, now="2026-08-26T00:00:00Z")
        assert expired[0].id == order.id
        assert expired[0].status == "expired"
    finally:
        engine.close()


class FakeAPI:
    def __init__(self) -> None:
        self.market = legacy_market()
        self.book = legacy_book()

    def close(self) -> None:
        pass

    def get_market(self, _identifier: str) -> Market:
        return self.market

    def get_order_book(self, _token_id: str) -> OrderBook:
        return self.book

    def get_fee_rate(self, _token_id: str) -> int:
        return 0

    def get_midpoint(self, _token_id: str) -> float:
        return 0.65


def test_legacy_engine_facade_is_paper_only(tmp_path: Path):
    engine = Engine(tmp_path, api=FakeAPI())
    try:
        engine.init_account(100)
        bought = engine.buy("compatibility-market", "YES", 10)
        assert bought.trade.shares > 0
        assert engine.get_balance()["cash"] < 100
        sold = engine.sell("compatibility-market", "yes", bought.trade.shares)
        assert sold.trade.side == "sell"
        assert engine.get_history()
    finally:
        engine.close()
