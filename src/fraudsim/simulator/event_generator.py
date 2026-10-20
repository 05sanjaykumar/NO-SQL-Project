"""
Stub event generator — will be fully implemented in Milestone 2.

For Milestone 1 this provides the interface and a minimal generator that
replays the existing seed.py data as events (backward compatibility).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from fraudsim.types import Event, EventType, new_event_id


class EventGenerator:
    """
    Generates a stream of simulation events.

    Milestone 1: Minimal implementation that converts the existing seed.py
    graph into a sequence of events with timestamps and event_ids.

    Milestone 2 will add: continuous stream, controllable rate, pause/resume,
    fraud ring lifecycle, evasion modes, etc.
    """

    def __init__(
        self,
        seed: int = 42,
        run_id: str = "",
        config: dict[str, Any] | None = None,
    ) -> None:
        self._rng = random.Random(seed)
        self._run_id = run_id
        self._config = config or {}
        self._seq = 0
        self._base_ts = datetime.now(UTC)

    def _next_event(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        ts_offset_ms: int = 0,
    ) -> Event:
        """Create the next event with a monotonically increasing sequence number."""
        self._seq += 1
        ts = self._base_ts + timedelta(milliseconds=ts_offset_ms)
        return Event(
            event_id=new_event_id(),
            event_type=event_type,
            ts=ts,
            payload=payload,
            run_id=self._run_id,
            seq=self._seq,
        )

    def generate_seed_events(self) -> list[Event]:
        """
        Convert the existing seed.py graph into a sequence of events.

        This is a backward-compatibility bridge: the planted rings become
        proper events with timestamps, allowing the new detection pipeline
        to process the original dataset.
        """
        from fraudsim.simulator._seed_bridge import seed_to_events

        return seed_to_events(self._run_id, self._base_ts)
