"""Append-only paper-usage log.

One JSON object per line in ``<data-dir>/activity.jsonl``. The file keeps its
last 200 lines. Reads never create the directory.
"""

from __future__ import annotations

import contextvars
import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

LOG_NAME = "activity.jsonl"
MAX_LINES = 200
_LOCK = threading.Lock()
_CLI_LOGGING: contextvars.ContextVar[bool] = contextvars.ContextVar("pm_cli_activity", default=False)


def cli_is_logging() -> bool:
    return _CLI_LOGGING.get()


def begin_cli_log() -> contextvars.Token:
    return _CLI_LOGGING.set(True)


def end_cli_log(token: contextvars.Token) -> None:
    _CLI_LOGGING.reset(token)


def append_activity(data_dir: str | Path, event: dict[str, Any]) -> None:
    """Append one compact event. Creates the data directory because a run happened."""

    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"ts": _now(), **event}
    line = json.dumps(payload, separators=(",", ":"), default=_json_default)
    path = directory / LOG_NAME
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES:
            path.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")


def read_activity(data_dir: str | Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """Newest events first. A missing log is an empty list and creates nothing."""

    path = Path(data_dir) / LOG_NAME
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
        if len(events) >= limit:
            break
    return events


def record_fee(
    data_dir: str | Path, *, token_id: str, bps: int | None, source: str = "public"
) -> None:
    append_activity(
        data_dir,
        {
            "kind": "lookup",
            "command": "fee rate",
            "target": "fee",
            "token_id": _clip(token_id),
            "bps": bps,
            "source": source,
        },
    )


def record_book(data_dir: str | Path, *, token_id: str, source: str) -> None:
    append_activity(
        data_dir,
        {
            "kind": "lookup",
            "command": "order book",
            "target": "book",
            "token_id": _clip(token_id),
            "source": source,
        },
    )


def record_market(data_dir: str | Path, *, identifier: str, slug: str | None, source: str) -> None:
    append_activity(
        data_dir,
        {
            "kind": "lookup",
            "command": "market get",
            "target": "market",
            "identifier": _clip(identifier),
            "slug": _clip(slug),
            "source": source,
        },
    )


def record_prices(
    data_dir: str | Path, *, token_id: str, count: int, source: str = "public"
) -> None:
    append_activity(
        data_dir,
        {
            "kind": "lookup",
            "command": "price history",
            "target": "prices",
            "token_id": _clip(token_id),
            "count": count,
            "source": source,
        },
    )


def record_command(data_dir: str | Path, args: Any, code: int, payload: dict[str, Any]) -> None:
    """One compact line, or a short run of lines, for a CLI command. ``safety`` is ignored."""

    command = getattr(args, "command", None) or "unknown"
    if command == "safety":
        return
    ok = code == 0 and payload.get("ok") is not False
    error = _error_code(payload) if not ok else None
    source = "fixture" if _uses_fixture(args) else "public"
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if command == "discover":
        _record_discovery(data_dir, args, result, source=source, ok=ok, error=error)
        return
    if command in {"market", "markets"}:
        _record_market_command(data_dir, args, result, source=source, ok=ok, error=error)
        return
    if command == "inspect":
        _record_inspect(data_dir, args, result, source=source, ok=ok, error=error)
        return
    if command in {"simulate", "replay"}:
        _record_order_run(data_dir, args, result, source=source, ok=ok, error=error)
        return
    if command == "report":
        append_activity(
            data_dir,
            {
                "kind": "lookup",
                "command": "report",
                "target": "report",
                "run_id": getattr(args, "run_id", None),
                "source": "local",
                "ok": ok,
                "error": error,
            },
        )
        return
    if command == "explain" and source != "fixture":
        source = "local"
    append_activity(
        data_dir,
        {"kind": "command", "command": command, "source": source, "ok": ok, "error": error},
    )


def record_tool(
    data_dir: str | Path, name: str, arguments: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Compact lines for one MCP tool call.

    Order lines for a successful paper run are written by the simulator.
    """

    if cli_is_logging():
        return
    ok = payload.get("ok") is not False
    if name == "discover_markets" and ok:
        return
    if name == "simulate" and ok:
        return
    if name == "replay" and ok:
        return
    if name == "portfolio_report":
        return
    command = {
        "discover_markets": "discover",
        "inspect_market": "inspect",
        "inspect_book": "inspect",
        "simulate": "simulate",
        "replay": "replay",
        "portfolio_report": "report",
        "explain_decision": "explain",
    }.get(name, name)
    if name == "inspect_book":
        inspect_command = "book"
    elif name == "inspect_market":
        inspect_command = "market"
    else:
        inspect_command = None
    args = SimpleNamespace(
        command=command,
        market_command=None,
        inspect_command=inspect_command,
        query=arguments.get("query"),
        limit=arguments.get("limit"),
        cursor=arguments.get("cursor"),
        identifier=arguments.get("identifier"),
        outcome=arguments.get("outcome"),
        run_id=arguments.get("run_id"),
        fixture=arguments.get("fixture")
        or arguments.get("market_fixture")
        or arguments.get("book_fixture"),
        market_fixture=arguments.get("market_fixture"),
        book_fixture=arguments.get("book_fixture"),
        dataset=arguments.get("dataset"),
        side=arguments.get("side", "buy"),
        price=arguments.get("price"),
        size=arguments.get("size"),
        tif=arguments.get("time_in_force", "fok"),
        _activity_market=None,
    )
    record_command(data_dir, args, 0 if ok else 1, payload)


def record_simulation(data_dir: str | Path, *, command: str, market: Any, simulation: Any) -> None:
    """Order, decision, fill, and cash lines for a library paper run."""

    if cli_is_logging():
        return
    label = _clip(getattr(market, "question", None) or getattr(market, "slug", None))
    cash = getattr(getattr(simulation, "report", None), "cash", None)
    matches = tuple(getattr(simulation, "matches", ()) or ())
    for match in matches:
        order = match.order
        append_activity(
            data_dir,
            {
                "kind": "order",
                "command": command,
                "market": label,
                "side": order.side,
                "price": _text(order.price),
                "size": _text(order.effective_quantity()),
                "tif": order.time_in_force,
                "source": "library",
            },
        )
        fills = tuple(getattr(match, "fills", ()) or ())
        append_activity(
            data_dir,
            {
                "kind": "decision",
                "command": command,
                "market": label,
                "side": order.side,
                "status": match.decision.status,
                "reason": match.decision.reason_code,
                "size": _text(sum((fill.quantity for fill in fills), Decimal(0)) if fills else 0),
                "source": "library",
            },
        )
        for fill in fills:
            append_activity(
                data_dir,
                {
                    "kind": "fill",
                    "command": command,
                    "market": label,
                    "side": order.side,
                    "price": _text(fill.price),
                    "size": _text(fill.quantity),
                    "fee": _text(fill.fee),
                    "source": "library",
                },
            )
    if cash is not None:
        append_activity(
            data_dir, {"kind": "cash", "command": command, "market": label, "cash": _text(cash)}
        )


def _record_discovery(data_dir, args, result, *, source, ok, error) -> None:
    count = _market_count(result)
    if getattr(args, "query", None):
        append_activity(
            data_dir,
            {
                "kind": "search",
                "command": "discover",
                "query": _clip(args.query),
                "count": count,
                "source": source,
                "ok": ok,
                "error": error,
            },
        )
        return
    append_activity(
        data_dir,
        {
            "kind": "keyset",
            "command": "discover",
            "limit": getattr(args, "limit", None),
            "cursor": getattr(args, "cursor", None),
            "count": count,
            "source": source,
            "ok": ok,
            "error": error,
        },
    )


def _record_market_command(data_dir, args, result, *, source, ok, error) -> None:
    market_command = getattr(args, "market_command", None)
    if market_command == "search":
        append_activity(
            data_dir,
            {
                "kind": "search",
                "command": "market search",
                "query": _clip(getattr(args, "query", None)),
                "count": _market_count(result),
                "source": source,
                "ok": ok,
                "error": error,
            },
        )
        return
    if market_command == "get":
        append_activity(
            data_dir,
            {
                "kind": "lookup",
                "command": "market get",
                "target": "market",
                "identifier": _clip(getattr(args, "identifier", None)),
                "slug": result.get("slug"),
                "source": source,
                "ok": ok,
                "error": error,
            },
        )
        return
    append_activity(
        data_dir,
        {
            "kind": "keyset",
            "command": "market list",
            "limit": getattr(args, "limit", None),
            "count": _market_count(result),
            "source": source,
            "ok": ok,
            "error": error,
        },
    )


def _record_inspect(data_dir, args, result, *, source, ok, error) -> None:
    if getattr(args, "inspect_command", None) == "book":
        append_activity(
            data_dir,
            {
                "kind": "lookup",
                "command": "inspect book",
                "target": "book",
                "outcome": _clip(getattr(args, "outcome", None)),
                "token_id": _clip(result.get("token_id") or result.get("asset_id")),
                "source": source,
                "ok": ok,
                "error": error,
            },
        )
        return
    append_activity(
        data_dir,
        {
            "kind": "lookup",
            "command": "inspect market",
            "target": "market",
            "slug": result.get("slug"),
            "source": source,
            "ok": ok,
            "error": error,
        },
    )


def _record_order_run(data_dir, args, result, *, source, ok, error) -> None:
    command = args.command
    dataset = getattr(args, "dataset", None)
    replay_line = {
        "kind": "replay",
        "command": "replay",
        "dataset": Path(str(dataset)).name if dataset else None,
        "source": source,
        "ok": ok,
        "error": error,
    }
    if not ok:
        if command == "replay":
            append_activity(data_dir, replay_line)
            return
        append_activity(
            data_dir,
            {"kind": "command", "command": command, "source": source, "ok": False, "error": error},
        )
        return
    market = _market_label(args, result)
    report = result.get("report") if isinstance(result.get("report"), dict) else {}
    matches = result.get("matches") if isinstance(result.get("matches"), list) else []
    append_activity(
        data_dir,
        {
            "kind": "order",
            "command": command,
            "market": market,
            "side": getattr(args, "side", None),
            "price": _text(getattr(args, "price", None)),
            "size": _text(getattr(args, "size", None)),
            "tif": getattr(args, "tif", None),
            "source": source,
        },
    )
    for match in matches:
        if not isinstance(match, dict):
            continue
        order = match.get("order") if isinstance(match.get("order"), dict) else {}
        decision = match.get("decision") if isinstance(match.get("decision"), dict) else {}
        fills = match.get("fills") if isinstance(match.get("fills"), list) else []
        label = _clip(order.get("market_id") or market)
        append_activity(
            data_dir,
            {
                "kind": "decision",
                "command": command,
                "market": label,
                "side": order.get("side") or getattr(args, "side", None),
                "status": decision.get("status"),
                "reason": decision.get("reason_code"),
                "source": source,
            },
        )
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            append_activity(
                data_dir,
                {
                    "kind": "fill",
                    "command": command,
                    "market": label,
                    "side": order.get("side"),
                    "price": _text(fill.get("price")),
                    "size": _text(fill.get("quantity")),
                    "fee": _text(fill.get("fee")),
                    "source": source,
                },
            )
    if report.get("cash") is not None:
        append_activity(
            data_dir,
            {
                "kind": "cash",
                "command": command,
                "market": market,
                "cash": _text(report.get("cash")),
                "source": source,
            },
        )
    if command == "replay":
        append_activity(data_dir, replay_line)


def _market_label(args: Any, result: dict[str, Any]) -> str | None:
    named = getattr(args, "_activity_market", None) or result.get("question") or result.get("slug")
    if named:
        return _clip(named)
    matches = result.get("matches") if isinstance(result.get("matches"), list) else []
    if matches and isinstance(matches[0], dict):
        order = matches[0].get("order") if isinstance(matches[0].get("order"), dict) else {}
        return _clip(order.get("market_id"))
    return None


def _uses_fixture(args: Any) -> bool:
    return any(getattr(args, name, None) for name in ("fixture", "market_fixture", "book_fixture"))


def _market_count(result: dict[str, Any]) -> int | None:
    markets = result.get("markets")
    if isinstance(markets, list):
        return len(markets)
    return None


def _error_code(payload: dict[str, Any]) -> str | None:
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], dict):
        return errors[0].get("code")
    return None


def _clip(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:160]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_default(value: Any) -> str:
    return str(value)
