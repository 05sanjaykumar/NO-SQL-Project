"""
Ground-truth ledger for the simulation.

Tracks every fraud ring from spawn to dissolution. This data is NEVER
accessible to detectors — it exists purely for evaluation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fraudsim.types import (
    EvasionMode,
    GroundTruthRing,
    RingLifecycle,
    RingType,
)


class GroundTruthLedger:
    """
    Immutable-append ground-truth ledger.

    Records the full lifecycle of every fraud ring. Provides query methods
    for metrics evaluation but NEVER exposes data to detection components.
    """

    def __init__(self) -> None:
        self._rings: dict[str, GroundTruthRing] = {}
        self._log: list[dict[str, Any]] = []

    @property
    def rings(self) -> dict[str, GroundTruthRing]:
        """All tracked rings, keyed by ring_id."""
        return dict(self._rings)

    def active_rings(self, at: datetime | None = None) -> list[GroundTruthRing]:
        """Return rings that are currently ACTIVE (or GROWING/MUTATING)."""
        active_states = {
            RingLifecycle.GROWING,
            RingLifecycle.ACTIVE,
            RingLifecycle.MUTATING,
        }
        result = []
        for ring in self._rings.values():
            if ring.lifecycle in active_states and (
                at is None
                or (
                    ring.spawn_ts
                    and ring.spawn_ts <= at
                    and (ring.dissolve_ts is None or ring.dissolve_ts > at)
                )
            ):
                result.append(ring)
        return result

    def spawn_ring(
        self,
        ring_id: str,
        ring_type: RingType,
        members: set[str],
        ts: datetime,
        evasion_mode: EvasionMode = EvasionMode.NONE,
        shared_attributes: dict[str, str] | None = None,
    ) -> GroundTruthRing:
        """Register a new fraud ring."""
        ring = GroundTruthRing(
            ring_id=ring_id,
            ring_type=ring_type,
            evasion_mode=evasion_mode,
            lifecycle=RingLifecycle.SPAWNED,
            members=set(members),
            shared_attributes=shared_attributes or {},
            spawn_ts=ts,
        )
        for uid in members:
            ring.member_history.append((ts, "join", uid))

        self._rings[ring_id] = ring
        self._append_log("spawn", ring_id, ts, {"members": sorted(members)})
        return ring

    def activate_ring(self, ring_id: str, ts: datetime) -> None:
        """Mark a ring as ACTIVE."""
        ring = self._rings[ring_id]
        ring.lifecycle = RingLifecycle.ACTIVE
        ring.activate_ts = ts
        self._append_log("activate", ring_id, ts)

    def grow_ring(self, ring_id: str, user_id: str, ts: datetime) -> None:
        """Add a member to a ring."""
        ring = self._rings[ring_id]
        ring.lifecycle = RingLifecycle.GROWING
        ring.add_member(user_id, ts)
        self._append_log("grow", ring_id, ts, {"user_id": user_id})

    def mutate_ring(
        self, ring_id: str, ts: datetime, changes: dict[str, Any] | None = None
    ) -> None:
        """Record a mutation (e.g. swap a device or card)."""
        ring = self._rings[ring_id]
        ring.lifecycle = RingLifecycle.MUTATING
        self._append_log("mutate", ring_id, ts, changes)

    def dissolve_ring(self, ring_id: str, ts: datetime) -> None:
        """Mark a ring as DISSOLVED."""
        ring = self._rings[ring_id]
        ring.lifecycle = RingLifecycle.DISSOLVED
        ring.dissolve_ts = ts
        self._append_log("dissolve", ring_id, ts)

    def get_members_at(self, ring_id: str, at: datetime) -> set[str]:
        """Return the member set of a ring at a given point in time."""
        ring = self._rings[ring_id]
        members: set[str] = set()
        for ts, action, uid in ring.member_history:
            if ts > at:
                break
            if action == "join":
                members.add(uid)
            elif action == "leave":
                members.discard(uid)
        return members

    def _append_log(
        self,
        action: str,
        ring_id: str,
        ts: datetime,
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "action": action,
            "ring_id": ring_id,
            "ts": ts.isoformat(),
        }
        if details:
            entry["details"] = details
        self._log.append(entry)

    def export_jsonl(self, path: Path) -> None:
        """Write the full ledger to a JSONL file."""
        with open(path, "w") as f:
            for ring in self._rings.values():
                f.write(json.dumps(ring.to_dict(), default=str) + "\n")

    def export_log(self, path: Path) -> None:
        """Write the event log to a JSONL file."""
        with open(path, "w") as f:
            for entry in self._log:
                f.write(json.dumps(entry, default=str) + "\n")

    def summary(self) -> dict[str, Any]:
        """Return a summary of the ledger state."""
        by_type: dict[str, int] = {}
        by_lifecycle: dict[str, int] = {}
        by_evasion: dict[str, int] = {}
        for ring in self._rings.values():
            by_type[ring.ring_type.value] = by_type.get(ring.ring_type.value, 0) + 1
            by_lifecycle[ring.lifecycle.value] = by_lifecycle.get(ring.lifecycle.value, 0) + 1
            by_evasion[ring.evasion_mode.value] = by_evasion.get(ring.evasion_mode.value, 0) + 1

        return {
            "total_rings": len(self._rings),
            "by_type": by_type,
            "by_lifecycle": by_lifecycle,
            "by_evasion": by_evasion,
            "total_log_entries": len(self._log),
        }
