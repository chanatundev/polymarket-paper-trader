---
type: success-metrics
project: polymarket-paper-trader
updated: 2026-08-27
---

# Success Metrics

| ID | Metric | Initial target | Evidence |
| --- | --- | --- | --- |
| SM-001 | Current public payload coverage | Fixture tests preserve every required market/book field and classify unknown fields | Contract-test report |
| SM-002 | Fee correctness | Known fee fixtures match the declared fee formula, exponent, rounding, maker/taker side, and unknown-state behavior | Fee golden tests |
| SM-003 | Matching fidelity | Replayed fixtures produce expected price-time, partial-fill, cancel, FAK/FOK, GTC/GTD, and post-only outcomes | Matching golden tests |
| SM-004 | Reproducibility | Same dataset, seed, and configuration produce byte-stable ledger/report output | Replay checksum |
| SM-005 | Provenance | Every simulated fill links to source observation IDs, clock time, and model version | Ledger audit |
| SM-006 | Safety | No paper-mode path imports signing/submission behavior or writes to a live trading endpoint | Static and negative tests |
| SM-007 | Agent ergonomics | CLI/MCP responses explain state, freshness, skipped fills, fees, and assumptions in structured form | Tool contract tests |
| SM-008 | Honest performance | A documented benchmark reports ingestion, replay, and memory behavior on a declared fixture size | Benchmark record |
| SM-009 | Human trade review | A reviewer can launch the local GUI, scan bounded past-trade history, and inspect a selected trade's accounting and evidence without direct SQL access | GUI integration test and manual review |

## Interpretation

These are product-quality metrics, not trading-performance promises. Strategy returns should be reported only with dataset coverage, fees, latency, and uncertainty visible.
