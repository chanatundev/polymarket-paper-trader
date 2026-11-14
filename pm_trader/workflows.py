"""Agent-facing research workflows and bounded structured envelopes."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .activity import append_activity, cli_is_logging, record_command, record_simulation
from .api import CLOBAdapter, FixtureTransport, GammaAdapter, ResourceLimits
from .account import ACCOUNT_ID, load_account
from .errors import LocalValidationError
from .market_data import (
    aggregate_positions,
    book_summary,
    compact_book,
    estimate_fill,
    load_stored_fills,
    mark_position,
    market_outcomes,
    market_stats,
    market_trading_rules,
    portfolio_totals,
    price_summary,
    time_to_end,
    worst_freshness,
)
from .models import BookSnapshot, Decision, MarketState, PaperOrder, SimulationConfig
from .normalizers import make_provenance, normalize_book, normalize_market
from .recording import ReplayEngine
from .serialization import canonical_json, sanitize
from .simulation import SimulationEngine
from .storage import SQLiteStore


def as_jsonable(value: Any) -> Any:
    if isinstance(value, str):
        return value
    try:
        return json.loads(canonical_json(value))
    except (TypeError, ValueError):
        return sanitize(value)


DISCOVERY_ORDERS = {
    "volume_24hr": "volume24hr",
    "volume": "volumeNum",
    "liquidity": "liquidityNum",
    "end_date": "endDate",
    "start_date": "startDate",
}


def scan_market(market: MarketState, *, now: datetime | None = None) -> dict[str, Any]:
    """Discovery row small enough to scan a page. Inspect still returns the full record.

    ``hours_to_end`` is measured from ``now`` (default: current UTC time); a date-only
    ``end_time`` counts as the end of that day in UTC.
    """

    reasons = {
        key: market.unknown_reasons[key]
        for key in ("start_time", "end_time")
        if key in market.unknown_reasons
    }
    row: dict[str, Any] = {
        "condition_id": market.condition_id,
        "slug": market.slug,
        "question": market.question,
        "start_time": market.start_time,
        "end_time": market.end_time,
        **time_to_end(market.end_time, now),
        "active": market.active,
        "closed": market.closed,
        "accepting_orders": market.accepting_orders,
        "enable_order_book": market.enable_order_book,
        "freshness": market.freshness.value,
        "provenance_id": market.provenance.observation_id if market.provenance else None,
        "outcomes": market_outcomes(market),
    }
    stats = market_stats(market)
    if stats:
        row["stats"] = stats
    if reasons:
        row["unknown_reasons"] = reasons
    return row


_END_TIME_ASSUMPTION = {"date_only_end_time_is_end_of_day_utc": True}


def _decimal_arg(name: str, value: Any) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        result = None
    if result is None or not result.is_finite():
        raise LocalValidationError(f"{name} must be a decimal number", details={name: str(value)})
    return result


def envelope(
    *,
    result: Any = None,
    warnings: Iterable[str] = (),
    freshness: str = "unknown",
    provenance_ids: Iterable[str] = (),
    assumptions: Mapping[str, Any] | None = None,
    errors: Iterable[Mapping[str, Any]] = (),
    ok: bool = True,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "result": as_jsonable(result),
        "warnings": list(warnings),
        "freshness": freshness,
        "provenance_ids": list(dict.fromkeys(provenance_ids)),
        "assumptions": as_jsonable(dict(assumptions or {})),
        "errors": [as_jsonable(error) for error in errors],
    }


@dataclass
class ResearchService:
    store: SQLiteStore
    gamma: GammaAdapter | None = None
    clob: CLOBAdapter | None = None
    limits: ResourceLimits = field(default_factory=ResourceLimits)

    def __post_init__(self) -> None:
        self.gamma = self.gamma or GammaAdapter(limits=self.limits)
        self.clob = self.clob or CLOBAdapter(limits=self.limits)
        if self.store.db_path.name != ":memory:":
            self.clob.activity_dir = self.store.db_path.parent

    def discover(
        self,
        *,
        query: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        order: str | None = None,
        ascending: bool | None = None,
        open_only: bool = False,
    ) -> dict[str, Any]:
        if order is not None and order not in DISCOVERY_ORDERS:
            raise LocalValidationError(
                "unsupported discovery order",
                details={"order": order, "allowed": sorted(DISCOVERY_ORDERS)},
            )
        if query:
            found = self.gamma.search(query, limit=limit)
            payload = envelope(
                result={
                    "query": query,
                    "markets": [scan_market(market) for market in found.markets],
                    "events": found.events,
                    "tags": found.tags,
                    "profiles": found.profiles,
                    "pagination": found.pagination,
                },
                warnings=(),
                provenance_ids=(found.observation.provenance.observation_id,),
                assumptions={
                    "provider": "gamma",
                    "route": "/public-search",
                    **_END_TIME_ASSUMPTION,
                },
            )
        else:
            filters: dict[str, Any] = {}
            if order:
                filters["order"] = DISCOVERY_ORDERS[order]
            if open_only:
                filters["closed"] = "false"
                filters["active"] = "true"
            page = self.gamma.list_markets(
                limit=limit,
                after_cursor=cursor,
                # Ranked lists are most useful largest-first unless the caller says otherwise.
                ascending=(order is None) if ascending is None else ascending,
                filters=filters,
            )
            payload = envelope(
                result={
                    "markets": [scan_market(market) for market in page.items],
                    "next_cursor": page.next_cursor,
                    "query": page.query,
                    "response_metadata": page.response_metadata,
                },
                provenance_ids=(page.observation.provenance.observation_id,),
                assumptions={
                    "provider": "gamma",
                    "route": "/markets/keyset",
                    "pagination": "keyset",
                    **_END_TIME_ASSUMPTION,
                },
            )
        _record_service_command(
            self,
            SimpleNamespace(
                command="discover",
                query=query,
                limit=limit,
                cursor=cursor,
                fixture=isinstance(self.gamma.transport, FixtureTransport) or None,
            ),
            0,
            payload,
        )
        return payload

    def inspect_market(self, identifier: str) -> dict[str, Any]:
        market = self._market(identifier)
        return envelope(
            result=market,
            freshness=market.freshness.value,
            provenance_ids=(market.provenance.observation_id,) if market.provenance else (),
            assumptions={"provider_state_is_point_in_time": True},
        )

    def inspect_book(self, market: MarketState, outcome: str) -> dict[str, Any]:
        token = market.token_for(outcome)
        observation = self.clob.get_book(token.token_id)
        book = observation.value
        return envelope(
            result=book,
            freshness=book.freshness.value,
            provenance_ids=(observation.provenance.observation_id,),
            assumptions={"executable_book_state": True, "context_inputs_are_not_executable": True},
        )

    def quote(
        self,
        identifier: str,
        *,
        outcome: str | None = None,
        depth: int = 5,
        side: str | None = None,
        size: Decimal | str | None = None,
        price: Decimal | str | None = None,
        compact: bool = False,
    ) -> dict[str, Any]:
        """Live market rules plus a best-first book summary for one or every outcome.

        With ``side`` and ``size`` (and a required ``outcome``) it also returns an
        ``estimate``: a taker walk of that outcome's book, optionally capped at ``price``.
        ``compact`` keeps only top-of-book numbers per outcome.
        """

        if depth < 1 or depth > 50:
            raise LocalValidationError("depth must be between 1 and 50", details={"depth": depth})
        wants_estimate = side is not None or size is not None or price is not None
        if wants_estimate:
            if side is None or size is None:
                raise LocalValidationError("side and size are required together for an estimate")
            if side.lower() not in {"buy", "sell"}:
                raise LocalValidationError("side must be buy or sell", details={"side": side})
            if not outcome:
                raise LocalValidationError("an estimate needs --outcome (one book to walk)")
            size = _decimal_arg("size", size)
            price = None if price is None else _decimal_arg("price", price)
        market = self._market(identifier)
        tokens = [market.token_for(outcome)] if outcome else list(market.tokens)
        books = []
        snapshots = []
        provenance = [market.provenance.observation_id] if market.provenance else []
        for token in tokens:
            observation = self.clob.get_book(token.token_id)
            provenance.append(observation.provenance.observation_id)
            snapshots.append(observation.value)
            books.append(
                {"outcome": token.outcome, **book_summary(observation.value, depth=depth)}
            )
        result: dict[str, Any] = {
            "market": {
                **scan_market(market),
                "outcomes": market_outcomes(market, token_ids=True),
                "event_slug": market.event_slug,
                "category": market.category,
                **market_trading_rules(market),
            },
            "books": [compact_book(book) for book in books] if compact else books,
        }
        if wants_estimate:
            result["estimate"] = estimate_fill(
                snapshots[0], market, side=side, size=size, price=price
            )
        return envelope(
            result=result,
            freshness=worst_freshness(book["freshness"] for book in books),
            provenance_ids=provenance,
            assumptions={
                "book_is_point_in_time": True,
                "gamma_stats_are_indicative_not_executable": True,
                "prices_are_probabilities_per_share_paying_1": True,
                "freshness_is_book_age_at_receipt_fresh_within_30s": True,
                **_END_TIME_ASSUMPTION,
                **(
                    {"estimate_is_taker_walk_not_a_simulation": True, "estimate_fees": "per_level"}
                    if wants_estimate
                    else {}
                ),
            },
        )

    def price_history(
        self,
        identifier: str,
        *,
        outcome: str = "Yes",
        interval: str = "1w",
        fidelity: int | None = None,
        max_points: int = 120,
    ) -> dict[str, Any]:
        if max_points < 1 or max_points > 1000:
            raise LocalValidationError(
                "max_points must be between 1 and 1000", details={"max_points": max_points}
            )
        market = self._market(identifier)
        token = market.token_for(outcome)
        observation = self.clob.get_price_history(
            token.token_id, interval=interval, fidelity=fidelity
        )
        return envelope(
            result={
                "market": {"condition_id": market.condition_id, "slug": market.slug,
                           "question": market.question},
                "outcome": token.outcome,
                "token_id": token.token_id,
                "interval": interval,
                "fidelity_minutes": fidelity,
                **price_summary(observation.value, max_points=max_points),
            },
            provenance_ids=(observation.provenance.observation_id,),
            assumptions={"history_is_context_not_executable": True},
        )

    def portfolio(self, *, mark: bool = False, max_fills: int = 5000) -> dict[str, Any]:
        """Net paper exposure across every stored run, optionally marked to live midpoints."""

        fills, orders, truncated = load_stored_fills(self.store.conn, limit=max_fills)
        positions = aggregate_positions(fills, orders)
        warnings = ["fill_scan_truncated"] if truncated else []
        provenance: list[str] = []
        book_freshness: list[str] = []
        if mark:
            marked = []
            for index, row in enumerate(positions):
                quantity = row.get("quantity") or "0"
                if index >= 25 or quantity in {"0", None}:
                    marked.append(mark_position(row, None))
                    continue
                try:
                    observation = self.clob.get_book(str(row["asset_id"]))
                except Exception:  # noqa: BLE001 - one failed mark must not hide the rest.
                    warnings.append(f"mark_unavailable:{row['asset_id']}")
                    marked.append(mark_position(row, None))
                    continue
                provenance.append(observation.provenance.observation_id)
                summary = book_summary(observation.value, depth=1)
                book_freshness.append(summary["freshness"])
                midpoint = summary["midpoint"]
                marked.append(mark_position(row, None if midpoint is None else Decimal(midpoint)))
            positions = marked
        open_positions = [row for row in positions if row.get("quantity") not in {"0", None}]
        if mark:
            unmarked = sum(1 for row in open_positions if row.get("market_value") is None)
            if unmarked:
                warnings.append(f"unmarked_open_positions:{unmarked}")
        return envelope(
            result={
                "fills_scanned": len(fills),
                "open_positions": len(open_positions),
                "positions": positions,
                "totals": portfolio_totals(positions, marked=mark),
            },
            warnings=warnings,
            freshness=worst_freshness(book_freshness) if mark else "unknown",
            provenance_ids=provenance,
            assumptions={
                "aggregates_independent_runs": True,
                "cost_method": "average_cost",
                "realized_pnl_excludes_fees": True,
                "unrealized_pnl_is_midpoint_based_and_excludes_exit_fees": True,
                "net_pnl": "realized_pnl + unrealized_pnl (0 if unmarked) - fees",
                "totals_market_value_and_unrealized_cover_marked_positions_only": True,
                "mark": "live_book_midpoint" if mark else "none",
                "paper_pnl_is_not_live_pnl": True,
            },
        )

    def live_book(self, market: MarketState, outcome: str) -> BookSnapshot:
        return self.clob.get_book(market.token_for(outcome).token_id).value

    def simulate(
        self,
        *,
        market: MarketState,
        book: BookSnapshot,
        orders: Iterable[PaperOrder],
        config: SimulationConfig | None = None,
        dataset_id: str | None = None,
        account: bool = False,
    ) -> dict[str, Any]:
        """Paper-match orders. With ``account``, start from and update the data-dir account."""

        config = config or SimulationConfig()
        display_orders = tuple(_with_market_display(order, market) for order in orders)
        opening = None
        if account:
            state = load_account(self.store.conn, starting_cash=config.starting_cash)
            opening = state.opening_positions()
            config = replace(config, starting_cash=state.cash)
            display_orders = tuple(
                replace(order, metadata={**dict(order.metadata), "account": ACCOUNT_ID})
                for order in display_orders
            )
        simulation = SimulationEngine(self.store).run(
            market=market,
            book=book,
            orders=display_orders,
            config=config,
            dataset_id=dataset_id,
            opening_positions=opening,
        )
        _record(self, "simulate", market, simulation)
        warnings = list(simulation.report.warnings)
        for match in simulation.matches:
            warnings.extend(match.decision.warnings)
        result = as_jsonable(simulation)
        result["summary"] = [_order_summary(match) for match in simulation.matches]
        if account:
            result["account"] = load_account(
                self.store.conn, starting_cash=state.starting_cash
            ).to_dict()
        # A paper order that neither filled nor rests on the book did not happen: say so.
        failed = [item for item in result["summary"] if item["outcome"] == "rejected"]
        return envelope(
            result=result,
            warnings=warnings,
            freshness=simulation.matches[0].decision.freshness.value
            if simulation.matches
            else "unknown",
            provenance_ids=[
                identifier
                for match in simulation.matches
                for identifier in match.decision.provenance_ids
            ],
            assumptions={
                **dict(simulation.report.assumptions),
                "mode": "account" if account else "isolated_run",
            },
            errors=[
                {
                    "code": item["reason_code"] or "ORDER_REJECTED",
                    "category": "paper_order_rejected",
                    "message": "; ".join(item["reasons"]) or item["reason_code"],
                    "order_id": item["order_id"],
                }
                for item in failed
            ],
            ok=not failed,
        )

    def replay(
        self,
        *,
        dataset_path: str | Path,
        market: MarketState,
        orders: Iterable[PaperOrder],
        config: SimulationConfig | None = None,
    ) -> dict[str, Any]:
        display_orders = tuple(_with_market_display(order, market) for order in orders)
        simulation = ReplayEngine(self.store, limits=self.limits).replay_jsonl(
            dataset_path, market=market, orders=display_orders, config=config
        )
        _record(self, "replay", market, simulation)
        if not cli_is_logging() and self.store.db_path.name != ":memory:":
            try:
                append_activity(
                    self.store.db_path.parent,
                    {
                        "kind": "replay",
                        "command": "replay",
                        "dataset": Path(str(dataset_path)).name,
                        "source": "library",
                        "ok": True,
                        "error": None,
                    },
                )
            except OSError:
                pass
        return envelope(
            result=simulation,
            warnings=simulation.report.warnings,
            freshness="fresh",
            provenance_ids=[
                fill.observation_id
                for match in simulation.matches
                for fill in match.fills
                if fill.observation_id
            ],
            assumptions=simulation.report.assumptions,
        )

    @staticmethod
    def explain(decision: Decision) -> dict[str, Any]:
        return envelope(
            result=decision,
            warnings=decision.warnings,
            freshness=decision.freshness.value,
            provenance_ids=decision.provenance_ids,
            assumptions=decision.assumptions,
        )

    def portfolio_report(self, run_id: str) -> dict[str, Any]:
        report = self.store.get_report(run_id)
        if report is None:
            payload = envelope(
                result=None, errors=({"code": "REPORT_NOT_FOUND", "message": run_id},), ok=False
            )
        else:
            payload = envelope(result=report, assumptions={"paper_pnl_is_not_live_pnl": True})
        _record_service_command(
            self,
            SimpleNamespace(command="report", run_id=run_id),
            0 if payload["ok"] else 1,
            payload,
        )
        return payload

    def _market(self, identifier: str) -> MarketState:
        if identifier.startswith("0x"):
            return self.clob.get_market_info(identifier).value
        page = self.gamma.list_markets(
            limit=min(self.limits.max_page_size, 100), filters={"slug": identifier}
        )
        if not page.items:
            raise ValueError(f"market not found: {identifier}")
        market = page.items[0]
        _record_service_command(
            self,
            SimpleNamespace(
                command="market",
                market_command="get",
                identifier=identifier,
                fixture=isinstance(self.gamma.transport, FixtureTransport) or None,
            ),
            0,
            envelope(result=market),
        )
        return market


def _order_summary(match: Any) -> dict[str, Any]:
    """One flat line per order: did it fill, rest, or get rejected, and at what cost."""

    filled = sum((fill.quantity for fill in match.fills), Decimal(0))
    notional = sum((fill.notional for fill in match.fills), Decimal(0))
    fees = sum((fill.fee or Decimal(0) for fill in match.fills), Decimal(0))
    if match.fills:
        outcome = "partially_filled" if match.remaining_quantity > 0 else "filled"
    elif match.order.status == "open":
        outcome = "resting"
    else:
        outcome = "rejected"
    return {
        "order_id": match.order.order_id,
        "side": match.order.side,
        "outcome": outcome,
        "reason_code": match.decision.reason_code,
        "reasons": list(match.decision.reasons),
        "filled_quantity": str(filled),
        "remaining_quantity": str(match.remaining_quantity),
        "average_price": str(notional / filled) if filled else None,
        "notional": str(notional),
        "fees": str(fees),
    }


def _with_market_display(order: PaperOrder, market: MarketState) -> PaperOrder:
    metadata = dict(order.metadata)
    metadata["market_question"] = metadata.get("market_question") or market.question
    metadata["market_slug"] = metadata.get("market_slug") or market.slug
    token = next((item for item in market.tokens if item.token_id == order.asset_id), None)
    if token is not None:
        metadata["outcome"] = metadata.get("outcome") or token.outcome
    return replace(order, metadata=metadata)


def _record(service: ResearchService, command: str, market: MarketState, simulation: Any) -> None:
    path = service.store.db_path
    if path.name == ":memory:":
        return
    try:
        record_simulation(path.parent, command=command, market=market, simulation=simulation)
    except OSError:
        pass


def _record_service_command(
    service: ResearchService, args: Any, code: int, payload: dict[str, Any]
) -> None:
    path = service.store.db_path
    if path.name == ":memory:" or cli_is_logging():
        return
    try:
        record_command(path.parent, args, code, payload)
    except OSError:
        pass


def load_market_fixture(path: str | Path) -> MarketState:
    fixture_path = Path(path)
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping) and "market" in raw and isinstance(raw["market"], Mapping):
        raw = raw["market"]
    elif isinstance(raw, Mapping) and isinstance(raw.get("markets"), list) and raw["markets"]:
        raw = raw["markets"][0]
    provenance = make_provenance(
        source="fixture",
        endpoint=str(fixture_path),
        raw=raw,
        retrieved_at=(raw.get("retrieved_at") if isinstance(raw, Mapping) else None),
        received_at=(raw.get("received_at") if isinstance(raw, Mapping) else None),
        schema_version="fixture-v1",
    )
    return normalize_market(
        raw, provenance=provenance, endpoint=str(fixture_path), provider="fixture"
    )


def load_book_fixture(path: str | Path) -> BookSnapshot:
    fixture_path = Path(path)
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping) and "book" in raw and isinstance(raw["book"], Mapping):
        raw = raw["book"]
    provenance = make_provenance(
        source="fixture",
        endpoint=str(fixture_path),
        raw=raw,
        retrieved_at=(raw.get("retrieved_at") if isinstance(raw, Mapping) else None),
        received_at=(raw.get("received_at") if isinstance(raw, Mapping) else None),
        schema_version="fixture-v1",
    )
    return normalize_book(
        raw, provenance=provenance, endpoint=str(fixture_path), provider="fixture"
    )
