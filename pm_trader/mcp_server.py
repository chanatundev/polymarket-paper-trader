"""Dependency-free MCP-shaped JSON-RPC server for structured paper workflows."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .api import ResourceLimits
from .errors import TraderError
from .models import PaperOrder, SimulationConfig
from .serialization import bounded_json, canonical_json
from .storage import SQLiteStore
from .activity import record_tool
from .workflows import ResearchService, envelope, load_book_fixture, load_market_fixture

_IDENT = {"type": "string", "description": "Market slug or 0x condition id."}
_ORDER_FIELDS = {
    "outcome": {"type": "string", "description": "Outcome label, e.g. Yes or No. Default Yes."},
    "side": {"type": "string", "enum": ["buy", "sell"], "description": "Default buy."},
    "price": {"type": "string", "description": "Decimal limit price in (0, 1). Default 0.66."},
    "size": {"type": "string", "description": "Shares. Must meet the market min_order_size."},
    "time_in_force": {"type": "string", "enum": ["gtc", "gtd", "fak", "fok"]},
    "post_only": {"type": "boolean"},
    "order_id": {"type": "string", "description": "Unique per data directory."},
    "starting_cash": {
        "type": "string",
        "description": "Account opening deposit (or isolated-run cash). Default 10000.",
    },
    "data_dir": {"type": "string", "description": "SQLite data directory. Default .pm-trader."},
}

TOOL_DEFINITIONS = [
    {
        "name": "discover_markets",
        "description": (
            "Find public markets. Each row includes outcomes with token ids and indicative "
            "prices, plus volume, liquidity, best bid/ask and recent price change when Gamma "
            "reports them. Use order=volume_24hr with open_only=true to find active markets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search (max 50 rows)."},
                "limit": {"type": "integer", "description": "1-100. Default 20."},
                "cursor": {"type": "string", "description": "next_cursor from a previous page."},
                "order": {
                    "type": "string",
                    "enum": ["volume_24hr", "volume", "liquidity", "end_date", "start_date"],
                    "description": "Rank results, largest first unless ascending=true.",
                },
                "ascending": {"type": "boolean"},
                "open_only": {"type": "boolean", "description": "Only active, unclosed markets."},
            },
        },
    },
    {
        "name": "quote_market",
        "description": (
            "Live trading rules (tick, min size, fees incl. fee formula, hours_to_end) and a "
            "best-first order-book summary for each outcome: best bid/ask, midpoint, spread, "
            "age_seconds/freshness, top levels and depth near the midpoint. Pass outcome + side "
            "+ size (and optionally a limit price) to also get a pre-trade `estimate`: vwap, "
            "worst price, fee, slippage vs midpoint and immediate mark loss. Use compact=true "
            "for top-of-book numbers only. Call this before choosing a paper order."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["market"],
            "properties": {
                "market": _IDENT,
                "outcome": {"type": "string", "description": "Omit to quote every outcome."},
                "depth": {"type": "integer", "description": "Levels per side, 1-50. Default 5."},
                "side": {
                    "type": "string",
                    "enum": ["buy", "sell"],
                    "description": "With outcome and size: estimate a taker fill (buy walks asks, "
                    "sell walks bids).",
                },
                "size": {"type": "string", "description": "Shares to estimate. Needs side."},
                "price": {
                    "type": "string",
                    "description": "Optional limit price; levels beyond it are not used.",
                },
                "compact": {
                    "type": "boolean",
                    "description": "Per book only outcome, best bid/ask, midpoint, spread, "
                    "age_seconds and depth within 1c.",
                },
            },
        },
    },
    {
        "name": "price_history",
        "description": "Live price history for one outcome with open/close/high/low/change.",
        "inputSchema": {
            "type": "object",
            "required": ["market"],
            "properties": {
                "market": _IDENT,
                "outcome": {"type": "string", "description": "Default Yes."},
                "interval": {
                    "type": "string",
                    "enum": ["1h", "6h", "1d", "1w", "1m", "max", "all"],
                    "description": "Default 1w.",
                },
                "fidelity": {"type": "integer", "description": "Bar size in minutes."},
                "max_points": {"type": "integer", "description": "1-1000. Default 120."},
            },
        },
    },
    {
        "name": "paper_portfolio",
        "description": (
            "Net paper positions across every stored run: quantity, average price, cost basis, "
            "realized P&L, fees and net_pnl, plus totals. With mark=true, adds live midpoint value "
            "and unrealized P&L (midpoint-based, excludes exit fees)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"mark": {"type": "boolean"}, "data_dir": _ORDER_FIELDS["data_dir"]},
        },
    },
    {
        "name": "settle_market",
        "description": (
            "After a market resolves (closed with final 0/1 outcome prices), pay out the "
            "paper account's positions in it: winners at 1 per share, losers at 0. Returns "
            "MARKET_NOT_RESOLVED (ok=false) while the market is still open."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": _IDENT,
                "market_fixture": {"type": "string", "description": "Offline market JSON."},
                "data_dir": _ORDER_FIELDS["data_dir"],
            },
        },
    },
    {
        "name": "inspect_market",
        "description": "Inspect a normalized market state and its unknown fields.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": _IDENT,
                "fixture": {"type": "string", "description": "Local JSON instead of a live read."},
            },
        },
    },
    {
        "name": "inspect_book",
        "description": "Inspect a normalized executable book snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": _IDENT,
                "outcome": {"type": "string", "description": "Default Yes (live reads only)."},
                "fixture": {"type": "string", "description": "Local JSON instead of a live read."},
            },
        },
    },
    {
        "name": "simulate",
        "description": (
            "Paper-match one order. Pass market to match against the live order book, or "
            "market_fixture and book_fixture for an offline run. By default the order trades "
            "the data directory's persistent paper account (cash and positions carry over, so "
            "you can sell what you bought earlier); isolated=true runs a one-off from "
            "starting_cash. Returns summary (filled/resting/rejected, average price, fees), "
            "account, fills, and a stored report. A rejected order returns ok=false."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": _IDENT,
                "market_fixture": {"type": "string"},
                "book_fixture": {"type": "string"},
                "isolated": {"type": "boolean", "description": "Skip the persistent account."},
                **_ORDER_FIELDS,
            },
        },
    },
    {
        "name": "replay",
        "description": "Replay a bounded recorded JSONL dataset.",
        "inputSchema": {
            "type": "object",
            "required": ["dataset", "market_fixture"],
            "properties": {
                "dataset": {"type": "string"},
                "market_fixture": {"type": "string"},
                **_ORDER_FIELDS,
            },
        },
    },
    {
        "name": "portfolio_report",
        "description": "Read a bounded paper portfolio/P&L report by run identity.",
        "inputSchema": {
            "type": "object",
            "required": ["run_id"],
            "properties": {"run_id": {"type": "string"}, "data_dir": {"type": "string"}},
        },
    },
    {
        "name": "explain_decision",
        "description": "Return a structured decision explanation.",
        "inputSchema": {
            "type": "object",
            "required": ["status", "reason_code"],
            "properties": {
                "status": {"type": "string"},
                "reason_code": {"type": "string"},
                "reasons": {"type": "array"},
            },
        },
    },
]


def _service(data_dir: str | Path = ".pm-trader") -> ResearchService:
    return ResearchService(SQLiteStore(data_dir), limits=ResourceLimits())


def dispatch_tool(
    name: str, arguments: Mapping[str, Any] | None = None, *, service: ResearchService | None = None
) -> dict[str, Any]:
    args = dict(arguments or {})
    owned = service is None
    workflow = service or _service(args.get("data_dir", ".pm-trader"))
    payload: dict[str, Any] | None = None
    try:
        if name == "discover_markets":
            payload = workflow.discover(
                query=args.get("query"),
                limit=int(args.get("limit", 20)),
                cursor=args.get("cursor"),
                order=args.get("order"),
                ascending=args.get("ascending"),
                open_only=bool(args.get("open_only", False)),
            )
        elif name == "quote_market":
            payload = workflow.quote(
                str(args["market"]),
                outcome=args.get("outcome"),
                depth=int(args.get("depth", 5)),
                side=args.get("side"),
                size=args.get("size"),
                price=args.get("price"),
                compact=bool(args.get("compact", False)),
            )
        elif name == "price_history":
            fidelity = args.get("fidelity")
            payload = workflow.price_history(
                str(args["market"]),
                outcome=str(args.get("outcome", "Yes")),
                interval=str(args.get("interval", "1w")),
                fidelity=None if fidelity is None else int(fidelity),
                max_points=int(args.get("max_points", 120)),
            )
        elif name == "settle_market":
            market = (
                load_market_fixture(str(args["market_fixture"]))
                if "market_fixture" in args
                else workflow._market(str(args["market"]))
            )
            payload = workflow.settle(market)
        elif name == "paper_portfolio":
            payload = workflow.portfolio(mark=bool(args.get("mark", False)))
        elif name == "inspect_market" and "fixture" not in args:
            payload = workflow.inspect_market(str(args["market"]))
        elif name == "inspect_book" and "fixture" not in args:
            market = workflow._market(str(args["market"]))
            payload = workflow.inspect_book(market, str(args.get("outcome", "Yes")))
        elif name == "inspect_market":
            market = load_market_fixture(str(args["fixture"]))
            payload = envelope(
                result=market,
                freshness=market.freshness.value,
                provenance_ids=(market.provenance.observation_id,) if market.provenance else (),
            )
        elif name == "inspect_book":
            book = load_book_fixture(str(args["fixture"]))
            payload = envelope(
                result=book,
                freshness=book.freshness.value,
                provenance_ids=(book.provenance.observation_id,) if book.provenance else (),
            )
        elif name == "simulate":
            if "market" in args:
                market = workflow._market(str(args["market"]))
                book = workflow.live_book(market, str(args.get("outcome", "Yes")))
            else:
                market = load_market_fixture(str(args["market_fixture"]))
                book = load_book_fixture(str(args["book_fixture"]))
            token = market.token_for(str(args.get("outcome", "Yes")))
            order = PaperOrder(
                order_id=str(args.get("order_id", "mcp-order-1")),
                market_id=market.condition_id,
                asset_id=token.token_id,
                side=str(args.get("side", "buy")),
                price=args.get("price", "0.66"),
                quantity=args.get("size", "10"),
                time_in_force=str(args.get("time_in_force", "fok")),
                post_only=bool(args.get("post_only", False)),
                model_version="matcher-v1",
            )
            payload = workflow.simulate(
                market=market,
                book=book,
                orders=(order,),
                config=SimulationConfig(starting_cash=args.get("starting_cash", "10000")),
                account=not bool(args.get("isolated", False)),
            )
        elif name == "replay":
            market = load_market_fixture(str(args["market_fixture"]))
            token = market.token_for(str(args.get("outcome", "Yes")))
            order = PaperOrder(
                order_id=str(args.get("order_id", "mcp-replay-order-1")),
                market_id=market.condition_id,
                asset_id=token.token_id,
                side=str(args.get("side", "buy")),
                price=args.get("price", "0.66"),
                quantity=args.get("size", "10"),
                time_in_force=str(args.get("time_in_force", "fok")),
            )
            payload = workflow.replay(
                dataset_path=str(args["dataset"]),
                market=market,
                orders=(order,),
                config=SimulationConfig(starting_cash=args.get("starting_cash", "10000")),
            )
        elif name == "portfolio_report":
            payload = workflow.portfolio_report(str(args["run_id"]))
        elif name == "explain_decision":
            payload = envelope(
                result={
                    "status": args.get("status"),
                    "reason_code": args.get("reason_code"),
                    "reasons": args.get("reasons", []),
                    "checks": args.get("checks", {}),
                },
                freshness=str(args.get("freshness", "unknown")),
                provenance_ids=args.get("provenance_ids", []),
                assumptions=args.get("assumptions", {}),
            )
        else:
            payload = envelope(
                result=None, errors=({"code": "UNKNOWN_TOOL", "message": name},), ok=False
            )
    except TraderError as exc:
        payload = envelope(result=None, errors=(exc.to_dict(),), ok=False)
    except (OSError, ValueError, TypeError, KeyError, IndexError, sqlite3.Error) as exc:
        payload = envelope(
            result=None, errors=({"code": "UNEXPECTED_ERROR", "message": str(exc)},), ok=False
        )
    try:
        if payload is not None and workflow.store.db_path.name != ":memory:":
            try:
                record_tool(workflow.store.db_path.parent, name, args, payload)
            except OSError:
                pass
        return payload or envelope(
            result=None, errors=({"code": "UNEXPECTED_ERROR", "message": name},), ok=False
        )
    finally:
        if owned:
            workflow.store.close()


def handle_request(
    request: Mapping[str, Any] | Any, *, service: ResearchService | None = None
) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "request must be a JSON object"},
        }
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "polymarket-paper-trader", "version": "0.2.0"},
        }
    elif method == "notifications/initialized":
        return {}
    elif method == "tools/list":
        result = {"tools": TOOL_DEFINITIONS}
    elif method == "tools/call":
        params = request.get("params", {})
        if not isinstance(params, Mapping):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "params must be an object"},
            }
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "arguments must be an object"},
            }
        result = {
            "content": [
                {
                    "type": "text",
                    "text": canonical_json(dispatch_tool(str(name), arguments, service=service)),
                }
            ]
        }
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    limits = ResourceLimits()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            encoded, truncated = bounded_json(response, limits.max_response_bytes)
            if truncated:
                encoded = canonical_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request.get("id"),
                        "result": {
                            "isError": True,
                            "content": [
                                {
                                    "type": "text",
                                    "text": canonical_json(
                                        envelope(
                                            result=None,
                                            errors=(
                                                {
                                                    "code": "RESPONSE_SIZE_LIMIT",
                                                    "message": "response exceeded configured MCP size limit",
                                                },
                                            ),
                                            ok=False,
                                        )
                                    ),
                                }
                            ],
                        },
                    }
                )
            sys.stdout.write(encoded + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            sys.stdout.write(
                canonical_json(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "invalid JSON"},
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
