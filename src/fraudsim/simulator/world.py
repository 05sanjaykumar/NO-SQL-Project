"""
Observable world state for the simulator.

Holds users, attributes (device / card / IP) and who is linked to what. It
knows nothing about ground truth: which users belong to a ring is tracked by
the ring agents and the ledger, never here. Identifiers are opaque and
sequential so nothing in an id hints at fraud or hub status.
"""

from __future__ import annotations

import bisect
import itertools
import random
from dataclasses import dataclass, field

from fraudsim.types import EventType

LINK_EVENT: dict[str, EventType] = {
    "device": EventType.DEVICE_LINK,
    "card": EventType.CARD_LINK,
    "ip": EventType.IP_LINK,
}
PAYLOAD_KEY: dict[str, str] = {"device": "device_id", "card": "card_id", "ip": "ip"}
_ID_FORMAT: dict[str, str] = {"device": "device_{:05d}", "card": "card_{:05d}", "ip": "ip_{:05d}"}

# Benign users may share a personal attribute with at most this many users in total.
# Two keeps accidental sharing realistic without fabricating 3+ member clusters
# (the same rule seed.py used for its noise).
PERSONAL_SHARE_CAP = 2


@dataclass(slots=True)
class User:
    user_id: str
    joined_s: float
    activity: float  # heavy-tailed activity weight for benign sampling
    active: bool = True
    attrs: dict[str, set[str]] = field(
        default_factory=lambda: {"device": set(), "card": set(), "ip": set()}
    )
    contacts: list[str] = field(default_factory=list)


class AttributePool:
    """Mints opaque attribute ids and hands out personal attributes with a sharing cap."""

    def __init__(self, kind: str, initial: int, rng: random.Random) -> None:
        self.kind = kind
        self._rng = rng
        self._counter = itertools.count(1)
        self.users: dict[str, set[str]] = {}
        # Attributes still available as *personal* attributes (usage < cap).
        self._personal_free: list[str] = []
        self._personal_use: dict[str, int] = {}
        for _ in range(initial):
            self._personal_free.append(self.mint())

    def mint(self) -> str:
        attr_id = _ID_FORMAT[self.kind].format(next(self._counter))
        self.users[attr_id] = set()
        return attr_id

    def degree(self, attr_id: str) -> int:
        return len(self.users.get(attr_id, ()))

    def take_personal(self) -> str:
        """Pick an attribute for a benign user's own use, minting one when the pool is full."""
        while self._personal_free:
            idx = self._rng.randrange(len(self._personal_free))
            attr_id = self._personal_free[idx]
            # Skip attributes that were repurposed (hub / ring anchor) or filled up elsewhere.
            if (
                self._personal_use.get(attr_id, 0) < PERSONAL_SHARE_CAP
                and self.degree(attr_id) < PERSONAL_SHARE_CAP
            ):
                self._personal_use[attr_id] = self._personal_use.get(attr_id, 0) + 1
                if self._personal_use[attr_id] >= PERSONAL_SHARE_CAP:
                    self._swap_remove(idx)
                return attr_id
            self._swap_remove(idx)
        attr_id = self.mint()
        self._personal_use[attr_id] = 1
        self._personal_free.append(attr_id)
        return attr_id

    def reserve(self) -> str:
        """A fresh attribute that will never be handed out as a personal one."""
        attr_id = self.mint()
        self._personal_use[attr_id] = PERSONAL_SHARE_CAP
        return attr_id

    def _swap_remove(self, idx: int) -> None:
        self._personal_free[idx] = self._personal_free[-1]
        self._personal_free.pop()


class World:
    """Users, attributes and links. Everything here is observable by detectors."""

    def __init__(
        self,
        rng: random.Random,
        num_devices: int,
        num_cards: int,
        num_ips: int,
        heavy_tail_alpha: float,
    ) -> None:
        self._rng = rng
        self._alpha = heavy_tail_alpha
        self._user_counter = itertools.count(1)
        self._txn_counter = itertools.count(1)
        self.users: dict[str, User] = {}
        self.pools: dict[str, AttributePool] = {
            "device": AttributePool("device", num_devices, rng),
            "card": AttributePool("card", num_cards, rng),
            "ip": AttributePool("ip", num_ips, rng),
        }
        # Benign users eligible to act in background traffic, with activity weights.
        self._benign: list[str] = []
        self._benign_index: dict[str, int] = {}
        self._cum_weights: list[float] | None = None

    # ─── Users ────────────────────────────────────────────────────────

    def new_user_id(self) -> str:
        return f"u{next(self._user_counter):06d}"

    def new_txn_id(self) -> str:
        return f"t{next(self._txn_counter):09d}"

    def add_user(self, now_s: float, benign: bool) -> User:
        # Pareto-distributed activity: a few users do most of the transacting.
        activity = min(self._rng.paretovariate(self._alpha), 200.0)
        user = User(user_id=self.new_user_id(), joined_s=now_s, activity=activity)
        self.users[user.user_id] = user
        if benign:
            self._benign_index[user.user_id] = len(self._benign)
            self._benign.append(user.user_id)
            self._cum_weights = None
        return user

    def remove_user(self, user_id: str) -> None:
        user = self.users[user_id]
        user.active = False
        idx = self._benign_index.pop(user_id, None)
        if idx is not None:
            last = self._benign.pop()
            if last != user_id:
                self._benign[idx] = last
                self._benign_index[last] = idx
            self._cum_weights = None

    @property
    def benign_count(self) -> int:
        return len(self._benign)

    def benign_users(self) -> list[str]:
        return list(self._benign)

    def sample_benign(self, exclude: str | None = None) -> str | None:
        """Activity-weighted benign user (heavy-tailed), or None if there are none."""
        if not self._benign or (len(self._benign) == 1 and self._benign[0] == exclude):
            return None
        if self._cum_weights is None:
            self._cum_weights = list(
                itertools.accumulate(self.users[u].activity for u in self._benign)
            )
        total = self._cum_weights[-1]
        for _ in range(16):
            r = self._rng.random() * total
            uid = self._benign[bisect.bisect_right(self._cum_weights, r)]
            if uid != exclude:
                return uid
        return next(u for u in self._benign if u != exclude)

    def sample_benign_uniform(self, k: int, exclude: set[str] | None = None) -> list[str]:
        pool = [u for u in self._benign if not exclude or u not in exclude]
        return self._rng.sample(pool, min(k, len(pool)))

    # ─── Links ────────────────────────────────────────────────────────

    def link(self, user_id: str, kind: str, attr_id: str) -> bool:
        """Record a user-attribute link. Returns False if it already existed."""
        users = self.pools[kind].users.setdefault(attr_id, set())
        existed = user_id in users
        users.add(user_id)
        self.users[user_id].attrs[kind].add(attr_id)
        return not existed

    def unlink(self, user_id: str, kind: str, attr_id: str) -> bool:
        users = self.pools[kind].users.get(attr_id)
        if not users or user_id not in users:
            return False
        users.discard(user_id)
        self.users[user_id].attrs[kind].discard(attr_id)
        return True

    def highest_degree(self, kind: str) -> str | None:
        pool = self.pools[kind].users
        if not pool:
            return None
        # Sort for determinism: dict order is insertion order, ties broken by id.
        return max(sorted(pool), key=lambda a: len(pool[a]))
