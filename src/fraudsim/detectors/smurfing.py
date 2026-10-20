"""
SmurfingDetector: fan-in / fan-out structuring within a time window.

Fan-in: one account receives transfers from at least `min_fan_count`
distinct senders within `time_window_s`, each just under the reporting
threshold (in [band_low * threshold, threshold)). Fan-out is the mirror
image: one account sends such transfers to many distinct receivers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.store import GraphStore, GraphView, Txn
from fraudsim.types import DetectorType, RingCandidate, RingType


@dataclass(slots=True)
class SmurfingConfig(DetectorConfig):
    """Configuration for the SmurfingDetector."""

    detector_type: DetectorType = DetectorType.SMURFING
    name: str = "SmurfingDetector"
    time_window_s: float = 300.0
    min_fan_count: int = 4
    amount_threshold: float = 10_000.0
    band_low: float = 0.8  # "just under" = at least 80% of the threshold
    extra: dict[str, Any] = field(default_factory=dict)


class SmurfingDetector(BaseDetector):
    """Detects fan-in and fan-out bursts of just-under-threshold transfers."""

    incremental = True

    def __init__(self, config: SmurfingConfig | None = None, store: GraphStore | None = None):
        super().__init__(config or SmurfingConfig(), store)
        self.cfg: SmurfingConfig = self._config  # type: ignore[assignment]

    def detect(self, view: GraphView) -> list[RingCandidate]:
        cfg = self.cfg
        lo, hi = cfg.band_low * cfg.amount_threshold, cfg.amount_threshold
        band = [t for t in view.txns if lo <= t.amount < hi and t.sender != t.receiver]
        incoming: dict[str, list[Txn]] = defaultdict(list)
        outgoing: dict[str, list[Txn]] = defaultdict(list)
        for t in band:
            incoming[t.receiver].append(t)
            outgoing[t.sender].append(t)

        candidates = []
        for hub, txns, direction in [
            *((u, ts, "fan_in") for u, ts in sorted(incoming.items())),
            *((u, ts, "fan_out") for u, ts in sorted(outgoing.items())),
        ]:
            window = self._best_window(txns, direction)
            if window is None:
                continue
            others = {t.sender if direction == "fan_in" else t.receiver for t in window}
            ring_type = (
                RingType.SMURFING_FAN_IN if direction == "fan_in" else RingType.SMURFING_FAN_OUT
            )
            amounts = [t.amount for t in window]
            evidence = {
                "direction": direction,
                "hub_account": hub,
                "counterparties": sorted(others),
                "n_transfers": len(window),
                "window_s": round(window[-1].t - window[0].t, 3),
                "amount_min": round(min(amounts), 2),
                "amount_max": round(max(amounts), 2),
                "total": round(sum(amounts), 2),
                "threshold": cfg.amount_threshold,
                "txn_ids": [t.txn_id for t in window],
            }
            score = min(1.0, len(others) / (2 * cfg.min_fan_count))
            candidates.append(self._candidate(ring_type, others | {hub}, view, score, evidence))
        return candidates

    def _best_window(self, txns: list[Txn], direction: str) -> list[Txn] | None:
        """Largest set of distinct counterparties inside any window of time_window_s."""
        txns = sorted(txns, key=lambda t: (t.t, t.txn_id))
        best: list[Txn] | None = None
        best_n = 0
        lo = 0
        for hi in range(len(txns)):
            while txns[hi].t - txns[lo].t > self.cfg.time_window_s:
                lo += 1
            window = txns[lo : hi + 1]
            parties = {t.sender if direction == "fan_in" else t.receiver for t in window}
            if len(parties) >= self.cfg.min_fan_count and len(parties) > best_n:
                best, best_n = window, len(parties)
        return best
