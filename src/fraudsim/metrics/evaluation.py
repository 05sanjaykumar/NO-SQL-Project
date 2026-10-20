"""
Run evaluation (Milestone 5): accuracy, timeliness, triage quality, latency
and resource metrics for one detection run, against the ground-truth ledger.

Definitions (all configurable, all reported alongside the numbers):

- A *flag* is the moment the system tells an analyst about a ring: an
  `alert` when a risk model is used (confirmed and risk >= threshold),
  otherwise a `confirmed` status change. The flagged member set is the one
  shown at that moment.
- *True rings* are ground-truth rings that became ACTIVE during the run.
  Their member set is every account that was ever a member.
- A flag *matches* a true ring when Jaccard(flag members, ring members) >=
  `jaccard` (default 0.5; 0.3 and 0.8 are reported as sensitivity checks).
  Matching is one-to-one, greedy by Jaccard, and uses each ring's first
  matching flag.
- *Time-to-detect* = first matching flag - ring activation (simulated time;
  negative means it was flagged while still forming).
- Missed true rings are categorised, first rule that applies:
  late (flag only after the ring dissolved) > below_threshold (tracked and
  matched, never flagged, still live at the end) > aged_out (tracked and
  matched, dissolved without a flag) > hidden_behind_hub (no matching tracked
  ring; shares a busy attribute or uses hub hiding) > blind_spot (none of the
  detectors produced anything matching it).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fraudsim.detectors.ensemble import RingTracker, RingUpdate
from fraudsim.metrics.matching import jaccard
from fraudsim.simulator.ground_truth import GroundTruthLedger
from fraudsim.types import EvasionMode, GroundTruthRing, RingStatus

HUB_DEGREE = 20  # an attribute this busy counts as a hub when explaining misses
MISS_REASONS = ("late", "below_threshold", "aged_out", "hidden_behind_hub", "blind_spot")


def percentiles(values: list[float], ps: tuple[int, ...] = (50, 90, 95, 99)) -> dict[str, Any]:
    """Linear-interpolated percentiles plus count/mean/min/max (None when empty)."""
    out: dict[str, Any] = {"count": len(values)}
    if not values:
        out.update({f"p{p}": None for p in ps} | {"mean": None, "min": None, "max": None})
        return out
    xs = sorted(values)
    for p in ps:
        k = (len(xs) - 1) * p / 100
        lo, hi = math.floor(k), math.ceil(k)
        out[f"p{p}"] = round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 6)
    out["mean"] = round(statistics.fmean(xs), 6)
    out["min"] = round(xs[0], 6)
    out["max"] = round(xs[-1], 6)
    return out


@dataclass(frozen=True, slots=True)
class Flag:
    t: float  # simulated epoch seconds
    ring_id: str
    members: frozenset[str]
    risk: float | None
    batch: int


@dataclass(slots=True)
class EvalConfig:
    jaccard: float = 0.5
    sensitivity: tuple[float, ...] = (0.3, 0.5, 0.8)
    window_s: float = 30.0  # rolling window for time series
    step_s: float = 10.0  # time-series resolution
    review_cost_min: float = 15.0  # analyst minutes per false alert (cost proxy)
    miss_cost_min: float = 240.0  # cost proxy per missed ring (investigation later)
    k_values: tuple[int, ...] = (5, 10, 20)


@dataclass(slots=True)
class RunMetrics:
    summary: dict[str, Any] = field(default_factory=dict)
    rings: list[dict[str, Any]] = field(default_factory=list)  # one row per true ring
    flags: list[dict[str, Any]] = field(default_factory=list)  # one row per flag
    timeseries: list[dict[str, Any]] = field(default_factory=list)
    batches: list[dict[str, Any]] = field(default_factory=list)


def collect_flags(reports: list[Any], flag_change: str) -> list[Flag]:
    flags: list[Flag] = []
    for rep in reports:
        for upd in rep.updates:
            if upd.change == flag_change:
                flags.append(_flag(upd, rep.batch))
    return flags


def _flag(upd: RingUpdate, batch: int) -> Flag:
    return Flag(
        t=upd.ts.timestamp(),
        ring_id=upd.ring_id,
        members=frozenset(upd.members),
        risk=upd.score if upd.change == "alert" else None,
        batch=batch,
    )


def true_rings(ledger: GroundTruthLedger) -> list[GroundTruthRing]:
    return sorted(
        (r for r in ledger.rings.values() if r.activate_ts is not None), key=lambda r: r.ring_id
    )


def all_members(ring: GroundTruthRing) -> set[str]:
    return {u for _, action, u in ring.member_history if action == "join"}


def match_flags(
    flags: list[Flag], truth: list[GroundTruthRing], threshold: float
) -> dict[str, Flag]:
    """One-to-one greedy matching: true ring id -> its earliest best-matching flag."""
    members = {r.ring_id: all_members(r) for r in truth}
    # Collapse to one flag per tracked ring (its first), then match greedily.
    first: dict[str, Flag] = {}
    for f in sorted(flags, key=lambda f: (f.t, f.ring_id)):
        first.setdefault(f.ring_id, f)
    pairs = sorted(
        (
            (jaccard(set(f.members), members[r.ring_id]), f.t, f.ring_id, r.ring_id)
            for f in first.values()
            for r in truth
        ),
        key=lambda x: (-x[0], x[1], x[2], x[3]),
    )
    out: dict[str, Flag] = {}
    used: set[str] = set()
    for j, _, fid, rid in pairs:
        if j < threshold:
            break
        if rid in out or fid in used:
            continue
        out[rid] = first[fid]
        used.add(fid)
    return out


def evaluate_run(
    ledger: GroundTruthLedger,
    tracker: RingTracker,
    reports: list[Any],
    start_t: float,
    end_t: float,
    scored: bool,
    cfg: EvalConfig | None = None,
    hub_degree_of: dict[str, int] | None = None,
    extra: dict[str, Any] | None = None,
) -> RunMetrics:
    """
    Evaluate one run. `hub_degree_of` maps true ring id -> degree of the busiest
    attribute its members share (for the hidden_behind_hub category); when absent,
    only the ring's evasion mode is used.
    """
    cfg = cfg or EvalConfig()
    flag_change = "alert" if scored else "confirmed"
    flags = collect_flags(reports, flag_change)
    truth = true_rings(ledger)
    members = {r.ring_id: all_members(r) for r in truth}
    fraud_users = set().union(*members.values()) if members else set()
    matched = match_flags(flags, truth, cfg.jaccard)
    flagged_rings = {f.ring_id for f in flags}
    # A flagged ring that matches no true ring (at any Jaccard >= threshold) is false.
    false_ids = sorted(
        rid
        for rid in flagged_rings
        if not any(
            jaccard(set(f.members), m) >= cfg.jaccard
            for f in flags
            if f.ring_id == rid
            for m in members.values()
        )
    )

    # ─── Per true ring ────────────────────────────────────────────────
    ring_rows: list[dict[str, Any]] = []
    ttd: list[float] = []
    for r in truth:
        act = r.activate_ts.timestamp() if r.activate_ts else None
        dis = r.dissolve_ts.timestamp() if r.dissolve_ts else None
        f = matched.get(r.ring_id)
        reason = None
        detected = f is not None and (dis is None or f.t <= dis)
        if f is not None and not detected:
            reason = "late"
        elif f is None:
            reason = _miss_reason(r, members[r.ring_id], tracker, cfg.jaccard, hub_degree_of)
        if detected and f is not None and act is not None:
            ttd.append(f.t - act)
        ring_rows.append(
            {
                "true_ring": r.ring_id,
                "type": r.ring_type.value,
                "evasion": r.evasion_mode.value,
                "size": len(members[r.ring_id]),
                "spawn_s": _rel(r.spawn_ts, start_t),
                "activate_s": _rel(r.activate_ts, start_t),
                "dissolve_s": _rel(r.dissolve_ts, start_t),
                "detected": detected,
                "flag_ring_id": f.ring_id if f else None,
                "flag_s": round(f.t - start_t, 3) if f else None,
                "jaccard": round(jaccard(set(f.members), members[r.ring_id]), 4) if f else None,
                "time_to_detect_s": round(f.t - act, 3) if (detected and f and act) else None,
                "miss_reason": reason,
            }
        )
    n_true = len(truth)
    tp = sum(1 for row in ring_rows if row["detected"])
    fp = len(false_ids)
    n_flagged = len(flagged_rings)

    # User level: everyone shown in any flag vs every member of any true ring.
    flagged_users = set().union(*(set(f.members) for f in flags)) if flags else set()
    user_tp = len(flagged_users & fraud_users)

    sensitivity = {
        str(j): _prf(
            sum(
                1
                for rid, f in match_flags(flags, truth, j).items()
                if _in_time(f, ledger.rings[rid])
            ),
            n_true,
            n_flagged,
            sum(
                1
                for rid in flagged_rings
                if not any(
                    jaccard(set(f.members), m) >= j
                    for f in flags
                    if f.ring_id == rid
                    for m in members.values()
                )
            ),
        )
        for j in cfg.sensitivity
    }

    # ─── Triage: rank every scored tracked ring by its peak risk ──────
    ranked = []
    for ring in tracker.rings.values():
        if ring.risk is None and not scored:
            continue
        peak = max([h.get("risk", 0.0) for h in ring.history if "risk" in h] + [ring.risk or 0.0])
        is_true = any(jaccard(ring.members, m) >= cfg.jaccard for m in members.values())
        ranked.append((peak, ring.ring_id, is_true))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    triage = {
        f"precision_at_{k}": (
            round(sum(1 for _, _, ok in ranked[:k] if ok) / min(k, len(ranked)), 4)
            if ranked
            else None
        )
        for k in cfg.k_values
    }
    triage["pr_auc"] = _average_precision([ok for _, _, ok in ranked], n_true) if ranked else None
    hours = max(end_t - start_t, 1e-9) / 3600.0
    triage["alerts_per_hour"] = round(len(flags) / hours, 3)
    triage["false_alerts"] = fp
    triage["false_alert_cost_min"] = round(fp * cfg.review_cost_min, 1)
    triage["missed_ring_cost_min"] = round((n_true - tp) * cfg.miss_cost_min, 1)

    # ─── Latency / resources from batch reports ───────────────────────
    batch_rows = [_batch_row(rep, start_t) for rep in reports]
    detect = [b["detect_ms"] for b in batch_rows]
    ingest_per_event = [b["ingest_ms"] / b["n_events"] for b in batch_rows if b["n_events"]]
    flag_batches = {f.batch for f in flags}
    e2e = [b["total_ms"] for b in batch_rows if b["batch"] in flag_batches]
    wall_s = sum(b["total_ms"] for b in batch_rows) / 1000.0
    n_events = sum(b["n_events"] for b in batch_rows)
    queries = [b["store_queries"] for b in batch_rows if b["store_queries"] is not None]

    misses = {k: sum(1 for r in ring_rows if r["miss_reason"] == k) for k in MISS_REASONS}
    summary: dict[str, Any] = {
        "flag_rule": f"{flag_change} ("
        + ("risk >= threshold" if scored else "ensemble status")
        + ")",
        "jaccard": cfg.jaccard,
        "true_rings": n_true,
        "rings_flagged": n_flagged,
        "rings_detected": tp,
        "rings_missed": n_true - tp,
        "false_positive_rings": fp,
        "ring": _prf(tp, n_true, n_flagged, fp),
        "user": {
            **_prf(user_tp, len(fraud_users), len(flagged_users), len(flagged_users) - user_tp),
            "flagged_users": len(flagged_users),
            "fraud_users": len(fraud_users),
        },
        "sensitivity": sensitivity,
        "missed_reasons": misses,
        "time_to_detect_s": percentiles(ttd),
        "triage": triage,
        "latency_ms": {
            "ingest_per_event": percentiles(ingest_per_event, (50, 95, 99)),
            "detect_per_batch": percentiles(detect, (50, 95, 99)),
            "batch_total": percentiles([b["total_ms"] for b in batch_rows], (50, 95, 99)),
            "event_to_flag_processing": percentiles(e2e, (50, 95, 99)),
        },
        "throughput": {
            "events": n_events,
            "batches": len(batch_rows),
            "processing_wall_s": round(wall_s, 4),
            "events_per_s": round(n_events / wall_s, 1) if wall_s > 0 else None,
        },
        "resources": {
            "db_hits_sampled": (
                sum(b["db_hits"] for b in batch_rows if b.get("db_hits") is not None)
                if any(b.get("db_hits") is not None for b in batch_rows)
                else None
            ),
            "rss_mb_peak": max((b["rss_mb"] for b in batch_rows if b.get("rss_mb")), default=None),
            "graph_final": next(
                (
                    {k[6:]: v for k, v in b.items() if k.startswith("graph_")}
                    for b in reversed(batch_rows)
                    if any(k.startswith("graph_") for k in b)
                ),
                None,
            ),
        },
        "store_queries": {
            "total": sum(queries) if queries else None,
            "per_batch": percentiles([float(q) for q in queries], (50, 95, 99))
            if queries
            else None,
        },
        **(extra or {}),
    }
    flag_rows = [
        {
            "t_s": round(f.t - start_t, 3),
            "ring_id": f.ring_id,
            "size": len(f.members),
            "risk": f.risk,
            "batch": f.batch,
            "true_ring": next(
                (rid for rid, mf in matched.items() if mf.ring_id == f.ring_id), None
            ),
            "false": f.ring_id in false_ids,
        }
        for f in sorted(flags, key=lambda f: (f.t, f.ring_id))
    ]
    in_time = {rid: f for rid, f in matched.items() if _in_time(f, ledger.rings[rid])}
    timeseries = _timeseries(flags, truth, in_time, false_ids, start_t, end_t, cfg)
    return RunMetrics(summary, ring_rows, flag_rows, timeseries, batch_rows)


# ─── Helpers ──────────────────────────────────────────────────────────


def _rel(ts: datetime | None, start_t: float) -> float | None:
    return round(ts.timestamp() - start_t, 3) if ts else None


def _in_time(f: Flag, ring: GroundTruthRing) -> bool:
    return ring.dissolve_ts is None or f.t <= ring.dissolve_ts.timestamp()


def _prf(tp: int, n_true: int, n_pred: int, fp: int) -> dict[str, Any]:
    """
    precision = predicted items that match something true / all predicted items
    (n_pred - fp) / n_pred; recall = true items matched (one-to-one) / all true items.
    Several flags may match the same true ring, so tp and n_pred - fp can differ.
    """
    precision = (n_pred - fp) / n_pred if n_pred else None
    recall = tp / n_true if n_true else None
    if precision is None or recall is None:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "tp": tp,
        "fp": fp,
        "fn": n_true - tp,
    }


def _average_precision(labels: list[bool], n_true: int) -> float | None:
    """AP of a ranked list, normalised by the number of true rings (so misses cost)."""
    if not n_true:
        return None
    hits, total = 0, 0.0
    for i, ok in enumerate(labels, start=1):
        if ok:
            hits += 1
            total += hits / i
    return round(total / max(n_true, hits), 4)


def _miss_reason(
    ring: GroundTruthRing,
    members: set[str],
    tracker: RingTracker,
    threshold: float,
    hub_degree_of: dict[str, int] | None,
) -> str:
    tracked = [r for r in tracker.rings.values() if jaccard(r.members, members) >= threshold]
    if any(r.status != RingStatus.DISSOLVED for r in tracked):
        return "below_threshold"
    if tracked:
        return "aged_out"
    hub_deg = (hub_degree_of or {}).get(ring.ring_id, 0)
    if ring.evasion_mode == EvasionMode.HUB_HIDING or hub_deg >= HUB_DEGREE:
        return "hidden_behind_hub"
    return "blind_spot"


def _batch_row(rep: Any, start_t: float) -> dict[str, Any]:
    tm = rep.timings_ms
    detect = (
        tm.get("dirty", 0.0)
        + tm.get("view", 0.0)
        + tm.get("full_view", 0.0)
        + sum(rep.detector_ms.values())
        + tm.get("track", 0.0)
        + tm.get("score", 0.0)
    )
    return {
        "batch": rep.batch,
        "t_s": round(rep.now.timestamp() - start_t, 3),
        "n_events": rep.n_events,
        "region": rep.region,
        "full_run": rep.full_run,
        "ingest_ms": tm.get("ingest", 0.0),
        "detect_ms": round(detect, 3),
        "total_ms": tm.get("total", 0.0),
        "store_queries": rep.store_queries,
        "db_hits": getattr(rep, "db_hits", None),
        **{f"graph_{k}": v for k, v in (getattr(rep, "graph_stats", None) or {}).items()},
        "rss_mb": getattr(rep, "rss_mb", None),
    }


def _timeseries(
    flags: list[Flag],
    truth: list[GroundTruthRing],
    matched: dict[str, Flag],
    false_ids: list[str],
    start_t: float,
    end_t: float,
    cfg: EvalConfig,
) -> list[dict[str, Any]]:
    """Cumulative and rolling-window counts every step_s of simulated time."""
    rows = []
    false_first = {}
    for f in sorted(flags, key=lambda f: f.t):
        if f.ring_id in false_ids:
            false_first.setdefault(f.ring_id, f.t)
    detect_t = {rid: f.t for rid, f in matched.items()}
    act_t = {r.ring_id: r.activate_ts.timestamp() for r in truth if r.activate_ts}
    t = start_t + cfg.step_s
    while t <= end_t + 1e-9:
        lo = t - cfg.window_s
        active = [rid for rid, a in act_t.items() if a <= t]
        caught = [rid for rid in active if detect_t.get(rid, math.inf) <= t]
        n_false = sum(1 for ft in false_first.values() if ft <= t)
        n_flags = sum(1 for f in flags if f.t <= t)
        r_active = [rid for rid in active if act_t[rid] > lo or detect_t.get(rid, math.inf) > lo]
        r_caught = [rid for rid in caught if detect_t[rid] > lo]
        r_false = sum(1 for ft in false_first.values() if lo < ft <= t)
        rows.append(
            {
                "t_s": round(t - start_t, 3),
                "cum_true_active": len(active),
                "cum_detected": len(caught),
                "cum_recall": round(len(caught) / len(active), 4) if active else None,
                "cum_false_rings": n_false,
                "cum_flags": n_flags,
                "cum_precision": round(
                    (len({f.ring_id for f in flags if f.t <= t}) - n_false)
                    / max(1, len({f.ring_id for f in flags if f.t <= t})),
                    4,
                )
                if n_flags
                else None,
                "win_detected": len(r_caught),
                "win_open_rings": len(r_active),
                "win_false_rings": r_false,
            }
        )
        t += cfg.step_s
    return rows
