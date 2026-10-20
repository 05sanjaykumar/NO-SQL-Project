"""
Milestone 3: detectors, store, tracker and engine on hand-built micro-graphs.
No database needed (InMemoryGraphStore).
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from fraudsim.detectors.community import CommunityConfig, CommunityDetector
from fraudsim.detectors.cycle import TemporalCycleConfig, TemporalCycleDetector
from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
from fraudsim.detectors.ensemble import EnsembleConfig, RingTracker, merge_candidates
from fraudsim.detectors.hubaware import (
    HubAwareConfig,
    HubAwareSharedAttribute,
    decay,
    hub_weight,
)
from fraudsim.detectors.smurfing import SmurfingDetector
from fraudsim.detectors.static_cypher import StaticCypherBaseline, StaticCypherConfig
from fraudsim.graph.store import InMemoryGraphStore
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.types import DetectorType, Event, EventType, RingCandidate, RingStatus, RingType

T0 = datetime(2026, 1, 1, tzinfo=UTC)
KEY = {
    "device": ("device_link", "device_id"),
    "card": ("card_link", "card_id"),
    "ip": ("ip_link", "ip"),
}


class G:
    """Tiny event builder for micro-graphs."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._txn = itertools.count(1)

    def _add(self, etype: str, t: float, **payload: Any) -> G:
        self.events.append(
            Event(
                event_id=f"e{len(self.events) + 1}",
                event_type=EventType(etype),
                ts=T0 + timedelta(seconds=t),
                payload=payload,
                seq=len(self.events) + 1,
            )
        )
        return self

    def users(self, *uids: str, t: float = 0.0) -> G:
        for u in uids:
            self._add("user_join", t, user_id=u, name=u)
        return self

    def link(self, uid: str, kind: str, attr: str, t: float = 0.0) -> G:
        etype, key = KEY[kind]
        return self._add(etype, t, user_id=uid, **{key: attr})

    def share(self, uids: list[str], kind: str, attr: str, t: float = 0.0) -> G:
        for u in uids:
            self.link(u, kind, attr, t)
        return self

    def txn(self, s: str, r: str, amount: float, t: float) -> G:
        return self._add(
            "transaction", t, txn_id=f"t{next(self._txn)}", sender=s, receiver=r, amount=amount
        )

    def leave(self, uid: str, t: float) -> G:
        return self._add("user_leave", t, user_id=uid)

    def unlink(self, uid: str, kind: str, attr: str, t: float) -> G:
        return self._add("attribute_unlink", t, user_id=uid, attr_type=kind, attr_id=attr)

    def store(self) -> InMemoryGraphStore:
        s = InMemoryGraphStore()
        s.apply(self.events)
        return s

    def full(self, now: float | None = None, since: float = float("-inf")):
        now_t = (
            (T0 + timedelta(seconds=now)).timestamp()
            if now is not None
            else (self.events[-1].ts.timestamp())
        )
        return self.store().full_view(since, now_t)


def ids(prefix: str, n: int, start: int = 1) -> list[str]:
    return [f"{prefix}{i:02d}" for i in range(start, start + n)]


# ─── Store ────────────────────────────────────────────────────────────


def test_store_degree_expand_view_and_prune() -> None:
    hub = ids("h", 30)
    g = G().users(*hub, "a", "b", "c", "x").share(hub, "ip", "ip_hub")
    g.share(["a", "b"], "device", "d1").link("b", "card", "c1").link("c", "card", "c1")
    g.link("a", "ip", "ip_hub").txn("c", "x", 50.0, t=10)
    s = g.store()
    now = (T0 + timedelta(seconds=20)).timestamp()

    # Expansion stops at attributes busier than max_fanout.
    assert s.expand({"a"}, k=1, max_fanout=10, since_t=0) == {"a", "b"}
    assert s.expand({"a"}, k=2, max_fanout=10, since_t=0) == {"a", "b", "c"}
    assert s.expand({"a"}, k=3, max_fanout=10, since_t=0) == {"a", "b", "c", "x"}
    assert len(s.expand({"a"}, k=1, max_fanout=100, since_t=0)) == 32  # through the hub

    view = s.view({"a", "b"}, since_t=0, now_t=now)
    assert view.attr_degree[("ip", "ip_hub")] == 31  # global degree, not region degree
    assert {(lk.user, lk.attr) for lk in view.links} >= {("a", "d1"), ("b", "c1")}

    # Departed users drop out of degrees and views; unlink removes an edge.
    g.leave("x", t=30).unlink("b", "card", "c1", t=31)
    s = g.store()
    assert "x" not in s.full_view(0, now).users
    assert s.view({"b"}, 0, now).attr_degree.get(("card", "c1")) is None
    assert s.expand({"c"}, k=1, max_fanout=10, since_t=0) == {"c"}

    dropped = s.prune((T0 + timedelta(seconds=5)).timestamp())
    assert dropped["links"] > 0 and dropped["transactions"] == 0
    assert s.stats()["transactions"] == 1


# ─── Hub-aware weighting ──────────────────────────────────────────────


def test_hub_weight_and_decay_functions() -> None:
    assert hub_weight(3, "inverse_log_degree") == pytest.approx(0.7213, abs=1e-4)
    assert hub_weight(50, "inverse_log_degree") == pytest.approx(0.2543, abs=1e-4)
    assert hub_weight(50, "none") == 1.0
    assert hub_weight(4, "inverse_degree") == pytest.approx(1 / 3)
    assert decay(300, 300) == pytest.approx(0.5)
    assert decay(10_000, None) == 1.0
    with pytest.raises(ValueError):
        hub_weight(3, "bogus")


def test_hub_aware_finds_shared_device_ring() -> None:
    ring = ids("r", 4)
    g = G().users(*ring).share(ring, "device", "dev_x")
    found = HubAwareSharedAttribute().detect(g.full())
    assert len(found) == 1
    assert found[0].members == set(ring)
    assert found[0].ring_type == RingType.SHARED_DEVICE
    assert found[0].evidence["shared_attributes"][0]["degree"] == 4


def test_hub_aware_ignores_benign_hub_but_recovers_ring_hidden_in_it() -> None:
    benign = ids("b", 26)
    ring = ids("r", 4)
    g = G().users(*benign, *ring).share(benign + ring, "ip", "office")
    # Benign hub users barely transact with each other.
    g.txn("b01", "b02", 40.0, t=5)
    # The hidden ring moves money among itself.
    t = 10.0
    for s, r in itertools.permutations(ring, 2):
        g.txn(s, r, 900.0, t)
        t += 1
    found = HubAwareSharedAttribute().detect(g.full())
    assert [c.members for c in found] == [set(ring)]
    assert found[0].evidence["max_attribute_degree"] == 30

    # Ablation: without hub weighting the whole office is flagged.
    flat = HubAwareSharedAttribute(HubAwareConfig(weight_method="none", max_ring_size=40))
    flagged = flat.detect(g.full())
    assert any(len(c.members) == 30 for c in flagged)


def test_hub_aware_rejects_sparse_chains() -> None:
    # a-b share a device, b-c a card, c-d an IP: connected, but not a dense group.
    g = G().users("a", "b", "c", "d")
    g.share(["a", "b"], "device", "d1").share(["b", "c"], "card", "c1").share(
        ["c", "d"], "ip", "i1"
    )
    assert HubAwareSharedAttribute().detect(g.full()) == []


def test_hub_aware_mixed_attribute_ring() -> None:
    m = ids("m", 6)
    pattern = [("device", "card"), ("card", "ip"), ("ip", "device")]
    g = G().users(*m)
    for i, u in enumerate(m):
        for kind in pattern[i % 3]:
            g.link(u, kind, {"device": "D", "card": "C", "ip": "I"}[kind])
    found = HubAwareSharedAttribute().detect(g.full())
    assert len(found) == 1 and found[0].members == set(m)
    assert found[0].ring_type == RingType.MIXED_ATTRIBUTE


def test_time_decay_ages_out_stale_sharing() -> None:
    ring = ids("r", 3)
    g = G().users(*ring).share(ring, "card", "c1", t=0)
    stale = g.full(now=3600)  # links are an hour old, half-life 5 minutes
    assert HubAwareSharedAttribute().detect(stale) == []
    no_decay = HubAwareSharedAttribute(HubAwareConfig(time_decay_half_life_s=None))
    assert no_decay.detect(stale)[0].members == set(ring)


# ─── Temporal cycles ──────────────────────────────────────────────────


def cycle_graph(n: int, amounts: list[float], times: list[float]) -> G:
    m = ids("c", n)
    g = G().users(*m)
    for i in range(n):
        g.txn(m[i], m[(i + 1) % n], amounts[i], times[i])
    return g


@pytest.mark.parametrize("n", [3, 4, 5, 6])
def test_cycle_detected_for_lengths_3_to_6(n: int) -> None:
    amounts = [5000 * 0.97**i for i in range(n)]
    found = TemporalCycleDetector().detect(
        cycle_graph(n, amounts, [10.0 * i for i in range(n)]).full()
    )
    assert len(found) == 1
    assert found[0].evidence["length"] == n
    assert found[0].members == set(ids("c", n))


def test_cycle_rejections() -> None:
    det = TemporalCycleDetector()
    ok_amounts = [5000, 4900, 4800]
    # length 7 is beyond the bound
    assert (
        det.detect(cycle_graph(7, [5000 * 0.97**i for i in range(7)], list(range(7))).full()) == []
    )
    # unrelated amounts: a coincidental cycle of ordinary payments
    assert det.detect(cycle_graph(3, [5000, 120, 3300], [0, 1, 2]).full()) == []
    # not in time order
    assert det.detect(cycle_graph(3, ok_amounts, [0, 20, 10]).full()) == []
    # too slow for the window
    slow = TemporalCycleDetector(TemporalCycleConfig(time_window_s=60))
    assert slow.detect(cycle_graph(3, ok_amounts, [0, 50, 100]).full()) == []


# ─── Smurfing ─────────────────────────────────────────────────────────


def test_smurfing_fan_in_and_out() -> None:
    smurfs = ids("s", 5)
    g = G().users("col", *smurfs, "src", *ids("m", 5))
    for i, s in enumerate(smurfs):
        g.txn(s, "col", 9400.0 + 10 * i, t=5.0 * i)
    for i, m in enumerate(ids("m", 5)):
        g.txn("src", m, 9100.0, t=100 + 3.0 * i)
    found = SmurfingDetector().detect(g.full())
    by_type = {c.ring_type: c for c in found}
    assert by_type[RingType.SMURFING_FAN_IN].members == {"col", *smurfs}
    assert by_type[RingType.SMURFING_FAN_OUT].members == {"src", *ids("m", 5)}


def test_smurfing_ignores_small_or_spread_out_transfers() -> None:
    g = G().users("col", *ids("s", 5))
    for i, s in enumerate(ids("s", 5)):
        g.txn(s, "col", 3000.0, t=float(i))  # far below the threshold band
    assert SmurfingDetector().detect(g.full()) == []
    g = G().users("col", *ids("s", 5))
    for i, s in enumerate(ids("s", 5)):
        g.txn(s, "col", 9500.0, t=1000.0 * i)  # one per ~17 minutes
    assert SmurfingDetector().detect(g.full()) == []


# ─── Static baseline reproduces the original course results ───────────


def seed_store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    store.apply(EventGenerator(seed=42, run_id="seed").generate_seed_events())
    return store


@pytest.mark.parametrize(("cap", "hub_flagged"), [(10, False), (100, True)])
def test_static_baseline_on_seed_graph(cap: int, hub_flagged: bool) -> None:
    store = seed_store()
    det = StaticCypherBaseline(config=StaticCypherConfig(max_degree_cap=cap), store=store)
    found = det.detect(store.full_view(0, 0))
    groups = {frozenset(c.members): c for c in found}
    assert frozenset({"u01", "u02", "u03", "u04"}) in groups  # Q1 ring A on device_007
    assert frozenset({"u10", "u11", "u12"}) in groups  # Q2 ring B on card_005
    assert frozenset({"u21", "u22", "u23"}) in groups  # Q3 cycle
    assert frozenset({"u27", "u28", "u29"}) in groups  # Q4 ring C on ip_004
    hub = frozenset(f"u{i:02d}" for i in range(13, 25))
    assert (hub in groups) is hub_flagged  # the office wifi hub (12 users)


# ─── Community ────────────────────────────────────────────────────────


def test_community_louvain_and_wcc() -> None:
    a, b = ids("a", 5), ids("b", 5)
    g = G().users(*a, *b)
    for grp, tag in ((a, "A"), (b, "B")):
        g.share(grp, "device", f"dev{tag}").share(grp, "card", f"card{tag}")
    g.link("a01", "ip", "bridge").link("b01", "ip", "bridge")  # weak bridge
    louvain = CommunityDetector().detect(g.full())
    assert sorted(sorted(c.members) for c in louvain) == [sorted(a), sorted(b)]
    wcc = CommunityDetector(CommunityConfig(algorithm="wcc", min_density=0.3)).detect(g.full())
    assert [c.members for c in wcc] == [set(a) | set(b)]


# ─── Ensemble / tracker ───────────────────────────────────────────────


def cand(members: set[str], det: DetectorType = DetectorType.HUB_AWARE) -> RingCandidate:
    return RingCandidate(
        ring_id="x",
        detector=det,
        ring_type=RingType.SHARED_DEVICE,
        members=set(members),
        score=0.8,
        first_seen=T0,
        last_seen=T0,
    )


def test_merge_candidates_across_detectors() -> None:
    merged = merge_candidates(
        [
            cand({"a", "b", "c"}),
            cand({"a", "b", "c", "d"}, DetectorType.TEMPORAL_CYCLE),
            cand({"x", "y", "z"}, DetectorType.SMURFING),
        ],
        EnsembleConfig(),
    )
    assert [sorted(m.members) for m in merged] == [["a", "b", "c", "d"], ["x", "y", "z"]]
    assert merged[0].evidence["detectors"] == ["hub_aware", "temporal_cycle"]


def test_tracker_identity_hysteresis_and_dissolve() -> None:
    tr = RingTracker(EnsembleConfig(hysteresis_confirm=2, hysteresis_dissolve=2))
    t = [T0 + timedelta(seconds=i) for i in range(10)]
    ring = {"a", "b", "c"}
    ev = {"hub_aware": {"a", "b", "c", "d", "e"}}

    ups = tr.update([cand(ring)], ev, t[0])
    rid = ups[0].ring_id
    assert [u.change for u in ups] == ["created"]
    assert tr.rings[rid].status == RingStatus.CANDIDATE

    ups = tr.update([cand(ring | {"d"})], ev, t[1])  # same ring, grew: same id
    assert [u.change for u in ups] == ["grew", "confirmed"]
    assert tr.flagged_users() == {"a", "b", "c", "d"}

    ups = tr.update([cand(ring)], ev, t[2])
    assert [u.change for u in ups] == ["shrank"] and ups[0].ring_id == rid

    # Not evaluated (region elsewhere): no miss counted.
    tr.update([], {"hub_aware": {"z"}}, t[3])
    tr.update([], {"temporal_cycle": None}, t[4])  # a detector that never found it
    assert tr.rings[rid].misses == 0

    tr.update([], ev, t[5])
    assert tr.rings[rid].status == RingStatus.CONFIRMED  # one miss: hysteresis holds
    ups = tr.update([], ev, t[6])
    assert [u.change for u in ups] == ["dissolved"]
    assert tr.rings[rid].status == RingStatus.DISSOLVED


def test_tracker_merge_and_split() -> None:
    tr = RingTracker(EnsembleConfig(hysteresis_confirm=1))
    ev: dict[str, set[str] | None] = {"hub_aware": None}
    tr.update([cand({"a", "b", "c"}), cand({"x", "y", "z"})], ev, T0)
    r1, r2 = sorted(tr.rings)
    ups = tr.update([cand({"a", "b", "c", "x", "y", "z"})], ev, T0 + timedelta(seconds=1))
    merged = [u for u in ups if u.change == "merged_into"]
    assert len(merged) == 1 and merged[0].details["into"] in (r1, r2)
    assert len(tr.live()) == 1

    live = tr.live()[0]
    ups = tr.update(
        [cand({"a", "b", "c", "x"}), cand({"y", "z", "q"})], ev, T0 + timedelta(seconds=2)
    )
    assert any(u.change == "split_from" and u.details["parent"] == live.ring_id for u in ups)
    assert tr.rings[live.ring_id].members == {"a", "b", "c", "x"}


# ─── Engine ───────────────────────────────────────────────────────────


def run_engine(events: list[Event], **cfg: Any) -> DetectionEngine:
    store = InMemoryGraphStore()
    overrides = cfg.pop("overrides", None)
    engine = DetectionEngine(store, build_detectors(store, overrides), config=EngineConfig(**cfg))
    engine.run(events)
    return engine


def test_engine_on_seed_graph_confirms_the_planted_rings() -> None:
    events = EventGenerator(seed=42, run_id="seed").generate_seed_events()
    engine = run_engine(events, batch_size=20, full_interval_s=0.0)
    confirmed = [r.members for r in engine.tracker.confirmed()]
    for planted in ({"u01", "u02", "u03", "u04"}, {"u10", "u11", "u12"}, {"u27", "u28", "u29"}):
        assert any(planted <= m for m in confirmed), planted
    report = engine.reports[-1]
    assert {"ingest", "dirty", "view", "track", "total"} <= set(report.timings_ms)
    assert report.region >= report.touched > 0


def test_incremental_matches_full_recompute() -> None:
    """Incremental dirty-set detection must find what full recomputation finds."""
    events = EventGenerator(seed=5, run_id="t", config=load_scenario("baseline")).generate()
    only_inc = {"static_cypher": {"enabled": False}, "community": {"enabled": False}}
    inc = run_engine(events, overrides=only_inc)
    full = run_engine(events, overrides=only_inc, incremental=False)
    inc_rings = [r.members for r in inc.tracker.confirmed()]
    full_rings = [r.members for r in full.tracker.confirmed()]
    assert full_rings
    for members in full_rings:
        best = max((len(members & m) / len(members | m) for m in inc_rings), default=0)
        assert best >= 0.8, members
    # And incremental evaluated far fewer users per batch.
    inc_region = sum(r.region for r in inc.reports) / len(inc.reports)
    full_region = sum(r.region for r in full.reports) / len(full.reports)
    assert inc_region < full_region


def test_engine_detector_flags_and_timers() -> None:
    events = EventGenerator(seed=1, run_id="t", config=load_scenario("baseline")).generate()
    engine = run_engine(
        events,
        overrides={"community": {"enabled": False}},
        full_interval_s=20.0,
        ttl_s=30.0,
        prune_interval_s=10.0,
    )
    names = {d.name for d in engine.detectors if d.enabled}
    assert "CommunityDetector" not in names
    full_runs = [r for r in engine.reports if r.full_run]
    # baseline lasts 60 s: full-graph detectors ran at t~0, 20, 40 (and maybe 60)
    assert 3 <= len(full_runs) <= 4
    assert all("StaticCypherBaseline" in r.detector_ms for r in full_runs)
    assert not any(
        "StaticCypherBaseline" in r.detector_ms for r in engine.reports if not r.full_run
    )
    assert any(r.pruned.get("transactions") for r in engine.reports)


def test_unknown_override_rejected() -> None:
    with pytest.raises(ValueError, match="unknown detector"):
        build_detectors(InMemoryGraphStore(), {"gnn": {}})


def test_closed_loop_run_writes_artefacts_and_feeds_attacker(tmp_path) -> None:
    from fraudsim.detectors.runner import run_detection

    result = run_detection("adversarial", 42, tmp_path, duration_s=90.0)
    run_dir = tmp_path / result.manifest["run_id"]
    for name in ("events", "batches", "ring_updates", "rings", "ground_truth", "manifest"):
        suffix = ".json" if name == "manifest" else ".jsonl"
        assert (run_dir / f"{name}{suffix}").exists(), name
    assert result.manifest["feedback_calls"] > 0
    # The adaptive attacker reacted to flags on its own accounts.
    observed = [e for e in result.generator.ledger.log if e["action"] == "attacker_observed_flag"]
    assert observed
    # Same inputs, same outputs.
    again = run_detection("adversarial", 42, None, duration_s=90.0)
    rings = lambda r: sorted(  # noqa: E731
        (tuple(sorted(x.members)), x.status.value) for x in r.engine.tracker.rings.values()
    )
    assert rings(again) == rings(result)
    assert again.manifest["stream_digest"] == result.manifest["stream_digest"]


def test_far_hops_do_not_cross_hubs() -> None:
    hub_a, hub_b = ids("a", 30), ids("b", 30)
    g = G().users(*hub_a, *hub_b, "x").share(hub_a, "ip", "hubA").share(hub_b, "ip", "hubB")
    g.link("a01", "device", "bridge").link("b01", "device", "bridge")
    g.link("x", "ip", "hubA")
    s = g.store()
    # First hop reaches hub A's co-members; the second hop may not fan out through hub B.
    near = s.expand({"x"}, k=2, max_fanout=100, since_t=0, far_fanout=10)
    assert set(hub_a) <= near and "b01" in near and not set(hub_b[1:]) & near
    assert set(hub_b) <= s.expand({"x"}, k=3, max_fanout=100, since_t=0)  # no far cap


def test_detection_identical_across_hash_seeds() -> None:
    """Set/hash iteration order must not leak into detection results."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import sys, hashlib, logging, structlog; sys.path.insert(0, sys.argv[1]);"
        "structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING));"
        "from fraudsim.detectors.runner import run_detection;"
        "r = run_detection('high_noise', 102, None, attacker_feedback=False);"
        "rings = sorted((tuple(sorted(x.members)), x.status.value, round(x.score, 9))"
        " for x in r.engine.tracker.rings.values());"
        "print(hashlib.sha256(repr(rings).encode()).hexdigest())"
    )
    digests = set()
    for hash_seed in ("0", "1", "7"):
        out = subprocess.run(
            [sys.executable, "-c", code, str(src)],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=True,
        )
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1
