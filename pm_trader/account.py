"""One persistent paper account per data directory.

Account runs are ordinary append-only runs tagged for ``ACCOUNT_ID``: simulation runs
whose orders carry ``metadata["account"]``, and settlement runs whose resolution ledger
entries carry ``assumptions["account"]``. The account is rebuilt by replaying those
runs' ledger entries in order, so a later run can sell into an earlier buy and a
resolved market pays out once. Runs without the tag (``--isolated`` simulations,
replays, older stores) never touch it.

One P&L convention everywhere: buy fees are part of cost basis, ``realized_pnl`` is
net of sell fees, and ``net_pnl = realized_pnl + unrealized_pnl``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .precision import ZERO, decimal

ACCOUNT_ID = "paper-account"
DEFAULT_ACCOUNT_CASH = Decimal(10000)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


@dataclass
class AccountPosition:
    asset_id: str
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO  # fee-inclusive
    realized_pnl: Decimal = ZERO  # net of fees
    fees: Decimal = ZERO
    fills: int = 0
    resolved_payout: Decimal | None = None
    labels: dict[str, Any] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "market_id": self.labels.get("market_id"),
            "market_slug": self.labels.get("market_slug"),
            "market_question": self.labels.get("market_question"),
            "outcome": self.labels.get("outcome"),
            "quantity": _text(self.quantity),
            "average_price": _text(self.cost_basis / self.quantity) if self.quantity > 0 else None,
            "cost_basis": _text(self.cost_basis),
            "realized_pnl": _text(self.realized_pnl),
            "fees": _text(self.fees),
            "fills": self.fills,
            "status": "settled"
            if self.resolved_payout is not None
            else "open"
            if self.quantity > 0
            else "closed",
            **(
                {"resolved_payout": _text(self.resolved_payout)}
                if self.resolved_payout is not None
                else {}
            ),
        }


@dataclass
class AccountState:
    starting_cash: Decimal
    cash: Decimal
    positions: dict[str, AccountPosition] = field(default_factory=dict)
    fees: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    fills: int = 0
    runs: int = 0

    def opening_positions(self) -> dict[str, AccountPosition]:
        return {asset: pos for asset, pos in self.positions.items() if pos.quantity > ZERO}

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": ACCOUNT_ID,
            "starting_cash": _text(self.starting_cash),
            "cash": _text(self.cash),
            "fees": _text(self.fees),
            "realized_pnl": _text(self.realized_pnl),
            "fills": self.fills,
            "runs": self.runs,
            "open_positions": [pos.row() for pos in self.opening_positions().values()],
        }


def account_run_ids(conn: Any) -> list[str]:
    """Completed account runs that changed something (a fill or a settlement), oldest first."""

    rows = conn.execute(
        """
        SELECT r.run_id FROM simulation_runs r
        WHERE r.status = 'completed'
          AND (
            (EXISTS (SELECT 1 FROM orders o WHERE o.run_id = r.run_id
                     AND json_extract(o.payload, '$.metadata.account') = :account)
             AND EXISTS (SELECT 1 FROM fills f WHERE f.run_id = r.run_id))
            OR EXISTS (SELECT 1 FROM ledger_entries l WHERE l.run_id = r.run_id
                       AND json_extract(l.payload, '$.kind') = 'resolution'
                       AND json_extract(l.payload, '$.assumptions.account') = :account)
          )
        ORDER BY r.rowid
        """,
        {"account": ACCOUNT_ID},
    ).fetchall()
    return [row["run_id"] for row in rows]


def _labels(conn: Any, run_ids: list[str]) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for run_id in run_ids:
        for row in conn.execute("SELECT payload FROM orders WHERE run_id = ?", (run_id,)):
            order = json.loads(row["payload"])
            metadata = order.get("metadata") or {}
            labels.setdefault(
                str(order.get("asset_id")),
                {
                    "market_id": order.get("market_id"),
                    "market_slug": metadata.get("market_slug"),
                    "market_question": metadata.get("market_question"),
                    "outcome": metadata.get("outcome"),
                },
            )
    return labels


def load_account(conn: Any, *, starting_cash: Decimal = DEFAULT_ACCOUNT_CASH) -> AccountState:
    """Replay every account ledger entry with the ledger's own cash and cost rules."""

    starting = decimal(starting_cash, field="starting_cash")
    state = AccountState(starting_cash=starting, cash=starting)
    run_ids = account_run_ids(conn)
    state.runs = len(run_ids)
    labels = _labels(conn, run_ids)

    def position(asset_id: str) -> AccountPosition:
        if asset_id not in state.positions:
            state.positions[asset_id] = AccountPosition(
                asset_id=asset_id, labels=labels.get(asset_id, {})
            )
        return state.positions[asset_id]

    for run_id in run_ids:
        for row in conn.execute(
            "SELECT payload FROM ledger_entries WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall():
            entry = json.loads(row["payload"])
            kind = entry.get("kind")
            if kind in {"initial_cash", "opening_position"}:
                continue
            state.cash += decimal(entry.get("cash_delta") or 0, field="cash_delta")
            asset_id = entry.get("asset_id")
            if kind == "fill":
                pos = position(str(asset_id))
                delta = decimal(entry["position_delta"], field="position_delta")
                notional = decimal(entry["notional"], field="notional")
                fee = decimal(entry.get("fee") or 0, field="fee")
                pos.fills += 1
                pos.fees += fee
                state.fills += 1
                state.fees += fee
                if delta > ZERO:
                    pos.quantity += delta
                    pos.cost_basis += notional + fee
                else:
                    quantity = -delta
                    average = pos.cost_basis / pos.quantity if pos.quantity else ZERO
                    realized = notional - average * quantity - fee
                    pos.realized_pnl += realized
                    state.realized_pnl += realized
                    pos.cost_basis -= average * quantity
                    pos.quantity -= quantity
            elif kind == "resolution":
                pos = position(str(asset_id))
                realized = decimal(entry.get("realized_pnl") or 0, field="realized_pnl")
                pos.realized_pnl += realized
                state.realized_pnl += realized
                pos.quantity += decimal(entry["position_delta"], field="position_delta")
                pos.cost_basis = ZERO
                pos.resolved_payout = decimal(
                    entry["assumptions"]["payout_per_share"], field="payout_per_share"
                )
    return state
