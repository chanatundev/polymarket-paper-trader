"""Provider-payload normalizers with strict required-field and unknown handling."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .errors import LocalValidationError, MalformedPayloadError
from .models import (
    BookLevel,
    BookSnapshot,
    FeeSchedule,
    Freshness,
    MarketEvent,
    MarketState,
    MarketToken,
    PricePoint,
    Provenance,
    PublicContext,
)
from .precision import decimal, non_negative, parse_bool, parse_timestamp, probability
from .serialization import sanitize, sha256_json

CANONICAL_SCHEMA_VERSION = "canonical-v1"
GAMMA_SCHEMA_VERSION = "gamma-public-2026-08"
CLOB_SCHEMA_VERSION = "clob-public-2026-08"
WS_SCHEMA_VERSION = "clob-market-ws-2026-08"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_provenance(
    *,
    source: str,
    endpoint: str | None,
    raw: Any,
    retrieved_at: str | None = None,
    received_at: str | None = None,
    schema_version: str = CANONICAL_SCHEMA_VERSION,
    request_metadata: Mapping[str, Any] | None = None,
    observation_id: str | None = None,
) -> Provenance:
    safe_raw = sanitize(raw)
    payload_hash = sha256_json(safe_raw)
    return Provenance(
        observation_id=observation_id or payload_hash[:24],
        source=source,
        endpoint=endpoint,
        retrieved_at=retrieved_at or now_utc(),
        received_at=received_at or now_utc(),
        schema_version=schema_version,
        request_metadata=dict(request_metadata or {}),
        payload_sha256=payload_hash,
    )


def _mapping(payload: Any, *, provider: str, endpoint: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise MalformedPayloadError(
            f"{provider} payload must be an object",
            provider=provider,
            endpoint=endpoint,
            details={"actual_type": type(payload).__name__},
        )
    return payload


def _pick(payload: Mapping[str, Any], *keys: str) -> tuple[Any, str | None]:
    for key in keys:
        if key in payload:
            return payload[key], key
    return None, None


def _json_list(value: Any, *, field: str, provider: str, endpoint: str) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MalformedPayloadError(
                f"{field} must contain valid JSON",
                provider=provider,
                endpoint=endpoint,
                details={"field": field},
            ) from exc
    if not isinstance(value, list):
        raise MalformedPayloadError(
            f"{field} must be an array",
            provider=provider,
            endpoint=endpoint,
            details={"field": field},
        )
    return value


def _required_text(
    payload: Mapping[str, Any], keys: tuple[str, ...], *, field: str, provider: str, endpoint: str
) -> str:
    value, source_field = _pick(payload, *keys)
    if not isinstance(value, str) or not value.strip():
        raise MalformedPayloadError(
            f"required field {field} is missing or invalid",
            provider=provider,
            endpoint=endpoint,
            details={"field": field, "accepted_fields": list(keys), "source_field": source_field},
        )
    return value.strip()


def _optional_decimal(
    payload: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    field: str,
    unknown: dict[str, str],
    provider: str,
    endpoint: str,
    minimum: Decimal | None = None,
) -> Decimal | None:
    value, source_field = _pick(payload, *keys)
    if source_field is None or value is None or value == "":
        unknown[field] = f"provider field unavailable ({'/'.join(keys)})"
        return None
    try:
        result = decimal(value, field=field)
    except LocalValidationError as exc:
        raise MalformedPayloadError(
            f"field {field} is invalid",
            provider=provider,
            endpoint=endpoint,
            details={"field": field, "source_field": source_field, "value": sanitize(value)},
        ) from exc
    if minimum is not None and result < minimum:
        raise MalformedPayloadError(
            f"field {field} is below its allowed minimum",
            provider=provider,
            endpoint=endpoint,
            details={"field": field, "minimum": str(minimum)},
        )
    return result


def _optional_bool(
    payload: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    field: str,
    unknown: dict[str, str],
    provider: str,
    endpoint: str,
) -> bool | None:
    value, source_field = _pick(payload, *keys)
    if source_field is None or value is None:
        unknown[field] = f"provider field unavailable ({'/'.join(keys)})"
        return None
    try:
        return parse_bool(value, field=field)
    except LocalValidationError as exc:
        raise MalformedPayloadError(
            f"field {field} is invalid",
            provider=provider,
            endpoint=endpoint,
            details={"field": field, "source_field": source_field, "value": sanitize(value)},
        ) from exc


def normalize_fee_schedule(
    payload: Any,
    *,
    source: str = "gamma",
    endpoint: str = "/markets",
    unknown: dict[str, str] | None = None,
) -> FeeSchedule | None:
    """Normalize current ``feeSchedule`` or an explicit legacy base-fee shape.

    A missing fee schedule stays ``None``. Legacy ``base_fee``/``fee_rate_bps``
    is converted only when the payload explicitly names that field, with the
    compatibility formula and version visible on the returned record.
    """
    unknown = unknown if unknown is not None else {}
    if payload is None:
        unknown["fee_schedule"] = "provider did not return a fee schedule"
        return None
    if isinstance(payload, Mapping):
        rate_value, rate_field = _pick(payload, "rate", "fee_rate", "feeRate", "r")
        exponent_value, exponent_field = _pick(payload, "exponent", "e")
        if rate_field is not None:
            if exponent_field is None:
                unknown["fee_schedule"] = "schedule exponent is unavailable"
                return None
            try:
                rate = decimal(rate_value, field="fee_schedule.rate")
                exponent = decimal(exponent_value, field="fee_schedule.exponent")
                if rate < 0 or rate > 1 or exponent < 0:
                    raise ValueError
                taker_only = parse_bool(
                    payload.get("takerOnly", payload.get("taker_only", True)), field="takerOnly"
                )
                rebate_value, _ = _pick(payload, "rebateRate", "rebate_rate", "rebate")
                rebate = (
                    None
                    if rebate_value is None
                    else decimal(rebate_value, field="fee_schedule.rebate_rate")
                )
                if rebate is not None and (rebate < 0 or rebate > 1):
                    raise ValueError
            except (LocalValidationError, ValueError) as exc:
                raise MalformedPayloadError(
                    "fee schedule contains invalid rate, exponent, takerOnly, or rebateRate",
                    provider=source,
                    endpoint=endpoint,
                    details={"field": rate_field},
                ) from exc
            return FeeSchedule(
                rate=rate,
                exponent=exponent,
                taker_only=taker_only,
                rebate_rate=rebate,
                version="clob-v2-dynamic",
                source=source,
                raw=sanitize(payload),
            )

        legacy_value, legacy_field = _pick(payload, "base_fee", "fee_rate_bps", "feeRateBps")
        if legacy_field is not None:
            try:
                bps = non_negative(legacy_value, field=legacy_field)
            except LocalValidationError as exc:
                raise MalformedPayloadError(
                    "legacy fee rate is invalid",
                    provider=source,
                    endpoint=endpoint,
                    details={"field": legacy_field},
                ) from exc
            return FeeSchedule(
                rate=bps / Decimal(10000),
                exponent=Decimal(1),
                taker_only=True,
                rebate_rate=None,
                version="legacy-base-fee-bps",
                formula="legacy_shares_rate_price_product",
                source=source,
                raw=sanitize(payload),
            )
    unknown["fee_schedule"] = "fee schedule shape is not recognized"
    return None


def normalize_market(
    payload: Any,
    *,
    provenance: Provenance | None = None,
    endpoint: str = "/markets/keyset",
    provider: str = "gamma",
) -> MarketState:
    raw = _mapping(payload, provider=provider, endpoint=endpoint)
    consumed: set[str] = set()
    unknown: dict[str, str] = {}

    condition_id = _required_text(
        raw,
        ("conditionId", "condition_id", "conditionID"),
        field="condition_id",
        provider=provider,
        endpoint=endpoint,
    )
    slug = _required_text(
        raw, ("slug", "market_slug"), field="slug", provider=provider, endpoint=endpoint
    )
    question = _required_text(
        raw, ("question", "title"), field="question", provider=provider, endpoint=endpoint
    )
    for key in raw:
        if key in {"conditionId", "condition_id", "conditionID"}:
            consumed.add(key)
        if key in {"slug", "market_slug"}:
            consumed.add(key)
        if key in {"question", "title"}:
            consumed.add(key)

    outcomes_value, outcomes_field = _pick(raw, "outcomes", "outcomeNames")
    if outcomes_field is None:
        raise MalformedPayloadError(
            "required field outcomes is missing",
            provider=provider,
            endpoint=endpoint,
            details={"field": "outcomes"},
        )
    outcomes_raw = _json_list(
        outcomes_value, field="outcomes", provider=provider, endpoint=endpoint
    )
    outcomes: list[str] = []
    for index, outcome in enumerate(outcomes_raw):
        if not isinstance(outcome, str) or not outcome.strip():
            raise MalformedPayloadError(
                "outcomes contains an invalid value",
                provider=provider,
                endpoint=endpoint,
                details={"index": index},
            )
        outcomes.append(outcome.strip())
    if not outcomes:
        raise MalformedPayloadError(
            "outcomes cannot be empty",
            provider=provider,
            endpoint=endpoint,
            details={"field": "outcomes"},
        )
    consumed.add(outcomes_field)

    token_value, token_field = _pick(raw, "clobTokenIds", "clob_token_ids", "tokens")
    if token_field is None:
        raise MalformedPayloadError(
            "required token mapping is missing",
            provider=provider,
            endpoint=endpoint,
            details={"field": "clobTokenIds"},
        )
    token_items = _json_list(token_value, field=token_field, provider=provider, endpoint=endpoint)
    tokens: list[MarketToken] = []
    for index, token in enumerate(token_items):
        if isinstance(token, str):
            token_id = token
            outcome = outcomes[index] if index < len(outcomes) else f"Outcome {index + 1}"
        elif isinstance(token, Mapping):
            token_id_value, _ = _pick(token, "token_id", "tokenId", "asset_id", "assetId", "id")
            outcome_value, _ = _pick(token, "outcome", "name")
            token_id = str(token_id_value) if token_id_value is not None else ""
            outcome = (
                str(outcome_value)
                if outcome_value is not None
                else (outcomes[index] if index < len(outcomes) else "")
            )
        else:
            token_id = ""
            outcome = ""
        if not token_id.strip() or not outcome.strip():
            raise MalformedPayloadError(
                "token mapping contains an invalid value",
                provider=provider,
                endpoint=endpoint,
                details={"index": index, "field": token_field},
            )
        tokens.append(MarketToken(token_id=token_id.strip(), outcome=outcome.strip()))
    consumed.add(token_field)
    if len(tokens) != len(outcomes):
        raise MalformedPayloadError(
            "outcomes and token mapping lengths differ",
            provider=provider,
            endpoint=endpoint,
            details={"outcomes": len(outcomes), "tokens": len(tokens)},
        )

    prices_value, prices_field = _pick(raw, "outcomePrices", "outcome_prices", "prices")
    if prices_field is None or prices_value is None:
        outcome_prices: tuple[Decimal | None, ...] = tuple(None for _ in outcomes)
        unknown["outcome_prices"] = "provider field unavailable"
    else:
        prices_raw = _json_list(
            prices_value, field=prices_field, provider=provider, endpoint=endpoint
        )
        if len(prices_raw) != len(outcomes):
            raise MalformedPayloadError(
                "outcome prices and outcomes lengths differ",
                provider=provider,
                endpoint=endpoint,
                details={"prices": len(prices_raw), "outcomes": len(outcomes)},
            )
        try:
            outcome_prices = tuple(
                probability(value, field=f"outcome_prices[{i}]")
                for i, value in enumerate(prices_raw)
            )
        except LocalValidationError as exc:
            raise MalformedPayloadError(
                "outcomePrices contains an invalid probability",
                provider=provider,
                endpoint=endpoint,
                details={"field": prices_field},
            ) from exc
        consumed.add(prices_field)

    for key_group in (
        ("active",),
        ("closed",),
        ("acceptingOrders", "accepting_orders", "acceptingOrdersEnabled"),
        ("enableOrderBook", "enable_order_book"),
        ("negRisk", "neg_risk"),
        ("feesEnabled", "fees_enabled"),
    ):
        consumed.update(key for key in key_group if key in raw)
    active = _optional_bool(
        raw, ("active",), field="active", unknown=unknown, provider=provider, endpoint=endpoint
    )
    closed = _optional_bool(
        raw, ("closed",), field="closed", unknown=unknown, provider=provider, endpoint=endpoint
    )
    accepting = _optional_bool(
        raw,
        ("acceptingOrders", "accepting_orders", "acceptingOrdersEnabled"),
        field="accepting_orders",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
    )
    enable_book = _optional_bool(
        raw,
        ("enableOrderBook", "enable_order_book"),
        field="enable_order_book",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
    )
    neg_risk = _optional_bool(
        raw,
        ("negRisk", "neg_risk"),
        field="neg_risk",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
    )
    fees_enabled = _optional_bool(
        raw,
        ("feesEnabled", "fees_enabled"),
        field="fees_enabled",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
    )

    min_size = _optional_decimal(
        raw,
        ("orderMinSize", "order_min_size", "minimum_order_size", "minimumOrderSize"),
        field="min_order_size",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
        minimum=Decimal(0),
    )
    tick_size = _optional_decimal(
        raw,
        ("orderPriceMinTickSize", "order_price_min_tick_size", "tick_size", "minimum_tick_size"),
        field="tick_size",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
        minimum=Decimal("0.00000001"),
    )
    consumed.update(
        key
        for key in (
            "orderMinSize",
            "order_min_size",
            "minimum_order_size",
            "minimumOrderSize",
            "orderPriceMinTickSize",
            "order_price_min_tick_size",
            "tick_size",
            "minimum_tick_size",
        )
        if key in raw
    )

    schedule_value, schedule_field = _pick(raw, "feeSchedule", "fee_schedule")
    if schedule_field is None:
        # An explicit legacy fee field is a compatibility shape, not a default.
        schedule_value, schedule_field = _pick(raw, "base_fee", "fee_rate_bps", "feeRateBps")
    fee_schedule = normalize_fee_schedule(
        schedule_value
        if schedule_field is not None
        and schedule_field not in {"base_fee", "fee_rate_bps", "feeRateBps"}
        else (raw if schedule_field is not None else None),
        source=provider,
        endpoint=endpoint,
        unknown=unknown,
    )
    consumed.update(
        key
        for key in ("feeSchedule", "fee_schedule", "base_fee", "fee_rate_bps", "feeRateBps")
        if key in raw
    )

    provider_timestamp_value, provider_timestamp_field = _pick(
        raw, "updatedAt", "updated_at", "endDateIso", "endDate", "timestamp"
    )
    provider_timestamp = None
    if provider_timestamp_value is not None:
        provider_timestamp = parse_timestamp(provider_timestamp_value, field="provider_timestamp")
        consumed.add(provider_timestamp_field or "")
    else:
        unknown["provider_timestamp"] = "provider field unavailable"

    event_id_value, event_id_field = _pick(raw, "eventId", "event_id", "parentEventId")
    event_slug_value, event_slug_field = _pick(raw, "eventSlug", "event_slug")
    event_id = str(event_id_value) if event_id_value is not None else None
    event_slug = str(event_slug_value) if event_slug_value is not None else None
    consumed.update(key for key in (event_id_field, event_slug_field) if key)
    if (
        isinstance(raw.get("events"), list)
        and raw["events"]
        and isinstance(raw["events"][0], Mapping)
    ):
        event = raw["events"][0]
        event_id = event_id or str(event.get("id")) if event.get("id") is not None else event_id
        event_slug = (
            event_slug or str(event.get("slug")) if event.get("slug") is not None else event_slug
        )
        consumed.add("events")

    description_value, description_field = _pick(raw, "description", "subtitle")
    category_value, category_field = _pick(raw, "category", "marketCategory")
    consumed.update(key for key in (description_field, category_field) if key)
    known_keys = consumed | {
        "id",
        "createdAt",
        "created_at",
        "startDate",
        "startDateIso",
        "volume",
        "volumeNum",
        "liquidity",
        "liquidityNum",
        "slug",
        "question",
    }
    unknown_fields = {key: sanitize(value) for key, value in raw.items() if key not in known_keys}

    return MarketState(
        condition_id=condition_id,
        slug=slug,
        question=question,
        outcomes=tuple(outcomes),
        tokens=tuple(tokens),
        event_id=event_id,
        event_slug=event_slug,
        description=str(description_value) if description_value is not None else None,
        outcome_prices=outcome_prices,
        active=active,
        closed=closed,
        accepting_orders=accepting,
        enable_order_book=enable_book,
        min_order_size=min_size,
        tick_size=tick_size,
        neg_risk=neg_risk,
        fees_enabled=fees_enabled,
        fee_schedule=fee_schedule,
        category=str(category_value) if category_value is not None else None,
        provider_timestamp=provider_timestamp,
        observed_at=provenance.received_at if provenance else None,
        freshness=Freshness.UNKNOWN,
        unknown_reasons=unknown,
        unknown_fields=unknown_fields,
        provenance=provenance,
    )


def _book_levels(value: Any, *, field: str, provider: str, endpoint: str) -> tuple[BookLevel, ...]:
    items = _json_list(value, field=field, provider=provider, endpoint=endpoint)
    levels: list[BookLevel] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise MalformedPayloadError(
                "book level must be an object",
                provider=provider,
                endpoint=endpoint,
                details={"field": field, "index": index},
            )
        price_value, _ = _pick(item, "price", "p")
        size_value, _ = _pick(item, "size", "quantity", "q")
        if price_value is None or size_value is None:
            raise MalformedPayloadError(
                "book level requires price and size",
                provider=provider,
                endpoint=endpoint,
                details={"field": field, "index": index},
            )
        try:
            price = probability(price_value, field=f"{field}[{index}].price")
            size = non_negative(size_value, field=f"{field}[{index}].size")
        except LocalValidationError as exc:
            raise MalformedPayloadError(
                "book level contains invalid price or size",
                provider=provider,
                endpoint=endpoint,
                details={"field": field, "index": index},
            ) from exc
        order_id_value, _ = _pick(item, "order_id", "orderId", "id")
        timestamp_value, _ = _pick(item, "timestamp", "time")
        levels.append(
            BookLevel(
                price=price,
                size=size,
                order_id=str(order_id_value) if order_id_value is not None else None,
                timestamp=parse_timestamp(timestamp_value, field=f"{field}[{index}].timestamp")
                if timestamp_value is not None
                else None,
            )
        )
    return tuple(levels)


def normalize_book(
    payload: Any,
    *,
    provenance: Provenance | None = None,
    endpoint: str = "/book",
    provider: str = "clob",
) -> BookSnapshot:
    raw = _mapping(payload, provider=provider, endpoint=endpoint)
    asset_id = _required_text(
        raw,
        ("asset_id", "assetId", "token_id", "tokenId"),
        field="asset_id",
        provider=provider,
        endpoint=endpoint,
    )
    timestamp_value, timestamp_field = _pick(
        raw, "timestamp", "provider_timestamp", "providerTimestamp"
    )
    if timestamp_field is None:
        raise MalformedPayloadError(
            "book timestamp is required",
            provider=provider,
            endpoint=endpoint,
            details={"field": "timestamp"},
        )
    try:
        provider_timestamp = parse_timestamp(timestamp_value, field="timestamp")
    except LocalValidationError as exc:
        raise MalformedPayloadError(
            "book timestamp is invalid",
            provider=provider,
            endpoint=endpoint,
            details={"field": timestamp_field},
        ) from exc
    bids_value, bids_field = _pick(raw, "bids")
    asks_value, asks_field = _pick(raw, "asks")
    if bids_field is None or asks_field is None:
        raise MalformedPayloadError(
            "book bids and asks are required",
            provider=provider,
            endpoint=endpoint,
            details={"bids": bids_field is not None, "asks": asks_field is not None},
        )
    bids = _book_levels(bids_value, field="bids", provider=provider, endpoint=endpoint)
    asks = _book_levels(asks_value, field="asks", provider=provider, endpoint=endpoint)
    unknown: dict[str, str] = {}
    market_value, _ = _pick(raw, "market", "condition_id", "conditionId")
    hash_value, hash_field = _pick(raw, "hash", "book_hash", "bookHash")
    if hash_field is None or hash_value in (None, ""):
        unknown["book_hash"] = "provider field unavailable"
    min_size = _optional_decimal(
        raw,
        ("min_order_size", "minOrderSize", "orderMinSize"),
        field="min_order_size",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
        minimum=Decimal(0),
    )
    tick_size = _optional_decimal(
        raw,
        ("tick_size", "tickSize", "orderPriceMinTickSize"),
        field="tick_size",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
        minimum=Decimal("0.00000001"),
    )
    neg_risk = _optional_bool(
        raw,
        ("neg_risk", "negRisk"),
        field="neg_risk",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
    )
    last_trade = _optional_decimal(
        raw,
        ("last_trade_price", "lastTradePrice"),
        field="last_trade_price",
        unknown=unknown,
        provider=provider,
        endpoint=endpoint,
        minimum=Decimal(0),
    )
    sequence_value, sequence_field = _pick(raw, "sequence", "sequence_number", "seq")
    sequence: int | None = None
    if sequence_value is not None:
        try:
            sequence = int(sequence_value)
        except (TypeError, ValueError) as exc:
            raise MalformedPayloadError(
                "book sequence is invalid",
                provider=provider,
                endpoint=endpoint,
                details={"field": sequence_field},
            ) from exc
    unknown_keys = {
        "asset_id",
        "assetId",
        "token_id",
        "tokenId",
        "timestamp",
        "provider_timestamp",
        "providerTimestamp",
        "bids",
        "asks",
        "market",
        "condition_id",
        "conditionId",
        "hash",
        "book_hash",
        "bookHash",
        "min_order_size",
        "minOrderSize",
        "orderMinSize",
        "tick_size",
        "tickSize",
        "orderPriceMinTickSize",
        "neg_risk",
        "negRisk",
        "last_trade_price",
        "lastTradePrice",
        "sequence",
        "sequence_number",
        "seq",
    }
    unknown_fields = {key: sanitize(value) for key, value in raw.items() if key not in unknown_keys}
    return BookSnapshot(
        market_id=str(market_value) if market_value is not None else None,
        asset_id=asset_id,
        bids=bids,
        asks=asks,
        provider_timestamp=provider_timestamp,
        received_at=provenance.received_at if provenance else now_utc(),
        book_hash=str(hash_value) if hash_value is not None else None,
        sequence=sequence,
        min_order_size=min_size,
        tick_size=tick_size,
        neg_risk=neg_risk,
        last_trade_price=last_trade,
        freshness=Freshness.UNKNOWN,
        continuity="authoritative" if sequence is None else "contiguous",
        provenance=provenance,
        unknown_reasons=unknown,
        unknown_fields=unknown_fields,
    )


def normalize_event(
    payload: Any,
    *,
    provenance: Provenance | None = None,
    endpoint: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    provider: str = "clob-ws",
) -> MarketEvent:
    raw = _mapping(payload, provider=provider, endpoint=endpoint)
    event_type_value, _ = _pick(raw, "event_type", "eventType", "type")
    if not isinstance(event_type_value, str) or not event_type_value.strip():
        raise MalformedPayloadError(
            "stream event type is required",
            provider=provider,
            endpoint=endpoint,
            details={"field": "event_type"},
        )
    market_value, _ = _pick(raw, "market", "condition_id", "conditionId")
    asset_value, _ = _pick(raw, "asset_id", "assetId", "token_id", "tokenId")
    timestamp_value, _ = _pick(raw, "timestamp", "time", "provider_timestamp")
    sequence_value, _ = _pick(raw, "sequence", "sequence_number", "seq")
    sequence = None
    if sequence_value is not None:
        try:
            sequence = int(sequence_value)
        except (TypeError, ValueError) as exc:
            raise MalformedPayloadError(
                "stream sequence is invalid",
                provider=provider,
                endpoint=endpoint,
                details={"field": "sequence"},
            ) from exc
    event_type = event_type_value.strip().casefold()
    executable = event_type not in {
        "comment",
        "crypto_price",
        "rtds",
        "external_context",
        "profile",
    }
    return MarketEvent(
        event_type=event_type,
        market_id=str(market_value) if market_value is not None else None,
        asset_id=str(asset_value) if asset_value is not None else None,
        provider_timestamp=parse_timestamp(timestamp_value, field="provider_timestamp")
        if timestamp_value is not None
        else None,
        received_at=provenance.received_at if provenance else now_utc(),
        sequence=sequence,
        payload=sanitize(raw),
        executable=executable,
        provenance=provenance,
    )


def normalize_price_history(
    payload: Any,
    *,
    provenance: Provenance | None = None,
    max_points: int = 10_000,
    endpoint: str = "/prices-history",
    provider: str = "clob",
) -> tuple[PricePoint, ...]:
    raw = _mapping(payload, provider=provider, endpoint=endpoint)
    points_value, points_field = _pick(raw, "history", "prices")
    if points_field is None:
        raise MalformedPayloadError(
            "price history requires history",
            provider=provider,
            endpoint=endpoint,
            details={"field": "history"},
        )
    points_raw = _json_list(points_value, field="history", provider=provider, endpoint=endpoint)
    if len(points_raw) > max_points:
        raise MalformedPayloadError(
            "price history exceeds configured point limit",
            provider=provider,
            endpoint=endpoint,
            details={"max_points": max_points, "actual": len(points_raw)},
        )
    points: list[PricePoint] = []
    for index, point in enumerate(points_raw):
        if not isinstance(point, Mapping):
            raise MalformedPayloadError(
                "price history point must be an object",
                provider=provider,
                endpoint=endpoint,
                details={"index": index},
            )
        timestamp_value, _ = _pick(point, "t", "timestamp", "time")
        price_value, _ = _pick(point, "p", "price")
        try:
            timestamp = parse_timestamp(timestamp_value, field=f"history[{index}].timestamp")
            price = probability(price_value, field=f"history[{index}].price")
        except (LocalValidationError, TypeError) as exc:
            raise MalformedPayloadError(
                "price history point contains invalid timestamp or price",
                provider=provider,
                endpoint=endpoint,
                details={"index": index},
            ) from exc
        points.append(PricePoint(timestamp=timestamp, price=price, provenance=provenance))
    return tuple(points)


def normalize_public_context(
    context_type: str,
    payload: Any,
    *,
    provenance: Provenance | None = None,
) -> PublicContext:
    raw = _mapping(payload, provider="data", endpoint=f"/{context_type}")
    records_value, records_field = _pick(raw, "data", "activity", "positions", "trades", "results")
    if records_field is None:
        records = (sanitize(raw),)
    else:
        records_raw = _json_list(
            records_value, field=records_field, provider="data", endpoint=f"/{context_type}"
        )
        records = tuple(
            sanitize(item) if isinstance(item, Mapping) else {"value": sanitize(item)}
            for item in records_raw
        )
    return PublicContext(
        context_type=context_type,
        records=records,
        as_of=provenance.received_at if provenance else now_utc(),
        executable=False,
        provenance=provenance,
    )
