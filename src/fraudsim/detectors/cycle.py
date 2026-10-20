"""
TemporalCycleDetector: bounded-length, time-windowed directed money cycles.

A cycle u0 -> u1 -> ... -> u(L-1) -> u0 is reported when
- 3 <= L <= max_cycle_length and all members are distinct,
- the transfers happen in time order and within `time_window_s` of the first,
- amounts are consistent: each hop passes on between (1 - max_hop_loss) and
  (1 + amount_tolerance) of what it received (money is relayed, minus a cut).

Random benign payments close cycles by chance, but with unrelated amounts,
so the amount check is what separates relaying from coincidence.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.store import GraphStore, GraphView, Txn
from fraudsim.types import DetectorType, RingCandidate, RingType


@dataclass(slots=True)
class TemporalCycleConfig(DetectorConfig):
    """Configuration for the TemporalCycleDetector."""

    detector_type: DetectorType = DetectorType.TEMPORAL_CYCLE
    name: str = "TemporalCycleDetector"
    min_cycle_length: int = 3
    max_cycle_length: int = 6
    time_window_s: float = 900.0
    max_hop_loss: float = 0.10  # a hop may keep up to 10% as its cut
    amount_tolerance: float = 0.02  # or pass on up to 2% more (rounding, fees)
    min_amount: float = 0.0
    max_expansions: int = 200_000  # search budget per call
    extra: dict[str, Any] = field(default_factory=dict)


class TemporalCycleDetector(BaseDetector):
    """Finds amount-consistent, time-ordered directed cycles of length 3..6."""

    incremental = True

    def __init__(self, config: TemporalCycleConfig | None = None, store: GraphStore | None = None):
        super().__init__(config or TemporalCycleConfig(), store)
        self.cfg: TemporalCycleConfig = self._config  # type: ignore[assignment]

    def detect(self, view: GraphView) -> list[RingCandidate]:
        cfg = self.cfg
        out_edges: dict[str, list[Txn]] = defaultdict(list)
        for txn in sorted(view.txns, key=lambda t: (t.t, t.txn_id)):
            if txn.sender != txn.receiver and txn.amount >= cfg.min_amount:
                out_edges[txn.sender].append(txn)

        found: dict[frozenset[str], list[Txn]] = {}
        budget = [cfg.max_expansions]

        def consistent(prev: Txn, nxt: Txn) -> bool:
            return (
                prev.amount * (1.0 - cfg.max_hop_loss)
                <= nxt.amount
                <= prev.amount * (1.0 + cfg.amount_tolerance)
            )

        def extend(path: list[Txn], visited: set[str]) -> None:
            if budget[0] <= 0:
                return
            last = path[-1]
            start = path[0]
            for nxt in out_edges.get(last.receiver, ()):
                budget[0] -= 1
                if nxt.t < last.t or nxt.t - start.t > cfg.time_window_s:
                    continue
                if not consistent(last, nxt):
                    continue
                if nxt.receiver == start.sender:
                    length = len(path) + 1
                    if length >= cfg.min_cycle_length:
                        members = frozenset(t.sender for t in path) | {nxt.sender}
                        if len(members) == length and members not in found:
                            found[members] = [*path, nxt]
                    continue
                if nxt.receiver in visited or len(path) + 1 >= cfg.max_cycle_length:
                    continue
                visited.add(nxt.receiver)
                path.append(nxt)
                extend(path, visited)
                path.pop()
                visited.discard(nxt.receiver)

        for txns in out_edges.values():
            for first in txns:
                extend([first], {first.sender, first.receiver})

        candidates = []
        for members, path in sorted(found.items(), key=lambda kv: sorted(kv[0])):
            amounts = [t.amount for t in path]
            losses = [1 - b / a for a, b in itertools.pairwise(amounts)]
            evidence = {
                "cycle": [t.sender for t in path] + [path[0].sender],
                "length": len(path),
                "txn_ids": [t.txn_id for t in path],
                "amounts": [round(a, 2) for a in amounts],
                "duration_s": round(path[-1].t - path[0].t, 3),
                "mean_hop_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
                "search_budget_exhausted": budget[0] <= 0,
            }
            candidates.append(
                self._candidate(RingType.MONEY_CYCLE, set(members), view, 1.0, evidence)
            )
        return candidates
