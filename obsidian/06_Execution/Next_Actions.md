---
type: next-actions
project: polymarket-paper-trader
updated: 2026-08-27
---

# Next Actions

1. **Define the GUI read model.** Map canonical fills and legacy trades into one bounded history/detail contract, including a stable display ID, source kind, market labels when persisted, and explicit unknowns when they are not.
2. **Add safe read access.** Extend the SQLite store/query service with allow-listed filters, newest-first pagination, detail joins, and response bounds. Keep the UI independent of private table layout and prevent arbitrary SQL.
3. **Preserve reviewer context.** Ensure canonical simulation records retain or expose market/outcome labels, order status, ledger/report links, provenance IDs, timestamps, model versions, queue/latency assumptions, and warnings needed by the detail view.
4. **Build the local GUI.** Add static HTML/CSS/JavaScript and a `pm-trader --data-dir .pm-trader gui` loopback launcher with list, filter, selection/detail, loading, empty, error, and paper-only/read-only states.
5. **Validate the GUI slice.** Seed canonical and legacy fixtures; test list/detail correctness, filters, bounds, keyboard navigation, HTML escaping, no provider calls, no SQLite mutation, and no write-like route.
6. **Update user-facing run instructions.** Document the launch command, data-directory behavior, supported filters, displayed evidence boundaries, and the fact that paper fills/P&L are model outputs.
7. **Complete release gates.** Keep bounded current-provider probes, dataset-sharing review, and human review of paper-only/report wording open until evidence is recorded.

## Immediate implementation slice

The smallest useful GUI slice is: one seeded simulation store -> bounded trade-history list -> selected trade detail -> linked report/ledger/provenance context. It should not require a WebSocket recorder, provider access, credentials, or a new frontend build service.
