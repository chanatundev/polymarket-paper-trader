from __future__ import annotations

import json
from pathlib import Path

from pm_trader.cli import run
from pm_trader.mcp_server import handle_request


ROOT = Path(__file__).parents[1]


def test_cli_simulation_is_structured_and_paper_only(tmp_path: Path):
    code, payload = run([
        "--data-dir", str(tmp_path / "db"), "simulate",
        "--market-fixture", str(ROOT / "fixtures/gamma_markets_keyset_page1.json"),
        "--book-fixture", str(ROOT / "fixtures/clob_book.json"),
        "--price", "0.67", "--size", "40", "--tif", "fak",
    ])
    assert code == 0
    assert payload["ok"] is True
    assert payload["result"]["report"]["assumptions"]["paper_only"] is True
    assert payload["result"]["report"]["cash"] == "9973.00367"
    assert payload["errors"] == []


def test_mcp_tools_list_and_explain():
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"discover_markets", "simulate", "replay", "explain_decision"} <= names
    explained = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "explain_decision", "arguments": {"status": "rejected", "reason_code": "STALE_BOOK", "reasons": ["book_stale"]}}})
    text = explained["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["result"]["reason_code"] == "STALE_BOOK"
