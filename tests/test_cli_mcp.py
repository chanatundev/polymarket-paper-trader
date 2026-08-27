from __future__ import annotations

import json
from pathlib import Path

from pm_trader.cli import run
from pm_trader.mcp_server import dispatch_tool, handle_request

ROOT = Path(__file__).parents[1]


def test_cli_simulation_is_structured_and_paper_only(tmp_path: Path):
    code, payload = run(
        [
            "--data-dir",
            str(tmp_path / "db"),
            "simulate",
            "--market-fixture",
            str(ROOT / "fixtures/gamma_markets_keyset_page1.json"),
            "--book-fixture",
            str(ROOT / "fixtures/clob_book.json"),
            "--price",
            "0.67",
            "--size",
            "40",
            "--tif",
            "fak",
        ]
    )
    assert code == 0
    assert payload["ok"] is True
    assert payload["result"]["report"]["assumptions"]["paper_only"] is True
    assert payload["result"]["report"]["uncertainty"]["paper_fills"] == "model_output"
    assert payload["result"]["report"]["cash"] == "9973.00367"
    assert payload["errors"] == []


def test_mcp_tools_list_and_explain():
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {
        "discover_markets",
        "simulate",
        "replay",
        "portfolio_report",
        "explain_decision",
    } <= names
    explained = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "explain_decision",
                "arguments": {
                    "status": "rejected",
                    "reason_code": "STALE_BOOK",
                    "reasons": ["book_stale"],
                },
            },
        }
    )
    text = explained["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["result"]["reason_code"] == "STALE_BOOK"


def test_mcp_rejects_non_object_requests_and_params():
    assert handle_request([])["error"]["code"] == -32600
    response = handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": []})
    assert response["error"]["code"] == -32602


def test_cli_market_alias_and_mcp_portfolio_report(tmp_path: Path):
    code, listed = run(
        [
            "--data-dir",
            str(tmp_path / "alias-db"),
            "markets",
            "list",
            "--fixture",
            str(ROOT / "fixtures/gamma_markets_keyset_page1.json"),
        ]
    )
    assert code == 0
    assert listed["result"]["markets"][0]["slug"] == "will-the-example-event-happen-by-2030"

    data_dir = tmp_path / "db"
    code, simulated = run(
        [
            "--data-dir",
            str(data_dir),
            "simulate",
            "--market-fixture",
            str(ROOT / "fixtures/gamma_markets_keyset_page1.json"),
            "--book-fixture",
            str(ROOT / "fixtures/clob_book.json"),
            "--price",
            "0.66",
            "--size",
            "10",
        ]
    )
    assert code == 0
    run_id = simulated["result"]["run_id"]
    report = dispatch_tool("portfolio_report", {"run_id": run_id, "data_dir": str(data_dir)})
    assert report["ok"] is True
    assert report["result"]["checksum"] == simulated["result"]["report"]["checksum"]


def test_safety_command_reports_no_live_capabilities(tmp_path: Path):
    code, payload = run(["--data-dir", str(tmp_path / "safety-db"), "safety"])
    assert code == 0
    assert payload["result"] == {
        "mode": "paper_only",
        "live_writes": False,
        "signing": False,
        "cancellation": False,
        "redemption": False,
        "fund_movement": False,
    }
