---
type: gui-design
project: polymarket-paper-trader
status: implemented
updated: 2026-08-27
---

# Trade-history GUI

## Decision

The implementation adds a lightweight, local browser GUI for reviewing past
paper trades. The GUI is an inspection surface over the selected SQLite data
directory; it is not a live Polymarket terminal and does not replace the
CLI/MCP workflows.

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

The implemented bounded read model normalizes each list row to a stable
`history_id` and `source_kind` while retaining source-specific identifiers. A
canonical fill uses `canonical:{run_id}:{fill_id}`; a legacy trade uses
`legacy:{trade_id}`.

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

## Implemented local interface

The implementation uses a small standard-library HTTP server and static assets:

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

The implementation lives in `pm_trader/history.py` and `pm_trader/gui.py` with
static assets under `pm_trader/gui_assets/`. The GUI calls the typed,
parameterized `TradeHistoryReader`; it does not construct SQL in JavaScript,
inspect tables from the browser, or instantiate provider adapters. The server
binds to `127.0.0.1`, uses immutable SQLite read-only mode, and applies the
existing page/response bounds.

The implemented routes are `GET /`, `GET /api/trades` with allow-listed
filters and cursor pagination, and `GET /api/trades/{history_id}`. Other
methods return `405`. The `pm-trader gui` launcher supports `--port` and
`--open`; a missing store is reported without creating a directory or
database.

For canonical records, detail joins use stable IDs across `fills`, `orders`, `order_events`, `ledger_entries`, `simulation_runs`, and `reports`. For legacy records, the reader adapts `trades` rows to the same display contract and retains the `legacy` source marker. If the legacy table is absent, it returns canonical-only history without creating or altering schema. SQLite immutable read-only mode prevents the GUI from creating WAL sidecars or other database changes.

## Implementation evidence

- `pm_trader/history.py` provides bounded canonical/legacy list and detail
  projections, source markers, stable IDs, filters, pagination, joins, and
  explicit unknown reasons.
- `pm_trader/gui.py` provides the loopback-only JSON/static server and
  paper/read-only envelope.
- `pm_trader/gui_assets/` provides the list/filter/detail surface with
  text-only data insertion, visible warnings, empty/error states, and
  keyboard-focusable row buttons.
- `tests/test_history_gui.py` covers canonical/legacy fixtures, joins,
  filters, bounds, escaping, no-provider behavior, immutable reads, and
  write-like route rejection.
- [[../07_Validation/GUI_Smoke_2026-08-27|GUI smoke review]] records the
  local browser check.

## Exit criteria

- A reviewer can open the GUI against a seeded SQLite store and see canonical plus legacy history in newest-first order.
- Filters and selection are deterministic, bounded, and do not require SQL knowledge.
- The selected detail shows execution/accounting/evidence/assumption fields or explicit unknown reasons.
- Empty, malformed-store, missing-detail, and oversized-result states are safe and understandable.
- Route/static tests prove no provider calls, no credentials, no write-like endpoints, and no SQLite mutation.
- The 2026-08-27 manual review confirms keyboard access, readable status cues,
  responsive layout, and honest paper-only wording; broader accessibility
  conformance remains a release gate.
