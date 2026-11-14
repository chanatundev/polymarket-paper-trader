"""Compact, decision-ready summaries of markets, books, price history, and stored fills.

These helpers turn normalized provider state into the small numbers an agent needs to
choose a paper order: prices per outcome, top-of-book, depth, spread, recent price
movement, and current paper exposure. They never create orders or call providers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import BookLevel, BookSnapshot, MarketState, PricePoint

# Gamma market fields (kept in ``unknown_fields``) that help rank and price a market.
_MARKET_STAT_FIELDS = {
    "volume_24hr": "volume24hr",
    "volume_1wk": "volume1wk",
    "volume": "volumeClob",
    "liquidity": "liquidityClob",
    "best_bid": "bestBid",
    "best_ask": "bestAsk",
    "spread": "spread",
    "last_trade_price": "lastTradePrice",
    "one_day_price_change": "oneDayPriceChange",
    "one_week_price_change": "oneWeekPriceChange",
}
_STAT_FALLBACKS = {"volume": "volume", "liquidity": "liquidity"}


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _iso(value: Any) -> Any:
    """Render unix seconds or milliseconds as an ISO-8601 UTC string; leave other text alone."""

    number = _dec(value)
    if number is None:
        return value
    seconds = number / 1000 if number > 100_000_000_000 else number
    try:
        moment = datetime.fromtimestamp(float(seconds), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return value
    return moment.isoformat().replace("+00:00", "Z")


def market_outcomes(market: MarketState, *, token_ids: bool = False) -> list[dict[str, Any]]:
    """Outcome label and indicative Gamma price for every token (token ids on request)."""

    rows = []
    for index, token in enumerate(market.tokens):
        price = market.outcome_prices[index] if index < len(market.outcome_prices) else None
        row: dict[str, Any] = {"outcome": token.outcome, "price": _text(price)}
        if token_ids:
            row["token_id"] = token.token_id
        rows.append(row)
    return rows


def market_stats(market: MarketState) -> dict[str, str]:
    """Volume, liquidity, and top-of-book hints reported by Gamma; absent fields are omitted."""

    fields = market.unknown_fields or {}
    stats: dict[str, str] = {}
    for name, key in _MARKET_STAT_FIELDS.items():
        value = _dec(fields.get(key))
        if value is None and name in _STAT_FALLBACKS:
            value = _dec(fields.get(_STAT_FALLBACKS[name]))
        if value is not None:
            if name.startswith(("volume", "liquidity")):
                value = value.quantize(Decimal("0.01"))
            stats[name] = _text(value)
    return stats


def market_trading_rules(market: MarketState) -> dict[str, Any]:
    return {
        "tick_size": _text(market.tick_size),
        "min_order_size": _text(market.min_order_size),
        "neg_risk": market.neg_risk,
        "fees_enabled": market.fees_enabled,
        "fee_schedule": None
        if market.fee_schedule is None
        else {
            "rate": _text(market.fee_schedule.rate),
            "exponent": _text(market.fee_schedule.exponent),
            "taker_only": market.fee_schedule.taker_only,
            "rebate_rate": _text(market.fee_schedule.rebate_rate),
        },
    }


def _levels(levels: Iterable[BookLevel], *, best_first_descending: bool) -> list[BookLevel]:
    return sorted(levels, key=lambda level: level.price, reverse=best_first_descending)


def _depth(levels: Sequence[BookLevel]) -> dict[str, str]:
    shares = sum((level.size for level in levels), Decimal(0))
    notional = sum((level.size * level.price for level in levels), Decimal(0))
    return {"shares": _text(shares), "notional": _text(notional)}


def _within(levels: Sequence[BookLevel], reference: Decimal, width: Decimal, *, bids: bool):
    if bids:
        return [level for level in levels if level.price >= reference - width]
    return [level for level in levels if level.price <= reference + width]


def book_summary(book: BookSnapshot, *, depth: int = 5) -> dict[str, Any]:
    """Best-first top levels plus spread, midpoint, and depth near the touch."""

    bids = _levels(book.bids, best_first_descending=True)
    asks = _levels(book.asks, best_first_descending=False)
    best_bid = bids[0].price if bids else None
    best_ask = asks[0].price if asks else None
    midpoint = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    summary: dict[str, Any] = {
        "asset_id": book.asset_id,
        "best_bid": _text(best_bid),
        "best_ask": _text(best_ask),
        "midpoint": _text(midpoint),
        "spread": _text(spread),
        "tick_size": _text(book.tick_size),
        "min_order_size": _text(book.min_order_size),
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "bids": [{"price": _text(l.price), "size": _text(l.size)} for l in bids[:depth]],
        "asks": [{"price": _text(l.price), "size": _text(l.size)} for l in asks[:depth]],
        "provider_timestamp": _iso(book.provider_timestamp),
        "received_at": book.received_at,
        "freshness": book.freshness.value,
    }
    if midpoint is not None:
        # Liquidity a marketable order can reach within a few cents of the midpoint.
        summary["depth_near_mid"] = {
            f"within_{cents}c": {
                "bids": _depth(_within(bids, midpoint, Decimal(cents) / 100, bids=True)),
                "asks": _depth(_within(asks, midpoint, Decimal(cents) / 100, bids=False)),
            }
            for cents in (1, 5)
        }
    return summary


def price_summary(
    points: Sequence[PricePoint], *, max_points: int = 120
) -> dict[str, Any]:
    """Open/close/high/low/change for a history plus an evenly downsampled series."""

    if not points:
        return {"count": 0, "points": []}
    prices = [point.price for point in points]
    first, last = prices[0], prices[-1]
    step = max(1, -(-len(points) // max(1, max_points)))
    sampled = list(points[::step])
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    return {
        "count": len(points),
        "start": _iso(points[0].timestamp),
        "end": _iso(points[-1].timestamp),
        "open": _text(first),
        "close": _text(last),
        "high": _text(max(prices)),
        "low": _text(min(prices)),
        "change": _text(last - first),
        "returned_points": len(sampled),
        "points": [{"t": _iso(point.timestamp), "p": _text(point.price)} for point in sampled],
    }


def aggregate_positions(
    fills: Iterable[Mapping[str, Any]], orders: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Average-cost net position per asset across every stored paper fill."""

    book: dict[str, dict[str, Any]] = {}
    for fill in fills:
        asset = str(fill.get("asset_id"))
        quantity = _dec(fill.get("quantity")) or Decimal(0)
        price = _dec(fill.get("price")) or Decimal(0)
        fee = _dec(fill.get("fee")) or Decimal(0)
        order = orders.get((str(fill.get("run_id")), str(fill.get("order_id")))) or {}
        metadata = order.get("metadata") if isinstance(order.get("metadata"), Mapping) else {}
        row = book.setdefault(
            asset,
            {
                "asset_id": asset,
                "market_id": order.get("market_id"),
                "market_question": metadata.get("market_question"),
                "market_slug": metadata.get("market_slug"),
                "outcome": metadata.get("outcome"),
                "quantity": Decimal(0),
                "cost_basis": Decimal(0),
                "realized_pnl": Decimal(0),
                "fees": Decimal(0),
                "fills": 0,
            },
        )
        row["fills"] += 1
        row["fees"] += fee
        if fill.get("side") == "buy":
            row["quantity"] += quantity
            row["cost_basis"] += quantity * price
        else:
            held = row["quantity"]
            average = row["cost_basis"] / held if held else Decimal(0)
            closed = min(quantity, held) if held > 0 else Decimal(0)
            row["realized_pnl"] += closed * (price - average)
            row["quantity"] -= quantity
            row["cost_basis"] -= closed * average
    rows = []
    for row in book.values():
        quantity = row["quantity"]
        rows.append(
            {
                **row,
                "quantity": _text(quantity),
                "average_price": _text(row["cost_basis"] / quantity) if quantity > 0 else None,
                "cost_basis": _text(row["cost_basis"]),
                "realized_pnl": _text(row["realized_pnl"]),
                "fees": _text(row["fees"]),
            }
        )
    rows.sort(key=lambda item: (_dec(item["quantity"]) or Decimal(0)) == 0)
    return rows


def mark_position(row: dict[str, Any], midpoint: Decimal | None) -> dict[str, Any]:
    quantity = _dec(row.get("quantity")) or Decimal(0)
    if midpoint is None or quantity <= 0:
        return {**row, "mark_price": _text(midpoint), "market_value": None, "unrealized_pnl": None}
    value = quantity * midpoint
    cost = _dec(row.get("cost_basis")) or Decimal(0)
    return {
        **row,
        "mark_price": _text(midpoint),
        "market_value": _text(value),
        "unrealized_pnl": _text(value - cost),
    }


def load_stored_fills(conn: Any, *, limit: int) -> tuple[list[dict[str, Any]], dict, bool]:
    """Read at most ``limit`` fills (oldest first) and their parent orders from SQLite."""

    rows = conn.execute(
        "SELECT run_id, payload FROM fills ORDER BY rowid LIMIT ?", (limit + 1,)
    ).fetchall()
    truncated = len(rows) > limit
    fills = []
    for row in rows[:limit]:
        payload = json.loads(row["payload"])
        payload["run_id"] = row["run_id"]
        fills.append(payload)
    orders = {
        (row["run_id"], row["order_id"]): json.loads(row["payload"])
        for row in conn.execute("SELECT run_id, order_id, payload FROM orders").fetchall()
    }
    return fills, orders, truncated
