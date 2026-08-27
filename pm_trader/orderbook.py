"""Pure legacy order-book helpers retained for the original Python surface.

The canonical implementation lives in :mod:`pm_trader.matching`. These
helpers accept the original float-based ``OrderBook`` record and perform only
paper calculations; they never contact a provider or mutate storage.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import FillResult, OrderBook, OrderBookLevel


@dataclass
class Fill:
    """One compatibility fill produced while walking a legacy book."""

    price: float
    shares: float
    cost: float
    level: int


def calculate_fee(fee_rate_bps: int, price: float, size: float) -> float:
    """Calculate the original basis-point compatibility fee.

    New simulations should use :func:`pm_trader.fees.calculate_fee`, which
    carries a versioned schedule and returns an explicit result record.
    """

    if fee_rate_bps < 0:
        raise ValueError("fee_rate_bps cannot be negative")
    if fee_rate_bps == 0 or size <= 0:
        return 0.0
    fee = (fee_rate_bps / 10_000) * min(price, 1.0 - price) * size
    return max(fee, 0.0001) if fee > 0 else 0.0


def _midpoint(book: OrderBook) -> float | None:
    if not book.bids or not book.asks:
        return None
    return (max(level.price for level in book.bids) + min(level.price for level in book.asks)) / 2


def _empty_fill_result() -> FillResult:
    return FillResult(
        filled=False,
        avg_price=0.0,
        total_cost=0.0,
        total_shares=0.0,
        fee=0.0,
        slippage_bps=0.0,
        levels_filled=0,
        is_partial=False,
        fills=[],
    )


def _validate_order_type(order_type: str) -> str:
    normalized = order_type.casefold()
    if normalized not in {"fok", "fak"}:
        raise ValueError("compatibility order_type must be 'fok' or 'fak'")
    return normalized


def simulate_buy_fill(
    book: OrderBook,
    amount_usd: float,
    fee_rate_bps: int,
    order_type: str = "fok",
    max_price: float | None = None,
) -> FillResult:
    """Walk asks to buy up to ``amount_usd`` before fees."""

    order_type = _validate_order_type(order_type)
    if amount_usd <= 0 or not book.asks:
        return _empty_fill_result()
    remaining_usd = amount_usd
    fills: list[Fill] = []
    for level_index, level in enumerate(sorted(book.asks, key=lambda item: item.price), 1):
        if remaining_usd <= 0:
            break
        if max_price is not None and level.price > max_price:
            break
        level_cost = level.price * level.size
        shares = level.size if level_cost <= remaining_usd else remaining_usd / level.price
        cost = shares * level.price
        fills.append(Fill(level.price, shares, cost, level_index))
        remaining_usd -= cost

    if not fills or (order_type == "fok" and remaining_usd > 1e-12):
        return _empty_fill_result()
    total_cost = sum(fill.cost for fill in fills)
    total_shares = sum(fill.shares for fill in fills)
    avg_price = total_cost / total_shares if total_shares else 0.0
    midpoint = _midpoint(book)
    slippage = ((avg_price - midpoint) / midpoint * 10_000) if midpoint else 0.0
    return FillResult(
        filled=remaining_usd <= 1e-12,
        avg_price=avg_price,
        total_cost=total_cost,
        total_shares=total_shares,
        fee=calculate_fee(fee_rate_bps, avg_price, total_cost),
        slippage_bps=slippage,
        levels_filled=len(fills),
        is_partial=remaining_usd > 1e-12,
        fills=fills,
    )


def simulate_sell_fill(
    book: OrderBook,
    shares: float,
    fee_rate_bps: int,
    order_type: str = "fok",
    min_price: float | None = None,
) -> FillResult:
    """Walk bids to sell up to ``shares``."""

    order_type = _validate_order_type(order_type)
    if shares <= 0 or not book.bids:
        return _empty_fill_result()
    remaining_shares = shares
    fills: list[Fill] = []
    for level_index, level in enumerate(
        sorted(book.bids, key=lambda item: item.price, reverse=True), 1
    ):
        if remaining_shares <= 0:
            break
        if min_price is not None and level.price < min_price:
            break
        matched = min(level.size, remaining_shares)
        fills.append(Fill(level.price, matched, matched * level.price, level_index))
        remaining_shares -= matched

    if not fills or (order_type == "fok" and remaining_shares > 1e-12):
        return _empty_fill_result()
    total_cost = sum(fill.cost for fill in fills)
    total_shares = sum(fill.shares for fill in fills)
    avg_price = total_cost / total_shares if total_shares else 0.0
    midpoint = _midpoint(book)
    slippage = ((avg_price - midpoint) / midpoint * 10_000) if midpoint else 0.0
    return FillResult(
        filled=remaining_shares <= 1e-12,
        avg_price=avg_price,
        total_cost=total_cost,
        total_shares=total_shares,
        fee=calculate_fee(fee_rate_bps, avg_price, total_shares),
        slippage_bps=slippage,
        levels_filled=len(fills),
        is_partial=remaining_shares > 1e-12,
        fills=fills,
    )


__all__ = [
    "Fill",
    "FillResult",
    "OrderBook",
    "OrderBookLevel",
    "calculate_fee",
    "simulate_buy_fill",
    "simulate_sell_fill",
]
