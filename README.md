# Polymarket Paper Trader

This repository implements the `IMPLEMENTATION.md` plan as a paper-only public-data research workbench.

It provides:

- versioned Gamma, CLOB, Data API, and market-stream normalization;
- sanitized raw observations with provenance and bounded SQLite recording;
- freshness, lifecycle, identity, tick, minimum-size, and fee-schedule gates;
- deterministic price-time paper matching for GTC, GTD, FAK, FOK, and post-only orders;
- per-fill dynamic fees, separately labeled maker-rebate estimates, and an append-only cash/position ledger;
- bounded JSONL replay with declared clock, latency, queue policy, seed, schema, and model versions;
- JSON-first CLI and dependency-free MCP-shaped stdio operations.
- a lightweight loopback-only GUI for reviewing stored paper-trade history and linked evidence.

No package in this repository creates, signs, submits, cancels, or redeems live orders, handles private keys, or moves funds. A paper fill is a model output and paper P&L is not live performance evidence.

## Quick start

```bash
uv run python -m pm_trader inspect market --fixture fixtures/gamma_markets_keyset_page1.json
uv run python -m pm_trader simulate \
  --market-fixture fixtures/gamma_markets_keyset_page1.json \
  --book-fixture fixtures/clob_book.json \
  --price 0.67 --size 40 --tif fak

uv run python -m pm_trader --data-dir .pm-trader gui
```

Agent loop against live public data (paper fills only):

```bash
uv run python -m pm_trader discover --order volume_24hr --open-only --limit 10
uv run python -m pm_trader quote <slug>                  # book, spread, depth, rules
uv run python -m pm_trader prices <slug> --interval 1w   # recent movement
uv run python -m pm_trader simulate --market <slug> --outcome Yes --price 0.55 --size 20 --tif fak --order-id my-1
uv run python -m pm_trader simulate --market <slug> --outcome Yes --side sell --price 0.60 --size 10 --tif fak --order-id my-2
uv run python -m pm_trader portfolio --mark              # net paper exposure and P&L
```

Simulations in one data directory share a persistent paper account, so later
orders can sell earlier positions. A rejected order returns `ok: false` and
exit status 1.

The CLI returns bounded structured envelopes with `result`, `warnings`, `freshness`, `provenance_ids`, `assumptions`, and `errors`.

The GUI listens on `127.0.0.1` and reads the selected SQLite store without
creating or modifying it. It lists past canonical fills and compatible legacy
trades; paper fills, fees, and P&L remain model outputs rather than live
execution evidence. Add `--open` to open the loopback URL automatically.

See the [CLI guide](docs/CLI.md) for the complete command reference, offline
fixtures, replay preparation, output contract, and safety boundaries.

Run the complete offline discovery-to-replay example with:

```bash
uv run python -m examples.discovery_to_replay
```

## Validation

```bash
uv run --extra dev pytest
```

The dashboard pagination tests execute the production JavaScript reducer and require
Node.js (`node`) on `PATH`; the test suite fails clearly if it is not installed.

Live reads are intentionally not part of the local test suite. A release still requires a bounded read-only provider probe and human review of the paper-only boundary, as recorded in `IMPLEMENTATION.md`.
