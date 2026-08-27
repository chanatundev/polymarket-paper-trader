"""High-level paper simulation orchestration over the standalone matcher."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from .errors import LocalValidationError
from .ledger import AppendOnlyLedger
from .matching import PaperMatcher
from .models import (
    BookSnapshot,
    Decision,
    MarketState,
    MatchResult,
    PaperOrder,
    SimulationConfig,
    SimulationReport,
)
from .precision import ZERO
from .serialization import sha256_json
from .storage import SQLiteStore


@dataclass(frozen=True)
class SimulationResult:
    run_id: str
    matches: tuple[MatchResult, ...]
    report: SimulationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "matches": [
                {
                    "order": match.order,
                    "fills": match.fills,
                    "decision": match.decision,
                    "remaining_quantity": match.remaining_quantity,
                }
                for match in self.matches
            ],
            "report": self.report,
        }


def run_id_for(
    *,
    dataset_id: str | None,
    market: MarketState,
    orders: Iterable[PaperOrder],
    config: SimulationConfig,
) -> str:
    # Receipt clocks and raw-provider extensions are provenance, not run
    # identity. Keep the identity stable when the same declared dataset and
    # configuration are loaded in another process.
    market_identity = {
        "condition_id": market.condition_id,
        "slug": market.slug,
        "outcomes": market.outcomes,
        "tokens": market.tokens,
        "tick_size": market.tick_size,
        "min_order_size": market.min_order_size,
        "neg_risk": market.neg_risk,
        "fees_enabled": market.fees_enabled,
        "fee_schedule": market.fee_schedule,
    }
    return sha256_json(
        {
            "dataset_id": dataset_id,
            "market": market_identity,
            "orders": list(orders),
            "config": config,
        }
    )[:24]


class SimulationEngine:
    """Run paper orders with explicit declared configuration and no write path."""

    def __init__(self, store: SQLiteStore, *, matcher: PaperMatcher | None = None) -> None:
        self.store = store
        self.matcher = matcher or PaperMatcher()

    def run(
        self,
        *,
        market: MarketState,
        book: BookSnapshot,
        orders: Iterable[PaperOrder],
        config: SimulationConfig | None = None,
        dataset_id: str | None = None,
        run_id: str | None = None,
        mark_prices: Mapping[str, Decimal] | None = None,
        extra_warnings: list[str] | None = None,
        data_coverage: Mapping[str, Any] | None = None,
    ) -> SimulationResult:
        return self.run_stream(
            market=market,
            snapshots=(book,),
            orders=orders,
            config=config,
            dataset_id=dataset_id,
            run_id=run_id,
            mark_prices=mark_prices,
            extra_warnings=extra_warnings,
            data_coverage=data_coverage,
        )

    def run_stream(
        self,
        *,
        market: MarketState,
        snapshots: Iterable[BookSnapshot],
        orders: Iterable[PaperOrder],
        config: SimulationConfig | None = None,
        dataset_id: str | None = None,
        run_id: str | None = None,
        mark_prices: Mapping[str, Decimal] | None = None,
        extra_warnings: list[str] | None = None,
        data_coverage: Mapping[str, Any] | None = None,
    ) -> SimulationResult:
        config = config or SimulationConfig()
        snapshot_list = tuple(snapshots)
        if not snapshot_list:
            raise LocalValidationError("simulation requires at least one book snapshot")
        order_list = tuple(orders)
        resolved_run_id = run_id or run_id_for(
            dataset_id=dataset_id, market=market, orders=order_list, config=config
        )
        if self.store.run_exists(resolved_run_id):
            raise LocalValidationError(
                "run identity already exists in this append-only store",
                details={"run_id": resolved_run_id},
            )
        for snapshot in snapshot_list:
            if snapshot.provenance is not None:
                self.store.ensure_observation(
                    provenance=snapshot.provenance,
                    raw_payload=snapshot,
                    normalized_payload=snapshot,
                )
        self.store.create_run(
            run_id=resolved_run_id,
            dataset_id=dataset_id,
            created_at=snapshot_list[0].received_at,
            config=config,
        )
        ledger = AppendOnlyLedger(
            self.store, run_id=resolved_run_id, starting_cash=config.starting_cash
        )
        matches: list[MatchResult] = []
        warnings = list(extra_warnings or [])
        try:
            self.matcher.gate.stale_tolerance_seconds = config.stale_tolerance_seconds
            for order in order_list:
                effective_order = replace(
                    order,
                    latency_ms=config.latency_ms if order.latency_ms == 0 else order.latency_ms,
                    queue_policy=config.queue_policy
                    if order.queue_policy == "front_of_queue"
                    else order.queue_policy,
                )
                ledger.record_order(effective_order)
                current_order = effective_order
                latest_match: MatchResult | None = None
                for attempt, snapshot in enumerate(snapshot_list):
                    if snapshot.asset_id != current_order.asset_id:
                        continue
                    candidate = self.matcher.match_order(
                        market,
                        snapshot,
                        current_order,
                        now=snapshot.provider_timestamp,
                        fill_prefix=f"{current_order.order_id}:{attempt}",
                    )
                    latest_match = self._settle(
                        candidate, current_order=current_order, ledger=ledger
                    )
                    if latest_match.fills:
                        current_order = (
                            replace(
                                latest_match.order,
                                quantity=latest_match.remaining_quantity,
                                size=None,
                                status="open",
                            )
                            if latest_match.remaining_quantity > ZERO
                            and current_order.time_in_force.casefold() in {"gtc", "gtd"}
                            else latest_match.order
                        )
                        if (
                            latest_match.remaining_quantity > ZERO
                            and current_order.time_in_force.casefold() in {"gtc", "gtd"}
                        ):
                            continue
                    elif (
                        latest_match.order.status == "open"
                        and current_order.time_in_force.casefold() in {"gtc", "gtd"}
                    ):
                        current_order = latest_match.order
                        continue
                    break
                if latest_match is None:
                    latest_match = MatchResult(
                        order=current_order.with_status(
                            "rejected", remaining=current_order.effective_quantity()
                        ),
                        fills=(),
                        decision=Decision(
                            status="unknown",
                            reason_code="NO_BOOK_FOR_ORDER",
                            reasons=("no recorded snapshot matched the order token",),
                            assumptions={"paper_only": True},
                        ),
                        remaining_quantity=current_order.effective_quantity(),
                    )
                ledger.record_order(latest_match.order)
                matches.append(latest_match)
            effective_marks = dict(mark_prices or {})
            if not effective_marks:
                mark_book = next(
                    (
                        snapshot
                        for snapshot in reversed(snapshot_list)
                        if snapshot.asset_id in ledger.state.positions
                    ),
                    snapshot_list[-1],
                )
                best_bid = mark_book.best_bid()
                best_ask = mark_book.best_ask()
                if best_bid and best_ask:
                    effective_marks[mark_book.asset_id] = (
                        best_bid.price + best_ask.price
                    ) / Decimal(2)
                    warnings.append("mark_price_derived_from_book_midpoint")
            report = ledger.report(
                mark_prices=effective_marks,
                warnings=warnings,
                assumptions={
                    "clock": config.clock,
                    "latency_ms": config.latency_ms,
                    "queue_policy": config.queue_policy,
                    "seed": config.seed,
                    "paper_only": True,
                    "paper_pnl_is_not_live_pnl": True,
                },
                data_coverage={
                    "dataset_id": dataset_id,
                    "book_observations": len(snapshot_list),
                    "full_depth": True,
                    **dict(data_coverage or {}),
                },
            )
            return SimulationResult(run_id=resolved_run_id, matches=tuple(matches), report=report)
        except Exception:
            self.store.finish_run(resolved_run_id, status="failed")
            raise

    @staticmethod
    def _settle(
        match: MatchResult, *, current_order: PaperOrder, ledger: AppendOnlyLedger
    ) -> MatchResult:
        if not match.fills:
            return match
        required_cash = sum(
            (fill.notional + (fill.fee or Decimal(0)))
            for fill in match.fills
            if fill.side.casefold() == "buy"
        )
        required_sell_quantity: dict[str, Decimal] = {}
        for fill in match.fills:
            if fill.side.casefold() == "sell":
                required_sell_quantity[fill.asset_id] = (
                    required_sell_quantity.get(fill.asset_id, ZERO) + fill.quantity
                )
        insufficient_position = any(
            ledger.state.positions.get(asset_id, None) is None
            or ledger.state.positions[asset_id].quantity < quantity
            for asset_id, quantity in required_sell_quantity.items()
        )
        if required_cash > ledger.state.cash:
            return MatchResult(
                order=current_order.with_status(
                    "rejected", remaining=current_order.effective_quantity()
                ),
                fills=(),
                decision=Decision(
                    status="rejected",
                    reason_code="INSUFFICIENT_CASH",
                    reasons=("paper cash is insufficient for the complete matched quantity",),
                    freshness=match.decision.freshness,
                    provenance_ids=match.decision.provenance_ids,
                    assumptions={"paper_only": True},
                    checks={
                        "required_cash": str(required_cash),
                        "available_cash": str(ledger.state.cash),
                    },
                ),
                remaining_quantity=current_order.effective_quantity(),
            )
        if insufficient_position:
            return MatchResult(
                order=current_order.with_status(
                    "rejected", remaining=current_order.effective_quantity()
                ),
                fills=(),
                decision=Decision(
                    status="rejected",
                    reason_code="INSUFFICIENT_POSITION",
                    reasons=("paper position is insufficient for the complete matched quantity",),
                    freshness=match.decision.freshness,
                    provenance_ids=match.decision.provenance_ids,
                    assumptions={"paper_only": True},
                ),
                remaining_quantity=current_order.effective_quantity(),
            )
        for fill in match.fills:
            ledger.apply_fill(fill)
        return match


def simulate_order(
    *,
    market: MarketState,
    book: BookSnapshot,
    order: PaperOrder,
    config: SimulationConfig | None = None,
    store: SQLiteStore | None = None,
) -> SimulationResult:
    """Convenience one-order vertical slice using an in-memory store by default."""
    owned_store = store or SQLiteStore()
    try:
        return SimulationEngine(owned_store).run(
            market=market, book=book, orders=(order,), config=config
        )
    finally:
        if store is None:
            owned_store.close()
