"""Offline discovery-to-replay workflow using only repository fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pm_trader.api import GAMMA_BASE, FixtureTransport, GammaAdapter
from pm_trader.models import PaperOrder, SimulationConfig
from pm_trader.recording import Recorder
from pm_trader.serialization import canonical_json
from pm_trader.storage import SQLiteStore
from pm_trader.workflows import ResearchService, load_book_fixture, load_market_fixture

ROOT = Path(__file__).parents[1]


def main() -> None:
    market_fixture = ROOT / "fixtures/gamma_markets_keyset_page1.json"
    book_fixture = ROOT / "fixtures/clob_book.json"
    gamma_payload = json.loads(market_fixture.read_text(encoding="utf-8"))
    transport = FixtureTransport({f"{GAMMA_BASE}/markets/keyset": gamma_payload})
    store = SQLiteStore(":memory:")
    service = ResearchService(
        store,
        gamma=GammaAdapter(transport=transport, clock=lambda: "2026-08-26T20:00:20Z"),
    )
    market = load_market_fixture(market_fixture)
    book = load_book_fixture(book_fixture)
    token = market.token_for("Yes")
    order = PaperOrder(
        order_id="example-order-1",
        market_id=market.condition_id,
        asset_id=token.token_id,
        side="buy",
        price="0.67",
        quantity="40",
        time_in_force="fak",
    )
    config = SimulationConfig(starting_cash="1000", seed=7)
    with TemporaryDirectory(prefix="pm-trader-example-") as directory:
        dataset_path = Path(directory) / "books.jsonl"
        recorder = Recorder(store, dataset_id="example-recording")
        recorder.record_book(book)
        recorder.export_jsonl(dataset_path)
        discovery = service.discover(limit=1)
        simulation = service.simulate(
            market=market,
            book=book,
            orders=(order,),
            config=config,
            dataset_id="example-simulation",
        )
        replay = service.replay(
            dataset_path=dataset_path, market=market, orders=(order,), config=config
        )
        print(
            canonical_json(
                {
                    "discovery": {
                        "market_count": len(discovery["result"]["markets"]),
                        "next_cursor": discovery["result"]["next_cursor"],
                    },
                    "inspection": {
                        "market": market.slug,
                        "book_asset": book.asset_id,
                        "book_depth": len(book.bids) + len(book.asks),
                    },
                    "simulation": {
                        "run_id": simulation["result"]["run_id"],
                        "fills": simulation["result"]["report"]["fills"],
                        "checksum": simulation["result"]["report"]["checksum"],
                    },
                    "replay": {
                        "run_id": replay["result"]["run_id"],
                        "fills": replay["result"]["report"]["fills"],
                        "checksum": replay["result"]["report"]["checksum"],
                    },
                }
            )
        )
    store.close()


if __name__ == "__main__":
    main()
