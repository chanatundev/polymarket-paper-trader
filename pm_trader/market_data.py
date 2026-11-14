"""Compact, decision-ready summaries of markets, books, price history, and stored fills.

These helpers turn normalized provider state into the small numbers an agent needs to
choose a paper order: prices per outcome, top-of-book, depth, spread, recent price
movement, and current paper exposure. They never create orders or call providers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .errors import LocalValidationError
from .fees import FEE_ROUNDING_PLACES, calculate_fee
from .models import BookLevel, BookSnapshot, FeeSchedule, MarketState, PricePoint
from .precision import is_tick_aligned, quantize_money

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

# A book older than this (received_at - provider_timestamp) is reported as stale in quotes.
# Display only: the matcher's own freshness gate is separate and unchanged.
BOOK_FRESH_SECONDS = Decimal(30)
_FRESHNESS_RANK = {"fresh": 0, "stale": 1, "unknown": 2}
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def _epoch_seconds(value: Any) -> Decimal | None:
    """Unix seconds/milliseconds or an ISO-8601 string as epoch seconds; None if unparseable."""

    if value is None or isinstance(value, bool):
        return None
    number = _dec(value)
    if number is not None:
        return number / 1000 if number > 100_000_000_000 else number
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return Decimal(str(parsed.timestamp()))


def book_age_seconds(provider_timestamp: Any, received_at: Any) -> Decimal | None:
    """Seconds between the provider's book timestamp and when we received it."""

    provider, received = _epoch_seconds(provider_timestamp), _epoch_seconds(received_at)
    if provider is None or received is None:
        return None
    return (received - provider).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def book_freshness(age: Decimal | None) -> str:
    if age is None:
        return "unknown"
    return "fresh" if age <= BOOK_FRESH_SECONDS else "stale"


def worst_freshness(values: Iterable[str]) -> str:
    """Least trustworthy label (``unknown`` > ``stale`` > ``fresh``); ``unknown`` if empty."""

    ranked = [value if value in _FRESHNESS_RANK else "unknown" for value in values]
    return max(ranked, key=_FRESHNESS_RANK.__getitem__, default="unknown")


def hours_to_end(end_time: Any, now: datetime | None = None) -> Decimal | None:
    """Hours from ``now`` until the market end (negative once passed); None if unknown.

    A date-only end (``2026-11-14``) is treated as the end of that day, 00:00 UTC the day after.
    """

    if end_time is None:
        return None
    if isinstance(end_time, str) and _DATE_ONLY.match(end_time.strip()):
        try:
            day = datetime.fromisoformat(end_time.strip()).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        end = Decimal(str((day + timedelta(days=1)).timestamp()))
    else:
        end = _epoch_seconds(end_time)
    if end is None:
        return None
    current = Decimal(str((now or datetime.now(timezone.utc)).timestamp()))
    return ((end - current) / 3600).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def time_to_end(end_time: Any, now: datetime | None = None) -> dict[str, Any]:
    hours = hours_to_end(end_time, now)
    return {
        "hours_to_end": None if hours is None else format(hours, "f"),
        "ends_within_24h": hours is not None and Decimal(0) <= hours <= 24,
    }


def market_outcomes(market: MarketState, *, token_ids: bool = False) -> list[dict[str, Any]]:
    """Outcome label and indicative Gamma price for every token (token ids on request)."""

    rows = []
    for index, token in enumerate(market.tokens):
        price = market.outcome_prices[index] if index < len(market.outcome_prices) else None
        # Gamma's last indicative price: a hint that lags the book; quote has the live mid.
        row: dict[str, Any] = {"outcome": token.outcome, "indicative_price": _text(price)}
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


def fee_formula(schedule: FeeSchedule) -> str:
    """Human-readable fee rule, mirroring ``fees.calculate_fee`` (taker pays, maker pays 0)."""

    if schedule.formula == "legacy_shares_rate_price_product":
        expression = "fee = shares * rate * price * (1 - price)"
    else:
        expression = "fee = shares * rate * (price * (1 - price)) ** exponent"
    return (
        f"{expression}; charged per fill level at its own price, taker only (makers pay 0), "
        f"rounded to {FEE_ROUNDING_PLACES} decimals"
    )


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
            "formula": fee_formula(market.fee_schedule),
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
    age = book_age_seconds(book.provider_timestamp, book.received_at)
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
        "age_seconds": _text(age),
        "freshness": book_freshness(age),
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


def compact_book(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Top-of-book numbers only: no level lists, just touch, age, and depth within 1c."""

    row = {
        key: summary.get(key)
        for key in ("outcome", "best_bid", "best_ask", "midpoint", "spread", "age_seconds")
    }
    near = (summary.get("depth_near_mid") or {}).get("within_1c")
    row["depth_near_mid"] = {"within_1c": near} if near is not None else None
    return row


def estimate_fill(
    book: BookSnapshot,
    market: MarketState,
    *,
    side: str,
    size: Decimal,
    price: Decimal | None = None,
) -> dict[str, Any]:
    """Pre-trade estimate: walk the opposite side best-first, as a taker, within a limit.

    Buys consume asks (lowest first), sells consume bids (highest first). Fees reuse
    ``fees.calculate_fee`` per level exactly as the matcher does. Nothing is simulated or stored.
    """

    side = side.lower()
    if side not in {"buy", "sell"}:
        raise LocalValidationError("side must be buy or sell", details={"side": side})
    if size <= 0:
        raise LocalValidationError("size must be positive", details={"size": _text(size)})
    if price is not None and not Decimal(0) < price < Decimal(1):
        raise LocalValidationError(
            "price must be between 0 and 1", details={"price": _text(price)}
        )
    warnings: list[str] = []
    min_size = book.min_order_size if book.min_order_size is not None else market.min_order_size
    tick = book.tick_size if book.tick_size is not None else market.tick_size
    if min_size is not None and size < min_size:
        warnings.append("below_min_order_size")
    if price is not None and tick is not None and not is_tick_aligned(price, tick):
        warnings.append("price_not_on_tick")

    bids = _levels(book.bids, best_first_descending=True)
    asks = _levels(book.asks, best_first_descending=False)
    best_bid = bids[0].price if bids else None
    best_ask = asks[0].price if asks else None
    mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None

    remaining = size
    fillable = notional = fee = Decimal(0)
    fee_known = True
    levels_used = 0
    worst: Decimal | None = None
    schedule = market.fee_schedule
    for level in asks if side == "buy" else bids:
        if remaining <= 0:
            break
        if price is not None and (
            (side == "buy" and level.price > price) or (side == "sell" and level.price < price)
        ):
            break
        matched = min(remaining, level.size)
        if matched <= 0:
            continue
        remaining -= matched
        fillable += matched
        notional += matched * level.price
        levels_used += 1
        worst = level.price
        if market.fees_enabled is not False:
            level_fee = calculate_fee(schedule, level.price, matched, side=side).amount
            if level_fee is None:
                fee_known = False
            else:
                fee += level_fee
    vwap = notional / fillable if fillable > 0 else None

    if market.fees_enabled is False:
        fee_text: str | None = "0"
        warnings.append("fees_disabled")
    elif not fee_known:
        fee_text = None
        warnings.append("fee_schedule_unknown")
    else:
        fee_text = _text(quantize_money(fee, FEE_ROUNDING_PLACES))
    if fillable <= 0:
        warnings.append("no_fill")
    elif fillable < size:
        warnings.append("partial_fill")
    if mid is None:
        warnings.append("midpoint_unavailable")

    slippage = loss = None
    if vwap is not None and mid is not None:
        slippage = vwap - mid if side == "buy" else mid - vwap
        loss = fillable * abs(vwap - mid) + (fee if fee_known else Decimal(0))
        if not fee_known:
            warnings.append("immediate_mark_loss_excludes_fee")
    elif fillable <= 0 and mid is not None:
        loss = Decimal(0)
    return {
        "side": side,
        "requested_size": _text(size),
        "limit_price": _text(price),
        "fillable_size": _text(fillable),
        "fully_fillable": fillable >= size,
        "vwap": _text(vwap),
        "worst_price": _text(worst),
        "levels_used": levels_used,
        "notional": _text(notional),
        "estimated_fee": fee_text,
        "slippage_vs_mid": _text(slippage),
        "immediate_mark_loss": _text(loss),
        "midpoint": _text(mid),
        "warnings": warnings,
    }


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


def _net_pnl(row: Mapping[str, Any]) -> str:
    """realized (net of fees) + unrealized; an unmarked position counts as zero unrealized."""

    realized = _dec(row.get("realized_pnl")) or Decimal(0)
    unrealized = _dec(row.get("unrealized_pnl")) or Decimal(0)
    return _text(realized + unrealized)


def mark_position(row: dict[str, Any], midpoint: Decimal | None) -> dict[str, Any]:
    quantity = _dec(row.get("quantity")) or Decimal(0)
    if midpoint is None or quantity <= 0:
        marked = {**row, "mark_price": _text(midpoint), "market_value": None, "unrealized_pnl": None}
        return {**marked, "net_pnl": _net_pnl(marked)}
    value = quantity * midpoint
    cost = _dec(row.get("cost_basis")) or Decimal(0)
    marked = {
        **row,
        "mark_price": _text(midpoint),
        "market_value": _text(value),
        "unrealized_pnl": _text(value - cost),
    }
    return {**marked, "net_pnl": _net_pnl(marked)}


def portfolio_totals(
    positions: Iterable[Mapping[str, Any]], *, marked: bool, cash: Decimal | None = None
) -> dict[str, Any]:
    """Sum positions. Unrealized/market value cover only positions that were actually marked."""

    rows = list(positions)

    def total(key: str) -> Decimal:
        return sum((_dec(row.get(key)) or Decimal(0) for row in rows), Decimal(0))

    market_value = unrealized = None
    if marked:
        market_value, unrealized = total("market_value"), total("unrealized_pnl")
    totals = {
        "cost_basis": _text(total("cost_basis")),
        "market_value": _text(market_value),
        "realized_pnl": _text(total("realized_pnl")),
        "unrealized_pnl": _text(unrealized),
        "fees": _text(total("fees")),
        "net_pnl": _text(total("net_pnl")),
    }
    if cash is not None and market_value is not None:
        totals["equity"] = _text(cash + market_value)
    return totals
