"""Match-time dynamic fee and separately reported maker-rebate estimates."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .errors import LocalValidationError
from .models import FeeResult, FeeSchedule, MakerTaker, RebateEstimate
from .precision import ONE, ZERO, decimal, probability, quantize_money


FEE_ROUNDING_PLACES = 5


def _schedule_from_legacy(value: Any) -> FeeSchedule:
    bps = decimal(value, field="fee_rate_bps")
    if bps < ZERO:
        raise LocalValidationError("fee_rate_bps cannot be negative")
    return FeeSchedule(rate=bps / Decimal("10000"), exponent=ONE, version="legacy-base-fee-bps", formula="legacy_shares_rate_price_product")


def calculate_fee(
    schedule: FeeSchedule | int | float | str | None,
    price: Decimal | int | float | str,
    shares: Decimal | int | float | str,
    *,
    role: str = MakerTaker.TAKER.value,
    side: str = "buy",
    rounding_places: int = FEE_ROUNDING_PLACES,
) -> FeeResult:
    """Calculate one fill's fee at its execution price.

    For the current dynamic schedule the formula is
    ``shares * rate * (price * (1 - price)) ** exponent``. ``shares`` is
    token quantity, while the result is collateral currency. Missing fee
    metadata returns an explicit ``unknown`` result instead of zero.
    """
    del side  # The current documented curve is symmetric; retain side in the API for auditability.
    p = probability(price)
    quantity = decimal(shares, field="shares")
    if quantity < ZERO:
        raise LocalValidationError("shares cannot be negative")
    if schedule is None:
        return FeeResult(
            status="unknown",
            amount=None,
            reason="fee schedule unavailable at match time",
            rate=None,
            exponent=None,
            maker_taker=role,
            rounded_places=rounding_places,
            formula=None,
        )
    if not isinstance(schedule, FeeSchedule):
        schedule = _schedule_from_legacy(schedule)
    if role.casefold() == MakerTaker.MAKER.value or (schedule.taker_only and role.casefold() != MakerTaker.TAKER.value):
        return FeeResult(
            status="known",
            amount=ZERO,
            reason="maker fee is zero under declared schedule",
            rate=schedule.rate,
            exponent=schedule.exponent,
            maker_taker=MakerTaker.MAKER.value,
            rounded_places=rounding_places,
            formula=schedule.formula,
        )
    curve = p * (ONE - p)
    if schedule.formula == "legacy_shares_rate_price_product":
        raw_amount = quantity * schedule.rate * curve
    else:
        raw_amount = quantity * schedule.rate * (curve ** schedule.exponent)
    amount = quantize_money(raw_amount, rounding_places)
    return FeeResult(
        status="known",
        amount=amount,
        reason=None,
        rate=schedule.rate,
        exponent=schedule.exponent,
        maker_taker=MakerTaker.TAKER.value,
        rounded_places=rounding_places,
        formula=schedule.formula,
    )


def calculate_dynamic_fee(schedule: FeeSchedule | None, price: Any, shares: Any, *, maker: bool = False, side: str = "buy") -> Decimal | None:
    """Convenience API returning only the amount, with ``None`` for unknown."""
    result = calculate_fee(schedule, price, shares, role=MakerTaker.MAKER.value if maker else MakerTaker.TAKER.value, side=side)
    return result.amount


def estimate_maker_rebate(schedule: FeeSchedule | None, *, price: Any, shares: Any, eligible: bool | None = None, coverage: str = "fixture_schedule_only") -> RebateEstimate:
    """Estimate a maker rebate without changing fee or cash accounting."""
    if schedule is None:
        return RebateEstimate(status="unknown", amount=None, eligible=None, confidence="none", coverage=coverage, assumptions=("fee schedule unavailable",), reason="maker rebate schedule unavailable")
    if schedule.rebate_rate is None:
        return RebateEstimate(status="unknown", amount=None, eligible=False if eligible is None else eligible, confidence="none", coverage=coverage, assumptions=("rebate rate not provided by the fixture",), reason="rebate rate unavailable")
    if eligible is False:
        return RebateEstimate(status="not_eligible", amount=ZERO, eligible=False, confidence="high", coverage=coverage, assumptions=("caller marked fill as not eligible",), reason="fill is not eligible for maker rebate")
    p = probability(price)
    quantity = decimal(shares, field="shares")
    if quantity < ZERO:
        raise LocalValidationError("shares cannot be negative")
    fee_equivalent = quantity * schedule.rate * (p * (ONE - p)) ** schedule.exponent
    amount = quantize_money(fee_equivalent * schedule.rebate_rate, FEE_ROUNDING_PLACES)
    return RebateEstimate(status="estimate", amount=amount, eligible=True if eligible is None else eligible, confidence="medium", coverage=coverage, assumptions=("provider-confirmed reward distribution is not present", "estimate is not cash", "market-level rebate pool is not observed"))


def calculate_platform_fee(amount_usd: Any, price: Any, fee_rate: Any, fee_exponent: Any = 1) -> Decimal:
    """Compatibility helper matching the CLOB V2 SDK's amount convention.

    ``amount_usd`` is notional; it is converted to shares before applying the
    declared dynamic curve. The return is intentionally not rounded so callers
    can choose their report precision, mirroring the SDK helper.
    """
    p = probability(price)
    amount = decimal(amount_usd, field="amount_usd")
    if p == ZERO:
        return ZERO
    rate = decimal(fee_rate, field="fee_rate")
    exponent = decimal(fee_exponent, field="fee_exponent")
    if rate < ZERO or exponent < ZERO:
        raise LocalValidationError("fee rate and exponent cannot be negative")
    return (amount / p) * rate * (p * (ONE - p)) ** exponent
