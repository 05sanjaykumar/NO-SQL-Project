"""
Immutable audit trail and replay utilities.

Records every flag, status change, and analyst action (confirm/dismiss).
Provides deterministic event stream replay capabilities.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class AuditEntry:
    """An immutable audit trail entry."""

    audit_id: str
    run_id: str
    ts: str
    action_type: (
        str  # FLAG_DETECTED, STATUS_CHANGED, ANALYST_CONFIRM, ANALYST_DISMISS, THRESHOLD_UPDATED
    )
    ring_id: str
    details: dict[str, Any]
    actor: str = "system"  # 'system' or analyst username


class AuditTrail:
    """Manages append-only immutable audit trail logs."""

    def __init__(self, log_dir: str | Path = "runs/latest", run_id: str = "run_default") -> None:
        self.log_dir = Path(log_dir)
        self.run_id = run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.log_dir / "audit.jsonl"

    def record(
        self,
        action_type: str,
        ring_id: str,
        details: dict[str, Any],
        actor: str = "system",
    ) -> AuditEntry:
        """Append an audit record to the immutable audit trail."""
        import uuid

        entry = AuditEntry(
            audit_id=uuid.uuid4().hex[:12],
            run_id=self.run_id,
            ts=datetime.now(UTC).isoformat(),
            action_type=action_type,
            ring_id=ring_id,
            details=details,
            actor=actor,
        )
        with open(self.audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), default=str) + "\n")
        return entry

    def read_entries(self) -> list[AuditEntry]:
        """Read all audit entries in chronological order."""
        if not self.audit_file.exists():
            return []
        entries: list[AuditEntry] = []
        with open(self.audit_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    entries.append(AuditEntry(**data))
        return entries


def load_events_for_replay(events_file: str | Path) -> Iterator[dict[str, Any]]:
    """Yield logged events from events.jsonl for deterministic simulation replay."""
    path = Path(events_file)
    if not path.exists():
        raise FileNotFoundError(f"Replay event file not found: {path}")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
