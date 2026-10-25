"""
StreamPlayer: plays a simulator's event stream onto an EventBus in wall-clock time.

The generator decides *what* happens and *when* in simulated time. The player
decides how fast simulated time runs against the wall clock:

- speed multiplier: 1.0 = real time, 10.0 = ten simulated seconds per wall
  second, 0 (or math.inf) = as fast as possible (headless experiments).
- rate: forwarded to the generator (benign events per simulated second).
- pause / resume: the stream holds without losing its place; simulated time
  does not advance while paused.

Pacing never changes the stream's content, so the same (scenario, seed)
produces the same events at any speed.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.stream.event_bus import EventBus
from fraudsim.types import Event

# Do not sleep for gaps shorter than this; publish immediately instead.
_MIN_SLEEP_S = 0.001
# When publishing without sleeping, yield to the event loop this often so that
# consumers, control requests and other tasks are never starved.
_YIELD_EVERY = 64


@dataclass(slots=True)
class PlayerStats:
    events_published: int = 0
    sim_time_s: float = 0.0
    wall_time_s: float = 0.0
    paused_time_s: float = 0.0

    @property
    def achieved_rate(self) -> float:
        """Events per wall-clock second while not paused."""
        active = self.wall_time_s - self.paused_time_s
        return self.events_published / active if active > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "events_published": self.events_published,
            "sim_time_s": round(self.sim_time_s, 6),
            "wall_time_s": round(self.wall_time_s, 6),
            "paused_time_s": round(self.paused_time_s, 6),
            "achieved_rate_eps": round(self.achieved_rate, 3),
        }


class StreamPlayer:
    """Publishes generator events to a bus, paced by simulated timestamps."""

    def __init__(
        self,
        generator: EventGenerator,
        bus: EventBus,
        speed: float = 1.0,
        until_s: float | None = None,
        on_event: Callable[[Event], Awaitable[None] | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.generator = generator
        self.bus = bus
        self.until_s = until_s
        self.on_event = on_event
        self._clock = clock
        self._sleep = sleep
        self._speed = self._validate_speed(speed)
        self._resumed = asyncio.Event()
        self._resumed.set()
        self._stopped = False
        self._wake = asyncio.Event()
        self.stats = PlayerStats()
        self.state = "idle"  # idle | running | paused | stopped | finished
        # Anchor mapping simulated time to wall time; reset on speed change / resume.
        self._anchor_wall = 0.0
        self._anchor_sim = 0.0
        self._paused_at: float | None = None
        self._started_at: float | None = None

    # ─── Controls ─────────────────────────────────────────────────────

    @staticmethod
    def _validate_speed(speed: float) -> float:
        if speed < 0 or math.isnan(speed):
            raise ValueError("speed must be >= 0 (0 = as fast as possible)")
        return math.inf if speed == 0 else float(speed)

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> None:
        self._speed = self._validate_speed(speed)
        self._reanchor()
        self._wake.set()

    def set_rate(self, events_per_second: float) -> None:
        self.generator.set_rate(events_per_second)

    def pause(self) -> None:
        if self.state == "running":
            self.state = "paused"
            self._paused_at = self._clock()
            self._resumed.clear()
            self._wake.set()

    def resume(self) -> None:
        if self.state == "paused":
            self.state = "running"
            if self._paused_at is not None:
                self.stats.paused_time_s += self._clock() - self._paused_at
                self._paused_at = None
            self._reanchor()
            self._resumed.set()

    def stop(self) -> None:
        self._stopped = True
        self._resumed.set()
        self._wake.set()

    @property
    def paused(self) -> bool:
        return self.state == "paused"

    def _reanchor(self) -> None:
        self._anchor_wall = self._clock()
        self._anchor_sim = self.stats.sim_time_s

    # ─── Main loop ────────────────────────────────────────────────────

    def prepare(self) -> None:
        """Mark the player running and anchor the clock (run() calls this if needed)."""
        if self.state == "idle":
            self.state = "running"
            self._started_at = self._clock()
            self._reanchor()

    async def run(self, close_bus: bool = True) -> PlayerStats:
        """Play until the scenario ends or stop() is called."""
        self.prepare()
        try:
            while not self._stopped:
                await self._resumed.wait()
                if self._stopped:
                    break
                event = self.generator.next_event(self.until_s)
                if event is None:
                    self.state = "finished"
                    break
                sim_t = (event.ts - self.generator.ts(0.0)).total_seconds()
                await self._wait_until(sim_t)
                if self._stopped:
                    break
                await self.bus.publish(event)
                self.stats.events_published += 1
                if self.stats.events_published % _YIELD_EVERY == 0:
                    await asyncio.sleep(0)
                self.stats.sim_time_s = sim_t
                if self.on_event is not None:
                    result = self.on_event(event)
                    if result is not None:
                        await result
        finally:
            if self._stopped:
                self.state = "stopped"
            started = self._started_at if self._started_at is not None else self._clock()
            self.stats.wall_time_s = self._clock() - started
            if close_bus:
                await self.bus.close()
        return self.stats

    async def _wait_until(self, sim_t: float) -> None:
        """Sleep until the wall-clock moment that corresponds to simulated time sim_t."""
        while not self._stopped:
            if not self._resumed.is_set():
                await self._resumed.wait()
                continue
            if math.isinf(self._speed):
                return
            target = self._anchor_wall + (sim_t - self._anchor_sim) / self._speed
            delay = target - self._clock()
            if delay <= _MIN_SLEEP_S:
                return
            self._wake.clear()
            sleeper = asyncio.ensure_future(self._sleep(delay))
            waker = asyncio.ensure_future(self._wake.wait())
            done, pending = await asyncio.wait(
                {sleeper, waker}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if sleeper in done:
                return
            # Woken by a control change: recompute the target with the new anchor/speed.
