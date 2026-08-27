---
type: open-questions
project: polymarket-paper-trader
updated: 2026-08-26
---

# Open Questions

| ID | Question | Why it matters | Default until decided |
| --- | --- | --- | --- |
| OQ-001 | Should the next implementation happen directly in the cloned `/tmp` repo, a new checkout, or a fork? | Determines source ownership and delivery workflow. | Do not modify the clone until explicitly requested. |
| OQ-002 | Is a web UI needed, or are CLI/MCP workflows sufficient for v0.x? | Changes product surface and implementation effort. | CLI/MCP first. |
| OQ-003 | Which market family should be the first recorded dataset: crypto, sports, or general events? | Determines stream/context fixtures and edge cases. | General events with one liquid market; choose before Phase 1. |
| OQ-004 | What retention limit is acceptable for raw book/event recordings? | Balances reproducibility, disk use, and privacy. | Bounded local datasets only. |
| OQ-005 | Is a maker-rebate estimate useful before provider-confirmed reward data is available? | Prevents false precision in strategy results. | Include only as visibly labeled optional output. |
| OQ-006 | Should the project support multiple fee-model versions in one replay? | May be required for historical comparisons across CLOB changes. | One declared model version per run until fixtures justify more. |
| OQ-007 | Is optional authenticated mode ever desired? | Would require a separate threat model, package boundary, and approval. | No; do not design around it. |

