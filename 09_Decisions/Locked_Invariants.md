---
type: locked-invariants
project: polymarket-paper-trader
updated: 2026-08-26
---

# Locked Invariants

These invariants are the current design baseline. Changing one requires a new decision-log entry and updated acceptance criteria.

1. **Paper-only first.** No live order, cancellation, redemption, signing, or fund movement is part of the initial product.
2. **Unknown is not zero.** Missing fee, price, state, timestamp, or book metadata cannot silently become a safe-looking numeric default.
3. **Provider data is not domain state until normalized.** Provider-specific routes and field names stay inside adapters.
4. **Every fill is explainable.** A fill links to the source observation, clock, matching assumptions, fee policy, and model version.
5. **Stale/gapped state is visible.** The engine cannot treat an unverified stream as authoritative after a disconnect or sequence gap.
6. **Paper P&L is not live P&L.** Reports must show data coverage and modeling assumptions.
7. **Fees and rebates are separate.** A rebate estimate never silently reduces a fee or cash requirement.
8. **Replay is deterministic by declaration.** Dataset, seed, clock, latency, queue policy, and model versions are part of run identity.
9. **No credentials in research artifacts.** Fixtures and reports are sanitized and contain no secrets or private wallet data.

