---
type: open-questions
project: polymarket-paper-trader
updated: 2026-08-27
---

# Open Questions

## Resolved decisions

| ID | Resolution | Evidence |
| --- | --- | --- |
| OQ-001 | The selected implementation checkout is `/Users/chanatun/code/projects/polymarket-paper-trader`; the audited `/tmp` clone is read-only context. | Root `IMPLEMENTATION.md`, selected checkout |
| OQ-002 | Add a local, loopback-only, read-only GUI for stored past paper trades. Do not expand this into live dashboarding or order control. | User request on 2026-08-27; [[Decision_Log|D-005]]; [[../04_Design/Trade_History_GUI|GUI design]] |

## Remaining questions

| ID | Question | Why it matters | Default until decided |
| --- | --- | --- | --- |
| OQ-003 | Which market family should be the first recorded dataset: crypto, sports, or general events? | Determines stream/context fixtures and edge cases. | General events with one liquid market; choose before Phase 1. |
| OQ-004 | What retention limit is acceptable for raw book/event recordings? | Balances reproducibility, disk use, and privacy. | Bounded local datasets only. |
| OQ-005 | Is a maker-rebate estimate useful before provider-confirmed reward data is available? | Prevents false precision in strategy results. | Include only as visibly labeled optional output. |
| OQ-006 | Should the project support multiple fee-model versions in one replay? | May be required for historical comparisons across CLOB changes. | One declared model version per run until fixtures justify more. |
| OQ-007 | Is optional authenticated mode ever desired? | Would require a separate threat model, package boundary, and approval. | No; do not design around it. |
