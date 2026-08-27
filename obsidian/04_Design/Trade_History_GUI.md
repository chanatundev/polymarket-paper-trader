---
type: gui-design
project: polymarket-paper-trader
status: proposed
updated: 2026-08-27
---

# Trade-history GUI

## Decision

Add a lightweight, local browser GUI for reviewing past paper trades. The GUI is an inspection surface over the selected SQLite data directory; it is not a live Polymarket terminal and does not replace the CLI/MCP workflows.

The first slice uses the repository's existing Python/SQLite shape and static HTML/CSS/JavaScript. A separate frontend build system, desktop wrapper, provider stream, authentication flow, and order-control surface are not required.

## User outcome

A human reviewer should be able to:

1. launch the GUI against a chosen paper-trading data directory;
2. scan a bounded newest-first list of stored canonical fills and compatible legacy trades;
3. filter the list by a small allow-listed set of fields;
4. select one row and understand what happened, what was modeled, and where the evidence came from; and
5. distinguish paper/model output, provider observation, unknown data, warnings, and unresolved human gates.

## Scope boundary

The GUI may read local records produced by paper simulation, replay, and the retained compatibility account surface. It must not:

- call Gamma, CLOB, Data API, WebSocket, or RTDS adapters;
- create, edit, cancel, submit, sign, redeem, or replay a new order from the UI;
- accept private keys, wallet addresses/secrets, credentials, or funds;
- mutate SQLite data, clear history, or alter a simulation run; or
- present paper fills, paper fees, or paper P&L as live execution or realized cash.

## Data contract

The planned bounded read model should normalize each list row to a stable `history_id` and `source_kind` while retaining source-specific identifiers. A canonical fill can use `run_id:fill_id`; a legacy trade can use `legacy:trade_id`.

| Display group | Fields | Evidence rule |
| --- | --- | --- |
| Identity | `history_id`, `source_kind`, `run_id`, `order_id`, `fill_id`/legacy trade ID | Keep canonical and legacy origins visible. |
| Market | condition/market ID, slug, question, outcome, asset/token ID | Show friendly labels when persisted; show `unknown` with a reason when they are not available. Do not fetch a provider record just to fill a label. |
| Execution | side, status, time-in-force/order type, requested quantity, filled quantity/shares, price/average price, notional/amount, partial flag, levels filled | Preserve decimal values and source semantics; do not silently round into a different result. |
| Accounting | fee, fee-rate/schedule when available, rebate estimate, cash/position delta, realized/unrealized P&L, report summary | Label estimates and model outputs separately from ledger cash effects. |
| Time | provider timestamp, local received timestamp, submitted/created timestamp, expiry where available | Keep provider and local clocks separate. |
| Evidence | observation/provenance IDs, dataset ID, checksum, schema/model versions, freshness, warnings, decision/rejection reasons | Use links/identifiers for traceability and retain unknown/error reasons. |
| Assumptions | latency, queue policy/ahead, fee model, seed/clock, data coverage, paper-only marker | Show the declared configuration that influenced the result. |

If the existing canonical `PaperOrder`/`Fill` records do not retain market question or outcome labels, add non-destructive metadata capture at simulation/run creation or expose the token ID as the honest fallback. The GUI must never infer a label from a live provider request.

## Layout

### Header and safety banner

- Project/data-directory label.
- Persistent `PAPER ONLY · LOCAL READ-ONLY` banner.
- Short note: “Paper fills and P&L are model outputs, not live execution or realized cash.”
- Refresh control may re-read local records; it is not a provider refresh.

### History list

- Newest-first rows with time, market/outcome or asset ID, side, status, filled quantity, average price, notional, fee, and source kind.
- Filters: free-text search over stored display fields, side, outcome/source kind, status, run ID, and a bounded date range if available.
- Explicit result count/limit and a visible “more results” state rather than an unbounded table.
- Empty state explains whether the store has no trades or filters matched nothing.
- Error state identifies an unavailable/malformed local store without exposing a traceback or raw payload.

### Selected-trade detail

Selecting a row opens an adjacent panel or a responsive detail section. It should group the data contract fields into Market, Order, Fill, Accounting, Evidence, and Assumptions. Unknowns, warnings, stale/inconsistent freshness, and rejection reasons should be visible rather than hidden in tooltips.

The detail view may link to a stored report by `run_id` and show its checksum, but it does not run a new simulation or render the entire raw dataset. Raw payloads remain bounded and sanitized.

## Planned local interface

The implementation may use a small standard-library HTTP server and static assets:

```text
pm-trader --data-dir .pm-trader gui [--port PORT]
        |
        +--> static index.html / styles / script
        +--> GET /api/trades?limit=...&cursor=...&filters...
        +--> GET /api/trades/{history_id}
                    |
                    +--> TradeHistoryReader / bounded query service
                                |
                                +--> local SQLite read-only connection
```

The exact module names can change during implementation, but the boundary is fixed: the GUI calls a typed, parameterized read service; it does not construct SQL, inspect internal tables directly, or instantiate the provider adapters. Bind to `127.0.0.1` by default. Keep the existing `ResourceLimits` page/response bounds or add an explicit GUI limit that is no looser.

For canonical records, detail joins should use stable IDs across `fills`, `orders`, `order_events`, `ledger_entries`, `simulation_runs`, and `reports`. For legacy records, the reader can adapt `trades` rows to the same display contract and must retain the `legacy` source marker. If the legacy table is absent, return canonical-only history without creating or altering schema. A read-only SQLite URI/connection mode is preferred; if the implementation uses a normal connection, tests must prove that all GUI routes execute reads only.

## Implementation sequence

1. Define the typed list/detail record and source adapters for canonical and legacy history.
2. Capture or surface friendly market/outcome metadata without changing ledger meaning.
3. Add bounded, allow-listed store queries and a service-level error envelope.
4. Add loopback HTTP routes and static UI states.
5. Add seeded integration fixtures for canonical fills, legacy trades, unknown fields, warnings, partial fills, and missing report context.
6. Validate read-only behavior, response bounds, HTML escaping, keyboard navigation, and the paper/live evidence boundary.

## Exit criteria

- A reviewer can open the GUI against a seeded SQLite store and see canonical plus legacy history in newest-first order.
- Filters and selection are deterministic, bounded, and do not require SQL knowledge.
- The selected detail shows execution/accounting/evidence/assumption fields or explicit unknown reasons.
- Empty, malformed-store, missing-detail, and oversized-result states are safe and understandable.
- Route/static tests prove no provider calls, no credentials, no write-like endpoints, and no SQLite mutation.
- A manual review confirms keyboard access, readable status cues, and honest paper-only wording.
