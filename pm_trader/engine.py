"""Compatibility paper-trading facade over the canonical read/simulate core.

The original package exposed an ``Engine`` with account, order, portfolio, and
history methods. This facade keeps those imports useful while every execution
path remains a public-read plus local-paper-calculation path. It has no live
order client, signer, cancellation endpoint, redemption path, or fund mover.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .api import PolymarketClient
from .db import Database
from .errors import (
    AmbiguousResolutionError,
    ApiError,
    InsufficientBalanceError,
    InvalidOutcomeError,
    MarketClosedError,
    NoPositionError,
    NotInitializedError,
    OrderRejectedError,
    TraderError,
)
from .models import (
    Account,
    FeeSchedule,
    Market,
    MarketState,
    OrderBook,
    ResolveResult,
    TradeResult,
)
from .orderbook import simulate_buy_fill, simulate_sell_fill
from .orders import (
    cancel_all_orders,
    cancel_order,
    create_order,
    expire_orders,
    get_pending_orders,
    init_orders_schema,
    mark_filled,
    reject_order,
)

MIN_ORDER_USD = 1.0


class Engine:
    """Paper account facade retained for source-compatible callers."""

    def __init__(self, data_dir: Path | str, *, api: PolymarketClient | None = None) -> None:
        self.db = Database(data_dir)
        self.db.init_schema()
        init_orders_schema(self.db.conn)
        self.api = api or PolymarketClient(self.db)

    def close(self) -> None:
        self.api.close()
        self.db.close()

    def init_account(self, balance: float = 10_000.0) -> Account:
        if balance < 0:
            raise OrderRejectedError("starting balance cannot be negative")
        return self.db.init_account(balance)

    def get_account(self) -> Account:
        account = self.db.get_account()
        if account is None:
            raise NotInitializedError()
        return account

    def reset(self) -> None:
        self.db.reset()
        self.db.init_schema()
        init_orders_schema(self.db.conn)

    def buy(
        self, slug_or_id: str, outcome: str, amount_usd: float, order_type: str = "fok"
    ) -> TradeResult:
        account = self.get_account()
        if amount_usd < MIN_ORDER_USD:
            raise OrderRejectedError(f"Minimum order size is ${MIN_ORDER_USD:.2f}")
        market = self._market(slug_or_id)
        outcome = self._validate_outcome(outcome, market)
        if market.closed:
            raise MarketClosedError(market.slug)
        book = self._book(market, outcome)
        fee_rate_bps = self._fee_rate(market, outcome)
        fill = simulate_buy_fill(book, amount_usd, fee_rate_bps, order_type)
        if not fill.fills:
            raise OrderRejectedError("Insufficient liquidity in order book")
        total_outflow = fill.total_cost + fill.fee
        if total_outflow > account.cash:
            raise InsufficientBalanceError(total_outflow, account.cash)
        self.db.update_cash(account.cash - total_outflow)
        trade = self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            side="buy",
            order_type=order_type,
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )
        self._update_position_after_buy(
            market, outcome, fill.total_shares, fill.total_cost + fill.fee
        )
        return TradeResult(trade=trade, account=self.get_account())

    def sell(
        self, slug_or_id: str, outcome: str, shares: float, order_type: str = "fok"
    ) -> TradeResult:
        account = self.get_account()
        market = self._market(slug_or_id)
        outcome = self._validate_outcome(outcome, market)
        position = self.db.get_position(market.condition_id, outcome)
        if position is None or position.shares <= 0:
            raise NoPositionError(market.slug, outcome)
        if shares <= 0 or shares > position.shares:
            raise OrderRejectedError(
                f"Cannot sell {shares:.4f} shares, only hold {position.shares:.4f}"
            )
        if market.closed:
            raise MarketClosedError(market.slug)
        book = self._book(market, outcome)
        fee_rate_bps = self._fee_rate(market, outcome)
        fill = simulate_sell_fill(book, shares, fee_rate_bps, order_type)
        if not fill.fills:
            raise OrderRejectedError("Insufficient liquidity in order book")
        net_proceeds = fill.total_cost - fill.fee
        self.db.update_cash(account.cash + net_proceeds)
        trade = self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            side="sell",
            order_type=order_type,
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )
        self._update_position_after_sell(market, outcome, fill.total_shares, net_proceeds)
        return TradeResult(trade=trade, account=self.get_account())

    def get_portfolio(self) -> list[dict[str, Any]]:
        self.get_account()
        result = []
        for position in self.db.get_open_positions():
            try:
                market = self._market(position.market_slug)
                token_id = market.get_token_id(position.outcome)
                live_price = float(self.api.get_midpoint(token_id))
            except (TraderError, OSError, ValueError, TypeError, KeyError, AttributeError):
                live_price = 0.0
            result.append(
                {
                    "market_slug": position.market_slug,
                    "market_question": position.market_question,
                    "outcome": position.outcome,
                    "shares": position.shares,
                    "avg_entry_price": position.avg_entry_price,
                    "total_cost": position.total_cost,
                    "live_price": live_price,
                    "current_value": position.current_value(live_price),
                    "unrealized_pnl": position.unrealized_pnl(live_price),
                    "percent_pnl": position.percent_pnl(live_price),
                }
            )
        return result

    def get_balance(self) -> dict[str, float]:
        account = self.get_account()
        positions_value = sum(item["current_value"] for item in self.get_portfolio())
        total_value = account.cash + positions_value
        return {
            "cash": account.cash,
            "starting_balance": account.starting_balance,
            "positions_value": positions_value,
            "total_value": total_value,
            "pnl": total_value - account.starting_balance,
        }

    def get_history(self, limit: int = 50):
        self.get_account()
        return self.db.get_trades(limit)

    def place_limit_order(
        self,
        slug_or_id: str,
        outcome: str,
        side: str,
        amount: float,
        limit_price: float,
        order_type: str = "gtc",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        self.get_account()
        side = side.casefold()
        order_type = order_type.casefold()
        if side not in {"buy", "sell"}:
            raise OrderRejectedError(f"Invalid side: {side!r}")
        if not 0 < limit_price < 1:
            raise OrderRejectedError("Limit price must be between 0 and 1")
        if order_type not in {"gtc", "gtd"}:
            raise OrderRejectedError("Invalid order type")
        if order_type == "gtd" and not expires_at:
            raise OrderRejectedError("GTD orders require expires_at timestamp")
        market = self._market(slug_or_id)
        normalized_outcome = self._validate_outcome(outcome, market)
        order = create_order(
            self.db.conn,
            market_slug=market.slug,
            market_condition_id=market.condition_id,
            outcome=normalized_outcome,
            side=side,
            amount=amount,
            limit_price=limit_price,
            order_type=order_type,
            expires_at=expires_at,
        )
        return _order_to_dict(order)

    def get_pending_orders(self) -> list[dict[str, Any]]:
        return [_order_to_dict(order) for order in get_pending_orders(self.db.conn)]

    def cancel_limit_order(self, order_id: int) -> dict[str, Any] | None:
        order = cancel_order(self.db.conn, order_id)
        return _order_to_dict(order) if order else None

    def cancel_all_orders(self) -> list[dict[str, Any]]:
        return [_order_to_dict(order) for order in cancel_all_orders(self.db.conn)]

    def check_orders(self) -> list[dict[str, Any]]:
        self.get_account()
        results = [
            {"order": _order_to_dict(order), "action": "expired"}
            for order in expire_orders(self.db.conn)
        ]
        for order in get_pending_orders(self.db.conn):
            try:
                market = self._market(order.market_slug)
                book = self._book(market, order.outcome)
                fee_rate_bps = self._fee_rate(market, order.outcome)
                fill = (
                    simulate_buy_fill(
                        book,
                        order.amount,
                        fee_rate_bps,
                        "fak",
                        max_price=order.limit_price,
                    )
                    if order.side == "buy"
                    else simulate_sell_fill(
                        book,
                        order.amount,
                        fee_rate_bps,
                        "fak",
                        min_price=order.limit_price,
                    )
                )
                if not fill.fills:
                    continue
                if order.side == "buy":
                    self._execute_limit_buy(market, order, fill, fee_rate_bps)
                else:
                    self._execute_limit_sell(market, order, fill, fee_rate_bps)
                results.append(
                    {
                        "order": _order_to_dict(mark_filled(self.db.conn, order.id)),
                        "action": "filled",
                    }
                )
            except (OrderRejectedError, InsufficientBalanceError, NoPositionError):
                results.append(
                    {
                        "order": _order_to_dict(reject_order(self.db.conn, order.id)),
                        "action": "rejected",
                    }
                )
            except (TraderError, OSError, ValueError, TypeError, KeyError, AttributeError):
                pass
        return results

    def watch_prices(
        self, slugs_or_ids: list[str], outcomes: list[str] | None = None
    ) -> list[dict[str, Any]]:
        results = []
        for identifier in slugs_or_ids:
            try:
                market = self._market(identifier)
                for outcome in outcomes or ["yes"]:
                    normalized = self._validate_outcome(outcome, market)
                    token = market.get_token_id(normalized)
                    results.append(
                        {
                            "market_slug": market.slug,
                            "outcome": normalized,
                            "midpoint": self.api.get_midpoint(token),
                            "condition_id": market.condition_id,
                        }
                    )
            except (TraderError, OSError, ValueError, TypeError, KeyError, AttributeError):
                pass
        return results

    def resolve_market(self, slug_or_id: str) -> list[ResolveResult]:
        self.get_account()
        market = self._market(slug_or_id)
        if not market.closed:
            raise MarketClosedError(f"{market.slug} is not yet closed/resolved")
        positions = self.db.get_positions_for_market(market.condition_id)
        if not positions:
            raise NoPositionError(market.slug, "any")
        winner = _determine_winner(market)
        results = []
        for position in positions:
            if position.is_resolved or position.shares <= 0:
                continue
            payout = position.shares if position.outcome == winner else 0.0
            resolved = self.db.resolve_position(market.condition_id, position.outcome, payout)
            self.db.update_cash(self.get_account().cash + payout)
            results.append(ResolveResult(resolved, payout, self.get_account()))
        return results

    def resolve_all(self) -> list[ResolveResult]:
        self.get_account()
        results = []
        seen: set[str] = set()
        for position in self.db.get_open_positions():
            if position.market_condition_id in seen:
                continue
            try:
                market = self._market(position.market_slug)
                if market.closed:
                    seen.add(position.market_condition_id)
                    results.extend(self.resolve_market(market.slug))
            except (ApiError, OSError, TimeoutError):
                continue
        return results

    def _market(self, identifier: str) -> Market:
        try:
            value = self.api.get_market(identifier)
        except Exception as exc:
            if isinstance(exc, (OrderRejectedError, MarketClosedError)):
                raise
            raise ApiError(str(exc)) from exc
        if isinstance(value, Market):
            return value
        if isinstance(value, MarketState):
            return _legacy_market(value)
        raise ApiError("public client returned an unsupported market record")

    def _book(self, market: Market, outcome: str) -> OrderBook:
        token_id = market.get_token_id(outcome)
        value = self.api.get_order_book(token_id)
        if isinstance(value, OrderBook):
            return value
        return OrderBook(
            bids=[type(level)(float(level.price), float(level.size)) for level in value.bids],
            asks=[type(level)(float(level.price), float(level.size)) for level in value.asks],
        )

    def _fee_rate(self, market: Market, outcome: str) -> int:
        value = self.api.get_fee_rate(market.get_token_id(outcome))
        if isinstance(value, int):
            return value
        if isinstance(value, FeeSchedule):
            return int(value.rate * 10_000)
        if value is None:
            raise OrderRejectedError("fee schedule unavailable")
        raise OrderRejectedError("unsupported fee schedule")

    @staticmethod
    def _validate_outcome(outcome: str, market: Market) -> str:
        normalized = outcome.casefold().strip()
        valid = [item.casefold() for item in market.outcomes]
        if normalized not in valid:
            raise InvalidOutcomeError(outcome, valid)
        return normalized

    def _update_position_after_buy(
        self, market: Market, outcome: str, shares: float, cost: float
    ) -> None:
        existing = self.db.get_position(market.condition_id, outcome)
        total_shares = (existing.shares if existing else 0) + shares
        total_cost = (existing.total_cost if existing else 0) + cost
        self.db.upsert_position(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            shares=total_shares,
            avg_entry_price=total_cost / total_shares if total_shares else 0,
            total_cost=total_cost,
            realized_pnl=existing.realized_pnl if existing else 0,
        )

    def _update_position_after_sell(
        self, market: Market, outcome: str, shares: float, proceeds: float
    ) -> None:
        existing = self.db.get_position(market.condition_id, outcome)
        if existing is None:
            return
        cost_of_sold = existing.avg_entry_price * shares
        self.db.upsert_position(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            shares=max(existing.shares - shares, 0),
            avg_entry_price=existing.avg_entry_price,
            total_cost=max(existing.total_cost - cost_of_sold, 0),
            realized_pnl=existing.realized_pnl + proceeds - cost_of_sold,
        )

    def _execute_limit_buy(self, market: Market, order: Any, fill: Any, fee_rate_bps: int) -> None:
        account = self.get_account()
        total = fill.total_cost + fill.fee
        if total > account.cash:
            raise InsufficientBalanceError(total, account.cash)
        self.db.update_cash(account.cash - total)
        self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=order.outcome,
            side="buy",
            order_type="fak",
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )
        self._update_position_after_buy(market, order.outcome, fill.total_shares, total)

    def _execute_limit_sell(self, market: Market, order: Any, fill: Any, fee_rate_bps: int) -> None:
        position = self.db.get_position(market.condition_id, order.outcome)
        if position is None or fill.total_shares > position.shares:
            raise NoPositionError(market.slug, order.outcome)
        self.db.update_cash(self.get_account().cash + fill.total_cost - fill.fee)
        self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=order.outcome,
            side="sell",
            order_type="fak",
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )
        self._update_position_after_sell(
            market, order.outcome, fill.total_shares, fill.total_cost - fill.fee
        )


def _legacy_market(market: MarketState) -> Market:
    return Market(
        condition_id=market.condition_id,
        slug=market.slug,
        question=market.question,
        description=market.description or "",
        outcomes=list(market.outcomes),
        outcome_prices=[
            float(price) if price is not None else 0.0 for price in market.outcome_prices
        ],
        tokens=[{"token_id": token.token_id, "outcome": token.outcome} for token in market.tokens],
        active=market.active is not False,
        closed=market.closed is True,
        fee_rate_bps=int(market.fee_schedule.rate * 10_000) if market.fee_schedule else 0,
        tick_size=float(market.tick_size or 0.01),
    )


def _determine_winner(market: Market) -> str:
    for index, outcome in enumerate(market.outcomes):
        if index < len(market.outcome_prices) and market.outcome_prices[index] >= 0.99:
            return outcome.casefold()
    raise AmbiguousResolutionError(market.slug, dict(zip(market.outcomes, market.outcome_prices)))


def _order_to_dict(order: Any) -> dict[str, Any]:
    return {
        "id": order.id,
        "market_slug": order.market_slug,
        "market_condition_id": order.market_condition_id,
        "outcome": order.outcome,
        "side": order.side,
        "amount": order.amount,
        "limit_price": order.limit_price,
        "order_type": order.order_type,
        "expires_at": order.expires_at,
        "status": order.status,
        "created_at": order.created_at,
        "filled_at": order.filled_at,
    }


__all__ = ["MIN_ORDER_USD", "Engine"]
