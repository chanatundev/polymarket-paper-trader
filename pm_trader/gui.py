"""Local, read-only HTTP surface for reviewing stored paper-trading history."""

from __future__ import annotations

import sqlite3
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .api import ResourceLimits
from .history import (
    HistoryQuery,
    HistoryRecordMalformed,
    HistoryStoreUnavailable,
    TradeHistoryReader,
)
from .serialization import bounded_json, canonical_json, sanitize

ASSET_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def gui_envelope(
    *,
    result: Any = None,
    warnings: list[str] | tuple[str, ...] = (),
    freshness: str = "unknown",
    provenance_ids: list[str] | tuple[str, ...] = (),
    assumptions: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    ok: bool = True,
) -> dict[str, Any]:
    """Keep GUI JSON aligned with the CLI/MCP envelope without provider imports."""

    return {
        "ok": ok,
        "result": sanitize(result),
        "warnings": list(dict.fromkeys(str(item) for item in warnings)),
        "freshness": freshness,
        "provenance_ids": list(dict.fromkeys(str(item) for item in provenance_ids if item)),
        "assumptions": sanitize(assumptions or {}),
        "errors": [sanitize(error) for error in errors],
    }


def create_server(
    data_dir: str | Path,
    *,
    port: int = 8765,
    limits: ResourceLimits | None = None,
) -> ThreadingHTTPServer:
    """Create a loopback-ready server without creating or modifying the data store."""

    if port < 0 or port > 65535:
        raise ValueError("GUI port must be between 0 and 65535")
    asset_dir = Path(__file__).with_name("gui_assets")
    if not asset_dir.is_dir():
        raise FileNotFoundError(f"GUI assets are missing: {asset_dir}")
    resolved_limits = limits or ResourceLimits()
    reader = TradeHistoryReader.open(data_dir, limits=resolved_limits)
    server = LocalHistoryServer(
        ("127.0.0.1", port),
        reader=reader,
        asset_dir=asset_dir,
        limits=resolved_limits,
    )
    return server


def serve_gui(
    data_dir: str | Path,
    *,
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    """Serve the local GUI until interrupted."""

    server = create_server(data_dir, port=port)
    host, bound_port = server.server_address[:2]
    url = f"http://{host}:{bound_port}/"
    print(f"Paper-trader GUI listening at {url}", file=sys.stderr, flush=True)
    print("Read-only local history; no provider requests or order controls.", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping paper-trader GUI.", file=sys.stderr)
    finally:
        server.server_close()


class LocalHistoryServer(ThreadingHTTPServer):
    """Threaded local server that owns one read-only history reader."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        reader: TradeHistoryReader,
        asset_dir: Path,
        limits: ResourceLimits,
    ) -> None:
        self.reader = reader
        self.asset_dir = asset_dir
        self.limits = limits
        super().__init__(server_address, HistoryRequestHandler)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self.reader.close()


class HistoryRequestHandler(BaseHTTPRequestHandler):
    """Serve only known static assets and read-only history GET endpoints."""

    server: LocalHistoryServer

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/api/trades":
            self._get_history(parse_qs(parsed.query, keep_blank_values=True))
            return
        if parsed.path.startswith("/api/trades/"):
            history_id = unquote(parsed.path[len("/api/trades/") :])
            self._get_detail(history_id)
            return
        self._get_asset(parsed.path)

    def do_POST(self) -> None:
        self._send_text(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "This local paper-trader GUI is read-only; only GET is supported.\n",
            allow="GET",
        )

    def do_PUT(self) -> None:
        self.do_POST()

    def do_PATCH(self) -> None:
        self.do_POST()

    def do_DELETE(self) -> None:
        self.do_POST()

    def _get_history(self, query_values: dict[str, list[str]]) -> None:
        values = {key: items[0] if items else "" for key, items in query_values.items()}
        try:
            query = HistoryQuery.from_mapping(values)
            page = self.server.reader.list_history(query)
        except HistoryStoreUnavailable as exc:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                gui_envelope(
                    result=None,
                    errors=(exc.error,),
                    assumptions=_gui_assumptions(self.server.reader),
                    ok=False,
                ),
            )
            return
        except (ValueError, HistoryRecordMalformed) as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                gui_envelope(
                    result=None,
                    errors=(
                        {
                            "code": "INVALID_HISTORY_QUERY",
                            "category": "local_validation",
                            "message": str(exc),
                        },
                    ),
                    assumptions=_gui_assumptions(self.server.reader),
                    ok=False,
                ),
            )
            return
        except sqlite3.Error:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                gui_envelope(
                    result=None,
                    errors=(
                        {
                            "code": "HISTORY_STORE_MALFORMED",
                            "category": "local_store",
                            "message": "the selected SQLite store has an unsupported or malformed schema",
                        },
                    ),
                    assumptions=_gui_assumptions(self.server.reader),
                    ok=False,
                ),
            )
            return
        self._send_json(
            HTTPStatus.OK,
            gui_envelope(
                result=page.to_dict(),
                warnings=page.warnings,
                assumptions=_gui_assumptions(self.server.reader),
            ),
        )

    def _get_detail(self, history_id: str) -> None:
        try:
            record = self.server.reader.get_history(history_id)
        except HistoryStoreUnavailable as exc:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                gui_envelope(
                    result=None,
                    errors=(exc.error,),
                    assumptions=_gui_assumptions(self.server.reader),
                    ok=False,
                ),
            )
            return
        except (HistoryRecordMalformed, KeyError, TypeError, ValueError) as exc:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                gui_envelope(
                    result=None,
                    errors=(
                        {
                            "code": "MALFORMED_HISTORY_RECORD",
                            "category": "local_store",
                            "message": str(exc),
                        },
                    ),
                    assumptions=_gui_assumptions(self.server.reader),
                    ok=False,
                ),
            )
            return
        except sqlite3.Error:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                gui_envelope(
                    result=None,
                    errors=(
                        {
                            "code": "HISTORY_STORE_MALFORMED",
                            "category": "local_store",
                            "message": "the selected SQLite store has an unsupported or malformed schema",
                        },
                    ),
                    assumptions=_gui_assumptions(self.server.reader),
                    ok=False,
                ),
            )
            return
        if record is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                gui_envelope(
                    result=None,
                    errors=(
                        {
                            "code": "HISTORY_RECORD_NOT_FOUND",
                            "category": "local_store",
                            "message": "the requested stored trade was not found",
                        },
                    ),
                    assumptions=_gui_assumptions(self.server.reader),
                    ok=False,
                ),
            )
            return
        evidence = record.get("evidence", {})
        self._send_json(
            HTTPStatus.OK,
            gui_envelope(
                result=record,
                warnings=record.get("warnings", []),
                freshness=str(evidence.get("freshness") or "unknown"),
                provenance_ids=evidence.get("provenance_ids", []),
                assumptions={
                    **_gui_assumptions(self.server.reader),
                    "record": record.get("assumptions", {}),
                },
            ),
        )

    def _get_asset(self, path: str) -> None:
        asset = ASSET_FILES.get(path)
        if asset is None:
            self._send_text(HTTPStatus.NOT_FOUND, "Not found\n")
            return
        filename, content_type = asset
        try:
            content = (self.server.asset_dir / filename).read_bytes()
        except OSError:
            self._send_text(HTTPStatus.INTERNAL_SERVER_ERROR, "GUI asset unavailable\n")
            return
        self._send_bytes(HTTPStatus.OK, content, content_type)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded, truncated = bounded_json(payload, self.server.limits.max_response_bytes)
        if truncated:
            encoded = canonical_json(
                gui_envelope(
                    result=None,
                    errors=(
                        {
                            "code": "HISTORY_RESPONSE_SIZE_LIMIT",
                            "category": "local_validation",
                            "message": "history response exceeded the configured size limit",
                        },
                    ),
                    assumptions=_gui_assumptions(self.server.reader),
                    ok=False,
                )
            )
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_bytes(
            status,
            encoded.encode("utf-8"),
            "application/json; charset=utf-8",
            cache_control="no-store",
        )

    def _send_text(self, status: HTTPStatus, body: str, *, allow: str | None = None) -> None:
        self._send_bytes(
            status,
            body.encode("utf-8"),
            "text/plain; charset=utf-8",
            allow=allow,
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        allow: str | None = None,
        cache_control: str = "no-cache",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        if allow:
            self.send_header("Allow", allow)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: Any) -> None:
        # Keep normal request logs useful but do not echo query values into logs.
        sys.stderr.write(
            f"GUI {self.command} {self.path.split('?', 1)[0]} - {format_string % args}\n"
        )


def _gui_assumptions(reader: TradeHistoryReader) -> dict[str, Any]:
    return {
        "source": "local_sqlite",
        "database_path": str(reader.database_path),
        "paper_only": True,
        "read_only": True,
        "provider_calls": False,
        "history_schema_version": "history-v1",
    }


__all__ = [
    "ASSET_FILES",
    "HistoryRequestHandler",
    "LocalHistoryServer",
    "create_server",
    "gui_envelope",
    "serve_gui",
]
