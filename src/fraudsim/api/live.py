"""
Live simulation run behind the API: generator -> StreamPlayer -> bus -> consumer.

The consumer forwards each event to WebSocket clients, keeps a short tail for
the dashboard, and feeds micro-batches to the incremental DetectionEngine
(in-memory store). Ring changes are broadcast as RING_UPDATE messages, and
newly confirmed rings are reported back to the simulator's adaptive attacker.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
from fraudsim.detectors.ensemble import RingUpdate, TrackedRing
from fraudsim.graph.store import InMemoryGraphStore
from fraudsim.logging.audit import AuditTrail
from fraudsim.logging.logger import RunLogger
from fraudsim.logging.replay import detection_records
from fraudsim.scoring.scorer import RingScorer
from fraudsim.scoring.stage import ScoringStage
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.player import StreamPlayer
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.stream.event_bus import AsyncQueueBus, EventBus
from fraudsim.types import Event

RECENT_EVENTS = 200
# Process a partial batch when no event arrived for this long (wall clock).
BATCH_IDLE_S = 0.25
# Recompute and broadcast run metrics at most this often (wall clock).
METRICS_EVERY_S = 1.0
HUB_DEGREE = 10  # attributes this busy are drawn as hubs
HUB_SAMPLE = 25  # users drawn around each hub (the hub node shows its full degree)


class LiveRun:
    """One running scenario with pause/resume, speed and rate controls."""

    def __init__(
        self,
        scenario: str,
        seed: int,
        run_id: str,
        speed: float = 1.0,
        broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        bus: EventBus | None = None,
        engine_config: EngineConfig | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self.scenario = scenario
        self.seed = seed
        self.run_id = run_id
        self.generator = EventGenerator(seed=seed, run_id=run_id, config=load_scenario(scenario))
        self.bus = bus or AsyncQueueBus(maxsize=10_000)
        self.player = StreamPlayer(self.generator, self.bus, speed=speed)
        self._broadcast = broadcast
        self.events_consumed = 0
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT_EVENTS)
        self._player_task: asyncio.Task[Any] | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        store = InMemoryGraphStore()
        self.scoring = ScoringStage(scorer=load_default_scorer())
        self.engine = DetectionEngine(
            store,
            build_detectors(store),
            config=engine_config or EngineConfig(),
            scorer=self.scoring,
        )
        self._batch: list[Event] = []
        self._flagged: set[str] = set()
        self._last_metrics_wall = 0.0
        self._metrics_cache: Any = None
        self.config_log: list[dict[str, Any]] = []
        # Logs + audit trail (Milestone 6); same layout as `fraudsim detect` runs, so
        # `fraudsim replay` works on a live run too.
        self.log: RunLogger | None = None
        self.audit: AuditTrail | None = None
        self.log_dir = Path(log_dir) if log_dir else None
        if self.log_dir is not None:
            self.log = RunLogger(self.log_dir, run_id)
            self.audit = AuditTrail(self.log_dir, run_id)
            self._batches_file = open(self.log_dir / "batches.jsonl", "w", encoding="utf-8")  # noqa: SIM115
            self._write_manifest()

    def _write_manifest(self) -> None:
        assert self.log_dir is not None
        from fraudsim.detectors.runner import _model_info

        manifest = {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "seed": self.seed,
            "backend": "memory",
            "mode": "live",
            "engine": self.engine.cfg.to_dict(),
            "detector_overrides": {},
            "model": _model_info(self.scoring),
            "control_log": self.generator.control_log,
            "events": self.events_consumed,
            "stream_digest": self.generator.digest,
        }
        (self.log_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8"
        )

    def record_feedback(
        self,
        ring_id: str,
        action: str,
        adaptation: dict[str, Any] | None,
        analyst_id: str,
        note: str,
    ) -> None:
        """Audit an analyst decision and the threshold change it caused."""
        if self.audit is None:
            return
        self.audit.record(
            "ANALYST_CONFIRM" if action == "confirm" else "ANALYST_DISMISS",
            ring_id,
            {"note": note, "sim_ts": self.generator.ts().isoformat()},
            actor=analyst_id,
        )
        if adaptation is not None:
            self.audit.record("THRESHOLD_UPDATED", ring_id, adaptation, actor=analyst_id)

    async def start(self) -> None:
        subscription = await self.bus.subscribe()
        self._consumer_task = asyncio.create_task(self._consume(subscription))
        self.player.prepare()
        self._player_task = asyncio.create_task(self.player.run())

    async def _consume(self, subscription: Any) -> None:
        iterator = subscription.__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(iterator.__anext__(), timeout=BATCH_IDLE_S)
            except TimeoutError:
                await self._detect()
                continue
            except StopAsyncIteration:
                break
            await self._handle(event)
            if len(self._batch) >= self.engine.cfg.batch_size:
                await self._detect()
        await self._detect()

    async def _detect(self) -> None:
        if not self._batch:
            return
        batch, self._batch = self._batch, []
        report = self.engine.process(batch)
        if self.log is not None and self.audit is not None:
            for rec in detection_records(report, self.engine):
                self.log.detection(rec, event_id=batch[-1].event_id)
            for upd in report.updates:
                self.audit.record_update(upd)
            self._batches_file.write(
                json.dumps(
                    {
                        "batch": report.batch,
                        "first_seq": report.first_seq,
                        "last_seq": report.last_seq,
                        "timings_ms": report.timings_ms,
                    }
                )
                + "\n"
            )
            self._batches_file.flush()
        newly: set[str] = set()
        flag_change = "alert" if self.scoring.scorer is not None else "confirmed"
        for upd in report.updates:
            if upd.change == flag_change:
                newly |= set(upd.members)
            if self._broadcast is not None:
                await self._broadcast({"type": "RING_UPDATE", "update": upd.to_dict()})
        newly -= self._flagged
        if newly:
            self._flagged |= newly
            self.generator.notify_flagged(newly)
        if self._broadcast is not None:
            await self._broadcast(
                {
                    "type": "BATCH",
                    "batch": report.batch,
                    "n_events": report.n_events,
                    "region": report.region,
                    "timings_ms": report.timings_ms,
                    "detector_ms": report.detector_ms,
                    "sim_time_s": round(self.generator.now_s, 3),
                    "events_total": self.events_consumed,
                    "wall_throughput_eps": self.player.stats.achieved_rate,
                    "graph": self.engine.store.stats(),
                    "events": [e.to_dict() for e in batch[-15:]],
                }
            )
            now = time.monotonic()
            if now - self._last_metrics_wall >= METRICS_EVERY_S:
                self._last_metrics_wall = now
                await self._broadcast({"type": "METRICS", "summary": self.metrics_summary()})

    def metrics(self) -> Any:
        """Evaluate the run so far against the simulator's ground truth."""
        from fraudsim.detectors.runner import evaluate_detection

        self._metrics_cache = evaluate_detection(
            self.generator, self.engine, self.scoring.scorer is not None
        )
        return self._metrics_cache

    def metrics_summary(self) -> dict[str, Any]:
        if not self.engine.reports:
            return {}
        return self.metrics().summary

    # ─── Dashboard controls ───────────────────────────────────────────

    def inject(self, ring_type: str, evasion: str, size: int) -> str:
        return self.generator.inject_ring(ring_type, evasion, size)

    def detectors(self) -> list[dict[str, Any]]:
        out = []
        for d in self.engine.detectors:
            cfg = d.config
            out.append(
                {
                    "type": d.detector_type.value,
                    "name": d.name,
                    "enabled": d.enabled,
                    "incremental": d.incremental,
                    "params": {
                        k: getattr(cfg, k)
                        for k in (
                            "weight_method",
                            "edge_threshold",
                            "time_decay_half_life_s",
                            "max_degree_cap",
                            "algorithm",
                            "min_fan_count",
                        )
                        if hasattr(cfg, k)
                    },
                }
            )
        return out

    def set_detector(self, detector_type: str, enabled: bool) -> None:
        for d in self.engine.detectors:
            if d.detector_type.value == detector_type:
                d.config.enabled = enabled
                self._log_config({"detector": detector_type, "enabled": enabled})
                return
        raise KeyError(detector_type)

    def set_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Live tuning: alert threshold and hub-aware weighting (applied from the next batch)."""
        from fraudsim.detectors.hubaware import HUB_WEIGHTS

        applied: dict[str, Any] = {}
        hub = next((d for d in self.engine.detectors if d.detector_type.value == "hub_aware"), None)
        if "alert_threshold" in changes and changes["alert_threshold"] is not None:
            value = float(changes["alert_threshold"])
            if not 0.0 < value < 1.0:
                raise ValueError("alert_threshold must be in (0, 1)")
            self.scoring.policy.default_threshold = value
            self.scoring.policy.thresholds.clear()
            applied["alert_threshold"] = value
        if hub is not None:
            cfg = hub.config
            if changes.get("edge_threshold") is not None:
                value = float(changes["edge_threshold"])
                if not 0.0 < value <= 3.0:
                    raise ValueError("edge_threshold must be in (0, 3]")
                cfg.edge_threshold = value  # type: ignore[attr-defined]
                applied["edge_threshold"] = value
            if changes.get("hub_weight_method") is not None:
                method = str(changes["hub_weight_method"])
                if method not in HUB_WEIGHTS:
                    raise ValueError(f"hub_weight_method must be one of {HUB_WEIGHTS}")
                cfg.weight_method = method  # type: ignore[attr-defined]
                applied["hub_weight_method"] = method
        if applied:
            self._log_config(applied)
        return applied

    def _log_config(self, change: dict[str, Any]) -> None:
        entry = {"sim_ts": self.generator.ts().isoformat(), **change}
        self.config_log.append(entry)
        if self.audit is not None:
            self.audit.record("CONFIG_CHANGED", "-", entry, actor="dashboard")

    def current_config(self) -> dict[str, Any]:
        hub = next((d for d in self.engine.detectors if d.detector_type.value == "hub_aware"), None)
        return {
            "alert_threshold": self.scoring.policy.default_threshold,
            "thresholds_by_type": dict(self.scoring.policy.thresholds),
            "edge_threshold": getattr(hub.config, "edge_threshold", None) if hub else None,
            "hub_weight_method": getattr(hub.config, "weight_method", None) if hub else None,
            "model": self.scoring.scorer.model_type if self.scoring.scorer else None,
            "batch_size": self.engine.cfg.batch_size,
        }

    # ─── Graph snapshot (windowed, level of detail) ───────────────────

    def graph_snapshot(
        self, max_users: int = 400, window_s: float = 60.0, oracle: bool = False
    ) -> dict[str, Any]:
        store: InMemoryGraphStore = self.engine.store  # type: ignore[assignment]
        now = self.generator.ts().timestamp()
        live_rings = self.engine.tracker.live()
        ring_of: dict[str, list[str]] = {}
        for ring in live_rings:
            for u in ring.members:
                ring_of.setdefault(u, []).append(ring.ring_id)
        truth: dict[str, str] = {}
        for gt in self.generator.ledger.rings.values():
            for _, action, u in gt.member_history:
                if action == "join":
                    truth[u] = gt.ring_id

        last: dict[str, float] = {}
        for u, links in store.user_links.items():
            if store.active.get(u) and links:
                last[u] = max(links.values())
        for u, txns in store.out_txns.items():
            if txns and store.active.get(u):
                last[u] = max(last.get(u, 0.0), txns[-1].t)
                for t in txns[-3:]:
                    if store.active.get(t.receiver):
                        last[t.receiver] = max(last.get(t.receiver, 0.0), t.t)
        must = set(ring_of) | ({u for u in truth if store.active.get(u)} if oracle else set())
        recent = sorted(
            (u for u, t in last.items() if t >= now - window_s and u not in must),
            key=lambda u: (-last[u], u),
        )
        # Level of detail: everything suspicious (tracked ring members, and the answer
        # key's members in oracle view), a sample of each busy hub's users so hubs read
        # as hubs, then the most recently active benign users as background.
        users = set(sorted(must)[:max_users])
        for (_kind, _attr), holders in sorted(store.attr_users.items()):
            active = sorted(u for u in holders if store.active.get(u))
            if len(active) >= HUB_DEGREE:
                users |= set(active[:HUB_SAMPLE])
        # Background benign users are drawn only while the picture is still sparse.
        background = max(0, min(40 - len(users), max_users - len(users)))
        users |= set(recent[:background])
        total_active = sum(1 for a in store.active.values() if a)

        attrs: dict[tuple[str, str], list[str]] = {}
        for u in users:
            for attr in store.user_links.get(u, {}):
                attrs.setdefault(attr, []).append(u)
        nodes: list[dict[str, Any]] = []
        flagged_ring = {r.ring_id: r for r in live_rings}
        for u in sorted(users):
            rids = ring_of.get(u, [])
            best = max(
                (flagged_ring[r] for r in rids),
                key=lambda r: (
                    r.alerted_at is not None,
                    r.status.value == "confirmed",
                    r.risk or 0,
                ),
                default=None,
            )
            nodes.append(
                {
                    "id": u,
                    "kind": "user",
                    "rings": rids,
                    "status": (
                        "alert"
                        if best is not None and best.alerted_at is not None
                        else (best.status.value if best is not None else None)
                    ),
                    "risk": round(best.risk, 3) if best is not None and best.risk else None,
                    "truth": truth.get(u) if oracle else None,
                    "last_s": round(last.get(u, 0.0) - self.generator.ts(0.0).timestamp(), 2),
                }
            )
        edges: list[dict[str, Any]] = []
        for (kind, attr), us in sorted(attrs.items()):
            degree = sum(1 for x in store.attr_users.get((kind, attr), {}) if store.active.get(x))
            if len(us) < 2 and degree < HUB_DEGREE:
                continue  # personal attribute of a single drawn user: level of detail
            nodes.append({"id": attr, "kind": kind, "degree": degree, "hub": degree >= HUB_DEGREE})
            edges += [
                {"id": f"{u}|{attr}", "source": u, "target": attr, "kind": "link"} for u in us
            ]
        pair: dict[tuple[str, str], list[float]] = {}
        for u in users:
            for t in store.out_txns.get(u, ()):
                if t.receiver in users and t.t >= now - window_s:
                    pair.setdefault((u, t.receiver), []).append(t.amount)
        for (s, r), amounts in sorted(pair.items()):
            edges.append(
                {
                    "id": f"{s}>{r}",
                    "source": s,
                    "target": r,
                    "kind": "transfer",
                    "count": len(amounts),
                    "amount": round(sum(amounts), 2),
                }
            )
        return {
            "sim_time_s": round(self.generator.now_s, 3),
            "nodes": nodes,
            "edges": edges,
            "shown_users": len(users),
            "total_active_users": total_active,
            "window_s": window_s,
            "oracle": self.oracle() if oracle else None,
        }

    def oracle(self) -> dict[str, Any]:
        """Answer key: which tracked rings are true or false, and which true rings are missed."""
        from fraudsim.metrics.matching import jaccard

        m = self._metrics_cache if self._metrics_cache is not None else None
        if m is None and self.engine.reports:
            m = self.metrics()
        truth = {
            rid: {u for _, a, u in r.member_history if a == "join"}
            for rid, r in self.generator.ledger.rings.items()
        }
        tracked = {}
        for ring in self.engine.tracker.rings.values():
            best = max(
                ((jaccard(ring.members, ms), rid) for rid, ms in truth.items()), default=(0, None)
            )
            tracked[ring.ring_id] = {
                "verdict": "true" if best[0] >= 0.5 else "false",
                "true_ring": best[1] if best[0] >= 0.5 else None,
                "jaccard": round(best[0], 3),
            }
        rows = m.rings if m is not None else []
        return {
            "tracked": tracked,
            "true_rings": [
                {
                    **row,
                    "members": sorted(truth.get(row["true_ring"], [])),
                }
                for row in rows
            ],
            "spawned_not_active": sorted(
                rid for rid, r in self.generator.ledger.rings.items() if r.activate_ts is None
            ),
        }

    def rings(self) -> list[dict[str, Any]]:
        return [ring_payload(r) for r in self.engine.tracker.rings.values()]

    async def _handle(self, event: Event) -> None:
        self.events_consumed += 1
        self.recent.append(event.to_dict())
        self._batch.append(event)
        if self.log is not None:
            self.log.event(event)

    @property
    def status(self) -> str:
        return self.player.state

    def pause(self) -> None:
        self.player.pause()

    def resume(self) -> None:
        self.player.resume()

    def set_speed(self, speed: float) -> None:
        self.player.set_speed(speed)

    def set_rate(self, rate: float) -> None:
        self.player.set_rate(rate)

    async def stop(self) -> None:
        self.player.stop()
        for task in (self._player_task, self._consumer_task):
            if task is None or task.done():
                continue
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self.close_logs()

    def close_logs(self) -> None:
        if self.log is not None:
            self._write_manifest()
            self.log.close()
            self._batches_file.close()
            self.log = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "sim_time_s": round(self.generator.now_s, 3),
            "rate": self.generator.rate,
            # 0 means "as fast as possible" (JSON has no infinity).
            "speed_multiplier": 0.0 if math.isinf(self.player.speed) else self.player.speed,
            "events_emitted": self.events_consumed,
            "player": self.player.stats.to_dict(),
        }


DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "models" / "ring_scorer_lr.joblib"


def load_default_scorer() -> RingScorer | None:
    """The trained logistic-regression scorer if `fraudsim train-scorer` produced one."""
    try:
        return RingScorer.load(DEFAULT_MODEL) if DEFAULT_MODEL.exists() else None
    except Exception:
        return None


def ring_payload(ring: TrackedRing) -> dict[str, Any]:
    """TrackedRing in the shape of api.schemas.RingDetailResponse."""
    return {
        "ring_id": ring.ring_id,
        "status": ring.status.value,
        "ring_type": ring.ring_type.value,
        "score": ring.score,
        "members": sorted(ring.members),
        "features": ring.features,
        "explanation": {
            "detectors": sorted(ring.sources),
            "risk": ring.risk,
            "alerted_at": ring.alerted_at.isoformat() if ring.alerted_at else None,
            "early_warned_at": ring.early_warned_at.isoformat() if ring.early_warned_at else None,
            **(ring.explanation or {}),
            "evidence": ring.evidence,
            "history": ring.history,
        },
        "created_at": ring.first_seen.isoformat(),
        "updated_at": ring.last_seen.isoformat(),
    }


__all__ = ["LiveRun", "RingUpdate", "ring_payload"]
