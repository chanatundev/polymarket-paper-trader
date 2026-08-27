---
type: decision-log
project: polymarket-paper-trader
updated: 2026-08-27
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

## D-005 — Add a local read-only trade-history GUI

- **Date:** 2026-08-27
- **Status:** Accepted and implemented
- **Decision:** Add a lightweight loopback-only browser GUI that reads bounded canonical paper fills and compatible legacy trades from the selected local SQLite store. Provide list/filter/detail views for execution, accounting, provenance, warnings, and assumptions. Keep the surface GET-only and separate from provider adapters and all order controls.
- **Reason:** A human reviewer should be able to inspect past paper trades and understand their evidence without learning private SQLite tables, while the existing CLI/MCP surface remains the agent-facing interface.
- **Tradeoffs:** A small local HTTP/static surface adds a UI and read-model maintenance cost, but avoids a desktop wrapper and keeps the core dependency-free. Friendly labels may require non-destructive metadata capture; missing values remain explicit unknowns.
- **Evidence:** User request on 2026-08-27; [[../04_Design/Trade_History_GUI|trade-history GUI design]]; [[../03_Requirements/Requirements|FR-016–018]]; `pm_trader/history.py`; `pm_trader/gui.py`; `tests/test_history_gui.py`; [[../07_Validation/GUI_Smoke_2026-08-27|GUI smoke review]].
