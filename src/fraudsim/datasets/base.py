"""
Common shape for public datasets and an account-level evaluation.

A loader turns a dataset into the simulator's observable event types
(user_join, transaction) in time order, plus labels that never reach the
engine: the set of illicit accounts and, where the dataset has them, labelled
groups (laundering attempts) used as ring ground truth.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fraudsim.types import Event, EventType

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class PublicDataset:
    name: str
    events: Iterable[Event]  # a list, or LazyEvents for large files
    illicit_accounts: set[str]
    accounts: set[str]
    groups: list[dict[str, Any]] = field(default_factory=list)  # {"id", "type", "members"}
    source: dict[str, Any] = field(default_factory=dict)  # files, sha256, rows read

    def summary(self) -> dict[str, Any]:
        return {
            "dataset": self.name,
            "events": len(self.events),  # type: ignore[arg-type]
            "accounts": len(self.accounts),
            "illicit_accounts": len(self.illicit_accounts),
            "groups": len(self.groups),
            **self.source,
        }


class EventBuilder:
    """Builds the observable stream; joins each account on first sight."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.events: list[Event] = []
        self.accounts: set[str] = set()

    def _emit(self, et: EventType, ts: datetime, payload: dict[str, Any]) -> None:
        seq = len(self.events)
        self.events.append(Event(f"{self.run_id}-{seq:09d}", et, ts, payload, self.run_id, seq))

    def account(self, acc: str, ts: datetime) -> None:
        if acc not in self.accounts:
            self.accounts.add(acc)
            self._emit(EventType.USER_JOIN, ts, {"user_id": acc, "name": acc})

    def transaction(
        self, txn_id: str, sender: str, receiver: str, amount: float, ts: datetime
    ) -> None:
        if sender == receiver:
            return
        self.account(sender, ts)
        self.account(receiver, ts)
        self._emit(
            EventType.TRANSACTION,
            ts,
            {"txn_id": txn_id, "sender": sender, "receiver": receiver, "amount": round(amount, 2)},
        )


class LazyEvents:
    """
    The observable stream of a large file, built on the fly from compact rows
    (offset_s, txn_id, sender, receiver, amount) sorted by time, so a multi-day
    slice of a 5M-row file never holds millions of Event objects at once.
    Produces exactly the events EventBuilder would.
    """

    def __init__(self, run_id: str, rows: list[tuple[float, str, str, str, float]]) -> None:
        self.run_id = run_id
        self.rows = rows
        seen: set[str] = set()
        n = 0
        for _, _, a, b, _ in rows:
            if a == b:
                continue
            for acc in (a, b):
                if acc not in seen:
                    seen.add(acc)
                    n += 1
            n += 1
        self.accounts = seen
        self._len = n

    def __len__(self) -> int:
        return self._len

    def __iter__(self) -> Iterator[Event]:
        seen: set[str] = set()
        seq = 0
        rid = self.run_id
        for offset, txn_id, a, b, amount in self.rows:
            if a == b:
                continue
            ts = sim_time(offset)
            for acc in (a, b):
                if acc not in seen:
                    seen.add(acc)
                    yield Event(
                        f"{rid}-{seq:09d}",
                        EventType.USER_JOIN,
                        ts,
                        {"user_id": acc, "name": acc},
                        rid,
                        seq,
                    )
                    seq += 1
            yield Event(
                f"{rid}-{seq:09d}",
                EventType.TRANSACTION,
                ts,
                {"txn_id": txn_id, "sender": a, "receiver": b, "amount": round(amount, 2)},
                rid,
                seq,
            )
            seq += 1


def sim_time(offset_s: float) -> datetime:
    return EPOCH + timedelta(seconds=offset_s)


def file_digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def evaluate_accounts(ds: PublicDataset, flagged_groups: list[set[str]]) -> dict[str, Any]:
    """Account-level precision/recall of the union of flagged groups, and group recall."""
    flagged = set().union(*flagged_groups) if flagged_groups else set()
    tp = len(flagged & ds.illicit_accounts)
    precision = tp / len(flagged) if flagged else None
    recall = tp / len(ds.illicit_accounts) if ds.illicit_accounts else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else (0.0 if precision is not None and recall is not None else None)
    )
    out: dict[str, Any] = {
        "accounts_flagged": len(flagged),
        "illicit_flagged": tp,
        "account_precision": precision,
        "account_recall": recall,
        "account_f1": f1,
        "groups_flagged": len(flagged_groups),
    }
    if ds.groups:
        # A labelled group counts as found when a flagged group covers half its members.
        hit = 0
        for g in ds.groups:
            members = set(g["members"])
            if any(len(members & fg) * 2 >= len(members) for fg in flagged_groups):
                hit += 1
        out["group_recall"] = hit / len(ds.groups)
        by_type: dict[str, list[int]] = {}
        for g in ds.groups:
            members = set(g["members"])
            found = any(len(members & fg) * 2 >= len(members) for fg in flagged_groups)
            acc = by_type.setdefault(g["type"], [0, 0])
            acc[0] += int(found)
            acc[1] += 1
        out["group_recall_by_type"] = {k: v[0] / v[1] for k, v in sorted(by_type.items())}
    return out


def run_public(
    ds: PublicDataset,
    detector_overrides: dict[str, dict[str, Any]] | None = None,
    batch_size: int = 200,
    window_s: float = 900.0,
) -> dict[str, Any]:
    """Replay the dataset through the engine (in-memory store) and score it."""
    from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
    from fraudsim.graph.store import InMemoryGraphStore

    overrides = {
        "static_cypher": {"enabled": False},
        "community": {"enabled": False},
        # No shared attributes in these datasets.
        "hub_aware": {"enabled": False},
        # The transaction detectors look back over the same window as the views.
        "temporal_cycle": {"time_window_s": window_s},
        "smurfing": {"time_window_s": window_s},
    } | (detector_overrides or {})
    store = InMemoryGraphStore()
    engine = DetectionEngine(
        store,
        build_detectors(store, overrides),
        # Links and transfers older than the window are pruned, which keeps memory bounded.
        config=EngineConfig(batch_size=batch_size, window_s=window_s, ttl_s=window_s),
    )
    t0 = time.perf_counter()
    engine.run(ds.events)
    wall = time.perf_counter() - t0
    # Every ring that was ever confirmed (hysteresis passed), with its last membership.
    groups = [set(r.members) for r in engine.tracker.rings.values() if r.confirmed_at is not None]
    det = sorted(
        r.timings_ms.get("dirty", 0) + r.timings_ms["view"] + sum(r.detector_ms.values())
        for r in engine.reports
    )
    return {
        **ds.summary(),
        **evaluate_accounts(ds, groups),
        "batches": len(engine.reports),
        "detect_ms_p50": det[len(det) // 2] if det else None,
        "wall_s": round(wall, 2),
        "window_s": window_s,
    }
