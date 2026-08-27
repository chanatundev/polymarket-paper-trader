---
type: decision-log
project: polymarket-paper-trader
updated: 2026-08-26
---

# Decision Log

## D-001 — Build a paper-research workbench before live execution

- **Date:** 2026-08-26
- **Status:** Accepted for planning
- **Decision:** Prioritize current-data normalization, event-driven simulation, recording/replay, and agent-facing explanations. Keep authenticated writes out of v0.x.
- **Reason:** The source's largest opportunity is fidelity and reproducibility; live execution would introduce a materially different security and operations scope.
- **Evidence:** [[../01_Research/Sources|source audit and official research]]; [[../02_Strategy/Project_Thesis|thesis]].

## D-002 — Preserve the source's Python, SQLite, CLI, and MCP shape initially

- **Date:** 2026-08-26
- **Status:** Accepted for planning
- **Decision:** Extend the existing interfaces where practical instead of rewriting the product surface before the domain model is proven.
- **Reason:** This keeps migration cost bounded and preserves the source's agent-oriented affordances while isolating new provider/model behavior.

## D-003 — Use a provider/domain normalization boundary

- **Date:** 2026-08-26
- **Status:** Accepted for planning
- **Decision:** Provider adapters own route/field compatibility; the matcher and ledger consume versioned canonical records.
- **Reason:** Current API changes affect routes, pagination, metadata, fee schedules, and stream semantics at different rates.

## D-004 — Treat current public API behavior as fixture-backed, not permanent

- **Date:** 2026-08-26
- **Status:** Accepted for planning
- **Decision:** Date probes, retain sanitized fixtures, and require contract tests before changing adapters.
- **Reason:** A live response is useful evidence but not a stable contract.

