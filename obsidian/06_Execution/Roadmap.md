---
type: roadmap
project: polymarket-paper-trader
updated: 2026-08-27
---

# Roadmap

## Phase 0 — Contract and fixtures

Create provider fixtures from current public response shapes, define canonical models, list legacy compatibility decisions, and add negative fixtures for missing/invalid fields.

**Exit:** schema contract and safety boundary reviewed; no implementation defaults unknown fee/state data to safe-looking values.

## Phase 1 — Current-data adapters

Update Gamma routes and keyset/search handling; normalize current market and CLOB fields; add explicit CLOB V2/fee-schedule metadata; preserve raw responses and provider errors.

**Depends on:** Phase 0.

**Exit:** contract tests pass for current and selected legacy fixtures; live read-only smoke checks are informative and bounded.

## Phase 2 — Matching and ledger fidelity

Implement a standalone order-book matcher, order lifecycle, order types, tick/minimum-size validation, dynamic fees, rebate estimates, and append-only portfolio ledger.

**Depends on:** Phase 1 canonical models.

**Exit:** golden and property tests cover partial fills, FAK/FOK, GTC/GTD, post-only, stale books, fee rounding, and conservation invariants.

## Phase 3 — Record and replay

Add bounded WebSocket/REST recording, raw/normalized provenance, reconnect resynchronization, deterministic replay, and report checksums.

**Depends on:** Phases 1–2.

**Exit:** an identical fixture produces the same ledger and report across repeated runs; gaps and resyncs are visible.

## Phase 4 — Agent workflow

Improve CLI/MCP operations, structured explanations, compact reports, and examples for discovery-to-replay workflows.

**Depends on:** Phases 1–3.

**Exit:** an agent can discover, inspect, simulate, replay, and explain a run without reading internal SQLite tables.

## Phase 5 — Local trade-history GUI

Implement the approved lightweight GUI for reviewing stored canonical paper fills and compatible legacy trades. Add a bounded read model/query service, friendly metadata enrichment where the canonical record lacks market labels, a loopback-only static browser surface, and a `pm-trader gui` launch path. Keep the GUI strictly read-only and local.

**Depends on:** Phases 0–4, [[../09_Decisions/Decision_Log|Decision D-005]], and the [[../04_Design/Trade_History_GUI|GUI design]].

**Exit:** a seeded SQLite store can be opened through the GUI; a reviewer can filter and select a past trade; detail shows available order/fill/ledger/report/provenance/assumption data; bounds, empty/error states, keyboard access, and no-write/no-provider behavior are tested.

## Phase 6 — Optional live read-only operations

Consider a live read-only monitor or alert layer for freshness and market changes only after the GUI and all prior acceptance criteria pass. Do not add authenticated writes by implication.

**Depends on:** all prior acceptance criteria and a new product/security decision.
