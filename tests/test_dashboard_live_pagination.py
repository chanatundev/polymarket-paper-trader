"""Regression: the browser's recent-trades table must stay gap-free and duplicate-free
while fills arrive after the user has loaded older pages.

The browser calls the pure reducer in ``dashboard/live.js`` (``applySnapshot`` on every
refresh, ``applyOlderPage`` on every "load older" click). This test runs that same
reducer in Node (no npm dependency) and feeds it payloads from the real dashboard HTTP
server. No second implementation of the client logic lives in Python.
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from test_history_gui import _seed_legacy

LIVE_JS = Path(__file__).parents[1] / "dashboard" / "live.js"
ID_PATTERN = re.compile(r'data-history-id="([^"]*)"')
CURSOR_PATTERN = re.compile(r'data-cursor="([^"]*)"')

REDUCER_SCRIPT = """
const live = require(process.argv[1]);
const op = process.argv[2];
const state = JSON.parse(process.argv[3]);
const payload = JSON.parse(process.argv[4]);
const next = op === "snapshot"
  ? live.applySnapshot(state, payload)
  : live.applyOlderPage(state, payload);
process.stdout.write(JSON.stringify(next));
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js is required to exercise dashboard/live.js; install Node.js")
    return node


def _reduce(op: str, state: dict, payload: dict) -> dict:
    completed = subprocess.run(
        [_node(), "-e", REDUCER_SCRIPT, str(LIVE_JS), op, json.dumps(state), json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(completed.stdout)


class _Browser:
    """Drives the production reducer with real server responses."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.state = {
            "displayed": [],
            "olderLoaded": False,
            "cursor": None,
            "total": None,
            "marker": None,
        }

    def _get(self, path: str) -> dict:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
            response = connection.getresponse()
            assert response.status == 200, (path, response.status)
            return json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    @property
    def displayed(self) -> list[str]:
        return self.state["displayed"]

    def refresh(self) -> None:
        snapshot = self._get("/snapshot")
        match = CURSOR_PATTERN.search(snapshot["transaction_more"])
        self.state = _reduce(
            "snapshot",
            self.state,
            {
                "ids": ID_PATTERN.findall(snapshot["transactions"]),
                "cursor": match.group(1) if match else None,
                "total": snapshot["transaction_total"],
                "marker": snapshot["transaction_marker"],
            },
        )

    def load_older(self) -> None:
        requested = self.state["cursor"]
        assert requested is not None
        page = self._get(f"/transactions?cursor={requested}")
        assert "error" not in page, page
        next_state = _reduce(
            "older",
            self.state,
            {
                "ids": ID_PATTERN.findall(page["rows"]),
                "requestedCursor": requested,
                "nextCursor": page["next_cursor"],
                "total": page["total"],
                "marker": page["transaction_marker"],
            },
        )
        if next_state.get("stale"):
            # A fill landed while the page was in flight; the browser rebuilds from the server.
            self.refresh()
            return
        self.state = next_state

    def load_all(self) -> None:
        while self.state["cursor"] is not None:
            self.load_older()


@pytest.fixture
def served_store(tmp_path: Path, monkeypatch):
    import dashboard.app as app
    from dashboard.app import create_dashboard_server

    monkeypatch.setattr(app, "TRANSACTION_PAGE_SIZE", 10)
    data_dir = tmp_path / "paging"
    data_dir.mkdir()
    for index in range(25):
        _seed_legacy(data_dir, created_at=f"2026-08-26T20:{index:02d}:00Z")
    server = create_dashboard_server(data_dir, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield data_dir, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _store_order(data_dir: Path) -> list[str]:
    from pm_trader.history import HistoryQuery, TradeHistoryReader

    reader = TradeHistoryReader.open(data_dir)
    try:
        page = reader.list_history(HistoryQuery(limit=100))
        return [record["history_id"] for record in page.records]
    finally:
        reader.close()


def test_fill_inserted_inside_loaded_range_is_not_skipped(served_store):
    # Regression: a fill sorting into rows already loaded (here an equal-timestamp
    # tie-break beyond the 10-row snapshot window) used to be missed while later
    # "load older" pages advanced past it.
    data_dir, port = served_store
    browser = _Browser(port)
    browser.refresh()
    browser.load_older()
    assert len(browser.displayed) == 20

    _seed_legacy(data_dir, created_at="2026-08-26T20:12:00Z")
    browser.refresh()
    assert browser.displayed == _store_order(data_dir)[:10]
    browser.load_all()

    assert len(browser.displayed) == len(set(browser.displayed))
    assert browser.displayed == _store_order(data_dir)


def test_fill_at_top_after_older_pages_rebuilds_without_gaps(served_store):
    # Prepending above loaded older pages would shift the server offset cursor and skip
    # rows on the next page, so any change to the store resets the table to the server.
    data_dir, port = served_store
    browser = _Browser(port)
    browser.refresh()
    browser.load_older()

    _seed_legacy(data_dir, created_at="2026-08-26T21:00:00Z")
    browser.refresh()
    assert browser.state["olderLoaded"] is False
    assert browser.displayed == _store_order(data_dir)[:10]
    browser.load_all()

    assert len(browser.displayed) == len(set(browser.displayed))
    assert browser.displayed == _store_order(data_dir)


def test_refresh_without_new_fills_keeps_loaded_older_pages_and_cursor(served_store):
    data_dir, port = served_store
    browser = _Browser(port)
    browser.refresh()
    browser.load_older()
    before = dict(browser.state)

    browser.refresh()
    assert browser.state == before
    browser.load_all()
    assert browser.displayed == _store_order(data_dir)


def test_burst_larger_than_the_recent_window_resets_without_losing_rows(served_store):
    data_dir, port = served_store
    browser = _Browser(port)
    browser.refresh()
    browser.load_older()

    for index in range(12):
        _seed_legacy(data_dir, created_at=f"2026-08-26T21:{index:02d}:00Z")
    browser.refresh()
    assert browser.state["olderLoaded"] is False
    assert browser.displayed == _store_order(data_dir)[:10]
    browser.load_all()

    assert len(browser.displayed) == len(set(browser.displayed))
    assert browser.displayed == _store_order(data_dir)


def test_page_fetched_before_a_fill_is_discarded_as_stale(served_store):
    data_dir, port = served_store
    browser = _Browser(port)
    browser.refresh()
    cursor = browser.state["cursor"]
    page = browser._get(f"/transactions?cursor={cursor}")

    # The page is in flight when a fill lands and the table rebuilds from the server.
    _seed_legacy(data_dir, created_at="2026-08-26T21:00:00Z")
    browser.refresh()
    assert browser.state["total"] == page["total"] + 1
    state = _reduce(
        "older",
        browser.state,
        {
            "ids": ID_PATTERN.findall(page["rows"]),
            "requestedCursor": cursor,
            "nextCursor": page["next_cursor"],
            "total": page["total"],
            "marker": page["transaction_marker"],
        },
    )
    assert state["stale"] is True
    assert state["displayed"] == browser.displayed
    assert state["cursor"] == cursor


def test_change_marker_resets_loaded_pages_when_scan_total_is_capped():
    state = {
        "displayed": ["newest", "older-page-first", "older-page-last"],
        "olderLoaded": True,
        "cursor": "MTAw",
        "total": 100_000,
        "marker": "store-before-fill",
    }
    refreshed = _reduce(
        "snapshot",
        state,
        {
            "ids": ["newest", "late-fill-now-in-window"],
            "cursor": "MTAw",
            "total": 100_000,
            "marker": "store-after-fill",
        },
    )

    assert refreshed["olderLoaded"] is False
    assert refreshed["displayed"] == ["newest", "late-fill-now-in-window"]
    assert refreshed["marker"] == "store-after-fill"

    page = _reduce(
        "older",
        state,
        {
            "ids": ["stale-offset-page"],
            "requestedCursor": state["cursor"],
            "nextCursor": None,
            "total": 100_000,
            "marker": "store-after-fill",
        },
    )
    assert page["stale"] is True


def test_missing_node_is_a_hard_test_prerequisite(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="Node.js is required"):
        _node()
