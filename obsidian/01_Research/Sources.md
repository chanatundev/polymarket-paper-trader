---
type: research-sources
project: polymarket-paper-trader
updated: 2026-08-26
research_allowed: true
---

# Research and Sources

Research performed on 2026-08-26. Official Polymarket documentation is preferred for current provider behavior; local repository evidence is used to identify compatibility gaps. URLs and API behavior may change.

## Official Polymarket sources

- [Predictions API changelog](https://docs.polymarket.com/changelog/predictions) — records recent CLOB V2, fee, pagination, heartbeat, post-only, real-time, and operational changes.
- [CLOB V2 migration](https://docs.polymarket.com/v2-migration) — documents pUSD collateral, order-structure changes, match-time fees, builder metadata, EIP-712 versioning, order types, and SDK direction.
- [Trading fees](https://docs.polymarket.com/trading/fees) — documents dynamic fees, maker/taker treatment, five-decimal rounding, and fee schedules.
- [Maker rebates](https://docs.polymarket.com/programs/maker-rebates) — documents eligibility and distribution concepts for liquidity-provider rewards.
- [Real-time data](https://docs.polymarket.com/market-data/realtime-data) — documents market WebSocket, RTDS, event types, and heartbeat/reconnect expectations.
- [Market-data overview](https://docs.polymarket.com/market-data/overview) — maps Gamma, CLOB, and Data API responsibilities.
- [List markets with keyset pagination](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination) — documents `/markets/keyset`, cursors, and bounded page sizes.
- [Get an event by slug](https://docs.polymarket.com/api-reference/events/get-event-by-slug) — documents `/events/slug/{slug}`.
- [Search markets, events, and profiles](https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles) — documents public search.
- [Get order book](https://docs.polymarket.com/api-reference/market-data/get-order-book) — documents current book response fields.
- [Get prices history](https://docs.polymarket.com/api-reference/markets/get-prices-history) — documents historical CLOB price queries and fidelity/interval parameters.
- [Send heartbeat](https://docs.polymarket.com/api-reference/trade/send-heartbeat) — documents order-management liveness behavior.

## Local repository evidence

- Repository: `/tmp/polymarket-paper-trader.2Yr325/repo`
- Revision audited: `e4263988a8a0775b61f41857184a3b693beea40f0`
- Key source areas: `pm_trader/api.py`, `pm_trader/orderbook.py`, `pm_trader/engine.py`, `pm_trader/backtest.py`, and `pm_trader/mcp_server.py`.
- Existing substantive feature commits include order-book matching, Gamma/CLOB integration, MCP/CLI surfaces, neg-risk handling, backtesting, GTC/GTD, market discovery, tradability gates, and slippage.
- An upstream remote branch contains additional unmerged fee/order fixes; it is evidence for candidate improvements, not part of the checked-out main baseline.

## Point-in-time public probes

The following public responses were observed from the checked-out environment:

- Gamma `/markets/keyset` returned wrapper pagination and market fields including `enableOrderBook`, `orderMinSize`, `orderPriceMinTickSize`, `feeSchedule`, `feesEnabled`, `negRisk`, and rewards data.
- Gamma `/events/slug/{slug}` worked for an event slug; the legacy `/events/{slug}` form did not resolve the same way.
- Gamma `public-search` returned event/search pagination data.
- CLOB book responses included `hash`, `asset_id`, `timestamp`, `min_order_size`, `neg_risk`, `tick_size`, and `last_trade_price` in addition to bids and asks.
- CLOB `/fee-rate` returned a `base_fee` field for the sampled token, while the source parser expects the older `fee_rate_bps` shape.

These probes indicate a concrete normalization task. They do not establish a permanent API contract without versioned fixtures and contract tests.

## Research interpretation

The strongest product opportunity is a reproducible paper-research workbench: current provider adapters are valuable, but observed books, explicit fee/freshness semantics, and replayable provenance are more important than adding an authenticated order-submission path.

