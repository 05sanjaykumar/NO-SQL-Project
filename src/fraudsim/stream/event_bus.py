"""
Async event bus — pluggable backend.

Default: in-process asyncio.Queue.
Optional: Redis Streams (requires `redis` extra).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from fraudsim.types import Event


class EventBus(ABC):
    """
    Abstract event bus interface.

    All implementations must support publish/subscribe with typed Events.
    """

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publish a single event to the bus."""

    @abstractmethod
    async def publish_batch(self, events: list[Event]) -> None:
        """Publish a batch of events."""

    @abstractmethod
    async def subscribe(self) -> AsyncEventIterator:
        """Return an async iterator over events."""

    @abstractmethod
    async def close(self) -> None:
        """Shut down the bus gracefully."""


class AsyncEventIterator(ABC):
    """Async iterator over events from the bus."""

    @abstractmethod
    def __aiter__(self) -> AsyncEventIterator: ...

    @abstractmethod
    async def __anext__(self) -> Event: ...


# ─── In-process asyncio implementation ────────────────────────────────


class AsyncQueueBus(EventBus):
    """
    In-process event bus backed by asyncio.Queue.

    Good for single-process deployments and testing. Zero external
    dependencies. Events are lost on process crash.
    """

    def __init__(self, maxsize: int = 10_000) -> None:
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    async def publish(self, event: Event) -> None:
        if self._closed:
            raise RuntimeError("Bus is closed")
        await self._queue.put(event)

    async def publish_batch(self, events: list[Event]) -> None:
        for event in events:
            await self.publish(event)

    async def subscribe(self) -> _AsyncQueueIterator:
        return _AsyncQueueIterator(self._queue)

    async def close(self) -> None:
        self._closed = True
        await self._queue.put(None)  # sentinel to unblock subscribers


class _AsyncQueueIterator(AsyncEventIterator):
    """Async iterator for AsyncQueueBus."""

    def __init__(self, queue: asyncio.Queue[Event | None]) -> None:
        self._queue = queue

    def __aiter__(self) -> _AsyncQueueIterator:
        return self

    async def __anext__(self) -> Event:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


# ─── Redis Streams implementation (stub — requires redis extra) ───────


class RedisStreamBus(EventBus):
    """
    Redis Streams backed event bus.

    Provides durability and multi-consumer support. Requires the `redis`
    optional dependency.

    Stub for Milestone 1 — full implementation in Milestone 2.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        stream_name: str = "fraudsim:events",
    ) -> None:
        self._redis_url = redis_url
        self._stream_name = stream_name
        self._client: Any = None

    async def _connect(self) -> None:
        try:
            import redis.asyncio as aioredis
        except ImportError as e:
            raise ImportError(
                "Redis support requires the 'redis' extra: pip install fraudsim[redis]"
            ) from e
        self._client = aioredis.from_url(self._redis_url)

    async def publish(self, event: Event) -> None:
        raise NotImplementedError("Redis Streams bus — Milestone 2")

    async def publish_batch(self, events: list[Event]) -> None:
        raise NotImplementedError("Redis Streams bus — Milestone 2")

    async def subscribe(self) -> AsyncEventIterator:
        raise NotImplementedError("Redis Streams bus — Milestone 2")

    async def close(self) -> None:
        if self._client:
            await self._client.close()


def create_event_bus(backend: str = "asyncio", **kwargs: Any) -> EventBus:
    """
    Factory for creating an event bus.

    Parameters
    ----------
    backend
        "asyncio" (default) or "redis".
    **kwargs
        Passed to the backend constructor.
    """
    if backend == "asyncio":
        return AsyncQueueBus(**kwargs)
    elif backend == "redis":
        return RedisStreamBus(**kwargs)
    else:
        raise ValueError(f"Unknown event bus backend: {backend}")
