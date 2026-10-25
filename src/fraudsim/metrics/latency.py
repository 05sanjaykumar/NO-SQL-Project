"""
Latency tracking — records per-event and per-batch latencies.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from fraudsim.types import LatencyRecord


class LatencyTracker:
    """
    Collects latency measurements and computes percentile statistics.

    Separates measurements by component (ingest, detect, end_to_end).
    """

    def __init__(self) -> None:
        self._records: dict[str, list[float]] = defaultdict(list)

    def record(self, component: str, duration_ms: float) -> None:
        """Record a single latency measurement."""
        self._records[component].append(duration_ms)

    def add(self, record: LatencyRecord) -> None:
        """Add a LatencyRecord."""
        self._records[record.component].append(record.duration_ms)

    def stats(self, component: str) -> dict[str, float]:
        """Compute p50/p95/p99/mean/min/max for a component."""
        values = self._records.get(component, [])
        if not values:
            return {"count": 0}

        sorted_vals = sorted(values)
        n = len(sorted_vals)
        return {
            "count": n,
            "mean": statistics.mean(sorted_vals),
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "p50": sorted_vals[int(n * 0.50)],
            "p95": sorted_vals[min(int(n * 0.95), n - 1)],
            "p99": sorted_vals[min(int(n * 0.99), n - 1)],
        }

    def all_stats(self) -> dict[str, dict[str, float]]:
        """Compute stats for all components."""
        return {comp: self.stats(comp) for comp in self._records}

    def reset(self) -> None:
        """Clear all recorded latencies."""
        self._records.clear()
