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

`--limit` is bounded to 100 for keyset pages.

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

The response preserves the query, normalized markets, events, tags, profiles,
pagination metadata, and provenance ID. Search results are bounded to 50.

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

`simulate` matches one paper order against one declared book fixture and persists
the report in the selected SQLite data directory:

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
| `0` | The requested workflow completed; inspect `ok`, `warnings`, and `errors`. |
| `1` | The command could not return the requested stored result, such as a missing report. |
| `2` | Provider, network, malformed-payload, or local-validation failure. |

`--help` uses standard `argparse` text output. Execution responses remain JSON and
are capped at 64 KiB. A response that would exceed the cap becomes a structured
`RESPONSE_SIZE_LIMIT` error instead of being partially emitted.

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
