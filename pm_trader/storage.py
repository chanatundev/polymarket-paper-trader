"""SQLite persistence for observations, simulation runs, fills, and ledgers."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .models import Fill, LedgerEntry, PaperOrder, Provenance, SimulationConfig
from .serialization import canonical_json

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    max_bytes INTEGER NOT NULL,
    max_observations INTEGER NOT NULL,
    bytes_used INTEGER NOT NULL DEFAULT 0,
    observation_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    dataset_id TEXT,
    source TEXT NOT NULL,
    endpoint TEXT,
    retrieved_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    request_metadata TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    normalized_payload TEXT,
    payload_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'observation',
    FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
);

CREATE TABLE IF NOT EXISTS stream_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT,
    observation_id TEXT,
    event_type TEXT NOT NULL,
    sequence INTEGER,
    gap_detected INTEGER NOT NULL DEFAULT 0,
    resynchronized INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id),
    FOREIGN KEY(observation_id) REFERENCES observations(observation_id)
);

CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id TEXT PRIMARY KEY,
    dataset_id TEXT,
    created_at TEXT NOT NULL,
    config TEXT NOT NULL,
    status TEXT NOT NULL,
    model_versions TEXT NOT NULL,
    FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
);

CREATE TABLE IF NOT EXISTS orders (
    run_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(run_id, order_id),
    FOREIGN KEY(run_id) REFERENCES simulation_runs(run_id)
);

CREATE TABLE IF NOT EXISTS order_events (
    run_id TEXT NOT NULL,
    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES simulation_runs(run_id)
);

CREATE TABLE IF NOT EXISTS fills (
    run_id TEXT NOT NULL,
    fill_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    observation_id TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY(run_id, fill_id),
    FOREIGN KEY(run_id) REFERENCES simulation_runs(run_id),
    FOREIGN KEY(observation_id) REFERENCES observations(observation_id)
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    entry_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(run_id, sequence),
    UNIQUE(run_id, entry_id),
    FOREIGN KEY(run_id) REFERENCES simulation_runs(run_id)
);

CREATE TABLE IF NOT EXISTS reports (
    run_id TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES simulation_runs(run_id)
);
"""


class SQLiteStore:
    """A small append-only SQLite store with foreign-key provenance links."""

    def __init__(self, path_or_dir: str | Path = ":memory:") -> None:
        raw_path = str(path_or_dir)
        if raw_path == ":memory:":
            self.db_path = Path(":memory:")
            self._conn = sqlite3.connect(":memory:")
        else:
            path = Path(path_or_dir)
            if path.suffix in {".db", ".sqlite", ".sqlite3"}:
                self.db_path = path
            else:
                path.mkdir(parents=True, exist_ok=True)
                self.db_path = path / "paper.db"
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def create_dataset(
        self, dataset_id: str, *, created_at: str, max_bytes: int, max_observations: int
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO datasets(dataset_id, created_at, max_bytes, max_observations) VALUES (?, ?, ?, ?)",
            (dataset_id, created_at, max_bytes, max_observations),
        )
        self._conn.commit()

    def dataset_usage(self, dataset_id: str) -> tuple[int, int]:
        row = self._conn.execute(
            "SELECT bytes_used, observation_count FROM datasets WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()
        if row is None:
            return (0, 0)
        return (int(row["bytes_used"]), int(row["observation_count"]))

    def insert_observation(
        self,
        *,
        dataset_id: str | None,
        provenance: Provenance,
        raw_payload: Any,
        normalized_payload: Any | None,
        byte_size: int,
        kind: str = "observation",
    ) -> None:
        raw_text = canonical_json(raw_payload)
        normalized_text = None if normalized_payload is None else canonical_json(normalized_payload)
        cursor = self._conn.execute(
            """INSERT INTO observations(
                observation_id, dataset_id, source, endpoint, retrieved_at,
                received_at, schema_version, request_metadata, raw_payload,
                normalized_payload, payload_sha256, byte_size, kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provenance.observation_id,
                dataset_id,
                provenance.source,
                provenance.endpoint,
                provenance.retrieved_at,
                provenance.received_at,
                provenance.schema_version,
                canonical_json(provenance.request_metadata),
                raw_text,
                normalized_text,
                provenance.payload_sha256,
                byte_size,
                kind,
            ),
        )
        if dataset_id is not None and cursor.rowcount:
            self._conn.execute(
                "UPDATE datasets SET bytes_used = bytes_used + ?, observation_count = observation_count + 1 WHERE dataset_id = ?",
                (byte_size, dataset_id),
            )
        self._conn.commit()

    def ensure_observation(
        self, *, provenance: Provenance, raw_payload: Any, normalized_payload: Any | None = None
    ) -> None:
        """Make a provenance target available without changing dataset counters."""
        raw_text = canonical_json(raw_payload)
        normalized_text = None if normalized_payload is None else canonical_json(normalized_payload)
        self._conn.execute(
            """INSERT OR IGNORE INTO observations(
                observation_id, dataset_id, source, endpoint, retrieved_at,
                received_at, schema_version, request_metadata, raw_payload,
                normalized_payload, payload_sha256, byte_size, kind
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'source_reference')""",
            (
                provenance.observation_id,
                provenance.source,
                provenance.endpoint,
                provenance.retrieved_at,
                provenance.received_at,
                provenance.schema_version,
                canonical_json(provenance.request_metadata),
                raw_text,
                normalized_text,
                provenance.payload_sha256,
                len(raw_text.encode("utf-8")),
            ),
        )
        self._conn.commit()

    def insert_stream_event(
        self,
        *,
        dataset_id: str | None,
        observation_id: str | None,
        event_type: str,
        sequence: int | None,
        gap_detected: bool,
        resynchronized: bool,
        payload: Mapping[str, Any],
    ) -> None:
        self._conn.execute(
            """INSERT INTO stream_events(dataset_id, observation_id, event_type, sequence, gap_detected, resynchronized, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset_id,
                observation_id,
                event_type,
                sequence,
                int(gap_detected),
                int(resynchronized),
                canonical_json(payload),
            ),
        )
        self._conn.commit()

    def create_run(
        self,
        *,
        run_id: str,
        dataset_id: str | None,
        created_at: str,
        config: SimulationConfig,
        status: str = "running",
    ) -> None:
        if dataset_id is not None:
            self._conn.execute(
                "INSERT OR IGNORE INTO datasets(dataset_id, created_at, max_bytes, max_observations) VALUES (?, ?, ?, ?)",
                (dataset_id, created_at, 50 * 1024 * 1024, 100_000),
            )
        self._conn.execute(
            "INSERT INTO simulation_runs(run_id, dataset_id, created_at, config, status, model_versions) VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                dataset_id,
                created_at,
                canonical_json(config),
                status,
                canonical_json(config.model_versions),
            ),
        )
        self._conn.commit()

    def finish_run(self, run_id: str, status: str = "completed") -> None:
        self._conn.execute(
            "UPDATE simulation_runs SET status = ? WHERE run_id = ?", (status, run_id)
        )
        self._conn.commit()

    def insert_order(self, run_id: str, order: PaperOrder) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO orders(run_id, order_id, payload) VALUES (?, ?, ?)",
            (run_id, order.order_id, canonical_json(order)),
        )
        self._conn.commit()

    def insert_order_event(self, run_id: str, order: PaperOrder) -> None:
        self._conn.execute(
            "INSERT INTO order_events(run_id, order_id, status, payload) VALUES (?, ?, ?, ?)",
            (run_id, order.order_id, order.status, canonical_json(order)),
        )
        self._conn.commit()

    def insert_fill(self, run_id: str, fill: Fill) -> None:
        self._conn.execute(
            "INSERT INTO fills(run_id, fill_id, order_id, observation_id, payload) VALUES (?, ?, ?, ?, ?)",
            (run_id, fill.fill_id, fill.order_id, fill.observation_id, canonical_json(fill)),
        )
        self._conn.commit()

    def insert_ledger_entry(self, entry: LedgerEntry) -> None:
        self._conn.execute(
            "INSERT INTO ledger_entries(run_id, sequence, entry_id, payload) VALUES (?, ?, ?, ?)",
            (entry.run_id, entry.sequence, entry.entry_id, canonical_json(entry)),
        )
        self._conn.commit()

    def save_report(self, run_id: str, checksum: str, payload: Mapping[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO reports(run_id, checksum, payload) VALUES (?, ?, ?)",
            (run_id, checksum, canonical_json(payload)),
        )
        self._conn.commit()

    def get_observations(self, dataset_id: str | None = None) -> list[sqlite3.Row]:
        if dataset_id is None:
            return list(self._conn.execute("SELECT * FROM observations ORDER BY rowid").fetchall())
        return list(
            self._conn.execute(
                "SELECT * FROM observations WHERE dataset_id = ? ORDER BY rowid", (dataset_id,)
            ).fetchall()
        )

    def get_ledger_entries(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM ledger_entries WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall()
        import json

        return [json.loads(row["payload"]) for row in rows]

    def get_fills(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM fills WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        import json

        return [json.loads(row["payload"]) for row in rows]

    def get_orders(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM orders WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        import json

        return [json.loads(row["payload"]) for row in rows]

    def get_order_events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM order_events WHERE run_id = ? ORDER BY event_sequence", (run_id,)
        ).fetchall()
        import json

        return [json.loads(row["payload"]) for row in rows]

    def get_stream_events(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        if dataset_id is None:
            rows = self._conn.execute("SELECT * FROM stream_events ORDER BY id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM stream_events WHERE dataset_id = ? ORDER BY id", (dataset_id,)
            ).fetchall()
        import json

        return [
            {
                "id": row["id"],
                "dataset_id": row["dataset_id"],
                "observation_id": row["observation_id"],
                "event_type": row["event_type"],
                "sequence": row["sequence"],
                "gap_detected": bool(row["gap_detected"]),
                "resynchronized": bool(row["resynchronized"]),
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def get_report(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT checksum, payload FROM reports WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        import json

        result = json.loads(row["payload"])
        result["checksum"] = row["checksum"]
        return result

    def run_exists(self, run_id: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM simulation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            is not None
        )


Database = SQLiteStore
