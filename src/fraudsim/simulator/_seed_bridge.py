"""
Seed bridge — converts the original seed.py graph into Event objects.

This preserves full backward compatibility: the existing 126-node demo
graph can be replayed through the new event-driven pipeline.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta

from fraudsim.simulator.ground_truth import GroundTruthLedger
from fraudsim.types import Event, EventType, RingType, new_event_id

# ─── Original seed.py constants (preserved exactly) ──────────────────

N_USERS = 30
N_DEVICES = 18
N_CARDS = 18
N_IPS = 20
N_RANDOM_TXNS = 37

RING_A_USERS = ["u01", "u02", "u03", "u04"]
RING_A_DEVICE = "device_007"

RING_B_USERS = ["u10", "u11", "u12"]
RING_B_CARD = "card_005"

RING_C_USERS = ["u27", "u28", "u29"]
RING_C_IP = "ip_004"

CYCLE_USERS = ["u21", "u22", "u23"]

HUB_IP = "ip_001"
HUB_USERS = [f"u{i:02d}" for i in range(13, 25)]

RING_OF: dict[str, str] = {}
for u in RING_A_USERS:
    RING_OF[u] = "A"
for u in RING_B_USERS:
    RING_OF[u] = "B"
for u in RING_C_USERS:
    RING_OF[u] = "C"
for u in CYCLE_USERS:
    RING_OF[u] = "cycle"


def _assign_with_cap(pool: list[str], n_needed: int, cap: int, rng: random.Random) -> list[str]:
    """Hand out items from pool so no single item is used more than cap times."""
    slots = [x for x in pool for _ in range(cap)]
    if len(slots) < n_needed:
        raise ValueError(f"pool too small: {len(slots)} slots for {n_needed} needed")
    rng.shuffle(slots)
    return slots[:n_needed]


def seed_to_events(run_id: str, base_ts: datetime) -> list[Event]:
    """
    Build the original seed.py graph and convert it to a sequence of Events.

    Uses the same RNG seed (42) as the original to produce an identical graph.
    Each graph mutation becomes an event with a monotonically increasing timestamp.
    """
    rng = random.Random(42)

    users = [f"u{i:02d}" for i in range(1, N_USERS + 1)]
    devices = [f"device_{i:03d}" for i in range(1, N_DEVICES + 1)]
    cards = [f"card_{i:03d}" for i in range(1, N_CARDS + 1)]
    ips = [f"ip_{i:03d}" for i in range(1, N_IPS + 1)]

    free_devices = [d for d in devices if d != RING_A_DEVICE]
    free_cards = [c for c in cards if c != RING_B_CARD]
    free_ips = [i for i in ips if i not in (HUB_IP, RING_C_IP)]

    noise_devices = _assign_with_cap(free_devices, len(users), cap=2, rng=rng)
    noise_cards = _assign_with_cap(free_cards, len(users), cap=2, rng=rng)
    noise_ips = _assign_with_cap(free_ips, len(users), cap=2, rng=rng)

    events: list[Event] = []
    seq = 0
    ms = 0

    def _emit(event_type: EventType, payload: dict) -> None:
        nonlocal seq, ms
        seq += 1
        ms += 10  # 10ms between events
        events.append(
            Event(
                event_id=new_event_id(),
                event_type=event_type,
                ts=base_ts + timedelta(milliseconds=ms),
                payload=payload,
                run_id=run_id,
                seq=seq,
            )
        )

    # Phase 1: User joins
    for u in users:
        _emit(EventType.USER_JOIN, {"user_id": u, "name": f"User {u[1:]}"})

    # Phase 2: Device links (noise)
    used_device: dict[str, str] = {}
    for u, dev in zip(users, noise_devices, strict=False):
        used_device[u] = dev
        _emit(EventType.DEVICE_LINK, {"user_id": u, "device_id": dev})

    # Phase 3: Card links (noise)
    has_card: dict[str, str] = {}
    for u, card in zip(users, noise_cards, strict=False):
        has_card[u] = card
        _emit(EventType.CARD_LINK, {"user_id": u, "card_id": card})

    # Phase 4: IP links (noise)
    logged_in_from: dict[str, list[str]] = defaultdict(list)
    for u, ip in zip(users, noise_ips, strict=False):
        logged_in_from[u] = [ip]
        _emit(EventType.IP_LINK, {"user_id": u, "ip": ip})

    # Phase 5: Plant fraud rings
    # Ring A: shared device
    for u in RING_A_USERS:
        used_device[u] = RING_A_DEVICE
        _emit(EventType.DEVICE_LINK, {"user_id": u, "device_id": RING_A_DEVICE})

    # Ring B: shared card
    for u in RING_B_USERS:
        has_card[u] = RING_B_CARD
        _emit(EventType.CARD_LINK, {"user_id": u, "card_id": RING_B_CARD})

    # Ring C: shared IP
    for u in RING_C_USERS:
        logged_in_from[u].append(RING_C_IP)
        _emit(EventType.IP_LINK, {"user_id": u, "ip": RING_C_IP})

    # Hub (not fraud): office wifi
    for u in HUB_USERS:
        logged_in_from[u].append(HUB_IP)
        _emit(EventType.IP_LINK, {"user_id": u, "ip": HUB_IP})

    # Phase 6: Transactions — cycle first, then noise
    a, b, c = CYCLE_USERS
    for i, (sender, receiver, amount) in enumerate(
        [(a, b, 5000.0), (b, c, 4800.0), (c, a, 4600.0)]
    ):
        _emit(
            EventType.TRANSACTION,
            {
                "txn_id": f"t{i:03d}",
                "sender": sender,
                "receiver": receiver,
                "amount": amount,
            },
        )

    out: dict[str, set[str]] = defaultdict(set)
    out[a].add(b)
    out[b].add(c)
    out[c].add(a)

    def _closes_cycle(sender: str, receiver: str) -> bool:
        return any(sender in out[mid] for mid in out[receiver])

    made = 0
    while made < N_RANDOM_TXNS:
        sender, receiver = rng.sample(users, 2)
        if receiver in out[sender] or _closes_cycle(sender, receiver):
            continue
        out[sender].add(receiver)
        _emit(
            EventType.TRANSACTION,
            {
                "txn_id": f"t{made + 3:03d}",
                "sender": sender,
                "receiver": receiver,
                "amount": round(rng.uniform(50, 2000), 2),
            },
        )
        made += 1

    return events


def seed_ground_truth(ledger: GroundTruthLedger, events: list[Event]) -> None:
    """
    Record the four planted seed.py rings in the ground-truth ledger.

    Replaces the old `ring` node property: labels live here, never on the graph.
    Each ring spawns at its first planting event and activates at its last one.
    """
    plantings: dict[str, list[datetime]] = defaultdict(list)
    for ev in events:
        p = ev.payload
        if ev.event_type == EventType.DEVICE_LINK and p.get("device_id") == RING_A_DEVICE:
            plantings["A"].append(ev.ts)
        elif ev.event_type == EventType.CARD_LINK and p.get("card_id") == RING_B_CARD:
            plantings["B"].append(ev.ts)
        elif ev.event_type == EventType.IP_LINK and p.get("ip") == RING_C_IP:
            plantings["C"].append(ev.ts)
        elif ev.event_type == EventType.TRANSACTION and p.get("txn_id") in ("t000", "t001", "t002"):
            plantings["cycle"].append(ev.ts)

    rings = [
        ("A", RingType.SHARED_DEVICE, RING_A_USERS, {"device": RING_A_DEVICE}),
        ("B", RingType.SHARED_CARD, RING_B_USERS, {"card": RING_B_CARD}),
        ("C", RingType.SHARED_IP, RING_C_USERS, {"ip": RING_C_IP}),
        ("cycle", RingType.MONEY_CYCLE, CYCLE_USERS, {}),
    ]
    for label, ring_type, members, shared in rings:
        times = plantings[label]
        ledger.spawn_ring(
            ring_id=f"seed_{label}",
            ring_type=ring_type,
            members=set(members),
            ts=min(times),
            shared_attributes=shared,
            details={"source": "seed.py"},
        )
        ledger.activate_ring(f"seed_{label}", max(times))
