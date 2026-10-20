"""Milestone 5: ring/user metrics, miss reasons, time-to-detect, triage, exports."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from fraudsim.detectors.ensemble import EnsembleConfig, RingTracker, RingUpdate
from fraudsim.detectors.runner import run_detection
from fraudsim.metrics.evaluation import EvalConfig, evaluate_run, percentiles
from fraudsim.metrics.exporters import prometheus_text, write_run_metrics
from fraudsim.simulator.ground_truth import GroundTruthLedger
from fraudsim.types import DetectorType, EvasionMode, RingCandidate, RingStatus, RingType

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def ledger() -> GroundTruthLedger:
    lg = GroundTruthLedger()
    for rid, members, ev in [
        ("A", {"a1", "a2", "a3", "a4"}, EvasionMode.NONE),
        ("B", {"b1", "b2", "b3"}, EvasionMode.HUB_HIDING),
        ("C", {"c1", "c2", "c3"}, EvasionMode.NONE),
        ("D", {"d1", "d2", "d3"}, EvasionMode.NONE),
    ]:
        lg.spawn_ring(rid, RingType.SHARED_DEVICE, members, at(0), evasion_mode=ev)
        lg.activate_ring(rid, at(10))
    lg.dissolve_ring("D", at(20))
    lg.spawn_ring("E", RingType.SHARED_CARD, {"e1", "e2"}, at(50))  # never activates
    return lg


def upd(t: float, rid: str, members: set[str], change: str = "confirmed") -> RingUpdate:
    return RingUpdate(
        at(t), rid, change, RingStatus.CONFIRMED, tuple(sorted(members)), ("hub_aware",), 0.9
    )


def report(batch: int, t: float, updates: list[RingUpdate]) -> SimpleNamespace:
    return SimpleNamespace(
        batch=batch,
        now=at(t),
        n_events=10,
        region=5,
        full_run=False,
        timings_ms={"ingest": 2.0, "dirty": 1.0, "view": 1.0, "track": 0.5, "total": 6.0},
        detector_ms={"HubAwareSharedAttribute": 1.5},
        updates=updates,
        store_queries=None,
    )


def tracker_with(rings: dict[str, tuple[set[str], RingStatus]]) -> RingTracker:
    tr = RingTracker(EnsembleConfig())
    for rid, (members, status) in rings.items():
        tr.update(
            [
                RingCandidate(
                    ring_id="x",
                    detector=DetectorType.HUB_AWARE,
                    ring_type=RingType.SHARED_DEVICE,
                    members=members,
                    first_seen=at(0),
                    last_seen=at(0),
                )
            ],
            {"hub_aware": None},
            at(0),
        )
        ring = tr.rings[f"R{len(tr.rings):06d}"]
        ring.status = status
        del rid
    return tr


def test_ring_user_metrics_reasons_and_timing() -> None:
    lg = ledger()
    tr = tracker_with(
        {
            "c": ({"c1", "c2", "c3"}, RingStatus.CANDIDATE)
        }  # tracked, never flagged -> below_threshold
    )
    reports = [
        report(1, 12.0, [upd(12.0, "R9", {"a1", "a2", "a3"})]),  # A, Jaccard 0.75, ttd 2 s
        report(2, 15.0, [upd(15.0, "R8", {"x1", "x2", "x3"})]),  # false ring
        report(3, 25.0, [upd(25.0, "R7", {"d1", "d2", "d3"})]),  # D, but D dissolved at 20: late
    ]
    m = evaluate_run(
        lg, tr, reports, T0.timestamp(), at(60).timestamp(), scored=False, hub_degree_of={"B": 40}
    )
    s = m.summary
    assert s["true_rings"] == 4  # E never activated
    assert s["rings_detected"] == 1 and s["false_positive_rings"] == 1
    assert s["ring"]["recall"] == 0.25
    assert s["ring"]["precision"] == pytest.approx(
        2 / 3, abs=1e-4
    )  # A and D flags match, x does not
    reasons = {r["true_ring"]: r["miss_reason"] for r in m.rings}
    assert reasons == {"A": None, "B": "hidden_behind_hub", "C": "below_threshold", "D": "late"}
    assert s["time_to_detect_s"]["p50"] == pytest.approx(2.0)
    assert s["user"]["flagged_users"] == 9 and s["user"]["tp"] == 6
    assert s["sensitivity"]["0.8"]["tp"] == 0  # A matched only at 0.75
    assert s["latency_ms"]["detect_per_batch"]["p50"] == pytest.approx(4.0)
    assert s["throughput"]["events"] == 30
    ts = m.timeseries
    assert ts[0]["t_s"] == 10.0 and ts[-1]["cum_detected"] == 1


def test_percentiles() -> None:
    p = percentiles([1, 2, 3, 4, 5])
    assert p["p50"] == 3 and p["p90"] == pytest.approx(4.6) and p["count"] == 5
    assert percentiles([])["p50"] is None


def test_prometheus_and_files(tmp_path: Path) -> None:
    text = prometheus_text(
        {"ring": {"recall": 0.5, "precision": None}, "n": 3, "ok": True}, labels='run_id="r"'
    )
    assert 'fraudsim_ring_recall{run_id="r"} 0.5' in text
    assert "precision" not in text and "fraudsim_ok" in text

    result = run_detection("baseline", 5, tmp_path, duration_s=40.0)
    mdir = tmp_path / result.manifest["run_id"] / "metrics"
    for name in ("rings", "flags", "timeseries", "batches"):
        assert (mdir / f"{name}.csv").exists() and (mdir / f"{name}.parquet").exists()
    summary = json.loads((mdir / "summary.json").read_text())
    assert summary["true_rings"] == result.metrics.summary["true_rings"]
    assert len(pd.read_parquet(mdir / "batches.parquet")) == len(result.engine.reports)
    assert any(b.get("graph_users") for b in result.metrics.batches)
    assert (mdir / "metrics.prom").read_text().startswith("# TYPE")
    write_run_metrics(result.metrics, tmp_path / "again")
    assert (tmp_path / "again" / "summary.json").exists()


def test_eval_config_controls_windows() -> None:
    lg = ledger()
    m = evaluate_run(
        lg,
        RingTracker(),
        [report(1, 12.0, [])],
        T0.timestamp(),
        at(40).timestamp(),
        scored=False,
        cfg=EvalConfig(step_s=20.0, window_s=20.0),
    )
    assert [r["t_s"] for r in m.timeseries] == [20.0, 40.0]
    assert m.summary["rings_detected"] == 0
    assert m.summary["missed_reasons"]["blind_spot"] == 3


def test_compare_modes_same_stream() -> None:
    from fraudsim.detectors.runner import compare_modes

    d = compare_modes("baseline", 3, duration_s=30.0)
    assert d["incremental"]["stream_digest"] == d["full"]["stream_digest"]
    assert d["incremental"]["region_users_p50"] <= d["full"]["region_users_p50"]
    assert d["speedup_p50"] is not None
