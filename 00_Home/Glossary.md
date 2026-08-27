---
type: glossary
project: polymarket-paper-trader
updated: 2026-08-26
---

# Glossary

| Term | Meaning in this project |
| --- | --- |
| CLOB | Central Limit Order Book API and matching venue used by Polymarket. |
| CLOB V2 | Current CLOB contract/API generation described by Polymarket documentation; includes pUSD collateral and match-time fee behavior. |
| Gamma API | Public market and event metadata API, including discovery and market configuration. |
| RTDS | Real-Time Data Socket for provider streams such as crypto prices, comments, and sports-related data. |
| Data API | Public data surface for activity, positions, closed positions, trades, value, open interest, and holders. |
| condition ID | Identifier for a prediction-market condition/event grouping. |
| token ID / asset ID | Identifier for a tradable outcome token. |
| neg-risk | Market configuration in which outcome positions can participate in a negative-risk multi-outcome structure. |
| feeSchedule | Market metadata describing dynamic fee rate, exponent, taker-only behavior, and possible rebate rate. |
| maker | An order that adds liquidity to the book and is not charged the taker fee under the current fee model. |
| taker | An order that removes liquidity by matching against resting liquidity. |
| post-only | Order instruction that requires the order to add liquidity rather than immediately take it. |
| FOK | Fill-or-kill: fill the requested amount immediately or cancel it. |
| FAK | Fill-and-kill: fill what is immediately available and cancel the remainder. |
| GTC | Good-til-canceled order. |
| GTD | Good-til-date order. |
| heartbeat | Periodic liveness signal associated with order-management safety behavior. |
| keyset pagination | Cursor-based pagination for stable traversal of large market or event collections. |
| book snapshot | A point-in-time bid/ask representation, including identity and freshness metadata. |
| event stream | Ordered provider messages such as book changes, trades, best bid/ask, and market lifecycle events. |
| replay | Re-running a simulation from recorded observations with a declared clock and latency model. |
| paper order | An order stored and matched only inside this simulator; it has no provider-side effect. |
| fill ledger | Append-only record of simulated executions, quantities, prices, fees, rebates, and provenance. |
| maker rebate | A programmatic reward distributed to eligible liquidity providers; paper estimates are not guaranteed payouts. |
| pUSD | Current collateral unit referenced by CLOB V2 documentation. |

