from __future__ import annotations

import json
from pathlib import Path

import pytest

from pm_trader.workflows import load_book_fixture, load_market_fixture


ROOT = Path(__file__).parents[1]


@pytest.fixture
def market():
    return load_market_fixture(ROOT / "fixtures/gamma_markets_keyset_page1.json")


@pytest.fixture
def book():
    return load_book_fixture(ROOT / "fixtures/clob_book.json")
