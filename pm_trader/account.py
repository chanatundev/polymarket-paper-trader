"""One persistent paper account per data directory.

Account runs are ordinary append-only simulation runs whose orders carry
``metadata["account"] == ACCOUNT_ID``. The account's cash and positions are rebuilt
from those runs' stored fills, so a later run can sell into an earlier buy. Runs
without the tag (``--isolated`` simulations, replays, older stores) are untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .precision import ZERO, decimal

ACCOUNT_ID = "paper-account"
DEFAULT_ACCOUNT_CASH = Decimal(10000)


@dataclass
class AccountPosition:
    quantity: Decimal = ZERO
    # Ledger convention: buy fees are capitalised into cost basis.
    cost_basis: Decimal = ZERO
    realized_pnl: Decimal = ZERO


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
            "starting_cash": str(self.starting_cash),
            "cash": str(self.cash),
            "fees": str(self.fees),
            "realized_pnl": str(self.realized_pnl),
            "fills": self.fills,
            "runs": self.runs,
            "open_positions": {
                asset: {"quantity": str(pos.quantity), "cost_basis": str(pos.cost_basis)}
                for asset, pos in self.opening_positions().items()
            },
        }


def account_run_ids(conn: Any) -> list[str]:
    """Completed runs whose orders are tagged for the data-directory account, oldest first."""

    rows = conn.execute(
        "SELECT DISTINCT o.run_id, r.rowid AS run_order FROM orders o "
        "JOIN simulation_runs r ON r.run_id = o.run_id "
        "WHERE r.status = 'completed' "
        "AND json_extract(o.payload, '$.metadata.account') = ? ORDER BY r.rowid",
        (ACCOUNT_ID,),
    ).fetchall()
    return [row["run_id"] for row in rows]


def load_account(conn: Any, *, starting_cash: Decimal = DEFAULT_ACCOUNT_CASH) -> AccountState:
    """Replay every account fill in run order with the ledger's cash and cost rules."""

    starting = decimal(starting_cash, field="starting_cash")
    state = AccountState(starting_cash=starting, cash=starting)
    run_ids = account_run_ids(conn)
    state.runs = len(run_ids)
    for run_id in run_ids:
        # Cash comes from the ledger's own quantized entries so it matches run reports exactly.
        for row in conn.execute(
            "SELECT payload FROM ledger_entries WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall():
            entry = json.loads(row["payload"])
            if entry.get("kind") not in {"initial_cash", "opening_position"}:
                state.cash += decimal(entry.get("cash_delta") or 0, field="cash_delta")
        for row in conn.execute(
            "SELECT payload FROM fills WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall():
            fill = json.loads(row["payload"])
            quantity = decimal(fill["quantity"], field="fill.quantity")
            notional = decimal(fill["notional"], field="fill.notional")
            fee = decimal(fill.get("fee") or 0, field="fill.fee")
            position = state.positions.setdefault(str(fill["asset_id"]), AccountPosition())
            state.fills += 1
            state.fees += fee
            if str(fill["side"]).casefold() == "buy":
                position.quantity += quantity
                position.cost_basis += notional + fee
            else:
                average = position.cost_basis / position.quantity if position.quantity else ZERO
                realized = notional - average * quantity - fee
                position.realized_pnl += realized
                state.realized_pnl += realized
                position.cost_basis -= average * quantity
                position.quantity -= quantity
    return state
