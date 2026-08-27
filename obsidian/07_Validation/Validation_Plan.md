---
type: validation-plan
project: polymarket-paper-trader
updated: 2026-08-27
---

# Validation Plan

## Test layers

| Layer | What it proves | What it does not prove |
| --- | --- | --- |
| Unit tests | Local parser, fee, matcher, ledger, and explanation behavior | Current provider behavior outside fixtures |
| Contract fixtures | Known public payload shapes and compatibility decisions | Future schema stability |
| Property tests | Invariants such as no overfill, monotonic cash/position accounting, and tick alignment | Real queue placement or profitability |
| Replay tests | Determinism and provenance for a declared dataset/configuration | That the dataset represents all live liquidity |
| Bounded live read-only probes | Current endpoint reachability and response shape | Safety of writes, future availability, or trading results |
| Static/negative checks | No secret/signing/live-write path in paper mode | Human operational behavior outside the repository |
| GUI integration | Bounded local list/detail behavior over seeded canonical and legacy records | Live provider behavior, visual quality beyond the tested/manual review surface |
| Human review | Paper-only boundary, assumptions, and report honesty | Automatic correctness of all provider behavior |

## Required scenarios

- keyset pagination advances and terminates without offset assumptions;
- event-by-slug and public search normalize to the same event identity model;
- a current CLOB book preserves identity, timestamp/hash, tick, minimum size, and last trade fields;
- a missing fee schedule produces `unknown` or an explicit policy rejection, never a silent zero fee;
- dynamic fee rounding matches golden fixtures;
- maker and taker paths differ correctly;
- partial fills consume levels in declared price-time order;
- FAK and FOK leave the correct remainder state;
- post-only rejects a crossing order;
- GTC/GTD expiry and cancel are deterministic;
- stale, gapped, closed, inactive, or non-accepting market state cannot produce an eligible simulated order unless policy explicitly allows it;
- recorder reconnects from an authoritative snapshot after a gap;
- repeated replay produces identical ledger/report output;
- paper mode has no live write or signing side effect.
- canonical fills and legacy trades normalize into a bounded newest-first GUI history view;
- selecting a GUI row shows the required execution, accounting, provenance, warning, and assumption fields without silently filling unknowns;
- GUI filters, missing records, malformed local data, and response limits produce safe structured states;
- GUI routes are GET-only, make no provider calls, accept no credentials, and do not mutate SQLite;
- GUI output escapes stored text and remains keyboard-navigable with status meaning that is not color-only.

## Phase 5 local GUI evidence

The implemented GUI slice is covered by [[GUI_Smoke_2026-08-27|the 2026-08-27
smoke review]] and `tests/test_history_gui.py`. The tests seed canonical fills
and compatible legacy trades, verify the bounded normalized list/detail view,
exercise filters and cursor pagination, prove immutable SQLite reads and no
database creation for missing stores, reject write-like methods, and assert
that stored text is inserted through `textContent` rather than HTML parsing.
The browser review checked the persistent safety banner, detail sections,
empty/clear filter states, responsive layout, keyboard-focusable row controls,
and text status cues.

## External and human gates

- Verify current provider behavior with bounded read-only requests immediately before release.
- Review official documentation changes for CLOB V2, fees, pagination, and real-time channels.
- Review whether any research dataset contains personal, credential, or wallet-sensitive data before sharing it.
- Have a human review the paper-only boundary and the wording of any strategy-performance report.
- Manually review the GUI's paper-only/read-only banner, keyboard access, focus order, empty/error states, and clarity of unknown/provenance labels.
