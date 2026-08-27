"""Append-only cash, position, fee, rebate, and P&L ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from .errors import LocalValidationError, UnknownFeeScheduleError
from .models import Fill, LedgerEntry, LedgerKind, PaperOrder, SimulationReport
from .precision import ZERO, decimal, quantize_money
from .serialization import canonical_json, sha256_json
from .storage import SQLiteStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PositionState:
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO
    realized_pnl: Decimal = ZERO


@dataclass
class LedgerState:
    run_id: str
    starting_cash: Decimal
    cash: Decimal
    positions: dict[str, PositionState] = field(default_factory=dict)
    fees: Decimal = ZERO
    rebate_estimates: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    entries: list[LedgerEntry] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    orders: list[PaperOrder] = field(default_factory=list)


class AppendOnlyLedger:
    """Ledger whose financial changes are represented only by immutable entries."""

    def __init__(self, store: SQLiteStore, *, run_id: str, starting_cash: Decimal) -> None:
        self.store = store
        self.state = LedgerState(run_id=run_id, starting_cash=starting_cash, cash=ZERO)
        self._sequence = 0
        self._append(
            kind=LedgerKind.INITIAL_CASH.value,
            asset_id=None,
            order_id=None,
            fill_id=None,
            cash_delta=starting_cash,
            position_delta=ZERO,
            notional=ZERO,
            fee=None,
            rebate_estimate=None,
            realized_pnl=None,
            unrealized_pnl=None,
            observation_id=None,
            assumptions={"paper_only": True},
        )

    def record_order(self, order: PaperOrder) -> None:
        self.state.orders.append(order)
        self.store.insert_order(self.state.run_id, order)

    def apply_fill(self, fill: Fill) -> None:
        if fill.fee is None:
            raise UnknownFeeScheduleError("cannot apply a fill with unknown fee")
        quantity = decimal(fill.quantity, field="fill.quantity")
        notional = decimal(fill.notional, field="fill.notional")
        fee = decimal(fill.fee, field="fill.fee")
        if quantity <= ZERO or notional < ZERO or fee < ZERO:
            raise LocalValidationError("fill quantities, notional, and fee must be non-negative")
        sign = Decimal("1") if fill.side.casefold() == "buy" else Decimal("-1")
        position = self.state.positions.setdefault(fill.asset_id, PositionState())
        if sign < ZERO:
            if position.quantity < quantity:
                raise LocalValidationError("sell fill exceeds current paper position", details={"asset_id": fill.asset_id, "available": str(position.quantity), "requested": str(quantity)})
            average_cost = position.cost_basis / position.quantity if position.quantity else ZERO
            realized = notional - (average_cost * quantity) - fee
            position.realized_pnl += realized
            self.state.realized_pnl += realized
            position.cost_basis -= average_cost * quantity
        else:
            position.cost_basis += notional + fee
        position.quantity += sign * quantity
        cash_delta = -notional if sign > ZERO else notional
        self._append(
            kind=LedgerKind.FILL.value,
            asset_id=fill.asset_id,
            order_id=fill.order_id,
            fill_id=fill.fill_id,
            cash_delta=cash_delta,
            position_delta=sign * quantity,
            notional=notional,
            fee=fee,
            rebate_estimate=fill.rebate_estimate,
            realized_pnl=None,
            unrealized_pnl=None,
            observation_id=fill.observation_id,
            assumptions=fill.assumptions,
        )
        self._append(
            kind=LedgerKind.FEE.value,
            asset_id=fill.asset_id,
            order_id=fill.order_id,
            fill_id=fill.fill_id,
            cash_delta=-fee,
            position_delta=ZERO,
            notional=ZERO,
            fee=fee,
            rebate_estimate=None,
            realized_pnl=None,
            unrealized_pnl=None,
            observation_id=fill.observation_id,
            assumptions={"source": "match_time_fee"},
        )
        if fill.rebate_estimate is not None:
            rebate = decimal(fill.rebate_estimate, field="fill.rebate_estimate")
            if rebate < ZERO:
                raise LocalValidationError("rebate estimate cannot be negative")
            self.state.rebate_estimates += rebate
            self._append(
                kind=LedgerKind.REBATE_ESTIMATE.value,
                asset_id=fill.asset_id,
                order_id=fill.order_id,
                fill_id=fill.fill_id,
                cash_delta=ZERO,
                position_delta=ZERO,
                notional=ZERO,
                fee=None,
                rebate_estimate=rebate,
                realized_pnl=None,
                unrealized_pnl=None,
                observation_id=fill.observation_id,
                assumptions={"cash_effect": "none_until_provider_confirmed"},
            )
        self.state.fees += fee
        self.state.fills.append(fill)
        self.store.insert_fill(self.state.run_id, fill)

    def _append(self, *, kind: str, asset_id: str | None, order_id: str | None, fill_id: str | None, cash_delta: Decimal, position_delta: Decimal, notional: Decimal, fee: Decimal | None, rebate_estimate: Decimal | None, realized_pnl: Decimal | None, unrealized_pnl: Decimal | None, observation_id: str | None, assumptions: Mapping[str, Any]) -> None:
        entry = LedgerEntry(
            entry_id=f"{self.state.run_id}:{self._sequence}",
            run_id=self.state.run_id,
            sequence=self._sequence,
            kind=kind,
            asset_id=asset_id,
            order_id=order_id,
            fill_id=fill_id,
            cash_delta=quantize_money(cash_delta),
            position_delta=position_delta,
            notional=quantize_money(notional),
            fee=None if fee is None else quantize_money(fee),
            rebate_estimate=None if rebate_estimate is None else quantize_money(rebate_estimate),
            realized_pnl=None if realized_pnl is None else quantize_money(realized_pnl),
            unrealized_pnl=None if unrealized_pnl is None else quantize_money(unrealized_pnl),
            observation_id=observation_id,
            assumptions=dict(assumptions),
        )
        self._sequence += 1
        self.state.cash += entry.cash_delta
        self.state.entries.append(entry)
        self.store.insert_ledger_entry(entry)

    def report(self, *, mark_prices: Mapping[str, Decimal] | None = None, warnings: list[str] | None = None, assumptions: Mapping[str, Any] | None = None, data_coverage: Mapping[str, Any] | None = None) -> SimulationReport:
        marks = mark_prices or {}
        unrealized = ZERO
        positions: dict[str, Decimal] = {}
        missing_marks: list[str] = []
        exposure = ZERO
        for asset_id, position in sorted(self.state.positions.items()):
            if position.quantity == ZERO:
                continue
            positions[asset_id] = position.quantity
            mark = marks.get(asset_id)
            if mark is None:
                missing_marks.append(asset_id)
                continue
            value = position.quantity * decimal(mark, field="mark_price")
            exposure += value
            unrealized += value - position.cost_basis
        report_warnings = list(warnings or [])
        if missing_marks:
            report_warnings.append("unrealized_pnl_unknown_without_mark_price:" + ",".join(missing_marks))
        report_payload = {
            "run_id": self.state.run_id,
            "starting_cash": self.state.starting_cash,
            "cash": self.state.cash,
            "positions": positions,
            "exposure": exposure,
            "fees": self.state.fees,
            "rebate_estimates": self.state.rebate_estimates,
            "realized_pnl": self.state.realized_pnl,
            "unrealized_pnl": unrealized,
            "fills": len(self.state.fills),
            "orders": len(self.state.orders),
            "warnings": report_warnings,
            "assumptions": {"paper_only": True, **dict(assumptions or {})},
            "data_coverage": dict(data_coverage or {}),
        }
        checksum = sha256_json(report_payload)
        report = SimulationReport(
            run_id=self.state.run_id,
            starting_cash=self.state.starting_cash,
            cash=self.state.cash,
            positions=positions,
            exposure=exposure,
            fees=self.state.fees,
            rebate_estimates=self.state.rebate_estimates,
            realized_pnl=self.state.realized_pnl,
            unrealized_pnl=unrealized,
            fills=len(self.state.fills),
            orders=len(self.state.orders),
            warnings=tuple(report_warnings),
            assumptions={"paper_only": True, **dict(assumptions or {})},
            data_coverage=dict(data_coverage or {}),
            checksum=checksum,
        )
        self.store.save_report(self.state.run_id, checksum, report_payload)
        self.store.finish_run(self.state.run_id)
        return report
