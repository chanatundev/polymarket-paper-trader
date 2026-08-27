---
type: project-goals
project: polymarket-paper-trader
updated: 2026-08-26
---

# Goals

## P0 — Restore truthful current-data behavior

- Normalize current Gamma and CLOB response shapes without silently dropping important fields.
- Represent market lifecycle, order-book enablement, accepting-orders state, minimum order size, tick size, neg-risk, and freshness explicitly.
- Replace legacy fee assumptions with a versioned dynamic-fee model and clear unknown states.

## P1 — Make simulations reproducible

- Record raw and normalized observations with timestamps, source, request parameters, and schema version.
- Replay books and events deterministically with configurable latency, queue assumptions, and clock behavior.
- Produce an append-only order/fill/fee/rebate/portfolio ledger with provenance.

## P2 — Make agent research efficient

- Preserve and improve CLI/MCP discovery, inspection, simulation, and reporting tools.
- Add structured “why not tradable,” “why not filled,” and “what data is stale” explanations.
- Provide compact reports that an agent can inspect without loading raw datasets.

## P3 — Keep the safety boundary legible

- Make paper-only mode the default and enforce it in code paths and configuration.
- Keep credentials, signing, order submission, cancellation, redemption, and live fund movement out of the initial implementation.

