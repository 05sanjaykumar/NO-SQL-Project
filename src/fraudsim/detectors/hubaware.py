"""
HubAwareSharedAttribute detector (research contribution C1).

Replaces the hard degree cap with continuous hub-aware weighting. For two
users u, v in the region, the edge weight is

    W(u, v) = sum over shared attributes a of  h(deg(a)) * decay(age_a(u, v))
            + beta * sum over transactions between u and v of  decay(age_txn)

where h is the hub weight (default 1 / ln(1 + deg), so a device shared by 3
weighs 0.72 and an office IP shared by 50 weighs 0.25), age_a(u, v) is the
time since the *older* of the two links to a was last refreshed, and decay
is exponential with half-life `time_decay_half_life_s`. Busy hubs contribute
little on their own, but a group that shares a hub *and* moves money among
itself accumulates weight, so rings hiding behind hubs can still surface.

Candidate rings are dense groups in the thresholded projection: edges with
W >= edge_threshold (and a non-zero shared-attribute part), connected
components peeled down to cores with edge density >= min_edge_density and
size in [min_ring_size, max_ring_size].
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fraudsim.detectors.base import BaseDetector, DetectorConfig
from fraudsim.graph.store import Attr, GraphStore, GraphView
from fraudsim.types import DetectorType, RingCandidate, RingType

HUB_WEIGHTS = ("inverse_log_degree", "adamic_adar", "inverse_degree", "none")
_KIND_TYPE = {
    "device": RingType.SHARED_DEVICE,
    "card": RingType.SHARED_CARD,
    "ip": RingType.SHARED_IP,
}


def hub_weight(degree: int, method: str) -> float:
    """Weight of one shared attribute as a function of its degree (number of users)."""
    d = max(degree, 2)
    if method == "inverse_log_degree":
        return 1.0 / math.log(1.0 + d)
    if method == "adamic_adar":
        return min(1.0, 1.0 / math.log(d))
    if method == "inverse_degree":
        return 1.0 / (d - 1)
    if method == "none":  # ablation: no hub weighting
        return 1.0
    raise ValueError(f"unknown hub weight method {method!r}; expected one of {HUB_WEIGHTS}")


def decay(age_s: float, half_life_s: float | None) -> float:
    if half_life_s is None or half_life_s <= 0:
        return 1.0  # ablation: no time decay
    return math.exp(-math.log(2.0) * max(0.0, age_s) / half_life_s)


@dataclass(slots=True)
class HubAwareConfig(DetectorConfig):
    """Configuration for the HubAwareSharedAttribute detector."""

    detector_type: DetectorType = DetectorType.HUB_AWARE
    name: str = "HubAwareSharedAttribute"
    weight_method: str = "inverse_log_degree"
    time_decay_half_life_s: float | None = 300.0
    txn_weight: float = 0.15  # beta: weight per (decayed) transaction between the pair
    max_txn_weight: float = 0.6  # cap on the transaction part of one pair
    edge_threshold: float = 0.5
    min_ring_size: int = 3
    max_ring_size: int = 25
    min_edge_density: float = 0.75
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PairWeight:
    attr: float = 0.0
    txn: float = 0.0
    shared: list[Attr] = field(default_factory=list)
    n_txns: int = 0

    @property
    def total(self) -> float:
        return self.attr + self.txn


class HubAwareSharedAttribute(BaseDetector):
    """Weighted user-user projection with hub-aware edge weighting."""

    incremental = True

    def __init__(self, config: HubAwareConfig | None = None, store: GraphStore | None = None):
        super().__init__(config or HubAwareConfig(), store)
        self.cfg: HubAwareConfig = self._config  # type: ignore[assignment]
        hub_weight(3, self.cfg.weight_method)  # validate early

    # ─── Projection ───────────────────────────────────────────────────

    def pair_weights(self, view: GraphView) -> dict[tuple[str, str], PairWeight]:
        cfg = self.cfg
        pairs: dict[tuple[str, str], PairWeight] = defaultdict(PairWeight)
        for attr, links in view.links_by_attr().items():
            if len(links) < 2:
                continue
            h = hub_weight(view.attr_degree.get(attr, len(links)), cfg.weight_method)
            ordered = sorted(links, key=lambda lk: lk.user)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    age = view.now_t - min(a.t, b.t)
                    pw = pairs[(a.user, b.user)]
                    pw.attr += h * decay(age, cfg.time_decay_half_life_s)
                    pw.shared.append(attr)
        if cfg.txn_weight > 0:
            for txn in view.txns:
                if txn.sender == txn.receiver:
                    continue
                key = (min(txn.sender, txn.receiver), max(txn.sender, txn.receiver))
                if key not in pairs:
                    continue  # transactions only reinforce pairs that share an attribute
                pw = pairs[key]
                pw.n_txns += 1
                pw.txn = min(
                    cfg.max_txn_weight,
                    pw.txn + cfg.txn_weight * decay(view.now_t - txn.t, cfg.time_decay_half_life_s),
                )
        return pairs

    # ─── Detection ────────────────────────────────────────────────────

    def detect(self, view: GraphView) -> list[RingCandidate]:
        cfg = self.cfg
        pairs = self.pair_weights(view)
        adj: dict[str, dict[str, float]] = defaultdict(dict)
        for (u, v), pw in pairs.items():
            if pw.attr > 0 and pw.total >= cfg.edge_threshold:
                adj[u][v] = pw.total
                adj[v][u] = pw.total

        candidates: list[RingCandidate] = []
        remaining = set(adj)
        while True:
            groups = [g for g in _components(adj, remaining) if len(g) >= cfg.min_ring_size]
            if not groups:
                break
            progressed = False
            for group in groups:
                core = _peel(adj, group, cfg.min_edge_density, cfg.max_ring_size, cfg.min_ring_size)
                if core:
                    candidates.append(self._describe(core, adj, pairs, view))
                    remaining -= core
                    progressed = True
                else:
                    remaining -= group
            if not progressed:
                break
        return candidates

    def _describe(
        self,
        core: set[str],
        adj: dict[str, dict[str, float]],
        pairs: dict[tuple[str, str], PairWeight],
        view: GraphView,
    ) -> RingCandidate:
        members = sorted(core)
        edges = [
            (u, v, adj[u][v])
            for i, u in enumerate(members)
            for v in members[i + 1 :]
            if v in adj[u]
        ]
        n_pairs = len(members) * (len(members) - 1) / 2
        density = len(edges) / n_pairs
        mean_w = sum(w for _, _, w in edges) / len(edges)

        # Which attributes hold the group together, and how busy are they?
        coverage: dict[Attr, set[str]] = defaultdict(set)
        by_user = view.links_by_user()
        for u in members:
            for lk in by_user.get(u, ()):
                coverage[(lk.kind, lk.attr)].add(u)
        shared = {a: us for a, us in coverage.items() if len(us) >= 2}
        dominant = max(shared.items(), key=lambda kv: (len(kv[1]), kv[0]), default=None)
        if dominant and len(dominant[1]) >= 0.75 * len(members):
            ring_type = _KIND_TYPE[dominant[0][0]]
        else:
            ring_type = RingType.MIXED_ATTRIBUTE
        n_txns = sum(pairs[(u, v)].n_txns for u, v, _ in edges)
        hub_exposure = max((view.attr_degree.get(a, 0) for a in shared), default=0)
        evidence = {
            "edges": [[u, v, round(w, 4)] for u, v, w in edges],
            "edge_density": round(density, 4),
            "mean_edge_weight": round(mean_w, 4),
            "internal_txns": n_txns,
            "shared_attributes": [
                {
                    "kind": a[0],
                    "attr": a[1],
                    "degree": view.attr_degree.get(a, 0),
                    "members": sorted(us),
                    "hub_weight": round(
                        hub_weight(view.attr_degree.get(a, 2), self.cfg.weight_method), 4
                    ),
                }
                for a, us in sorted(shared.items())
            ],
            "max_attribute_degree": hub_exposure,
            "weight_method": self.cfg.weight_method,
            "half_life_s": self.cfg.time_decay_half_life_s,
        }
        score = min(1.0, mean_w) * density
        return self._candidate(
            ring_type,
            core,
            view,
            score,
            evidence,
            {f"{a[0]}:{a[1]}": len(us) for a, us in sorted(shared.items())},
        )


def _components(adj: dict[str, dict[str, float]], nodes: set[str]) -> list[set[str]]:
    seen: set[str] = set()
    out: list[set[str]] = []
    for start in sorted(nodes):
        if start in seen:
            continue
        comp = {start}
        stack = [start]
        seen.add(start)
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v in nodes and v not in seen:
                    seen.add(v)
                    comp.add(v)
                    stack.append(v)
        out.append(comp)
    return out


def _peel(
    adj: dict[str, dict[str, float]],
    group: set[str],
    min_density: float,
    max_size: int,
    min_size: int,
) -> set[str] | None:
    """
    Remove the weakest member (fewest edges, then lowest total weight, then id)
    until the group is dense and small enough. Degrees are updated
    incrementally and the weakest member comes from a lazy heap, so peeling
    a component of n users with m edges costs O((n + m) log n).
    """
    core = set(group)
    degree = {u: sum(1 for v in adj[u] if v in core) for u in core}
    strength = {u: sum(w for v, w in adj[u].items() if v in core) for u in core}
    edges = sum(degree.values()) // 2
    heap = [(degree[u], strength[u], u) for u in core]
    heapq.heapify(heap)
    while len(core) >= min_size:
        n = len(core)
        if 2 * edges / (n * (n - 1)) >= min_density and n <= max_size:
            comps = _components(adj, core)  # a dense core must still be connected
            if len(comps) == 1:
                return core
            keep = max(comps, key=lambda c: (len(c), sorted(c)))
            return _peel(adj, keep, min_density, max_size, min_size)
        while True:
            d, st, u = heapq.heappop(heap)
            if u in core and d == degree[u] and st == strength[u]:
                break
        core.discard(u)
        for v, w in adj[u].items():
            if v in core:
                degree[v] -= 1
                strength[v] -= w
                edges -= 1
                heapq.heappush(heap, (degree[v], strength[v], v))
    return None
