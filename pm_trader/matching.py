"""Deterministic paper order-book matcher with explicit lifecycle decisions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .errors import LocalValidationError
from .fees import calculate_fee, estimate_maker_rebate
from .freshness import FreshnessStateGate
from .models import (
    BookLevel,
    BookSnapshot,
    Decision,
    Fill,
    MakerTaker,
    MarketState,
    MatchResult,
    OrderBook,
    OrderStatus,
    PaperOrder,
    TimeInForce,
)
from .precision import ZERO, decimal, is_tick_aligned, non_negative, probability, timestamp_seconds


def _snapshot(book: BookSnapshot | OrderBook) -> BookSnapshot:
    if isinstance(book, BookSnapshot):
        return book
    return BookSnapshot(
        market_id=None,
        asset_id="compatibility-asset",
        bids=tuple(
            BookLevel(
                price=decimal(level.price, field="bid.price"),
                size=decimal(level.size, field="bid.size"),
            )
            for level in book.bids
        ),
        asks=tuple(
            BookLevel(
                price=decimal(level.price, field="ask.price"),
                size=decimal(level.size, field="ask.size"),
            )
            for level in book.asks
        ),
        provider_timestamp="0",
        received_at="0",
        book_hash="compatibility-book",
        continuity="authoritative",
    )


def _rejected(
    order: PaperOrder,
    code: str,
    reasons: list[str],
    *,
    freshness: Any = "unknown",
    provenance_ids: tuple[str, ...] = (),
    checks: dict[str, Any] | None = None,
) -> MatchResult:
    status = OrderStatus.REJECTED.value
    return MatchResult(
        order=order.with_status(status, remaining=order.effective_quantity()),
        fills=(),
        decision=Decision(
            status="rejected",
            reason_code=code,
            reasons=tuple(reasons),
            freshness=freshness,
            provenance_ids=provenance_ids,
            assumptions={"paper_only": True},
            checks=checks or {},
        ),
        remaining_quantity=order.effective_quantity(),
    )


class PaperMatcher:
    """Match paper orders against a declared canonical book snapshot."""

    def __init__(self, *, stale_tolerance_seconds: int = 30, fee_rounding_places: int = 5) -> None:
        self.gate = FreshnessStateGate(stale_tolerance_seconds=stale_tolerance_seconds)
        self.fee_rounding_places = fee_rounding_places

    def match_order(
        self,
        market: MarketState,
        book: BookSnapshot | OrderBook,
        order: PaperOrder,
        *,
        now: str | None = None,
        fill_prefix: str | None = None,
        allow_resting_post_only_fill: bool = False,
    ) -> MatchResult:
        canonical_book = _snapshot(book)
        # Matching a declared snapshot defaults to that snapshot's provider
        # clock. Callers processing a live wall clock must pass ``now`` to
        # activate the stale-data check against the current time.
        gate = self.gate.evaluate(
            market, canonical_book, now=now or canonical_book.provider_timestamp
        )
        if gate.decision.status != "eligible":
            rejected_status = "stale" if gate.decision.status == "stale" else "rejected"
            updated = order.with_status(rejected_status, remaining=order.effective_quantity())
            return MatchResult(
                order=updated,
                fills=(),
                decision=gate.decision,
                remaining_quantity=order.effective_quantity(),
            )

        if order.market_id != market.condition_id:
            return _rejected(
                order,
                "MARKET_ID_MISMATCH",
                ["order market does not match the declared market state"],
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
            )
        side = order.side.casefold()
        if side not in {"buy", "sell"}:
            return _rejected(
                order,
                "INVALID_SIDE",
                ["order side must be buy or sell"],
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
            )
        tif = order.time_in_force.casefold()
        if tif not in {item.value for item in TimeInForce}:
            return _rejected(
                order,
                "INVALID_TIME_IN_FORCE",
                [f"unsupported time-in-force: {order.time_in_force}"],
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
            )
        quantity = order.effective_quantity()
        if quantity <= ZERO:
            return _rejected(
                order,
                "INVALID_SIZE",
                ["order quantity must be greater than zero"],
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
            )
        if order.asset_id != canonical_book.asset_id:
            return _rejected(
                order,
                "TOKEN_ID_MISMATCH",
                ["order token does not match the book"],
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
            )
        if tif == TimeInForce.GTD.value and not order.expires_at:
            return _rejected(
                order,
                "MISSING_EXPIRY",
                ["GTD orders require expires_at"],
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
            )
        if order.expires_at and canonical_book.provider_timestamp:
            try:
                if timestamp_seconds(canonical_book.provider_timestamp) >= timestamp_seconds(
                    order.expires_at
                ):
                    updated = order.with_status(OrderStatus.EXPIRED.value, remaining=quantity)
                    return MatchResult(
                        order=updated,
                        fills=(),
                        decision=Decision(
                            status="rejected",
                            reason_code="EXPIRED",
                            reasons=("order expired before matching",),
                            freshness=gate.decision.freshness,
                            provenance_ids=gate.decision.provenance_ids,
                            assumptions={"paper_only": True},
                        ),
                        remaining_quantity=quantity,
                    )
            except (TypeError, ValueError, OverflowError):
                return _rejected(
                    order,
                    "INVALID_EXPIRY",
                    ["expires_at is not a valid timestamp"],
                    freshness=gate.decision.freshness,
                    provenance_ids=gate.decision.provenance_ids,
                )

        try:
            price = None if order.price is None else probability(order.price, field="order.price")
        except (LocalValidationError, TypeError, ValueError):
            return _rejected(
                order,
                "INVALID_PRICE",
                ["order price must be a finite probability between 0 and 1"],
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
            )
        if (
            price is not None
            and gate.effective_tick_size is not None
            and not is_tick_aligned(price, gate.effective_tick_size)
        ):
            return _rejected(
                order,
                "TICK_SIZE_VIOLATION",
                [f"price {price} is not aligned to tick size {gate.effective_tick_size}"],
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
                checks={"tick_size": str(gate.effective_tick_size), "price": str(price)},
            )
        if gate.effective_min_order_size is not None and quantity < gate.effective_min_order_size:
            return _rejected(
                order,
                "MINIMUM_SIZE_VIOLATION",
                [
                    f"quantity {quantity} is below minimum order size {gate.effective_min_order_size}"
                ],
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
                checks={
                    "min_order_size": str(gate.effective_min_order_size),
                    "quantity": str(quantity),
                },
            )
        if order.submitted_at and canonical_book.provider_timestamp:
            try:
                available_at = timestamp_seconds(order.submitted_at) + order.latency_ms / 1000
                if timestamp_seconds(canonical_book.provider_timestamp) < available_at:
                    return MatchResult(
                        order=order.with_status(OrderStatus.OPEN.value, remaining=quantity),
                        fills=(),
                        decision=Decision(
                            status="rejected",
                            reason_code="LATENCY_NOT_REACHED",
                            reasons=("book timestamp precedes declared order latency",),
                            freshness=gate.decision.freshness,
                            provenance_ids=gate.decision.provenance_ids,
                            assumptions={"latency_ms": order.latency_ms, "paper_only": True},
                        ),
                        remaining_quantity=quantity,
                    )
            except (TypeError, ValueError, OverflowError):
                return _rejected(
                    order,
                    "INVALID_TIMESTAMP",
                    ["submitted_at is not a valid timestamp"],
                    freshness=gate.decision.freshness,
                    provenance_ids=gate.decision.provenance_ids,
                )

        opposite = list(canonical_book.asks if side == "buy" else canonical_book.bids)
        if side == "buy":
            opposite.sort(key=lambda level: level.price)
            best = opposite[0] if opposite else None
            crosses = best is not None and (price is None or best.price <= price)
        else:
            opposite.sort(key=lambda level: level.price, reverse=True)
            best = opposite[0] if opposite else None
            crosses = best is not None and (price is None or best.price >= price)
        if order.post_only and not allow_resting_post_only_fill:
            if price is None:
                return _rejected(
                    order,
                    "POST_ONLY_REQUIRES_PRICE",
                    ["post-only orders require a limit price"],
                    freshness=gate.decision.freshness,
                    provenance_ids=gate.decision.provenance_ids,
                )
            if crosses:
                return _rejected(
                    order,
                    "POST_ONLY_WOULD_TAKE",
                    ["post-only order would remove liquidity"],
                    freshness=gate.decision.freshness,
                    provenance_ids=gate.decision.provenance_ids,
                )
            return MatchResult(
                order=order.with_status(OrderStatus.OPEN.value, remaining=quantity),
                fills=(),
                decision=Decision(
                    status="eligible",
                    reason_code="POST_ONLY_RESTING",
                    reasons=("post-only order rests without crossing the declared book",),
                    freshness=gate.decision.freshness,
                    provenance_ids=gate.decision.provenance_ids,
                    assumptions={
                        "maker_taker": "maker",
                        "queue_policy": order.queue_policy,
                        "paper_only": True,
                    },
                ),
                remaining_quantity=quantity,
            )
        if best is None:
            return self._no_fill(
                order, gate, "NO_LIQUIDITY", "opposite book side has no liquidity", quantity
            )
        if not crosses:
            return self._no_fill(
                order,
                gate,
                "PRICE_NOT_CROSSED",
                "best opposite price does not cross the limit",
                quantity,
                open_order=tif in {TimeInForce.GTC.value, TimeInForce.GTD.value},
            )

        schedule = gate.effective_fee_schedule
        remaining = quantity
        fills: list[Fill] = []
        queue_ahead = non_negative(order.queue_ahead, field="queue_ahead")
        role = (
            MakerTaker.MAKER.value
            if order.post_only or allow_resting_post_only_fill
            else MakerTaker.TAKER.value
        )
        for level_index, level in enumerate(opposite):
            if remaining <= ZERO:
                break
            if price is not None and (
                (side == "buy" and level.price > price) or (side == "sell" and level.price < price)
            ):
                break
            available = level.size
            if queue_ahead > ZERO:
                skipped = min(queue_ahead, available)
                queue_ahead -= skipped
                available -= skipped
            if available <= ZERO:
                continue
            matched = min(remaining, available)
            fee_result = calculate_fee(
                schedule,
                level.price,
                matched,
                role=role,
                side=side,
                rounding_places=self.fee_rounding_places,
            )
            if fee_result.amount is None:
                return MatchResult(
                    order=order.with_status(OrderStatus.REJECTED.value, remaining=quantity),
                    fills=(),
                    decision=Decision(
                        status="unknown",
                        reason_code="UNKNOWN_FEE_SCHEDULE",
                        reasons=("match-time fee schedule is unavailable",),
                        freshness=gate.decision.freshness,
                        provenance_ids=gate.decision.provenance_ids,
                        assumptions={"paper_only": True},
                    ),
                    remaining_quantity=quantity,
                )
            rebate = estimate_maker_rebate(
                schedule, price=level.price, shares=matched, eligible=role == MakerTaker.MAKER.value
            )
            fill_id = f"{fill_prefix or order.order_id}:{level_index + 1}"
            fills.append(
                Fill(
                    fill_id=fill_id,
                    order_id=order.order_id,
                    asset_id=order.asset_id,
                    side=side,
                    quantity=matched,
                    price=level.price,
                    notional=matched * level.price,
                    maker_taker=role,
                    fee=fee_result.amount,
                    rebate_estimate=rebate.amount,
                    observation_id=canonical_book.provenance.observation_id
                    if canonical_book.provenance
                    else None,
                    provider_timestamp=canonical_book.provider_timestamp,
                    received_at=canonical_book.received_at,
                    model_version=order.model_version,
                    queue_policy=order.queue_policy,
                    assumptions={
                        "latency_ms": order.latency_ms,
                        "queue_ahead": str(order.queue_ahead),
                        "fee_model_version": schedule.version if schedule else None,
                        "paper_only": True,
                    },
                )
            )
            remaining -= matched

        if tif == TimeInForce.FOK.value and remaining > ZERO:
            reason = "FOK_NOT_FILLED" if fills else "NO_LIQUIDITY"
            return self._no_fill(
                order, gate, reason, "FOK requires the entire quantity to be executable", quantity
            )
        if not fills:
            return self._no_fill(
                order,
                gate,
                "PRICE_NOT_CROSSED",
                "no declared level satisfied the limit",
                quantity,
                open_order=tif in {TimeInForce.GTC.value, TimeInForce.GTD.value},
            )
        if remaining == ZERO:
            status = OrderStatus.FILLED.value
            decision_status = "eligible"
            reason_code = "FILLED"
            reasons = ("order fully matched against declared liquidity",)
        elif tif == TimeInForce.FAK.value:
            status = OrderStatus.PARTIALLY_FILLED.value
            decision_status = "eligible"
            reason_code = "PARTIAL_FILL_REMAINDER_CANCELLED"
            reasons = ("available liquidity was consumed and the remainder was cancelled by FAK",)
        else:
            status = OrderStatus.PARTIALLY_FILLED.value
            decision_status = "eligible"
            reason_code = "PARTIAL_FILL_REMAINDER_OPEN"
            reasons = ("available liquidity was consumed and the remainder remains open",)
        decision = Decision(
            status=decision_status,
            reason_code=reason_code,
            reasons=reasons,
            freshness=gate.decision.freshness,
            provenance_ids=gate.decision.provenance_ids,
            assumptions={
                "latency_ms": order.latency_ms,
                "queue_policy": order.queue_policy,
                "maker_taker": role,
                "paper_only": True,
            },
            checks={
                "requested_quantity": str(quantity),
                "matched_quantity": str(quantity - remaining),
                "remaining_quantity": str(remaining),
            },
        )
        return MatchResult(
            order=order.with_status(status, remaining=remaining),
            fills=tuple(fills),
            decision=decision,
            remaining_quantity=remaining,
        )

    def _no_fill(
        self,
        order: PaperOrder,
        gate: Any,
        code: str,
        reason: str,
        quantity: Decimal,
        *,
        open_order: bool = False,
    ) -> MatchResult:
        status = OrderStatus.OPEN.value if open_order else OrderStatus.UNFILLED.value
        return MatchResult(
            order=order.with_status(status, remaining=quantity),
            fills=(),
            decision=Decision(
                status="eligible" if open_order else "rejected",
                reason_code=code,
                reasons=(reason,),
                freshness=gate.decision.freshness,
                provenance_ids=gate.decision.provenance_ids,
                assumptions={"paper_only": True, "queue_policy": order.queue_policy},
            ),
            remaining_quantity=quantity,
        )

    match = match_order

    def match_resting_order(
        self,
        market: MarketState,
        book: BookSnapshot,
        order: PaperOrder,
        *,
        now: str | None = None,
        fill_prefix: str | None = None,
    ) -> MatchResult:
        """Match an already-resting GTC/GTD order as maker after a new book."""
        if not order.post_only and order.time_in_force.casefold() not in {"gtc", "gtd"}:
            return _rejected(
                order,
                "INVALID_RESTING_ORDER",
                ["only GTC/GTD orders can be matched as resting liquidity"],
                freshness=book.freshness,
            )
        return self.match_order(
            market, book, order, now=now, fill_prefix=fill_prefix, allow_resting_post_only_fill=True
        )
