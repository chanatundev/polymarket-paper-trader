"""Optional read-only paper dashboard over the canonical history store.

Reads go through ``pm_trader.history.TradeHistoryReader`` only. The store is
opened read-only and is never created: a missing data directory or SQLite file
renders as an empty state. The server binds 127.0.0.1 and exposes GET only.
"""

from __future__ import annotations

import html
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pm_trader.activity import read_activity
from pm_trader.history import (
    HistoryQuery,
    HistoryRecordMalformed,
    HistoryStoreUnavailable,
    TradeHistoryReader,
    resolve_database_path,
)

HOST = "127.0.0.1"
DEFAULT_PORT = 8766
ROW_LIMIT = 20
PAGE_TEMPLATE = Path(__file__).with_name("page.html")
BRAND_ASSETS = {
    "/brand/inter.ttf": (Path(__file__).with_name("brand") / "inter.ttf", "font/ttf"),
    "/brand/polymarket.svg": (
        Path(__file__).with_name("brand") / "polymarket.svg",
        "image/svg+xml",
    ),
}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
        "font-src 'self'; base-uri 'none'; form-action 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def build_snapshot(data_dir: str | Path) -> dict[str, Any]:
    """Read the activity log, then the store. Never creates either."""

    events = read_activity(data_dir, limit=ROW_LIMIT)
    if events:
        rows = [_event_row(event) for event in events]
        cash = next((event.get("cash") for event in events if event.get("cash")), None)
        return _snapshot("ready", rows=rows, total=len(rows), cash=cash)
    if not resolve_database_path(data_dir).is_file():
        return _snapshot("missing")
    reader = TradeHistoryReader.open(data_dir)
    try:
        if not reader.available:
            return _snapshot("missing")
        page = reader.list_history(HistoryQuery(limit=ROW_LIMIT))
        if page.total == 0:
            return _snapshot("empty")
        rows = [_row(record) for record in page.records]
        return _snapshot(
            "ready",
            rows=rows,
            total=page.total,
            cash=_newest_cash(reader, page.records[0].get("history_id")),
        )
    except (sqlite3.Error, HistoryStoreUnavailable, HistoryRecordMalformed, ValueError):
        return _snapshot("error")
    finally:
        reader.close()


def render_dashboard_html(snapshot: dict[str, Any]) -> str:
    """Fill the static template. Every stored value passes through ``html.escape``."""

    template = PAGE_TEMPLATE.read_text(encoding="utf-8")
    return template.replace("<!--CONTENT-->", _content_html(snapshot)).replace(
        "<!--CASH-->", _text(snapshot.get("cash"))
    )


def create_dashboard_server(data_dir: str | Path, port: int = DEFAULT_PORT) -> DashboardServer:
    return DashboardServer(data_dir, port)


def serve_dashboard(data_dir: str | Path, port: int = DEFAULT_PORT) -> None:
    """Blocking launch used only by ``pm-trader --dashboard``."""

    server = create_dashboard_server(data_dir, port=port)
    bound_port = server.server_address[1]
    sys.stdout.write(f"pm-trader dashboard (paper only, read only): http://{HOST}:{bound_port}/\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, data_dir: str | Path, port: int) -> None:
        self.data_dir = Path(data_dir)
        super().__init__((HOST, port), _DashboardHandler)


class _DashboardHandler(BaseHTTPRequestHandler):
    server_version = "pm-trader-dashboard"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        port = self.server.server_address[1]
        if self.headers.get("Host") not in {f"{HOST}:{port}", f"localhost:{port}"}:
            self._send(400, "text/plain; charset=utf-8", b"bad host header\n")
            return
        request_path = urlsplit(self.path).path
        asset = BRAND_ASSETS.get(request_path)
        if asset is not None:
            asset_path, content_type = asset
            self._send(
                200,
                content_type,
                asset_path.read_bytes(),
                cache_control="public, max-age=3600",
            )
            return
        if request_path not in {"/", "/index.html"}:
            self._send(404, "text/plain; charset=utf-8", b"not found\n")
            return
        body = render_dashboard_html(build_snapshot(self.server.data_dir))
        self._send(200, "text/html; charset=utf-8", body.encode("utf-8"))

    def _send(
        self,
        status: int,
        content_type: str,
        body: bytes,
        *,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        headers = dict(SECURITY_HEADERS)
        headers["Cache-Control"] = cache_control
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def _snapshot(state: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"state": state, "rows": [], "total": 0, "cash": None}
    base.update(fields)
    return base


def _row(record: dict[str, Any]) -> dict[str, Any]:
    market = record.get("market") or {}
    execution = record.get("execution") or {}
    return {
        "kind": "trade",
        "label": market.get("question") or market.get("slug") or "(unnamed market)",
        "side": execution.get("side"),
        "price": execution.get("price"),
        "size": execution.get("filled_quantity"),
        "time": record.get("occurred_at"),
    }


def _event_row(event: dict[str, Any]) -> dict[str, Any]:
    kind = str(event.get("kind") or "command")
    return {
        "kind": kind,
        "label": _event_label(event),
        "side": event.get("side"),
        "price": event.get("price"),
        "size": event.get("size"),
        "time": event.get("ts"),
    }


def _event_label(event: dict[str, Any]) -> str:
    kind = event.get("kind")
    if kind == "search":
        label = f'search "{event.get("query") or ""}" · {event.get("count")} markets'
    elif kind == "keyset":
        label = f'page limit {event.get("limit")} · {event.get("count")} markets'
    elif kind == "lookup" and event.get("target") == "market":
        label = f'market {event.get("slug") or event.get("identifier") or ""}'
    elif kind == "lookup" and event.get("target") == "book":
        parts = ["book", event.get("outcome") or "", event.get("token_id") or ""]
        label = " ".join(part for part in parts if part)
    elif kind == "lookup" and event.get("target") == "fee":
        label = f'fee {event.get("token_id") or ""} · {event.get("bps")} bps'
    elif kind == "lookup" and event.get("target") == "prices":
        label = f'prices {event.get("token_id") or ""} · {event.get("count")} points'
    elif kind == "lookup" and event.get("target") == "report":
        label = f'report {event.get("run_id") or ""}'
    elif kind == "order":
        label = f'order {event.get("market") or ""} {event.get("tif") or ""}'.strip()
    elif kind == "decision":
        label = f'{event.get("status") or "decision"} {event.get("reason") or ""}'.strip()
    elif kind == "fill":
        fee = event.get("fee")
        label = f'fill {event.get("market") or ""}' + (f" fee {fee}" if fee else "")
    elif kind == "cash":
        label = f'cash {event.get("cash") or ""} (model)'
    elif kind == "replay":
        label = f'replay {event.get("dataset") or ""}'
    else:
        command = str(event.get("command") or "command")
        market = event.get("market")
        label = f"{command} {market}".strip() if market else command
        if event.get("ok") is False:
            label = f'{label} failed {event.get("error") or ""}'.strip()
    if event.get("ok") is False and event.get("error") and "failed" not in label:
        label = f'{label} failed {event.get("error")}'.strip()
    return label


def _newest_cash(reader: TradeHistoryReader, history_id: Any) -> str | None:
    """Cash is stored on the simulation report of the full record, not the compact row."""

    if not isinstance(history_id, str):
        return None
    detail = reader.get_history(history_id) or {}
    report = (detail.get("accounting") or {}).get("report") or {}
    return report.get("cash")


def _text(value: Any) -> str:
    return html.escape("—" if value is None or value == "" else str(value), quote=True)


def _content_html(snapshot: dict[str, Any]) -> str:
    state = snapshot["state"]
    if state == "missing":
        return (
            '<p class="empty">No paper trades yet. No paper-trading store was found in the '
            "selected data directory, so nothing was created.</p>"
        )
    if state == "empty":
        return '<p class="empty">No paper trades yet. The store exists but holds no trades.</p>'
    if state == "error":
        return '<p class="empty">The paper-trading store could not be read.</p>'
    rows = "".join(
        "<tr>"
        f'<td data-label="Activity">{_text(row["label"])}</td>'
        f'<td data-label="Side">{_text(row["side"])}</td>'
        f'<td class="num" data-label="Price">{_text(row["price"])}</td>'
        f'<td class="num" data-label="Size">{_text(row["size"])}</td>'
        f'<td data-label="Time">{_text(row["time"])}</td>'
        "</tr>"
        for row in snapshot["rows"]
    )
    return (
        "<table><thead><tr>"
        "<th>Activity</th><th>Side</th><th class=\"num\">Price</th>"
        "<th class=\"num\">Size</th><th>Time</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
        f'<p class="meta">Showing {len(snapshot["rows"])} of {snapshot["total"]} events.</p>'
    )
