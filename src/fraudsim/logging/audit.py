"""
Immutable, hash-chained audit trail (Milestone 6).

Every flag, ring status change, early warning, analyst action and threshold
adaptation is appended to `audit.jsonl`. Each entry stores the SHA-256 of the
previous entry and its own hash over a canonical serialisation, so any edit,
deletion or reordering of past entries is detectable with `verify()`.

System entries carry simulated time and are deterministic, so a replay of the
same event stream must produce byte-identical system entries. Analyst entries
carry wall-clock time. `replay_audit` rebuilds every ring's status history
from the trail alone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64

ACTION_FOR_CHANGE = {
    "created": "RING_CREATED",
    "confirmed": "STATUS_CHANGED",
    "dissolved": "STATUS_CHANGED",
    "merged_into": "STATUS_CHANGED",
    "grew": "MEMBERS_CHANGED",
    "shrank": "MEMBERS_CHANGED",
    "split_from": "RING_CREATED",
    "early_warning": "EARLY_WARNING",
    "alert": "FLAG_RAISED",
}


@dataclass
class AuditEntry:
    """One immutable audit record."""

    audit_id: str
    run_id: str
    ts: str
    action_type: str  # RING_CREATED, STATUS_CHANGED, MEMBERS_CHANGED, EARLY_WARNING,
    # FLAG_RAISED, ANALYST_CONFIRM, ANALYST_DISMISS, THRESHOLD_UPDATED
    ring_id: str
    details: dict[str, Any]
    actor: str = "system"
    seq: int = 0
    prev_hash: str = GENESIS
    hash: str = ""

    def body(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("hash")
        return d


def _digest(body: dict[str, Any]) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class AuditTrail:
    """Append-only, hash-chained audit log."""

    def __init__(self, log_dir: str | Path = "runs/latest", run_id: str = "run_default") -> None:
        self.log_dir = Path(log_dir)
        self.run_id = run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.log_dir / "audit.jsonl"
        self._seq = 0
        self._last = GENESIS
        if self.audit_file.exists():  # continue an existing chain
            for entry in self.read_entries():
                self._seq, self._last = entry.seq, entry.hash

    def record(
        self,
        action_type: str,
        ring_id: str,
        details: dict[str, Any],
        actor: str = "system",
        ts: str | None = None,
    ) -> AuditEntry:
        """Append one entry. System entries should pass the simulated time as ts."""
        self._seq += 1
        entry = AuditEntry(
            audit_id=f"{self.run_id}-{self._seq:07d}",
            run_id=self.run_id,
            ts=ts or datetime.now(UTC).isoformat(),
            action_type=action_type,
            ring_id=ring_id,
            details=details,
            actor=actor,
            seq=self._seq,
            prev_hash=self._last,
        )
        entry.hash = _digest(entry.body())
        self._last = entry.hash
        with open(self.audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), default=str, separators=(",", ":")) + "\n")
        return entry

    def record_update(self, update: Any) -> AuditEntry:
        """Audit one RingUpdate from the tracker or the scoring stage."""
        return self.record(
            ACTION_FOR_CHANGE.get(update.change, "STATUS_CHANGED"),
            update.ring_id,
            {
                "change": update.change,
                "status": update.status.value,
                "members": list(update.members),
                "detectors": list(update.detectors),
                "score": update.score,
                **{k: v for k, v in update.details.items() if k != "reason"},
            },
            ts=update.ts.isoformat(),
        )

    def read_entries(self) -> list[AuditEntry]:
        """All entries in order."""
        if not self.audit_file.exists():
            return []
        with open(self.audit_file, encoding="utf-8") as f:
            return [AuditEntry(**json.loads(line)) for line in f if line.strip()]

    def verify(self) -> tuple[bool, str | None]:
        """Check the hash chain. Returns (ok, description of the first problem)."""
        prev = GENESIS
        for i, entry in enumerate(self.read_entries(), start=1):
            if entry.seq != i:
                return False, f"entry {i}: sequence {entry.seq} (expected {i})"
            if entry.prev_hash != prev:
                return False, f"entry {i}: prev_hash does not match entry {i - 1}"
            if _digest(entry.body()) != entry.hash:
                return False, f"entry {i}: content does not match its hash"
            prev = entry.hash
        return True, None


@dataclass
class RingState:
    ring_id: str
    status: str = "candidate"
    members: list[str] = field(default_factory=list)
    flagged: bool = False
    analyst: str | None = None
    history: list[str] = field(default_factory=list)


def replay_audit(entries: list[AuditEntry]) -> dict[str, RingState]:
    """Rebuild every ring's state from the audit trail alone."""
    rings: dict[str, RingState] = {}
    for e in entries:
        if e.action_type == "THRESHOLD_UPDATED":
            continue
        ring = rings.setdefault(e.ring_id, RingState(e.ring_id))
        d = e.details
        if "status" in d:
            ring.status = d["status"]
        if "members" in d:
            ring.members = list(d["members"])
        if e.action_type == "FLAG_RAISED":
            ring.flagged = True
        if e.action_type in ("ANALYST_CONFIRM", "ANALYST_DISMISS"):
            ring.analyst = e.action_type.split("_")[1].lower()
        ring.history.append(d.get("change", e.action_type))
    return rings


def system_entries(entries: list[AuditEntry]) -> list[dict[str, Any]]:
    """The deterministic part of a trail (system actions), for replay comparison."""
    return [e.body() for e in entries if e.actor == "system"]


def load_events_for_replay(events_file: str | Path) -> Iterator[dict[str, Any]]:
    """Yield logged events from events.jsonl for deterministic replay."""
    path = Path(events_file)
    if not path.exists():
        raise FileNotFoundError(f"Replay event file not found: {path}")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
