---
type: next-actions
project: polymarket-paper-trader
updated: 2026-08-27
---

# Next Actions

1. **Keep the Phase 5 implementation evidence linked.** The bounded history
   read model, loopback server, static UI, CLI launcher, and tests are complete;
   see [[../04_Design/Trade_History_GUI|GUI design]],
   [[../07_Validation/GUI_Smoke_2026-08-27|GUI smoke review]], and the root
   `IMPLEMENTATION.md`.
2. **Run release review.** Keep bounded current-provider probes,
   dataset-sharing review, and human review of paper-only/report wording open;
   these are not implied by the local GUI.
3. **Consider Phase 6 only after approval.** Any live read-only monitor or
   alert layer requires a new scope decision and must preserve the paper-only
   boundary.

## Immediate implementation slice

The smallest useful GUI slice is: one seeded simulation store -> bounded trade-history list -> selected trade detail -> linked report/ledger/provenance context. It should not require a WebSocket recorder, provider access, credentials, or a new frontend build service.
