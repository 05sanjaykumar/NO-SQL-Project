"""
Typed simulator configuration parsed from scenario YAML.

The YAML files in configs/scenarios/ are the single source of truth. This
module turns the raw dict into validated dataclasses so that the rest of the
simulator never has to guess at key names or defaults.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from fraudsim.types import EvasionMode, RingType

# Attribute kinds a ring or hub can be anchored on.
ATTRIBUTE_KINDS = ("device", "card", "ip")

# Ring types anchored on a single shared attribute, and which kind it is.
SHARED_ATTRIBUTE_KIND: dict[RingType, str] = {
    RingType.SHARED_DEVICE: "device",
    RingType.SHARED_CARD: "card",
    RingType.SHARED_IP: "ip",
}

# Benign hub categories and the attribute kind each one lives on.
HUB_KINDS: dict[str, str] = {
    "office": "ip",
    "public_wifi": "ip",
    "carrier_nat": "ip",
    "family_device": "device",
    "kiosk": "device",
    "corporate_card": "card",
}

DEFAULT_START_TS = datetime(2026, 1, 1, tzinfo=UTC)

DEFAULT_BENIGN_MIX: dict[str, float] = {
    "transaction": 0.85,
    "ip_login": 0.08,
    "device_link": 0.04,
    "card_link": 0.03,
}


@dataclass(frozen=True, slots=True)
class HubSpec:
    """A benign shared-infrastructure node with a fixed target size."""

    name: str
    kind: str  # device | card | ip
    size: int
    category: str = "office"
    # Seconds of sim time over which the hub fills up. 0 = present at start.
    fill_time_s: float = 0.0
    start_time_s: float = 0.0


@dataclass(frozen=True, slots=True)
class HubDistribution:
    """Randomly generated hubs whose sizes follow a heavy-tailed distribution."""

    count: int = 0
    min_size: int = 3
    max_size: int = 40
    alpha: float = 1.5  # Pareto shape for hub sizes
    categories: tuple[str, ...] = ("office", "public_wifi", "carrier_nat", "family_device")


@dataclass(frozen=True, slots=True)
class DriftSpec:
    """Mid-run change in benign behaviour."""

    trigger_time_s: float
    new_hub_size: int = 0
    new_hub_kind: str = "ip"
    shift_multiplier: float = 1.0  # multiplies benign event rate and amount scale


@dataclass(frozen=True, slots=True)
class RingSpec:
    """One fraud ring, either scheduled explicitly or drawn at random."""

    ring_id: str
    ring_type: RingType
    size: int
    spawn_time_s: float
    evasion: EvasionMode = EvasionMode.NONE
    shared_attribute: str | None = None
    length: int | None = None  # cycle length for money_cycle
    # Seconds between successive members joining during GROW.
    grow_interval_s: float = 2.0
    # Number of members present at SPAWN (the rest join during GROW).
    initial_members: int = 2
    # Seconds the ring stays ACTIVE before DISSOLVE. None = until run end.
    active_duration_s: float | None = None
    # Mean seconds between fraudulent actions while ACTIVE.
    action_interval_s: float = 3.0
    # Probability that each action is followed by a MUTATE.
    mutate_prob: float = 0.05
    intensity: float = 1.0  # amount multiplier


@dataclass(frozen=True, slots=True)
class RandomRingSpec:
    """Poisson process that spawns rings at random times."""

    rate_per_min: float = 0.0
    types: tuple[RingType, ...] = tuple(RingType)
    size_min: int = 3
    size_max: int = 6
    evasion_weights: dict[EvasionMode, float] = field(
        default_factory=lambda: {EvasionMode.NONE: 1.0}
    )
    active_duration_s: tuple[float, float] = (20.0, 60.0)


@dataclass(frozen=True, slots=True)
class AdaptiveSpec:
    """Behaviour of the adaptive attacker."""

    # Multiplicative penalty applied to a strategy's weight when one of its rings is flagged.
    penalty: float = 0.5
    # Seconds between the adaptive attacker launching replacement rings.
    respawn_delay_s: float = 5.0
    max_rings: int = 6
    strategies: tuple[EvasionMode, ...] = (
        EvasionMode.HUB_HIDING,
        EvasionMode.LOW_AND_SLOW,
        EvasionMode.IDENTITY_ROTATION,
        EvasionMode.DECOY_EDGES,
    )


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Complete simulator configuration for one scenario."""

    name: str
    duration_seconds: float
    target_event_rate: float  # benign events per simulated second
    num_users: int
    num_devices: int
    num_cards: int
    num_ips: int
    heavy_tail_alpha: float = 2.5
    # Mix of benign event kinds (normalised internally).
    benign_mix: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BENIGN_MIX))
    user_join_rate: float = 0.0  # new benign users per simulated second
    user_leave_rate: float = 0.0  # departing benign users per simulated second
    amount_median: float = 120.0
    amount_sigma: float = 1.1
    hubs: tuple[HubSpec, ...] = ()
    hub_distribution: HubDistribution = field(default_factory=HubDistribution)
    drift: DriftSpec | None = None
    rings: tuple[RingSpec, ...] = ()
    random_rings: RandomRingSpec = field(default_factory=RandomRingSpec)
    adaptive: AdaptiveSpec = field(default_factory=AdaptiveSpec)
    start_ts: datetime = DEFAULT_START_TS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Parsing ──────────────────────────────────────────────────────────


def _as_float(value: Any, key: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"'{key}' must be a number, got {value!r}") from e


def _parse_hubs(raw: dict[str, Any] | None) -> tuple[HubSpec, ...]:
    hubs: list[HubSpec] = []
    for name, spec in (raw or {}).items():
        kind = spec.get("type", "ip")
        if kind not in ATTRIBUTE_KINDS:
            raise ValueError(f"hub '{name}' has unknown type {kind!r}")
        size = int(spec["size"])
        if size < 2:
            raise ValueError(f"hub '{name}' size must be >= 2")
        hubs.append(
            HubSpec(
                name=name,
                kind=kind,
                size=size,
                category=spec.get("category", _guess_category(name, kind)),
                fill_time_s=_as_float(spec.get("fill_time_s", 0.0), "fill_time_s"),
                start_time_s=_as_float(spec.get("start_time_s", 0.0), "start_time_s"),
            )
        )
    return tuple(hubs)


def _guess_category(name: str, kind: str) -> str:
    lowered = name.lower()
    for category in HUB_KINDS:
        if category in lowered:
            return category
    if "nat" in lowered or "vpn" in lowered or "proxy" in lowered:
        return "carrier_nat"
    if "wifi" in lowered:
        return "public_wifi"
    return {"ip": "office", "device": "kiosk", "card": "corporate_card"}[kind]


def _parse_ring(raw: dict[str, Any], index: int) -> RingSpec:
    try:
        ring_type = RingType(raw["type"])
    except (KeyError, ValueError) as e:
        raise ValueError(f"ring #{index} has missing/unknown type: {raw.get('type')!r}") from e
    size = int(raw.get("size", 3))
    if size < 2:
        raise ValueError(f"ring #{index} size must be >= 2")
    length = raw.get("length")
    if ring_type == RingType.MONEY_CYCLE:
        length = int(length or size)
        if not 3 <= length <= 6:
            raise ValueError(f"ring #{index}: money_cycle length must be 3..6, got {length}")
        size = max(size, length)
    evasion = EvasionMode(raw.get("evasion", "none"))
    initial = int(raw.get("initial_members", min(2, size)))
    return RingSpec(
        ring_id=str(raw.get("id", f"ring_{index:03d}")),
        ring_type=ring_type,
        size=size,
        spawn_time_s=_as_float(raw.get("spawn_time_s", 0.0), "spawn_time_s"),
        evasion=evasion,
        shared_attribute=raw.get("shared_attribute"),
        length=int(length) if length is not None else None,
        grow_interval_s=_as_float(raw.get("grow_interval_s", 2.0), "grow_interval_s"),
        initial_members=max(1, min(initial, size)),
        active_duration_s=(
            _as_float(raw["active_duration_s"], "active_duration_s")
            if raw.get("active_duration_s") is not None
            else None
        ),
        action_interval_s=_as_float(raw.get("action_interval_s", 3.0), "action_interval_s"),
        mutate_prob=_as_float(raw.get("mutate_prob", 0.05), "mutate_prob"),
        intensity=_as_float(raw.get("intensity", 1.0), "intensity"),
    )


def _parse_random_rings(raw: dict[str, Any] | None) -> RandomRingSpec:
    if not raw:
        return RandomRingSpec()
    types = tuple(RingType(t) for t in raw.get("types", [t.value for t in RingType]))
    weights = {
        EvasionMode(k): float(v) for k, v in (raw.get("evasion_weights") or {"none": 1.0}).items()
    }
    dur = raw.get("active_duration_s", [20.0, 60.0])
    return RandomRingSpec(
        rate_per_min=float(raw.get("rate_per_min", 0.0)),
        types=types,
        size_min=int(raw.get("size_min", 3)),
        size_max=int(raw.get("size_max", 6)),
        evasion_weights=weights,
        active_duration_s=(float(dur[0]), float(dur[1])),
    )


def _parse_adaptive(raw: dict[str, Any] | None) -> AdaptiveSpec:
    if not raw:
        return AdaptiveSpec()
    default = AdaptiveSpec()
    strategies = raw.get("strategies")
    return AdaptiveSpec(
        penalty=float(raw.get("penalty", default.penalty)),
        respawn_delay_s=float(raw.get("respawn_delay_s", default.respawn_delay_s)),
        max_rings=int(raw.get("max_rings", default.max_rings)),
        strategies=(
            tuple(EvasionMode(s) for s in strategies) if strategies else default.strategies
        ),
    )


def parse_simulation_config(scenario: dict[str, Any]) -> SimulationConfig:
    """Validate a raw scenario dict (as loaded from YAML) into a SimulationConfig."""
    sim = scenario.get("simulation") or {}
    required = ("duration_seconds", "target_event_rate", "num_users")
    missing = [k for k in required if k not in sim]
    if missing:
        raise ValueError(f"scenario '{scenario.get('name')}' simulation block missing {missing}")

    hub_dist_raw = sim.get("hub_size_distribution") or {}
    hub_dist = HubDistribution(
        count=int(hub_dist_raw.get("count", 0)),
        min_size=int(hub_dist_raw.get("min_size", 3)),
        max_size=int(hub_dist_raw.get("max_size", 40)),
        alpha=float(hub_dist_raw.get("alpha", 1.5)),
        categories=tuple(hub_dist_raw.get("categories", HubDistribution().categories)),
    )
    for cat in hub_dist.categories:
        if cat not in HUB_KINDS:
            raise ValueError(f"unknown hub category {cat!r}; expected one of {list(HUB_KINDS)}")

    drift_raw = sim.get("drift")
    drift = (
        DriftSpec(
            trigger_time_s=float(drift_raw["trigger_time_s"]),
            new_hub_size=int(drift_raw.get("new_hub_size", 0)),
            new_hub_kind=drift_raw.get("new_hub_kind", "ip"),
            shift_multiplier=float(drift_raw.get("shift_multiplier", 1.0)),
        )
        if drift_raw
        else None
    )

    rings = tuple(_parse_ring(r, i) for i, r in enumerate(scenario.get("rings") or []))
    ids = [r.ring_id for r in rings]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate ring ids in scenario '{scenario.get('name')}'")

    benign_mix = sim.get("benign_mix") or DEFAULT_BENIGN_MIX

    start_raw = sim.get("start_ts")
    start_ts = (
        datetime.fromisoformat(str(start_raw)).astimezone(UTC) if start_raw else DEFAULT_START_TS
    )

    cfg = SimulationConfig(
        name=str(scenario.get("name", "unnamed")),
        duration_seconds=float(sim["duration_seconds"]),
        target_event_rate=float(sim["target_event_rate"]),
        num_users=int(sim["num_users"]),
        num_devices=int(sim.get("num_devices", max(1, sim["num_users"] // 2))),
        num_cards=int(sim.get("num_cards", max(1, sim["num_users"] // 2))),
        num_ips=int(sim.get("num_ips", max(1, sim["num_users"] // 2))),
        heavy_tail_alpha=float(sim.get("heavy_tail_alpha", 2.5)),
        benign_mix={str(k): float(v) for k, v in benign_mix.items()},
        user_join_rate=float(sim.get("user_join_rate", 0.0)),
        user_leave_rate=float(sim.get("user_leave_rate", 0.0)),
        amount_median=float(sim.get("amount_median", 120.0)),
        amount_sigma=float(sim.get("amount_sigma", 1.1)),
        hubs=_parse_hubs(sim.get("hub_distribution")),
        hub_distribution=hub_dist,
        drift=drift,
        rings=rings,
        random_rings=_parse_random_rings(scenario.get("random_rings")),
        adaptive=_parse_adaptive(scenario.get("adaptive_attacker")),
        start_ts=start_ts,
    )
    if cfg.target_event_rate <= 0:
        raise ValueError("target_event_rate must be > 0")
    if cfg.heavy_tail_alpha <= 1.0:
        raise ValueError("heavy_tail_alpha must be > 1 (finite mean)")
    if any(v < 0 for v in cfg.benign_mix.values()) or sum(cfg.benign_mix.values()) <= 0:
        raise ValueError("benign_mix weights must be non-negative and not all zero")
    unknown_mix = set(cfg.benign_mix) - {"transaction", "ip_login", "device_link", "card_link"}
    if unknown_mix:
        raise ValueError(f"unknown benign_mix keys: {sorted(unknown_mix)}")
    return cfg
