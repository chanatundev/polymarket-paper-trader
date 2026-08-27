---
type: requirements
project: polymarket-paper-trader
updated: 2026-08-26
---

# Requirements

Requirements are intentionally phrased so they can be tested without claiming live-provider stability.

## Functional requirements

| ID | Priority | Requirement | Evidence/dependency |
| --- | --- | --- | --- |
| FR-001 | P0 | Discover markets through stable cursor pagination and public search, preserving query, cursor, and response metadata. | Gamma keyset/search fixtures |
| FR-002 | P0 | Normalize market/event metadata including identifiers, outcomes, prices, order-book enablement, active/closed/accepting-orders state, minimum size, tick size, neg-risk, fee schedule, and timestamps. | Gamma/CLOB contract fixtures |
| FR-003 | P0 | Normalize order-book snapshots with bids, asks, asset/market identity, timestamp, hash, minimum size, tick size, neg-risk, and last trade price. | CLOB book fixtures |
| FR-004 | P1 | Ingest market WebSocket events and reconnect with an authoritative snapshot before resuming incremental state. | WebSocket fixture/reconnect test |
| FR-005 | P0 | Mark state as fresh, stale, unavailable, or inconsistent; never silently convert missing provider data into a tradable zero/default. | Freshness/state tests |
| FR-006 | P0 | Simulate paper orders with price-time matching, partial fills, configurable latency, price/tick validation, minimum-size validation, and explicit order lifecycle. | Matching golden tests |
| FR-007 | P0 | Model GTC, GTD, FAK, FOK, and post-only behavior as paper semantics with rejection reasons. | Order-type tests |
| FR-008 | P0 | Calculate dynamic match-time fees from the declared market schedule and side, including rounding and unknown schedule behavior. | Fee golden tests |
| FR-009 | P1 | Track maker-rebate estimates separately from provider-confirmed rewards and surface eligibility/coverage assumptions. | Rebate model tests |
| FR-010 | P0 | Persist append-only orders, fills, fees, rebates, cash, positions, exposure, and P&L with provenance links. | Ledger audit tests |
| FR-011 | P1 | Record raw and normalized observations and replay them deterministically with declared clock, latency, seed, schema, and model versions. | Replay checksums |
| FR-012 | P1 | Add price-history and public activity/position context as optional research inputs, clearly separated from executable book state. | Data API/history fixtures |
| FR-013 | P1 | Expose discovery, inspection, simulation, replay, and reporting through structured CLI and MCP operations. | CLI/MCP contract tests |
| FR-014 | P0 | Enforce paper-only mode: no live order creation, signing, submission, cancellation, redemption, or fund movement. | Static/negative tests |
| FR-015 | P1 | Explain why a market is not eligible, why an order was rejected, why a paper order did not fill, and which data/assumption affected a result. | Explainability fixtures |

## Non-functional requirements

| ID | Requirement |
| --- | --- |
| NFR-001 | Provider schemas are versioned and unknown fields are retained or logged rather than discarded without notice. |
| NFR-002 | A simulation run is reproducible from declared inputs and produces a stable ledger/report checksum. |
| NFR-003 | Errors distinguish network failure, provider rejection, stale data, malformed data, and local validation failure. |
| NFR-004 | Sensitive values are excluded from datasets, logs, fixtures, reports, and MCP responses. |
| NFR-005 | Resource limits are explicit for page size, history fidelity, recorder duration, replay size, and MCP response size. |
| NFR-006 | Tests cover current fixtures and legacy compatibility fixtures where preserving source behavior is intentional. |

