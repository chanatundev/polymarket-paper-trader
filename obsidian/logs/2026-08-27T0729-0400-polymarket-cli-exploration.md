---
type: validation-log
project: polymarket-paper-trader
run_started: 2026-08-27T11:29:10Z
scope: public-read-only-paper-only
---

# Polymarket CLI near-term market exploration

## Bounded outcome

The live discovery probe was run at system time `2026-08-27T11:29:10Z`
(`2026-08-27T07:29:10-0400`). The requested two-hour end-time cutoff was
`2026-08-27T13:29:10Z`. No eligible market was verified, because the CLI
returned a response-size failure before exposing any market records or
provenance. This is not evidence that no eligible markets existed.

No prediction or paper simulation was completed. The live result contained no
market identifier, token, end time, or executable book to use. The local
fixture market was not eligible for this window, so it was not used as a
substitute for a live near-term market.

## Documentation and CLI inspection

Read-only inspection covered `README.md`, `docs/CLI.md`, the CLI dispatch, the
public API adapter, the market normalizer, the paper simulation workflow, and
the project-local `obsidian/` layout. The documented safest workflow is:

1. Run `safety` and confirm `mode: "paper_only"` with all live-write,
   signing, cancellation, redemption, and fund-movement flags false.
2. Use `discover` or `market list/search/get` for public metadata reads.
3. Keep live discovery separate from fixture/recorded-book simulation.
4. Run `simulate` only with an explicit local market fixture and book fixture,
   in a dedicated data directory, and treat fills, fees, and P&L as model
   outputs.

The checkout was clean on `main` before the run. Runtime was Python `3.14.7`.
No credentials, private keys, wallets, signing, trading, order placement,
cancellation, settlement, redemption, or account/provider mutation was used.

## Exact CLI commands and evidence

Working directory for all commands was:

`/Users/chanatun/code/projects/polymarket-paper-trader`

Help and command-surface probes:

```text
python3 -m pm_trader --help
python3 -m pm_trader discovery --help 2>&1 || true
python3 -m pm_trader markets --help 2>&1 || true
python3 -m pm_trader paper --help 2>&1 || true
python3 -m pm_trader simulate --help 2>&1 || true
python3 -m pm_trader replay --help 2>&1 || true
PYTHONDONTWRITEBYTECODE=1 python3 -m pm_trader discover --help
PYTHONDONTWRITEBYTECODE=1 python3 -m pm_trader market list --help
PYTHONDONTWRITEBYTECODE=1 python3 -m pm_trader market search --help
PYTHONDONTWRITEBYTECODE=1 python3 -m pm_trader market get --help
```

`discovery` and `paper` are not top-level commands; argparse rejected them
with exit code `2`. The documented command names are `discover` and
`simulate`. `markets --help` is accepted as the alias for `market`.

Safety probe:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pm_trader safety
```

Observed JSON (exit code `0`):

```json
{"assumptions":{"authenticated_execution":"out_of_scope"},"errors":[],"freshness":"unknown","ok":true,"provenance_ids":[],"result":{"cancellation":false,"fund_movement":false,"live_writes":false,"mode":"paper_only","redemption":false,"signing":false},"warnings":[]}
```

The first safety probe used the default data directory, `.pm-trader`, which
already existed and was ignored by Git. Source inspection shows that the CLI
opens/initializes `SQLiteStore(args.data_dir)` before dispatching `safety`; this
is a local store initialization side effect even though the reported provider
capability boundary is immutable. Later probe commands used only the temporary
directory inside this log scope.

Bounded live discovery probe:

```text
run_dir='obsidian/logs/.cli-probe-20260827T0730-0400'
mkdir -p "$run_dir"
PYTHONDONTWRITEBYTECODE=1 python3 -m pm_trader --data-dir "$run_dir" discover --limit 100 > "$run_dir/keyset-page-1.json"
```

The CLI completed in about `1.2` seconds with `cli_exit=0`. Its complete
stdout file contained exactly:

```json
{"assumptions":{},"errors":[{"code":"RESPONSE_SIZE_LIMIT","message":"response exceeded configured CLI size limit"}],"freshness":"unknown","ok":false,"provenance_ids":[],"result":null,"warnings":[]}
```

The command reached the CLI response-size guard rather than a network timeout
or rate-limit error. The attempted provider source was Gamma's public
`/markets/keyset` route, with the adapter's query `limit=100&ascending=true`;
the CLI's configured response bound is `64 KiB`. Because the final envelope
had `result: null` and `provenance_ids: []`, no live market payload could be
used to determine end times.

The bounded post-processing command was:

```text
jq -r '.result.markets[] | [.condition_id, .slug, .question, (.unknown_fields.endDateIso // .unknown_fields.endDate // .unknown_fields.end_date // "<end-date-not-preserved>"), (.provider_timestamp // "<provider-timestamp-none>"), (.active // "unknown"), (.closed // "unknown"), (.accepting_orders // "unknown"), (.enable_order_book // "unknown")] | @tsv' "$run_dir/keyset-page-1.json" | sed -n '1,120p'
```

It failed locally with `Cannot iterate over null (null)` because the CLI error
envelope has no `result.markets`; this was not an additional provider call.

## Market identifiers, end times, and data sources

### Live provider

No live market identifier or stated end time was surfaced. Therefore:

- eligible `<2h` markets verified: none;
- live predictions: none;
- live CLOB book or fee data: not requested;
- current market availability and provider rate-limit status: not verified.

### Local fixtures

Read-only fixture inspection found the following non-eligible reference:

| Source | Condition/asset | Stated end or observation time | Eligibility |
| --- | --- | --- | --- |
| `fixtures/gamma_markets_keyset_page1.json` | `0xgeneral-event-001` / `will-the-example-event-happen-by-2030` | `endDateIso=2030-12-31T23:59:59Z` | Outside the two-hour window |
| `fixtures/clob_book.json` | `asset_id=token-general-yes` | book timestamp `2026-08-26T20:00:10Z` | Dated fixture; not a live near-term book |

The fixture market was not used for a prediction or simulation. No eligible
live market was available to connect to it, and using it would have created a
misleading current-market experiment.

## Findings and recommendations

| ID | Observed behavior and why it matters | Classification | Impact on a paper-trading user | Concrete recommendation |
| --- | --- | --- | --- | --- |
| F-001 | A valid maximum `discover --limit 100` request produced the structured `RESPONSE_SIZE_LIMIT` error, so no markets were exposed. The documented page-size bound and the response-size bound interact in a way that is not obvious from help. | Usability problem; documentation gap; response-safety behavior | A user cannot scan current markets or apply an end-time filter even though the public discovery request reached the CLI successfully. | Add a response-size-aware default/cap or a compact summary mode; provide an actionable error suggesting a smaller limit; add a bounded end-time/eligibility filter whose output is guaranteed to fit the envelope. |
| F-002 | The CLI exited `0` while returning `ok: false`, `result: null`, and `RESPONSE_SIZE_LIMIT`. | Usability problem; automation/documentation gap | Shell automation may treat the failed discovery as successful and silently record zero markets. | Return a nonzero status for `ok: false` execution failures, or explicitly document that structured failures can exit `0` and provide a stable failure-status contract. |
| F-003 | `MarketState` has no first-class end-time field. The normalizer prioritizes `updatedAt` for `provider_timestamp`; `endDateIso` is retained as an unmodeled provider field rather than a canonical end time. This was established by source/fixture inspection, not live output. | Data limitation; model/documentation gap | Reliable “ends within two hours” selection requires provider-specific field knowledge and cannot safely rely on the canonical market record. | Add canonical `start_time`/`end_time` fields with documented provider aliases, preserve raw provenance, and add tests for end-time filtering and missing/ambiguous timestamps. |
| F-004 | `safety` reports no live writes but the CLI constructs `SQLiteStore` before handling the command. The default safety probe therefore opens/initializes the existing ignored `.pm-trader` store. | Usability/scope surprise; expected provider-safety behavior | A user expecting a purely observational capability check may get a local SQLite side effect or a new default store. | Dispatch `safety` before persistent-store construction, or force a nonpersistent in-memory store; document that the capability check has no provider writes but may initialize local state until fixed. |
| F-005 | The paper simulation path requires explicit local market and book fixtures; there is no CLI command that directly turns a live discovery response into an executable simulation book. | Expected paper-only safety boundary; workflow/data limitation | This prevents accidental live execution, but it also means a discovery user may expect a next step that cannot be completed from the current CLI alone. | Document the live-discovery-to-recorded-fixture handoff explicitly and keep the boundary visible; add a future read-only recording workflow only with bounded provenance and no write-capable provider path. |
| F-006 | Intuitive probes `discovery` and `paper` are rejected, while the canonical names are `discover` and `simulate`. | Expected command-surface behavior; low-severity usability issue | A new user may spend time probing invalid names, though the help/docs use the canonical names. | Keep examples consistent and optionally mention the canonical names prominently in top-level help; no safety change is required. |

## Explicit boundaries and verification limits

### Public/read-only

- The only live provider operation attempted was the CLI's unauthenticated
  Gamma market-discovery GET through `discover --limit 100`.
- Help and safety commands were local.
- No CLOB, Data API, WebSocket, RTDS, authenticated, or provider-write
  operation was attempted.

### Paper-only

- No paper order was created, matched, canceled, or settled in this run.
- No prediction, paper fill, fee, rebate, P&L, or performance claim was made.
- The local fixture was deliberately not treated as current market data.

### Not verified

- No eligible live `<2h` market was verified because the CLI discarded the
  oversized discovery response.
- No live market end time, lifecycle state, CLOB book, fee schedule, liquidity,
  prediction quality, or provider current availability was verified.
- The response-size failure is not evidence of a Gamma timeout or rate limit.

The disposable probe directory and its SQLite store were removed after the
evidence was captured. No source code, tests, implementation notes, other
Obsidian notes, configuration, dependencies, or unrelated worktree changes
were modified.
