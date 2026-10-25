"""
Tests for StreamPlayer pacing (rate, speed multiplier, pause/resume) and the
Redis Streams event bus.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.player import StreamPlayer
from fraudsim.stream.event_bus import AsyncQueueBus, RedisStreamBus, create_event_bus
from fraudsim.types import Event, EventType


def small_scenario(rate: float = 20.0, duration: float = 30.0) -> dict[str, Any]:
    return {
        "name": "player_test",
        "simulation": {
            "duration_seconds": duration,
            "target_event_rate": rate,
            "num_users": 20,
        },
        "rings": [{"id": "r", "type": "shared_device", "size": 3, "spawn_time_s": 2}],
    }


class FakeClock:
    """Deterministic wall clock: sleeping advances time instantly."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        self.slept += seconds
        await asyncio.sleep(0)


async def drain(bus: AsyncQueueBus) -> list[Event]:
    sub = await bus.subscribe()
    return [e async for e in sub]


@pytest.mark.parametrize("speed", [1.0, 4.0, 20.0])
async def test_speed_multiplier_scales_wall_time(speed: float) -> None:
    clock = FakeClock()
    gen = EventGenerator(seed=1, run_id="p", config=small_scenario())
    bus = AsyncQueueBus(maxsize=0)
    player = StreamPlayer(gen, bus, speed=speed, clock=clock, sleep=clock.sleep)
    stats = await player.run()
    assert player.state == "finished"
    # Wall time spent = last event's simulated time / speed (within one sleep threshold).
    assert stats.wall_time_s == pytest.approx(stats.sim_time_s / speed, abs=0.01)
    assert len(await drain(bus)) == stats.events_published


async def test_speed_zero_is_unpaced_and_stream_is_unchanged() -> None:
    clock = FakeClock()
    gen = EventGenerator(seed=1, run_id="p", config=small_scenario())
    bus = AsyncQueueBus(maxsize=0)
    stats = await StreamPlayer(gen, bus, speed=0, clock=clock, sleep=clock.sleep).run()
    assert clock.slept == 0.0
    reference = EventGenerator(seed=1, run_id="p", config=small_scenario())
    reference.generate()
    assert gen.digest == reference.digest  # pacing never changes content
    assert stats.events_published == reference.events_emitted


async def test_rate_controls_events_per_second() -> None:
    results = {}
    for rate in (5.0, 40.0):
        clock = FakeClock()
        gen = EventGenerator(seed=3, run_id="p", config=small_scenario(rate=rate, duration=60))
        bus = AsyncQueueBus(maxsize=0)
        player = StreamPlayer(gen, bus, speed=1.0, clock=clock, sleep=clock.sleep)
        results[rate] = (await player.run()).achieved_rate
    assert results[40.0] > 4 * results[5.0]


async def test_pause_resume_and_live_controls() -> None:
    gen = EventGenerator(seed=1, run_id="p", config=small_scenario(rate=200.0, duration=1e6))
    bus = AsyncQueueBus(maxsize=0)
    player = StreamPlayer(gen, bus, speed=50.0)
    task = asyncio.create_task(player.run())
    await asyncio.sleep(0.15)
    player.pause()
    await asyncio.sleep(0.02)
    held = player.stats.events_published
    sim_held = player.stats.sim_time_s
    await asyncio.sleep(0.15)
    assert player.paused
    assert player.stats.events_published == held  # nothing published while paused
    assert player.stats.sim_time_s == sim_held

    player.resume()
    player.set_speed(0)  # as fast as possible
    player.set_rate(10.0)
    await asyncio.sleep(0.1)
    assert player.stats.events_published > held
    player.stop()
    stats = await asyncio.wait_for(task, timeout=5)
    assert player.state == "stopped"
    assert stats.paused_time_s >= 0.1
    assert any(c["action"] == "set_rate" for c in gen.control_log)


async def test_on_event_callback_and_until() -> None:
    seen: list[Event] = []
    gen = EventGenerator(seed=1, run_id="p", config=small_scenario())
    bus = AsyncQueueBus(maxsize=0)
    player = StreamPlayer(gen, bus, speed=0, until_s=5.0, on_event=seen.append)
    stats = await player.run()
    assert len(seen) == stats.events_published
    assert stats.sim_time_s <= 5.0


def test_invalid_speed() -> None:
    gen = EventGenerator(seed=1, run_id="p", config=small_scenario())
    with pytest.raises(ValueError):
        StreamPlayer(gen, AsyncQueueBus(), speed=-1)


# ─── Redis Streams bus ────────────────────────────────────────────────


@pytest.fixture
def fake_redis() -> Any:
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.FakeAsyncRedis()


async def test_redis_bus_roundtrip(fake_redis: Any) -> None:
    bus = create_event_bus("redis", client=fake_redis, stream_name="t:events", block_ms=50)
    assert isinstance(bus, RedisStreamBus)
    gen = EventGenerator(seed=1, run_id="p", config=small_scenario())
    events = gen.generate(until_s=5.0)
    await bus.publish(events[0])
    await bus.publish_batch(events[1:])
    await bus.close()
    with pytest.raises(RuntimeError):
        await bus.publish(events[0])

    received = [e async for e in await bus.subscribe()]
    assert [e.to_dict() for e in received] == [e.to_dict() for e in events]


async def test_player_over_redis_bus(fake_redis: Any) -> None:
    bus = RedisStreamBus(client=fake_redis, stream_name="t:player", block_ms=50)
    gen = EventGenerator(seed=2, run_id="p", config=small_scenario())
    stats = await StreamPlayer(gen, bus, speed=0, until_s=10.0).run()
    received = [e async for e in await bus.subscribe()]
    assert len(received) == stats.events_published
    assert received[0].event_type == EventType.USER_JOIN
