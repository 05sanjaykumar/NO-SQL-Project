"""
Structured JSON logging (Milestone 6).

Each run writes separate streams into its run directory:

- events.jsonl      every observable event (the replay input), one per line
- detections.jsonl  every tracked-ring change, with the full explanation when
                    a ring is flagged; carries the batch boundaries (first_seq,
                    last_seq) so a replay can reproduce the exact same batches
- metrics.jsonl     per-batch timings/sizes and the final run summary
- errors.log        errors and exceptions (also mirrored to the console)

Every record carries run_id, component, the simulated timestamp `ts` and, where
it applies, the event_id that caused it. Wall-clock time is kept separately
(`wall_ts`) and is excluded from replay comparisons.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from fraudsim.types import Event


def _wall() -> str:
    return datetime.now(UTC).isoformat()


def configure_console(level: int = logging.INFO) -> None:
    """Console logging as JSON lines with level and timestamp (structlog)."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


class RunLogger:
    """Writes the four log streams of one run."""

    def __init__(self, log_dir: str | Path, run_id: str) -> None:
        self.log_dir = Path(log_dir)
        self.run_id = run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.events_file = self.log_dir / "events.jsonl"
        self.detections_file = self.log_dir / "detections.jsonl"
        self.metrics_file = self.log_dir / "metrics.jsonl"
        self.errors_file = self.log_dir / "errors.log"
        self._handles: dict[Path, Any] = {}
        self._console = structlog.get_logger("fraudsim").bind(run_id=run_id)

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        handle = self._handles.get(path)
        if handle is None:
            handle = self._handles[path] = open(path, "a", encoding="utf-8")  # noqa: SIM115
        handle.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()

    def __enter__(self) -> RunLogger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ─── Streams ──────────────────────────────────────────────────────

    def event(self, event: Event) -> None:
        self._write(self.events_file, {**event.to_dict(), "component": "simulator"})

    def detection(self, record: dict[str, Any], event_id: str | None = None) -> None:
        self._write(
            self.detections_file,
            {
                "run_id": self.run_id,
                "component": "detection",
                "event_id": event_id,
                **record,
                "wall_ts": _wall(),
            },
        )

    def metric(self, record: dict[str, Any], component: str = "metrics") -> None:
        self._write(
            self.metrics_file,
            {"run_id": self.run_id, "component": component, **record, "wall_ts": _wall()},
        )

    def error(self, message: str, component: str = "general", **fields: Any) -> None:
        line = json.dumps(
            {
                "run_id": self.run_id,
                "component": component,
                "level": "error",
                "message": message,
                **fields,
                "wall_ts": _wall(),
            },
            default=str,
        )
        with open(self.errors_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        self._console.error(message, component=component, **fields)


class FraudSimLogger(RunLogger):
    """Backward-compatible facade (Milestone 1 API) over RunLogger."""

    def __init__(self, log_dir: str | Path = "runs/latest", run_id: str = "run_default") -> None:
        super().__init__(log_dir, run_id)

    def _plain(self, path: Path, data: dict[str, Any]) -> None:
        self._write(path, {"run_id": self.run_id, "ts": _wall(), **data})
        self._handles[path].flush()

    def log_event(self, event_data: dict[str, Any]) -> None:
        self._plain(self.events_file, {"component": "event_bus", **event_data})

    def log_detection(self, detection_data: dict[str, Any]) -> None:
        self._plain(self.detections_file, {"component": "detection_engine", **detection_data})

    def log_metric(self, metric_data: dict[str, Any]) -> None:
        self._plain(self.metrics_file, {"component": "metrics", **metric_data})

    def log_error(self, message: str, exc_info: Any = None, **kwargs: Any) -> None:
        self.error(message, exc_info=str(exc_info) if exc_info else None, **kwargs)

    def get_logger(self, component: str = "general") -> Any:
        return self._console.bind(component=component)
