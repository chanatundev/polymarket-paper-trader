"""Freshness and tradability/state gates for paper simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .models import BookSnapshot, Decision, FeeSchedule, Freshness, MarketState
from .precision import timestamp_seconds


def _now_seconds(value: str | None) -> float:
    if value is None:
        return datetime.now(timezone.utc).timestamp()
    return timestamp_seconds(value)


@dataclass(frozen=True)
class GateResult:
    decision: Decision
    effective_tick_size: Decimal | None = None
    effective_min_order_size: Decimal | None = None
    effective_fee_schedule: FeeSchedule | None = None


class FreshnessStateGate:
    """Reject missing, stale, closed, gapped, or inconsistent state explicitly."""

    def __init__(self, *, stale_tolerance_seconds: int = 30, clock: callable | None = None) -> None:
        self.stale_tolerance_seconds = stale_tolerance_seconds
        self.clock = clock

    def evaluate(
        self, market: MarketState | None, book: BookSnapshot | None, *, now: str | None = None
    ) -> GateResult:
        reasons: list[str] = []
        warnings: list[str] = []
        checks: dict[str, Any] = {}
        provenance_ids: list[str] = []
        if market and market.provenance:
            provenance_ids.append(market.provenance.observation_id)
        if book and book.provenance:
            provenance_ids.append(book.provenance.observation_id)
        if market is None or book is None:
            return GateResult(
                Decision(
                    status="unknown",
                    reason_code="UNAVAILABLE_STATE",
                    reasons=("market or book observation is unavailable",),
                    freshness=Freshness.UNAVAILABLE,
                    provenance_ids=tuple(provenance_ids),
                    checks=checks,
                )
            )

        if market.closed is True:
            reasons.append("market_closed")
        elif market.closed is None:
            reasons.append("market_closed_state_unknown")
        if market.active is False:
            reasons.append("market_inactive")
        elif market.active is None:
            reasons.append("market_active_state_unknown")
        if market.accepting_orders is False:
            reasons.append("market_not_accepting_orders")
        elif market.accepting_orders is None:
            reasons.append("accepting_orders_state_unknown")
        if market.enable_order_book is False:
            reasons.append("order_book_disabled")
        elif market.enable_order_book is None:
            reasons.append("order_book_enablement_unknown")
        if market.neg_risk is None:
            reasons.append("market_neg_risk_unknown")
        if market.fees_enabled is None:
            reasons.append("fees_enabled_state_unknown")

        if book.market_id is not None and book.market_id != market.condition_id:
            reasons.append("book_market_identity_mismatch")
        try:
            market.token_for(book.asset_id)
        except ValueError:
            reasons.append("book_token_identity_mismatch")
        if book.neg_risk is None:
            reasons.append("book_neg_risk_unknown")
        elif market.neg_risk is not None and book.neg_risk != market.neg_risk:
            reasons.append("book_neg_risk_mismatch")
        if book.continuity in {"gap", "inconsistent", "unknown_after_gap"}:
            reasons.append("book_sequence_gap_or_inconsistency")
        if book.freshness in {Freshness.STALE, Freshness.UNAVAILABLE, Freshness.INCONSISTENT}:
            reasons.append(f"book_freshness_{book.freshness.value}")

        effective_tick = book.tick_size if book.tick_size is not None else market.tick_size
        effective_min = (
            book.min_order_size if book.min_order_size is not None else market.min_order_size
        )
        if effective_tick is None:
            reasons.append("tick_size_unknown")
        if effective_min is None:
            reasons.append("minimum_order_size_unknown")
        if (
            book.tick_size is not None
            and market.tick_size is not None
            and book.tick_size != market.tick_size
        ):
            reasons.append("book_market_tick_size_mismatch")
        if (
            book.min_order_size is not None
            and market.min_order_size is not None
            and book.min_order_size != market.min_order_size
        ):
            warnings.append("book_market_minimum_size_differs")

        if market.fees_enabled is True and market.fee_schedule is None:
            reasons.append("fee_schedule_unknown")
        if market.fees_enabled is False:
            warnings.append("market_explicitly_fee_free")
        elif market.fee_schedule is not None and market.fee_schedule.version.startswith("legacy"):
            warnings.append("legacy_fee_schedule_used")

        age: float | None = None
        if book.provider_timestamp is None:
            reasons.append("book_timestamp_unknown")
        else:
            try:
                age = _now_seconds(now) - timestamp_seconds(book.provider_timestamp)
            except (TypeError, ValueError, OverflowError):
                reasons.append("book_timestamp_invalid")
            else:
                checks["book_age_seconds"] = round(age, 6)
                if age < -self.stale_tolerance_seconds:
                    reasons.append("book_timestamp_in_future")
                elif age > self.stale_tolerance_seconds:
                    reasons.append("book_stale")
        if book.book_hash is None and book.sequence is None:
            reasons.append("book_hash_and_sequence_unknown")
        checks.update(
            {
                "market_active": market.active,
                "market_closed": market.closed,
                "accepting_orders": market.accepting_orders,
                "enable_order_book": market.enable_order_book,
                "neg_risk": market.neg_risk,
                "fees_enabled": market.fees_enabled,
                "book_continuity": book.continuity,
                "stale_tolerance_seconds": self.stale_tolerance_seconds,
            }
        )

        if any(reason in {"book_stale", "book_freshness_stale"} for reason in reasons):
            status = "stale"
            freshness = Freshness.STALE
            reason_code = "STALE_BOOK"
        elif any(
            reason
            in {
                "book_market_identity_mismatch",
                "book_token_identity_mismatch",
                "book_neg_risk_mismatch",
                "book_sequence_gap_or_inconsistency",
                "book_market_tick_size_mismatch",
            }
            for reason in reasons
        ):
            status = "rejected"
            freshness = Freshness.INCONSISTENT
            reason_code = "INCONSISTENT_STATE"
        elif reasons:
            status = "unknown"
            freshness = Freshness.UNKNOWN
            reason_code = "UNKNOWN_STATE"
        else:
            status = "eligible"
            freshness = Freshness.FRESH
            reason_code = None
        return GateResult(
            decision=Decision(
                status=status,
                reason_code=reason_code,
                reasons=tuple(reasons),
                warnings=tuple(warnings),
                freshness=freshness,
                provenance_ids=tuple(dict.fromkeys(provenance_ids)),
                assumptions={"stale_tolerance_seconds": self.stale_tolerance_seconds},
                checks=checks,
            ),
            effective_tick_size=effective_tick,
            effective_min_order_size=effective_min,
            effective_fee_schedule=market.fee_schedule
            if market.fees_enabled is not False
            else FeeSchedule(
                rate=Decimal(0), exponent=Decimal(1), taker_only=True, version="explicit-fee-free"
            ),
        )
