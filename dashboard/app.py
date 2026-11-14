"""Optional read-only paper dashboard over the canonical history store.

Reads go through ``pm_trader.history.TradeHistoryReader`` only. The store is
opened read-only and is never created: a missing data directory or SQLite file
renders as an empty state. The server binds 127.0.0.1 and exposes GET only.
"""

from __future__ import annotations

import html
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pm_trader.activity import LOG_NAME, MAX_LINES, read_activity
from pm_trader.history import (
    HistoryQuery,
    HistoryRecordMalformed,
    HistoryStoreUnavailable,
    TradeHistoryReader,
    decode_cursor,
    resolve_database_path,
)

HOST = "127.0.0.1"
DEFAULT_PORT = 8766
TRANSACTION_PAGE_SIZE = 100
PAGE_TEMPLATE = Path(__file__).with_name("page.html")
BRAND_ASSETS = {
    "/live.js": (Path(__file__).with_name("live.js"), "text/javascript; charset=utf-8"),
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
        "font-src 'self'; script-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def build_snapshot(data_dir: str | Path) -> dict[str, Any]:
    """Read all retained activity and the latest account/fill history, without writes."""

    history = _read_history_snapshot(data_dir)
    activity = _read_activity_snapshot(data_dir)
    return _combine_snapshot(history, activity)


def _read_history_snapshot(data_dir: str | Path) -> dict[str, Any]:
    database_exists = resolve_database_path(data_dir).is_file()
    snapshot = _snapshot(
        "empty" if database_exists else "missing",
        report=None,
        positions=[],
        transactions=[],
        transaction_total=0,
        transaction_marker=_history_marker(data_dir),
        transaction_next_cursor=None,
        transaction_scan_limited=False,
        transaction_warnings=[],
    )
    if not database_exists:
        return snapshot
    reader = TradeHistoryReader.open(data_dir)
    try:
        if not reader.available:
            snapshot["state"] = "error"
            return snapshot
        report = reader.latest_report()
        page = reader.list_history(HistoryQuery(limit=TRANSACTION_PAGE_SIZE))
        snapshot.update(
            state="ready" if report or page.total else "empty",
            report=report,
            positions=_position_rows(report),
            transactions=[_row(record) for record in page.records],
            transaction_total=page.total,
            transaction_marker=_history_marker(data_dir),
            transaction_next_cursor=page.next_cursor,
            transaction_scan_limited=page.scan_limited,
            transaction_warnings=list(page.warnings),
        )
        return snapshot
    except (sqlite3.Error, HistoryStoreUnavailable, HistoryRecordMalformed, ValueError):
        snapshot["state"] = "error"
        return snapshot
    finally:
        reader.close()


def _read_activity_snapshot(data_dir: str | Path) -> list[dict[str, Any]]:
    return [_event_row(event) for event in read_activity(data_dir, limit=MAX_LINES)]


def _combine_snapshot(
    history: dict[str, Any], activity: list[dict[str, Any]]
) -> dict[str, Any]:
    snapshot = {**history, "activity": activity}
    if snapshot["state"] in {"missing", "empty"} and activity:
        snapshot["state"] = "ready"
    return snapshot


def _file_state(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return (-1, -1, exc.errno or 0, 0, 0)
    # Opening a read-only SQLite connection in WAL mode can create an empty sidecar.
    # It carries no committed data and must not invalidate the history cache.
    if path.name.endswith("-wal") and stat.st_size == 0:
        return None
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _path_marker(paths: tuple[Path, ...]) -> str:
    state = [_file_state(path) for path in paths]
    encoded = json.dumps(state, separators=(",", ":")).encode("ascii")
    return hashlib.blake2b(encoded, digest_size=16).hexdigest()


def _history_marker(data_dir: str | Path) -> str:
    database_path = resolve_database_path(data_dir)
    wal_path = database_path.with_name(database_path.name + "-wal")
    return _path_marker((database_path, wal_path))


def _activity_marker(data_dir: str | Path) -> str:
    return _path_marker((Path(data_dir) / LOG_NAME,))


def render_dashboard_html(snapshot: dict[str, Any]) -> str:
    """Fill the static template. Every stored value passes through ``html.escape``."""

    template = PAGE_TEMPLATE.read_text(encoding="utf-8")
    fragments = _live_fragments(snapshot)
    return _replace_fragments(template, fragments)


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
        self._snapshot_lock = Lock()
        self._history_snapshot: dict[str, Any] | None = None
        self._history_cache_marker: str | None = None
        self._activity_snapshot: list[dict[str, Any]] | None = None
        self._activity_cache_marker: str | None = None
        super().__init__((HOST, port), _DashboardHandler)

    def get_snapshot(self) -> dict[str, Any]:
        """Refresh only the pieces whose cheap file markers have changed."""

        with self._snapshot_lock:
            history_before = _history_marker(self.data_dir)
            if self._history_snapshot is None or history_before != self._history_cache_marker:
                history = _read_history_snapshot(self.data_dir)
                history_after = _history_marker(self.data_dir)
                history["transaction_marker"] = history_after
                self._history_snapshot = history
                self._history_cache_marker = (
                    history_after if history_before == history_after else history_before
                )
                if history["state"] == "error":
                    self._history_snapshot = None
                    self._history_cache_marker = None

            activity_before = _activity_marker(self.data_dir)
            if self._activity_snapshot is None or activity_before != self._activity_cache_marker:
                self._activity_snapshot = _read_activity_snapshot(self.data_dir)
                activity_after = _activity_marker(self.data_dir)
                self._activity_cache_marker = (
                    activity_after if activity_before == activity_after else activity_before
                )

            history = self._history_snapshot or _snapshot("error")
            activity = self._activity_snapshot if self._activity_snapshot is not None else []
            return _combine_snapshot(history, activity)


class _DashboardHandler(BaseHTTPRequestHandler):
    server_version = "pm-trader-dashboard"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        port = self.server.server_address[1]
        if self.headers.get("Host") not in {f"{HOST}:{port}", f"localhost:{port}"}:
            self._send(400, "text/plain; charset=utf-8", b"bad host header\n")
            return
        request_path = urlsplit(self.path).path
        if request_path == "/snapshot":
            self._send_json(200, _live_fragments(self.server.get_snapshot()))
            return
        if request_path == "/transactions":
            query = parse_qs(urlsplit(self.path).query)
            cursor = query.get("cursor", [None])[0]
            payload = _transaction_page(self.server.data_dir, cursor)
            status = 400 if payload.get("error") == "invalid_cursor" else 200
            self._send_json(status, payload)
            return
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
        body = render_dashboard_html(self.server.get_snapshot())
        self._send(200, "text/html; charset=utf-8", body.encode("utf-8"))

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

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
    base: dict[str, Any] = {"state": state}
    base.update(fields)
    return base


def _row(record: dict[str, Any]) -> dict[str, Any]:
    market = record.get("market") or {}
    execution = record.get("execution") or {}
    accounting = record.get("accounting") or {}
    label = market.get("question") or market.get("slug")
    return {
        "history_id": record.get("history_id"),
        "label": label or "",
        "label_saved": bool(label),
        "outcome": market.get("outcome"),
        "market_id": market.get("market_id"),
        "asset_id": market.get("asset_id"),
        "side": execution.get("side"),
        "price": execution.get("price"),
        "size": execution.get("filled_quantity"),
        "fee": accounting.get("fee"),
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


def _position_rows(latest: dict[str, Any] | None) -> list[dict[str, Any]]:
    report = (latest or {}).get("report") or {}
    positions = report.get("positions")
    if not isinstance(positions, dict):
        return []
    labels = (latest or {}).get("position_labels") or {}
    return [
        {
            "asset_id": asset_id,
            "quantity": quantity,
            "label": labels.get(asset_id, {}).get("question")
            or labels.get(asset_id, {}).get("slug")
            or "Market title unavailable",
            "outcome": labels.get(asset_id, {}).get("outcome"),
        }
        for asset_id, quantity in sorted(positions.items())
        if quantity not in (None, "0", 0, "0.0")
    ]


def _live_fragments(snapshot: dict[str, Any]) -> dict[str, Any]:
    report = snapshot.get("report")
    raw_report = (report or {}).get("report") or {}
    if not isinstance(raw_report, dict):
        raw_report = {}
    uncertainty = raw_report.get("uncertainty") or {}
    if not isinstance(uncertainty, dict):
        uncertainty = {}
    unrealized = raw_report.get("unrealized_pnl")
    if uncertainty.get("unrealized_pnl") == "unknown_without_mark":
        unrealized = None
    account_html = _account_html(report, uncertainty, unrealized)
    positions_html = _positions_html(snapshot.get("positions", []), report)
    activity_rows = _rows_html(snapshot.get("activity", []), "activity")
    transaction_rows = _rows_html(snapshot.get("transactions", []), "transaction")
    if snapshot.get("state") == "error":
        transaction_rows = (
            '<tr><td class="empty" colspan="4">'
            "The local paper history could not be read.</td></tr>"
        )
    elif snapshot.get("state") == "missing":
        transaction_rows = (
            '<tr><td class="empty" colspan="4">'
            "No paper trades yet. No store was found, and none was created.</td></tr>"
        )
    elif snapshot.get("state") == "empty":
        transaction_rows = (
            '<tr><td class="empty" colspan="4">'
            "No paper trades yet. The store has no fills.</td></tr>"
        )
    return {
        "account": account_html,
        "cash_chart": _cash_chart(report),
        "pnl_chart": _pnl_chart(raw_report, uncertainty),
        "positions": positions_html,
        "activity": activity_rows,
        "activity_meta": _activity_meta(snapshot),
        "activity_count": f'{len(snapshot.get("activity", []))} events',
        "transactions": transaction_rows,
        "transaction_meta": _transaction_meta(snapshot),
        "transaction_more": _transaction_more(snapshot.get("transaction_next_cursor")),
        "transaction_total": snapshot.get("transaction_total", 0),
        "transaction_marker": snapshot.get("transaction_marker"),
    }


def _replace_fragments(template: str, fragments: dict[str, Any]) -> str:
    for key, value in fragments.items():
        template = template.replace(f"<!--{key.upper()}-->", str(value))
    return template


def _account_html(
    latest: dict[str, Any] | None, uncertainty: dict[str, Any], unrealized: Any
) -> str:
    if not latest:
        return '<p class="empty">No completed simulator report is available yet.</p>'
    report = latest.get("report") or {}
    fields = (
        ("Cash available", _format_money(report.get("cash"))),
        ("Closed-trade result", _format_money(report.get("realized_pnl"))),
        (
            "Open-trade estimate",
            "No price mark" if uncertainty.get("unrealized_pnl") == "unknown_without_mark"
            else _format_money(unrealized),
        ),
        ("Simulated fees", _format_money(report.get("fees"))),
    )
    cards = "".join(
        f'<div class="metric"><span class="meta">{label}</span><strong>{_text(value)}</strong>'
        "</div>"
        for label, value in fields
    )
    run = _text(latest.get("run_id"))
    created = _text(_format_time(latest.get("created_at")))
    exposure = (
        "Incomplete · missing price marks"
        if uncertainty.get("unrealized_pnl") == "unknown_without_mark"
        else _format_money(report.get("exposure"))
    )
    starting_cash = _format_money(report.get("starting_cash"))
    return (
        f'<p class="meta">Latest paper run · {created}</p>'
        f'<div class="metrics">{cards}</div>'
        f'<p class="secondary-summary">Started with <strong>{_text(starting_cash)}</strong>'
        f' <span aria-hidden="true">·</span> Open position value '
        f'<strong>{_text(exposure)}</strong> <span aria-hidden="true">·</span> '
        f'{_text(report.get("fills"))} trades in this run</p>'
        f'<details class="technical"><summary>Run ID</summary><code>{run}</code></details>'
    )


def _positions_html(positions: list[dict[str, Any]], latest: dict[str, Any] | None) -> str:
    if not latest:
        return '<p class="empty">No completed run report is available yet.</p>'
    if not positions:
        return '<p class="empty">No open paper positions in the latest completed run.</p>'
    rows = []
    for row in positions:
        outcome = f'<span class="meta">{_text(row["outcome"])}</span>' if row["outcome"] else ""
        token = (
            f'<details class="technical"><summary>Token ID</summary>'
            f'<code>{_text(row["asset_id"])}</code></details>'
        )
        rows.append(
            '<li class="position-row"><div><strong>'
            f'{_text(row["label"])}</strong>{outcome}{token}</div>'
            f'<strong class="position-quantity">{_text(_format_quantity(row["quantity"]))} '
            "shares</strong></li>"
        )
    return f'<ul class="position-list">{"".join(rows)}</ul>'


def _format_money(value: Any) -> str:
    number = _decimal_value(value)
    if number is None:
        return "Not available" if value is None else str(value)
    rounded = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{'-' if rounded < 0 else ''}${abs(rounded):,.2f}"


def _format_quantity(value: Any) -> str:
    number = _decimal_value(value)
    if number is None:
        return str(value) if value not in (None, "") else "—"
    return format(number.normalize(), "f")


def _format_price(value: Any) -> str:
    number = _decimal_value(value)
    if number is None:
        return "—" if value in (None, "") else str(value)
    cents = (number * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{format(cents.normalize(), 'f')}¢"


def _decimal_value(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _format_time(value: Any) -> str:
    if not value:
        return "—"
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    time = parsed.strftime("%I:%M %p %Z").lstrip("0")
    return f"{parsed.strftime('%b')} {parsed.day} · {time}"


def _side_class(side: Any) -> str:
    return "buy" if str(side or "").casefold() == "buy" else "sell"


def _outcome_details(row: dict[str, Any]) -> str:
    outcome = row.get("outcome")
    return f'<span class="meta outcome">{_text(outcome)}</span>' if outcome else ""


def _identifier_details(row: dict[str, Any]) -> str:
    identifiers = (
        ("Market ID", row.get("market_id")),
        ("Token ID", row.get("asset_id")),
    )
    items = "".join(
        f'<span class="technical-id"><span>{label}</span> <code>{_text(value)}</code></span>'
        for label, value in identifiers
        if value
    )
    if not items:
        return ""
    return f'<details class="technical"><summary>Technical details</summary>{items}</details>'


def _activity_detail(row: dict[str, Any]) -> str:
    if not row.get("side"):
        return _text(row.get("kind", "event").replace("_", " ").title())
    action = "Bought" if str(row["side"]).casefold() == "buy" else "Sold"
    return _text(
        f"{action} {_format_quantity(row.get('size'))} shares at "
        f"{_format_price(row.get('price'))}"
    )


def _cash_chart(latest: dict[str, Any] | None) -> str:
    if not latest:
        return '<p class="chart-empty">No completed run to chart yet.</p>'
    history = latest.get("cash_history") or {}
    if history.get("too_many"):
        return '<p class="chart-empty">This run is too large to chart safely.</p>'
    points = history.get("points") or []
    values = [_decimal_value(point.get("cash")) for point in points]
    if not values or any(value is None for value in values):
        return '<p class="chart-empty">No cash movement is available for this run.</p>'
    numbers = [value for value in values if value is not None]
    low, high = min(numbers), max(numbers)
    padding = max((high - low) * Decimal("0.15"), Decimal("0.05"))
    low -= padding
    high += padding
    left, right, top, bottom = 78, 700, 20, 184
    span = max(1, len(numbers) - 1)
    coords = []
    for index, value in enumerate(numbers):
        x = left + (right - left) * index / span
        y = bottom - float(value - low) / float(high - low) * (bottom - top)
        coords.append((x, y))
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    grid = []
    for fraction in (0, 0.5, 1):
        y = top + (bottom - top) * fraction
        value = high - (high - low) * Decimal(str(fraction))
        grid.append(
            f'<line class="chart-grid" x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}"/>'
            f'<text class="chart-axis" x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">'
            f'{_text(_format_money(value))}</text>'
        )
    dots = "".join(
        f'<circle class="chart-dot" cx="{x:.1f}" cy="{y:.1f}" r="3.5">'
        f'<title>Step {_text(point.get("step"))}: {_text(_format_money(point.get("cash")))}</title></circle>'
        for (x, y), point in zip(coords, points, strict=True)
    )
    return (
        '<svg class="chart-svg" viewBox="0 0 720 224" role="img" '
        'aria-label="Cash balance from the start to the latest step of the most recent paper run">'
        '<title>Cash balance during the latest paper run</title>'
        f'{"".join(grid)}<polyline class="cash-line" points="{polyline}"/>{dots}'
        f'<text class="chart-axis" x="{left}" y="212">Start</text>'
        f'<text class="chart-axis" x="{right}" y="212" text-anchor="end">Latest change</text>'
        '</svg>'
    )


def _pnl_chart(report: dict[str, Any], uncertainty: dict[str, Any]) -> str:
    realized = _decimal_value(report.get("realized_pnl"))
    unrealized = (
        None
        if uncertainty.get("unrealized_pnl") == "unknown_without_mark"
        else _decimal_value(report.get("unrealized_pnl"))
    )
    if realized is None and unrealized is None:
        return '<p class="chart-empty">No result values are available for this run.</p>'
    values = [value for value in (realized, unrealized) if value is not None]
    scale = max(max(abs(value) for value in values), Decimal("0.01")) * Decimal("1.2")
    zero = 400
    half_width = 205
    rows = (
        ("Closed trade result", realized, 50),
        ("Open trade estimate", unrealized, 108),
    )
    elements = []
    for label, value, y in rows:
        elements.append(f'<text class="chart-label" x="10" y="{y + 4}">{label}</text>')
        if value is None:
            elements.append(
                f'<text class="chart-axis" x="{zero}" y="{y + 4}">No price mark</text>'
            )
            continue
        width = float(abs(value) / scale) * half_width
        x = zero - width if value < 0 else zero
        elements.append(
            f'<rect class="pnl-bar" x="{x:.1f}" y="{y - 10}" width="{width:.1f}" height="18" rx="3"/>'
        )
        elements.append(
            f'<text class="chart-value" x="710" y="{y + 4}" text-anchor="end">'
            f'{_text(_format_money(value))}</text>'
        )
    limit = _format_money(scale)
    return (
        '<svg class="chart-svg" viewBox="0 0 720 164" role="img" '
        'aria-label="Closed trade result and open trade estimate for the latest paper run">'
        '<title>Paper trading results</title>'
        f'<line class="chart-grid" x1="{zero}" y1="20" x2="{zero}" y2="132"/>'
        f'{"".join(elements)}'
        f'<text class="chart-axis" x="195" y="154">−{limit}</text>'
        '<text class="chart-axis" x="400" y="154" text-anchor="middle">$0</text>'
        f'<text class="chart-axis" x="605" y="154" text-anchor="end">+{limit}</text>'
        '</svg>'
    )


def _rows_html(rows: list[dict[str, Any]], kind: str) -> str:
    if not rows:
        label = (
            "No activity events have been recorded."
            if kind == "activity"
            else "No paper trades have been recorded yet."
        )
        return f'<li class="empty">{label}</li>' if kind == "activity" else (
            f'<tr><td class="empty" colspan="4">{label}</td></tr>'
        )
    if kind == "activity":
        return "".join(
            '<li class="activity-row">'
            f'<time>{_text(_format_time(row["time"]))}</time>'
            f'<div><strong>{_text(row["label"])}</strong>'
            f'<span class="meta">{_activity_detail(row)}</span></div>'
            f'<span class="event-kind">{_text(row["kind"].replace("_", " ").title())}</span>'
            '</li>'
            for row in rows
        )
    return "".join(_transaction_row_html(row) for row in rows)


def _transaction_row_html(row: dict[str, Any]) -> str:
    return (
        "<tr "
        f'data-history-id="{_text(row["history_id"])}">'
        f'<td data-label="When"><time>{_text(_format_time(row["time"]))}</time></td>'
        f'<td data-label="Trade"><strong class="trade-side {_side_class(row["side"])}">'
        f'{_text((row["side"] or "trade").title())}</strong> '
        f'{_text(_format_quantity(row["size"]))} shares at '
        f'{_text(_format_price(row["price"]))}</td>'
        f'<td data-label="Market"><strong>{_text(row["label"])}</strong>'
        f'{_outcome_details(row)}'
        f'{_identifier_details(row)}</td>'
        f'<td class="num" data-label="Fee">{_text(_format_money(row["fee"]))}</td>'
        "</tr>"
    )


def _activity_meta(snapshot: dict[str, Any]) -> str:
    count = len(snapshot.get("activity", []))
    return f"All {count} events currently kept in the local log, newest first."


def _transaction_meta(snapshot: dict[str, Any]) -> str:
    total = snapshot.get("transaction_total", 0)
    label = f"{total} paper executions · newest first."
    if any(not row.get("label_saved") for row in snapshot.get("transactions", [])):
        label += " Some older trades lack market titles; IDs are under Technical details."
    if snapshot.get("transaction_scan_limited"):
        label += " Store scan reached its 100,000-row bound; older rows may be omitted."
    if snapshot.get("transaction_warnings"):
        label += " Some stored records were skipped; this history may be incomplete."
    return label


def _transaction_more(cursor: str | None) -> str:
    if not cursor:
        return ""
    return (
        '<button id="load-older" type="button" data-cursor="'
        f'{_text(cursor)}">Load older fills</button>'
    )


def _transaction_page(data_dir: str | Path, cursor: str | None) -> dict[str, Any]:
    if not cursor:
        return {"error": "invalid_cursor"}
    try:
        decode_cursor(cursor)
    except ValueError:
        return {"error": "invalid_cursor"}
    reader = TradeHistoryReader.open(data_dir)
    try:
        if not reader.available:
            return {"error": "history_unavailable"}
        page = reader.list_history(HistoryQuery(limit=TRANSACTION_PAGE_SIZE, cursor=cursor))
        return {
            "rows": _rows_html([_row(record) for record in page.records], "transaction"),
            "next_cursor": page.next_cursor,
            "total": page.total,
            "transaction_marker": _history_marker(data_dir),
        }
    except (sqlite3.Error, HistoryStoreUnavailable, HistoryRecordMalformed, ValueError):
        return {"error": "history_unavailable"}
    finally:
        reader.close()


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
        label = f'fill {event.get("market") or ""}' + (
            f" · fee {_format_money(fee)}" if fee else ""
        )
    elif kind == "cash":
        label = f'Cash balance {_format_money(event.get("cash"))} (model)'
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


def _text(value: Any) -> str:
    return html.escape("—" if value is None or value == "" else str(value), quote=True)
