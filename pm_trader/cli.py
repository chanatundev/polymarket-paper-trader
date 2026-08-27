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
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser(
        "discover", help="discover markets through keyset pagination or public search"
    )
    discover.add_argument("--query")
    discover.add_argument("--limit", type=int, default=20)
    discover.add_argument("--cursor")
    discover.add_argument("--fixture", type=Path)

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

    inspect = sub.add_parser("inspect", help="inspect market or book")
    inspect_sub = inspect.add_subparsers(dest="inspect_command", required=True)
    inspect_market = inspect_sub.add_parser("market")
    inspect_market.add_argument("--fixture", type=Path, required=True)
    inspect_book = inspect_sub.add_parser("book")
    inspect_book.add_argument("--fixture", type=Path, required=True)

    simulate = sub.add_parser("simulate", help="simulate paper orders against a declared book")
    simulate.add_argument("--market-fixture", type=Path, required=True)
    simulate.add_argument("--book-fixture", type=Path, required=True)
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
            normalize_market(item, provenance=provenance, endpoint=str(path), provider="fixture")
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
        normalize_market(item, provenance=provenance, endpoint=str(path), provider="fixture")
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


def _run_parsed(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    store = SQLiteStore(args.data_dir)
    try:
        if args.command == "safety":
            return 0, envelope(
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
        if args.command == "discover":
            if args.fixture:
                return 0, _fixture_page(
                    args.fixture, search=bool(args.query), query=args.query or "", limit=args.limit
                )
            return 0, ResearchService(store, limits=ResourceLimits()).discover(
                query=args.query, limit=args.limit, cursor=args.cursor
            )
        if args.command in {"market", "markets"}:
            if args.market_command == "list":
                if args.fixture:
                    return 0, _fixture_page(args.fixture, limit=args.limit)
                return 0, ResearchService(store).discover(limit=args.limit)
            if args.market_command == "search":
                if args.fixture:
                    return 0, _fixture_page(
                        args.fixture, search=True, query=args.query, limit=args.limit
                    )
                return 0, ResearchService(store).discover(query=args.query, limit=args.limit)
            market = (
                load_market_fixture(args.fixture)
                if args.fixture
                else ResearchService(store)._market(args.identifier)
            )
            return 0, envelope(
                result=market,
                freshness=market.freshness.value,
                provenance_ids=(market.provenance.observation_id,) if market.provenance else (),
            )
        if args.command == "inspect":
            if args.inspect_command == "market":
                market = load_market_fixture(args.fixture)
                return 0, envelope(
                    result=market,
                    freshness=market.freshness.value,
                    provenance_ids=(market.provenance.observation_id,) if market.provenance else (),
                )
            book = load_book_fixture(args.fixture)
            return 0, envelope(
                result=book,
                freshness=book.freshness.value,
                provenance_ids=(book.provenance.observation_id,) if book.provenance else (),
            )
        if args.command == "simulate":
            market = load_market_fixture(args.market_fixture)
            book = load_book_fixture(args.book_fixture)
            order = _order(args, market)
            config = SimulationConfig(starting_cash=args.starting_cash, latency_ms=args.latency_ms)
            return 0, ResearchService(store).simulate(
                market=market, book=book, orders=(order,), config=config, dataset_id=args.dataset_id
            )
        if args.command == "replay":
            market = load_market_fixture(args.market_fixture)
            order = _order(args, market)
            config = SimulationConfig(starting_cash=args.starting_cash)
            return 0, ResearchService(store).replay(
                dataset_path=args.dataset, market=market, orders=(order,), config=config
            )
        if args.command == "report":
            report = store.get_report(args.run_id)
            if report is None:
                return 1, envelope(
                    result=None,
                    errors=({"code": "REPORT_NOT_FOUND", "message": args.run_id},),
                    ok=False,
                )
            return 0, envelope(
                result=report, freshness="unknown", assumptions={"paper_pnl_is_not_live_pnl": True}
            )
        if args.command == "explain":
            return 0, envelope(
                result={
                    "status": args.status,
                    "reason_code": args.reason_code,
                    "reasons": args.reason,
                    "freshness": args.freshness,
                },
                freshness=args.freshness,
            )
        return 1, envelope(
            result=None, errors=({"code": "UNKNOWN_COMMAND", "message": args.command},), ok=False
        )
    except TraderError as exc:
        return 2, envelope(result=None, errors=(exc.to_dict(),), ok=False)
    except (OSError, ValueError, TypeError, KeyError, IndexError, sqlite3.Error) as exc:
        return 2, envelope(
            result=None,
            errors=(
                {"code": "UNEXPECTED_ERROR", "category": "local_validation", "message": str(exc)},
            ),
            ok=False,
        )
    finally:
        store.close()


def run(argv: list[str] | None = None) -> tuple[int, dict[str, Any]]:
    """Run a JSON-returning command; GUI returns its launch descriptor for tests/callers."""

    args = _parser().parse_args(argv)
    if args.command == "gui":
        return _gui_descriptor(args)
    return _run_parsed(args)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "gui":
        from .gui import serve_gui

        serve_gui(args.data_dir, port=args.port, open_browser=args.open_browser)
        return
    code, payload = _run_parsed(args)
    encoded, truncated = bounded_json(payload, ResourceLimits().max_response_bytes)
    if truncated:
        encoded = canonical_json(
            envelope(
                result=None,
                errors=(
                    {
                        "code": "RESPONSE_SIZE_LIMIT",
                        "message": "response exceeded configured CLI size limit",
                    },
                ),
                ok=False,
            )
        )
    sys.stdout.write(encoded + "\n")
    raise SystemExit(code)
