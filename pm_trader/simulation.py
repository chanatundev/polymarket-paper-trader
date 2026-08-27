"""High-level paper simulation orchestration over the standalone matcher."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .errors import LocalValidationError
from .ledger import AppendOnlyLedger
from .matching import PaperMatcher
from .models import BookSnapshot, Decision, MarketState, MatchResult, PaperOrder, SimulationConfig, SimulationReport
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


def run_id_for(*, dataset_id: str | None, market: MarketState, orders: Iterable[PaperOrder], config: SimulationConfig) -> str:
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
    return sha256_json({"dataset_id": dataset_id, "market": market_identity, "orders": list(orders), "config": config})[:24]


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
        config = config or SimulationConfig()
        order_list = tuple(orders)
        resolved_run_id = run_id or run_id_for(dataset_id=dataset_id, market=market, orders=order_list, config=config)
        if self.store.run_exists(resolved_run_id):
            raise LocalValidationError("run identity already exists in this append-only store", details={"run_id": resolved_run_id})
        if book.provenance is not None:
            self.store.ensure_observation(provenance=book.provenance, raw_payload=book, normalized_payload=book)
        self.store.create_run(run_id=resolved_run_id, dataset_id=dataset_id, created_at=book.received_at, config=config)
        ledger = AppendOnlyLedger(self.store, run_id=resolved_run_id, starting_cash=config.starting_cash)
        matches: list[MatchResult] = []
        warnings = list(extra_warnings or [])
        try:
            for order in order_list:
                match = self.matcher.match_order(market, book, order, now=book.provider_timestamp)
                if match.fills:
                    required_cash = sum((fill.notional + (fill.fee or Decimal("0"))) for fill in match.fills if fill.side.casefold() == "buy")
                    if required_cash > ledger.state.cash:
                        match = MatchResult(
                            order=order.with_status("rejected", remaining=order.effective_quantity()),
                            fills=(),
                            decision=Decision(status="rejected", reason_code="INSUFFICIENT_CASH", reasons=("paper cash is insufficient for the complete matched quantity",), freshness=match.decision.freshness, provenance_ids=match.decision.provenance_ids, assumptions={"paper_only": True}, checks={"required_cash": str(required_cash), "available_cash": str(ledger.state.cash)}),
                            remaining_quantity=order.effective_quantity(),
                        )
                    else:
                        try:
                            if any(fill.side.casefold() == "sell" and ledger.state.positions.get(fill.asset_id, None) is None for fill in match.fills):
                                raise LocalValidationError("sell fill has no paper position")
                            for fill in match.fills:
                                ledger.apply_fill(fill)
                        except LocalValidationError as exc:
                            match = MatchResult(
                                order=order.with_status("rejected", remaining=order.effective_quantity()),
                                fills=(),
                                decision=Decision(status="rejected", reason_code="INSUFFICIENT_POSITION", reasons=(str(exc),), freshness=match.decision.freshness, provenance_ids=match.decision.provenance_ids, assumptions={"paper_only": True}),
                                remaining_quantity=order.effective_quantity(),
                            )
                ledger.record_order(match.order)
                matches.append(match)
            effective_marks = dict(mark_prices or {})
            if not effective_marks:
                best_bid = book.best_bid()
                best_ask = book.best_ask()
                if best_bid and best_ask:
                    effective_marks[book.asset_id] = (best_bid.price + best_ask.price) / Decimal("2")
                    warnings.append("mark_price_derived_from_book_midpoint")
            report = ledger.report(mark_prices=effective_marks, warnings=warnings, assumptions={"clock": config.clock, "latency_ms": config.latency_ms, "queue_policy": config.queue_policy, "seed": config.seed, "paper_only": True, "paper_pnl_is_not_live_pnl": True}, data_coverage={"dataset_id": dataset_id, "book_observations": 1, "full_depth": True, **dict(data_coverage or {})})
            return SimulationResult(run_id=resolved_run_id, matches=tuple(matches), report=report)
        except Exception:
            self.store.finish_run(resolved_run_id, status="failed")
            raise


def simulate_order(*, market: MarketState, book: BookSnapshot, order: PaperOrder, config: SimulationConfig | None = None, store: SQLiteStore | None = None) -> SimulationResult:
    """Convenience one-order vertical slice using an in-memory store by default."""
    owned_store = store or SQLiteStore()
    try:
        return SimulationEngine(owned_store).run(market=market, book=book, orders=(order,), config=config)
    finally:
        if store is None:
            owned_store.close()
