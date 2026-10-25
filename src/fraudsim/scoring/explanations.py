"""
Explanations for ring flags (Milestone 4): "why was this ring flagged?"

Every scored ring gets:
1. ranked per-feature contributions to the model's log-odds,
2. a templated plain-English reason built from the actual evidence,
3. the evidence subgraph (members, shared attributes, links, transfers) as JSON,
4. counterfactuals: what would have to change for it not to be flagged,
5. the confidence (calibrated score), its calibration bucket, and the observed
   positive rate of that bucket on held-out data.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

from fraudsim.detectors.hubaware import HubAwareConfig, hub_weight
from fraudsim.graph.store import GraphView
from fraudsim.scoring.features import FEATURE_LABELS, FEATURE_NAMES, RingFeatures
from fraudsim.scoring.scorer import RingScorer, calibration_bucket
from fraudsim.types import Explanation

# Features an analyst can reason about as "if this were different".
ACTIONABLE = (
    "hub_exposure",
    "internal_txns_per_member",
    "account_age_similarity",
    "temporal_burstiness",
    "near_threshold_frac",
    "density",
    "hub_adjusted_weight",
    "size",
)


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    return f"{seconds / 3600:.1f} hours"


def evidence_subgraph(
    members: set[str], view: GraphView, joined: dict[str, float]
) -> dict[str, Any]:
    """Members, the attributes at least two of them share, and the transfers among them."""
    holders: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for lk in view.links:
        if lk.user in members:
            holders[(lk.kind, lk.attr)].append((lk.user, lk.t))
    shared = {a: hs for a, hs in holders.items() if len(hs) >= 2}
    nodes: list[dict[str, Any]] = [
        {"id": u, "type": "user", "joined_t": joined.get(u)} for u in sorted(members)
    ]
    edges: list[dict[str, Any]] = []
    for (kind, attr), hs in sorted(shared.items()):
        nodes.append(
            {
                "id": attr,
                "type": kind,
                "degree": view.attr_degree.get((kind, attr), len(hs)),
                "hub_weight": round(
                    hub_weight(view.attr_degree.get((kind, attr), len(hs)), "inverse_log_degree"), 4
                ),
            }
        )
        edges += [{"source": u, "target": attr, "type": "link", "t": t} for u, t in sorted(hs)]
    edges += [
        {
            "source": t.sender,
            "target": t.receiver,
            "type": "transfer",
            "amount": t.amount,
            "t": t.t,
            "txn_id": t.txn_id,
        }
        for t in sorted(view.txns, key=lambda t: (t.t, t.txn_id))
        if t.sender in members and t.receiver in members
    ]
    return {"nodes": nodes, "edges": edges}


def reason_text(
    feats: RingFeatures,
    subgraph: dict[str, Any],
    joined: dict[str, float],
    evidence: dict[str, Any],
) -> str:
    """Plain-English reason assembled only from the ring's actual evidence."""
    parts: list[str] = []
    attrs = [n for n in subgraph["nodes"] if n["type"] != "user"]
    users_of: dict[str, int] = defaultdict(int)
    for e in subgraph["edges"]:
        if e["type"] == "link":
            users_of[e["target"]] += 1
    for node in sorted(attrs, key=lambda n: (-users_of[n["id"]], n["id"]))[:2]:
        k = users_of[node["id"]]
        busy = (
            f"a busy {node['type']} used by {node['degree']} accounts in total"
            if node["degree"] >= 10
            else f"{node['type']} {node['id']} (used by {node['degree']} accounts)"
        )
        if node["degree"] >= 10:
            parts.append(
                f"{k} members share {busy} ({node['id']}, hub weight {node['hub_weight']:.2f}), "
                "so sharing it alone is weak evidence"
            )
        else:
            parts.append(f"{k} users share {busy}, hub weight {node['hub_weight']:.2f}")
    transfers = [e for e in subgraph["edges"] if e["type"] == "transfer"]
    if len(transfers) == 1:
        parts.append("1 transfer between members")
    elif transfers:
        span = transfers[-1]["t"] - transfers[0]["t"]
        parts.append(f"{len(transfers)} transfers between members within {_duration(span)}")
    if feats.cycle_present:
        parts.append(f"money returns to its origin in {int(feats.cycle_length)} hops")
    near = [e for e in transfers if 8000 <= e["amount"] < 10000]
    if len(near) >= 2:
        parts.append(f"{len(near)} transfers just under the 10,000 reporting threshold")
    ages = sorted(
        joined[u]
        for u in {n["id"] for n in subgraph["nodes"] if n["type"] == "user"}
        if u in joined
    )
    if len(ages) >= 2 and ages[-1] - ages[0] < 3600:
        parts.append(f"{len(ages)} accounts created within {_duration(ages[-1] - ages[0])}")
    detectors = evidence.get("detectors", [])
    if detectors:
        parts.append("found by " + ", ".join(detectors))
    return "; ".join(parts) + "." if parts else "Flagged by the ensemble."


def counterfactuals(
    scorer: RingScorer,
    x: np.ndarray,
    contributions: dict[str, float],
    threshold: float,
    subgraph: dict[str, Any],
    evidence: dict[str, Any],
) -> list[str]:
    out: list[str] = []
    feats = dict(zip(FEATURE_NAMES, x.tolist(), strict=True))
    for name, c in contributions.items():
        if c <= 0 or name not in ACTIONABLE:
            continue
        target = scorer.counterfactual_value(x, name, threshold)
        if target is None:
            continue
        current = feats[name]
        if name == "hub_exposure":
            if target <= current:
                continue
            out.append(
                f"Would score below {threshold:.2f} if its busiest shared attribute were used by "
                f"about {math.expm1(target):.0f} accounts instead of {math.expm1(current):.0f}."
            )
        elif target < current and target >= 0:
            out.append(
                f"Would score below {threshold:.2f} if {FEATURE_LABELS[name]} were "
                f"{target:.2f} instead of {current:.2f}."
            )
        if len(out) >= 2:
            break
    # Rule counterfactual for the hub-aware detector.
    hub_ev = (evidence.get("by_detector") or {}).get("hub_aware")
    if hub_ev:
        theta = HubAwareConfig().edge_threshold
        attrs = [n for n in subgraph["nodes"] if n["type"] != "user"]
        if attrs:
            top = max(attrs, key=lambda n: n["degree"])
            txn_part = max(0.0, hub_ev.get("mean_edge_weight", 0.0) - top["hub_weight"])
            room = theta - txn_part
            if room <= 0:
                out.append(
                    "The hub-aware rule would link these members even through an arbitrarily busy "
                    "hub: their transfers to each other alone carry enough weight."
                )
            else:
                exponent = 1.0 / room
                if exponent > 30:  # more accounts than any real attribute could have
                    out.append(
                        f"Only an attribute shared by far more accounts than exist would stop "
                        f"the hub-aware rule linking these members (shared now by {top['degree']})."
                    )
                    return out
                n_needed = math.ceil(math.exp(exponent) - 1.0)
                out.append(
                    f"The hub-aware rule would stop linking these members if {top['id']} were "
                    f"shared by more than {n_needed} accounts (now {top['degree']})."
                )
    return out


def explain(
    ring_id: str,
    members: set[str],
    feats: RingFeatures,
    view: GraphView,
    joined: dict[str, float],
    evidence: dict[str, Any],
    scorer: RingScorer | None,
    threshold: float,
) -> tuple[float | None, Explanation]:
    x = np.asarray(feats.to_vector(), dtype=float)
    subgraph = evidence_subgraph(members, view, joined)
    if scorer is not None and scorer.fitted:
        risk = float(scorer.predict(x)[0])
        contrib = scorer.contributions(x)
        cfs = counterfactuals(scorer, x, contrib, threshold, subgraph, evidence)
        bucket = calibration_bucket(risk)
        observed = next(
            (r["observed_rate"] for r in scorer.meta.reliability if r["bucket"] == bucket), None
        )
    else:
        risk, contrib, cfs, bucket, observed = None, {}, [], "", None
    exp = Explanation(
        ring_id=ring_id,
        score=round(risk, 6) if risk is not None else 0.0,
        feature_contributions=contrib,
        reason_text=reason_text(feats, subgraph, joined, evidence),
        evidence_subgraph=subgraph,
        counterfactual=" ".join(cfs),
        confidence=round(risk, 6) if risk is not None else 0.0,
        calibration_bucket=(
            f"{bucket} (observed fraud rate {observed:.2f} on held-out runs)"
            if observed is not None
            else bucket
        ),
    )
    return risk, exp
