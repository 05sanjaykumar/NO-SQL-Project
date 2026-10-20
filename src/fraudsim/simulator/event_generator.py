"""
Real-time graph simulator: a deterministic discrete-event generator.

The generator advances a simulated clock and produces observable Events
(user joins/leaves, attribute links/unlinks, transactions). Timestamps are
simulated time on a fixed epoch, so a (scenario, seed) pair always produces
the same stream, byte for byte, independent of wall-clock speed. Pacing in
wall-clock time (rate, pause/resume, speed multiplier) is the job of
`StreamPlayer`, which consumes this generator.

Ground truth goes to the GroundTruthLedger. Event payloads never contain a
ring id, ring type or any other label.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import math
import random
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fraudsim.simulator.config import (
    HUB_KINDS,
    HubSpec,
    RingSpec,
    SimulationConfig,
    parse_simulation_config,
)
from fraudsim.simulator.ground_truth import GroundTruthLedger
from fraudsim.simulator.rings import AdaptiveAttacker, RingAgent
from fraudsim.simulator.world import LINK_EVENT, PAYLOAD_KEY, World
from fraudsim.types import EvasionMode, Event, EventType

# Contacts a benign user remembers and mostly transacts with again.
MAX_CONTACTS = 8
REPEAT_CONTACT_PROB = 0.7


class EventGenerator:
    """
    Deterministic event stream for one scenario and seed.

    Usage (headless)::

        gen = EventGenerator(load_scenario("baseline"), seed=7, run_id="r1")
        events = gen.generate()          # whole scenario
        gen.ledger                       # ground truth, for evaluation only

    Usage (live): call `next_event()` repeatedly (StreamPlayer does this),
    `set_rate()` to change the benign event rate, and `notify_flagged()` to
    tell the adaptive attacker which accounts were flagged.
    """

    def __init__(
        self,
        seed: int = 42,
        run_id: str = "",
        config: SimulationConfig | dict[str, Any] | None = None,
    ) -> None:
        self.seed = seed
        self._run_id = run_id
        self.config: SimulationConfig | None = (
            parse_simulation_config(config) if isinstance(config, dict) and config else None
        )
        if isinstance(config, SimulationConfig):
            self.config = config
        self.ledger = GroundTruthLedger()
        self._seq = 0
        # Independent RNG streams so that, e.g., adding a ring does not change the
        # benign background. String seeds hash deterministically (SHA-512).
        self._rng_benign = random.Random(f"{seed}:benign")
        self._rng_rings = random.Random(f"{seed}:rings")
        self._rng_ids = random.Random(f"{seed}:ids")
        self._rng_attack = random.Random(f"{seed}:adaptive")
        self._now = 0.0
        self._queue: list[tuple[float, int, Callable[..., None], tuple[Any, ...]]] = []
        self._order = itertools.count()
        self._out: deque[Event] = deque()
        self._started = False
        self._digest = hashlib.sha256()
        self.control_log: list[dict[str, Any]] = []
        self.rings: dict[str, RingAgent] = {}
        self.hubs: dict[str, dict[str, Any]] = {}  # hub name -> {kind, attr_id, category, size}
        self._hub_attrs: set[tuple[str, str]] = set()
        self._benign_token = 0
        self._rate_mult = 1.0
        self._amount_mult = 1.0
        self.counts: dict[str, int] = {}
        self._random_ring_counter = itertools.count(1)
        self.world: World
        self.attacker: AdaptiveAttacker | None = None
        if self.config is not None:
            self._base_ts = self.config.start_ts
            self.rate = self.config.target_event_rate
            self.world = World(
                self._rng_benign,
                self.config.num_devices,
                self.config.num_cards,
                self.config.num_ips,
                self.config.heavy_tail_alpha,
            )
            self.attacker = AdaptiveAttacker(self.config.adaptive, self._rng_attack)
        else:
            self._base_ts = datetime(2026, 1, 1, tzinfo=UTC)
            self.rate = 0.0

    # ─── Clock, ids, scheduling ───────────────────────────────────────

    @property
    def now_s(self) -> float:
        """Current simulated time in seconds since the scenario start."""
        return self._now

    @property
    def run_id(self) -> str:
        return self._run_id

    def ts(self, at_s: float | None = None) -> datetime:
        return self._base_ts + timedelta(seconds=self._now if at_s is None else at_s)

    def schedule(self, delay_s: float, fn: Callable[..., None], *args: Any) -> None:
        """Run fn(*args) at now + delay in simulated time."""
        heapq.heappush(self._queue, (self._now + max(0.0, delay_s), next(self._order), fn, args))

    def _event_id(self) -> str:
        return uuid.UUID(int=self._rng_ids.getrandbits(128), version=4).hex

    def emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        self._seq += 1
        event = Event(
            event_id=self._event_id(),
            event_type=event_type,
            ts=self.ts(),
            payload=payload,
            run_id=self._run_id,
            seq=self._seq,
        )
        self._out.append(event)
        self.counts[event_type.value] = self.counts.get(event_type.value, 0) + 1
        self._digest.update(_canonical(event).encode())

    @property
    def digest(self) -> str:
        """SHA-256 over every event emitted so far (run_id excluded): a determinism fingerprint."""
        return self._digest.hexdigest()

    @property
    def events_emitted(self) -> int:
        return self._seq

    # ─── Observable primitives used by benign traffic and ring agents ─

    def join_user(self, benign: bool) -> str:
        assert self.config is not None
        user = self.world.add_user(self._now, benign=benign)
        self.emit(EventType.USER_JOIN, {"user_id": user.user_id, "name": f"User {user.user_id}"})
        for kind in ("device", "card", "ip"):
            self.link(user.user_id, kind, self.world.pools[kind].take_personal())
        return user.user_id

    def leave_user(self, user_id: str) -> None:
        user = self.world.users.get(user_id)
        if user is None or not user.active:
            return
        self.world.remove_user(user_id)
        self.emit(EventType.USER_LEAVE, {"user_id": user_id})

    def link(self, user_id: str, kind: str, attr_id: str, force: bool = False) -> None:
        """Link a user to an attribute. Re-emits an existing link only when force=True."""
        if not self.world.users[user_id].active:
            return
        created = self.world.link(user_id, kind, attr_id)
        if created or force:
            self.emit(LINK_EVENT[kind], {"user_id": user_id, PAYLOAD_KEY[kind]: attr_id})

    def unlink(self, user_id: str, kind: str, attr_id: str) -> None:
        if self.world.unlink(user_id, kind, attr_id):
            self.emit(
                EventType.ATTRIBUTE_UNLINK,
                {"user_id": user_id, "attr_type": kind, "attr_id": attr_id},
            )

    def transact(self, sender: str, receiver: str, amount: float) -> None:
        users = self.world.users
        if sender == receiver or not users[sender].active or not users[receiver].active:
            return
        self.emit(
            EventType.TRANSACTION,
            {
                "txn_id": self.world.new_txn_id(),
                "sender": sender,
                "receiver": receiver,
                "amount": round(amount, 2),
            },
        )

    # ─── Hubs (benign shared infrastructure) ──────────────────────────

    def is_hub(self, kind: str, attr_id: str) -> bool:
        return (kind, attr_id) in self._hub_attrs

    def find_hub(self, kind: str, name: str | None) -> str | None:
        """Attribute id of the named hub, or the largest hub of this kind."""
        if name and name in self.hubs and self.hubs[name]["kind"] == kind:
            return str(self.hubs[name]["attr_id"])
        candidates = [h for h in self.hubs.values() if h["kind"] == kind]
        if not candidates:
            return None
        best = max(candidates, key=lambda h: (h["size"], h["attr_id"]))
        return str(best["attr_id"])

    def find_hub_any(self) -> str | None:
        """'kind:attr_id' of the largest hub of any kind (used as cover by hub-hiding rings)."""
        if not self.hubs:
            return None
        best = max(self.hubs.values(), key=lambda h: (h["size"], h["attr_id"]))
        return f"{best['kind']}:{best['attr_id']}"

    def _create_hub(self, spec: HubSpec) -> None:
        attr_id = self.world.pools[spec.kind].reserve()
        self.hubs[spec.name] = {
            "name": spec.name,
            "kind": spec.kind,
            "attr_id": attr_id,
            "category": spec.category,
            "size": spec.size,
            "created_s": self._now,
        }
        self._hub_attrs.add((spec.kind, attr_id))
        members = self.world.sample_benign_uniform(spec.size)
        for i, uid in enumerate(members):
            delay = spec.fill_time_s * (i / max(1, len(members) - 1)) if spec.fill_time_s else 0.0
            self.schedule(delay, self.link, uid, spec.kind, attr_id)

    def _random_hub_specs(self) -> list[HubSpec]:
        assert self.config is not None
        dist = self.config.hub_distribution
        specs = []
        for i in range(dist.count):
            draw = dist.min_size * self._rng_benign.paretovariate(dist.alpha)
            size = int(min(dist.max_size, draw))
            category = self._rng_benign.choice(list(dist.categories))
            specs.append(
                HubSpec(
                    name=f"auto_{category}_{i:03d}",
                    kind=HUB_KINDS[category],
                    size=max(2, size),
                    category=category,
                )
            )
        return specs

    # ─── Benign behaviour ─────────────────────────────────────────────

    def _schedule_benign(self) -> None:
        rate = self.rate * self._rate_mult
        if rate <= 0:
            return
        self.schedule(self._rng_benign.expovariate(rate), self._benign_event, self._benign_token)

    def _benign_event(self, token: int) -> None:
        if token != self._benign_token:
            return  # superseded by a rate change
        assert self.config is not None
        mix = self.config.benign_mix
        kinds = sorted(mix)
        kind = self._rng_benign.choices(kinds, weights=[mix[k] for k in kinds])[0]
        world = self.world
        sender = world.sample_benign()
        if sender is not None:
            if kind == "transaction":
                self._benign_transaction(sender)
            elif kind == "ip_login":
                self._benign_login(sender)
            elif kind == "device_link":
                self.link(sender, "device", world.pools["device"].take_personal())
            elif kind == "card_link":
                self.link(sender, "card", world.pools["card"].take_personal())
        self._schedule_benign()

    def _benign_transaction(self, sender: str) -> None:
        assert self.config is not None
        world = self.world
        user = world.users[sender]
        contacts = [c for c in user.contacts if world.users[c].active]
        if contacts and self._rng_benign.random() < REPEAT_CONTACT_PROB:
            receiver: str | None = self._rng_benign.choice(contacts)
        else:
            receiver = world.sample_benign(exclude=sender)
            if receiver is not None and receiver not in user.contacts:
                user.contacts.append(receiver)
                if len(user.contacts) > MAX_CONTACTS:
                    user.contacts.pop(0)
        if receiver is None:
            return
        cfg = self.config
        amount = self._rng_benign.lognormvariate(math.log(cfg.amount_median), cfg.amount_sigma)
        self.transact(sender, receiver, amount * self._amount_mult)

    def _benign_login(self, uid: str) -> None:
        """Re-login from a known IP (refreshes the link), a shared public hub, or a new IP."""
        world = self.world
        r = self._rng_benign.random()
        own = sorted(world.users[uid].attrs["ip"])
        public = sorted(
            h["attr_id"]
            for h in self.hubs.values()
            if h["kind"] == "ip" and h["category"] in ("public_wifi", "carrier_nat")
        )
        if own and r < 0.6:
            self.link(uid, "ip", self._rng_benign.choice(own), force=True)
        elif public and r < 0.85:
            self.link(uid, "ip", self._rng_benign.choice(public))
        else:
            self.link(uid, "ip", world.pools["ip"].take_personal())

    def _user_join(self) -> None:
        assert self.config is not None
        self.join_user(benign=True)
        self.schedule(self._rng_benign.expovariate(self.config.user_join_rate), self._user_join)

    def _user_leave(self) -> None:
        assert self.config is not None
        # Keep at least a handful of benign users so traffic can continue.
        if self.world.benign_count > 5:
            uid = self._rng_benign.choice(self.world.benign_users())
            self.leave_user(uid)
        self.schedule(self._rng_benign.expovariate(self.config.user_leave_rate), self._user_leave)

    def _drift(self) -> None:
        assert self.config is not None and self.config.drift is not None
        drift = self.config.drift
        self._rate_mult = drift.shift_multiplier
        self._amount_mult = drift.shift_multiplier
        self.control_log.append(
            {"t_s": self._now, "action": "drift", "shift_multiplier": drift.shift_multiplier}
        )
        if drift.new_hub_size > 0:
            self._create_hub(
                HubSpec(
                    name="drift_hub",
                    kind=drift.new_hub_kind,
                    size=drift.new_hub_size,
                    category="office" if drift.new_hub_kind == "ip" else "kiosk",
                    fill_time_s=10.0,
                )
            )
        self._benign_token += 1
        self._schedule_benign()

    # ─── Rings ────────────────────────────────────────────────────────

    def _spawn_ring(self, spec: RingSpec) -> None:
        strategy = spec.evasion
        if strategy == EvasionMode.ADAPTIVE:
            assert self.attacker is not None
            strategy = self.attacker.choose()
        agent = RingAgent(self, spec, random.Random(f"{self.seed}:ring:{spec.ring_id}"), strategy)
        self.rings[spec.ring_id] = agent
        agent.spawn()

    def _random_ring(self) -> None:
        assert self.config is not None
        rr = self.config.random_rings
        rng = self._rng_rings
        ring_type = rng.choice(list(rr.types))
        modes = sorted(rr.evasion_weights, key=lambda m: m.value)
        evasion = rng.choices(modes, weights=[rr.evasion_weights[m] for m in modes])[0]
        size = rng.randint(rr.size_min, rr.size_max)
        length = min(max(3, size), 6) if ring_type.value == "money_cycle" else None
        spec = RingSpec(
            ring_id=f"rnd_{next(self._random_ring_counter):04d}",
            ring_type=ring_type,
            size=max(size, length or 0),
            spawn_time_s=self._now,
            evasion=evasion,
            length=length,
            active_duration_s=rng.uniform(*rr.active_duration_s),
        )
        self._spawn_ring(spec)
        self.schedule(rng.expovariate(rr.rate_per_min / 60.0), self._random_ring)

    def inject_ring(self, ring_type: str, evasion: str = "none", size: int = 4) -> str:
        """Spawn a ring right now (dashboard "inject" control). Logged in control_log."""
        from fraudsim.types import RingType

        rtype = RingType(ring_type)
        mode = EvasionMode(evasion)
        size = max(2, int(size))
        length = min(max(3, size), 6) if rtype == RingType.MONEY_CYCLE else None
        ring_id = f"inj_{next(self._random_ring_counter):04d}"
        spec = RingSpec(
            ring_id=ring_id,
            ring_type=rtype,
            size=max(size, length or 0),
            spawn_time_s=self._now,
            evasion=mode,
            length=length,
            grow_interval_s=1.5,
            action_interval_s=2.0,
        )
        self.control_log.append(
            {
                "t_s": self._now,
                "action": "inject_ring",
                "ring_id": ring_id,
                "ring_type": rtype.value,
                "evasion": mode.value,
                "size": spec.size,
            }
        )
        self.schedule(0.0, self._spawn_ring, spec)
        return ring_id

    def on_ring_dissolved(self, agent: RingAgent) -> None:
        """Hook called by a ring agent when it dissolves."""

    def notify_flagged(self, user_ids: set[str] | list[str]) -> list[str]:
        """
        Feedback channel for the adaptive attacker: these accounts were flagged.

        The attacker only learns about its own accounts (as a real fraudster would
        when accounts get blocked). Adaptive rings with a flagged member are
        abandoned and replaced by a ring using a re-weighted strategy. Returns the
        ring ids that reacted. Calls are logged so a run can be replayed.
        """
        flagged = set(user_ids)
        self.control_log.append(
            {"t_s": self._now, "action": "notify_flagged", "user_ids": sorted(flagged)}
        )
        reacted: list[str] = []
        if self.attacker is None:
            return reacted
        for ring_id in sorted(self.rings):
            agent = self.rings[ring_id]
            if agent.spec.evasion != EvasionMode.ADAPTIVE or not agent.alive:
                continue
            hit = flagged.intersection(agent.members)
            if not hit:
                continue
            reacted.append(ring_id)
            self.attacker.penalise(agent.strategy)
            self.ledger.record_attacker_observation(
                ring_id,
                self.ts(),
                {"flagged_members": sorted(hit), "strategy": agent.strategy.value},
            )
            agent.dissolve()
            live = sum(
                1 for a in self.rings.values() if a.alive and a.spec.evasion == EvasionMode.ADAPTIVE
            )
            if live < self.attacker.spec.max_rings:
                replacement = self.attacker.replacement_spec(agent.spec)
                self.schedule(self.attacker.spec.respawn_delay_s, self._spawn_ring, replacement)
            self.attacker.history.append(
                {
                    "t_s": self._now,
                    "burned": ring_id,
                    "strategy": agent.strategy.value,
                    "weights": {k.value: round(v, 6) for k, v in self.attacker.weights.items()},
                }
            )
        return reacted

    # ─── Rate control ─────────────────────────────────────────────────

    def set_rate(self, events_per_second: float) -> None:
        """Change the benign event rate (events per simulated second) from now on."""
        if events_per_second < 0:
            raise ValueError("rate must be >= 0")
        self.rate = float(events_per_second)
        self.control_log.append({"t_s": self._now, "action": "set_rate", "rate": self.rate})
        if self._started:
            self._benign_token += 1
            self._schedule_benign()

    # ─── Driving the simulation ───────────────────────────────────────

    def _start(self) -> None:
        if self._started:
            return
        if self.config is None:
            raise RuntimeError("EventGenerator needs a scenario config to simulate")
        self._started = True
        cfg = self.config
        for _ in range(cfg.num_users):
            self.join_user(benign=True)
        for hub in (*cfg.hubs, *self._random_hub_specs()):
            if hub.start_time_s > 0:
                self.schedule(hub.start_time_s, self._create_hub, hub)
            else:
                self._create_hub(hub)
        self._schedule_benign()
        if cfg.user_join_rate > 0:
            self.schedule(self._rng_benign.expovariate(cfg.user_join_rate), self._user_join)
        if cfg.user_leave_rate > 0:
            self.schedule(self._rng_benign.expovariate(cfg.user_leave_rate), self._user_leave)
        if cfg.drift is not None:
            self.schedule(cfg.drift.trigger_time_s, self._drift)
        for spec in cfg.rings:
            self.schedule(spec.spawn_time_s, self._spawn_ring, spec)
        if cfg.random_rings.rate_per_min > 0:
            first = self._rng_rings.expovariate(cfg.random_rings.rate_per_min / 60.0)
            self.schedule(first, self._random_ring)

    def _advance(self, until_s: float) -> bool:
        """Process scheduled actions until at least one event is buffered. False when done."""
        while not self._out:
            if not self._queue or self._queue[0][0] > until_s:
                return False
            t, _, fn, args = heapq.heappop(self._queue)
            self._now = t
            fn(*args)
        return True

    def next_event(self, until_s: float | None = None) -> Event | None:
        """Return the next event, or None once the scenario (or until_s) is exhausted."""
        self._start()
        assert self.config is not None
        limit = self.config.duration_seconds if until_s is None else until_s
        if not self._out and not self._advance(limit):
            return None
        return self._out.popleft()

    def iter_events(self, until_s: float | None = None) -> Iterator[Event]:
        while (event := self.next_event(until_s)) is not None:
            yield event

    def generate(self, until_s: float | None = None) -> list[Event]:
        """Run the scenario to completion (or until_s) and return every event."""
        return list(self.iter_events(until_s))

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self._run_id,
            "seed": self.seed,
            "scenario": self.config.name if self.config else None,
            "sim_time_s": round(self._now, 6),
            "events": self._seq,
            "events_by_type": dict(sorted(self.counts.items())),
            "users_total": len(self.world.users) if self.config else 0,
            "users_active": sum(1 for u in self.world.users.values() if u.active)
            if self.config
            else 0,
            "hubs": len(self.hubs),
            "rings": self.ledger.summary(),
            "adaptive_history": self.attacker.history if self.attacker else [],
            "digest": self.digest,
        }

    # ─── Legacy seed.py bridge ────────────────────────────────────────

    def generate_seed_events(self) -> list[Event]:
        """
        Convert the original seed.py graph into a sequence of events.

        Backward-compatibility bridge: the planted rings become events with
        timestamps, and their labels go to `self.ledger` instead of a node property.
        """
        from fraudsim.simulator._seed_bridge import seed_ground_truth, seed_to_events

        events = seed_to_events(self._run_id, self._base_ts)
        seed_ground_truth(self.ledger, events)
        return events


def _canonical(event: Event) -> str:
    """Stable serialisation of an event for hashing (run_id excluded so seeds compare)."""
    return json.dumps(
        {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "ts": event.ts.isoformat(),
            "payload": event.payload,
            "seq": event.seq,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
