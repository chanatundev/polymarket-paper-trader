"""SQLite compatibility facade for the original paper-trader account API."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Account, Position, Trade
from .storage import SQLiteStore

LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY DEFAULT 1,
    starting_balance REAL NOT NULL DEFAULT 10000,
    cash REAL NOT NULL DEFAULT 10000,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_condition_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (length(outcome) > 0),
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL DEFAULT 'fok' CHECK (order_type IN ('fok', 'fak')),
    avg_price REAL NOT NULL,
    amount_usd REAL NOT NULL,
    shares REAL NOT NULL,
    fee_rate_bps INTEGER NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    slippage REAL NOT NULL DEFAULT 0,
    levels_filled INTEGER NOT NULL DEFAULT 1,
    is_partial INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
    market_condition_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (length(outcome) > 0),
    shares REAL NOT NULL DEFAULT 0,
    avg_entry_price REAL NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    is_resolved INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT,
    PRIMARY KEY (market_condition_id, outcome)
);

CREATE TABLE IF NOT EXISTS market_cache (
    cache_key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database(SQLiteStore):
    """Modern store plus the original account/trade/position tables."""

    def __init__(self, data_dir: Path | str) -> None:
        super().__init__(data_dir)

    def init_schema(self) -> None:
        self.conn.executescript(LEGACY_SCHEMA)
        from .orders import init_orders_schema

        init_orders_schema(self.conn)
        self.conn.commit()

    def init_account(self, balance: float = 10_000.0) -> Account:
        self.conn.execute(
            "INSERT OR REPLACE INTO account(id, starting_balance, cash) VALUES (1, ?, ?)",
            (balance, balance),
        )
        self.conn.commit()
        return self.get_account()

    def get_account(self) -> Account | None:
        row = self.conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
        if row is None:
            return None
        return Account(
            id=row["id"],
            starting_balance=row["starting_balance"],
            cash=row["cash"],
            created_at=row["created_at"],
        )

    def update_cash(self, new_cash: float) -> None:
        self.conn.execute("UPDATE account SET cash = ? WHERE id = 1", (new_cash,))
        self.conn.commit()

    def reset(self) -> None:
        for table in (
            "reports",
            "ledger_entries",
            "fills",
            "order_events",
            "orders",
            "simulation_runs",
            "stream_events",
            "observations",
            "datasets",
            "trades",
            "positions",
            "account",
            "market_cache",
            "limit_orders",
        ):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    def insert_trade(
        self,
        *,
        market_condition_id: str,
        market_slug: str,
        market_question: str,
        outcome: str,
        side: str,
        order_type: str,
        avg_price: float,
        amount_usd: float,
        shares: float,
        fee_rate_bps: int,
        fee: float,
        slippage: float,
        levels_filled: int,
        is_partial: bool,
    ) -> Trade:
        cursor = self.conn.execute(
            """INSERT INTO trades(
                market_condition_id, market_slug, market_question, outcome,
                side, order_type, avg_price, amount_usd, shares, fee_rate_bps,
                fee, slippage, levels_filled, is_partial
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market_condition_id,
                market_slug,
                market_question,
                outcome,
                side,
                order_type,
                avg_price,
                amount_usd,
                shares,
                fee_rate_bps,
                fee,
                slippage,
                levels_filled,
                int(is_partial),
            ),
        )
        self.conn.commit()
        return self.get_trades_by_id(int(cursor.lastrowid))

    def get_trades_by_id(self, trade_id: int) -> Trade:
        row = self.conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise ValueError(f"trade not found: {trade_id}")
        return _row_to_trade(row)

    def get_trades(self, limit: int = 50) -> list[Trade]:
        rows = self.conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_trade(row) for row in rows]

    def upsert_position(
        self,
        *,
        market_condition_id: str,
        market_slug: str,
        market_question: str,
        outcome: str,
        shares: float,
        avg_entry_price: float,
        total_cost: float,
        realized_pnl: float = 0.0,
    ) -> Position:
        self.conn.execute(
            """INSERT INTO positions(
                market_condition_id, market_slug, market_question, outcome,
                shares, avg_entry_price, total_cost, realized_pnl
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_condition_id, outcome) DO UPDATE SET
                shares = excluded.shares,
                avg_entry_price = excluded.avg_entry_price,
                total_cost = excluded.total_cost,
                realized_pnl = excluded.realized_pnl""",
            (
                market_condition_id,
                market_slug,
                market_question,
                outcome,
                shares,
                avg_entry_price,
                total_cost,
                realized_pnl,
            ),
        )
        self.conn.commit()
        return self.get_position(market_condition_id, outcome)

    def get_position(self, market_condition_id: str, outcome: str) -> Position | None:
        row = self.conn.execute(
            "SELECT * FROM positions WHERE market_condition_id = ? AND outcome = ?",
            (market_condition_id, outcome),
        ).fetchone()
        return None if row is None else _row_to_position(row)

    def get_open_positions(self) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE is_resolved = 0 AND shares > 0"
        ).fetchall()
        return [_row_to_position(row) for row in rows]

    def get_positions_for_market(self, market_condition_id: str) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE market_condition_id = ?", (market_condition_id,)
        ).fetchall()
        return [_row_to_position(row) for row in rows]

    def resolve_position(self, market_condition_id: str, outcome: str, payout: float) -> Position:
        position = self.get_position(market_condition_id, outcome)
        if position is None:
            raise ValueError(f"No position for {market_condition_id}/{outcome}")
        new_realized = position.realized_pnl + payout - position.total_cost
        self.conn.execute(
            """UPDATE positions SET is_resolved = 1, resolved_at = datetime('now'),
            realized_pnl = ?, shares = 0
            WHERE market_condition_id = ? AND outcome = ?""",
            (new_realized, market_condition_id, outcome),
        )
        self.conn.commit()
        return self.get_position(market_condition_id, outcome)

    def set_cache(self, key: str, data: dict | list) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO market_cache(cache_key, data, fetched_at)
            VALUES (?, ?, datetime('now'))""",
            (key, json.dumps(data)),
        )
        self.conn.commit()

    def get_cache(self, key: str) -> dict | list | None:
        row = self.conn.execute(
            "SELECT data FROM market_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        return None if row is None else json.loads(row["data"])


def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=row["id"],
        market_condition_id=row["market_condition_id"],
        market_slug=row["market_slug"],
        market_question=row["market_question"],
        outcome=row["outcome"],
        side=row["side"],
        order_type=row["order_type"],
        avg_price=row["avg_price"],
        amount_usd=row["amount_usd"],
        shares=row["shares"],
        fee_rate_bps=row["fee_rate_bps"],
        fee=row["fee"],
        slippage=row["slippage"],
        levels_filled=row["levels_filled"],
        is_partial=bool(row["is_partial"]),
        created_at=row["created_at"],
    )


def _row_to_position(row: sqlite3.Row) -> Position:
    return Position(
        market_condition_id=row["market_condition_id"],
        market_slug=row["market_slug"],
        market_question=row["market_question"],
        outcome=row["outcome"],
        shares=row["shares"],
        avg_entry_price=row["avg_entry_price"],
        total_cost=row["total_cost"],
        realized_pnl=row["realized_pnl"],
        is_resolved=bool(row["is_resolved"]),
        resolved_at=row["resolved_at"],
    )


__all__ = ["Database", "SQLiteStore"]
