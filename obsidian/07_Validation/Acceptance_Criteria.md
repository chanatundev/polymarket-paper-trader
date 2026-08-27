---
type: acceptance-criteria
project: polymarket-paper-trader
status: proposed
updated: 2026-08-26
---

# Acceptance Criteria

## Current-data compatibility

- [ ] Current Gamma keyset, event-by-slug, and public-search fixtures parse with pagination and provenance intact.
- [ ] Current market and CLOB fields required by [[../03_Requirements/Requirements|requirements]] are preserved in canonical records.
- [ ] Route, schema, rate-limit, and malformed-payload errors are explicit and tested.

## Simulation fidelity

- [ ] A declared book fixture produces expected full, partial, and unfilled outcomes.
- [ ] GTC, GTD, FAK, FOK, and post-only behavior is covered by golden tests.
- [ ] Tick size, minimum size, lifecycle, accepting-orders, freshness, and neg-risk gates are explainable.
- [ ] Fees use the declared dynamic schedule, side, exponent/rate, and rounding; unknown values remain unknown.
- [ ] Rebate estimates are separate from fees and labeled with assumptions.

## Reproducibility and agent use

- [ ] Raw/normalized observations and model/config versions identify every simulated fill.
- [ ] Replaying the same dataset/configuration produces the same ledger/report checksum.
- [ ] CLI/MCP outputs are structured, bounded, and explain rejected/stale/unfilled decisions.

## Safety

- [ ] Paper mode cannot sign, submit, cancel, redeem, or move live funds.
- [ ] No credentials or sensitive wallet data appear in fixtures, logs, reports, or documentation.
- [ ] A human has reviewed the boundary between paper evidence and live-provider claims.

## Release evidence

Every checked box must link to a test result, fixture, report, or human-review record. Until then, this document remains a proposed checklist.

