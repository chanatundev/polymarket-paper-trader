---
type: rejected-ideas
project: polymarket-paper-trader
updated: 2026-08-27
---

# Rejected Ideas

## R-I-001 — Add authenticated live execution as the next feature

Rejected for the initial scope. It changes the risk, security, operational, and approval profile before the simulator has demonstrated truthful data and reproducible fills.

## R-I-002 — Keep the legacy fee calculation and rename the field

Rejected. Current fee metadata and match-time behavior require an explicit schedule/model boundary, rounding tests, and unknown-state handling.

## R-I-003 — Treat midpoint/price history as full-depth historical books

Rejected. Price history can support context and some strategies, but it cannot prove the depth, queue, or fill opportunity required for full-fidelity replay.

## R-I-004 — Hide provider incompatibilities behind zero/default values

Rejected. Silent defaults can turn missing fees, prices, or state into false confidence and make agent decisions unauditable.

## R-I-005 — Rewrite all interfaces before proving the core model

Rejected for now. The existing Python, SQLite, CLI, and MCP shape provides a useful migration surface; redesign only after the canonical domain model and vertical slice expose real needs.

## R-I-006 — Turn the trade-history GUI into a live trading dashboard

Rejected for this slice. Live provider refresh, alerts, credentials, order controls, and authenticated execution would change the security and operational boundary. The requested GUI remains a local read-only view of stored paper history; live monitoring requires a separate decision and release evidence.
