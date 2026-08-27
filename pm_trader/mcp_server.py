"""Dependency-free MCP-shaped JSON-RPC server for structured paper workflows."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .api import ResourceLimits
from .errors import TraderError
from .models import Decision, PaperOrder, SimulationConfig
from .serialization import bounded_json, canonical_json
from .storage import SQLiteStore
from .workflows import ResearchService, envelope, load_market_fixture
from .workflows import load_book_fixture


TOOL_DEFINITIONS = [
    {"name": "discover_markets", "description": "Discover public markets through keyset pagination or public search.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}, "cursor": {"type": "string"}}}},
    {"name": "inspect_market", "description": "Inspect a normalized market state and its unknown fields.", "inputSchema": {"type": "object", "required": ["fixture"], "properties": {"fixture": {"type": "string"}}}},
    {"name": "inspect_book", "description": "Inspect a normalized executable book snapshot.", "inputSchema": {"type": "object", "required": ["fixture"], "properties": {"fixture": {"type": "string"}}}},
    {"name": "simulate", "description": "Simulate paper orders against a declared book fixture.", "inputSchema": {"type": "object", "required": ["market_fixture", "book_fixture"], "properties": {"market_fixture": {"type": "string"}, "book_fixture": {"type": "string"}, "side": {"type": "string"}, "price": {"type": "string"}, "size": {"type": "string"}, "time_in_force": {"type": "string"}}}},
    {"name": "replay", "description": "Replay a bounded recorded JSONL dataset.", "inputSchema": {"type": "object", "required": ["dataset", "market_fixture"], "properties": {"dataset": {"type": "string"}, "market_fixture": {"type": "string"}}}},
    {"name": "explain_decision", "description": "Return a structured decision explanation.", "inputSchema": {"type": "object", "required": ["status", "reason_code"], "properties": {"status": {"type": "string"}, "reason_code": {"type": "string"}, "reasons": {"type": "array"}}}},
]


def _service(data_dir: str | Path = ".pm-trader") -> ResearchService:
    return ResearchService(SQLiteStore(data_dir), limits=ResourceLimits())


def dispatch_tool(name: str, arguments: Mapping[str, Any] | None = None, *, service: ResearchService | None = None) -> dict[str, Any]:
    args = dict(arguments or {})
    owned = service is None
    workflow = service or _service(args.get("data_dir", ".pm-trader"))
    try:
        if name == "discover_markets":
            return workflow.discover(query=args.get("query"), limit=int(args.get("limit", 20)), cursor=args.get("cursor"))
        if name == "inspect_market":
            return workflow.inspect_market(str(args["fixture"]))
        if name == "inspect_book":
            book = load_book_fixture(str(args["fixture"]))
            return envelope(result=book, freshness=book.freshness.value, provenance_ids=(book.provenance.observation_id,) if book.provenance else ())
        if name == "simulate":
            market = load_market_fixture(str(args["market_fixture"]))
            book = load_book_fixture(str(args["book_fixture"]))
            token = market.token_for(str(args.get("outcome", "Yes")))
            order = PaperOrder(order_id=str(args.get("order_id", "mcp-order-1")), market_id=market.condition_id, asset_id=token.token_id, side=str(args.get("side", "buy")), price=args.get("price", "0.66"), quantity=args.get("size", "10"), time_in_force=str(args.get("time_in_force", "fok")), post_only=bool(args.get("post_only", False)), model_version="matcher-v1")
            return workflow.simulate(market=market, book=book, orders=(order,), config=SimulationConfig(starting_cash=args.get("starting_cash", "10000")))
        if name == "replay":
            market = load_market_fixture(str(args["market_fixture"]))
            token = market.token_for(str(args.get("outcome", "Yes")))
            order = PaperOrder(order_id=str(args.get("order_id", "mcp-replay-order-1")), market_id=market.condition_id, asset_id=token.token_id, side=str(args.get("side", "buy")), price=args.get("price", "0.66"), quantity=args.get("size", "10"), time_in_force=str(args.get("time_in_force", "fok")))
            return workflow.replay(dataset_path=str(args["dataset"]), market=market, orders=(order,), config=SimulationConfig(starting_cash=args.get("starting_cash", "10000")))
        if name == "explain_decision":
            return envelope(result={"status": args.get("status"), "reason_code": args.get("reason_code"), "reasons": args.get("reasons", []), "checks": args.get("checks", {})}, freshness=str(args.get("freshness", "unknown")), provenance_ids=args.get("provenance_ids", []), assumptions=args.get("assumptions", {}))
        return envelope(result=None, errors=({"code": "UNKNOWN_TOOL", "message": name},), ok=False)
    except TraderError as exc:
        return envelope(result=None, errors=(exc.to_dict(),), ok=False)
    except Exception as exc:
        return envelope(result=None, errors=({"code": "UNEXPECTED_ERROR", "message": str(exc)},), ok=False)
    finally:
        if owned:
            workflow.store.close()


def handle_request(request: Mapping[str, Any], *, service: ResearchService | None = None) -> dict[str, Any]:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "polymarket-paper-trader", "version": "0.2.0"}}
    elif method == "notifications/initialized":
        return {}
    elif method == "tools/list":
        result = {"tools": TOOL_DEFINITIONS}
    elif method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        result = {"content": [{"type": "text", "text": canonical_json(dispatch_tool(str(name), params.get("arguments", {}), service=service))}]}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
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
                encoded = canonical_json({"jsonrpc": "2.0", "id": request.get("id"), "result": {"isError": True, "content": [{"type": "text", "text": canonical_json(envelope(result=None, errors=({"code": "RESPONSE_SIZE_LIMIT", "message": "response exceeded configured MCP size limit"},), ok=False))}]}})
            sys.stdout.write(encoded + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            sys.stdout.write(canonical_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid JSON"}}) + "\n")
            sys.stdout.flush()
