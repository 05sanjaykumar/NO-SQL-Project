"""
Early-warning evaluation: did we warn about a ring before it became active?

For each ground-truth ring, the warning time is the earliest moment a tracked
ring whose members are mostly that ring's members (at least
`min_overlap_members` shared and >= `min_precision` of the tracked members in
the true ring) raised an early warning or an alert. Lead time = true
activation time - warning time; positive means the warning came first, while
the ring was still forming.
"""

from __future__ import annotations

from typing import Any

from fraudsim.detectors.ensemble import RingTracker
from fraudsim.simulator.ground_truth import GroundTruthLedger


def lead_times(
    ledger: GroundTruthLedger,
    tracker: RingTracker,
    min_overlap_members: int = 2,
    min_precision: float = 0.66,
) -> list[dict[str, Any]]:
    out = []
    for gt in sorted(ledger.rings.values(), key=lambda r: r.ring_id):
        everyone = {u for _, action, u in gt.member_history if action == "join"}
        first = None
        via = None
        for ring in tracker.rings.values():
            inter = ring.members & everyone
            if len(inter) < min_overlap_members or len(inter) / len(ring.members) < min_precision:
                continue
            for when, kind in ((ring.early_warned_at, "early_warning"), (ring.alerted_at, "alert")):
                if when is not None and (first is None or when < first):
                    first, via = when, (ring.ring_id, kind)
        lead = (
            (gt.activate_ts - first).total_seconds()
            if first is not None and gt.activate_ts is not None
            else None
        )
        out.append(
            {
                "true_ring": gt.ring_id,
                "type": gt.ring_type.value,
                "evasion": gt.evasion_mode.value,
                "activated": gt.activate_ts.isoformat() if gt.activate_ts else None,
                "first_warning": first.isoformat() if first else None,
                "warned_by": via[0] if via else None,
                "warning_kind": via[1] if via else None,
                "lead_time_s": round(lead, 3) if lead is not None else None,
            }
        )
    return out
