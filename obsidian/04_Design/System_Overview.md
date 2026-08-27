---
type: system-design
project: polymarket-paper-trader
status: proposed
updated: 2026-08-26
---

# System Overview

## Design objective

Build a versioned boundary between changing Polymarket payloads and a deterministic paper-simulation core. The simulator should be usable from a live public-data stream and from an identical recorded-data replay.

## Components

### Provider adapters

- **Gamma adapter:** market/event discovery, keyset pagination, event-by-slug, public search, tags/series where useful.
- **CLOB REST adapter:** market info, book/books, price/prices, midpoint/spread, price history, fee/tick metadata.
- **Data adapter:** public activity, positions, closed positions, trades, value, open interest, or holders when research context requires them.
- **Market WebSocket adapter:** snapshots and incremental book/trade/lifecycle events; reconnect and resynchronize after a gap.
- **RTDS adapter:** optional external context such as crypto and comments; never treat context as executable book state.

Each adapter returns raw payloads plus typed observations, request metadata, provider timestamps, local receipt time, and schema version.

### Canonical domain model

At minimum, define versioned records for:

- `MarketState`: condition, event, outcome/token mapping, lifecycle flags, order-book flags, min size, tick, neg-risk, fee schedule, and freshness.
- `BookSnapshot`: market/token identity, bids, asks, timestamp, hash, last trade, min size, tick, source, and sequence/gap state.
- `MarketEvent`: book change, trade, best bid/ask, new market, resolution, or external context with raw provenance.
- `PaperOrder`: requested side, token, price, size, type, time-in-force, post-only, submission time, expiry, and model configuration.
- `Fill`: order ID, matched quantity, price, book observation, simulated latency/queue assumptions, fee, rebate estimate, and timestamps.
- `LedgerEntry`: cash, position, fee, rebate, realized/unrealized P&L, and source links.

Missing values remain typed unknown/null with a reason. They must not be coerced to zero when that changes eligibility, price, fee, or P&L behavior.

### Freshness and state gate

Before an order is accepted by the paper engine, the gate evaluates:

1. market active/closed/accepting-orders state;
2. order-book enablement and neg-risk configuration;
3. book identity, token identity, timestamp, hash/sequence continuity, and age;
4. minimum size and tick-size constraints;
5. fee schedule availability and model version;
6. local simulation policy, including stale-data tolerance.

The result is an explainable decision with `eligible`, `rejected`, `stale`, or `unknown` status.

### Matching engine

The engine consumes a snapshot/event clock and paper orders. It applies declared latency and queue assumptions, matches against available levels, supports partial fills, and emits immutable fill events. It must distinguish “no liquidity,” “price not crossed,” “expired,” “post-only would take,” “stale book,” and “invalid order.”

### Fee and rebate engine

Fee calculation is a separate policy component. It receives the market's versioned schedule, match price, matched amount, order side, and maker/taker status. It applies the declared dynamic formula, exponent/rate semantics, rounding, and minimum/zero behavior from fixtures. Maker rebates are a separate estimate with a confidence/eligibility field; they do not silently offset fees.

### Recorder and replayer

The recorder stores raw payloads, normalized observations, receipt timestamps, request/subscription metadata, and schema versions. The replayer can run the same normalized stream through the matching and ledger components with a fixed seed and declared timing policy. Raw data is retained for debugging subject to an explicit retention limit.

### Storage

SQLite remains the first persistence layer. Tables should separate provider observations, normalized market state, books/events, simulation runs, orders, fills, ledger entries, and reports. Foreign keys or stable provenance IDs must make every fill traceable to its inputs.

### CLI and MCP

Retain the source's agent-facing interfaces but return structured envelopes containing result, warnings, freshness, provenance IDs, assumptions, and errors. Proposed operations include discovery, market inspection, book inspection, record, replay, simulate, portfolio report, and explain-decision.

## Data flow

`provider -> raw observation -> normalizer -> canonical state/event -> freshness gate -> recorder and/or matcher -> immutable ledger -> report/CLI/MCP`

## Important separation

Public market data can inform a paper simulation. It must not be wired to an authenticated write path in this release. A future live adapter, if ever approved, must be a separate package and decision surface rather than a flag that changes paper behavior.

