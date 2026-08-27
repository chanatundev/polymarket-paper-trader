---
type: project-index
project: polymarket-paper-trader
status: phase-5-gui-implemented-pending-release-gates
updated: 2026-08-27
links:
  - "[[300_Maps/coding|Coding]]"
  - "[[300_Maps/engineering|Engineering]]"
---

# Polymarket Paper Trader

An agent-first, paper-only research and simulation workbench for Polymarket. The project keeps the useful CLI/MCP surface of the source repository while updating its market-data, order-book, fee, and execution models for current Polymarket behavior. A lightweight local browser GUI is implemented for reviewing stored paper trades.

## Start here

- [[Project_Overview|Project overview]]
- [[Status|Current status]]
- [[Project_Map|Project map]]
- [[../01_Research/Sources|Research and sources]]
- [[../02_Strategy/Project_Thesis|Project thesis]]
- [[../04_Design/System_Overview|System overview]]
- [[../04_Design/Trade_History_GUI|Trade-history GUI]]
- [[../06_Execution/Roadmap|Roadmap]]
- [[../06_Execution/Next_Actions|Next actions]]

## Current decision

Keep the V2-aware simulator and research tool paper-only, and add a loopback-only, read-only GUI for reviewing stored trades. Authenticated live execution and live-provider dashboarding remain explicitly out of scope.

## Navigation

| Area | Entry point |
| --- | --- |
| Context | [[Project_Overview|Project Overview]], [[Glossary|Glossary]], [[Status|Status]] |
| Research | [[../01_Research/Sources|Sources]] |
| Strategy | [[../02_Strategy/Project_Thesis|Thesis]], [[../02_Strategy/Goals|Goals]], [[../02_Strategy/Scope_and_Non_Goals|Scope]], [[../02_Strategy/Success_Metrics|Success metrics]] |
| Requirements | [[../03_Requirements/Requirements|Requirements]], [[../03_Requirements/Constraints|Constraints]] |
| Design | [[../04_Design/System_Overview|System overview]], [[../04_Design/Trade_History_GUI|Trade-history GUI]] |
| Execution | [[../06_Execution/Roadmap|Roadmap]], [[../06_Execution/Next_Actions|Next actions]] |
| Validation | [[../07_Validation/Validation_Plan|Validation plan]], [[../07_Validation/Acceptance_Criteria|Acceptance criteria]] |
| Risks | [[../08_Risks/Risk_Register|Risk register]] |
| Decisions | [[../09_Decisions/Locked_Invariants|Locked invariants]], [[../09_Decisions/Decision_Log|Decision log]], [[../09_Decisions/Open_Questions|Open questions]], [[../09_Decisions/Assumptions|Assumptions]], [[../09_Decisions/Rejected_Ideas|Rejected ideas]] |

## Evidence boundary

The core successor implementation and Phase 5 GUI exist in the selected checkout; local evidence is recorded in the root `IMPLEMENTATION.md` and [[../07_Validation/GUI_Smoke_2026-08-27|GUI smoke review]]. Provider-current, dataset-sharing, broader accessibility, and human paper/live review gates remain open. This knowledge base does not claim that live providers will remain stable or that paper results predict real-world profitability.
