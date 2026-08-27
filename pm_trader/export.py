"""CSV and JSON export helpers for the compatibility paper account surface."""

from __future__ import annotations

import csv
import io
import json

from .models import Trade


def export_trades_csv(trades: list[Trade]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "timestamp",
            "market_slug",
            "side",
            "outcome",
            "shares",
            "avg_price",
            "amount_usd",
            "fee",
            "order_type",
            "levels_filled",
            "is_partial",
        ]
    )
    for trade in trades:
        writer.writerow(
            [
                trade.id,
                trade.created_at,
                trade.market_slug,
                trade.side,
                trade.outcome,
                f"{trade.shares:.6f}",
                f"{trade.avg_price:.6f}",
                f"{trade.amount_usd:.2f}",
                f"{trade.fee:.4f}",
                trade.order_type,
                trade.levels_filled,
                "true" if trade.is_partial else "false",
            ]
        )
    return output.getvalue()


def export_trades_json(trades: list[Trade]) -> str:
    return json.dumps(
        [
            {
                "id": trade.id,
                "timestamp": trade.created_at,
                "market_slug": trade.market_slug,
                "market_condition_id": trade.market_condition_id,
                "side": trade.side,
                "outcome": trade.outcome,
                "shares": trade.shares,
                "avg_price": trade.avg_price,
                "amount_usd": trade.amount_usd,
                "fee": trade.fee,
                "fee_rate_bps": trade.fee_rate_bps,
                "order_type": trade.order_type,
                "slippage": trade.slippage,
                "levels_filled": trade.levels_filled,
                "is_partial": trade.is_partial,
            }
            for trade in trades
        ],
        indent=2,
    )


def export_positions_csv(positions: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "market_slug",
            "outcome",
            "shares",
            "avg_entry_price",
            "total_cost",
            "live_price",
            "current_value",
            "unrealized_pnl",
            "percent_pnl",
        ]
    )
    for position in positions:
        writer.writerow(
            [
                position["market_slug"],
                position["outcome"],
                f"{position['shares']:.6f}",
                f"{position['avg_entry_price']:.6f}",
                f"{position['total_cost']:.2f}",
                f"{position['live_price']:.6f}",
                f"{position['current_value']:.2f}",
                f"{position['unrealized_pnl']:.2f}",
                f"{position['percent_pnl']:.2f}",
            ]
        )
    return output.getvalue()


def export_positions_json(positions: list[dict]) -> str:
    return json.dumps(positions, indent=2)


__all__ = [
    "export_positions_csv",
    "export_positions_json",
    "export_trades_csv",
    "export_trades_json",
]
