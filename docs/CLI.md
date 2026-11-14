# Paper trader CLI guide

The CLI is a JSON-first interface for public-data research, paper matching, and
bounded replay. It does not place or manage live orders.

Every valid command execution returns a bounded JSON envelope with these top-level
fields:

```json
{
  "ok": true,
  "result": {},
  "warnings": [],
  "freshness": "fresh",
  "provenance_ids": [],
  "assumptions": {},
  "errors": []
}
```

Paper fills, paper fees, paper P&L, and rebate estimates are model outputs. They
are not evidence of live execution or live performance.

## Prerequisites

Run commands from the repository root. The examples use the module form because
it works directly from a checkout:

```bash
uv run python -m pm_trader --help
```

The package also declares a `pm-trader` console-script entry point when installed
into an environment, but the module form is the canonical checkout command.

The repository fixtures are safe for offline exploration. A command that includes
`--fixture` reads local JSON only. Commands without a fixture may make bounded,
unauthenticated public GET requests to the configured provider APIs.

## Data directory and repeatability

The global option must appear before the subcommand:

```bash
uv run python -m pm_trader --data-dir .pm-trader-demo simulate ...
```

The default data directory is `.pm-trader`. It can also be set with
`PM_TRADER_DATA_DIR` when `--data-dir` is omitted.

Simulation and replay reports are stored in SQLite under this directory. The
response contains the resulting `result.run_id`; pass that ID to `report` using
the same data directory. Run identities are deterministic. Repeating the same
dataset, market, order, and configuration in the same append-only store is
rejected; use a distinct `--dataset-id` or data directory for a separate run.

## Safety check

Start by checking the declared capability boundary:

```bash
uv run python -m pm_trader safety
```

The result reports `mode: "paper_only"` and false values for `live_writes`,
`signing`, `cancellation`, `redemption`, and `fund_movement`.

There are no CLI flags for credentials, private keys, wallets, signing, order
submission, redemption, or fund transfers. Local paper cancellation is only a
paper lifecycle operation; it does not call a provider.

## Local trade-history GUI

Launch the lightweight browser review surface against the same data directory
used by simulation and replay:

```bash
uv run python -m pm_trader --data-dir .pm-trader-demo gui
```

The server binds to `127.0.0.1` and stays in the foreground until interrupted.
Use `--port 0` to choose a free loopback port or `--open` to open the displayed
URL in the default browser:

```bash
uv run python -m pm_trader --data-dir .pm-trader-demo gui --port 8765 --open
```

The global `--data-dir` may name a directory (the GUI reads `paper.db` inside
it) or a `.db`, `.sqlite`, or `.sqlite3` file. The GUI opens SQLite in
read-only, query-only mode. If the store uses WAL mode, SQLite may use or create
WAL coordination sidecars while reading committed rows; the paper database is
not modified. A missing store is reported in the page and does not create a
directory or database. The GUI makes no Gamma, CLOB, Data API, WebSocket, or
RTDS requests and has no order, credential, wallet, or fund controls.

The list is bounded to 25 rows by default and 100 rows at most per request. It
is newest-first and can be filtered in the page by search text, side, status,
source kind, market/asset, outcome, and run ID. The local API also accepts
bounded `start` and `end` timestamp filters. Selecting a row shows its market,
order, fill, accounting, timestamps, report/checksum, provenance, warnings,
and declared assumptions; absent values remain explicit unknowns. Canonical
records retain their `canonical_fill` source marker, while compatibility rows
retain `legacy_trade`.

The read-only routes are:

| Route | Purpose |
| --- | --- |
| `GET /` | Static GUI shell. |
| `GET /api/trades?limit=...&cursor=...` | Bounded history list and allow-listed filters. |
| `GET /api/trades/{history_id}` | One bounded trade detail record. |

Other methods return `405 Method Not Allowed`. JSON responses use the same
64 KiB response bound as the agent-facing interface. The persistent banner in
the UI states that paper fills and P&L are model outputs, not live execution or
realized cash.

## Discovery

### List markets

Use keyset discovery without a query. The `next_cursor` in the result is the
cursor for the next page:

```bash
uv run python -m pm_trader discover --limit 20
uv run python -m pm_trader discover --limit 20 --cursor '<next_cursor>'
```

For an offline run, supply a Gamma keyset fixture:

```bash
uv run python -m pm_trader discover \
  --fixture fixtures/gamma_markets_keyset_page1.json \
  --limit 1
```

`--limit` is bounded to 100 for keyset pages. List and search results are scan
rows: condition id, slug, question, start and end time, lifecycle flags,
freshness, and provenance id. Missing start or end times stay null with an
`unknown_reasons` entry. Use `inspect market` or `market get` for the full
canonical record, including tokens, fees, and unknown provider fields.

Rank and filter keyset pages with `--order` (`volume_24hr`, `volume`,
`liquidity`, `end_date`, `start_date`; largest first unless `--ascending`) and
`--open-only` (active, not closed):

```bash
uv run python -m pm_trader discover --order volume_24hr --open-only --limit 20
```

Each scan row includes `outcomes` (label and indicative Gamma price) and, when
Gamma reports them, `stats`: `volume_24hr`, `volume_1wk`, `volume`,
`liquidity`, `best_bid`, `best_ask`, `spread`, `last_trade_price`,
`one_day_price_change`, and `one_week_price_change`. These are indicative, not
executable; use `quote` for the live book.

### Search public markets

Pass `--query` to use public search instead of keyset pagination:

```bash
uv run python -m pm_trader discover --query "example event" --limit 20
```

Offline search uses the public-search fixture:

```bash
uv run python -m pm_trader discover \
  --query "example event" \
  --fixture fixtures/gamma_public_search.json \
  --limit 10
```

The response preserves the query, scan rows, events, tags, profiles, pagination
metadata, and provenance ID. Search results are bounded to 50.

### `market` aliases

`market` and `markets` are aliases for the same command. They provide convenient
list, search, and get forms:

```bash
uv run python -m pm_trader market list --fixture fixtures/gamma_markets_keyset_page1.json
uv run python -m pm_trader markets search "example" --fixture fixtures/gamma_public_search.json
uv run python -m pm_trader market get will-the-example-event-happen-by-2030
```

Without `--fixture`, `market get` resolves a slug through Gamma. An identifier
starting with `0x` is resolved through the public CLOB market-info route.

## Live market data for agents

These commands make bounded, unauthenticated public reads and return compact,
decision-ready numbers. Use them between discovery and simulation.

### Quote a market

```bash
uv run python -m pm_trader quote xi-jinping-out-before-2027
uv run python -m pm_trader quote xi-jinping-out-before-2027 --outcome Yes --depth 10
```

The result has the market's trading rules (`tick_size`, `min_order_size`,
`neg_risk`, `fees_enabled`, `fee_schedule`), outcomes with token ids, and one
book summary per outcome (or only `--outcome`): `best_bid`, `best_ask`,
`midpoint`, `spread`, level counts, best-first `bids`/`asks` up to `--depth`
(1-50, default 5), and `depth_near_mid` (shares and notional within 1c and 5c of
the midpoint). Book timestamps are ISO-8601 UTC.

### Price history

```bash
uv run python -m pm_trader prices xi-jinping-out-before-2027 --outcome Yes \
  --interval 1w --fidelity 60 --max-points 120
```

Returns `open`, `close`, `high`, `low`, `change`, `start`/`end`, the raw point
count, and an evenly downsampled `points` series (`--max-points`, 1-1000,
always including the last point). Intervals: `1h`, `6h`, `1d`, `1w`, `1m`,
`max`, `all`. History is context, not an executable price.

### Paper portfolio

```bash
uv run python -m pm_trader --data-dir .pm-trader portfolio
uv run python -m pm_trader --data-dir .pm-trader portfolio --mark
```

Aggregates every stored paper fill in the data directory into net positions
per outcome token: `quantity`, `average_price`, `cost_basis`, `realized_pnl`
(average cost, fees excluded), `fees`, and `fills`. `--mark` reads the live
book for up to 25 open positions and adds `mark_price` (midpoint),
`market_value`, and `unrealized_pnl`. Each simulation is still an independent
run with its own starting cash; the portfolio is a read-only roll-up across
them.

### MCP tools

The MCP server exposes the same data as `quote_market`, `price_history`, and
`paper_portfolio`. `discover_markets` accepts `order`, `ascending`, and
`open_only`; `inspect_market`, `inspect_book`, and `simulate` accept a live
`market` (slug or condition id) in place of fixture paths.

## Inspect normalized state

Inspection is fixture-based and does not make a network request:

```bash
uv run python -m pm_trader inspect market \
  --fixture fixtures/gamma_markets_keyset_page1.json

uv run python -m pm_trader inspect book \
  --fixture fixtures/clob_book.json
```

Market inspection returns canonical identifiers, outcomes, token mappings,
lifecycle state, minimum size, tick size, neg-risk, fee information, timestamps,
unknown fields, freshness, and provenance. Book inspection returns the normalized
bids, asks, identity, timestamp, hash, continuity, and provenance.

The market fixture contains a `Yes` token named `token-general-yes`, which is used
by the simulation examples below.

## Simulate a paper order

`simulate` matches one paper order against one book and persists the report in
the selected SQLite data directory. Pass `--market` to match against the live
public order book:

```bash
uv run python -m pm_trader --data-dir .pm-trader simulate \
  --market xi-jinping-out-before-2027 --outcome Yes \
  --side buy --price 0.02 --size 100 --tif fak --order-id live-1
```

Or pass both fixture options for an offline, reproducible run:

```bash
uv run python -m pm_trader --data-dir .pm-trader-demo simulate \
  --market-fixture fixtures/gamma_markets_keyset_page1.json \
  --book-fixture fixtures/clob_book.json \
  --order-id demo-fak-1 \
  --side buy \
  --outcome Yes \
  --price 0.67 \
  --size 40 \
  --tif fak \
  --starting-cash 1000 \
  --latency-ms 0 \
  --dataset-id demo-book-v1
```

The response includes `result.run_id`, matches, decisions, fills, and a report.
The report includes cash, positions, exposure, fees, separately labeled rebate
estimates, realized/unrealized P&L, data coverage, assumptions, uncertainty, and
a checksum.

Available simulation options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--market` | unset | Live market slug or `0x` condition id; fetches the live book. |
| `--market-fixture` / `--book-fixture` | unset | Offline market and book JSON; use both instead of `--market`. |
| `--order-id` | `cli-order-1` | Stable paper-order identifier. |
| `--side` | `buy` | `buy` or `sell`. |
| `--outcome` | `Yes` | Outcome label or token resolved from the market fixture. |
| `--price` | `0.66` | Decimal limit price. |
| `--size` | `10` | Decimal order quantity. |
| `--tif` | `fok` | `gtc`, `gtd`, `fak`, or `fok`. |
| `--post-only` | off | Reject if the paper order would immediately take liquidity. |
| `--starting-cash` | `10000` | Paper cash available for settlement. |
| `--latency-ms` | `0` | Declared paper latency before matching. |
| `--dataset-id` | unset | Optional dataset label included in run identity and coverage. |

Matching is fail-closed. Closed or inactive markets, stale or inconsistent books,
identity mismatches, invalid tick/size values, missing fee schedules, insufficient
paper cash, and insufficient paper positions produce explicit decisions rather
than silent fills.

## Read a stored report

Copy the `result.run_id` from a successful simulation or replay, then use the same
data directory:

```bash
uv run python -m pm_trader --data-dir .pm-trader-demo report \
  --run-id '<run_id>'
```

If the run is not present, the command returns `ok: false` with an
`errors` entry whose code is `REPORT_NOT_FOUND`.

## Replay a recorded JSONL dataset

`replay` consumes a bounded JSONL recording and replays its normalized book
snapshots. Non-book stream rows are not used as executable books, but recorded
gap/resynchronization metadata is surfaced as warnings. Replay uses the supplied
market fixture and one paper order:

```bash
uv run python -m pm_trader --data-dir .pm-trader-demo replay \
  --dataset data/example-books.jsonl \
  --market-fixture fixtures/gamma_markets_keyset_page1.json \
  --order-id replay-fok-1 \
  --side buy \
  --outcome Yes \
  --price 0.66 \
  --size 10 \
  --tif fok \
  --starting-cash 10000
```

The CLI does not have a recording subcommand. Create a JSONL dataset with the
library `Recorder`, or run the complete temporary workflow:

```bash
uv run python -m examples.discovery_to_replay
```

That example records a fixture-backed book, simulates it, replays it, and prints
compact run IDs and checksums. Its temporary JSONL file is deleted when the
example exits. To retain a dataset for later CLI replay, a minimal recording
script is:

```python
from pathlib import Path

from pm_trader.recording import Recorder
from pm_trader.storage import SQLiteStore
from pm_trader.workflows import load_book_fixture

store = SQLiteStore(".pm-trader-recording")
try:
    recorder = Recorder(store, dataset_id="example-books-v1")
    recorder.record_book(load_book_fixture(Path("fixtures/clob_book.json")))
    recorder.export_jsonl(Path("data/example-books.jsonl"))
finally:
    store.close()
```

Run the script from the repository root before invoking `replay`. Recordings are
sanitized and bounded; the default limits are 50 MiB, 100,000 observations, and
100,000 replay events.

## Explain a decision

`explain` formats a structured decision explanation. It does not rerun matching:

```bash
uv run python -m pm_trader explain \
  --status rejected \
  --reason-code STALE_BOOK \
  --reason "book timestamp exceeded the declared tolerance" \
  --freshness stale
```

Repeat `--reason` for multiple reasons. Common reason codes include `FILLED`,
`PARTIAL_FILL_REMAINDER_CANCELLED`, `PRICE_NOT_CROSSED`, `FOK_NOT_FILLED`,
`POST_ONLY_WOULD_TAKE`, `STALE_BOOK`, `TICK_SIZE_VIOLATION`,
`MINIMUM_SIZE_VIOLATION`, `INSUFFICIENT_CASH`, and `UNKNOWN_FEE_SCHEDULE`.

## Output and errors

For valid invocations, exit status has this meaning:

| Exit status | Meaning |
| --- | --- |
| `0` | The workflow completed with `ok: true`. Warnings can still be present. |
| `1` | The envelope is `ok: false`, including a missing report or a response that cannot fit the size cap. |
| `2` | Provider, network, malformed-payload, or local-validation failure. |

`--help` uses standard `argparse` text output. Execution responses remain JSON and
are capped at 64 KiB. A discovery page that does not fit returns the longest
prefix that does, withholds `next_cursor`, and warns to retry with a smaller
`--limit`. A response that still cannot fit becomes a structured
`RESPONSE_SIZE_LIMIT` error and exits `1`. `safety` reports the paper-only
boundary and does not create a data directory.

Warnings are data, not decoration. In particular, review freshness, provenance
IDs, assumptions, data coverage, and uncertainty before interpreting a fill or
P&L value.

## Full offline path

For a single command that demonstrates discovery, inspection, simulation, and
replay without network access:

```bash
uv run python -m examples.discovery_to_replay
```

For individual steps, use the fixture commands in this guide. The fixture-backed
workflow is reproducible, but it remains a paper model over dated fixture data,
not a claim about current provider behavior or live execution.
