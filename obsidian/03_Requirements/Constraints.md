---
type: constraints
project: polymarket-paper-trader
updated: 2026-08-27
---

# Constraints

## Product and safety

- Paper-only is the default and initial release boundary.
- No private keys, wallet secrets, signing payloads, live order writes, cancellations, redemptions, or fund transfers.
- A paper fill is an estimate produced by a declared model, not evidence of a live fill.
- Strategy evaluation must show fees, latency, data coverage, and assumptions.

## Provider and data

- Polymarket APIs and schemas are changing; adapters must use versioned fixtures and fail visibly on incompatible payloads.
- Public endpoints may rate-limit, time out, return partial data, or change pagination semantics.
- Streaming state requires reconnect and snapshot resynchronization; local state cannot be assumed authoritative after a gap.
- Full-depth historical replay is impossible without recorded books or a provider history source that contains sufficient depth; midpoint history is not a substitute.
- Provider timestamps and local receipt timestamps must remain separate.

## Modeling

- Fees are evaluated at match time using the schedule and side known to the simulator; missing fee data is an explicit unknown, not zero.
- Maker rebates are estimates until backed by a provider-confirmed reward record.
- Market lifecycle, order-book enablement, accepting-orders state, minimum size, tick size, and neg-risk are independent fields.
- Simulations need a declared queue/latency assumption because public snapshots do not reveal the simulator's place in a live queue.

## Engineering

- Preserve the existing Python/SQLite/CLI/MCP shape where it reduces migration risk.
- Keep provider adapters, domain models, matching, ledger, and agent interfaces independently testable.
- Keep bounded storage and response sizes so long-running recorders cannot grow without policy.
- Avoid undocumented endpoints when official public APIs cover the use case.

## GUI

- The first GUI is a local inspection surface for stored paper fills/trades, not a live market terminal.
- Bind the local server to loopback by default and use a dedicated bounded read/query service; the browser must not read arbitrary SQLite tables or accept raw SQL.
- GUI routes are read-only and must not call provider adapters, accept credentials, or expose order or account mutation controls.
- Reuse the existing Python/SQLite dependency-free shape and static assets; do not introduce a frontend build service for the first slice.
- Do not dump raw payloads into the list view. Detail views may expose bounded sanitized provenance and assumptions, with unknown values and reasons kept visible.
- Preserve the paper-only banner and distinguish model output, provider observation, and live/human-review gates in the UI copy.

## Documentation and operations

- Research dates, source URLs, fixture versions, and live-probe boundaries must be recorded.
- Unknowns and deferred human decisions remain visible in [[../09_Decisions/Open_Questions|Open questions]].
- No canonical vault logs are updated by this project unless explicitly requested.
