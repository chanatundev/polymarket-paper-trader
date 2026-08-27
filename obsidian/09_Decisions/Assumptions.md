---
type: assumptions
project: polymarket-paper-trader
updated: 2026-08-27
---

# Assumptions

These are working assumptions, not verified accomplishments or permanent product decisions.

- The repository URL and name define the project name `polymarket-paper-trader`.
- The checked-out `main` revision is the baseline for comparison; the unmerged remote branch is reference evidence only.
- Public provider data is sufficient for the first paper-research workflow, subject to rate limits and availability.
- SQLite is adequate for the first local recorder/replayer before a larger analytical store is justified.
- A deterministic matching model can be useful even though it cannot know a real queue position.
- The source's existing CLI and MCP users are worth preserving during the domain-model migration.
- Official documentation and sanitized live probes are the preferred current-behavior evidence; third-party claims require separate validation.
- The owner prefers an honest paper-only result over an ambiguous live-trading capability. This was inferred from the preceding task discussion and should be revisited if the product intent differs.
- The first GUI can use a local browser and the same SQLite data directory as the CLI; a desktop wrapper is not needed for the requested lightweight review surface.
- Some canonical fills may not yet retain human-friendly market question/outcome labels; the GUI will show the asset/market ID and an explicit unknown reason until non-destructive metadata capture is added.
