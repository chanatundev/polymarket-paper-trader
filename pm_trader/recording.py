"""Bounded observation recording and deterministic replay."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .api import Observation, ResourceLimits
from .errors import DatasetLimitError, LocalValidationError, MalformedPayloadError
from .models import BookLevel, BookSnapshot, MarketState, PaperOrder, Provenance, SimulationConfig
from .normalizers import make_provenance
from .serialization import canonical_json, sanitize, sha256_json
from .simulation import SimulationEngine, SimulationResult
from .storage import SQLiteStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Recorder:
    """Store sanitized raw and normalized observations under explicit bounds."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        dataset_id: str,
        limits: ResourceLimits | None = None,
        created_at: str | None = None,
    ) -> None:
        self.store = store
        self.dataset_id = dataset_id
        self.limits = limits or ResourceLimits()
        self.created_at = created_at or _now()
        self.store.create_dataset(
            dataset_id,
            created_at=self.created_at,
            max_bytes=self.limits.max_recorded_bytes,
            max_observations=self.limits.max_recorded_observations,
        )

    def record(
        self,
        observation: Observation[Any],
        *,
        normalized: Any | None = None,
        kind: str = "observation",
    ) -> Provenance:
        raw = sanitize(observation.raw_payload)
        normalized_payload = normalized if normalized is not None else observation.value
        raw_bytes = len(canonical_json(raw).encode("utf-8")) + len(
            canonical_json(normalized_payload).encode("utf-8")
        )
        used_bytes, used_count = self.store.dataset_usage(self.dataset_id)
        if used_count >= self.limits.max_recorded_observations:
            raise DatasetLimitError(
                "recording observation count exceeds configured limit",
                details={
                    "dataset_id": self.dataset_id,
                    "max_observations": self.limits.max_recorded_observations,
                },
            )
        if used_bytes + raw_bytes > self.limits.max_recorded_bytes:
            raise DatasetLimitError(
                "recording byte retention exceeds configured limit",
                details={
                    "dataset_id": self.dataset_id,
                    "max_bytes": self.limits.max_recorded_bytes,
                    "next_bytes": raw_bytes,
                },
            )
        self.store.insert_observation(
            dataset_id=self.dataset_id,
            provenance=observation.provenance,
            raw_payload=raw,
            normalized_payload=normalized_payload,
            byte_size=raw_bytes,
            kind=kind,
        )
        return observation.provenance

    def record_book(self, snapshot: BookSnapshot, *, raw_payload: Any | None = None) -> Provenance:
        provenance = snapshot.provenance or make_provenance(
            source="recorded",
            endpoint="/book",
            raw=raw_payload or snapshot,
            retrieved_at=snapshot.received_at,
            received_at=snapshot.received_at,
            schema_version="canonical-v1",
        )
        observation = Observation(
            value=snapshot, raw_payload=raw_payload or snapshot, provenance=provenance
        )
        return self.record(observation, normalized=snapshot, kind="book")

    def record_event(self, event: Any, *, raw_payload: Any | None = None) -> Provenance:
        provenance = getattr(event, "provenance", None) or make_provenance(
            source="recorded",
            endpoint="/ws/market",
            raw=raw_payload or event,
            schema_version="canonical-v1",
        )
        observation = Observation(
            value=event, raw_payload=raw_payload or event, provenance=provenance
        )
        result = self.record(observation, normalized=event, kind="event")
        if hasattr(event, "event_type"):
            self.store.insert_stream_event(
                dataset_id=self.dataset_id,
                observation_id=result.observation_id,
                event_type=str(event.event_type),
                sequence=getattr(event, "sequence", None),
                gap_detected=bool(getattr(event, "gap_detected", False)),
                resynchronized=bool(getattr(event, "resynchronized", False)),
                payload=getattr(event, "payload", {}),
            )
        return result

    def record_stream_updates(self, updates: Iterable[Any]) -> tuple[Provenance, ...]:
        """Record processor output and make gap/resync evidence queryable.

        A full snapshot and its normalized WebSocket event commonly share one
        provenance ID, so the snapshot is stored as the observation and the
        stream row is added directly instead of attempting a duplicate insert.
        """

        recorded: list[Provenance] = []
        for update in updates:
            event = getattr(update, "event", update)
            snapshot = getattr(update, "snapshot", None)
            event_provenance = self.record_event(event)
            recorded.append(event_provenance)
            if snapshot is None:
                continue
            if getattr(event, "event_type", "") in {"book", "authoritative_resync"}:
                if (
                    snapshot.provenance is not None
                    and snapshot.provenance.observation_id == event_provenance.observation_id
                ):
                    continue
                self.record_book(snapshot)
        return tuple(recorded)

    def export_jsonl(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = self.store.get_observations(self.dataset_id)
        with destination.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    canonical_json(
                        {
                            "observation_id": row["observation_id"],
                            "dataset_id": row["dataset_id"],
                            "source": row["source"],
                            "endpoint": row["endpoint"],
                            "retrieved_at": row["retrieved_at"],
                            "received_at": row["received_at"],
                            "schema_version": row["schema_version"],
                            "request_metadata": json.loads(row["request_metadata"]),
                            "raw_payload": json.loads(row["raw_payload"]),
                            "normalized_payload": json.loads(row["normalized_payload"])
                            if row["normalized_payload"]
                            else None,
                            "payload_sha256": row["payload_sha256"],
                            "kind": row["kind"],
                        }
                    )
                    + "\n"
                )
        return destination


def _book_from_payload(
    payload: Mapping[str, Any], provenance: Provenance | None = None
) -> BookSnapshot:
    def levels(key: str) -> tuple[BookLevel, ...]:
        values = payload.get(key) or []
        return tuple(
            BookLevel(
                price=Decimal(str(item["price"])),
                size=Decimal(str(item["size"])),
                order_id=item.get("order_id"),
                timestamp=item.get("timestamp"),
            )
            for item in values
        )

    return BookSnapshot(
        market_id=payload.get("market_id"),
        asset_id=str(payload["asset_id"]),
        bids=levels("bids"),
        asks=levels("asks"),
        provider_timestamp=str(payload["provider_timestamp"]),
        received_at=str(payload["received_at"]),
        book_hash=payload.get("book_hash"),
        sequence=payload.get("sequence"),
        min_order_size=Decimal(str(payload["min_order_size"]))
        if payload.get("min_order_size") is not None
        else None,
        tick_size=Decimal(str(payload["tick_size"]))
        if payload.get("tick_size") is not None
        else None,
        neg_risk=payload.get("neg_risk"),
        last_trade_price=Decimal(str(payload["last_trade_price"]))
        if payload.get("last_trade_price") is not None
        else None,
        freshness=payload.get("freshness", "unknown"),
        continuity=str(payload.get("continuity", "unknown")),
        provenance=provenance,
        unknown_reasons=payload.get("unknown_reasons", {}),
        unknown_fields=payload.get("unknown_fields", {}),
    )


def load_book_dataset(
    path: str | Path, *, max_events: int = 100_000
) -> tuple[tuple[BookSnapshot, ...], str]:
    """Load recorded book observations from JSONL and return a dataset hash."""
    source = Path(path)
    if not source.is_file():
        raise LocalValidationError("dataset file does not exist", details={"path": str(source)})
    snapshots: list[BookSnapshot] = []
    lines: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= max_events:
                raise DatasetLimitError(
                    "replay event limit exceeded", details={"max_events": max_events}
                )
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MalformedPayloadError(
                    "dataset contains invalid JSON",
                    provider="dataset",
                    endpoint=str(source),
                    details={"line": index + 1},
                ) from exc
            lines.append(record)
            if record.get("kind") != "book":
                continue
            normalized = record.get("normalized_payload")
            if not isinstance(normalized, Mapping):
                raise MalformedPayloadError(
                    "book dataset record lacks normalized payload",
                    provider="dataset",
                    endpoint=str(source),
                    details={"line": index + 1},
                )
            provenance = Provenance(
                observation_id=str(record["observation_id"]),
                source=str(record["source"]),
                endpoint=record.get("endpoint"),
                retrieved_at=str(record["retrieved_at"]),
                received_at=str(record["received_at"]),
                schema_version=str(record["schema_version"]),
                request_metadata=record.get("request_metadata", {}),
                payload_sha256=str(record.get("payload_sha256", "")),
            )
            snapshots.append(_book_from_payload(normalized, provenance=provenance))
    return tuple(snapshots), sha256_json(lines)


def load_stream_metadata(path: str | Path, *, max_events: int = 100_000) -> dict[str, Any]:
    """Return bounded gap/resync metadata without exposing raw dataset rows."""

    source = Path(path)
    if not source.is_file():
        raise LocalValidationError("dataset file does not exist", details={"path": str(source)})
    event_count = 0
    gap_count = 0
    resync_count = 0
    with source.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= max_events:
                raise DatasetLimitError(
                    "replay event limit exceeded", details={"max_events": max_events}
                )
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MalformedPayloadError(
                    "dataset contains invalid JSON",
                    provider="dataset",
                    endpoint=str(source),
                    details={"line": index + 1},
                ) from exc
            if record.get("kind") != "event":
                continue
            event_count += 1
            normalized = record.get("normalized_payload")
            if isinstance(normalized, Mapping) and normalized.get("gap_detected"):
                gap_count += 1
            if isinstance(normalized, Mapping) and normalized.get("resynchronized"):
                resync_count += 1
    return {
        "stream_events": event_count,
        "gap_events": gap_count,
        "resynchronizations": resync_count,
    }


class ReplayEngine:
    """Replay recorded full-depth snapshots with declared deterministic inputs."""

    def __init__(self, store: SQLiteStore, *, limits: ResourceLimits | None = None) -> None:
        self.store = store
        self.limits = limits or ResourceLimits()

    def replay(
        self,
        *,
        dataset_id: str,
        market: MarketState,
        snapshots: Iterable[BookSnapshot],
        orders: Iterable[PaperOrder],
        config: SimulationConfig | None = None,
        mark_prices: Mapping[str, Decimal] | None = None,
        extra_warnings: Iterable[str] = (),
        data_coverage: Mapping[str, Any] | None = None,
    ) -> SimulationResult:
        snapshot_list = tuple(snapshots)
        if len(snapshot_list) > self.limits.max_replay_events:
            raise DatasetLimitError(
                "replay event limit exceeded", details={"max_events": self.limits.max_replay_events}
            )
        order_list = tuple(orders)
        if not snapshot_list:
            raise LocalValidationError("replay dataset has no book snapshots")
        config = config or SimulationConfig()
        engine = SimulationEngine(self.store)
        result = engine.run_stream(
            market=market,
            snapshots=snapshot_list,
            orders=order_list,
            config=config,
            dataset_id=dataset_id,
            mark_prices=mark_prices,
            extra_warnings=[
                "replay_uses_declared_snapshot_sequence",
                "paper_fills_are_model_outputs",
                *extra_warnings,
            ],
            data_coverage={
                "recorded_snapshots": len(snapshot_list),
                "full_depth": True,
                "dataset_sha256": sha256_json(snapshot_list),
                **dict(data_coverage or {}),
            },
        )
        return result

    def replay_jsonl(
        self,
        path: str | Path,
        *,
        market: MarketState,
        orders: Iterable[PaperOrder],
        config: SimulationConfig | None = None,
        dataset_id: str | None = None,
    ) -> SimulationResult:
        snapshots, computed_dataset_id = load_book_dataset(
            path, max_events=(config.max_events if config else self.limits.max_replay_events)
        )
        metadata = load_stream_metadata(
            path, max_events=(config.max_events if config else self.limits.max_replay_events)
        )
        warnings: list[str] = []
        if metadata["gap_events"]:
            warnings.append(f"recorded_stream_gaps:{metadata['gap_events']}")
        if metadata["resynchronizations"]:
            warnings.append(f"recorded_stream_resynchronizations:{metadata['resynchronizations']}")
        return self.replay(
            dataset_id=dataset_id or computed_dataset_id,
            market=market,
            snapshots=snapshots,
            orders=orders,
            config=config,
            extra_warnings=warnings,
            data_coverage=metadata,
        )
