"""
Tests for the real-time graph simulator (Milestone 2).

Covers determinism, ground-truth isolation, benign behaviour, every ring type,
every evasion mode, the ring lifecycle, rate control and drift.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest

from fraudsim.simulator.config import parse_simulation_config
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.recorder import run_simulation
from fraudsim.simulator.rings import REPORTING_THRESHOLD
from fraudsim.simulator.scenario_loader import list_scenarios, load_scenario
from fraudsim.types import (
    GROUND_TRUTH_ONLY_EVENTS,
    EvasionMode,
    Event,
    EventType,
    RingLifecycle,
    RingType,
)

SRC = Path(__file__).resolve().parents[1] / "src"

# Payload keys each observable event type may carry. Anything else could leak labels.
ALLOWED_PAYLOAD_KEYS: dict[EventType, set[str]] = {
    EventType.USER_JOIN: {"user_id", "name"},
    EventType.USER_LEAVE: {"user_id"},
    EventType.DEVICE_LINK: {"user_id", "device_id"},
    EventType.CARD_LINK: {"user_id", "card_id"},
    EventType.IP_LINK: {"user_id", "ip"},
    EventType.ATTRIBUTE_UNLINK: {"user_id", "attr_type", "attr_id"},
    EventType.TRANSACTION: {"txn_id", "sender", "receiver", "amount"},
}


def scenario(
    rings: list[dict[str, Any]] | None = None,
    duration: float = 60.0,
    rate: float = 20.0,
    users: int = 60,
    hubs: dict[str, Any] | None = None,
    **sim: Any,
) -> dict[str, Any]:
    """Small scenario dict for focused tests."""
    return {
        "name": "test",
        "simulation": {
            "duration_seconds": duration,
            "target_event_rate": rate,
            "num_users": users,
            "num_devices": users // 2,
            "num_cards": users // 2,
            "num_ips": users // 2,
            "hub_distribution": hubs
            if hubs is not None
            else {"office": {"size": 15, "type": "ip", "category": "office"}},
            **sim,
        },
        "rings": rings or [],
    }


def run(cfg: dict[str, Any], seed: int = 3) -> tuple[EventGenerator, list[Event]]:
    gen = EventGenerator(seed=seed, run_id="test", config=cfg)
    return gen, gen.generate()


def txns(events: list[Event]) -> list[dict[str, Any]]:
    return [e.payload for e in events if e.event_type == EventType.TRANSACTION]


def links(events: list[Event]) -> dict[str, set[str]]:
    """attribute id -> users ever linked to it."""
    out: dict[str, set[str]] = defaultdict(set)
    for e in events:
        p = e.payload
        if e.event_type == EventType.DEVICE_LINK:
            out[p["device_id"]].add(p["user_id"])
        elif e.event_type == EventType.CARD_LINK:
            out[p["card_id"]].add(p["user_id"])
        elif e.event_type == EventType.IP_LINK:
            out[p["ip"]].add(p["user_id"])
    return out


# ─── Determinism ──────────────────────────────────────────────────────


@pytest.mark.parametrize("name", list_scenarios())
def test_every_scenario_runs_and_is_deterministic(name: str) -> None:
    cfg = load_scenario(name)
    a, ev_a = run(cfg, seed=11)
    b, ev_b = run(cfg, seed=11)
    assert len(ev_a) > 0
    assert a.digest == b.digest
    assert [e.to_dict() for e in ev_a[:200]] == [e.to_dict() for e in ev_b[:200]]
    assert a.ledger.summary() == b.ledger.summary()


def test_different_seeds_differ() -> None:
    cfg = load_scenario("baseline")
    assert run(cfg, seed=1)[0].digest != run(cfg, seed=2)[0].digest


def test_digest_independent_of_python_hash_seed() -> None:
    """Set iteration order must never leak into the stream."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from fraudsim.simulator.event_generator import EventGenerator;"
        "from fraudsim.simulator.scenario_loader import load_scenario;"
        "g = EventGenerator(seed=5, run_id='x', config=load_scenario('adversarial'));"
        "g.generate(); print(g.digest)"
    )
    digests = set()
    for hash_seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        out = subprocess.run(
            [sys.executable, "-c", code, str(SRC)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        digests.add(out.stdout.strip())
    assert len(digests) == 1


def test_events_have_ids_timestamps_and_monotonic_order() -> None:
    _, events = run(load_scenario("adversarial"))
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert all(re.fullmatch(r"[0-9a-f]{32}", e.event_id) for e in events)
    assert len({e.event_id for e in events}) == len(events)
    assert all(a.ts <= b.ts for a, b in itertools.pairwise(events))
    assert all(e.run_id == "test" for e in events)


def test_until_s_truncates_stream() -> None:
    gen, events = EventGenerator(seed=1, run_id="t", config=load_scenario("baseline")), []
    events = gen.generate(until_s=10.0)
    assert events
    assert (events[-1].ts - gen.ts(0.0)).total_seconds() <= 10.0


# ─── Ground truth stays out of the stream ─────────────────────────────


@pytest.mark.parametrize("name", list_scenarios())
def test_no_ground_truth_in_observable_stream(name: str) -> None:
    gen, events = run(load_scenario(name))
    member_ids = {u for r in gen.ledger.rings.values() for _, _, u in r.member_history}
    for e in events:
        assert e.event_type not in GROUND_TRUTH_ONLY_EVENTS
        assert set(e.payload) <= ALLOWED_PAYLOAD_KEYS[e.event_type], e
        assert "ring" not in json.dumps(e.payload).lower()
    # Identifiers are opaque: fraud accounts look exactly like benign ones.
    for uid in member_ids:
        assert re.fullmatch(r"u\d{6}", uid)
    for attr in links(events):
        assert re.fullmatch(r"(device|card|ip)_\d{5}", attr)


# ─── Benign behaviour ─────────────────────────────────────────────────


def test_transaction_activity_is_heavy_tailed() -> None:
    _, events = run(scenario(users=200, rate=60.0, heavy_tail_alpha=1.5, duration=60.0))
    senders = Counter(t["sender"] for t in txns(events))
    counts = sorted(senders.values(), reverse=True)
    top_decile = sum(counts[: max(1, len(counts) // 10)])
    # Uniform activity would put ~10% of volume on the top decile.
    assert top_decile / sum(counts) > 0.25

    amounts = sorted(t["amount"] for t in txns(events))
    median = amounts[len(amounts) // 2]
    assert amounts[int(len(amounts) * 0.99)] > 5 * median  # long right tail


def test_hubs_reach_configured_size_and_are_benign() -> None:
    cfg = scenario(
        hubs={
            "office": {"size": 20, "type": "ip", "category": "office"},
            "family": {"size": 4, "type": "device", "category": "family_device"},
        }
    )
    gen, events = run(cfg)
    linked = links(events)
    for hub in gen.hubs.values():
        assert len(linked[hub["attr_id"]]) >= hub["size"]
    ring_members = {u for r in gen.ledger.rings.values() for u in r.members}
    assert not ring_members  # no rings configured: every hub member is benign


def test_random_hub_size_distribution() -> None:
    cfg = scenario(hub_size_distribution={"count": 25, "min_size": 3, "max_size": 40})
    gen, _ = run(cfg)
    sizes = [h["size"] for h in gen.hubs.values() if h["name"].startswith("auto_")]
    assert len(sizes) == 25
    assert min(sizes) >= 3 and max(sizes) <= 40
    assert len(set(sizes)) > 3


def test_users_join_and_leave() -> None:
    _, events = run(scenario(user_join_rate=0.5, user_leave_rate=0.5, duration=60.0))
    kinds = Counter(e.event_type for e in events)
    assert kinds[EventType.USER_JOIN] > 60  # initial population plus arrivals
    assert kinds[EventType.USER_LEAVE] > 5
    left_at = {e.payload["user_id"]: e.seq for e in events if e.event_type == EventType.USER_LEAVE}
    seq_of = {e.payload.get("txn_id"): e.seq for e in events}
    for t in txns(events):
        for uid in (t["sender"], t["receiver"]):
            if uid in left_at:
                assert seq_of[t["txn_id"]] < left_at[uid], "departed user transacted"


def test_drift_raises_activity_and_adds_hub() -> None:
    cfg = scenario(
        duration=60.0,
        rate=20.0,
        drift={"trigger_time_s": 30, "new_hub_size": 25, "shift_multiplier": 2.5},
    )
    gen, events = run(cfg)
    start = gen.ts(0.0)
    before = sum(
        1 for e in events if e.event_type == EventType.TRANSACTION and (e.ts - start).seconds < 30
    )
    after = sum(
        1 for e in events if e.event_type == EventType.TRANSACTION and (e.ts - start).seconds >= 30
    )
    assert after > 1.8 * before
    assert "drift_hub" in gen.hubs
    assert any(c["action"] == "drift" for c in gen.control_log)


def test_set_rate_changes_density_and_is_logged() -> None:
    cfg = scenario(rate=10.0, duration=40.0)
    gen = EventGenerator(seed=4, run_id="t", config=cfg)
    first = gen.generate(until_s=20.0)
    gen.set_rate(50.0)
    second = gen.generate(until_s=40.0)
    n1 = sum(1 for e in first if e.event_type == EventType.TRANSACTION)
    n2 = sum(1 for e in second if e.event_type == EventType.TRANSACTION)
    assert n2 > 3 * n1
    assert gen.control_log[-1]["action"] == "set_rate"

    # Same control calls at the same points reproduce the same stream.
    again = EventGenerator(seed=4, run_id="t", config=cfg)
    again.generate(until_s=20.0)
    again.set_rate(50.0)
    again.generate(until_s=40.0)
    assert again.digest == gen.digest


# ─── Ring types ───────────────────────────────────────────────────────


def ring_run(ring: dict[str, Any], seed: int = 3, **kw: Any) -> tuple[EventGenerator, list[Event]]:
    defaults = {"id": "r1", "spawn_time_s": 5, "grow_interval_s": 1.0, "action_interval_s": 2.0}
    return run(scenario(rings=[{**defaults, **ring}], **kw), seed=seed)


@pytest.mark.parametrize(
    ("ring_type", "kind"),
    [("shared_device", "device"), ("shared_card", "card"), ("shared_ip", "ip")],
)
def test_shared_attribute_rings(ring_type: str, kind: str) -> None:
    gen, events = ring_run({"type": ring_type, "size": 5})
    ring = gen.ledger.rings["r1"]
    anchor = ring.shared_attributes[kind]
    assert ring.members <= links(events)[anchor]
    assert len(ring.members) == 5
    # Money moves between members.
    internal = [t for t in txns(events) if {t["sender"], t["receiver"]} <= ring.members]
    assert len(internal) >= 3


@pytest.mark.parametrize("length", [3, 4, 5, 6])
def test_money_cycle_of_each_length(length: int) -> None:
    gen, events = ring_run({"type": "money_cycle", "size": length, "length": length})
    ring = gen.ledger.rings["r1"]
    edges = [(t["sender"], t["receiver"], t["amount"]) for t in txns(events)]
    internal = [e for e in edges if e[0] in ring.members and e[1] in ring.members]
    succ = {s: r for s, r, _ in internal}
    # Walk the cycle: it must return to the start after exactly `length` hops.
    start = internal[0][0]
    node, hops = start, 0
    while True:
        node = succ[node]
        hops += 1
        if node == start or hops > length:
            break
    assert node == start and hops == length
    first_round = internal[:length]
    amounts = [a for _, _, a in first_round]
    assert all(b < a for a, b in itertools.pairwise(amounts))  # each hop takes a cut


def test_cycle_length_validation() -> None:
    with pytest.raises(ValueError, match=r"length must be 3\.\.6"):
        parse_simulation_config(scenario(rings=[{"type": "money_cycle", "size": 7, "length": 7}]))


def test_smurfing_fan_in_and_fan_out() -> None:
    gen, events = ring_run({"type": "smurfing_fan_in", "size": 7})
    ring = gen.ledger.rings["r1"]
    into = Counter(t["receiver"] for t in txns(events) if t["sender"] in ring.members)
    collector, n = into.most_common(1)[0]
    assert collector in ring.members and n >= 6
    burst = [t for t in txns(events) if t["receiver"] == collector and t["sender"] in ring.members]
    assert all(t["amount"] < REPORTING_THRESHOLD for t in burst)

    gen, events = ring_run({"type": "smurfing_fan_out", "size": 7})
    ring = gen.ledger.rings["r1"]
    out = Counter(t["sender"] for t in txns(events) if t["receiver"] in ring.members)
    source, n = out.most_common(1)[0]
    assert source in ring.members and n >= 6


def test_layered_chain() -> None:
    gen, events = ring_run({"type": "layered_chain", "size": 5})
    ring = gen.ledger.rings["r1"]
    internal = [t for t in txns(events) if {t["sender"], t["receiver"]} <= ring.members]
    chain = internal[:4]
    assert all(a["receiver"] == b["sender"] for a, b in itertools.pairwise(chain))
    assert len({t["sender"] for t in chain} | {chain[-1]["receiver"]}) == 5
    assert all(b["amount"] < a["amount"] for a, b in itertools.pairwise(chain))


def test_mixed_attribute_ring_has_partial_overlaps() -> None:
    gen, events = ring_run({"type": "mixed_attribute", "size": 6})
    ring = gen.ledger.rings["r1"]
    linked = links(events)
    anchors = ring.shared_attributes
    assert set(anchors) == {"device", "card", "ip"}
    covered = [linked[a] & ring.members for a in anchors.values()]
    assert all(len(c) >= 2 for c in covered)  # every anchor is shared
    assert all(c != ring.members for c in covered)  # none covers the whole ring
    assert set().union(*covered) == ring.members  # together they connect everyone


# ─── Lifecycle ────────────────────────────────────────────────────────


def test_lifecycle_spawn_grow_activate_mutate_dissolve() -> None:
    gen, events = ring_run(
        {
            "type": "shared_device",
            "size": 5,
            "initial_members": 2,
            "mutate_prob": 0.5,
            "active_duration_s": 25,
        },
        duration=60.0,
    )
    ring = gen.ledger.rings["r1"]
    actions = [entry["action"] for entry in gen.ledger.log if entry["ring_id"] == "r1"]
    assert actions[0] == "spawn"
    assert actions.count("grow") == 3
    assert "activate" in actions and "mutate" in actions and actions[-1] == "dissolve"
    assert ring.lifecycle == RingLifecycle.DISSOLVED
    assert ring.spawn_ts < ring.activate_ts < ring.dissolve_ts
    assert len(gen.ledger.get_members_at("r1", ring.spawn_ts)) == 2
    assert len(gen.ledger.get_members_at("r1", ring.activate_ts)) == 5
    # Mutation swapped the shared device: an unlink event for the old one exists.
    unlinks = [e for e in events if e.event_type == EventType.ATTRIBUTE_UNLINK]
    assert unlinks
    # Members vanish after dissolve.
    left = {e.payload["user_id"] for e in events if e.event_type == EventType.USER_LEAVE}
    assert ring.members <= left
    assert gen.ledger.members_at(ring.dissolve_ts) == {}


def test_random_rings_appear_and_vanish() -> None:
    cfg = scenario(duration=120.0)
    cfg["random_rings"] = {
        "rate_per_min": 6.0,
        "types": [t.value for t in RingType],
        "active_duration_s": [10, 20],
    }
    gen, _ = run(cfg)
    rings = gen.ledger.rings
    assert len(rings) >= 5
    assert any(r.lifecycle == RingLifecycle.DISSOLVED for r in rings.values())
    spawn_times = sorted(r.spawn_ts for r in rings.values())
    assert spawn_times[0] != spawn_times[-1]


# ─── Evasion modes ────────────────────────────────────────────────────


def test_hub_hiding_anchors_on_a_busy_hub() -> None:
    gen, events = ring_run(
        {"type": "shared_ip", "size": 4, "evasion": "hub_hiding", "shared_attribute": "office"}
    )
    ring = gen.ledger.rings["r1"]
    anchor = ring.shared_attributes["ip"]
    assert anchor == gen.hubs["office"]["attr_id"]
    benign_on_hub = links(events)[anchor] - ring.members
    assert len(benign_on_hub) >= 15  # the ring hides among the hub's legitimate users


def test_hub_hiding_without_matching_hub_uses_cover() -> None:
    gen, events = ring_run({"type": "money_cycle", "size": 4, "evasion": "hub_hiding"})
    ring = gen.ledger.rings["r1"]
    _kind, attr = ring.details["cover_hub"].split(":")
    assert ring.members <= links(events)[attr]


def test_low_and_slow_is_quieter_and_smaller() -> None:
    def fraud_stats(evasion: str) -> tuple[int, float]:
        gen, events = ring_run(
            {"type": "shared_card", "size": 4, "evasion": evasion}, duration=120.0
        )
        members = gen.ledger.rings["r1"].members
        ts = [t for t in txns(events) if {t["sender"], t["receiver"]} <= members]
        return len(ts), sum(t["amount"] for t in ts) / max(1, len(ts))

    n_fast, amt_fast = fraud_stats("none")
    n_slow, amt_slow = fraud_stats("low_and_slow")
    assert n_slow < n_fast / 3
    assert amt_slow < amt_fast / 3


def test_identity_rotation_replaces_members() -> None:
    gen, events = ring_run(
        {"type": "shared_device", "size": 4, "evasion": "identity_rotation"}, duration=90.0
    )
    rotations = [e for e in gen.ledger.log if e["action"] == "rotate"]
    assert rotations
    left = {e.payload["user_id"] for e in events if e.event_type == EventType.USER_LEAVE}
    for rot in rotations:
        assert rot["details"]["left"] in left
    ring = gen.ledger.rings["r1"]
    assert len(ring.members) == 4
    assert len({u for _, _, u in ring.member_history}) > 4


def test_decoy_edges_touch_legitimate_users() -> None:
    gen, events = ring_run({"type": "shared_device", "size": 4, "evasion": "decoy_edges"})
    members = gen.ledger.rings["r1"].members
    outward = [t for t in txns(events) if t["sender"] in members and t["receiver"] not in members]
    assert len(outward) >= 4


def test_adaptive_attacker_reacts_to_flags() -> None:
    cfg = scenario(
        rings=[
            {
                "id": "adv",
                "type": "shared_device",
                "size": 3,
                "spawn_time_s": 2,
                "evasion": "adaptive",
                "grow_interval_s": 0.5,
            }
        ],
        duration=60.0,
    )
    cfg["adaptive_attacker"] = {"penalty": 0.25, "respawn_delay_s": 2}
    gen = EventGenerator(seed=9, run_id="t", config=cfg)
    gen.generate(until_s=15.0)
    first = gen.rings["adv"]
    chosen = first.strategy
    assert chosen in gen.attacker.spec.strategies

    reacted = gen.notify_flagged({next(iter(sorted(first.members)))})
    assert reacted == ["adv"]
    assert gen.attacker.weights[chosen] == pytest.approx(0.25)
    assert not first.alive

    gen.generate(until_s=40.0)
    assert "adv~1" in gen.rings  # replacement ring launched
    assert gen.ledger.rings["adv"].lifecycle == RingLifecycle.DISSOLVED
    assert gen.ledger.rings["adv"].details["flags_observed"]
    assert gen.attacker.history[0]["burned"] == "adv"

    # Flags on accounts the attacker does not own change nothing.
    assert gen.notify_flagged({"u999999"}) == []


def test_adaptive_attacker_shifts_strategy_distribution() -> None:
    cfg = scenario(duration=600.0, rate=1.0, users=30)
    cfg["random_rings"] = {
        "rate_per_min": 12.0,
        "types": ["shared_card"],
        "size_min": 2,
        "size_max": 2,
        "evasion_weights": {"adaptive": 1.0},
        "active_duration_s": [5, 10],
    }
    cfg["adaptive_attacker"] = {
        "penalty": 0.5,
        "respawn_delay_s": 1,
        "strategies": ["hub_hiding", "low_and_slow"],
    }
    gen = EventGenerator(seed=2, run_id="t", config=cfg)
    # A "detector" that always catches hub_hiding rings and never low_and_slow ones.
    for step in range(5, 601, 5):
        gen.generate(until_s=float(step))
        for agent in list(gen.rings.values()):
            if agent.alive and agent.strategy == EvasionMode.HUB_HIDING:
                gen.notify_flagged(set(agent.members))
    w = gen.attacker.weights
    assert w[EvasionMode.HUB_HIDING] < 0.01 * w[EvasionMode.LOW_AND_SLOW]

    by_time = sorted(gen.rings.values(), key=lambda a: gen.ledger.rings[a.ring_id].spawn_ts)
    third = len(by_time) // 3
    early = sum(a.strategy == EvasionMode.HUB_HIDING for a in by_time[:third]) / third
    late = sum(a.strategy == EvasionMode.HUB_HIDING for a in by_time[-third:]) / third
    assert late < early


# ─── Recorder / artefacts ─────────────────────────────────────────────


def test_run_simulation_writes_reproducible_artefacts(tmp_path: Path) -> None:
    m1 = run_simulation("hub_heavy", 7, tmp_path, run_id="a")
    m2 = run_simulation("hub_heavy", 7, tmp_path, run_id="b")
    assert m1["stream_digest"] == m2["stream_digest"]
    assert m1["config_hash"] == m2["config_hash"]
    run_dir = tmp_path / "a"
    for name in (
        "events.jsonl",
        "ground_truth.jsonl",
        "ground_truth_log.jsonl",
        "hubs.jsonl",
        "manifest.json",
    ):
        assert (run_dir / name).exists(), name
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == m1["events"]
    restored = [Event.from_dict(json.loads(line)) for line in lines[:5]]
    assert restored[0].seq == 1
    gt = [json.loads(line) for line in (run_dir / "ground_truth.jsonl").read_text().splitlines()]
    assert {r["ring_id"] for r in gt} == {
        "ring_in_hub",
        "ring_behind_kiosk",
        "standard_ring",
        "mixed_behind_hub",
    }
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["seed"] == 7 and manifest["scenario"] == "hub_heavy"


def test_config_validation_errors() -> None:
    bad = scenario()
    bad["simulation"]["target_event_rate"] = 0
    with pytest.raises(ValueError, match="target_event_rate"):
        parse_simulation_config(bad)
    with pytest.raises(ValueError, match="unknown type"):
        parse_simulation_config(scenario(rings=[{"type": "teleport", "size": 3}]))
    with pytest.raises(ValueError, match="duplicate ring ids"):
        parse_simulation_config(
            scenario(
                rings=[
                    {"id": "x", "type": "shared_device", "size": 3},
                    {"id": "x", "type": "shared_card", "size": 3},
                ]
            )
        )
