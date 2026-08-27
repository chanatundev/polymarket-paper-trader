"""Typed errors used at provider, normalization, and simulation boundaries."""

from __future__ import annotations

from typing import Any


class SimError(Exception):
    """Compatibility base for the original paper-trader API."""

    code = "SIM_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": "local_validation",
            "message": self.message,
            "details": self.details,
        }


class TraderError(SimError):
    """Base error with a stable machine-readable code."""

    code = "TRADER_ERROR"
    category = "local_validation"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "details": self.details,
        }


class ProviderError(TraderError):
    """Base error for a public provider operation."""

    code = "PROVIDER_ERROR"
    category = "provider"

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        endpoint: str,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            details={
                "provider": provider,
                "endpoint": endpoint,
                "status_code": status_code,
                **(details or {}),
            },
        )
        self.provider = provider
        self.endpoint = endpoint
        self.status_code = status_code


class NetworkProviderError(ProviderError):
    code = "PROVIDER_NETWORK_ERROR"
    category = "network_failure"


class ProviderRejectedError(ProviderError):
    code = "PROVIDER_REJECTED"
    category = "provider_rejection"


class RateLimitError(ProviderRejectedError):
    code = "PROVIDER_RATE_LIMITED"


class MalformedPayloadError(ProviderError):
    code = "MALFORMED_PAYLOAD"
    category = "malformed_data"


class LocalValidationError(TraderError):
    code = "LOCAL_VALIDATION_ERROR"
    category = "local_validation"


class UnknownDataError(TraderError):
    code = "UNKNOWN_DATA"
    category = "unknown_data"


class StaleDataError(TraderError):
    code = "STALE_DATA"
    category = "stale_data"


class PaperSafetyError(TraderError):
    code = "PAPER_SAFETY_VIOLATION"
    category = "paper_safety"


class OrderRejectedError(TraderError):
    code = "ORDER_REJECTED"
    category = "order_rejection"


class InsufficientCashError(OrderRejectedError):
    code = "INSUFFICIENT_CASH"


class InsufficientPositionError(OrderRejectedError):
    code = "INSUFFICIENT_POSITION"


class UnknownFeeScheduleError(OrderRejectedError):
    code = "UNKNOWN_FEE_SCHEDULE"


class DatasetLimitError(TraderError):
    code = "DATASET_LIMIT_EXCEEDED"
    category = "resource_limit"


# Compatibility errors retained for callers of the original Engine/CLI
# surface. They remain paper-only and carry no authenticated-operation data.
class NotInitializedError(TraderError):
    code = "NOT_INITIALIZED"

    def __init__(
        self, message: str = "Account not initialized. Run 'pm-trader init' first."
    ) -> None:
        super().__init__(message)


class InsufficientBalanceError(InsufficientCashError):
    code = "INSUFFICIENT_BALANCE"

    def __init__(self, required: float, available: float) -> None:
        super().__init__(
            f"Insufficient balance: need ${required:.2f}, have ${available:.2f}",
            details={"required": required, "available": available},
        )
        self.required = required
        self.available = available


class MarketNotFoundError(TraderError):
    code = "MARKET_NOT_FOUND"

    def __init__(self, identifier: str) -> None:
        super().__init__(f"Market not found: {identifier}", details={"identifier": identifier})
        self.identifier = identifier


class MarketClosedError(TraderError):
    code = "MARKET_CLOSED"

    def __init__(self, slug: str) -> None:
        super().__init__(f"Market is closed: {slug}", details={"slug": slug})
        self.slug = slug


class NoPositionError(TraderError):
    code = "NO_POSITION"

    def __init__(self, market: str, outcome: str) -> None:
        super().__init__(f"No position in {market} ({outcome})")
        self.market = market
        self.outcome = outcome


class InvalidOutcomeError(TraderError):
    code = "INVALID_OUTCOME"

    def __init__(self, outcome: str, valid: list[str] | None = None) -> None:
        message = f"Invalid outcome: {outcome!r}."
        if valid:
            message += f" Must be one of {valid}."
        super().__init__(message)
        self.outcome = outcome


class TickSizeViolationError(TraderError):
    code = "TICK_SIZE_VIOLATION"

    def __init__(self, price: float, tick_size: float) -> None:
        super().__init__(f"Price {price} violates tick size {tick_size}")
        self.price = price
        self.tick_size = tick_size


class AmbiguousResolutionError(TraderError):
    code = "AMBIGUOUS_RESOLUTION"

    def __init__(self, slug: str, prices: dict[Any, Any]) -> None:
        super().__init__(f"No clear winner for {slug}: outcome prices {prices} (none >= 0.99)")
        self.slug = slug
        self.prices = prices


class ApiError(TraderError):
    code = "API_ERROR"

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message, details={"status_code": status_code})
        self.status_code = status_code
