---
type: risk-register
project: polymarket-paper-trader
updated: 2026-08-27
---

# Risk Register

| ID | Risk | Likelihood | Impact | Mitigation | Status |
| --- | --- | --- | --- | --- | --- |
| R-001 | Provider schemas/routes change or deprecate. | High | High | Versioned adapters, fixtures, explicit errors, changelog review, bounded live probes. | Open |
| R-002 | Paper fills overstate executable liquidity because queue position and latency are unknown. | High | High | Record assumptions, support depth replay, model latency/queue policy, report uncertainty. | Open |
| R-003 | Fee schedule is missing, misread, or changes at match time. | Medium | High | Separate fee policy, current fixtures, unknown-state handling, fee golden tests. | Open |
| R-004 | WebSocket gaps create an incorrect local book. | Medium | High | Track sequence/age/hash, mark gaps, resync from authoritative snapshot before matching. | Open |
| R-005 | Synthetic backtests create false confidence. | High | High | Label synthetic data, prefer recorded books, expose coverage and provenance in reports. | Open |
| R-006 | Rebate estimates are mistaken for guaranteed revenue. | Medium | Medium | Separate estimates, eligibility, and provider-confirmed reward records. | Open |
| R-007 | A future live flag accidentally exposes write-capable behavior. | Low | Critical | Keep live execution out of v0.x, negative tests, separate package/decision, no secrets. | Open |
| R-008 | SQLite recorder grows without bound or leaks sensitive payloads. | Medium | High | Retention/size limits, sanitization, redaction review, dataset export policy. | Open |
| R-009 | Neg-risk or multi-outcome semantics are simplified incorrectly. | Medium | High | Preserve explicit market config, scenario fixtures, domain-specific validation before strategy use. | Open |
| R-010 | Current docs/API probes are mistaken for permanent guarantees. | High | Medium | Date every research claim and distinguish docs, probe, fixture, and inference. | Open |
| R-011 | Agent-facing output hides important warnings in prose. | Medium | Medium | Structured envelopes with warnings, freshness, provenance, assumptions, and rejection codes. | Open |
| R-012 | A local GUI grows into a live trading surface or accidentally exposes a write-like route. | Low | Critical | Loopback-only GET routes, no provider adapters/credentials, read-only query service, static/negative tests, persistent paper-only banner, separate live-monitoring decision. | Open |
| R-013 | Canonical and legacy history records lack the same market labels or accounting fields, causing misleading joins or false precision. | Medium | High | Stable source markers, typed history view model, non-destructive metadata capture, explicit unknown reasons, provenance/detail tests. | Open |
| R-014 | Large or malicious stored text makes the GUI slow, unsafe, or renders as executable markup. | Medium | Medium | Page/detail bounds, parameterized queries, HTML escaping, sanitized payload policy, oversized-result/error tests. | Open |
