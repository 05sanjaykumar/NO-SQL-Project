"""
Async event bus — pluggable backend.

Default: in-process asyncio.Queue.
Optional: Redis Streams (requires `redis` extra).
"""

from __future__ import annotations

import asyncio
import json
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


# ─── Redis Streams implementation (requires redis extra) ──────────────


_EOS_FIELD = b"eos"


class RedisStreamBus(EventBus):
    """
    Redis Streams backed event bus.

    Each event is one stream entry (`XADD`) holding the JSON-serialised event.
    Subscribers read with blocking `XREAD` from the start of the stream, so a
    subscriber that attaches late still sees every event (same semantics as
    AsyncQueueBus). `close()` appends an end-of-stream marker. Requires the
    `redis` optional dependency; a pre-built client can be injected for tests.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        stream_name: str = "fraudsim:events",
        maxlen: int | None = 1_000_000,
        block_ms: int = 1000,
        client: Any = None,
    ) -> None:
        self._redis_url = redis_url
        self._stream_name = stream_name
        self._maxlen = maxlen
        self._block_ms = block_ms
        self._client: Any = client
        self._closed = False

    @property
    def stream_name(self) -> str:
        return self._stream_name

    async def _connect(self) -> Any:
        if self._client is None:
            try:
                import redis.asyncio as aioredis
            except ImportError as e:
                raise ImportError(
                    "Redis support requires the 'redis' extra: pip install fraudsim[redis]"
                ) from e
            self._client = aioredis.from_url(self._redis_url)
        return self._client

    async def publish(self, event: Event) -> None:
        if self._closed:
            raise RuntimeError("Bus is closed")
        client = await self._connect()
        await client.xadd(
            self._stream_name,
            {"data": json.dumps(event.to_dict(), separators=(",", ":"))},
            maxlen=self._maxlen,
            approximate=True,
        )

    async def publish_batch(self, events: list[Event]) -> None:
        if self._closed:
            raise RuntimeError("Bus is closed")
        client = await self._connect()
        async with client.pipeline(transaction=False) as pipe:
            for event in events:
                pipe.xadd(
                    self._stream_name,
                    {"data": json.dumps(event.to_dict(), separators=(",", ":"))},
                    maxlen=self._maxlen,
                    approximate=True,
                )
            await pipe.execute()

    async def subscribe(self) -> _RedisStreamIterator:
        client = await self._connect()
        return _RedisStreamIterator(client, self._stream_name, self._block_ms)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        client = await self._connect()
        await client.xadd(self._stream_name, {"eos": "1"}, maxlen=self._maxlen, approximate=True)


class _RedisStreamIterator(AsyncEventIterator):
    """Async iterator over a Redis stream, stopping at the end-of-stream marker."""

    def __init__(self, client: Any, stream: str, block_ms: int) -> None:
        self._client = client
        self._stream = stream
        self._block_ms = block_ms
        self._last_id: bytes | str = "0-0"
        self._buffer: list[tuple[Any, dict[Any, Any]]] = []

    def __aiter__(self) -> _RedisStreamIterator:
        return self

    async def __anext__(self) -> Event:
        while not self._buffer:
            resp = await self._client.xread(
                {self._stream: self._last_id}, count=500, block=self._block_ms
            )
            for _stream, entries in resp or []:
                self._buffer.extend(entries)
        entry_id, fields = self._buffer.pop(0)
        self._last_id = entry_id
        if _EOS_FIELD in fields or "eos" in fields:
            self._buffer.clear()
            raise StopAsyncIteration
        raw = fields.get(b"data", fields.get("data"))
        if isinstance(raw, bytes):
            raw = raw.decode()
        return Event.from_dict(json.loads(raw))


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
