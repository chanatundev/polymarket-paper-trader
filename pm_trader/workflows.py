"""Agent-facing research workflows and bounded structured envelopes."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .api import CLOBAdapter, GammaAdapter, ResourceLimits
from .models import BookSnapshot, Decision, MarketState, PaperOrder, SimulationConfig
from .normalizers import make_provenance, normalize_book, normalize_market
from .recording import ReplayEngine
from .serialization import canonical_json, sanitize
from .simulation import SimulationEngine
from .storage import SQLiteStore


def as_jsonable(value: Any) -> Any:
    if isinstance(value, str):
        return value
    try:
        return json.loads(canonical_json(value))
    except (TypeError, ValueError):
        return sanitize(value)


def envelope(
    *,
    result: Any = None,
    warnings: Iterable[str] = (),
    freshness: str = "unknown",
    provenance_ids: Iterable[str] = (),
    assumptions: Mapping[str, Any] | None = None,
    errors: Iterable[Mapping[str, Any]] = (),
    ok: bool = True,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "result": as_jsonable(result),
        "warnings": list(warnings),
        "freshness": freshness,
        "provenance_ids": list(dict.fromkeys(provenance_ids)),
        "assumptions": as_jsonable(dict(assumptions or {})),
        "errors": [as_jsonable(error) for error in errors],
    }


@dataclass
class ResearchService:
    store: SQLiteStore
    gamma: GammaAdapter | None = None
    clob: CLOBAdapter | None = None
    limits: ResourceLimits = field(default_factory=ResourceLimits)

    def __post_init__(self) -> None:
        self.gamma = self.gamma or GammaAdapter(limits=self.limits)
        self.clob = self.clob or CLOBAdapter(limits=self.limits)

    def discover(
        self, *, query: str | None = None, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        if query:
            found = self.gamma.search(query, limit=limit)
            return envelope(
                result={
                    "query": query,
                    "markets": found.markets,
                    "events": found.events,
                    "tags": found.tags,
                    "profiles": found.profiles,
                    "pagination": found.pagination,
                },
                warnings=(),
                provenance_ids=(found.observation.provenance.observation_id,),
                assumptions={"provider": "gamma", "route": "/public-search"},
            )
        page = self.gamma.list_markets(limit=limit, after_cursor=cursor)
        return envelope(
            result={
                "markets": page.items,
                "next_cursor": page.next_cursor,
                "query": page.query,
                "response_metadata": page.response_metadata,
            },
            provenance_ids=(page.observation.provenance.observation_id,),
            assumptions={"provider": "gamma", "route": "/markets/keyset", "pagination": "keyset"},
        )

    def inspect_market(self, identifier: str) -> dict[str, Any]:
        market = self._market(identifier)
        return envelope(
            result=market,
            freshness=market.freshness.value,
            provenance_ids=(market.provenance.observation_id,) if market.provenance else (),
            assumptions={"provider_state_is_point_in_time": True},
        )

    def inspect_book(self, market: MarketState, outcome: str) -> dict[str, Any]:
        token = market.token_for(outcome)
        observation = self.clob.get_book(token.token_id)
        book = observation.value
        return envelope(
            result=book,
            freshness=book.freshness.value,
            provenance_ids=(observation.provenance.observation_id,),
            assumptions={"executable_book_state": True, "context_inputs_are_not_executable": True},
        )

    def simulate(
        self,
        *,
        market: MarketState,
        book: BookSnapshot,
        orders: Iterable[PaperOrder],
        config: SimulationConfig | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        simulation = SimulationEngine(self.store).run(
            market=market, book=book, orders=orders, config=config, dataset_id=dataset_id
        )
        warnings = list(simulation.report.warnings)
        for match in simulation.matches:
            warnings.extend(match.decision.warnings)
        return envelope(
            result=simulation,
            warnings=warnings,
            freshness=simulation.matches[0].decision.freshness.value
            if simulation.matches
            else "unknown",
            provenance_ids=[
                identifier
                for match in simulation.matches
                for identifier in match.decision.provenance_ids
            ],
            assumptions=simulation.report.assumptions,
        )

    def replay(
        self,
        *,
        dataset_path: str | Path,
        market: MarketState,
        orders: Iterable[PaperOrder],
        config: SimulationConfig | None = None,
    ) -> dict[str, Any]:
        simulation = ReplayEngine(self.store, limits=self.limits).replay_jsonl(
            dataset_path, market=market, orders=orders, config=config
        )
        return envelope(
            result=simulation,
            warnings=simulation.report.warnings,
            freshness="fresh",
            provenance_ids=[
                fill.observation_id
                for match in simulation.matches
                for fill in match.fills
                if fill.observation_id
            ],
            assumptions=simulation.report.assumptions,
        )

    @staticmethod
    def explain(decision: Decision) -> dict[str, Any]:
        return envelope(
            result=decision,
            warnings=decision.warnings,
            freshness=decision.freshness.value,
            provenance_ids=decision.provenance_ids,
            assumptions=decision.assumptions,
        )

    def portfolio_report(self, run_id: str) -> dict[str, Any]:
        report = self.store.get_report(run_id)
        if report is None:
            return envelope(
                result=None, errors=({"code": "REPORT_NOT_FOUND", "message": run_id},), ok=False
            )
        return envelope(result=report, assumptions={"paper_pnl_is_not_live_pnl": True})

    def _market(self, identifier: str) -> MarketState:
        if identifier.startswith("0x"):
            return self.clob.get_market_info(identifier).value
        page = self.gamma.list_markets(
            limit=min(self.limits.max_page_size, 100), filters={"slug": identifier}
        )
        if not page.items:
            raise ValueError(f"market not found: {identifier}")
        return page.items[0]


def load_market_fixture(path: str | Path) -> MarketState:
    fixture_path = Path(path)
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping) and "market" in raw and isinstance(raw["market"], Mapping):
        raw = raw["market"]
    elif isinstance(raw, Mapping) and isinstance(raw.get("markets"), list) and raw["markets"]:
        raw = raw["markets"][0]
    provenance = make_provenance(
        source="fixture",
        endpoint=str(fixture_path),
        raw=raw,
        retrieved_at=(raw.get("retrieved_at") if isinstance(raw, Mapping) else None),
        received_at=(raw.get("received_at") if isinstance(raw, Mapping) else None),
        schema_version="fixture-v1",
    )
    return normalize_market(
        raw, provenance=provenance, endpoint=str(fixture_path), provider="fixture"
    )


def load_book_fixture(path: str | Path) -> BookSnapshot:
    fixture_path = Path(path)
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping) and "book" in raw and isinstance(raw["book"], Mapping):
        raw = raw["book"]
    provenance = make_provenance(
        source="fixture",
        endpoint=str(fixture_path),
        raw=raw,
        retrieved_at=(raw.get("retrieved_at") if isinstance(raw, Mapping) else None),
        received_at=(raw.get("received_at") if isinstance(raw, Mapping) else None),
        schema_version="fixture-v1",
    )
    return normalize_book(
        raw, provenance=provenance, endpoint=str(fixture_path), provider="fixture"
    )
