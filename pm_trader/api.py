"""Versioned public Gamma, CLOB, and Data API adapters.

The adapters own provider routes and field names. They return raw sanitized
payloads together with canonical observations and provenance; the simulation
core never constructs provider URLs or reads provider field names.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, Protocol, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .errors import (
    LocalValidationError,
    MalformedPayloadError,
    NetworkProviderError,
    ProviderRejectedError,
    RateLimitError,
)
from .models import BookSnapshot, FeeSchedule, MarketState, PricePoint, Provenance, PublicContext
from .normalizers import (
    CLOB_SCHEMA_VERSION,
    GAMMA_SCHEMA_VERSION,
    make_provenance,
    normalize_book,
    normalize_fee_schedule,
    normalize_market,
    normalize_price_history,
    normalize_public_context,
)
from .precision import decimal
from .serialization import sanitize

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"

T = TypeVar("T")


@dataclass(frozen=True)
class ResourceLimits:
    """Explicit bounds for all provider and agent-facing operations."""

    max_page_size: int = 100
    max_history_points: int = 10_000
    max_recorded_bytes: int = 50 * 1024 * 1024
    max_recorded_observations: int = 100_000
    max_replay_events: int = 100_000
    max_response_bytes: int = 64 * 1024
    max_search_results: int = 50


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    data: Any
    headers: Mapping[str, str] = field(default_factory=dict)
    url: str = ""


class JsonTransport(Protocol):
    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float = 10.0
    ) -> TransportResponse: ...


class UrllibJsonTransport:
    """Dependency-free JSON GET transport for bounded public reads."""

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float = 10.0
    ) -> TransportResponse:
        query = {key: value for key, value in (params or {}).items() if value is not None}
        request_url = f"{url}?{urlencode(query, doseq=True)}" if query else url
        request = Request(
            request_url, headers={"Accept": "application/json", "User-Agent": "pm-trader/0.2"}
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read()
                data = json.loads(body.decode("utf-8"))
                return TransportResponse(
                    response.status, data, dict(response.headers.items()), request_url
                )
        except HTTPError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            raise NetworkProviderError(
                f"public request failed: {exc}",
                provider="http",
                endpoint=request_url,
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MalformedPayloadError(
                "provider response was not valid JSON",
                provider="http",
                endpoint=request_url,
            ) from exc


class FixtureTransport:
    """Small deterministic transport used by contract tests and offline demos."""

    def __init__(self, responses: Mapping[str, Any]) -> None:
        self.responses = dict(responses)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float = 10.0
    ) -> TransportResponse:
        del timeout
        normalized_params = dict(params or {})
        self.requests.append((url, normalized_params))
        if url not in self.responses:
            raise NetworkProviderError(
                "fixture route is not registered",
                provider="fixture",
                endpoint=url,
                details={"params": normalized_params},
            )
        response = self.responses[url]
        if isinstance(response, TransportResponse):
            return response
        return TransportResponse(status_code=200, data=response, url=url)


@dataclass(frozen=True)
class Observation(Generic[T]):
    """Raw sanitized payload plus its canonical interpretation."""

    value: T
    raw_payload: Any
    provenance: Provenance
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CursorPage(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None
    query: Mapping[str, Any]
    response_metadata: Mapping[str, Any]
    observation: Observation[Any]


@dataclass(frozen=True)
class SearchResult:
    query: str
    markets: tuple[MarketState, ...]
    events: tuple[Mapping[str, Any], ...]
    tags: tuple[Mapping[str, Any], ...]
    profiles: tuple[Mapping[str, Any], ...]
    pagination: Mapping[str, Any]
    observation: Observation[Any]


@dataclass(frozen=True)
class EventResult:
    event: Mapping[str, Any]
    markets: tuple[MarketState, ...]
    observation: Observation[Any]


@dataclass(frozen=True)
class FeeRateResult:
    base_fee_bps: int | None
    schedule: FeeSchedule | None
    observation: Observation[Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _Adapter:
    provider = "provider"
    schema_version = "provider-v1"

    def __init__(
        self,
        *,
        transport: JsonTransport | None = None,
        base_url: str,
        limits: ResourceLimits | None = None,
        timeout: float = 10.0,
        clock: callable | None = None,
    ) -> None:
        self.transport = transport or UrllibJsonTransport()
        self.base_url = base_url.rstrip("/")
        self.limits = limits or ResourceLimits()
        self.timeout = timeout
        self.clock = clock or _utc_now

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Observation[Any]:
        endpoint = f"{self.base_url}{path}"
        requested_at = self.clock()
        try:
            response = self.transport.get(endpoint, params=params, timeout=self.timeout)
        except RateLimitError:
            raise
        except NetworkProviderError:
            raise
        except HTTPError as exc:
            provider = self.provider
            if exc.code == 429:
                raise RateLimitError(
                    f"{provider} rate limited the request",
                    provider=provider,
                    endpoint=endpoint,
                    status_code=exc.code,
                ) from exc
            raise ProviderRejectedError(
                f"{provider} rejected the request with HTTP {exc.code}",
                provider=provider,
                endpoint=endpoint,
                status_code=exc.code,
            ) from exc
        if response.status_code == 429:
            raise RateLimitError(
                f"{self.provider} rate limited the request",
                provider=self.provider,
                endpoint=endpoint,
                status_code=429,
            )
        if response.status_code >= 400:
            raise ProviderRejectedError(
                f"{self.provider} rejected the request with HTTP {response.status_code}",
                provider=self.provider,
                endpoint=endpoint,
                status_code=response.status_code,
                details={"response": sanitize(response.data)},
            )
        provenance = make_provenance(
            source=self.provider,
            endpoint=path,
            raw=response.data,
            retrieved_at=requested_at,
            received_at=self.clock(),
            schema_version=self.schema_version,
            request_metadata={"params": sanitize(dict(params or {})), "url": endpoint},
        )
        return Observation(
            value=response.data, raw_payload=sanitize(response.data), provenance=provenance
        )


class GammaAdapter(_Adapter):
    provider = "gamma"
    schema_version = GAMMA_SCHEMA_VERSION

    def __init__(
        self,
        *,
        transport: JsonTransport | None = None,
        base_url: str = GAMMA_BASE,
        limits: ResourceLimits | None = None,
        timeout: float = 10.0,
        clock: callable | None = None,
    ) -> None:
        super().__init__(
            transport=transport, base_url=base_url, limits=limits, timeout=timeout, clock=clock
        )

    def list_markets(
        self,
        *,
        limit: int = 20,
        after_cursor: str | None = None,
        ascending: bool = True,
        filters: Mapping[str, Any] | None = None,
    ) -> CursorPage[MarketState]:
        if limit < 1 or limit > self.limits.max_page_size:
            raise LocalValidationError(
                "limit is outside the configured page-size bound",
                details={"limit": limit, "max": self.limits.max_page_size},
            )
        query: dict[str, Any] = {"limit": limit, "ascending": str(ascending).lower()}
        if after_cursor is not None:
            query["after_cursor"] = after_cursor
        for key, value in (filters or {}).items():
            if key.casefold() == "offset":
                raise LocalValidationError(
                    "offset pagination is not supported; use after_cursor", details={"field": key}
                )
            query[key] = value
        observation = self._get("/markets/keyset", query)
        raw = observation.value
        if isinstance(raw, list):
            # Dated legacy compatibility shape: retain it and expose that the cursor is unavailable.
            market_payloads = raw
            next_cursor = None
            metadata: Mapping[str, Any] = {"compatibility": "legacy-list", "raw_shape": "array"}
        elif isinstance(raw, Mapping):
            market_payloads = raw.get("markets")
            if not isinstance(market_payloads, list):
                raise MalformedPayloadError(
                    "Gamma keyset response requires markets array",
                    provider=self.provider,
                    endpoint="/markets/keyset",
                    details={"keys": list(raw.keys())},
                )
            next_value = raw.get("next_cursor")
            next_cursor = str(next_value) if next_value not in (None, "") else None
            if next_cursor is not None and next_cursor == after_cursor:
                raise MalformedPayloadError(
                    "Gamma keyset cursor did not advance",
                    provider=self.provider,
                    endpoint="/markets/keyset",
                    details={"cursor": after_cursor},
                )
            metadata = {
                key: sanitize(value)
                for key, value in raw.items()
                if key not in {"markets", "next_cursor"}
            }
        else:
            raise MalformedPayloadError(
                "Gamma keyset response must be an object or legacy array",
                provider=self.provider,
                endpoint="/markets/keyset",
                details={"actual_type": type(raw).__name__},
            )
        markets = tuple(
            normalize_market(
                item,
                provenance=observation.provenance,
                endpoint="/markets/keyset",
                provider=self.provider,
            )
            for item in market_payloads
        )
        return CursorPage(
            items=markets,
            next_cursor=next_cursor,
            query=query,
            response_metadata=metadata,
            observation=observation,
        )

    def iter_markets(
        self, *, limit: int = 100, filters: Mapping[str, Any] | None = None, max_pages: int = 100
    ) -> tuple[MarketState, ...]:
        items: list[MarketState] = []
        cursor: str | None = None
        for _ in range(max_pages):
            page = self.list_markets(limit=limit, after_cursor=cursor, filters=filters)
            items.extend(page.items)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        else:
            raise LocalValidationError(
                "market pagination exceeded max_pages", details={"max_pages": max_pages}
            )
        return tuple(items)

    def search(
        self, query: str, *, limit: int = 20, page: int = 1, include_profiles: bool = False
    ) -> SearchResult:
        if not query.strip():
            raise LocalValidationError("search query cannot be empty")
        if limit < 1 or limit > self.limits.max_search_results:
            raise LocalValidationError(
                "search limit is outside the configured bound",
                details={"limit": limit, "max": self.limits.max_search_results},
            )
        params = {
            "q": query,
            "limit_per_type": limit,
            "page": page,
            "search_profiles": str(include_profiles).lower(),
        }
        observation = self._get("/public-search", params)
        raw = observation.value
        if not isinstance(raw, Mapping):
            raise MalformedPayloadError(
                "Gamma public-search response must be an object",
                provider=self.provider,
                endpoint="/public-search",
            )
        events_raw = raw.get("events") or []
        tags_raw = raw.get("tags") or []
        profiles_raw = raw.get("profiles") or []
        if not all(isinstance(item, Mapping) for item in (*events_raw, *tags_raw, *profiles_raw)):
            raise MalformedPayloadError(
                "Gamma public-search collections must contain objects",
                provider=self.provider,
                endpoint="/public-search",
            )
        markets: list[MarketState] = []
        for event in events_raw:
            for market in (
                event.get("markets", []) if isinstance(event.get("markets", []), list) else []
            ):
                markets.append(
                    normalize_market(
                        market,
                        provenance=observation.provenance,
                        endpoint="/public-search",
                        provider=self.provider,
                    )
                )
            if self._looks_like_market(event):
                markets.append(
                    normalize_market(
                        event,
                        provenance=observation.provenance,
                        endpoint="/public-search",
                        provider=self.provider,
                    )
                )
        pagination = raw.get("pagination") if isinstance(raw.get("pagination"), Mapping) else {}
        return SearchResult(
            query=query,
            markets=tuple(markets[:limit]),
            events=tuple(sanitize(item) for item in events_raw),
            tags=tuple(sanitize(item) for item in tags_raw),
            profiles=tuple(sanitize(item) for item in profiles_raw),
            pagination=sanitize(pagination),
            observation=observation,
        )

    @staticmethod
    def _looks_like_market(payload: Mapping[str, Any]) -> bool:
        return any(key in payload for key in ("conditionId", "condition_id")) and any(
            key in payload for key in ("clobTokenIds", "clob_token_ids", "tokens")
        )

    def event_by_slug(self, slug: str) -> EventResult:
        if not slug.strip():
            raise LocalValidationError("event slug cannot be empty")
        observation = self._get(f"/events/slug/{slug.strip()}")
        raw = observation.value
        if not isinstance(raw, Mapping):
            raise MalformedPayloadError(
                "Gamma event response must be an object",
                provider=self.provider,
                endpoint="/events/slug/{slug}",
            )
        markets_raw = raw.get("markets") or []
        if not isinstance(markets_raw, list):
            raise MalformedPayloadError(
                "Gamma event markets must be an array",
                provider=self.provider,
                endpoint="/events/slug/{slug}",
            )
        markets = tuple(
            normalize_market(
                item,
                provenance=observation.provenance,
                endpoint="/events/slug/{slug}",
                provider=self.provider,
            )
            for item in markets_raw
        )
        event = {key: sanitize(value) for key, value in raw.items() if key != "markets"}
        return EventResult(event=event, markets=markets, observation=observation)

    def list_tags(self) -> Observation[tuple[Mapping[str, Any], ...]]:
        observation = self._get("/tags")
        if not isinstance(observation.value, list) or not all(
            isinstance(item, Mapping) for item in observation.value
        ):
            raise MalformedPayloadError(
                "Gamma tags response must be an array of objects",
                provider=self.provider,
                endpoint="/tags",
            )
        return Observation(
            value=tuple(sanitize(item) for item in observation.value),
            raw_payload=observation.raw_payload,
            provenance=observation.provenance,
        )


class CLOBAdapter(_Adapter):
    provider = "clob"
    schema_version = CLOB_SCHEMA_VERSION

    def __init__(
        self,
        *,
        transport: JsonTransport | None = None,
        base_url: str = CLOB_BASE,
        limits: ResourceLimits | None = None,
        timeout: float = 10.0,
        clock: callable | None = None,
    ) -> None:
        super().__init__(
            transport=transport, base_url=base_url, limits=limits, timeout=timeout, clock=clock
        )

    def get_book(self, token_id: str) -> Observation[BookSnapshot]:
        if not token_id.strip():
            raise LocalValidationError("token_id cannot be empty")
        observation = self._get("/book", {"token_id": token_id})
        value = normalize_book(
            observation.value,
            provenance=observation.provenance,
            endpoint="/book",
            provider=self.provider,
        )
        return Observation(
            value=value, raw_payload=observation.raw_payload, provenance=observation.provenance
        )

    def get_market_info(self, condition_id: str) -> Observation[MarketState]:
        if not condition_id.strip():
            raise LocalValidationError("condition_id cannot be empty")
        observation = self._get(f"/markets/{condition_id}")
        raw = observation.value
        if not isinstance(raw, Mapping):
            raise MalformedPayloadError(
                "CLOB market response must be an object",
                provider=self.provider,
                endpoint="/markets/{condition_id}",
            )
        adapted = dict(raw)
        adapted.setdefault("condition_id", adapted.get("conditionId"))
        adapted.setdefault("slug", adapted.get("market_slug", adapted.get("marketSlug")))
        adapted.setdefault("question", adapted.get("question", adapted.get("market_slug", "")))
        token_payload = adapted.get("tokens")
        if isinstance(token_payload, str):
            try:
                token_payload = json.loads(token_payload)
            except json.JSONDecodeError as exc:
                raise MalformedPayloadError(
                    "CLOB tokens field is invalid JSON",
                    provider=self.provider,
                    endpoint="/markets/{condition_id}",
                ) from exc
        adapted.setdefault("clobTokenIds", token_payload)
        if isinstance(token_payload, list):
            adapted.setdefault(
                "outcomes",
                [
                    item.get("outcome", "") if isinstance(item, Mapping) else ""
                    for item in token_payload
                ],
            )
        adapted.setdefault("orderMinSize", adapted.get("minimum_order_size"))
        adapted.setdefault("orderPriceMinTickSize", adapted.get("minimum_tick_size"))
        value = normalize_market(
            adapted,
            provenance=observation.provenance,
            endpoint="/markets/{condition_id}",
            provider=self.provider,
        )
        return Observation(
            value=value, raw_payload=observation.raw_payload, provenance=observation.provenance
        )

    def get_fee_rate(self, token_id: str) -> FeeRateResult:
        observation = self._get("/fee-rate", {"token_id": token_id})
        raw = observation.value
        if not isinstance(raw, Mapping):
            raise MalformedPayloadError(
                "CLOB fee-rate response must be an object",
                provider=self.provider,
                endpoint="/fee-rate",
            )
        base_value = raw.get("base_fee", raw.get("fee_rate_bps"))
        base_fee_bps: int | None
        if base_value is None:
            base_fee_bps = None
        else:
            try:
                base_fee_bps = int(decimal(base_value, field="base_fee"))
            except (ValueError, LocalValidationError) as exc:
                raise MalformedPayloadError(
                    "CLOB base_fee is invalid",
                    provider=self.provider,
                    endpoint="/fee-rate",
                    details={"field": "base_fee"},
                ) from exc
        unknown: dict[str, str] = {}
        schedule = normalize_fee_schedule(
            raw, source=self.provider, endpoint="/fee-rate", unknown=unknown
        )
        return FeeRateResult(base_fee_bps=base_fee_bps, schedule=schedule, observation=observation)

    def get_price_history(
        self,
        token_id: str,
        *,
        start_ts: float | None = None,
        end_ts: float | None = None,
        interval: str | None = None,
        fidelity: int | None = None,
    ) -> Observation[tuple[PricePoint, ...]]:
        allowed_intervals = {"max", "all", "1m", "1w", "1d", "6h", "1h"}
        if interval is not None and interval not in allowed_intervals:
            raise LocalValidationError(
                "unsupported price-history interval",
                details={"interval": interval, "allowed": sorted(allowed_intervals)},
            )
        params = {
            "market": token_id,
            "startTs": start_ts,
            "endTs": end_ts,
            "interval": interval,
            "fidelity": fidelity,
        }
        observation = self._get("/prices-history", params)
        points = normalize_price_history(
            observation.value,
            provenance=observation.provenance,
            max_points=self.limits.max_history_points,
            endpoint="/prices-history",
            provider=self.provider,
        )
        return Observation(
            value=points, raw_payload=observation.raw_payload, provenance=observation.provenance
        )


class DataAdapter(_Adapter):
    provider = "data"
    schema_version = "data-public-2026-08"

    def __init__(
        self,
        *,
        transport: JsonTransport | None = None,
        base_url: str = DATA_BASE,
        limits: ResourceLimits | None = None,
        timeout: float = 10.0,
        clock: callable | None = None,
    ) -> None:
        super().__init__(
            transport=transport, base_url=base_url, limits=limits, timeout=timeout, clock=clock
        )

    def public_context(
        self, context_type: str, *, path: str, params: Mapping[str, Any] | None = None
    ) -> Observation[PublicContext]:
        observation = self._get(path, params)
        value = normalize_public_context(
            context_type, observation.value, provenance=observation.provenance
        )
        return Observation(
            value=value, raw_payload=observation.raw_payload, provenance=observation.provenance
        )

    def activity(
        self, *, user: str | None = None, market: str | None = None, limit: int = 100
    ) -> Observation[PublicContext]:
        return self.public_context(
            "activity",
            path="/activity",
            params={"user": user, "market": market, "limit": min(limit, self.limits.max_page_size)},
        )

    def positions(
        self, *, user: str, market: str | None = None, limit: int = 100
    ) -> Observation[PublicContext]:
        return self.public_context(
            "positions",
            path="/positions",
            params={"user": user, "market": market, "limit": min(limit, self.limits.max_page_size)},
        )


class PolymarketClient:
    """Convenience facade retaining the original public-client method names."""

    def __init__(
        self,
        *,
        transport: JsonTransport | None = None,
        limits: ResourceLimits | None = None,
        gamma_base: str = GAMMA_BASE,
        clob_base: str = CLOB_BASE,
        clock: callable | None = None,
    ) -> None:
        self.gamma = GammaAdapter(
            transport=transport, base_url=gamma_base, limits=limits, clock=clock
        )
        self.clob = CLOBAdapter(transport=transport, base_url=clob_base, limits=limits, clock=clock)

    def list_markets(self, *, limit: int = 20, sort_by: str = "volume") -> list[MarketState]:
        filters = (
            {"order": sort_by, "closed": "false"}
            if sort_by in {"volume", "liquidity"}
            else {"closed": "false"}
        )
        return list(self.gamma.list_markets(limit=limit, filters=filters).items)

    def search_markets(self, query: str, *, limit: int = 10) -> list[MarketState]:
        return list(self.gamma.search(query, limit=limit).markets)

    def get_event(self, slug: str) -> Mapping[str, Any]:
        return self.gamma.event_by_slug(slug).event

    def get_market(self, slug_or_id: str) -> MarketState:
        if slug_or_id.startswith("0x"):
            return self.clob.get_market_info(slug_or_id).value
        page = self.gamma.list_markets(limit=100, filters={"slug": slug_or_id})
        if not page.items:
            raise ProviderRejectedError(
                "market was not found in public data",
                provider="gamma",
                endpoint="/markets/keyset",
                details={"identifier": slug_or_id},
            )
        return page.items[0]

    def get_order_book(self, token_id: str) -> BookSnapshot:
        return self.clob.get_book(token_id).value

    def get_fee_rate(self, token_id: str) -> FeeSchedule | None:
        return self.clob.get_fee_rate(token_id).schedule
