from __future__ import annotations

from decimal import Decimal

import pytest

from pm_trader.errors import LocalValidationError
from pm_trader.fees import calculate_fee, calculate_platform_fee, estimate_maker_rebate
from pm_trader.models import FeeSchedule


def test_dynamic_fee_uses_per_fill_price_and_five_place_rounding():
    schedule = FeeSchedule(rate=Decimal("0.05"), exponent=Decimal(1), rebate_rate=Decimal("0.25"))
    result = calculate_fee(schedule, Decimal("0.66"), Decimal(25))
    assert result.amount == Decimal("0.28050")
    assert result.maker_taker == "taker"
    assert result.formula == "shares_rate_price_product_power"


def test_exponent_matches_v2_sdk_semantics():
    # CLOB V2's SDK accepts notional and converts to shares before applying
    # rate * (p * (1-p)) ** exponent.
    assert calculate_platform_fee(
        Decimal(50), Decimal("0.5"), Decimal("0.25"), Decimal(2)
    ) == Decimal("1.5625")
    assert calculate_platform_fee(
        Decimal(30), Decimal("0.3"), Decimal("0.25"), Decimal(2)
    ) == Decimal("1.1025")


def test_makers_are_not_charged_and_rebate_is_separate_estimate():
    schedule = FeeSchedule(rate=Decimal("0.05"), exponent=Decimal(1), rebate_rate=Decimal("0.25"))
    fee = calculate_fee(schedule, Decimal("0.5"), Decimal(100), role="maker")
    rebate = estimate_maker_rebate(schedule, price=Decimal("0.5"), shares=Decimal(100))
    assert fee.amount == Decimal(0)
    assert rebate.status == "estimate"
    assert rebate.amount == Decimal("0.31250")
    assert "estimate is not cash" in rebate.assumptions


def test_missing_schedule_is_unknown_not_zero():
    result = calculate_fee(None, Decimal("0.5"), Decimal(100))
    assert result.status == "unknown"
    assert result.amount is None


def test_unknown_fee_role_is_not_silently_treated_as_maker():
    with pytest.raises(LocalValidationError):
        calculate_fee(FeeSchedule(rate=Decimal("0.05")), Decimal("0.5"), Decimal(10), role="other")
