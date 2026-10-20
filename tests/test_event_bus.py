"""
Unit tests for the event bus (in-process asyncio queue and factory).
"""

from __future__ import annotations

import pytest

from fraudsim.stream.event_bus import AsyncQueueBus, create_event_bus
from fraudsim.types import Event, EventType


@pytest.mark.asyncio
async def test_async_queue_bus_publish_subscribe():
    bus = create_event_bus("asyncio")
    assert isinstance(bus, AsyncQueueBus)

    ev1 = Event.now(event_type=EventType.USER_JOIN, payload={"user_id": "u1"})
    ev2 = Event.now(event_type=EventType.DEVICE_LINK, payload={"user_id": "u1", "device_id": "d1"})

    await bus.publish(ev1)
    await bus.publish(ev2)
    await bus.close()

    received: list[Event] = []
    sub = await bus.subscribe()
    async for event in sub:
        received.append(event)

    assert len(received) == 2
    assert received[0].event_type == EventType.USER_JOIN
    assert received[1].event_type == EventType.DEVICE_LINK


@pytest.mark.asyncio
async def test_async_queue_bus_publish_batch():
    bus = AsyncQueueBus()
    events = [
        Event.now(event_type=EventType.TRANSACTION, payload={"amount": float(i)}) for i in range(5)
    ]
    await bus.publish_batch(events)
    await bus.close()

    collected = []
    sub = await bus.subscribe()
    async for event in sub:
        collected.append(event)

    assert len(collected) == 5
    assert [e.payload["amount"] for e in collected] == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_unknown_event_bus_backend():
    with pytest.raises(ValueError, match="Unknown event bus backend"):
        create_event_bus("kafka")
