"""Versioned canonical records consumed by adapters, simulation, and replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from .precision import decimal


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    INCONSISTENT = "inconsistent"
    UNKNOWN = "unknown"


class GateStatus(str, Enum):
    ELIGIBLE = "eligible"
    REJECTED = "rejected"
    STALE = "stale"
    UNKNOWN = "unknown"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TimeInForce(str, Enum):
    GTC = "gtc"
    GTD = "gtd"
    FAK = "fak"
    FOK = "fok"


class OrderStatus(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    UNFILLED = "unfilled"


class MakerTaker(str, Enum):
    MAKER = "maker"
    TAKER = "taker"


class LedgerKind(str, Enum):
    INITIAL_CASH = "initial_cash"
    ORDER = "order"
    FILL = "fill"
    FEE = "fee"
    REBATE_ESTIMATE = "rebate_estimate"
    RESOLUTION = "resolution"
    MARK = "mark"


@dataclass(frozen=True)
class Provenance:
    observation_id: str
    source: str
    endpoint: str | None
    retrieved_at: str
    received_at: str
    schema_version: str
    request_metadata: Mapping[str, Any] = field(default_factory=dict)
    payload_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source": self.source,
            "endpoint": self.endpoint,
            "retrieved_at": self.retrieved_at,
            "received_at": self.received_at,
            "schema_version": self.schema_version,
            "request_metadata": dict(self.request_metadata),
            "payload_sha256": self.payload_sha256,
        }


@dataclass(frozen=True)
class UnknownValue:
    reason: str
    source_field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"unknown": True, "reason": self.reason, "source_field": self.source_field}


@dataclass(frozen=True)
class FeeSchedule:
    """A declared match-time fee schedule.

    ``rate`` is a fraction (for example ``0.07``), not basis points. The
    default dynamic formula is ``C * rate * (p * (1-p)) ** exponent``.
    """

    rate: Decimal
    exponent: Decimal = Decimal("1")
    taker_only: bool = True
    rebate_rate: Decimal | None = None
    version: str = "clob-v2-dynamic"
    formula: str = "shares_rate_price_product_power"
    source: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate", decimal(self.rate, field="fee_schedule.rate"))
        object.__setattr__(self, "exponent", decimal(self.exponent, field="fee_schedule.exponent"))
        if self.rebate_rate is not None:
            object.__setattr__(self, "rebate_rate", decimal(self.rebate_rate, field="fee_schedule.rebate_rate"))


@dataclass(frozen=True)
class MarketToken:
    token_id: str
    outcome: str


@dataclass(frozen=True)
class MarketState:
    condition_id: str
    slug: str
    question: str
    outcomes: tuple[str, ...]
    tokens: tuple[MarketToken, ...]
    event_id: str | None = None
    event_slug: str | None = None
    description: str | None = None
    outcome_prices: tuple[Decimal | None, ...] = ()
    active: bool | None = None
    closed: bool | None = None
    accepting_orders: bool | None = None
    enable_order_book: bool | None = None
    min_order_size: Decimal | None = None
    tick_size: Decimal | None = None
    neg_risk: bool | None = None
    fees_enabled: bool | None = None
    fee_schedule: FeeSchedule | None = None
    category: str | None = None
    provider_timestamp: str | None = None
    observed_at: str | None = None
    freshness: Freshness = Freshness.UNKNOWN
    unknown_reasons: Mapping[str, str] = field(default_factory=dict)
    unknown_fields: Mapping[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        converted_tokens: list[MarketToken] = []
        for token in self.tokens:
            if isinstance(token, MarketToken):
                converted_tokens.append(token)
            elif isinstance(token, Mapping):
                converted_tokens.append(MarketToken(token_id=str(token.get("token_id", token.get("tokenId", ""))), outcome=str(token.get("outcome", token.get("name", "")))))
            else:
                raise TypeError("market tokens must be MarketToken or mapping values")
        object.__setattr__(self, "tokens", tuple(converted_tokens))
        object.__setattr__(self, "outcome_prices", tuple(None if value is None else decimal(value, field="outcome_price") for value in self.outcome_prices))
        if isinstance(self.freshness, str):
            object.__setattr__(self, "freshness", Freshness(self.freshness))

    def token_for(self, outcome: str) -> MarketToken:
        normalized = outcome.casefold()
        for token in self.tokens:
            if token.outcome.casefold() == normalized or token.token_id == outcome:
                return token
        raise ValueError(f"unknown outcome or token: {outcome}")

    def price_for(self, outcome: str) -> Decimal | None:
        token = self.token_for(outcome)
        for index, candidate in enumerate(self.tokens):
            if candidate.token_id == token.token_id:
                return self.outcome_prices[index] if index < len(self.outcome_prices) else None
        return None


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    size: Decimal
    order_id: str | None = None
    timestamp: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", decimal(self.price, field="book.price"))
        object.__setattr__(self, "size", decimal(self.size, field="book.size"))

    @property
    def cost(self) -> Decimal:
        return self.price * self.size


@dataclass(frozen=True)
class BookSnapshot:
    market_id: str | None
    asset_id: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    provider_timestamp: str
    received_at: str
    book_hash: str | None = None
    sequence: int | None = None
    min_order_size: Decimal | None = None
    tick_size: Decimal | None = None
    neg_risk: bool | None = None
    last_trade_price: Decimal | None = None
    freshness: Freshness = Freshness.UNKNOWN
    continuity: str = "unknown"
    provenance: Provenance | None = None
    unknown_reasons: Mapping[str, str] = field(default_factory=dict)
    unknown_fields: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "bids", tuple(self.bids))
        object.__setattr__(self, "asks", tuple(self.asks))
        if isinstance(self.freshness, str):
            object.__setattr__(self, "freshness", Freshness(self.freshness))

    def best_bid(self) -> BookLevel | None:
        return max(self.bids, key=lambda level: level.price, default=None)

    def best_ask(self) -> BookLevel | None:
        return min(self.asks, key=lambda level: level.price, default=None)


@dataclass(frozen=True)
class MarketEvent:
    event_type: str
    market_id: str | None
    asset_id: str | None
    provider_timestamp: str | None
    received_at: str
    sequence: int | None
    payload: Mapping[str, Any]
    executable: bool
    provenance: Provenance | None = None
    gap_detected: bool = False
    resynchronized: bool = False


@dataclass(frozen=True)
class PricePoint:
    timestamp: str
    price: Decimal
    provenance: Provenance | None = None


@dataclass(frozen=True)
class PublicContext:
    context_type: str
    records: tuple[Mapping[str, Any], ...]
    as_of: str | None
    executable: bool = False
    provenance: Provenance | None = None
    unknown_reasons: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    market_id: str
    asset_id: str
    side: str
    price: Decimal | None
    quantity: Decimal
    time_in_force: str = TimeInForce.GTC.value
    post_only: bool = False
    submitted_at: str | None = None
    expires_at: str | None = None
    latency_ms: int = 0
    queue_policy: str = "front_of_queue"
    queue_ahead: Decimal = Decimal("0")
    model_version: str = "matcher-v1"
    status: str = OrderStatus.NEW.value
    remaining_quantity: Decimal | None = None
    size: Decimal | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.price is not None:
            object.__setattr__(self, "price", decimal(self.price, field="order.price"))
        object.__setattr__(self, "quantity", decimal(self.quantity, field="order.quantity"))
        if self.size is not None:
            object.__setattr__(self, "size", decimal(self.size, field="order.size"))
        object.__setattr__(self, "queue_ahead", decimal(self.queue_ahead, field="order.queue_ahead"))

    def effective_quantity(self) -> Decimal:
        if self.size is not None:
            return self.size
        return self.quantity

    def with_status(self, status: str, *, remaining: Decimal | None = None) -> "PaperOrder":
        return PaperOrder(
            order_id=self.order_id,
            market_id=self.market_id,
            asset_id=self.asset_id,
            side=self.side,
            price=self.price,
            quantity=self.quantity,
            time_in_force=self.time_in_force,
            post_only=self.post_only,
            submitted_at=self.submitted_at,
            expires_at=self.expires_at,
            latency_ms=self.latency_ms,
            queue_policy=self.queue_policy,
            queue_ahead=self.queue_ahead,
            model_version=self.model_version,
            status=status,
            remaining_quantity=remaining,
            size=self.size,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    asset_id: str
    side: str
    quantity: Decimal
    price: Decimal
    notional: Decimal
    maker_taker: str
    fee: Decimal | None
    rebate_estimate: Decimal | None
    observation_id: str | None
    provider_timestamp: str | None
    received_at: str | None
    model_version: str
    queue_policy: str
    assumptions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("quantity", "price", "notional"):
            object.__setattr__(self, name, decimal(getattr(self, name), field=f"fill.{name}"))
        if self.fee is not None:
            object.__setattr__(self, "fee", decimal(self.fee, field="fill.fee"))
        if self.rebate_estimate is not None:
            object.__setattr__(self, "rebate_estimate", decimal(self.rebate_estimate, field="fill.rebate_estimate"))

    @property
    def shares(self) -> Decimal:
        return self.quantity

    @property
    def cost(self) -> Decimal:
        return self.notional


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    run_id: str
    sequence: int
    kind: str
    asset_id: str | None
    order_id: str | None
    fill_id: str | None
    cash_delta: Decimal
    position_delta: Decimal
    notional: Decimal
    fee: Decimal | None
    rebate_estimate: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    observation_id: str | None
    assumptions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("cash_delta", "position_delta", "notional"):
            object.__setattr__(self, name, decimal(getattr(self, name), field=f"ledger.{name}"))
        for name in ("fee", "rebate_estimate", "realized_pnl", "unrealized_pnl"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, decimal(value, field=f"ledger.{name}"))


@dataclass(frozen=True)
class Decision:
    status: str
    reason_code: str | None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    freshness: Freshness = Freshness.UNKNOWN
    provenance_ids: tuple[str, ...] = ()
    assumptions: Mapping[str, Any] = field(default_factory=dict)
    checks: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "freshness": self.freshness.value,
            "provenance_ids": list(self.provenance_ids),
            "assumptions": dict(self.assumptions),
            "checks": dict(self.checks),
        }


@dataclass(frozen=True)
class SimulationConfig:
    starting_cash: Decimal = Decimal("10000")
    stale_tolerance_seconds: int = 30
    latency_ms: int = 0
    queue_policy: str = "front_of_queue"
    seed: int = 0
    clock: str = "observation_time"
    schema_version: str = "canonical-v1"
    model_versions: Mapping[str, str] = field(
        default_factory=lambda: {"matcher": "matcher-v1", "fees": "fees-v2", "ledger": "ledger-v1"}
    )
    fee_model_version: str = "clob-v2-dynamic"
    max_events: int = 100_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "starting_cash", decimal(self.starting_cash, field="starting_cash"))


@dataclass(frozen=True)
class MatchResult:
    order: PaperOrder
    fills: tuple[Fill, ...]
    decision: Decision
    remaining_quantity: Decimal


@dataclass(frozen=True)
class FeeResult:
    status: str
    amount: Decimal | None
    reason: str | None
    rate: Decimal | None
    exponent: Decimal | None
    maker_taker: str
    rounded_places: int
    formula: str | None


@dataclass(frozen=True)
class RebateEstimate:
    status: str
    amount: Decimal | None
    eligible: bool | None
    confidence: str
    coverage: str
    assumptions: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True)
class SimulationReport:
    run_id: str
    starting_cash: Decimal
    cash: Decimal
    positions: Mapping[str, Decimal]
    exposure: Decimal
    fees: Decimal
    rebate_estimates: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fills: int
    orders: int
    warnings: tuple[str, ...]
    assumptions: Mapping[str, Any]
    data_coverage: Mapping[str, Any]
    checksum: str


# Backward-compatible small records from the original package shape. They are
# intentionally detached from the canonical model and contain no live actions.
@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)


@dataclass
class Market:
    condition_id: str
    slug: str
    question: str
    description: str
    outcomes: list[str]
    outcome_prices: list[float]
    tokens: list[dict[str, Any]]
    active: bool
    closed: bool
    volume: float = 0.0
    liquidity: float = 0.0
    end_date: str = ""
    fee_rate_bps: int = 0
    tick_size: float = 0.01

    def get_token_id(self, outcome: str) -> str:
        normalized = outcome.casefold()
        for token in self.tokens:
            if str(token.get("outcome", "")).casefold() == normalized:
                return str(token["token_id"])
        raise ValueError(f"No token found for outcome {outcome!r}")

    @property
    def yes_token_id(self) -> str:
        return self.get_token_id("yes")

    @property
    def no_token_id(self) -> str:
        return self.get_token_id("no")

    @property
    def yes_price(self) -> float:
        for index, outcome in enumerate(self.outcomes):
            if outcome.casefold() == "yes" and index < len(self.outcome_prices):
                return self.outcome_prices[index]
        return 0.0

    @property
    def no_price(self) -> float:
        for index, outcome in enumerate(self.outcomes):
            if outcome.casefold() == "no" and index < len(self.outcome_prices):
                return self.outcome_prices[index]
        return 0.0
