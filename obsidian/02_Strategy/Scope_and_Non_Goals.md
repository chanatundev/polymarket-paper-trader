---
type: project-scope
project: polymarket-paper-trader
updated: 2026-08-27
---

# Scope and Non-Goals

## In scope

- Public Gamma, CLOB, Data API, market WebSocket, and RTDS adapters where useful for paper research.
- Versioned provider-to-domain normalization.
- Market discovery, filtering, state/tradability explanations, book snapshots, price history, and public activity context.
- Full-depth paper matching with partial fills, configurable latency, GTC/GTD/FAK/FOK semantics, post-only simulation, and tick/minimum-size validation.
- Dynamic fee and maker-rebate estimate models with explicit uncertainty.
- Recording, replay, deterministic backtesting, portfolio/P&L reporting, CLI, MCP, a local read-only trade-history GUI, and test fixtures.

## Explicitly out of scope for v0.x

- Real-money order creation, signing, submission, cancellation, or redemption.
- Wallet management, private-key handling, allowance management, or custody.
- Claims that a paper fill is equivalent to a live fill.
- Autonomous strategy deployment or unattended capital allocation.
- A general-purpose prediction model or a promise of positive returns.
- Scraping undocumented/private endpoints when an official public interface is available.
- Live-provider dashboarding, live alerts, order controls, or any GUI path that creates, edits, cancels, signs, submits, or redeems orders.

## Deferred, gated ideas

- Read-only live dashboard or alerting after replay, freshness, and a separate live-monitoring decision pass.
- Authenticated execution only after a separate decision record authorizes it and the safety review is complete.
- Specialized sports/game-state simulation after the generic event model is stable.
