from __future__ import annotations

import json
from pathlib import Path

import pytest

from pm_trader.cli import main, run
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
    data_dir = tmp_path / "safety-db"
    code, payload = run(["--data-dir", str(data_dir), "safety"])
    assert code == 0
    assert payload["result"] == {
        "mode": "paper_only",
        "live_writes": False,
        "signing": False,
        "cancellation": False,
        "redemption": False,
        "fund_movement": False,
    }
    assert not data_dir.exists()


def _keyset_page(markets: list[dict]) -> dict:
    return {"markets": markets, "next_cursor": "fixture-cursor-page-2"}


def _example_market(**overrides) -> dict:
    market = json.loads((ROOT / "fixtures/gamma_markets_keyset_page1.json").read_text())["markets"][
        0
    ]
    market.update(overrides)
    return market


def test_discover_page_of_100_is_a_compact_scan(tmp_path: Path, capsys):
    markets = [
        _example_market(
            conditionId=f"0x{index:064x}",
            slug=f"will-event-{index}-happen-before-the-deadline",
            question=f"Will event {index} happen before the stated deadline in this example market?",
        )
        for index in range(100)
    ]
    fixture_path = tmp_path / "page100.json"
    fixture_path.write_text(json.dumps(_keyset_page(markets)), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--data-dir",
                str(tmp_path / "db"),
                "discover",
                "--fixture",
                str(fixture_path),
                "--limit",
                "100",
            ]
        )
    assert caught.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert len(payload["result"]["markets"]) == 100
    first = payload["result"]["markets"][0]
    assert first["condition_id"] == f"0x{0:064x}"
    assert first["end_time"] == "2030-12-31T23:59:59Z"
    assert first["slug"].startswith("will-event-0-")
    assert "unknown_fields" not in first
    assert "fee_schedule" not in first
    assert "tokens" not in first
    assert payload["result"]["next_cursor"] == "fixture-cursor-page-2"


def test_discover_keeps_a_fitting_prefix_when_one_market_is_too_large(tmp_path: Path, capsys):
    markets = [
        _example_market(conditionId="0xsmall", slug="small-market", question="small"),
        _example_market(conditionId="0xfat", slug="fat-market", question="Q" * 70_000),
    ]
    fixture_path = tmp_path / "mixed.json"
    fixture_path.write_text(json.dumps(_keyset_page(markets)), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--data-dir",
                str(tmp_path / "db"),
                "discover",
                "--fixture",
                str(fixture_path),
                "--limit",
                "2",
            ]
        )
    assert caught.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert [item["condition_id"] for item in payload["result"]["markets"]] == ["0xsmall"]
    assert payload["result"]["next_cursor"] is None
    assert payload["warnings"]
    assert "smaller --limit" in payload["warnings"][0]


def test_discover_exits_nonzero_when_nothing_fits(tmp_path: Path, capsys):
    fixture_path = tmp_path / "fat.json"
    fixture_path.write_text(
        json.dumps(
            _keyset_page(
                [_example_market(conditionId="0xfat", slug="fat-market", question="Q" * 70_000)]
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--data-dir",
                str(tmp_path / "db"),
                "discover",
                "--fixture",
                str(fixture_path),
                "--limit",
                "1",
            ]
        )
    assert caught.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["result"] is None
    assert payload["errors"][0]["code"] == "RESPONSE_SIZE_LIMIT"
