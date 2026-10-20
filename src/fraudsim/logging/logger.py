"""
Structured JSON logging using structlog and file streams.

Produces separate streams:
- events.jsonl: Ingested and emitted events
- detections.jsonl: Detected ring candidates and confirmed rings with explanations
- metrics.jsonl: Latency, accuracy, throughput snapshots
- errors.log: Error and exception logs
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog


class FraudSimLogger:
    """Centralized structured logger for fraud simulation runs."""

    def __init__(self, log_dir: str | Path = "runs/latest", run_id: str = "run_default") -> None:
        self.log_dir = Path(log_dir)
        self.run_id = run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.events_file = self.log_dir / "events.jsonl"
        self.detections_file = self.log_dir / "detections.jsonl"
        self.metrics_file = self.log_dir / "metrics.jsonl"
        self.errors_file = self.log_dir / "errors.log"

        # Setup standard Python logging configuration
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=logging.INFO,
        )

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=False,
        )
        self._logger = structlog.get_logger()

    def _write_jsonl(self, filepath: Path, data: dict[str, Any]) -> None:
        payload = {
            "run_id": self.run_id,
            "ts": datetime.now(UTC).isoformat(),
            **data,
        }
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")

    def log_event(self, event_data: dict[str, Any]) -> None:
        """Log an ingested or generated event."""
        self._write_jsonl(self.events_file, {"component": "event_bus", **event_data})

    def log_detection(self, detection_data: dict[str, Any]) -> None:
        """Log a detector candidate or confirmed ring flag with explanation."""
        self._write_jsonl(self.detections_file, {"component": "detection_engine", **detection_data})

    def log_metric(self, metric_data: dict[str, Any]) -> None:
        """Log point-in-time metrics record."""
        self._write_jsonl(self.metrics_file, {"component": "metrics", **metric_data})

    def log_error(self, message: str, exc_info: Any = None, **kwargs: Any) -> None:
        """Log an error to errors.log and stdout."""
        ts = datetime.now(UTC).isoformat()
        entry = f"[{ts}] [{self.run_id}] ERROR: {message} {kwargs}\n"
        if exc_info:
            entry += f"{exc_info}\n"
        with open(self.errors_file, "a", encoding="utf-8") as f:
            f.write(entry)
        self._logger.error(message, run_id=self.run_id, **kwargs)

    def get_logger(self, component: str = "general") -> Any:
        """Get a bound structlog logger."""
        return self._logger.bind(run_id=self.run_id, component=component)
