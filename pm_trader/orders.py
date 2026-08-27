"""Pure SQLite helpers for compatibility GTC/GTD paper orders.

These are intentionally separate from the canonical ``PaperOrder`` flow:
they preserve the original CRUD surface while remaining paper-only and
without any provider write capability.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone


@dataclass
class LimitOrder:
    id: int
    market_slug: str
    market_condition_id: str
    outcome: str
    side: str
    amount: float
    limit_price: float
    order_type: str
    expires_at: str | None
    status: str
    created_at: str
    filled_at: str | None = None


ORDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS limit_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    market_condition_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (length(outcome) > 0),
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    amount REAL NOT NULL,
    limit_price REAL NOT NULL,
    order_type TEXT NOT NULL CHECK (order_type IN ('gtc', 'gtd')),
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'filled', 'cancelled', 'expired', 'rejected')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    filled_at TEXT
);
"""


def init_orders_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(ORDERS_SCHEMA)
    conn.commit()


def _normalize_timestamp(timestamp: str) -> str:
    return timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp


def create_order(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    market_condition_id: str,
    outcome: str,
    side: str,
    amount: float,
    limit_price: float,
    order_type: str = "gtc",
    expires_at: str | None = None,
) -> LimitOrder:
    side = side.casefold()
    order_type = order_type.casefold()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if order_type not in {"gtc", "gtd"}:
        raise ValueError("order_type must be gtc or gtd")
    if not outcome.strip():
        raise ValueError("outcome cannot be empty")
    if not 0 < limit_price < 1:
        raise ValueError("limit_price must be between zero and one")
    if amount <= 0:
        raise ValueError("amount must be positive")
    if order_type == "gtd" and not expires_at:
        raise ValueError("GTD orders require expires_at")
    normalized_expires = _normalize_timestamp(expires_at) if expires_at else None
    cursor = conn.execute(
        """INSERT INTO limit_orders(
            market_slug, market_condition_id, outcome, side, amount,
            limit_price, order_type, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            market_slug,
            market_condition_id,
            outcome,
            side,
            amount,
            limit_price,
            order_type,
            normalized_expires,
        ),
    )
    conn.commit()
    return _get_order(conn, cursor.lastrowid)


def get_pending_orders(conn: sqlite3.Connection) -> list[LimitOrder]:
    rows = conn.execute(
        "SELECT * FROM limit_orders WHERE status = 'pending' ORDER BY id"
    ).fetchall()
    return [_row_to_order(row) for row in rows]


def get_order(conn: sqlite3.Connection, order_id: int) -> LimitOrder | None:
    return _get_order(conn, order_id)


def cancel_order(conn: sqlite3.Connection, order_id: int) -> LimitOrder | None:
    order = _get_order(conn, order_id)
    if order is None or order.status != "pending":
        return None
    conn.execute("UPDATE limit_orders SET status = 'cancelled' WHERE id = ?", (order_id,))
    conn.commit()
    return _get_order(conn, order_id)


def cancel_all_orders(conn: sqlite3.Connection) -> list[LimitOrder]:
    pending = get_pending_orders(conn)
    if not pending:
        return []
    conn.execute("UPDATE limit_orders SET status = 'cancelled' WHERE status = 'pending'")
    conn.commit()
    return [replace(order, status="cancelled") for order in pending]


def mark_filled(conn: sqlite3.Connection, order_id: int) -> LimitOrder:
    conn.execute(
        "UPDATE limit_orders SET status = 'filled', filled_at = datetime('now') WHERE id = ?",
        (order_id,),
    )
    conn.commit()
    return _get_order(conn, order_id)


def reject_order(conn: sqlite3.Connection, order_id: int) -> LimitOrder:
    conn.execute("UPDATE limit_orders SET status = 'rejected' WHERE id = ?", (order_id,))
    conn.commit()
    return _get_order(conn, order_id)


def expire_orders(conn: sqlite3.Connection, *, now: str | None = None) -> list[LimitOrder]:
    normalized_now = _normalize_timestamp(
        now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    rows = conn.execute(
        """SELECT * FROM limit_orders
        WHERE status = 'pending' AND order_type = 'gtd' AND expires_at <= ?""",
        (normalized_now,),
    ).fetchall()
    if rows:
        conn.execute(
            """UPDATE limit_orders SET status = 'expired'
            WHERE status = 'pending' AND order_type = 'gtd' AND expires_at <= ?""",
            (normalized_now,),
        )
        conn.commit()
    return [replace(_row_to_order(row), status="expired") for row in rows]


def should_fill(order: LimitOrder, best_price: float) -> bool:
    if order.side == "buy":
        return best_price <= order.limit_price
    return best_price >= order.limit_price


def _get_order(conn: sqlite3.Connection, order_id: int | None) -> LimitOrder | None:
    if order_id is None:
        return None
    row = conn.execute("SELECT * FROM limit_orders WHERE id = ?", (order_id,)).fetchone()
    return None if row is None else _row_to_order(row)


def _row_to_order(row: sqlite3.Row) -> LimitOrder:
    return LimitOrder(
        id=row["id"],
        market_slug=row["market_slug"],
        market_condition_id=row["market_condition_id"],
        outcome=row["outcome"],
        side=row["side"],
        amount=row["amount"],
        limit_price=row["limit_price"],
        order_type=row["order_type"],
        expires_at=row["expires_at"],
        status=row["status"],
        created_at=row["created_at"],
        filled_at=row["filled_at"],
    )


__all__ = [
    "LimitOrder",
    "cancel_all_orders",
    "cancel_order",
    "create_order",
    "expire_orders",
    "get_order",
    "get_pending_orders",
    "init_orders_schema",
    "mark_filled",
    "reject_order",
    "should_fill",
]
