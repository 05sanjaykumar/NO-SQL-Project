"""
Live simulation run behind the API: generator -> StreamPlayer -> bus -> consumer.

The consumer forwards each event to WebSocket clients, keeps a short tail for
the dashboard, and feeds micro-batches to the incremental DetectionEngine
(in-memory store). Ring changes are broadcast as RING_UPDATE messages, and
newly confirmed rings are reported back to the simulator's adaptive attacker.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
from fraudsim.detectors.ensemble import RingUpdate, TrackedRing
from fraudsim.graph.store import InMemoryGraphStore
from fraudsim.scoring.scorer import RingScorer
from fraudsim.scoring.stage import ScoringStage
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.player import StreamPlayer
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.stream.event_bus import AsyncQueueBus, EventBus
from fraudsim.types import Event

RECENT_EVENTS = 200
# Process a partial batch when no event arrived for this long (wall clock).
BATCH_IDLE_S = 0.25


class LiveRun:
    """One running scenario with pause/resume, speed and rate controls."""

    def __init__(
        self,
        scenario: str,
        seed: int,
        run_id: str,
        speed: float = 1.0,
        broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        bus: EventBus | None = None,
        engine_config: EngineConfig | None = None,
    ) -> None:
        self.scenario = scenario
        self.seed = seed
        self.run_id = run_id
        self.generator = EventGenerator(seed=seed, run_id=run_id, config=load_scenario(scenario))
        self.bus = bus or AsyncQueueBus(maxsize=10_000)
        self.player = StreamPlayer(self.generator, self.bus, speed=speed)
        self._broadcast = broadcast
        self.events_consumed = 0
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT_EVENTS)
        self._player_task: asyncio.Task[Any] | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        store = InMemoryGraphStore()
        self.scoring = ScoringStage(scorer=load_default_scorer())
        self.engine = DetectionEngine(
            store,
            build_detectors(store),
            config=engine_config or EngineConfig(),
            scorer=self.scoring,
        )
        self._batch: list[Event] = []
        self._flagged: set[str] = set()

    async def start(self) -> None:
        subscription = await self.bus.subscribe()
        self._consumer_task = asyncio.create_task(self._consume(subscription))
        self.player.prepare()
        self._player_task = asyncio.create_task(self.player.run())

    async def _consume(self, subscription: Any) -> None:
        iterator = subscription.__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(iterator.__anext__(), timeout=BATCH_IDLE_S)
            except TimeoutError:
                await self._detect()
                continue
            except StopAsyncIteration:
                break
            await self._handle(event)
            if len(self._batch) >= self.engine.cfg.batch_size:
                await self._detect()
        await self._detect()

    async def _detect(self) -> None:
        if not self._batch:
            return
        batch, self._batch = self._batch, []
        report = self.engine.process(batch)
        newly: set[str] = set()
        flag_change = "alert" if self.scoring.scorer is not None else "confirmed"
        for upd in report.updates:
            if upd.change == flag_change:
                newly |= set(upd.members)
            if self._broadcast is not None:
                await self._broadcast({"type": "RING_UPDATE", "update": upd.to_dict()})
        newly -= self._flagged
        if newly:
            self._flagged |= newly
            self.generator.notify_flagged(newly)
        if self._broadcast is not None:
            await self._broadcast(
                {
                    "type": "BATCH",
                    "batch": report.batch,
                    "n_events": report.n_events,
                    "region": report.region,
                    "timings_ms": report.timings_ms,
                }
            )

    def metrics(self) -> Any:
        """Evaluate the run so far against the simulator's ground truth."""
        from fraudsim.detectors.runner import evaluate_detection

        return evaluate_detection(self.generator, self.engine, self.scoring.scorer is not None)

    def rings(self) -> list[dict[str, Any]]:
        return [ring_payload(r) for r in self.engine.tracker.rings.values()]

    async def _handle(self, event: Event) -> None:
        self.events_consumed += 1
        data = event.to_dict()
        self.recent.append(data)
        self._batch.append(event)
        if self._broadcast is not None:
            await self._broadcast({"type": "EVENT", "event": data})

    @property
    def status(self) -> str:
        return self.player.state

    def pause(self) -> None:
        self.player.pause()

    def resume(self) -> None:
        self.player.resume()

    def set_speed(self, speed: float) -> None:
        self.player.set_speed(speed)

    def set_rate(self, rate: float) -> None:
        self.player.set_rate(rate)

    async def stop(self) -> None:
        self.player.stop()
        for task in (self._player_task, self._consumer_task):
            if task is None or task.done():
                continue
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    def snapshot(self) -> dict[str, Any]:
        return {
            "sim_time_s": round(self.generator.now_s, 3),
            "rate": self.generator.rate,
            # 0 means "as fast as possible" (JSON has no infinity).
            "speed_multiplier": 0.0 if math.isinf(self.player.speed) else self.player.speed,
            "events_emitted": self.events_consumed,
            "player": self.player.stats.to_dict(),
        }


DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "models" / "ring_scorer_lr.joblib"


def load_default_scorer() -> RingScorer | None:
    """The trained logistic-regression scorer if `fraudsim train-scorer` produced one."""
    try:
        return RingScorer.load(DEFAULT_MODEL) if DEFAULT_MODEL.exists() else None
    except Exception:
        return None


def ring_payload(ring: TrackedRing) -> dict[str, Any]:
    """TrackedRing in the shape of api.schemas.RingDetailResponse."""
    return {
        "ring_id": ring.ring_id,
        "status": ring.status.value,
        "ring_type": ring.ring_type.value,
        "score": ring.score,
        "members": sorted(ring.members),
        "features": ring.features,
        "explanation": {
            "detectors": sorted(ring.sources),
            "risk": ring.risk,
            "alerted_at": ring.alerted_at.isoformat() if ring.alerted_at else None,
            "early_warned_at": ring.early_warned_at.isoformat() if ring.early_warned_at else None,
            **(ring.explanation or {}),
            "evidence": ring.evidence,
        },
        "created_at": ring.first_seen.isoformat(),
        "updated_at": ring.last_seen.isoformat(),
    }


__all__ = ["LiveRun", "RingUpdate", "ring_payload"]
