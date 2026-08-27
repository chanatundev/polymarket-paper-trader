"""Typed errors used at provider, normalization, and simulation boundaries."""

from __future__ import annotations

from typing import Any


class TraderError(Exception):
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
