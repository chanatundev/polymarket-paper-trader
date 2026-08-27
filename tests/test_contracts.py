from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from pm_trader.api import (
    CLOB_BASE,
    GAMMA_BASE,
    CLOBAdapter,
    DataAdapter,
    FixtureTransport,
    GammaAdapter,
    TransportResponse,
)
from pm_trader.errors import (
    LocalValidationError,
    MalformedPayloadError,
    NetworkProviderError,
    ProviderRejectedError,
    RateLimitError,
)
from pm_trader.normalizers import normalize_book, normalize_market

ROOT = Path(__file__).parents[1]


def fixture(name: str):
    return json.loads((ROOT / "fixtures" / name).read_text())


def test_current_market_normalization_preserves_required_fields_and_unknowns():
    value = fixture("gamma_markets_keyset_page1.json")["markets"][0]
    market = normalize_market(value)
    assert market.condition_id == "0xgeneral-event-001"
    assert market.tokens[0].token_id == "token-general-yes"
    assert market.outcome_prices[0] == Decimal("0.64")
    assert market.enable_order_book is True
    assert market.accepting_orders is True
    assert market.fee_schedule.rate == Decimal("0.05")
    assert market.fee_schedule.rebate_rate == Decimal("0.25")
    assert market.unknown_fields["fixtureVersion"] == "gamma-2026-08-26-v1"


@pytest.mark.parametrize("field", ["conditionId", "slug", "question", "outcomes", "clobTokenIds"])
def test_required_market_fields_fail_closed(field):
    value = fixture("gamma_markets_keyset_page1.json")["markets"][0]
    value.pop(field)
    with pytest.raises(MalformedPayloadError):
        normalize_market(value)


def test_invalid_market_json_is_malformed():
    value = fixture("gamma_markets_keyset_page1.json")["markets"][0]
    value["outcomes"] = "not-json"
    with pytest.raises(MalformedPayloadError):
        normalize_market(value)


def test_current_book_normalization_preserves_identity_and_freshness_fields():
    book = normalize_book(fixture("clob_book.json"))
    assert book.market_id == "0xgeneral-event-001"
    assert book.asset_id == "token-general-yes"
    assert book.book_hash == "book-hash-general-001"
    assert book.min_order_size == Decimal(5)
    assert book.tick_size == Decimal("0.01")
    assert book.neg_risk is False
    assert book.last_trade_price == Decimal("0.65")
    assert book.bids[0].price == Decimal("0.62")


def test_invalid_book_level_is_malformed():
    value = fixture("clob_book.json")
    value["asks"][0]["price"] = "1.2"
    with pytest.raises(MalformedPayloadError):
        normalize_book(value)


def test_keyset_pagination_uses_cursor_and_rejects_offset():
    transport = FixtureTransport(
        {
            f"{GAMMA_BASE}/markets/keyset": fixture("gamma_markets_keyset_page1.json"),
        }
    )
    adapter = GammaAdapter(transport=transport, clock=lambda: "2026-08-26T20:00:20Z")
    page = adapter.list_markets(limit=1)
    assert page.next_cursor == "fixture-cursor-page-2"
    assert page.query["limit"] == 1
    assert transport.requests[0][1]["ascending"] == "true"
    transport.responses[f"{GAMMA_BASE}/markets/keyset"] = fixture("gamma_markets_keyset_page2.json")
    final_page = adapter.list_markets(limit=1, after_cursor=page.next_cursor)
    assert final_page.next_cursor is None
    assert final_page.items[0].condition_id == "0xgeneral-event-002"
    assert transport.requests[1][1]["after_cursor"] == "fixture-cursor-page-2"
    with pytest.raises(LocalValidationError):
        adapter.list_markets(filters={"offset": 1})


def test_event_search_and_fee_routes_are_current():
    search_transport = FixtureTransport(
        {f"{GAMMA_BASE}/public-search": fixture("gamma_public_search.json")}
    )
    search = GammaAdapter(transport=search_transport).search("example")
    assert search.markets[0].condition_id == "0xgeneral-event-001"
    assert search.pagination["hasMore"] is False
    assert search_transport.requests[0][0].endswith("/public-search")

    fee_transport = FixtureTransport({f"{CLOB_BASE}/fee-rate": fixture("clob_fee_rate.json")})
    fee = CLOBAdapter(transport=fee_transport).get_fee_rate("token-general-yes")
    assert fee.base_fee_bps == 500
    assert fee.schedule.version == "legacy-base-fee-bps"


def test_event_by_slug_uses_versioned_slug_route_and_clob_market_info_normalizes():
    event_transport = FixtureTransport(
        {f"{GAMMA_BASE}/events/slug/example-general-event": fixture("gamma_event_by_slug.json")}
    )
    event = GammaAdapter(transport=event_transport).event_by_slug("example-general-event")
    assert event.event["id"] == "event-general-001"
    assert event.markets[0].event_slug == "example-general-event"
    assert event_transport.requests[0][0].endswith("/events/slug/example-general-event")

    clob_transport = FixtureTransport(
        {f"{CLOB_BASE}/markets/0xgeneral-event-001": fixture("clob_market_info.json")}
    )
    info = CLOBAdapter(transport=clob_transport).get_market_info("0xgeneral-event-001")
    assert info.value.tick_size == Decimal("0.01")
    assert info.value.tokens[1].token_id == "token-general-no"


def test_price_history_and_public_context_are_bounded_and_non_executable():
    history_transport = FixtureTransport(
        {f"{CLOB_BASE}/prices-history": fixture("clob_price_history.json")}
    )
    history = CLOBAdapter(transport=history_transport).get_price_history(
        "token-general-yes", interval="1h", fidelity=60
    )
    assert len(history.value) == 3
    assert history.value[0].price == Decimal("0.61")
    assert history_transport.requests[0][1]["market"] == "token-general-yes"

    data_transport = FixtureTransport(
        {
            "https://data.example/activity": {
                "activity": [{"type": "trade", "user": "0xwallet", "size": "1"}]
            }
        }
    )
    context = DataAdapter(transport=data_transport, base_url="https://data.example").public_context(
        "activity", path="/activity"
    )
    assert context.value.executable is False
    assert context.value.records[0]["user"] == "[REDACTED]"


def test_http_429_is_distinguishable():
    transport = FixtureTransport(
        {f"{GAMMA_BASE}/markets/keyset": TransportResponse(429, {"message": "slow down"})}
    )
    with pytest.raises(RateLimitError):
        GammaAdapter(transport=transport).list_markets()


def test_http_network_and_provider_rejection_are_distinguishable():
    with pytest.raises(NetworkProviderError):
        GammaAdapter(transport=FixtureTransport({})).list_markets()

    rejected = FixtureTransport(
        {f"{GAMMA_BASE}/markets/keyset": TransportResponse(403, {"message": "forbidden"})}
    )
    with pytest.raises(ProviderRejectedError):
        GammaAdapter(transport=rejected).list_markets()
