---
type: project-overview
project: polymarket-paper-trader
status: phase-5-gui-implemented-pending-release-gates
updated: 2026-08-27
---

# Project Overview

## Purpose

Create a safer and more faithful successor to [agent-next/polymarket-paper-trader](https://github.com/agent-next/polymarket-paper-trader): an agent-accessible environment for discovering markets, observing current Polymarket data, replaying recorded books, simulating fills and fees, evaluating strategies, and giving a human a small local view of past paper trades without submitting real orders.

## Source baseline

- Clone: `/tmp/polymarket-paper-trader.2Yr325/repo`
- Branch and revision: `main` at `e4263988a8a0775b61f41857184a3b693beea40f0`
- Substantive feature burst audited: late February through early March 2026
- Existing interfaces worth preserving: Python package, CLI, MCP server, SQLite persistence, backtest entry point, and unit/e2e test style
- Audited source changes: none made in the original source clone
- Successor implementation checkout: `/Users/chanatun/code/projects/polymarket-paper-trader`

## Current state

The successor core and the local trade-history GUI are implemented in the selected checkout. The original source audit found compatibility and fidelity gaps against current Polymarket documentation and live public responses:

- current Gamma uses `/markets/keyset`, `/events/slug/{slug}`, and `public-search` capabilities that are not represented by the existing adapters;
- current market metadata exposes `feeSchedule`, `feesEnabled`, order minimums, tick sizes, and trading-state fields that the source model largely drops;
- current CLOB responses include book identity and freshness fields, while the source retains only bids and asks;
- current fee behavior is dynamic and match-time based, so a single legacy basis-point rate and average-price fee are insufficient;
- current public docs describe CLOB V2, pUSD collateral, post-only orders, heartbeats, WebSocket market streams, RTDS, and maker rebates that the source does not model end to end.

The source audit's local tests passed with `626 passed, 42 deselected` for non-live tests and `37 passed, 5 skipped` for the opt-in live e2e suite. Those results validate the checked-out source and test environment only; they do not prove current provider compatibility or future profitability.

## Proposed outcome

An event-driven, V2-aware paper simulator with:

1. normalized Gamma, CLOB, Data API, WebSocket, and RTDS observations;
2. explicit market and book freshness/state handling;
3. price-time order-book matching with partial fills and configurable latency;
4. dynamic fee, maker/taker, rebate-estimate, minimum-size, tick-size, and neg-risk handling;
5. record/replay datasets so backtests can use observed books instead of synthetic depth;
6. explainable portfolio, P&L, exposure, and fill ledgers exposed through CLI and MCP;
7. a loopback-only, read-only browser GUI for scanning stored paper fills/trades and opening their order, ledger, report, provenance, and assumption details.

## Users

- An agent or researcher exploring markets and testing hypotheses.
- A developer extending adapters, simulators, and evaluation workflows.
- A human reviewer who needs reproducible evidence for every simulated decision and wants to inspect prior paper trades without querying SQLite directly.

## Non-negotiable boundary

The initial product is paper-only. It may read public data through the research workflows, but it must not create, sign, submit, cancel, or redeem live orders or funds. The GUI reads the selected local SQLite data directory only, has no provider-write or order-control surface, and must not imply that a paper fill was live. Any future live mode requires a new decision, isolated credentials, explicit safety controls, and separate acceptance evidence.
