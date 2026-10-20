"""
Deterministic replay (Milestone 6).

`fraudsim replay runs/<run_id>` re-runs the detection engine on a run's logged
event stream, with the same micro-batch boundaries, engine and detector
configuration and risk model (checked by SHA-256), and compares the result
with what was logged:

- detections.jsonl: every ring change and explanation must be identical,
- audit.jsonl: every system entry, including its chain hash, must be identical.

Detection is a pure function of (events, batch boundaries, config, model), so
any difference means non-determinism or a tampered log.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fraudsim.detectors.engine import BatchReport, DetectionEngine, EngineConfig, build_detectors
from fraudsim.graph.store import InMemoryGraphStore
from fraudsim.logging.audit import AuditTrail, system_entries
from fraudsim.types import Event

VOLATILE = ("wall_ts", "component", "run_id")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def detection_records(report: BatchReport, engine: DetectionEngine) -> list[dict[str, Any]]:
    """Canonical, deterministic records of one batch's ring changes."""
    out = []
    for upd in report.updates:
        ring = engine.tracker.rings.get(upd.ring_id)
        rec: dict[str, Any] = {
            "batch": report.batch,
            "first_seq": report.first_seq,
            "last_seq": report.last_seq,
            **upd.to_dict(),
        }
        if ring is not None and upd.change in ("alert", "early_warning", "confirmed"):
            rec["explanation"] = ring.explanation
            rec["risk"] = ring.risk
        out.append(rec)
    return out


def _strip(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in VOLATILE}


def _norm(obj: Any) -> Any:
    """JSON round trip so in-memory objects compare equal to what was logged."""
    return json.loads(json.dumps(obj, default=str))


@dataclass
class ReplayResult:
    run_id: str
    events: int
    batches: int
    detection_records: int
    audit_entries: int
    identical: bool
    differences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "events": self.events,
            "batches": self.batches,
            "detection_records": self.detection_records,
            "audit_entries": self.audit_entries,
            "identical": self.identical,
            "differences": self.differences[:20],
        }


def replay_run(run_dir: str | Path, model_path: str | Path | None = None) -> ReplayResult:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    run_id = manifest["run_id"]
    events = [
        Event.from_dict(json.loads(line))
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_seq = {e.seq: e for e in events}
    bounds = [
        (b["first_seq"], b["last_seq"])
        for b in (
            json.loads(line)
            for line in (run_dir / "batches.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    ]

    engine_cfg = EngineConfig(**manifest["engine"])
    scoring = None
    model = manifest.get("model")
    if model:
        from fraudsim.scoring.scorer import RingScorer
        from fraudsim.scoring.stage import ScoringStage

        path = Path(model_path or model["path"])
        if file_sha256(path) != model["sha256"]:
            raise ValueError(f"model {path} differs from the one used by run {run_id} (sha256)")
        scoring = ScoringStage(scorer=RingScorer.load(path))

    store = InMemoryGraphStore()
    engine = DetectionEngine(
        store,
        build_detectors(store, manifest.get("detector_overrides") or {}),
        config=engine_cfg,
        scorer=scoring,
    )
    replayed: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        audit = AuditTrail(tmp, run_id)
        for first, last in bounds:
            report = engine.process([by_seq[s] for s in range(first, last + 1)])
            replayed += detection_records(report, engine)
            for upd in report.updates:
                audit.record_update(upd)
        replay_audit = system_entries(audit.read_entries())

    logged = [
        _strip(json.loads(line))
        for line in (run_dir / "detections.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for rec in logged:
        rec.pop("event_id", None)
    logged_audit = system_entries(AuditTrail(run_dir, run_id).read_entries())

    diffs: list[str] = []
    replayed_n = [_norm(r) for r in replayed]
    if len(replayed_n) != len(logged):
        diffs.append(f"detections: {len(logged)} logged vs {len(replayed_n)} replayed")
    for i, (a, b) in enumerate(zip(logged, replayed_n, strict=False)):
        if a != b:
            keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            diffs.append(f"detection #{i + 1} (batch {a.get('batch')}): differs in {keys}")
    if len(replay_audit) != len(logged_audit):
        diffs.append(f"audit: {len(logged_audit)} logged vs {len(replay_audit)} replayed")
    for i, (a, b) in enumerate(zip(logged_audit, replay_audit, strict=False)):
        if _norm(a) != _norm(b):
            diffs.append(f"audit entry {i + 1}: differs")
            break
    return ReplayResult(
        run_id=run_id,
        events=len(events),
        batches=len(bounds),
        detection_records=len(logged),
        audit_entries=len(logged_audit),
        identical=not diffs,
        differences=diffs,
    )
