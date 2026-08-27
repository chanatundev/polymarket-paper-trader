---
type: implementation-checklist
project: polymarket-paper-trader
status: core-implementation-complete-gui-pending
updated: 2026-08-27
---

# Implementation Checklist

This is the implementation checklist for the planning vault in this project. It is derived from the requirements, constraints, roadmap, validation plan, and accepted decisions in the numbered directories below, including the explicitly requested local trade-history GUI recorded as D-005. It does not authorize live execution, provider writes, or a GUI feature outside that decision.

## How to use this file

- Keep an item unchecked until its stated evidence exists.
- Link completed items to tests, fixtures, reports, checksums, or human-review records. A passing source-repository test is evidence about that source revision only; it is not evidence of current provider compatibility.
- Keep unknowns, assumptions, provider incompatibilities, and human gates visible. Do not replace them with zero/default values or silently choose an open-question answer.
- Treat the `P0`/`P1` priorities in [Requirements](obsidian/03_Requirements/Requirements.md) as the priority order. The phase order below follows [Roadmap](obsidian/06_Execution/Roadmap.md) and is sequencing guidance, not additional scope.
- Do not update canonical vault logs unless explicitly requested; record implementation evidence in the implementation repository and link it from this checklist as appropriate.

## Current baseline

- The source audit and planning pass are complete.
- The successor implementation is complete in this user-selected checkout. Local evidence is linked below; live-provider and human-review gates remain separate.
- The next product slice is a planned local, read-only GUI for viewing past canonical paper fills and compatible legacy trades. No GUI implementation evidence exists yet.
- The initial product is public-data, paper-only research and simulation. It must not create, sign, submit, cancel, or redeem live orders or move live funds.
- Paper fills are model outputs, not evidence of live execution or profitability.

## Phase 0 — Contract and fixtures

Complete this phase before changing provider adapters or building the matcher.

- [x] Freeze sanitized fixtures for Gamma keyset pagination, event-by-slug, public search, CLOB market/book, fee-rate, and price-history responses. Include retrieval timestamps, no secrets or wallet-sensitive values, required-field contract assertions, and the relevant fixture/schema versions. (evidence: `fixtures/`, `tests/test_contracts.py::test_current_market_normalization_preserves_required_fields_and_unknowns`) (Next Actions; FR-001–003, FR-008, FR-012; NFR-004, NFR-006)
- [x] Define the versioned canonical records consumed by the simulation core: market state, book snapshot, market event, paper order, fill, and ledger entry. Define the reason carried with each unknown/null value; do not coerce missing data into a value that changes eligibility, price, fees, or P&L. (evidence: `pm_trader/models.py`, `pm_trader/normalizers.py`, `tests/test_contracts.py`) (System Overview; FR-002–003, FR-005–011; NFR-001)
- [x] Record current-versus-legacy compatibility decisions for routes, pagination, payload fields, fee-rate shapes, and other intentional source behavior. (evidence: `pm_trader/api.py::GammaAdapter`, `pm_trader/api.py::CLOBAdapter`, `tests/test_contracts.py::test_keyset_pagination_uses_cursor_and_rejects_offset`) (Roadmap; Decision D-004; NFR-006)
- [x] Write compatibility, malformed-payload, route, and rate-limit tests before adapter changes so each failure identifies one field, route, or semantic mismatch. Add negative fixtures for missing and invalid fields. (evidence: `tests/test_contracts.py`) (Next Actions; Roadmap; NFR-003, NFR-006)
- [x] Set explicit bounds for page size, history fidelity, recording duration/retention, replay size, and agent-facing response size. Keep the policy visible where long-running recording or raw-payload retention is implemented. (evidence: `pm_trader/api.py::ResourceLimits`, `pm_trader/recording.py::Recorder`, `pm_trader/mcp_server.py::main`) (Constraints; FR-011; NFR-005; Risk R-008)
- [x] Record research dates, official source URLs, fixture versions, and the boundary between point-in-time live probes, fixtures, local tests, and human review. (evidence: `obsidian/01_Research/Sources.md`, fixture `fixtureVersion` fields, `README.md`) (Sources; Constraints; Risk R-010)
- [ ] Review the paper-only safety boundary and the proposed schema contract before implementation proceeds. (Roadmap Phase 0; Acceptance Criteria)

## Phase 1 — Current-data adapters and normalized state

- [x] **FR-001:** Support market discovery through stable cursor pagination and public search while preserving the query, cursor, and response metadata. Add fixture coverage for keyset pagination that advances and terminates without offset assumptions. (evidence: `pm_trader/api.py::GammaAdapter`, `tests/test_contracts.py::test_keyset_pagination_uses_cursor_and_rejects_offset`)
- [x] **FR-002:** Normalize market/event metadata including identifiers, outcomes, token mapping, prices, order-book enablement, active/closed/accepting-orders state, minimum size, tick size, neg-risk, fee schedule, and timestamps. (evidence: `pm_trader/normalizers.py::normalize_market`, `tests/test_contracts.py::test_current_market_normalization_preserves_required_fields_and_unknowns`)
- [x] **FR-003:** Normalize CLOB book snapshots with bids, asks, asset/market identity, timestamp, hash, minimum size, tick size, neg-risk, and last trade price. (evidence: `pm_trader/normalizers.py::normalize_book`, `tests/test_contracts.py::test_current_book_normalization_preserves_identity_and_freshness_fields`)
- [x] **FR-004:** Ingest market WebSocket snapshots and incremental book/trade/lifecycle events, and reconnect through an authoritative snapshot before resuming incremental state after a gap. Keep provider timestamps separate from local receipt timestamps. (evidence: `pm_trader/stream.py`, `fixtures/websocket_messages.json`, `tests/test_stream.py`, `tests/test_record_replay.py::test_recorded_stream_persists_gap_and_resync_evidence`)
- [x] **FR-005:** Implement an explicit freshness/state result that distinguishes fresh, stale, unavailable, and inconsistent data. A stale, gapped, closed, inactive, or non-accepting state must not silently become eligible for simulation. (evidence: `pm_trader/freshness.py::FreshnessStateGate`, `tests/test_matching.py::test_stale_book_is_not_eligible`, `tests/test_matching.py::test_unknown_lifecycle_and_fee_state_do_not_become_eligible`)
- [x] **NFR-001:** Keep provider-specific routes and field names inside versioned adapters. Retain or visibly log unknown fields instead of discarding them without notice. (evidence: `pm_trader/api.py`, `pm_trader/normalizers.py`, `tests/test_contracts.py::test_current_market_normalization_preserves_required_fields_and_unknowns`)
- [x] **NFR-003:** Make network failures, provider rejections, stale data, malformed data, and local validation failures distinguishable in adapter and normalized results. (evidence: `pm_trader/errors.py`, `tests/test_contracts.py::test_http_network_and_provider_rejection_are_distinguishable`, `tests/test_contracts.py::test_http_429_is_distinguishable`)
- [x] **FR-012:** Add price history and public activity/position context as optional research inputs when useful, clearly separated from executable book state. If RTDS is used for external context, never treat that context as executable book state. (evidence: `pm_trader/api.py::CLOBAdapter` and `DataAdapter`, `tests/test_contracts.py::test_price_history_and_public_context_are_bounded_and_non_executable`)
- [x] Preserve raw payloads alongside typed observations with request/subscription metadata, provider timestamps, local receipt time, provenance identifiers, and schema version. (evidence: `pm_trader/api.py::Observation`, `pm_trader/recording.py::Recorder`, `pm_trader/storage.py`, `tests/test_record_replay.py::test_recorder_stores_sanitized_raw_and_normalized_book`)

## Phase 2 — Paper matching, fees, and ledger

- [x] **FR-006:** Implement paper-order matching against declared book/event observations with price-time behavior, partial fills, configurable latency, price/tick validation, minimum-size validation, and an explicit order lifecycle. (evidence: `pm_trader/matching.py::PaperMatcher.match_order`, `tests/test_matching.py`)
- [x] **FR-007:** Cover GTC, GTD, FAK, FOK, and post-only paper semantics, including expiry/cancel behavior and explicit rejection reasons. (evidence: `pm_trader/matching.py::cancel_order`, `tests/test_matching.py`, `tests/test_compatibility.py::test_legacy_limit_order_helpers_preserve_gtd_lifecycle`)
- [x] Apply the freshness/state gate before a paper order is accepted. Check market lifecycle, order-book enablement, neg-risk configuration, book/token identity, timestamp/hash/sequence continuity, age, minimum size, tick size, fee-schedule availability, model version, and the declared stale-data tolerance. (evidence: `pm_trader/freshness.py::FreshnessStateGate`, `tests/test_matching.py`, `tests/test_stream.py`)
- [x] **FR-008:** Calculate match-time dynamic fees from the declared market schedule and side, including exponent/rate semantics, rounding, minimum/zero behavior from fixtures, and explicit unknown-schedule behavior. A missing schedule must remain unknown or produce a stated policy rejection, never a silent zero fee. (evidence: `pm_trader/fees.py`, `tests/test_fees.py`, `tests/test_matching.py::test_unknown_lifecycle_and_fee_state_do_not_become_eligible`)
- [x] **FR-009:** Track maker-rebate estimates separately from fees and provider-confirmed rewards. Surface eligibility, confidence, coverage, and assumptions; never silently subtract an estimate from fees or cash requirements. (evidence: `pm_trader/fees.py::estimate_maker_rebate`, `pm_trader/ledger.py`, `tests/test_record_replay.py::test_ledger_separates_fee_and_rebate_entries`)
- [x] **FR-010:** Persist append-only orders, fills, fees, rebates, cash, positions, exposure, and realized/unrealized P&L with provenance links so every fill is traceable to its source observation, clock, assumptions, fee policy, and model version. (evidence: `pm_trader/ledger.py`, `pm_trader/storage.py`, `tests/test_record_replay.py::test_ledger_separates_fee_and_rebate_entries`)
- [x] **FR-015:** Explain why a market is not eligible, why an order was rejected, why a paper order did not fill, and which data or assumption affected the result. Distinguish at least no liquidity, price not crossed, expired, post-only would take, stale book, and invalid order outcomes. (evidence: `pm_trader/models.py::Decision`, `pm_trader/workflows.py::explain`, `tests/test_matching.py`, `tests/test_cli_mcp.py::test_mcp_tools_list_and_explain`)
- [x] **FR-014:** Enforce paper-only behavior in every relevant code path and configuration. There must be no live order creation, signing, submission, cancellation, redemption, or fund movement, and no private-key, wallet-secret, allowance, or custody path in the initial implementation. (evidence: `pm_trader/api.py::UrllibJsonTransport` is public-GET-only, `pm_trader/cli.py::safety`, `README.md`, `tests/test_cli_mcp.py::test_safety_command_reports_no_live_capabilities`; live/human review remains open)
- [x] **NFR-004:** Exclude credentials and sensitive wallet data from fixtures, logs, reports, documentation, and agent-facing responses. (evidence: `pm_trader/serialization.py::sanitize`, `tests/test_record_replay.py::test_recorder_stores_sanitized_raw_and_normalized_book`, `tests/test_contracts.py::test_price_history_and_public_context_are_bounded_and_non_executable`)
- [x] Add unit, golden, property-like invariant, and ledger-audit coverage for full/partial/unfilled outcomes, no overfill, tick alignment, order lifecycle, fee rounding, maker/taker differences, and cash/position conservation. (evidence: `tests/test_matching.py`, `tests/test_fees.py`, `tests/test_record_replay.py`)

## Phase 3 — Recording and deterministic replay

- [x] **FR-011:** Record raw and normalized observations, provider and local receipt timestamps, request/subscription metadata, reconnect gaps, schema versions, and provenance identifiers. (evidence: `pm_trader/recording.py::Recorder`, `pm_trader/storage.py`, `tests/test_record_replay.py`)
- [x] Resynchronize from an authoritative snapshot after a stream gap before matching resumes, and make the gap/resync visible in the recorded evidence and reports. (evidence: `pm_trader/stream.py::MarketStreamProcessor`, `pm_trader/recording.py::record_stream_updates`, `tests/test_record_replay.py::test_recorded_stream_persists_gap_and_resync_evidence`)
- [x] Replay the same normalized stream with a declared dataset, clock, latency, queue policy, seed, schema version, and model versions. Make these inputs part of run identity. (evidence: `pm_trader/simulation.py::run_id_for`, `pm_trader/recording.py::ReplayEngine`, `tests/test_record_replay.py::test_replay_is_deterministic_across_two_stores`)
- [x] Verify that repeating the same dataset and configuration produces the same append-only ledger and report checksum. (evidence: `tests/test_record_replay.py::test_replay_is_deterministic_across_two_stores`)
- [x] Label synthetic data and modeling assumptions clearly; prefer recorded books for full-depth replay and do not present midpoint or price history as a historical full-depth book. (evidence: `README.md`, `examples/README.md`, `pm_trader/models.py::SimulationReport.uncertainty`)
- [x] Enforce the raw-data retention, size, and sanitization policy established in Phase 0. (evidence: `pm_trader/api.py::ResourceLimits`, `pm_trader/recording.py::Recorder`, `tests/test_record_replay.py::test_recorder_enforces_configured_retention_bounds`)

## Phase 4 — Agent-facing workflow

- [x] **FR-013:** Expose structured discovery, market inspection, book inspection, simulation, replay, portfolio/P&L reporting, and decision-explanation workflows through the existing agent-facing CLI/MCP shape where practical. (evidence: `pm_trader/cli.py`, `pm_trader/mcp_server.py`, `pm_trader/workflows.py`, `tests/test_cli_mcp.py`)
- [x] Return bounded structured envelopes containing results, warnings, freshness, provenance IDs, assumptions, and errors. Do not hide material warnings in prose or require callers to inspect private SQLite tables. (evidence: `pm_trader/workflows.py::envelope`, `pm_trader/serialization.py::bounded_json`, `tests/test_cli_mcp.py`)
- [x] Provide compact reports that show fees, latency, data coverage, uncertainty, paper/live evidence boundaries, exposure, and P&L assumptions. Do not present paper P&L as realized cash or promise positive returns. (evidence: `pm_trader/ledger.py::report`, `pm_trader/models.py::SimulationReport`, `README.md`, `tests/test_cli_mcp.py::test_cli_simulation_is_structured_and_paper_only`)
- [x] Add examples or fixtures covering a discovery-to-inspection-to-simulation-to-replay workflow that an agent can inspect without loading raw datasets. (evidence: `examples/discovery_to_replay.py`, `examples/README.md`, `fixtures/`)

## Phase 5 — Local trade-history GUI

This phase is approved for planning by D-005 but is not implemented by the current checkout. It adds human inspection of stored paper history without adding a live market terminal or order-control surface.

- [ ] Define a bounded `TradeHistoryRecord` list/detail contract that normalizes canonical `fills` and compatible legacy `trades`, keeps `source_kind` visible, and uses stable history IDs. (Requirements FR-016–017; [Trade-history GUI](obsidian/04_Design/Trade_History_GUI.md))
- [ ] Add a dedicated parameterized read/query service over SQLite that joins fills, orders, order events, ledger entries, simulation runs, reports, and legacy trades without exposing raw SQL to the UI. Apply allow-listed filters, newest-first ordering, pagination, and existing response bounds. (Requirements FR-016–018; NFR-007)
- [ ] Preserve or non-destructively capture friendly market/outcome metadata for canonical history. If it is absent, expose the asset/market ID and an explicit unknown reason rather than making a provider request or guessing. (Requirements FR-017; Risk R-013)
- [ ] Add a dependency-light local browser server and `pm-trader gui` launcher. Bind to `127.0.0.1` by default, serve static assets, expose only bounded `GET` history/detail routes, and avoid provider adapters, credentials, and all write-capable workflows. (Requirements FR-018; NFR-008)
- [ ] Build the list/filter/detail UI with a persistent paper-only/read-only notice, execution/accounting/evidence/assumption sections, and clear loading, empty, malformed-store, missing-detail, warning, and unknown states. (Requirements FR-016–018; NFR-009)
- [ ] Add canonical/legacy seeded integration fixtures and route/static tests for list/detail joins, filters, bounds, HTML escaping, keyboard navigation, no provider calls, no SQLite mutation, and no write-like endpoint. (Acceptance Criteria; Validation Plan)
- [ ] Update `README.md` and `docs/CLI.md` with the launch command, data-directory behavior, filters, limits, and paper/live evidence boundary after the GUI is implemented. (NFR-007–008)

### Phase 5 exit gate

- [ ] A reviewer can launch the GUI against a seeded data directory, find a past trade, select it, and inspect its linked order/fill/ledger/report/provenance/assumption context without direct SQL access.
- [ ] The GUI remains local/read-only and the tests prove it does not call a provider or mutate the store.
- [ ] The GUI acceptance items in `obsidian/07_Validation/Acceptance_Criteria.md` have linked evidence; unsupported visual/accessibility claims remain human-review gates.

## Validation and release evidence

The vault's acceptance checklist remains proposed until each checked item has linked evidence. Complete the following before calling the implementation accepted.

### Current-data compatibility

- [x] Dated current Gamma keyset, event-by-slug, and public-search fixtures parse with pagination and provenance intact. (evidence: `fixtures/`, `obsidian/01_Research/Sources.md`, `tests/test_contracts.py`; not a live compatibility claim)
- [x] Dated current market and CLOB fields required by [Requirements](obsidian/03_Requirements/Requirements.md) are preserved in canonical records. (evidence: `pm_trader/normalizers.py`, `obsidian/01_Research/Sources.md`, `tests/test_contracts.py`; not a live compatibility claim)
- [x] Route, schema, rate-limit, and malformed-payload errors are explicit and tested. (evidence: `pm_trader/api.py`, `pm_trader/errors.py`, `tests/test_contracts.py`)

### Simulation fidelity

- [x] A declared book fixture produces expected full, partial, and unfilled outcomes. (evidence: `tests/test_matching.py`)
- [x] GTC, GTD, FAK, FOK, and post-only behavior is covered by golden tests. (evidence: `tests/test_matching.py`, `tests/test_compatibility.py`)
- [x] Tick size, minimum size, lifecycle, accepting-orders, freshness, and neg-risk gates are explainable. (evidence: `pm_trader/freshness.py`, `tests/test_matching.py`)
- [x] Fees use the declared dynamic schedule, side, exponent/rate, and rounding; unknown values remain unknown. (evidence: `pm_trader/fees.py`, `tests/test_fees.py`, `tests/test_matching.py`)
- [x] Rebate estimates are separate from fees and labeled with assumptions. (evidence: `pm_trader/fees.py`, `pm_trader/ledger.py`, `tests/test_record_replay.py`)

### Reproducibility and agent use

- [x] Raw/normalized observations and model/config versions identify every simulated fill. (evidence: `pm_trader/models.py::Fill`, `pm_trader/simulation.py`, `tests/test_record_replay.py`)
- [x] Replaying the same dataset/configuration produces the same ledger/report checksum. (evidence: `tests/test_record_replay.py::test_replay_is_deterministic_across_two_stores`)
- [x] CLI/MCP outputs are structured, bounded, and explain rejected, stale, and unfilled decisions. (evidence: `pm_trader/cli.py`, `pm_trader/mcp_server.py`, `tests/test_cli_mcp.py`)

### Safety

- [x] Paper mode cannot sign, submit, cancel, redeem, or move live funds. Local paper cancellation is an in-memory/SQLite lifecycle operation only. (evidence: `pm_trader/cli.py::safety`, `pm_trader/api.py`, `README.md`, `tests/test_cli_mcp.py::test_safety_command_reports_no_live_capabilities`; human review remains open)
- [x] No credentials or sensitive wallet data appear in fixtures, logs, reports, or documentation. (evidence: `pm_trader/serialization.py::sanitize`, `tests/test_record_replay.py::test_recorder_stores_sanitized_raw_and_normalized_book`; human sharing review remains open)
- [ ] A human has reviewed the boundary between paper evidence and live-provider claims.

### Validation gates

- [x] Run local unit, contract, property-like invariant, golden, replay, static, and negative checks. (evidence: `uv run --with ruff ruff format pm_trader tests examples`, `uv run --with ruff ruff check pm_trader tests examples`, `uv run python -m pytest -q`, `git diff --check`)
- [ ] Run bounded, read-only live probes immediately before release and record their date and limits.
- [ ] Review official documentation changes for CLOB V2, fees, pagination, and real-time channels.
- [ ] Review any research dataset for personal, credential, or wallet-sensitive data before sharing it.
- [ ] Have a human review the paper-only boundary, assumptions, and wording of strategy-performance reports.
- [x] Link evidence for every completed acceptance item; leave unsupported or deferred items unchecked and explain the deferral. (evidence: checked items above; live, provider-current, dataset-sharing, and human-review gates remain unchecked)

## Decision gates — do not silently choose

Resolve these in [Open Questions](obsidian/09_Decisions/Open_Questions.md) or a new decision record before the related scope is expanded. Their defaults are not permission to implement additional features.

- [x] **OQ-001:** Confirm the implementation checkout/source ownership if work needs the audited `/tmp` clone. This checklist is located in the user-selected project directory; do not assume the audited clone is the implementation target. (evidence: user-selected implementation path `/Users/chanatun/code/projects/polymarket-paper-trader`; audited `/tmp` source was read-only context)
- [x] **OQ-002:** Decide whether v0.x needs a GUI. Add the requested local, loopback-only, read-only trade-history GUI; keep live dashboarding and order control out of this scope. (evidence: user request on 2026-08-27; D-005; `obsidian/04_Design/Trade_History_GUI.md`)
- [ ] **OQ-003:** Choose the first recorded market family before Phase 1: crypto, sports, or general events.
- [ ] **OQ-004:** Set the acceptable retention limit for raw book/event recordings.
- [ ] **OQ-005:** Decide whether to include maker-rebate estimates before provider-confirmed reward data exists. The current default is visibly labeled optional output.
- [ ] **OQ-006:** Decide whether one replay may contain multiple fee-model versions. The current default is one declared model version per run.
- [ ] **OQ-007:** Keep authenticated execution out of v0.x unless a separate product/security decision, threat model, package boundary, controls, and acceptance evidence authorize it.

### Implementation-scoped defaults (not owner decision records)

- OQ-001 is resolved for this run by the user-selected checkout: `/Users/chanatun/code/projects/polymarket-paper-trader` (a symlinked iCloud checkout). The audited `/tmp` source was used only as read-only compatibility context.
- The implementation uses the current bounded defaults while the remaining decision checkboxes stay open: CLI/MCP remains the agent-facing surface; the GUI is local/read-only over stored history; general-event fixtures; 50 MiB and 100,000-observation recording bounds; 10,000 history points; 100,000 replay events; 64 KiB agent responses; visibly labeled optional rebate estimates; one declared fee-model version per run; and no authenticated execution.
- OQ-002 is resolved only for the requested local history GUI. OQ-003 through OQ-007 remain unchecked because their defaults are implementation constraints, not owner approval for a market-family commitment, a product-retention policy, provider-confirmed rewards, multi-model replay, live monitoring, or authenticated execution.

## Accepted implementation posture

These are the vault's current planning decisions and invariants, not new feature requests:

- Paper-only first; no authenticated live execution in v0.x.
- Provider-specific compatibility stays behind a provider/domain normalization boundary.
- Preserve the existing Python, SQLite, CLI, and MCP shape where that reduces migration risk, while keeping adapters, domain models, matching, ledger, and interfaces independently testable.
- Treat current public API behavior as dated, sanitized, fixture-backed evidence rather than a permanent contract.
- Unknown is not zero; stale or gapped state is visible; every fill is explainable; fees and rebates remain separate; replay is deterministic by declaration; research artifacts contain no credentials.
- The GUI is a bounded local read-only projection of stored history; it preserves source kind, provenance, unknown reasons, warnings, assumptions, and the paper/live evidence boundary.

## Explicitly out of scope for v0.x

- Real-money order creation, signing, submission, cancellation, or redemption.
- Wallet management, private-key handling, allowance management, custody, or fund transfers.
- Claims that a paper fill is equivalent to a live fill.
- Autonomous strategy deployment, unattended capital allocation, a general-purpose prediction model, or a promise of positive returns.
- Undocumented/private endpoint scraping where an official public interface is available.
- A live-provider dashboard/alert layer, order controls, or any UI that creates, edits, cancels, signs, submits, redeems, or moves funds. The local stored-history GUI is limited to Phase 5.

## Source map

- Requirements and priorities: [Requirements](obsidian/03_Requirements/Requirements.md), [Constraints](obsidian/03_Requirements/Constraints.md)
- Design baseline: [System Overview](obsidian/04_Design/System_Overview.md), [Project Map](obsidian/00_Home/Project_Map.md)
- GUI design: [Trade-history GUI](obsidian/04_Design/Trade_History_GUI.md)
- Execution order: [Roadmap](obsidian/06_Execution/Roadmap.md), [Next Actions](obsidian/06_Execution/Next_Actions.md)
- Validation and evidence: [Acceptance Criteria](obsidian/07_Validation/Acceptance_Criteria.md), [Validation Plan](obsidian/07_Validation/Validation_Plan.md), [Success Metrics](obsidian/02_Strategy/Success_Metrics.md)
- Scope and decisions: [Scope and Non-Goals](obsidian/02_Strategy/Scope_and_Non_Goals.md), [Decision Log](obsidian/09_Decisions/Decision_Log.md), [Locked Invariants](obsidian/09_Decisions/Locked_Invariants.md), [Open Questions](obsidian/09_Decisions/Open_Questions.md), [Rejected Ideas](obsidian/09_Decisions/Rejected_Ideas.md)
