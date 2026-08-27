---
type: next-actions
project: polymarket-paper-trader
updated: 2026-08-26
---

# Next Actions

1. **Freeze current response fixtures.** Capture sanitized Gamma keyset, event-by-slug, public-search, CLOB market, book, fee-rate, and price-history shapes with retrieval timestamps. Done when fixtures contain no secrets and every required field has a contract assertion.
2. **Define the canonical schema.** Specify typed records and unknown/error behavior for market state, books, events, fees, orders, fills, and ledger entries. Done when the matcher can consume domain objects without provider-specific conditionals.
3. **Write compatibility tests before adapter changes.** Reproduce the current parser gaps and encode desired behavior for current and legacy payloads. Done when failures identify a single field/route/semantic mismatch.
4. **Implement the fee policy in isolation.** Cover dynamic rate/exponent, maker/taker, rounding, missing schedule, and rebate estimates. Done when golden fixtures and conservation tests pass.
5. **Implement the standalone matcher.** Add partial fills, latency, queue policy, order types, post-only, expiry, tick/minimum-size checks, and explainable rejection reasons. Done when golden order-book scenarios pass.
6. **Add recording/replay.** Store provenance and reconnect gaps; replay with deterministic configuration. Done when repeated runs produce the same ledger checksum.
7. **Expose structured CLI/MCP workflows.** Add discovery, inspect, simulate, replay, report, and explain operations. Done when an external caller can complete the workflow without private database knowledge.
8. **Run the validation matrix.** Execute local tests, bounded read-only live probes, and a human review of paper-only enforcement. Done when each acceptance criterion has evidence or an explicit deferral.

## Immediate implementation slice

The smallest useful slice is: one current market fixture -> normalized market/book -> one paper limit order -> one or more simulated fills -> dynamic fee entry -> reproducible report. It should not require a WebSocket recorder or any authenticated provider access.

