"""
DetectionEngine: incremental, micro-batched detection over a GraphStore.

For every micro-batch of events:

1. ingest   write the events to the store (batched UNWIND on Neo4j)
2. dirty    the touched users, expanded to their k-hop neighbourhood
            (the first hop may cross attributes with up to max_fanout users,
            later hops only those with up to far_fanout users)
3. view     snapshot of that region (links, global attribute degrees, and
            transactions inside the rolling window)
4. detect   every enabled incremental detector on the region
5. track    merge candidates and update stable ring identities/status

Full-graph detectors (the static Cypher baseline, community detection) run
every `full_interval_s` of simulated time on the whole graph. With
`incremental=False` (ablation), the incremental detectors also evaluate the
whole graph on every batch. Each stage is timed per batch so incremental and
full recomputation can be compared (RQ2).
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from fraudsim.detectors.base import BaseDetector
from fraudsim.detectors.ensemble import RingTracker, RingUpdate
from fraudsim.graph.store import GraphStore
from fraudsim.types import Event, RingCandidate

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class EngineConfig:
    batch_size: int = 50
    k_hop: int = 2
    max_fanout: int = 250  # first hop may cross attributes this busy (hub co-members)
    far_fanout: int = 20  # later hops only cross attributes at most this busy
    window_s: float = 900.0  # rolling window for transactions in views
    incremental: bool = True  # False = full recomputation every batch (ablation)
    full_interval_s: float = 30.0  # cadence of full-graph detectors (sim seconds)
    ttl_s: float | None = None  # prune links/transactions older than this
    prune_interval_s: float = 30.0
    sweep_interval_s: float = 60.0  # re-check tracked rings (time decay, pruning)
    stats_interval_s: float = 10.0  # sample graph size and process memory this often

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(slots=True)
class BatchReport:
    """What one micro-batch did and how long each stage took (wall-clock ms)."""

    batch: int
    n_events: int
    first_seq: int
    last_seq: int
    now: datetime
    touched: int
    region: int
    timings_ms: dict[str, float]
    detector_ms: dict[str, float]
    candidates: dict[str, int]
    updates: list[RingUpdate] = field(default_factory=list)
    full_run: bool = False
    pruned: dict[str, int] = field(default_factory=dict)
    store_queries: int | None = None
    db_hits: int | None = None  # from sampled PROFILE queries (Neo4j only)
    graph_stats: dict[str, int] | None = None  # sampled every stats_interval_s
    rss_mb: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "n_events": self.n_events,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "now": self.now.isoformat(),
            "touched": self.touched,
            "region": self.region,
            "timings_ms": self.timings_ms,
            "detector_ms": self.detector_ms,
            "candidates": self.candidates,
            "updates": [u.to_dict() for u in self.updates],
            "full_run": self.full_run,
            "pruned": self.pruned,
            "store_queries": self.store_queries,
            "db_hits": self.db_hits,
            "graph_stats": self.graph_stats,
            "rss_mb": self.rss_mb,
        }


def _rss_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 2**20, 1)
    except Exception:
        return None


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)


class DetectionEngine:
    """Runs detectors incrementally over a stream of events."""

    def __init__(
        self,
        store: GraphStore,
        detectors: list[BaseDetector],
        tracker: RingTracker | None = None,
        config: EngineConfig | None = None,
        scorer: Any = None,
    ) -> None:
        self.store = store
        self.detectors = detectors
        self.tracker = tracker or RingTracker()
        self.cfg = config or EngineConfig()
        # Optional scoring stage (fraudsim.scoring.stage.ScoringStage): features, risk
        # score and explanation for every ring that changed in a batch.
        self.scorer = scorer
        self.reports: list[BatchReport] = []
        self._batch = 0
        self._last_full_t: float | None = None
        self._last_prune_t: float | None = None
        self._last_sweep_t: float | None = None
        self._last_stats_t: float | None = None
        for d in detectors:
            if d.store is None:
                d.store = store

    @property
    def incremental_detectors(self) -> list[BaseDetector]:
        return [d for d in self.detectors if d.enabled and d.incremental]

    @property
    def full_detectors(self) -> list[BaseDetector]:
        return [d for d in self.detectors if d.enabled and not d.incremental]

    # ─── One micro-batch ──────────────────────────────────────────────

    def process(self, events: list[Event]) -> BatchReport:
        cfg = self.cfg
        t_total = time.perf_counter()
        now_t = events[-1].ts.timestamp()
        now = datetime.fromtimestamp(now_t, tz=UTC)
        since_t = now_t - cfg.window_s
        queries_before = getattr(self.store, "queries", None)
        hits_before = getattr(self.store, "db_hits", None)
        timings: dict[str, float] = {}
        detector_ms: dict[str, float] = {}
        counts: dict[str, int] = {}
        candidates: list[RingCandidate] = []
        evaluated: dict[str, set[str] | None] = {}

        t = time.perf_counter()
        touched = self.store.apply(events)
        timings["ingest"] = _ms(t)

        seeds = set(touched)
        if self._due(self._last_sweep_t, cfg.sweep_interval_s, now_t):
            self._last_sweep_t = now_t
            for ring in self.tracker.live():
                seeds |= ring.members

        region: set[str] = set()
        inc = self.incremental_detectors
        if inc:
            t = time.perf_counter()
            if cfg.incremental:
                region = self.store.expand(
                    seeds, cfg.k_hop, cfg.max_fanout, since_t, far_fanout=cfg.far_fanout
                )
                timings["dirty"] = _ms(t)
                t = time.perf_counter()
                view = self.store.view(region, since_t, now_t)
            else:
                view = self.store.full_view(since_t, now_t)
                region = set(view.users)
            timings["view"] = _ms(t)
            for det in inc:
                t = time.perf_counter()
                found = det.detect(view)
                detector_ms[det.name] = _ms(t)
                counts[det.name] = len(found)
                candidates += found
                evaluated[det.detector_type.value] = None if view.full else region

        full_run = False
        if self.full_detectors and self._due(self._last_full_t, cfg.full_interval_s, now_t):
            self._last_full_t = now_t
            full_run = True
            t = time.perf_counter()
            full_view = self.store.full_view(since_t, now_t)
            timings["full_view"] = _ms(t)
            for det in self.full_detectors:
                t = time.perf_counter()
                found = det.detect(full_view)
                detector_ms[det.name] = _ms(t)
                counts[det.name] = len(found)
                candidates += found
                evaluated[det.detector_type.value] = None

        t = time.perf_counter()
        updates = self.tracker.update(candidates, evaluated, now) if evaluated else []
        timings["track"] = _ms(t)

        if self.scorer is not None and updates:
            t = time.perf_counter()
            updates += self.scorer.score_updates(self, updates, now_t, since_t)
            timings["score"] = _ms(t)

        pruned: dict[str, int] = {}
        if cfg.ttl_s is not None and self._due(self._last_prune_t, cfg.prune_interval_s, now_t):
            self._last_prune_t = now_t
            t = time.perf_counter()
            pruned = self.store.prune(now_t - cfg.ttl_s)
            timings["prune"] = _ms(t)

        timings["total"] = _ms(t_total)
        graph_stats, rss_mb = None, None
        if self._due(self._last_stats_t, cfg.stats_interval_s, now_t):
            self._last_stats_t = now_t
            graph_stats = self.store.stats()
            rss_mb = _rss_mb()
        self._batch += 1
        queries_after = getattr(self.store, "queries", None)
        hits_after = getattr(self.store, "db_hits", None)
        report = BatchReport(
            batch=self._batch,
            n_events=len(events),
            first_seq=events[0].seq,
            last_seq=events[-1].seq,
            now=now,
            touched=len(touched),
            region=len(region),
            timings_ms=timings,
            detector_ms=detector_ms,
            candidates=counts,
            updates=updates,
            full_run=full_run,
            pruned=pruned,
            store_queries=(
                queries_after - queries_before
                if queries_before is not None and queries_after is not None
                else None
            ),
            db_hits=(
                hits_after - hits_before
                if hits_before is not None and hits_after is not None
                else None
            ),
            graph_stats=graph_stats,
            rss_mb=rss_mb,
        )
        self.reports.append(report)
        return report

    @staticmethod
    def _due(last: float | None, interval: float, now_t: float) -> bool:
        return last is None or now_t - last >= interval

    # ─── Driving ──────────────────────────────────────────────────────

    def batches(self, events: Iterable[Event]) -> Iterator[list[Event]]:
        batch: list[Event] = []
        for e in events:
            batch.append(e)
            if len(batch) >= self.cfg.batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def run(self, events: Iterable[Event]) -> list[BatchReport]:
        """Process a whole (finite) event stream headless."""
        return [self.process(b) for b in self.batches(events)]

    async def run_async(self, subscription: Any, max_wait_s: float = 0.25) -> None:
        """
        Consume an event-bus subscription. A batch is processed when it reaches
        batch_size or when no new event arrived for max_wait_s (wall clock).
        """
        import asyncio

        batch: list[Event] = []
        iterator = subscription.__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(iterator.__anext__(), timeout=max_wait_s)
            except TimeoutError:
                if batch:
                    self.process(batch)
                    batch = []
                continue
            except StopAsyncIteration:
                break
            batch.append(event)
            if len(batch) >= self.cfg.batch_size:
                self.process(batch)
                batch = []
            await asyncio.sleep(0)
        if batch:
            self.process(batch)

    def summary(self) -> dict[str, Any]:
        rings = list(self.tracker.rings.values())
        return {
            "batches": len(self.reports),
            "events": sum(r.n_events for r in self.reports),
            "rings_total": len(rings),
            "rings_by_status": {
                s: sum(1 for r in rings if r.status.value == s)
                for s in ("candidate", "confirmed", "dissolved")
            },
            "config": self.cfg.to_dict(),
            "detectors": [
                {"name": d.name, "type": d.detector_type.value, "incremental": d.incremental}
                for d in self.detectors
                if d.enabled
            ],
        }


def build_detectors(
    store: GraphStore, overrides: dict[str, dict[str, Any]] | None = None
) -> list[BaseDetector]:
    """
    The default detector set, each configurable (and switchable) by one dict.

    overrides maps a detector type value ("static_cypher", "hub_aware",
    "temporal_cycle", "smurfing", "community") to config field overrides,
    e.g. {"hub_aware": {"weight_method": "none"}, "community": {"enabled": False}}.
    """
    from fraudsim.detectors.community import CommunityConfig, CommunityDetector
    from fraudsim.detectors.cycle import TemporalCycleConfig, TemporalCycleDetector
    from fraudsim.detectors.hubaware import HubAwareConfig, HubAwareSharedAttribute
    from fraudsim.detectors.smurfing import SmurfingConfig, SmurfingDetector
    from fraudsim.detectors.static_cypher import StaticCypherBaseline, StaticCypherConfig

    overrides = overrides or {}
    known = {"static_cypher", "hub_aware", "temporal_cycle", "smurfing", "community"}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(
            f"unknown detector override(s) {sorted(unknown)}; expected {sorted(known)}"
        )

    def cfg(cls: type, key: str) -> Any:
        return cls(**overrides.get(key, {}))

    return [
        StaticCypherBaseline(config=cfg(StaticCypherConfig, "static_cypher"), store=store),
        HubAwareSharedAttribute(cfg(HubAwareConfig, "hub_aware"), store),
        TemporalCycleDetector(cfg(TemporalCycleConfig, "temporal_cycle"), store),
        SmurfingDetector(cfg(SmurfingConfig, "smurfing"), store),
        CommunityDetector(cfg(CommunityConfig, "community"), store),
    ]
