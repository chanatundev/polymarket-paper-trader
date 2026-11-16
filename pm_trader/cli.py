"""Bounded JSON-first CLI for discovery, inspection, simulation, and replay."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from .activity import begin_cli_log, end_cli_log, record_command
from .api import ResourceLimits
from .errors import TraderError
from .models import PaperOrder, SimulationConfig
from .normalizers import make_provenance, normalize_market
from .serialization import bounded_json, canonical_json
from .storage import SQLiteStore
from .workflows import (
    ResearchService,
    envelope,
    load_book_fixture,
    load_market_fixture,
    DISCOVERY_ORDERS,
    scan_market,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pm-trader", description="Paper-only Polymarket research and replay workbench"
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("PM_TRADER_DATA_DIR", ".pm-trader"),
        help="SQLite data directory",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="serve the optional read-only paper-trading dashboard instead of a subcommand",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8766,
        help="loopback TCP port for --dashboard (0 chooses a free port)",
    )
    # A missing command is rejected in run()/main() only when --dashboard is absent.
    sub = parser.add_subparsers(dest="command", required=False)

    discover = sub.add_parser(
        "discover", help="discover markets through keyset pagination or public search"
    )
    discover.add_argument("--query")
    discover.add_argument("--limit", type=int, default=20)
    discover.add_argument("--cursor")
    discover.add_argument("--fixture", type=Path)
    discover.add_argument(
        "--order",
        choices=sorted(DISCOVERY_ORDERS),
        help="rank keyset results (largest first unless --ascending)",
    )
    discover.add_argument("--ascending", action="store_true", help="sort smallest first")
    discover.add_argument(
        "--open-only", action="store_true", help="only active markets that are not closed"
    )

    market = sub.add_parser(
        "market", aliases=["markets"], help="market discovery and inspection aliases"
    )
    market_sub = market.add_subparsers(dest="market_command", required=True)
    market_list = market_sub.add_parser("list")
    market_list.add_argument("--limit", type=int, default=20)
    market_list.add_argument("--fixture", type=Path)
    market_search = market_sub.add_parser("search")
    market_search.add_argument("query")
    market_search.add_argument("--limit", type=int, default=20)
    market_search.add_argument("--fixture", type=Path)
    market_get = market_sub.add_parser("get")
    market_get.add_argument("identifier")
    market_get.add_argument("--fixture", type=Path)

    quote = sub.add_parser(
        "quote", help="live market rules plus best-first order-book summary per outcome"
    )
    quote.add_argument("identifier", help="market slug or 0x condition id")
    quote.add_argument("--outcome", help="quote one outcome (default: every outcome)")
    quote.add_argument("--depth", type=int, default=5, help="book levels per side (1-50)")
    quote.add_argument(
        "--side",
        choices=("buy", "sell"),
        help="with --size and --outcome: estimate a taker fill against the live book",
    )
    quote.add_argument("--size", type=Decimal, help="shares to estimate (needs --side)")
    quote.add_argument(
        "--price", type=Decimal, help="limit price for the estimate (worse levels are skipped)"
    )
    quote.add_argument(
        "--compact",
        action="store_true",
        help="per book: best bid/ask, midpoint, spread, age, depth within 1c only",
    )

    prices = sub.add_parser("prices", help="live price history and summary for one outcome")
    prices.add_argument("identifier", help="market slug or 0x condition id")
    prices.add_argument("--outcome", default="Yes")
    prices.add_argument(
        "--interval", default="1w", choices=("1h", "6h", "1d", "1w", "1m", "max", "all")
    )
    prices.add_argument("--fidelity", type=int, help="bar size in minutes")
    prices.add_argument("--max-points", type=int, default=120)

    portfolio = sub.add_parser(
        "portfolio", help="net paper positions across every stored run in this data directory"
    )
    portfolio.add_argument(
        "--mark", action="store_true", help="mark open positions to the live book midpoint"
    )

    settle = sub.add_parser(
        "settle", help="pay out account positions in a resolved market at its final prices"
    )
    settle.add_argument("identifier", nargs="?", help="market slug or 0x condition id")
    settle.add_argument("--market-fixture", type=Path, help="offline market JSON instead")

    inspect = sub.add_parser("inspect", help="inspect market or book")
    inspect_sub = inspect.add_subparsers(dest="inspect_command", required=True)
    inspect_market = inspect_sub.add_parser("market")
    inspect_market.add_argument("--fixture", type=Path, required=True)
    inspect_book = inspect_sub.add_parser("book")
    inspect_book.add_argument("--fixture", type=Path, required=True)

    simulate = sub.add_parser("simulate", help="simulate paper orders against a declared book")
    simulate.add_argument(
        "--market", help="live market slug or 0x condition id (fetches the live book)"
    )
    simulate.add_argument("--market-fixture", type=Path)
    simulate.add_argument("--book-fixture", type=Path)
    simulate.add_argument("--order-id", default="cli-order-1")
    simulate.add_argument("--side", choices=("buy", "sell"), default="buy")
    simulate.add_argument("--outcome", default="Yes")
    simulate.add_argument("--price", type=Decimal, default=Decimal("0.66"))
    simulate.add_argument("--size", type=Decimal, default=Decimal(10))
    simulate.add_argument("--tif", choices=("gtc", "gtd", "fak", "fok"), default="fok")
    simulate.add_argument("--post-only", action="store_true")
    simulate.add_argument("--starting-cash", type=Decimal, default=Decimal(10000))
    simulate.add_argument("--latency-ms", type=int, default=0)
    simulate.add_argument("--dataset-id")
    simulate.add_argument(
        "--isolated",
        action="store_true",
        help="one-off run from --starting-cash with no positions, outside the data-dir account",
    )

    replay = sub.add_parser("replay", help="replay a bounded recorded JSONL dataset")
    replay.add_argument("--dataset", type=Path, required=True)
    replay.add_argument("--market-fixture", type=Path, required=True)
    replay.add_argument("--order-id", default="replay-order-1")
    replay.add_argument("--side", choices=("buy", "sell"), default="buy")
    replay.add_argument("--outcome", default="Yes")
    replay.add_argument("--price", type=Decimal, default=Decimal("0.66"))
    replay.add_argument("--size", type=Decimal, default=Decimal(10))
    replay.add_argument("--tif", choices=("gtc", "gtd", "fak", "fok"), default="fok")
    replay.add_argument("--starting-cash", type=Decimal, default=Decimal(10000))

    report = sub.add_parser("report", help="read a stored simulation report")
    report.add_argument("--run-id", required=True)

    explain = sub.add_parser("explain", help="return a structured decision explanation")
    explain.add_argument("--status", default="rejected")
    explain.add_argument("--reason-code", default="UNKNOWN")
    explain.add_argument("--reason", action="append", default=[])
    explain.add_argument("--freshness", default="unknown")

    gui = sub.add_parser("gui", help="serve the local read-only trade-history GUI")
    gui.add_argument(
        "--port", type=int, default=8765, help="loopback TCP port (0 chooses a free port)"
    )
    gui.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="open the loopback URL in the default browser",
    )

    sub.add_parser("safety", help="show the immutable paper-only boundary")
    return parser


def _fixture_page(
    path: Path, *, search: bool = False, query: str = "", limit: int = 20
) -> dict[str, Any]:
    if limit < 1 or limit > ResourceLimits().max_page_size:
        raise ValueError(f"limit is outside the configured page-size bound: {limit}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    provenance = make_provenance(
        source="fixture", endpoint=str(path), raw=raw, schema_version="fixture-v1"
    )
    if search:
        events = raw.get("events", []) if isinstance(raw, dict) else []
        markets = []
        for event in events:
            markets.extend(event.get("markets", []) if isinstance(event, dict) else [])
        normalized = [
            scan_market(
                normalize_market(
                    item, provenance=provenance, endpoint=str(path), provider="fixture"
                )
            )
            for item in markets
        ]
        return envelope(
            result={
                "query": query,
                "markets": normalized[:limit],
                "events": events,
                "tags": raw.get("tags", []),
                "pagination": raw.get("pagination", {}),
            },
            provenance_ids=(provenance.observation_id,),
            assumptions={"provider": "fixture", "route": "/public-search"},
        )
    market_payloads = raw.get("markets", []) if isinstance(raw, dict) else raw
    normalized = [
        scan_market(
            normalize_market(item, provenance=provenance, endpoint=str(path), provider="fixture")
        )
        for item in market_payloads
    ]
    return envelope(
        result={
            "markets": normalized[:limit],
            "next_cursor": raw.get("next_cursor") if isinstance(raw, dict) else None,
            "response_metadata": {"fixture": str(path)},
        },
        provenance_ids=(provenance.observation_id,),
        assumptions={"provider": "fixture", "route": "/markets/keyset"},
    )


def _order(args: argparse.Namespace, market: Any) -> PaperOrder:
    token = market.token_for(getattr(args, "outcome", "Yes"))
    return PaperOrder(
        order_id=args.order_id,
        market_id=market.condition_id,
        asset_id=token.token_id,
        side=args.side,
        price=args.price,
        quantity=args.size,
        time_in_force=getattr(args, "tif", "fok"),
        post_only=getattr(args, "post_only", False),
        latency_ms=getattr(args, "latency_ms", 0),
        queue_policy="front_of_queue",
        model_version="matcher-v1",
    )


def _gui_descriptor(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    return 0, envelope(
        result={
            "mode": "local_read_only_gui",
            "host": "127.0.0.1",
            "port": args.port,
            "open_browser": bool(args.open_browser),
        },
        assumptions={
            "paper_only": True,
            "read_only": True,
            "provider_calls": False,
            "launcher": "pm_trader.gui.serve_gui",
        },
    )


def _dashboard_descriptor(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    return 0, envelope(
        result={
            "mode": "local_read_only_dashboard",
            "host": "127.0.0.1",
            "port": args.dashboard_port,
        },
        assumptions={
            "paper_only": True,
            "read_only": True,
            "provider_calls": False,
            "launcher": "dashboard.app.serve_dashboard",
        },
    )


def _parse_launch(argv: list[str] | None) -> argparse.Namespace:
    """Parse argv; a subcommand is required unless the optional --dashboard is set."""

    parser = _parser()
    args = parser.parse_args(argv)
    if args.dashboard:
        if args.command is not None:
            parser.error("--dashboard does not take a subcommand")
    elif args.command is None:
        parser.error("the following arguments are required: command")
    return args


def _safety_envelope() -> dict[str, Any]:
    return envelope(
        result={
            "mode": "paper_only",
            "live_writes": False,
            "signing": False,
            "cancellation": False,
            "redemption": False,
            "fund_movement": False,
        },
        assumptions={"authenticated_execution": "out_of_scope"},
    )


def _fit_market_page(payload: dict[str, Any], max_bytes: int) -> dict[str, Any] | None:
    """Keep the longest market prefix that fits. A cut page withholds its cursor."""

    result = payload.get("result")
    markets = result.get("markets") if isinstance(result, dict) else None
    if not isinstance(markets, list) or not markets:
        return None
    warning = "response truncated to the configured CLI size limit; retry with a smaller --limit"
    best: dict[str, Any] | None = None
    low = 1
    high = len(markets) - 1
    while low <= high:
        mid = (low + high) // 2
        candidate = {
            **payload,
            "warnings": [warning, *list(payload.get("warnings") or [])],
            "result": {**result, "markets": markets[:mid]},
        }
        if "next_cursor" in candidate["result"]:
            candidate["result"]["next_cursor"] = None
        _text, truncated = bounded_json(candidate, max_bytes)
        if not truncated:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def finalize_cli_response(code: int, payload: dict[str, Any], *, max_bytes: int) -> tuple[int, str]:
    """Serialize a CLI envelope under the byte cap. ``ok: false`` never exits 0."""

    _encoded, truncated = bounded_json(payload, max_bytes)
    if truncated:
        fitted = _fit_market_page(payload, max_bytes)
        if fitted is None:
            payload = envelope(
                result=None,
                errors=(
                    {
                        "code": "RESPONSE_SIZE_LIMIT",
                        "message": (
                            "response exceeded configured CLI size limit; retry with a smaller --limit"
                        ),
                    },
                ),
                ok=False,
            )
            code = max(code, 1)
        else:
            payload = fitted
    if payload.get("ok") is False and code == 0:
        code = 1
    return code, canonical_json(payload)


def _run_parsed(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.command == "safety":
        return 0, _safety_envelope()
    store = SQLiteStore(args.data_dir)
    token = begin_cli_log()
    try:
        if args.command == "discover":
            if args.fixture:
                return _logged(
                    args,
                    0,
                    _fixture_page(
                        args.fixture,
                        search=bool(args.query),
                        query=args.query or "",
                        limit=args.limit,
                    ),
                )
            return _logged(
                args,
                0,
                ResearchService(store, limits=ResourceLimits()).discover(
                    query=args.query,
                    limit=args.limit,
                    cursor=args.cursor,
                    order=args.order,
                    ascending=True if args.ascending else None,
                    open_only=args.open_only,
                ),
            )
        if args.command in {"market", "markets"}:
            if args.market_command == "list":
                if args.fixture:
                    return _logged(args, 0, _fixture_page(args.fixture, limit=args.limit))
                return _logged(args, 0, ResearchService(store).discover(limit=args.limit))
            if args.market_command == "search":
                if args.fixture:
                    return _logged(
                        args,
                        0,
                        _fixture_page(
                            args.fixture, search=True, query=args.query, limit=args.limit
                        ),
                    )
                return _logged(
                    args, 0, ResearchService(store).discover(query=args.query, limit=args.limit)
                )
            market = (
                load_market_fixture(args.fixture)
                if args.fixture
                else ResearchService(store)._market(args.identifier)
            )
            return _logged(
                args,
                0,
                envelope(
                    result=market,
                    freshness=market.freshness.value,
                    provenance_ids=(market.provenance.observation_id,) if market.provenance else (),
                ),
            )
        if args.command == "inspect":
            if args.inspect_command == "market":
                market = load_market_fixture(args.fixture)
                return _logged(
                    args,
                    0,
                    envelope(
                        result=market,
                        freshness=market.freshness.value,
                        provenance_ids=(market.provenance.observation_id,)
                        if market.provenance
                        else (),
                    ),
                )
            book = load_book_fixture(args.fixture)
            return _logged(
                args,
                0,
                envelope(
                    result=book,
                    freshness=book.freshness.value,
                    provenance_ids=(book.provenance.observation_id,) if book.provenance else (),
                ),
            )
        if args.command == "quote":
            return _logged(
                args,
                0,
                ResearchService(store).quote(
                    args.identifier,
                    outcome=args.outcome,
                    depth=args.depth,
                    side=args.side,
                    size=args.size,
                    price=args.price,
                    compact=args.compact,
                ),
            )
        if args.command == "prices":
            return _logged(
                args,
                0,
                ResearchService(store).price_history(
                    args.identifier,
                    outcome=args.outcome,
                    interval=args.interval,
                    fidelity=args.fidelity,
                    max_points=args.max_points,
                ),
            )
        if args.command == "settle":
            service = ResearchService(store)
            if bool(args.identifier) == bool(args.market_fixture):
                raise ValueError("settle needs a market identifier or --market-fixture")
            market = (
                load_market_fixture(args.market_fixture)
                if args.market_fixture
                else service._market(args.identifier)
            )
            payload = service.settle(market)
            return _logged(args, 0 if payload["ok"] else 1, payload)
        if args.command == "portfolio":
            return _logged(args, 0, ResearchService(store).portfolio(mark=args.mark))
        if args.command == "simulate":
            service = ResearchService(store)
            if args.market:
                if args.market_fixture or args.book_fixture:
                    raise ValueError("use --market or the fixture options, not both")
                market = service._market(args.market)
                book = service.live_book(market, args.outcome)
            else:
                if not (args.market_fixture and args.book_fixture):
                    raise ValueError(
                        "simulate needs --market, or both --market-fixture and --book-fixture"
                    )
                market = load_market_fixture(args.market_fixture)
                book = load_book_fixture(args.book_fixture)
            args._activity_market = market.question or market.slug
            order = _order(args, market)
            config = SimulationConfig(starting_cash=args.starting_cash, latency_ms=args.latency_ms)
            payload = service.simulate(
                market=market,
                book=book,
                orders=(order,),
                config=config,
                dataset_id=args.dataset_id,
                account=not args.isolated,
            )
            return _logged(args, 0 if payload["ok"] else 1, payload)
        if args.command == "replay":
            market = load_market_fixture(args.market_fixture)
            args._activity_market = market.question or market.slug
            order = _order(args, market)
            config = SimulationConfig(starting_cash=args.starting_cash)
            return _logged(
                args,
                0,
                ResearchService(store).replay(
                    dataset_path=args.dataset, market=market, orders=(order,), config=config
                ),
            )
        if args.command == "report":
            report = store.get_report(args.run_id)
            if report is None:
                return _logged(
                    args,
                    1,
                    envelope(
                        result=None,
                        errors=({"code": "REPORT_NOT_FOUND", "message": args.run_id},),
                        ok=False,
                    ),
                )
            return _logged(
                args,
                0,
                envelope(
                    result=report,
                    freshness="unknown",
                    assumptions={"paper_pnl_is_not_live_pnl": True},
                ),
            )
        if args.command == "explain":
            return _logged(
                args,
                0,
                envelope(
                    result={
                        "status": args.status,
                        "reason_code": args.reason_code,
                        "reasons": args.reason,
                        "freshness": args.freshness,
                    },
                    freshness=args.freshness,
                ),
            )
        return _logged(
            args,
            1,
            envelope(
                result=None,
                errors=({"code": "UNKNOWN_COMMAND", "message": args.command},),
                ok=False,
            ),
        )
    except TraderError as exc:
        return _logged(args, 2, envelope(result=None, errors=(exc.to_dict(),), ok=False))
    except (OSError, ValueError, TypeError, KeyError, IndexError, sqlite3.Error) as exc:
        return _logged(
            args,
            2,
            envelope(
                result=None,
                errors=(
                    {
                        "code": "UNEXPECTED_ERROR",
                        "category": "local_validation",
                        "message": str(exc),
                    },
                ),
                ok=False,
            ),
        )
    finally:
        end_cli_log(token)
        store.close()


def _logged(args: argparse.Namespace, code: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        record_command(args.data_dir, args, code, payload)
    except OSError:
        pass
    return code, payload


def run(argv: list[str] | None = None) -> tuple[int, dict[str, Any]]:
    """Run a JSON-returning command; GUI returns its launch descriptor for tests/callers."""

    args = _parse_launch(argv)
    if args.dashboard:
        return _dashboard_descriptor(args)
    if args.command == "gui":
        return _gui_descriptor(args)
    return _run_parsed(args)


def main(argv: list[str] | None = None) -> None:
    args = _parse_launch(argv)
    if args.dashboard:
        from dashboard.app import serve_dashboard

        serve_dashboard(args.data_dir, port=args.dashboard_port)
        return
    if args.command == "gui":
        from .gui import serve_gui

        serve_gui(args.data_dir, port=args.port, open_browser=args.open_browser)
        return
    code, payload = _run_parsed(args)
    exit_code, encoded = finalize_cli_response(
        code, payload, max_bytes=ResourceLimits().max_response_bytes
    )
    sys.stdout.write(encoded + "\n")
    raise SystemExit(exit_code)
