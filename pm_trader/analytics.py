"""Pure analytics helpers for compatibility account reports.

Canonical simulations expose their accounting in ``SimulationReport``. These
functions retain the original trade-history API for callers that still use
``Engine.get_balance`` or export legacy trade records.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .models import Account, Trade


def compute_stats(
    trades: list[Trade], account: Account, positions_value: float = 0.0
) -> dict[str, float | int]:
    total_value = account.cash + positions_value
    pnl = total_value - account.starting_balance
    chronological = list(reversed(trades))
    return {
        "starting_balance": account.starting_balance,
        "cash": account.cash,
        "positions_value": positions_value,
        "total_value": total_value,
        "pnl": pnl,
        "roi_pct": pnl / account.starting_balance * 100 if account.starting_balance else 0.0,
        "total_trades": len(trades),
        "buy_count": sum(1 for trade in trades if trade.side == "buy"),
        "sell_count": sum(1 for trade in trades if trade.side == "sell"),
        "win_rate": win_rate(trades),
        "sharpe_ratio": sharpe_ratio(chronological, account.starting_balance),
        "max_drawdown": max_drawdown(chronological, account.starting_balance),
        "total_fees": sum(trade.fee for trade in trades),
        "avg_trade_size": _avg_trade_size(trades),
    }


def win_rate(trades: list[Trade]) -> float:
    sells = [trade for trade in trades if trade.side == "sell"]
    if not sells:
        return 0.0
    buy_cost: dict[tuple[str, str], float] = defaultdict(float)
    buy_shares: dict[tuple[str, str], float] = defaultdict(float)
    for trade in trades:
        if trade.side == "buy":
            key = (trade.market_condition_id, trade.outcome)
            buy_cost[key] += trade.amount_usd
            buy_shares[key] += trade.shares
    wins = 0
    for trade in sells:
        key = (trade.market_condition_id, trade.outcome)
        entry_price = (
            buy_cost[key] / buy_shares[key] if buy_shares.get(key, 0.0) > 0 else trade.avg_price
        )
        wins += trade.avg_price > entry_price
    return wins / len(sells)


def sharpe_ratio(
    trades_chronological: list[Trade], starting_balance: float, annualize_days: int = 365
) -> float:
    daily_pnl = _daily_pnl(trades_chronological)
    if len(daily_pnl) < 2:
        return 0.0
    cumulative = starting_balance
    returns: list[float] = []
    for pnl in daily_pnl:
        returns.append(pnl / cumulative if cumulative > 0 else 0.0)
        cumulative += pnl
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    deviation = math.sqrt(variance)
    return mean / deviation * math.sqrt(annualize_days) if deviation else 0.0


def max_drawdown(trades_chronological: list[Trade], starting_balance: float) -> float:
    if not trades_chronological:
        return 0.0
    cumulative = starting_balance
    peak = cumulative
    drawdown = 0.0
    for trade in trades_chronological:
        cumulative += (
            -(trade.amount_usd + trade.fee) if trade.side == "buy" else trade.amount_usd - trade.fee
        )
        peak = max(peak, cumulative)
        if peak > 0:
            drawdown = max(drawdown, (peak - cumulative) / peak)
    return drawdown


def _daily_pnl(trades_chronological: list[Trade]) -> list[float]:
    by_date: dict[str, float] = defaultdict(float)
    for trade in trades_chronological:
        if trade.side == "buy":
            by_date[trade.created_at[:10]] -= trade.amount_usd + trade.fee
        else:
            by_date[trade.created_at[:10]] += trade.amount_usd - trade.fee
    return [by_date[date] for date in sorted(by_date)]


def _avg_trade_size(trades: list[Trade]) -> float:
    return sum(trade.amount_usd for trade in trades) / len(trades) if trades else 0.0


__all__ = ["compute_stats", "max_drawdown", "sharpe_ratio", "win_rate"]
