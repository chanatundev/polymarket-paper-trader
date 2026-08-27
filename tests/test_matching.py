from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from pm_trader.freshness import FreshnessStateGate
from pm_trader.matching import PaperMatcher
from pm_trader.models import PaperOrder


def order(market, *, tif="fok", price="0.67", size="40", side="buy", **kwargs):
    return PaperOrder(order_id=f"order-{tif}-{size}-{price}", market_id=market.condition_id, asset_id=market.tokens[0].token_id, side=side, price=price, quantity=size, time_in_force=tif, **kwargs)


def test_fok_walks_price_time_and_fills_all(market, book):
    result = PaperMatcher().match_order(market, book, order(market))
    assert result.order.status == "filled"
    assert len(result.fills) == 2
    assert result.fills[0].quantity == Decimal("25")
    assert result.fills[1].price == Decimal("0.67")
    assert result.decision.reason_code == "FILLED"


def test_fok_does_not_overfill_or_partially_commit(market, book):
    result = PaperMatcher().match_order(market, book, order(market, size="200"))
    assert result.fills == ()
    assert result.order.status == "unfilled"
    assert result.decision.reason_code == "FOK_NOT_FILLED"


def test_fak_returns_partial_and_cancels_remainder(market, book):
    result = PaperMatcher().match_order(market, book, order(market, tif="fak", size="200"))
    assert result.order.status == "partially_filled"
    assert result.remaining_quantity == Decimal("135")
    assert result.decision.reason_code == "PARTIAL_FILL_REMAINDER_CANCELLED"


def test_gtc_unfilled_is_open_with_price_reason(market, book):
    result = PaperMatcher().match_order(market, book, order(market, tif="gtc", price="0.65", size="10"))
    assert result.order.status == "open"
    assert result.decision.reason_code == "PRICE_NOT_CROSSED"


def test_post_only_crossing_order_is_rejected(market, book):
    result = PaperMatcher().match_order(market, book, order(market, tif="gtc", price="0.67", size="10", post_only=True))
    assert result.order.status == "rejected"
    assert result.decision.reason_code == "POST_ONLY_WOULD_TAKE"


def test_tick_and_minimum_size_are_explicit(market, book):
    tick = PaperMatcher().match_order(market, book, order(market, price="0.665"))
    assert tick.decision.reason_code == "TICK_SIZE_VIOLATION"
    minimum = PaperMatcher().match_order(market, book, order(market, size="1"))
    assert minimum.decision.reason_code == "MINIMUM_SIZE_VIOLATION"


def test_stale_book_is_not_eligible(market, book):
    future = "2026-08-26T21:00:00Z"
    result = PaperMatcher().match_order(market, book, order(market), now=future)
    assert result.fills == ()
    assert result.decision.status == "stale"
    assert result.decision.reason_code == "STALE_BOOK"


def test_unknown_lifecycle_and_fee_state_do_not_become_eligible(market, book):
    missing_state = replace(market, accepting_orders=None)
    result = PaperMatcher().match_order(missing_state, book, order(missing_state))
    assert result.decision.status == "unknown"
    assert "accepting_orders_state_unknown" in result.decision.reasons
    no_fee = replace(market, fee_schedule=None)
    result = PaperMatcher().match_order(no_fee, book, order(no_fee))
    assert result.decision.status == "unknown"
    assert "fee_schedule_unknown" in result.decision.reasons
