"""Offline compatibility backtesting helpers.

For recorded full-depth replay use :class:`pm_trader.recording.ReplayEngine`.
This module preserves the original midpoint-CSV helper and labels its
synthetic books as a convenience approximation rather than recorded depth.
"""

from __future__ import annotations

import csv
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .analytics import compute_stats
from .engine import Engine
from .models import Market, OrderBook, OrderBookLevel


@dataclass
class PriceSnapshot:
    timestamp: str
    market_slug: str
    outcome: str
    midpoint: float


@dataclass
class BacktestResult:
    strategy: str
    starting_balance: float
    ending_cash: float
    total_trades: int
    pnl: float
    roi_pct: float
    sharpe_ratio: float
    win_rate: float
    max_drawdown: float
    snapshots_processed: int


def load_snapshots_csv(path: Path) -> list[PriceSnapshot]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            PriceSnapshot(
                timestamp=row["timestamp"].strip(),
                market_slug=row["market_slug"].strip(),
                outcome=row["outcome"].strip().casefold(),
                midpoint=float(row["midpoint"]),
            )
            for row in csv.DictReader(handle)
        ]


def load_snapshots_json(path: Path) -> list[PriceSnapshot]:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise TypeError("backtest JSON must contain an array")
    return [
        PriceSnapshot(
            timestamp=str(value["timestamp"]),
            market_slug=str(value["market_slug"]),
            outcome=str(value["outcome"]).casefold(),
            midpoint=float(value["midpoint"]),
        )
        for value in values
    ]


def _build_synthetic_book(midpoint: float, spread: float = 0.02, depth: float = 500.0) -> OrderBook:
    half_spread = spread / 2
    ask = min(midpoint + half_spread, 0.99)
    bid = max(midpoint - half_spread, 0.01)
    return OrderBook(
        asks=[
            OrderBookLevel(round(min(ask + offset, 0.99), 4), depth) for offset in (0, 0.01, 0.02)
        ],
        bids=[
            OrderBookLevel(round(max(bid - offset, 0.01), 4), depth) for offset in (0, 0.01, 0.02)
        ],
    )


def run_backtest(
    snapshots: list[PriceSnapshot],
    strategy: Callable[[Engine, PriceSnapshot, dict[str, float]], None],
    strategy_name: str = "unnamed",
    balance: float = 10_000.0,
    spread: float = 0.02,
    depth: float = 500.0,
) -> BacktestResult:
    """Run a strategy over explicitly supplied midpoint observations.

    The API is patched with local synthetic records for the duration of the
    run. No network request is made, and the resulting metrics are paper-only.
    """

    with tempfile.TemporaryDirectory(prefix="pm-trader-backtest-") as directory:
        engine = Engine(Path(directory))
        engine.init_account(balance)
        latest: dict[str, float] = {}
        try:
            for snapshot in snapshots:
                latest[f"{snapshot.market_slug}:{snapshot.outcome}"] = snapshot.midpoint
                market = Market(
                    condition_id=f"backtest:{snapshot.market_slug}",
                    slug=snapshot.market_slug,
                    question=snapshot.market_slug,
                    description="synthetic backtest market",
                    outcomes=[snapshot.outcome],
                    outcome_prices=[snapshot.midpoint],
                    tokens=[
                        {
                            "token_id": f"{snapshot.market_slug}:{snapshot.outcome}",
                            "outcome": snapshot.outcome,
                        }
                    ],
                    active=True,
                    closed=False,
                    fee_rate_bps=0,
                    tick_size=0.01,
                )
                engine.api.get_market = lambda _identifier, value=market: value
                engine.api.get_order_book = lambda _token, mid=snapshot.midpoint: (
                    _build_synthetic_book(mid, spread, depth)
                )
                engine.api.get_midpoint = lambda _token, mid=snapshot.midpoint: mid
                engine.api.get_fee_rate = lambda _token: 0
                try:
                    strategy(engine, snapshot, dict(latest))
                except (
                    RuntimeError,
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                    AssertionError,
                ):
                    pass
            account = engine.get_account()
            trades = engine.db.get_trades(limit=100_000)
            stats = compute_stats(trades, account)
            return BacktestResult(
                strategy=strategy_name,
                starting_balance=balance,
                ending_cash=account.cash,
                total_trades=len(trades),
                pnl=account.cash - balance,
                roi_pct=(account.cash - balance) / balance * 100 if balance else 0.0,
                sharpe_ratio=float(stats["sharpe_ratio"]),
                win_rate=float(stats["win_rate"]),
                max_drawdown=float(stats["max_drawdown"]),
                snapshots_processed=len(snapshots),
            )
        finally:
            engine.close()


__all__ = [
    "BacktestResult",
    "PriceSnapshot",
    "load_snapshots_csv",
    "load_snapshots_json",
    "run_backtest",
]
