"""Bounded, read-only trade-history projections for the local review GUI."""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import quote

from .api import ResourceLimits
from .serialization import sanitize

HISTORY_SCHEMA_VERSION = "history-v1"
DEFAULT_HISTORY_LIMIT = 25
MAX_FILTER_LENGTH = 200
MAX_DISPLAY_TEXT = 512
DEFAULT_MAX_SCAN_ROWS = 100_000


class HistoryStoreUnavailable(RuntimeError):
    """The selected local SQLite store cannot be opened for a read-only view."""

    def __init__(self, error: Mapping[str, Any]) -> None:
        self.error = dict(error)
        super().__init__(self.error.get("message", "history store unavailable"))


class HistoryRecordMalformed(ValueError):
    """A persisted history record is not a JSON object with a supported shape."""


@dataclass(frozen=True)
class HistoryQuery:
    """Allow-listed, bounded filters accepted by the GUI history endpoint."""

    limit: int = DEFAULT_HISTORY_LIMIT
    cursor: str | None = None
    search: str = ""
    side: str | None = None
    status: str | None = None
    source_kind: str | None = None
    outcome: str | None = None
    run_id: str | None = None
    market: str | None = None
    start: str | None = None
    end: str | None = None

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("history limit must be positive")
        if self.limit > 100:
            raise ValueError("history limit exceeds the configured page-size bound")
        for name in (
            "search",
            "side",
            "status",
            "source_kind",
            "outcome",
            "run_id",
            "market",
            "start",
            "end",
        ):
            value = getattr(self, name)
            if value is not None and len(value) > MAX_FILTER_LENGTH:
                raise ValueError(f"history filter is too long: {name}")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> HistoryQuery:
        """Parse only known query keys; unknown keys never reach SQL."""

        def text(name: str) -> str | None:
            raw = values.get(name)
            if raw is None:
                return None
            if not isinstance(raw, str):
                raw = str(raw)
            normalized = raw.strip()
            return normalized or None

        raw_limit = values.get("limit", DEFAULT_HISTORY_LIMIT)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("history limit must be an integer") from exc
        return cls(
            limit=limit,
            cursor=text("cursor"),
            search=text("search") or text("q") or "",
            side=text("side"),
            status=text("status"),
            source_kind=text("source_kind"),
            outcome=text("outcome"),
            run_id=text("run_id"),
            market=text("market"),
            start=text("start"),
            end=text("end"),
        )


@dataclass(frozen=True)
class TradeHistoryPage:
    """A bounded page and its completeness/warning metadata."""

    records: tuple[dict[str, Any], ...]
    next_cursor: str | None
    total: int
    scanned: int
    scan_limited: bool
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "records": list(self.records),
            "next_cursor": self.next_cursor,
            "total": self.total,
            "returned": len(self.records),
            "scanned": self.scanned,
            "scan_limited": self.scan_limited,
            "total_is_complete": not self.scan_limited,
            "warnings": list(self.warnings),
        }


class TradeHistoryReader:
    """Read canonical and legacy trade history without changing the store."""

    def __init__(
        self,
        connection: sqlite3.Connection | None,
        *,
        database_path: Path,
        limits: ResourceLimits | None = None,
        max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
        owns_connection: bool = False,
        unavailable_error: Mapping[str, Any] | None = None,
    ) -> None:
        if max_scan_rows < 1:
            raise ValueError("max_scan_rows must be positive")
        self.connection = connection
        self.database_path = database_path
        self.limits = limits or ResourceLimits()
        self.max_scan_rows = max_scan_rows
        self.owns_connection = owns_connection
        self.unavailable_error = dict(unavailable_error) if unavailable_error else None
        self._tables: set[str] | None = None
        self._lock = RLock()
        if self.connection is not None:
            self.connection.row_factory = sqlite3.Row

    @classmethod
    def open(
        cls,
        data_dir: str | Path,
        *,
        limits: ResourceLimits | None = None,
        max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
    ) -> TradeHistoryReader:
        """Open a database in SQLite read-only mode without creating directories."""

        database_path = resolve_database_path(data_dir)
        if not database_path.is_file():
            return cls(
                None,
                database_path=database_path,
                limits=limits,
                max_scan_rows=max_scan_rows,
                unavailable_error={
                    "code": "HISTORY_STORE_UNAVAILABLE",
                    "category": "local_store",
                    "message": "the selected paper-trading SQLite store was not found",
                    "details": {"path": str(database_path)},
                },
            )
        try:
            encoded_path = quote(str(database_path.resolve()), safe="/:\\")
            connection = sqlite3.connect(
                f"file:{encoded_path}?mode=ro&immutable=1",
                uri=True,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
        except sqlite3.Error as exc:
            return cls(
                None,
                database_path=database_path,
                limits=limits,
                max_scan_rows=max_scan_rows,
                unavailable_error={
                    "code": "HISTORY_STORE_UNAVAILABLE",
                    "category": "local_store",
                    "message": "the selected SQLite store could not be opened read-only",
                    "details": {"path": str(database_path), "reason": str(exc)},
                },
            )
        return cls(
            connection,
            database_path=database_path,
            limits=limits,
            max_scan_rows=max_scan_rows,
            owns_connection=True,
        )

    @classmethod
    def from_connection(
        cls,
        connection: sqlite3.Connection,
        *,
        database_path: str | Path = ":memory:",
        limits: ResourceLimits | None = None,
        max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
    ) -> TradeHistoryReader:
        """Wrap a test or application connection and enforce query-only mode."""

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return cls(
            connection,
            database_path=Path(database_path),
            limits=limits,
            max_scan_rows=max_scan_rows,
        )

    @property
    def available(self) -> bool:
        return self.connection is not None and self.unavailable_error is None

    def close(self) -> None:
        if self.owns_connection and self.connection is not None:
            self.connection.close()
            self.connection = None

    def list_history(self, query: HistoryQuery | None = None) -> TradeHistoryPage:
        query = query or HistoryQuery()
        with self._lock:
            if query.limit > self.limits.max_page_size:
                raise ValueError("history limit exceeds the configured page-size bound")
            self._require_available()
            offset = decode_cursor(query.cursor)
            canonical, canonical_limited, warnings = self._read_canonical()
            legacy, legacy_limited, legacy_warnings = self._read_legacy()
            warnings.extend(legacy_warnings)
            records = [record for record in (*canonical, *legacy) if _matches(record, query)]
            records.sort(key=_sort_key, reverse=True)
            scan_limited = canonical_limited or legacy_limited
            if scan_limited:
                warnings.append("history_scan_limit_reached")
            page_records = records[offset : offset + query.limit]
            end_offset = offset + len(page_records)
            next_cursor = encode_cursor(end_offset) if end_offset < len(records) else None
            return TradeHistoryPage(
                records=tuple(_compact_record(record) for record in page_records),
                next_cursor=next_cursor,
                total=len(records),
                scanned=len(canonical) + len(legacy),
                scan_limited=scan_limited,
                warnings=tuple(dict.fromkeys(warnings)),
            )

    def get_history(self, history_id: str) -> dict[str, Any] | None:
        """Return one full detail record, or ``None`` for an unknown ID."""

        with self._lock:
            self._require_available()
            if not isinstance(history_id, str) or len(history_id) > MAX_FILTER_LENGTH:
                return None
            if history_id.startswith("legacy:"):
                return self._get_legacy_detail(history_id)
            if history_id.startswith("canonical:"):
                return self._get_canonical_detail(history_id)
            return None

    def _require_available(self) -> sqlite3.Connection:
        if not self.available or self.connection is None:
            raise HistoryStoreUnavailable(
                self.unavailable_error
                or {
                    "code": "HISTORY_STORE_UNAVAILABLE",
                    "category": "local_store",
                    "message": "history store is unavailable",
                }
            )
        return self.connection

    def _table_exists(self, table: str) -> bool:
        connection = self._require_available()
        if self._tables is None:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            self._tables = {str(row["name"]) for row in rows}
        return table in self._tables

    def _read_canonical(
        self,
    ) -> tuple[list[dict[str, Any]], bool, list[str]]:
        if not self._table_exists("fills"):
            return [], False, []
        connection = self._require_available()
        rows = connection.execute(
            self._canonical_select_sql(order_by="ORDER BY f.rowid DESC", limit=True),
            (self.max_scan_rows,),
        ).fetchall()
        warnings: list[str] = []
        records: list[dict[str, Any]] = []
        for row in rows:
            try:
                records.append(_canonical_record_from_row(row, []))
            except HistoryRecordMalformed:
                warnings.append("skipped_malformed_canonical_record")
        return records, len(rows) == self.max_scan_rows and self._has_more("fills"), warnings

    def _read_legacy(self) -> tuple[list[dict[str, Any]], bool, list[str]]:
        if not self._table_exists("trades"):
            return [], False, []
        connection = self._require_available()
        rows = connection.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (self.max_scan_rows,)
        ).fetchall()
        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for row in rows:
            try:
                records.append(_legacy_record_from_row(row))
            except (KeyError, TypeError, ValueError):
                warnings.append("skipped_malformed_legacy_record")
        return records, len(rows) == self.max_scan_rows and self._has_more("trades"), warnings

    def _has_more(self, table: str) -> bool:
        connection = self._require_available()
        row = connection.execute(
            f"SELECT 1 FROM {table} LIMIT 1 OFFSET ?",  # table is an internal allow-list
            (self.max_scan_rows,),
        ).fetchone()
        return row is not None

    def _ledger_for_fill(self, row: sqlite3.Row) -> list[dict[str, Any]]:
        if not self._table_exists("ledger_entries"):
            return []
        connection = self._require_available()
        entries = connection.execute(
            """
            SELECT payload
            FROM ledger_entries
            WHERE run_id = ?
            ORDER BY sequence
            LIMIT ?
            """,
            (row["run_id"], self.max_scan_rows),
        ).fetchall()
        matching: list[dict[str, Any]] = []
        for entry in entries:
            parsed = _safe_json_object(entry["payload"], "ledger entry")
            if parsed.get("fill_id") == row["fill_id"]:
                matching.append(parsed)
                if len(matching) == 50:
                    break
        return matching

    def _order_events(self, run_id: str, order_id: str) -> list[dict[str, Any]]:
        if not self._table_exists("order_events"):
            return []
        connection = self._require_available()
        rows = connection.execute(
            """
            SELECT payload
            FROM order_events
            WHERE run_id = ? AND order_id = ?
            ORDER BY event_sequence
            LIMIT 50
            """,
            (run_id, order_id),
        ).fetchall()
        return [_safe_json_object(row["payload"], "order event") for row in rows]

    def _get_canonical_detail(self, history_id: str) -> dict[str, Any] | None:
        parsed = _parse_canonical_id(history_id)
        if parsed is None or not self._table_exists("fills"):
            return None
        run_id, fill_id = parsed
        connection = self._require_available()
        row = connection.execute(
            self._canonical_select_sql(where="WHERE f.run_id = ? AND f.fill_id = ?"),
            (run_id, fill_id),
        ).fetchone()
        if row is None:
            return None
        record = _canonical_record_from_row(row, self._ledger_for_fill(row))
        record["related"]["order_events"] = self._order_events(run_id, row["order_id"])
        return record

    def _canonical_select_sql(
        self, *, where: str = "", order_by: str = "", limit: bool = False
    ) -> str:
        latest_event = (
            """
            (
                SELECT oe.payload
                FROM order_events AS oe
                WHERE oe.run_id = f.run_id AND oe.order_id = f.order_id
                ORDER BY oe.event_sequence DESC
                LIMIT 1
            )
            """
            if self._table_exists("order_events")
            else "NULL"
        )
        limit_clause = "LIMIT ?" if limit else ""
        return f"""
            SELECT
                f.run_id,
                f.fill_id,
                f.order_id,
                f.observation_id,
                f.payload AS fill_payload,
                o.payload AS order_payload,
                {latest_event} AS latest_order_event_payload,
                r.dataset_id,
                r.created_at AS run_created_at,
                r.config AS run_config,
                r.model_versions,
                r.status AS run_status,
                p.checksum AS report_checksum,
                p.payload AS report_payload
            FROM fills AS f
            LEFT JOIN orders AS o
                ON o.run_id = f.run_id AND o.order_id = f.order_id
            LEFT JOIN simulation_runs AS r
                ON r.run_id = f.run_id
            LEFT JOIN reports AS p
                ON p.run_id = f.run_id
            {where}
            {order_by}
            {limit_clause}
        """

    def _get_legacy_detail(self, history_id: str) -> dict[str, Any] | None:
        trade_id = _parse_legacy_id(history_id)
        if trade_id is None or not self._table_exists("trades"):
            return None
        row = (
            self._require_available()
            .execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
            .fetchone()
        )
        return None if row is None else _legacy_record_from_row(row)


def resolve_database_path(data_dir: str | Path) -> Path:
    """Resolve the same directory/file convention used by ``SQLiteStore``."""

    path = Path(data_dir)
    if path.suffix in {".db", ".sqlite", ".sqlite3"}:
        return path
    return path / "paper.db"


def encode_cursor(offset: int) -> str:
    if offset < 0:
        raise ValueError("cursor offset cannot be negative")
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    if len(cursor) > 32:
        raise ValueError("history cursor is too long")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_").decode("ascii")
        offset = int(decoded)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("history cursor is invalid") from exc
    if offset < 0:
        raise ValueError("history cursor cannot be negative")
    return offset


def _canonical_record_from_row(
    row: sqlite3.Row, ledger_entries: list[dict[str, Any]]
) -> dict[str, Any]:
    fill = _safe_json_object(row["fill_payload"], "canonical fill")
    order = _safe_json_object(row["order_payload"], "canonical order", allow_none=True)
    latest_event = _safe_json_object(
        row["latest_order_event_payload"], "latest order event", allow_none=True
    )
    config = _safe_json_object(row["run_config"], "simulation config", allow_none=True)
    model_versions = _safe_json_object(row["model_versions"], "model versions", allow_none=True)
    report = _safe_json_object(row["report_payload"], "simulation report", allow_none=True)
    order = order or {}
    config = config or {}
    model_versions = model_versions or {}
    report = report or {}
    metadata = _mapping(order.get("metadata"))
    final_event = latest_event or order
    final_status = _first(final_event, order, "status")
    unknown_reasons: dict[str, str] = {}

    market = {
        "market_id": _first(order, metadata, "market_id", "marketId", "condition_id"),
        "condition_id": _first(metadata, order, "condition_id", "conditionId", "market_id"),
        "slug": _first(metadata, order, "market_slug", "slug"),
        "question": _first(metadata, order, "market_question", "question"),
        "outcome": _first(metadata, order, "outcome"),
        "asset_id": _first(fill, order, "asset_id", "assetId"),
    }
    for field in ("slug", "question", "outcome"):
        if market[field] is None:
            unknown_reasons[f"market.{field}"] = "friendly market metadata was not persisted"

    execution = {
        "side": _first(fill, order, "side"),
        "status": final_status,
        "time_in_force": _first(order, "time_in_force", "timeInForce"),
        "post_only": _first(order, "post_only", "postOnly"),
        "requested_quantity": _first(order, "quantity", "size"),
        "filled_quantity": _first(fill, "quantity", "shares"),
        "remaining_quantity": _first(order, "remaining_quantity", "remainingQuantity"),
        "price": _first(fill, "price"),
        "notional": _first(fill, "notional", "cost"),
        "liquidity_role": _first(fill, "maker_taker", "makerTaker"),
        "partial": final_status == "partially_filled",
        "latency_ms": _first(order, "latency_ms", "latencyMs", default=config.get("latency_ms")),
        "queue_policy": _first(
            order, "queue_policy", "queuePolicy", default=config.get("queue_policy")
        ),
    }
    accounting_entry = next(
        (entry for entry in ledger_entries if entry.get("kind") == "fill"), None
    )
    accounting = {
        "fee": _first(fill, "fee"),
        "rebate_estimate": _first(fill, "rebate_estimate", "rebateEstimate"),
        "cash_delta": _first(accounting_entry or {}, "cash_delta", "cashDelta"),
        "position_delta": _first(accounting_entry or {}, "position_delta", "positionDelta"),
        "realized_pnl": _first(accounting_entry or {}, "realized_pnl", "realizedPnl"),
        "ledger_entries": ledger_entries,
        "report": _report_summary(report, row["report_checksum"]),
    }
    if accounting["report"] is None:
        unknown_reasons["accounting.report"] = "no stored simulation report is linked"

    timestamps = {
        "provider_timestamp": _first(fill, "provider_timestamp", "providerTimestamp"),
        "local_received_at": _first(fill, "received_at", "receivedAt"),
        "submitted_at": _first(order, "submitted_at", "submittedAt"),
        "expires_at": _first(order, "expires_at", "expiresAt"),
        "run_created_at": row["run_created_at"],
    }
    occurred_at = (
        timestamps["local_received_at"]
        or timestamps["provider_timestamp"]
        or timestamps["run_created_at"]
    )
    evidence = {
        "observation_id": _first(fill, "observation_id", default=row["observation_id"]),
        "provenance_ids": _unique_strings(
            [_first(fill, "observation_id", default=row["observation_id"])]
        ),
        "dataset_id": row["dataset_id"],
        "run_id": row["run_id"],
        "run_status": row["run_status"],
        "report_checksum": row["report_checksum"],
        "schema_version": config.get("schema_version"),
        "model_versions": model_versions,
        "freshness": None,
        "warnings": _string_list(report.get("warnings")),
    }
    unknown_reasons["evidence.freshness"] = "fill records do not persist a separate freshness value"
    assumptions = {
        "paper_only": True,
        **_known_config(config),
        "fill": _bounded_mapping(_mapping(fill.get("assumptions"))),
    }
    warnings = _string_list(report.get("warnings"))
    if not row["report_payload"]:
        warnings.append("simulation_report_unavailable")
    return {
        "history_id": f"canonical:{row['run_id']}:{row['fill_id']}",
        "source_kind": "canonical_fill",
        "occurred_at": _display_text(occurred_at),
        "identity": _bounded_mapping(
            {
                "run_id": row["run_id"],
                "order_id": row["order_id"],
                "fill_id": row["fill_id"],
                "observation_id": row["observation_id"],
            }
        ),
        "order": _bounded_mapping(
            {
                "order_id": row["order_id"],
                "market_id": _first(order, metadata, "market_id", "marketId", "condition_id"),
                "asset_id": _first(order, "asset_id", "assetId"),
                "side": _first(order, "side"),
                "status": final_status,
                "price": _first(order, "price"),
                "quantity": _first(order, "quantity", "size"),
                "time_in_force": _first(order, "time_in_force", "timeInForce"),
                "post_only": _first(order, "post_only", "postOnly"),
                "remaining_quantity": _first(order, "remaining_quantity", "remainingQuantity"),
                "submitted_at": _first(order, "submitted_at", "submittedAt"),
                "expires_at": _first(order, "expires_at", "expiresAt"),
                "model_version": _first(order, "model_version", "modelVersion"),
            }
        ),
        "fill": _bounded_mapping(
            {
                "fill_id": row["fill_id"],
                "order_id": row["order_id"],
                "asset_id": _first(fill, "asset_id", "assetId"),
                "side": _first(fill, "side"),
                "quantity": _first(fill, "quantity", "shares"),
                "price": _first(fill, "price"),
                "notional": _first(fill, "notional", "cost"),
                "liquidity_role": _first(fill, "maker_taker", "makerTaker"),
                "fee": _first(fill, "fee"),
                "rebate_estimate": _first(fill, "rebate_estimate", "rebateEstimate"),
                "observation_id": _first(fill, "observation_id", default=row["observation_id"]),
                "model_version": _first(fill, "model_version", "modelVersion"),
            }
        ),
        "market": _bounded_mapping(market),
        "execution": _bounded_mapping(execution),
        "accounting": _bounded_mapping(accounting),
        "timestamps": _bounded_mapping(timestamps),
        "evidence": _bounded_mapping(evidence),
        "assumptions": _bounded_mapping(assumptions),
        "warnings": list(dict.fromkeys(warnings)),
        "unknown_reasons": unknown_reasons,
        "related": {"order_events": []},
    }


def _legacy_record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    required = ("id", "market_condition_id", "market_slug", "market_question", "outcome")
    for key in required:
        if row[key] is None:
            raise HistoryRecordMalformed(f"legacy trade is missing {key}")
    trade_id = int(row["id"])
    occurred_at = _display_text(row["created_at"])
    unknown_reasons = {
        "evidence.provenance": "legacy trade rows predate canonical observation links",
        "evidence.freshness": "legacy trade rows do not persist freshness",
        "accounting.rebate_estimate": "legacy trade rows do not persist rebate estimates",
    }
    side = _display_text(row["side"])
    status = "partially_filled" if bool(row["is_partial"]) else "filled"
    execution = {
        "side": side,
        "status": status,
        "time_in_force": _display_text(row["order_type"]),
        "post_only": None,
        "requested_quantity": _display_text(row["shares"]),
        "filled_quantity": _display_text(row["shares"]),
        "remaining_quantity": None,
        "price": _display_text(row["avg_price"]),
        "notional": _display_text(row["amount_usd"]),
        "liquidity_role": None,
        "partial": bool(row["is_partial"]),
        "levels_filled": int(row["levels_filled"]),
        "slippage": _display_text(row["slippage"]),
    }
    accounting = {
        "fee": _display_text(row["fee"]),
        "fee_rate_bps": int(row["fee_rate_bps"]),
        "rebate_estimate": None,
        "cash_delta": _legacy_cash_delta(side, row["amount_usd"], row["fee"]),
        "position_delta": _display_text(row["shares"] if side == "buy" else -row["shares"]),
        "realized_pnl": None,
        "ledger_entries": [],
        "report": None,
    }
    return {
        "history_id": f"legacy:{trade_id}",
        "source_kind": "legacy_trade",
        "occurred_at": occurred_at,
        "identity": _bounded_mapping({"trade_id": trade_id}),
        "market": _bounded_mapping(
            {
                "market_id": row["market_condition_id"],
                "condition_id": row["market_condition_id"],
                "slug": row["market_slug"],
                "question": row["market_question"],
                "outcome": row["outcome"],
                "asset_id": None,
            }
        ),
        "execution": _bounded_mapping(execution),
        "accounting": _bounded_mapping(accounting),
        "timestamps": {
            "provider_timestamp": None,
            "local_received_at": None,
            "created_at": occurred_at,
        },
        "evidence": {
            "observation_id": None,
            "provenance_ids": [],
            "dataset_id": None,
            "run_id": None,
            "report_checksum": None,
            "schema_version": "legacy-trades",
            "model_versions": {},
            "freshness": None,
            "warnings": ["legacy_trade_record_has_no_canonical_provenance"],
        },
        "assumptions": {"paper_only": True, "legacy_record": True},
        "warnings": ["legacy_trade_record_has_no_canonical_provenance"],
        "unknown_reasons": unknown_reasons,
        "related": {"order_events": []},
    }


def _matches(record: Mapping[str, Any], query: HistoryQuery) -> bool:
    execution = _mapping(record.get("execution"))
    market = _mapping(record.get("market"))
    evidence = _mapping(record.get("evidence"))
    if query.side and str(execution.get("side", "")).casefold() != query.side.casefold():
        return False
    if query.status and str(execution.get("status", "")).casefold() != query.status.casefold():
        return False
    if (
        query.source_kind
        and str(record.get("source_kind", "")).casefold() != query.source_kind.casefold()
    ):
        return False
    if query.outcome and str(market.get("outcome", "")).casefold() != query.outcome.casefold():
        return False
    if query.run_id and str(evidence.get("run_id", "")) != query.run_id:
        return False
    if query.market:
        values = (
            market.get("market_id"),
            market.get("condition_id"),
            market.get("slug"),
            market.get("question"),
            market.get("asset_id"),
        )
        if (
            query.market.casefold()
            not in " ".join(str(value) for value in values if value).casefold()
        ):
            return False
    occurred_at = str(record.get("occurred_at") or "")
    if query.start and occurred_at < query.start:
        return False
    if query.end and occurred_at > query.end:
        return False
    if query.search:
        haystack = _search_text(record)
        if query.search.casefold() not in haystack.casefold():
            return False
    return True


def _compact_record(record: Mapping[str, Any]) -> dict[str, Any]:
    execution = _mapping(record.get("execution"))
    market = _mapping(record.get("market"))
    accounting = _mapping(record.get("accounting"))
    return {
        "history_id": record.get("history_id"),
        "source_kind": record.get("source_kind"),
        "occurred_at": record.get("occurred_at"),
        "market": {
            key: market.get(key)
            for key in ("market_id", "condition_id", "slug", "question", "outcome", "asset_id")
        },
        "execution": {
            key: execution.get(key)
            for key in (
                "side",
                "status",
                "time_in_force",
                "filled_quantity",
                "price",
                "notional",
                "liquidity_role",
                "partial",
            )
        },
        "accounting": {"fee": accounting.get("fee")},
        "warnings": list(record.get("warnings", ()))[:5],
        "unknown_reasons": dict(record.get("unknown_reasons", {})),
    }


def _sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    return str(record.get("occurred_at") or ""), str(record.get("history_id") or "")


def _search_text(record: Mapping[str, Any]) -> str:
    values: list[str] = [str(record.get("history_id", "")), str(record.get("source_kind", ""))]
    for section in ("market", "execution", "accounting", "evidence", "assumptions"):
        values.extend(
            str(value) for value in _mapping(record.get(section)).values() if value is not None
        )
    return " ".join(values)


def _report_summary(report: Mapping[str, Any], checksum: Any) -> dict[str, Any] | None:
    if not report and not checksum:
        return None
    return {
        "run_id": _display_text(report.get("run_id")),
        "cash": _display_text(report.get("cash")),
        "exposure": _display_text(report.get("exposure")),
        "fees": _display_text(report.get("fees")),
        "rebate_estimates": _display_text(report.get("rebate_estimates")),
        "realized_pnl": _display_text(report.get("realized_pnl")),
        "unrealized_pnl": _display_text(report.get("unrealized_pnl")),
        "fills": report.get("fills"),
        "orders": report.get("orders"),
        "checksum": _display_text(checksum or report.get("checksum")),
        "uncertainty": _bounded_mapping(_mapping(report.get("uncertainty"))),
    }


def _known_config(config: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "clock",
        "latency_ms",
        "queue_policy",
        "seed",
        "schema_version",
        "fee_model_version",
        "stale_tolerance_seconds",
        "max_events",
    )
    return {key: config[key] for key in allowed if key in config}


def _legacy_cash_delta(side: str | None, amount: Any, fee: Any) -> str | None:
    try:
        amount_value = float(amount)
        fee_value = float(fee)
    except (TypeError, ValueError):
        return None
    delta = -(amount_value + fee_value) if side == "buy" else amount_value - fee_value
    return f"{delta:.12f}".rstrip("0").rstrip(".")


def _safe_json_object(value: Any, label: str, *, allow_none: bool = False) -> dict[str, Any]:
    if value is None and allow_none:
        return {}
    if isinstance(value, Mapping):
        return _bounded_mapping(value)
    if not isinstance(value, str):
        raise HistoryRecordMalformed(f"{label} is not a JSON object")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise HistoryRecordMalformed(f"{label} is malformed JSON") from exc
    if parsed is None and allow_none:
        return {}
    if not isinstance(parsed, Mapping):
        raise HistoryRecordMalformed(f"{label} is not a JSON object")
    return _bounded_mapping(parsed)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _bounded_mapping(value: Mapping[str, Any], *, max_items: int = 40) -> dict[str, Any]:
    return {
        str(key)[:MAX_FILTER_LENGTH]: _bounded_value(item)
        for key, item in list(value.items())[:max_items]
    }


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return "[TRUNCATED]"
    value = sanitize(value)
    if isinstance(value, str):
        return value[:MAX_DISPLAY_TEXT] + ("..." if len(value) > MAX_DISPLAY_TEXT else "")
    if isinstance(value, list):
        return [_bounded_value(item, depth=depth + 1) for item in value[:40]]
    if isinstance(value, Mapping):
        return _bounded_mapping(value, max_items=40)
    return value


def _display_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:MAX_DISPLAY_TEXT] + ("..." if len(text) > MAX_DISPLAY_TEXT else "")


def _first(*sources_and_keys: Any, default: Any = None) -> Any:
    sources = [value for value in sources_and_keys if isinstance(value, Mapping)]
    keys = [value for value in sources_and_keys if isinstance(value, str)]
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                if isinstance(value, bool):
                    return value
                return _display_text(value) if isinstance(value, (str, int, float)) else value
    return default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_display_text(item) for item in value if _display_text(item)]


def _unique_strings(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _parse_canonical_id(history_id: str) -> tuple[str, str] | None:
    rest = history_id[len("canonical:") :] if history_id.startswith("canonical:") else ""
    if not rest or ":" not in rest:
        return None
    run_id, fill_id = rest.split(":", 1)
    return (run_id, fill_id) if run_id and fill_id else None


def _parse_legacy_id(history_id: str) -> int | None:
    raw = history_id[len("legacy:") :] if history_id.startswith("legacy:") else ""
    if not raw.isdigit():
        return None
    return int(raw)


__all__ = [
    "DEFAULT_MAX_SCAN_ROWS",
    "HISTORY_SCHEMA_VERSION",
    "HistoryQuery",
    "HistoryRecordMalformed",
    "HistoryStoreUnavailable",
    "TradeHistoryPage",
    "TradeHistoryReader",
    "decode_cursor",
    "encode_cursor",
    "resolve_database_path",
]
