"""
Ring feature extraction (Milestone 4).

Features describe a tracked ring as it looks *now* in the graph: they are
computed from a GraphView of the ring's members (their links, the global
degree of each linked attribute, and transactions among them inside the
window), the members' account creation times, and the ring's tracking
history. No ground truth is used.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from fraudsim.detectors.cycle import TemporalCycleDetector
from fraudsim.detectors.hubaware import hub_weight
from fraudsim.graph.store import GraphView

REPORTING_THRESHOLD = 10_000.0

FEATURE_NAMES: tuple[str, ...] = (
    "size",
    "density",
    "share_device",
    "share_card",
    "share_ip",
    "hub_adjusted_weight",
    "cycle_present",
    "cycle_length",
    "temporal_burstiness",
    "account_age_similarity",
    "account_age_min",
    "internal_txns_per_member",
    "amount_mean_log",
    "amount_cv",
    "near_threshold_frac",
    "growth_rate",
    "risk_signal_fraction",
    "hub_exposure",
    "n_detectors",
)

# Human-readable names and units, used by explanations and counterfactuals.
FEATURE_LABELS: dict[str, str] = {
    "size": "ring size (members)",
    "density": "share of member pairs directly linked",
    "share_device": "share of members on one device",
    "share_card": "share of members on one card",
    "share_ip": "share of members on one IP",
    "hub_adjusted_weight": "hub-adjusted sharing weight per pair",
    "cycle_present": "money returns to its origin",
    "cycle_length": "money-cycle length",
    "temporal_burstiness": "how tightly in time members linked their shared attributes",
    "account_age_similarity": "similarity of member account ages",
    "account_age_min": "age of the oldest member account (minutes, log scale)",
    "internal_txns_per_member": "transfers between members, per member",
    "amount_mean_log": "typical transfer size (log)",
    "amount_cv": "variation of transfer amounts",
    "near_threshold_frac": "share of transfers just under the 10k reporting threshold",
    "growth_rate": "members added per minute",
    "risk_signal_fraction": "share of members also in another flagged group",
    "hub_exposure": "busiest shared attribute (log users)",
    "n_detectors": "number of detectors that found it",
}


@dataclass(frozen=True, slots=True)
class RingFeatures:
    """Feature vector for a tracked ring at one point in time."""

    ring_id: str
    size: float = 0.0
    density: float = 0.0
    share_device: float = 0.0
    share_card: float = 0.0
    share_ip: float = 0.0
    hub_adjusted_weight: float = 0.0
    cycle_present: float = 0.0
    cycle_length: float = 0.0
    temporal_burstiness: float = 0.0
    account_age_similarity: float = 0.0
    account_age_min: float = 0.0
    internal_txns_per_member: float = 0.0
    amount_mean_log: float = 0.0
    amount_cv: float = 0.0
    near_threshold_frac: float = 0.0
    growth_rate: float = 0.0
    risk_signal_fraction: float = 0.0
    hub_exposure: float = 0.0
    n_detectors: float = 0.0

    def to_vector(self) -> list[float]:
        return [float(getattr(self, name)) for name in FEATURE_NAMES]

    def to_dict(self) -> dict[str, float]:
        d = asdict(self)
        d.pop("ring_id")
        return d

    @staticmethod
    def feature_names() -> list[str]:
        return list(FEATURE_NAMES)


@dataclass(slots=True)
class RingContext:
    """What the extractor needs to know about a ring besides the graph."""

    ring_id: str
    members: set[str]
    detectors: set[str]
    first_seen_t: float
    member_history: list[tuple[float, int]]  # (t, size) whenever membership changed
    other_flagged_users: set[str]
    cycle_evidence: dict[str, Any] | None = None


def extract_features(
    ctx: RingContext,
    view: GraphView,
    joined: dict[str, float],
    burst_window_s: float = 300.0,
) -> RingFeatures:
    """Compute the feature vector of one ring from a view of its members."""
    members = sorted(ctx.members & view.users) or sorted(ctx.members)
    n = len(members)
    member_set = set(members)
    now = view.now_t

    # Attributes held by at least two members.
    holders: dict[tuple[str, str], list[float]] = defaultdict(list)
    for lk in view.links:
        if lk.user in member_set:
            holders[(lk.kind, lk.attr)].append(lk.t)
    shared = {a: ts for a, ts in holders.items() if len(ts) >= 2}

    by_attr_users: dict[tuple[str, str], set[str]] = defaultdict(set)
    for lk in view.links:
        if lk.user in member_set and (lk.kind, lk.attr) in shared:
            by_attr_users[(lk.kind, lk.attr)].add(lk.user)

    linked_pairs: set[tuple[str, str]] = set()
    pair_weight: dict[tuple[str, str], float] = defaultdict(float)
    for attr, users in by_attr_users.items():
        h = hub_weight(view.attr_degree.get(attr, len(users)), "inverse_log_degree")
        ordered = sorted(users)
        for i, u in enumerate(ordered):
            for v in ordered[i + 1 :]:
                linked_pairs.add((u, v))
                pair_weight[(u, v)] += h
    internal = [t for t in view.txns if t.sender in member_set and t.receiver in member_set]
    for t in internal:
        if t.sender != t.receiver:
            linked_pairs.add((min(t.sender, t.receiver), max(t.sender, t.receiver)))
    n_pairs = n * (n - 1) / 2 if n > 1 else 1.0

    def coverage(kind: str) -> float:
        best = max((len(us) for (k, _), us in by_attr_users.items() if k == kind), default=0)
        return best / n if n else 0.0

    # Burstiness: how close in time members linked to the most shared attribute.
    burst = 0.0
    if shared:
        top = max(shared, key=lambda a: (len(shared[a]), a))
        span = max(shared[top]) - min(shared[top])
        burst = max(0.0, 1.0 - span / burst_window_s)

    ages = [max(0.0, now - joined[u]) for u in members if u in joined]
    if len(ages) >= 2:
        mean_age = statistics.mean(ages)
        sim = 1.0 - min(1.0, statistics.pstdev(ages) / (mean_age + 60.0))
    else:
        sim = 0.0
    oldest_min = math.log1p(max(ages) / 60.0) if ages else 0.0

    amounts = [t.amount for t in internal]
    mean_amt = statistics.mean(amounts) if amounts else 0.0
    cv = statistics.pstdev(amounts) / mean_amt if len(amounts) >= 2 and mean_amt > 0 else 0.0
    near = (
        sum(1 for a in amounts if 0.8 * REPORTING_THRESHOLD <= a < REPORTING_THRESHOLD)
        / len(amounts)
        if amounts
        else 0.0
    )

    cycle_len = 0
    if ctx.cycle_evidence:
        cycle_len = int(ctx.cycle_evidence.get("length", 0))
    if not cycle_len and len(internal) >= 3:
        sub = GraphView(now, view.since_t, member_set, [], {}, internal)
        found = TemporalCycleDetector().detect(sub)
        cycle_len = max((int(c.evidence["length"]) for c in found), default=0)

    growth = 0.0
    if ctx.member_history:
        t0, size0 = ctx.member_history[0]
        minutes = max((now - t0) / 60.0, 0.5)
        growth = max(0.0, (n - size0) / minutes)

    return RingFeatures(
        ring_id=ctx.ring_id,
        size=float(n),
        density=len(linked_pairs) / n_pairs if n > 1 else 0.0,
        share_device=coverage("device"),
        share_card=coverage("card"),
        share_ip=coverage("ip"),
        hub_adjusted_weight=sum(pair_weight.values()) / n_pairs if n > 1 else 0.0,
        cycle_present=1.0 if cycle_len else 0.0,
        cycle_length=float(cycle_len),
        temporal_burstiness=burst,
        account_age_similarity=sim,
        account_age_min=oldest_min,
        internal_txns_per_member=len(internal) / n if n else 0.0,
        amount_mean_log=math.log1p(mean_amt),
        amount_cv=cv,
        near_threshold_frac=near,
        growth_rate=growth,
        risk_signal_fraction=len(member_set & ctx.other_flagged_users) / n if n else 0.0,
        hub_exposure=math.log1p(max((view.attr_degree.get(a, 0) for a in shared), default=0)),
        n_detectors=float(len(ctx.detectors)),
    )
