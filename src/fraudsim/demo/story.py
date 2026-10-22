"""
`fraudsim demo-story`: one fixed-seed run, three detectors, one HTML page.

The same event stream (scenario `demo_story`) is run through:

1. the original degree-capped Cypher rules with cap = 10 (Q4b),
2. the same rules with cap = 100 (Q4a, "no cap"),
3. the full system: incremental hub-aware + cycle + smurfing detectors,
   ensemble tracking and the calibrated risk model (Milestone 4).

Each approach is scored against the ground-truth ledger (a true ring is
"caught" when a flagged group matches its members with Jaccard >= 0.5). The
page shows what each approach caught, missed and wrongly flagged, when it did
so, and, for the hidden ring, the full explanation from the risk model.
Every number on the page is computed from this run. The page carries the
run_id, seed, config hash and stream digest.
"""

from __future__ import annotations

import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
from fraudsim.graph.store import InMemoryGraphStore
from fraudsim.metrics.matching import jaccard
from fraudsim.scoring.features import FEATURE_LABELS
from fraudsim.scoring.scorer import RingScorer
from fraudsim.scoring.stage import ScoringStage
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.types import RingStatus, config_hash, new_run_id

TEMPLATE = Path(__file__).with_name("story_template.html")
DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "models" / "ring_scorer_lr.joblib"
MATCH_JACCARD = 0.5

STATIC_ONLY = {
    "hub_aware": {"enabled": False},
    "temporal_cycle": {"enabled": False},
    "smurfing": {"enabled": False},
    "community": {"enabled": False},
}


def _approaches() -> list[dict[str, Any]]:
    return [
        {
            "key": "cap10",
            "title": "Degree-capped Cypher (cap = 10)",
            "subtitle": "Original Q1/Q2/Q3/Q4b: shared device/card ≥ 3, 3-cycles, "
            "shared IP ≥ 3 and < 10",
            "overrides": STATIC_ONLY | {"static_cypher": {"max_degree_cap": 10}},
            "scored": False,
        },
        {
            "key": "cap100",
            "title": "Same rules, cap = 100",
            "subtitle": "Q4a: the cap raised so busy IPs are no longer skipped",
            "overrides": STATIC_ONLY | {"static_cypher": {"max_degree_cap": 100}},
            "scored": False,
        },
        {
            "key": "system",
            "title": "Hub-aware incremental system",
            "subtitle": "Hub-weighted projection + temporal cycles + smurfing, "
            "ensemble tracking, calibrated risk model",
            "overrides": {"static_cypher": {"enabled": False}, "community": {"enabled": False}},
            "scored": True,
        },
    ]


def run_story(
    seed: int = 7,
    out_dir: Path = Path("demo"),
    model_path: Path = DEFAULT_MODEL,
    scenario: str = "demo_story",
) -> Path:
    raw = load_scenario(scenario)
    run_id = new_run_id()
    gen = EventGenerator(seed=seed, run_id=run_id, config=raw)
    events = gen.generate()
    start_t = gen.ts(0.0).timestamp()
    duration = gen.config.duration_seconds if gen.config else 0.0

    def rel(dt: datetime | None) -> float | None:
        return round(dt.timestamp() - start_t, 2) if dt else None

    scorer = RingScorer.load(model_path) if Path(model_path).exists() else None
    truth = []
    for gt in sorted(gen.ledger.rings.values(), key=lambda r: r.spawn_ts or datetime.min):
        truth.append(
            {
                "ring_id": gt.ring_id,
                "type": gt.ring_type.value,
                "evasion": gt.evasion_mode.value,
                "members": sorted({u for _, a, u in gt.member_history if a == "join"}),
                "shared": gt.shared_attributes,
                "spawn_s": rel(gt.spawn_ts),
                "activate_s": rel(gt.activate_ts),
            }
        )
    truth_sets = {t["ring_id"]: set(t["members"]) for t in truth}
    fraud_users = set().union(*truth_sets.values()) if truth_sets else set()

    results = []
    system_engine: DetectionEngine | None = None
    system_store: InMemoryGraphStore | None = None
    stage: ScoringStage | None = None
    for ap in _approaches():
        store = InMemoryGraphStore()
        stage_ap = ScoringStage(scorer=scorer) if ap["scored"] else None
        engine = DetectionEngine(
            store,
            build_detectors(store, ap["overrides"]),
            config=EngineConfig(batch_size=25, full_interval_s=5.0),
            scorer=stage_ap,
        )
        scored = ap["scored"] and scorer is not None
        flag_change = "alert" if scored else "confirmed"
        log: list[dict[str, Any]] = []
        for batch in engine.batches(events):
            report = engine.process(batch)
            for upd in report.updates:
                if upd.change not in (flag_change, "early_warning") or (
                    upd.change == "early_warning" and not scored
                ):
                    continue
                ring = engine.tracker.rings[upd.ring_id]
                entry: dict[str, Any] = {
                    "t_s": rel(upd.ts),
                    "batch": report.batch,
                    "last_seq": report.last_seq,
                    "ring_id": upd.ring_id,
                    "kind": "flag" if upd.change == flag_change else "early_warning",
                    "members": list(upd.members),
                    "detectors": list(upd.detectors),
                    "risk": upd.score if scored else None,
                }
                best_id, best_j = None, 0.0
                for tid, members in truth_sets.items():
                    j = jaccard(set(upd.members), members)
                    if j > best_j:
                        best_id, best_j = tid, j
                entry["true_ring"] = best_id if best_j >= MATCH_JACCARD else None
                entry["jaccard"] = round(best_j, 3)
                if scored and ring.explanation:
                    exp = ring.explanation
                    entry["explanation"] = {
                        "reason": exp.get("reason_text"),
                        "counterfactual": exp.get("counterfactual"),
                        "calibration": exp.get("calibration_bucket"),
                        "threshold": exp.get("threshold"),
                        "contributions": [
                            {"feature": k, "label": FEATURE_LABELS.get(k, k), "value": v}
                            for k, v in list(exp.get("feature_contributions", {}).items())[:6]
                        ],
                    }
                log.append(entry)
        if ap["key"] == "system":
            system_engine, system_store, stage = engine, store, stage_ap
        result = _evaluate(ap, engine, truth_sets, fraud_users, rel, scored=scored)
        result["log"] = log
        results.append(result)

    assert system_engine is not None and system_store is not None
    hidden = next((t for t in truth if t["evasion"] == "hub_hiding"), truth[0] if truth else None)
    spotlight = _spotlight(system_engine, hidden, rel) if hidden else None
    graph = _graph(system_store, gen, truth, results, end_t=events[-1].ts.timestamp())
    _add_appear_times(graph, events, start_t)

    data = {
        "meta": {
            "run_id": run_id,
            "scenario": scenario,
            "seed": seed,
            "config_hash": config_hash({"scenario": raw, "seed": seed, "demo": "story-v1"}),
            "scenario_hash": config_hash(raw),
            "stream_digest": gen.digest,
            "events": len(events),
            "duration_s": duration,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "match_rule": (
                "a true ring counts as caught when a flagged group has Jaccard >= "
                f"{MATCH_JACCARD} with its members"
            ),
            "model": _model_meta(scorer, model_path),
            "alert_threshold": stage.policy.default_threshold if stage else None,
            "command": f"fraudsim demo-story --seed {seed}",
            "batch_size": 25,
            "full_interval_s": 5.0,
        },
        "truth": truth,
        "approaches": results,
        "spotlight": spotlight,
        "graph": graph,
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"story_{run_id}.json").write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(data, default=str).replace("</", "<\\/")
    )
    page = out_dir / "story.html"
    page.write_text(html, encoding="utf-8")
    (out_dir / f"story_{run_id}.html").write_text(html, encoding="utf-8")
    return page


def _model_meta(scorer: RingScorer | None, path: Path) -> dict[str, Any] | None:
    if scorer is None:
        return None
    train = scorer.meta.train
    return {
        "path": str(Path(path).name),
        "type": scorer.model_type,
        "training_id": train.get("training_id"),
        "train_seeds": train.get("train_seeds"),
        "test_seeds": train.get("test_seeds"),
        "test_metrics": scorer.meta.metrics.get("test"),
    }


def _evaluate(
    ap: dict[str, Any],
    engine: DetectionEngine,
    truth_sets: dict[str, set[str]],
    fraud_users: set[str],
    rel: Any,
    scored: bool,
) -> dict[str, Any]:
    """What this approach flagged, and how that compares with the ledger."""
    flagged = []
    for ring in engine.tracker.rings.values():
        when = ring.alerted_at if scored else ring.confirmed_at
        if when is None:
            continue
        best_id, best_j = None, 0.0
        for tid, members in truth_sets.items():
            j = jaccard(ring.members, members)
            if j > best_j:
                best_id, best_j = tid, j
        flagged.append(
            {
                "ring_id": ring.ring_id,
                "members": sorted(ring.members),
                "size": len(ring.members),
                "flagged_s": rel(when),
                "true_ring": best_id if best_j >= MATCH_JACCARD else None,
                "jaccard": round(best_j, 3),
                "benign_members": len(ring.members - fraud_users),
                "risk": round(ring.risk, 4) if ring.risk is not None else None,
                "still_live": ring.status != RingStatus.DISSOLVED,
            }
        )
    flagged.sort(key=lambda f: (f["flagged_s"] or 0, f["ring_id"]))
    caught = {}
    for f in flagged:
        tid = f["true_ring"]
        if tid and (tid not in caught or f["flagged_s"] < caught[tid]["flagged_s"]):
            caught[tid] = {
                "true_ring": tid,
                "ring_id": f["ring_id"],
                "jaccard": f["jaccard"],
                "flagged_s": f["flagged_s"],
            }
    false = [f for f in flagged if f["true_ring"] is None]
    benign_flagged = (
        set().union(*(set(f["members"]) for f in flagged)) - fraud_users if flagged else set()
    )
    return {
        "key": ap["key"],
        "title": ap["title"],
        "subtitle": ap["subtitle"],
        "flag_rule": "alerted (confirmed and risk ≥ threshold)"
        if scored
        else "confirmed (found on 2 consecutive runs)",
        "caught": sorted(caught.values(), key=lambda c: c["true_ring"]),
        "missed": sorted(set(truth_sets) - set(caught)),
        "false_rings": len(false),
        "benign_users_flagged": len(benign_flagged),
        "flagged": flagged,
        "flagged_users": sorted(set().union(*(set(f["members"]) for f in flagged)))
        if flagged
        else [],
    }


def _spotlight(engine: DetectionEngine, hidden: dict[str, Any], rel: Any) -> dict[str, Any] | None:
    members = set(hidden["members"])
    rings = [r for r in engine.tracker.rings.values() if r.explanation is not None]
    if not rings:
        return None
    best = max(rings, key=lambda r: (jaccard(r.members, members), r.risk or 0.0))
    j = jaccard(best.members, members)
    if j == 0:
        return {"true_ring": hidden["ring_id"], "found": False}
    exp = best.explanation or {}
    contribs = exp.get("feature_contributions", {})
    return {
        "true_ring": hidden["ring_id"],
        "found": True,
        "ring_id": best.ring_id,
        "jaccard": round(j, 3),
        "members": sorted(best.members),
        "status": best.status.value,
        "risk": best.risk,
        "threshold": exp.get("threshold"),
        "reason": exp.get("reason_text"),
        "counterfactual": exp.get("counterfactual"),
        "calibration": exp.get("calibration_bucket"),
        "contributions": [
            {"feature": k, "label": FEATURE_LABELS.get(k, k), "value": v}
            for k, v in list(contribs.items())[:8]
        ],
        "features": best.features,
        "evidence": exp.get("evidence_subgraph"),
        "detectors": sorted(best.sources),
        "first_seen_s": rel(best.first_seen),
        "early_warning_s": rel(best.early_warned_at),
        "confirmed_s": rel(best.confirmed_at),
        "alert_s": rel(best.alerted_at),
        "history": best.history,
    }


def _graph(
    store: InMemoryGraphStore,
    gen: EventGenerator,
    truth: list[dict[str, Any]],
    results: list[dict[str, Any]],
    end_t: float,
) -> dict[str, Any]:
    """The neighbourhood that tells the story: hubs, rings, their shared attributes, transfers."""
    ring_of = {u: t["ring_id"] for t in truth for u in t["members"]}
    hub_attrs = {(h["kind"], h["attr_id"]): h for h in gen.hubs.values()}
    users: set[str] = set(ring_of)
    for (kind, attr), _ in hub_attrs.items():
        users |= {u for u in store.attr_users.get((kind, attr), {}) if store.active.get(u)}
    flagged = {r["key"]: set(r["flagged_users"]) for r in results}

    # Attributes to draw: hubs, plus anything shared by >= 2 drawn users.
    attr_users: dict[tuple[str, str], set[str]] = {}
    for u in users:
        for attr in store.user_links.get(u, {}):
            attr_users.setdefault(attr, set()).add(u)
    # Hubs, plus attributes that link at least two drawn users including a ring member.
    shown = {
        a: us
        for a, us in attr_users.items()
        if a in hub_attrs or (len(us) >= 2 and us & set(ring_of))
    }

    nodes = []
    for u in sorted(users):
        nodes.append(
            {
                "id": u,
                "kind": "user",
                "truth": ring_of.get(u, "benign"),
                "active": bool(store.active.get(u)),
                "flagged": {k: (u in v) for k, v in flagged.items()},
            }
        )
    for kind, attr in sorted(shown):
        hub = hub_attrs.get((kind, attr))
        nodes.append(
            {
                "id": attr,
                "kind": kind,
                "degree": sum(
                    1 for x in store.attr_users.get((kind, attr), {}) if store.active.get(x)
                ),
                "hub": hub["name"] if hub else None,
                "hub_category": hub["category"] if hub else None,
            }
        )
    edges = [
        {"s": u, "t": attr, "type": "link"}
        for (kind, attr), us in sorted(shown.items())
        for u in sorted(us)
    ]
    for u in sorted(set(ring_of)):
        for txn in store.out_txns.get(u, ()):
            if txn.receiver in ring_of and txn.t <= end_t:
                edges.append({"s": u, "t": txn.receiver, "type": "transfer", "amount": txn.amount})
    return {"nodes": nodes, "edges": edges}


def _add_appear_times(graph: dict[str, Any], events: list[Any], start_t: float) -> None:
    """When each drawn node and edge first appeared in the event stream (for the replay)."""
    node_ids = {n["id"] for n in graph["nodes"]}
    first: dict[str, float] = {}
    first_edge: dict[tuple[str, str], float] = {}
    transfers: list[dict[str, Any]] = []
    keys = {"device_link": "device_id", "card_link": "card_id", "ip_link": "ip"}
    for e in events:
        t = round(e.ts.timestamp() - start_t, 3)
        p = e.payload
        et = e.event_type.value
        if et == "user_join" and p["user_id"] in node_ids:
            first.setdefault(p["user_id"], t)
        elif et in keys and p["user_id"] in node_ids and p[keys[et]] in node_ids:
            first.setdefault(p["user_id"], t)
            first.setdefault(p[keys[et]], t)
            first_edge.setdefault((p["user_id"], p[keys[et]]), t)
        elif et == "transaction" and p["sender"] in node_ids and p["receiver"] in node_ids:
            transfers.append({"s": p["sender"], "t": p["receiver"], "at": t, "amount": p["amount"]})
        elif et == "user_leave" and p["user_id"] in node_ids:
            for n in graph["nodes"]:
                if n["id"] == p["user_id"]:
                    n["left_s"] = t
    for n in graph["nodes"]:
        n["appear_s"] = first.get(n["id"], 0.0)
    for edge in graph["edges"]:
        if edge["type"] == "link":
            edge["appear_s"] = first_edge.get((edge["s"], edge["t"]), 0.0)
    graph["edges"] = [e for e in graph["edges"] if e["type"] == "link"]
    graph["transfers"] = transfers
