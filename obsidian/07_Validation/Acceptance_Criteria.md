---
type: acceptance-criteria
project: polymarket-paper-trader
status: phase-5-implemented-release-gates-open
updated: 2026-08-27
---

# Acceptance Criteria

## Current-data compatibility

- [ ] Current Gamma keyset, event-by-slug, and public-search fixtures parse with pagination and provenance intact.
- [ ] Current market and CLOB fields required by [[../03_Requirements/Requirements|requirements]] are preserved in canonical records.
- [ ] Route, schema, rate-limit, and malformed-payload errors are explicit and tested.

## Simulation fidelity

- [ ] A declared book fixture produces expected full, partial, and unfilled outcomes.
- [ ] GTC, GTD, FAK, FOK, and post-only behavior is covered by golden tests.
- [ ] Tick size, minimum size, lifecycle, accepting-orders, freshness, and neg-risk gates are explainable.
- [ ] Fees use the declared dynamic schedule, side, exponent/rate, and rounding; unknown values remain unknown.
- [ ] Rebate estimates are separate from fees and labeled with assumptions.

## Reproducibility and agent use

- [ ] Raw/normalized observations and model/config versions identify every simulated fill.
- [ ] Replaying the same dataset/configuration produces the same ledger/report checksum.
- [ ] CLI/MCP outputs are structured, bounded, and explain rejected/stale/unfilled decisions.

## Local trade-history GUI

- [x] `pm-trader --data-dir <path> gui` can open a selected paper-trading data directory on loopback without requiring provider access, credentials, or a frontend build service. (evidence: `pm_trader/cli.py`, `pm_trader/gui.py`, `tests/test_history_gui.py::test_cli_gui_descriptor_does_not_open_or_create_a_store`, [[GUI smoke review|GUI smoke review]])
- [x] The GUI lists bounded, newest-first canonical paper fills and compatible legacy trades, preserves the source kind, supports allow-listed filters, and has clear empty/loading/error states. (evidence: `pm_trader/history.py`, `pm_trader/gui_assets/`, `tests/test_history_gui.py::test_reader_filters_and_paginates_bounded_results`, `tests/test_history_gui.py::test_gui_reports_missing_store_without_creating_it`)
- [x] Selecting a row shows the available market/outcome, order, fill, accounting, timestamps, run/dataset, provenance, warnings, model/queue/latency assumptions, and explicit unknown reasons. (evidence: `tests/test_history_gui.py::test_reader_merges_canonical_and_legacy_with_full_detail`, `obsidian/04_Design/Trade_History_GUI.md`, [[GUI smoke review|GUI smoke review]])
- [x] The GUI displays a persistent paper-only/read-only notice and does not provide create, edit, cancel, submit, sign, redeem, credential, or fund controls. (evidence: `pm_trader/gui_assets/index.html`, `pm_trader/gui.py`, `tests/test_history_gui.py::test_gui_routes_are_bounded_local_gets_and_escape_stored_text`)
- [x] Integration/static checks prove GUI routes are GET-only, bounded, parameterized, provider-free, and do not mutate SQLite; the 2026-08-27 browser review confirms keyboard-focusable row controls, visible focus styling, and readable text status cues. (evidence: `tests/test_history_gui.py`, `obsidian/07_Validation/GUI_Smoke_2026-08-27.md`)

## Safety

- [ ] Paper mode cannot sign, submit, cancel, redeem, or move live funds.
- [ ] No credentials or sensitive wallet data appear in fixtures, logs, reports, or documentation.
- [ ] A human has reviewed the boundary between paper evidence and live-provider claims.

## Release evidence

Every checked box links to a test result, fixture, implementation file, or
human-review record. The remaining unchecked items are provider-current,
dataset-sharing, broader accessibility, and paper/live wording release gates.
