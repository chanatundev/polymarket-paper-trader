"""Decimal and timestamp helpers used by the deterministic core."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
from typing import Any

from .errors import LocalValidationError


ZERO = Decimal("0")
ONE = Decimal("1")


def decimal(value: Any, *, field: str = "value") -> Decimal:
    """Convert a finite numeric value without going through binary float math."""
    if isinstance(value, bool) or value is None:
        raise LocalValidationError(f"{field} must be a finite number", details={"field": field})
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LocalValidationError(f"{field} must be a finite number", details={"field": field}) from exc
    if not result.is_finite():
        raise LocalValidationError(f"{field} must be finite", details={"field": field})
    return result


def non_negative(value: Any, *, field: str) -> Decimal:
    result = decimal(value, field=field)
    if result < ZERO:
        raise LocalValidationError(f"{field} must be non-negative", details={"field": field})
    return result


def probability(value: Any, *, field: str = "price") -> Decimal:
    result = decimal(value, field=field)
    if result < ZERO or result > ONE:
        raise LocalValidationError(f"{field} must be between 0 and 1", details={"field": field})
    return result


def quantize_money(value: Decimal, places: int = 5) -> Decimal:
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def is_tick_aligned(price: Decimal, tick: Decimal) -> bool:
    if tick <= ZERO:
        return False
    quotient = price / tick
    return quotient == quotient.to_integral_value()


def parse_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
        return value.strip().casefold() == "true"
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise LocalValidationError(f"{field} must be a boolean", details={"field": field})


def parse_timestamp(value: Any, *, field: str = "timestamp") -> str:
    """Validate an ISO-8601 or Unix-seconds timestamp and return a stable string."""
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        try:
            number = float(value)
        except ValueError as exc:
            raise LocalValidationError(f"{field} must be a timestamp", details={"field": field}) from exc
        if not isfinite(number):
            raise LocalValidationError(f"{field} must be finite", details={"field": field})
        return str(value)
    if not isinstance(value, str) or not value.strip():
        raise LocalValidationError(f"{field} must be a timestamp", details={"field": field})
    text = value.strip()
    try:
        if text.replace(".", "", 1).isdigit():
            float(text)
        else:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LocalValidationError(f"{field} must be ISO-8601 or Unix seconds", details={"field": field}) from exc
    return text


def timestamp_seconds(value: str | int | float | Decimal) -> float:
    if isinstance(value, str):
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
    return float(value)
