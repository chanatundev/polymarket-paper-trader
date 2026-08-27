"""Market WebSocket event normalization and gap/resynchronization state."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from .errors import LocalValidationError, MalformedPayloadError
from .models import BookLevel, BookSnapshot, MarketEvent, Provenance
from .normalizers import (
    normalize_book,
    normalize_event,
)
from .precision import non_negative, probability


@dataclass(frozen=True)
class StreamUpdate:
    event: MarketEvent
    snapshot: BookSnapshot | None
    needs_resync: bool
    warnings: tuple[str, ...] = ()


class MarketStreamProcessor:
    """Apply full snapshots and sequence-checked incremental market events.

    Incremental messages received after a gap are never applied. Call
    :meth:`resync` with an authoritative REST snapshot before processing can
    resume; the resulting update remains visible as a resynchronization event.
    """

    def __init__(self, *, asset_id: str, expected_sequence: int | None = None) -> None:
        self.asset_id = asset_id
        self.snapshot: BookSnapshot | None = None
        self.last_sequence = expected_sequence - 1 if expected_sequence is not None else None
        self.gap_detected = False
        self.gap_events: list[MarketEvent] = []

    def apply(
        self, payload: Mapping[str, Any], *, provenance: Provenance | None = None
    ) -> StreamUpdate:
        event = normalize_event(
            payload, provenance=provenance, endpoint="/ws/market", provider="clob-ws"
        )
        if event.asset_id and event.asset_id != self.asset_id:
            raise LocalValidationError(
                "stream message belongs to another asset",
                details={"expected": self.asset_id, "actual": event.asset_id},
            )
        event = replace(event, asset_id=event.asset_id or self.asset_id)
        if event.event_type == "book":
            snapshot = normalize_book(
                payload, provenance=provenance, endpoint="/ws/market", provider="clob-ws"
            )
            if snapshot.asset_id != self.asset_id:
                raise LocalValidationError("book snapshot belongs to another asset")
            self.snapshot = replace(
                snapshot, continuity="authoritative", freshness=snapshot.freshness
            )
            self.last_sequence = snapshot.sequence
            if self.gap_detected:
                event = replace(event, gap_detected=True, resynchronized=True)
                self.gap_detected = False
            return StreamUpdate(event=event, snapshot=self.snapshot, needs_resync=False)

        if self.gap_detected:
            return StreamUpdate(
                event=replace(event, gap_detected=True),
                snapshot=self.snapshot,
                needs_resync=True,
                warnings=("incremental_event_ignored_until_resync",),
            )
        if (
            event.sequence is not None
            and self.last_sequence is not None
            and event.sequence != self.last_sequence + 1
        ):
            self.gap_detected = True
            event = replace(event, gap_detected=True)
            self.gap_events.append(event)
            return StreamUpdate(
                event=event,
                snapshot=self.snapshot,
                needs_resync=True,
                warnings=("sequence_gap_requires_authoritative_snapshot",),
            )
        if self.snapshot is None:
            self.gap_detected = True
            self.gap_events.append(event)
            return StreamUpdate(
                event=replace(event, gap_detected=True),
                snapshot=None,
                needs_resync=True,
                warnings=("incremental_event_requires_initial_authoritative_snapshot",),
            )
        snapshot = self._apply_incremental(event)
        if event.sequence is not None:
            self.last_sequence = event.sequence
        return StreamUpdate(event=event, snapshot=snapshot, needs_resync=False)

    def resync(
        self, snapshot: BookSnapshot, *, provenance: Provenance | None = None
    ) -> StreamUpdate:
        if snapshot.asset_id != self.asset_id:
            raise LocalValidationError("authoritative snapshot belongs to another asset")
        self.snapshot = replace(snapshot, continuity="resynchronized")
        self.last_sequence = snapshot.sequence
        self.gap_detected = False
        event = MarketEvent(
            event_type="authoritative_resync",
            market_id=snapshot.market_id,
            asset_id=self.asset_id,
            provider_timestamp=snapshot.provider_timestamp,
            received_at=snapshot.received_at,
            sequence=snapshot.sequence,
            payload={"reason": "sequence_gap", "book_hash": snapshot.book_hash},
            executable=True,
            provenance=provenance or snapshot.provenance,
            gap_detected=True,
            resynchronized=True,
        )
        return StreamUpdate(
            event=event,
            snapshot=self.snapshot,
            needs_resync=False,
            warnings=("authoritative_snapshot_applied_after_gap",),
        )

    def ingest(
        self,
        messages: Iterable[Mapping[str, Any]],
        *,
        snapshot_loader: Callable[[], BookSnapshot] | None = None,
        provenance_factory: Callable[[Mapping[str, Any]], Provenance] | None = None,
    ) -> tuple[StreamUpdate, ...]:
        updates: list[StreamUpdate] = []
        for payload in messages:
            provenance = provenance_factory(payload) if provenance_factory else None
            update = self.apply(payload, provenance=provenance)
            updates.append(update)
            if update.needs_resync and snapshot_loader is not None:
                updates.append(self.resync(snapshot_loader(), provenance=provenance))
        return tuple(updates)

    def _apply_incremental(self, event: MarketEvent) -> BookSnapshot | None:
        if self.snapshot is None:
            self.gap_detected = True
            return None
        payload = event.payload
        event_type = event.event_type
        bids = list(self.snapshot.bids)
        asks = list(self.snapshot.asks)
        if event_type in {"price_change", "book_update", "price_update"}:
            changes = payload.get("changes", payload.get("price_changes", [payload]))
            if isinstance(changes, Mapping):
                changes = [changes]
            if not isinstance(changes, list):
                raise MalformedPayloadError(
                    "stream price changes must be an array",
                    provider="clob-ws",
                    endpoint="/ws/market",
                )
            for change in changes:
                if not isinstance(change, Mapping):
                    raise MalformedPayloadError(
                        "stream price change must be an object",
                        provider="clob-ws",
                        endpoint="/ws/market",
                    )
                side = str(change.get("side", "")).casefold()
                price = probability(change.get("price"), field="stream.price")
                size = non_negative(
                    change.get("size", change.get("quantity", 0)), field="stream.size"
                )
                target = (
                    bids
                    if side in {"buy", "bid", "bids"}
                    else asks
                    if side in {"sell", "ask", "asks"}
                    else None
                )
                if target is None:
                    raise MalformedPayloadError(
                        "stream price change side is invalid",
                        provider="clob-ws",
                        endpoint="/ws/market",
                    )
                target[:] = [level for level in target if level.price != price]
                if size > 0:
                    target.append(
                        BookLevel(
                            price=price,
                            size=size,
                            order_id=str(change.get("order_id"))
                            if change.get("order_id") is not None
                            else None,
                            timestamp=event.provider_timestamp,
                        )
                    )
        elif event_type in {"last_trade_price", "last_trade", "trade"}:
            pass
        else:
            # Lifecycle and external context events are recorded but do not mutate executable depth.
            return replace(self.snapshot, continuity="contiguous")
        last_trade = self.snapshot.last_trade_price
        if event_type in {"last_trade_price", "last_trade", "trade"}:
            raw_price = payload.get("price", payload.get("last_trade_price"))
            if raw_price is not None:
                last_trade = probability(raw_price, field="last_trade_price")
        return replace(
            self.snapshot,
            bids=tuple(bids),
            asks=tuple(asks),
            sequence=event.sequence,
            provider_timestamp=event.provider_timestamp or self.snapshot.provider_timestamp,
            received_at=event.received_at,
            last_trade_price=last_trade,
            continuity="contiguous",
        )


class MarketWebSocketAdapter:
    """Offline-friendly stream adapter; network connection is supplied by caller."""

    def __init__(self, *, asset_id: str) -> None:
        self.processor = MarketStreamProcessor(asset_id=asset_id)

    def ingest_messages(
        self,
        messages: Iterable[Mapping[str, Any]],
        *,
        snapshot_loader: Callable[[], BookSnapshot] | None = None,
    ) -> tuple[StreamUpdate, ...]:
        return self.processor.ingest(messages, snapshot_loader=snapshot_loader)
