---
type: project-thesis
project: polymarket-paper-trader
updated: 2026-08-26
---

# Project Thesis

## Thesis

The best successor is an event-driven, V2-aware paper-research workbench rather than a thin live-trading wrapper. Polymarket's current surface is distributed across metadata, order books, historical prices, public activity, WebSockets, RTDS, dynamic fees, and liquidity incentives. An agent needs one normalized, inspectable state model before it needs the ability to place an order.

## Why this is better

1. **Fidelity:** fills use observed depth, tick sizes, minimum sizes, market state, latency, partial execution, and dynamic fees.
2. **Reproducibility:** raw provider messages and normalized observations can be replayed with a declared clock.
3. **Safety:** paper-only side effects make experimentation cheap and keep credentials and funds out of the first product.
4. **Agent usability:** CLI and MCP operations return structured evidence, not opaque text or silently defaulted fields.
5. **Extensibility:** a versioned provider-normalization boundary makes API changes local and testable.

## Strategic bet

Research quality will improve more from truthful data and execution simulation than from increasing the number of strategy templates. The first success criterion is therefore “can a reviewer reproduce why the simulator produced this fill and P&L?” rather than “can the bot trade unattended?”

## Product posture

- Default mode: public-data paper simulation.
- Research mode: recorded-data replay with deterministic seeds and declared assumptions.
- Optional future mode: live read-only monitoring, only after paper acceptance criteria pass.
- Not a promise: profitability, execution equivalence, or protection from provider/market risk.

