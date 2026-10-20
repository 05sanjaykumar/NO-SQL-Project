"""
Live simulation run behind the API: generator -> StreamPlayer -> bus -> consumer.

The consumer forwards each event to WebSocket clients and keeps a short tail
for the dashboard. Detection (Milestone 3) will subscribe to the same bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.player import StreamPlayer
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.stream.event_bus import AsyncQueueBus, EventBus
from fraudsim.types import Event

RECENT_EVENTS = 200


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

    async def start(self) -> None:
        subscription = await self.bus.subscribe()
        self._consumer_task = asyncio.create_task(self._consume(subscription))
        self.player.prepare()
        self._player_task = asyncio.create_task(self.player.run())

    async def _consume(self, subscription: Any) -> None:
        async for event in subscription:
            await self._handle(event)

    async def _handle(self, event: Event) -> None:
        self.events_consumed += 1
        data = event.to_dict()
        self.recent.append(data)
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
