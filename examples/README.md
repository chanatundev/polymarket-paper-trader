# Offline workflow example

`discovery_to_replay.py` runs a complete fixture-backed path:

1. discover one market through the cursor adapter;
2. inspect a canonical market and full-depth book;
3. record the book with raw/normalized provenance;
4. simulate a paper order; and
5. replay the recorded JSONL dataset and print compact checksums.

Run it from the repository root:

```bash
uv run python -m examples.discovery_to_replay
```

The example uses `FixtureTransport`, so it makes no network request and does
not create a live order. The resulting fills and P&L are model outputs only.
