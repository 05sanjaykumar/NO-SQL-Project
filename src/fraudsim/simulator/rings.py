"""
Fraud ring agents and the adaptive attacker.

Each RingAgent drives one ring through SPAWN -> GROW -> ACTIVE -> (MUTATE)* ->
DISSOLVE and emits only observable events (joins, links, unlinks,
transactions). Everything that would reveal the ring (its id, type, members,
strategy) goes to the ground-truth ledger, never into an event payload.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from fraudsim.simulator.config import SHARED_ATTRIBUTE_KIND, AdaptiveSpec, RingSpec
from fraudsim.types import EvasionMode, RingLifecycle, RingType

if TYPE_CHECKING:
    from fraudsim.simulator.event_generator import EventGenerator

# Naive per-transaction reporting threshold that smurfing deliberately stays under.
REPORTING_THRESHOLD = 10_000.0


@dataclass(frozen=True, slots=True)
class StrategyParams:
    """How an evasion strategy bends a ring's timing and amounts."""

    grow_mult: float = 1.0
    action_mult: float = 1.0
    amount_mult: float = 1.0
    hop_gap_s: tuple[float, float] = (1.0, 4.0)  # gap between hops of a cycle / chain / burst
    rotate_every: int = 0  # identity rotation after this many actions (0 = never)
    decoys_per_action: int = 0
    hide_behind_hub: bool = False


STRATEGIES: dict[EvasionMode, StrategyParams] = {
    EvasionMode.NONE: StrategyParams(),
    EvasionMode.HUB_HIDING: StrategyParams(hide_behind_hub=True),
    EvasionMode.LOW_AND_SLOW: StrategyParams(
        grow_mult=6.0, action_mult=8.0, amount_mult=0.15, hop_gap_s=(30.0, 120.0)
    ),
    EvasionMode.IDENTITY_ROTATION: StrategyParams(rotate_every=3),
    EvasionMode.DECOY_EDGES: StrategyParams(decoys_per_action=2),
}


class RingAgent:
    """One simulated fraud ring."""

    def __init__(
        self,
        gen: EventGenerator,
        spec: RingSpec,
        rng: random.Random,
        strategy: EvasionMode,
    ) -> None:
        self.gen = gen
        self.spec = spec
        self.rng = rng
        self.strategy = strategy
        self.params = STRATEGIES[strategy]
        self.members: list[str] = []  # ordered: position encodes role (cycle order, collector)
        self.anchors: dict[str, str] = {}  # kind -> attribute id the ring coordinates on
        self.cover_hub: str | None = None  # (kind:attr) hub every member also uses as cover
        self.state = RingLifecycle.SPAWNED
        self.actions_done = 0
        self.mixed_pattern: list[tuple[str, str]] = []

    @property
    def ring_id(self) -> str:
        return self.spec.ring_id

    @property
    def alive(self) -> bool:
        return self.state != RingLifecycle.DISSOLVED

    # ─── Lifecycle ────────────────────────────────────────────────────

    def spawn(self) -> None:
        gen = self.gen
        self._choose_anchors()
        for _ in range(min(self.spec.initial_members, self.spec.size)):
            self.members.append(self._recruit())
        shared = {f"{k}": v for k, v in self.anchors.items()}
        gen.ledger.spawn_ring(
            ring_id=self.ring_id,
            ring_type=self.spec.ring_type,
            members=set(self.members),
            ts=gen.ts(),
            evasion_mode=self.spec.evasion,
            shared_attributes=shared,
            details={
                "strategy": self.strategy.value,
                "target_size": self.spec.size,
                "cycle_length": self.spec.length,
                "label": self.spec.shared_attribute,
                "cover_hub": self.cover_hub,
                "intensity": self.spec.intensity,
            },
        )
        if len(self.members) >= self.spec.size:
            self._activate()
        else:
            self.state = RingLifecycle.GROWING
            self._schedule_grow()

    def _schedule_grow(self) -> None:
        delay = self.rng.expovariate(1.0 / (self.spec.grow_interval_s * self.params.grow_mult))
        self.gen.schedule(delay, self._grow)

    def _grow(self) -> None:
        if not self.alive:
            return
        uid = self._recruit()
        self.members.append(uid)
        self.gen.ledger.grow_ring(self.ring_id, uid, self.gen.ts())
        if len(self.members) >= self.spec.size:
            self._activate()
        else:
            self._schedule_grow()

    def _activate(self) -> None:
        self.state = RingLifecycle.ACTIVE
        self.gen.ledger.activate_ring(self.ring_id, self.gen.ts())
        self._schedule_action()
        if self.spec.active_duration_s is not None:
            self.gen.schedule(self.spec.active_duration_s, self.dissolve)

    def _schedule_action(self, busy_s: float = 0.0) -> None:
        """Next action after the current one finishes (busy_s) plus a random pause."""
        mean = self.spec.action_interval_s * self.params.action_mult
        self.gen.schedule(busy_s + self.rng.expovariate(1.0 / mean), self._act)

    def _act(self) -> None:
        if self.state not in (RingLifecycle.ACTIVE, RingLifecycle.MUTATING):
            return
        busy_s = self._fraud_action()
        self._decoys()
        self.actions_done += 1
        if self.params.rotate_every and self.actions_done % self.params.rotate_every == 0:
            self._rotate_identity()
        if self.state == RingLifecycle.ACTIVE and self.rng.random() < self.spec.mutate_prob:
            self._mutate()
        self._schedule_action(busy_s)

    def dissolve(self) -> None:
        if not self.alive:
            return
        self.state = RingLifecycle.DISSOLVED
        gen = self.gen
        gen.ledger.dissolve_ring(self.ring_id, gen.ts())
        # Members vanish: accounts close over the next few seconds.
        for uid in list(self.members):
            gen.schedule(self.rng.uniform(0.0, 5.0), gen.leave_user, uid)
        gen.on_ring_dissolved(self)

    # ─── Membership ───────────────────────────────────────────────────

    def _choose_anchors(self) -> None:
        gen = self.gen
        rt = self.spec.ring_type
        hide = self.params.hide_behind_hub
        if rt in SHARED_ATTRIBUTE_KIND:
            kind = SHARED_ATTRIBUTE_KIND[rt]
            hub = gen.find_hub(kind, self.spec.shared_attribute) if hide else None
            if hub is not None:
                self.anchors[kind] = hub
            else:
                self.anchors[kind] = gen.world.pools[kind].reserve()
                if hide:
                    self.cover_hub = gen.find_hub_any()
        elif rt == RingType.MIXED_ATTRIBUTE:
            for kind in ("device", "card", "ip"):
                hub = gen.find_hub(kind, None) if (hide and kind == "ip") else None
                self.anchors[kind] = hub or gen.world.pools[kind].reserve()
            # Each member links to two of the three anchors, rotating, so members
            # overlap partially and no single attribute covers the whole ring.
            self.mixed_pattern = [("device", "card"), ("card", "ip"), ("ip", "device")]
        elif hide:
            self.cover_hub = gen.find_hub_any()

    def _recruit(self) -> str:
        """Create a fresh account for the ring and wire up its links."""
        gen = self.gen
        uid = gen.join_user(benign=False)
        rt = self.spec.ring_type
        if rt in SHARED_ATTRIBUTE_KIND:
            kind = SHARED_ATTRIBUTE_KIND[rt]
            gen.link(uid, kind, self.anchors[kind])
        elif rt == RingType.MIXED_ATTRIBUTE:
            pattern = self.mixed_pattern[len(self.members) % len(self.mixed_pattern)]
            for kind in pattern:
                gen.link(uid, kind, self.anchors[kind])
        if self.cover_hub is not None:
            kind, attr = self.cover_hub.split(":", 1)
            gen.link(uid, kind, attr)
        if self.params.decoys_per_action:
            # Decoy: share an attribute with a random legitimate user.
            decoy = gen.world.sample_benign()
            if decoy is not None:
                kind = self.rng.choice(("device", "ip"))
                attrs = sorted(gen.world.users[decoy].attrs[kind])
                if attrs:
                    gen.link(uid, kind, self.rng.choice(attrs))
        return uid

    def _rotate_identity(self) -> None:
        """Retire one account and replace it with a fresh one in the same role."""
        if len(self.members) < 2:
            return
        gen = self.gen
        idx = self.rng.randrange(len(self.members))
        old = self.members[idx]
        new = self._recruit()
        self.members[idx] = new
        gen.leave_user(old)
        gen.ledger.replace_member(self.ring_id, old, new, gen.ts())

    def _mutate(self) -> None:
        """Swap the ring's shared device/card (or one member's device if anchored on a hub)."""
        gen = self.gen
        self.state = RingLifecycle.MUTATING
        rt = self.spec.ring_type
        kind = SHARED_ATTRIBUTE_KIND.get(rt)
        changes: dict[str, object]
        if kind is not None and kind != "ip" and not gen.is_hub(kind, self.anchors[kind]):
            old, new = self.anchors[kind], gen.world.pools[kind].reserve()
            self.anchors[kind] = new
            changes = {"kind": kind, "old": old, "new": new, "shared_attributes": {kind: new}}
            for i, uid in enumerate(list(self.members)):
                gen.schedule(0.2 * i, self._swap_link, uid, kind, old, new)
            finish = 0.2 * len(self.members) + 0.1
        else:
            uid = self.rng.choice(self.members)
            old_devices = sorted(gen.world.users[uid].attrs["device"])
            old = old_devices[0] if old_devices else ""
            new = gen.world.pools["device"].reserve()
            changes = {"kind": "device", "member": uid, "old": old, "new": new}
            gen.schedule(0.0, self._swap_link, uid, "device", old, new)
            finish = 0.1
        gen.ledger.mutate_ring(self.ring_id, gen.ts(), changes)
        gen.schedule(finish, self._finish_mutation)

    def _swap_link(self, uid: str, kind: str, old: str, new: str) -> None:
        if not self.gen.world.users[uid].active:
            return
        if old:
            self.gen.unlink(uid, kind, old)
        self.gen.link(uid, kind, new)

    def _finish_mutation(self) -> None:
        if self.state == RingLifecycle.MUTATING:
            self.state = RingLifecycle.ACTIVE
            self.gen.ledger.resume_ring(self.ring_id, self.gen.ts())

    # ─── Fraudulent activity ──────────────────────────────────────────

    def _amount(self, median: float) -> float:
        base = self.rng.lognormvariate(math.log(median), 0.4)
        return round(base * self.spec.intensity * self.params.amount_mult, 2)

    def _hop_gap(self) -> float:
        lo, hi = self.params.hop_gap_s
        return self.rng.uniform(lo, hi)

    def _fraud_action(self) -> float:
        """Perform one fraudulent action. Returns how long (sim s) its scheduled hops take."""
        rt = self.spec.ring_type
        gen = self.gen
        m = self.members
        if len(m) < 2:
            return 0.0
        if rt == RingType.MONEY_CYCLE:
            length = self.spec.length or len(m)
            order = m[:length]
            start = self.rng.uniform(3000.0, 9000.0) * self.spec.intensity
            start *= self.params.amount_mult
            for feeder in m[length:]:
                gen.transact(feeder, order[0], self._amount(1500.0))
            return self._chain([*order, order[0]], round(start, 2))
        elif rt == RingType.LAYERED_CHAIN:
            start = self.rng.uniform(5000.0, 20000.0) * self.spec.intensity
            return self._chain(list(m), round(start * self.params.amount_mult, 2))
        elif rt == RingType.SMURFING_FAN_IN:
            collector, smurfs = m[0], m[1:]
            return self._burst([(s, collector) for s in smurfs])
        elif rt == RingType.SMURFING_FAN_OUT:
            source, mules = m[0], m[1:]
            return self._burst([(source, r) for r in mules])
        else:
            # Shared-attribute and mixed rings: money moves inside the ring, and members
            # keep touching their shared attribute (refreshing the link timestamp).
            sender, receiver = self.rng.sample(m, 2)
            gen.transact(sender, receiver, self._amount(900.0))
            if self.anchors and self.rng.random() < 0.3:
                kind = self.rng.choice(sorted(self.anchors))
                member = self.rng.choice(m)
                if self.anchors[kind] in gen.world.users[member].attrs[kind]:
                    gen.link(member, kind, self.anchors[kind], force=True)
            return 0.0

    def _chain(self, path: list[str], amount: float) -> float:
        """Pass money along path, each hop keeping a small cut, hops spaced in time."""
        delay = 0.0
        for sender, receiver in itertools.pairwise(path):
            self.gen.schedule(delay, self._hop, sender, receiver, amount)
            amount = round(amount * (1.0 - self.rng.uniform(0.01, 0.05)), 2)
            delay += self._hop_gap()
        return delay

    def _burst(self, pairs: list[tuple[str, str]]) -> float:
        """Smurfing: many transfers just under the reporting threshold within a short window."""
        delay = 0.0
        for sender, receiver in pairs:
            amount = REPORTING_THRESHOLD * self.rng.uniform(0.85, 0.99)
            amount *= self.spec.intensity * self.params.amount_mult
            self.gen.schedule(delay, self._hop, sender, receiver, round(amount, 2))
            delay += self._hop_gap()
        return delay

    def _hop(self, sender: str, receiver: str, amount: float) -> None:
        if self.alive:
            self.gen.transact(sender, receiver, amount)

    def _decoys(self) -> None:
        for _ in range(self.params.decoys_per_action):
            target = self.gen.world.sample_benign()
            if target is not None and self.members:
                self.gen.transact(self.rng.choice(self.members), target, self._amount(80.0))


class AdaptiveAttacker:
    """
    An attacker that watches which of its rings get flagged and shifts strategy.

    It keeps a weight per evasion strategy. When one of its rings is flagged it
    abandons that ring, multiplies the strategy's weight by `penalty`, and
    launches a replacement ring with a strategy drawn from the updated weights.
    """

    def __init__(self, spec: AdaptiveSpec, rng: random.Random) -> None:
        self.spec = spec
        self.rng = rng
        self.weights: dict[EvasionMode, float] = {s: 1.0 for s in spec.strategies}
        self.history: list[dict[str, object]] = []
        self._generation: dict[str, int] = {}

    def choose(self) -> EvasionMode:
        strategies = list(self.spec.strategies)
        return self.rng.choices(strategies, weights=[self.weights[s] for s in strategies])[0]

    def penalise(self, strategy: EvasionMode) -> None:
        if strategy in self.weights:
            self.weights[strategy] *= self.spec.penalty

    def replacement_spec(self, burned: RingSpec) -> RingSpec:
        root = burned.ring_id.split("~", 1)[0]
        n = self._generation.get(root, 0) + 1
        self._generation[root] = n
        return replace(burned, ring_id=f"{root}~{n}", spawn_time_s=0.0)
