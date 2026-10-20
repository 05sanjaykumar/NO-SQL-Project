"""
Milestone 4: features, calibrated scorer, explanations, early warning, and the
analyst feedback loop.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from tests.test_detectors import G, ids

from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
from fraudsim.detectors.runner import run_detection
from fraudsim.graph.store import InMemoryGraphStore
from fraudsim.scoring.early_warning import lead_times
from fraudsim.scoring.explanations import explain
from fraudsim.scoring.features import FEATURE_NAMES, RingContext, extract_features
from fraudsim.scoring.scorer import RingScorer, calibration_bucket
from fraudsim.scoring.stage import AlertPolicy, ScoringStage
from fraudsim.scoring.train import parse_seeds, train_scorer

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def ctx_for(members: set[str], **kw: object) -> RingContext:
    defaults = dict(
        ring_id="R1",
        members=members,
        detectors={"hub_aware"},
        first_seen_t=T0.timestamp(),
        member_history=[(T0.timestamp(), len(members))],
        other_flagged_users=set(),
    )
    return RingContext(**(defaults | kw))  # type: ignore[arg-type]


# ─── Features ─────────────────────────────────────────────────────────


def test_features_of_a_hidden_ring_with_a_cycle() -> None:
    benign = ids("b", 26)
    ring = ids("r", 4)
    g = G().users(*benign, t=0).users(*ring, t=100)
    g.share(benign, "ip", "office", t=0).share(ring, "ip", "office", t=101)
    amounts = [5000, 4900, 4800, 4700]
    for i, (s, r) in enumerate(itertools.pairwise([*ring, ring[0]])):
        g.txn(s, r, amounts[i], t=110 + i)
    store = g.store()
    now = (T0 + timedelta(seconds=120)).timestamp()
    view = store.view(set(ring), now - 900, now)
    f = extract_features(ctx_for(set(ring)), view, store.joined(set(ring)))
    assert f.size == 4
    assert f.share_ip == 1.0 and f.share_device == 0.0
    assert f.hub_exposure == pytest.approx(np.log1p(30))
    assert f.cycle_present == 1.0 and f.cycle_length == 4
    assert f.account_age_similarity > 0.9  # all four accounts created at t=100
    assert f.temporal_burstiness > 0.9  # all linked to the hub within a second
    assert f.internal_txns_per_member == 1.0
    assert len(f.to_vector()) == len(FEATURE_NAMES)


def test_features_growth_and_other_risk_signals() -> None:
    m = ids("m", 4)
    g = G().users(*m).share(m, "card", "c1")
    store = g.store()
    now = (T0 + timedelta(seconds=120)).timestamp()
    ctx = ctx_for(set(m), member_history=[(T0.timestamp(), 2)], other_flagged_users={"m01"})
    f = extract_features(ctx, store.view(set(m), 0, now), store.joined(set(m)))
    assert f.growth_rate == pytest.approx(1.0)  # 2 members added over 2 minutes
    assert f.risk_signal_fraction == 0.25


# ─── Scorer ───────────────────────────────────────────────────────────


def synthetic(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, len(FEATURE_NAMES)))
    logits = (
        2.0 * x[:, FEATURE_NAMES.index("internal_txns_per_member")]
        - 1.5 * x[:, FEATURE_NAMES.index("hub_exposure")]
    )
    y = (rng.random(n) < 1 / (1 + np.exp(-logits))).astype(int)
    return x, y


@pytest.mark.parametrize("model_type", ["logistic_regression", "gradient_boosting"])
def test_scorer_learns_calibrates_and_round_trips(model_type: str, tmp_path: Path) -> None:
    x_fit, y_fit = synthetic(1500, 1)
    x_cal, y_cal = synthetic(800, 2)
    x_test, y_test = synthetic(800, 3)
    scorer = RingScorer(model_type).fit(x_fit, y_fit, x_cal, y_cal)
    metrics = scorer.evaluate(x_test, y_test)
    assert metrics["roc_auc"] > 0.8
    assert metrics["ece"] < 0.08
    p = scorer.predict(x_test)
    assert ((p >= 0) & (p <= 1)).all()

    # Averaged over rings, the two informative features dominate the contributions.
    mean_abs = {k: 0.0 for k in FEATURE_NAMES}
    for row in x_test[:100]:
        for k, v in scorer.contributions(row).items():
            mean_abs[k] += abs(v)
    top = sorted(mean_abs, key=mean_abs.get, reverse=True)[:2]  # type: ignore[arg-type]
    assert set(top) == {"internal_txns_per_member", "hub_exposure"}

    path = tmp_path / "m.joblib"
    scorer.meta.reliability = scorer.reliability_table(x_test, y_test)
    scorer.save(path)
    again = RingScorer.load(path)
    assert np.allclose(again.predict(x_test), p)
    assert path.with_suffix(".json").exists()


def test_lr_contributions_and_counterfactual_are_exact() -> None:
    x_fit, y_fit = synthetic(1500, 4)
    x_cal, y_cal = synthetic(800, 5)
    scorer = RingScorer().fit(x_fit, y_fit, x_cal, y_cal)
    x = np.zeros(len(FEATURE_NAMES))
    x[FEATURE_NAMES.index("internal_txns_per_member")] = 2.5
    # Contributions sum to the raw log-odds minus the intercept.
    lr = scorer.model.named_steps["lr"]
    raw = float(scorer.raw_proba(x)[0])
    assert sum(scorer.contributions(x).values()) + lr.intercept_[0] == pytest.approx(
        np.log(raw / (1 - raw)),
        abs=1e-4,  # contributions are rounded to 6 dp
    )
    risk = float(scorer.predict(x)[0])
    assert risk >= 0.5
    v = scorer.counterfactual_value(x, "internal_txns_per_member", 0.5)
    assert v is not None and v < 2.5
    x2 = x.copy()
    x2[FEATURE_NAMES.index("internal_txns_per_member")] = v
    assert float(scorer.predict(x2)[0]) < 0.5


def test_calibration_bucket_labels() -> None:
    assert calibration_bucket(0.0) == "0.0-0.1"
    assert calibration_bucket(0.87) == "0.8-0.9"
    assert calibration_bucket(1.0) == "0.9-1.0"


# ─── Explanations ─────────────────────────────────────────────────────


def test_explanation_reason_evidence_and_counterfactual() -> None:
    x_fit, y_fit = synthetic(1500, 6)
    x_cal, y_cal = synthetic(800, 7)
    scorer = RingScorer().fit(x_fit, y_fit, x_cal, y_cal)
    benign = ids("b", 26)
    ring = ids("r", 4)
    g = G().users(*benign, t=0).users(*ring, t=100)
    g.share(benign + ring, "ip", "office", t=101)
    t = 110.0
    for s, r in itertools.permutations(ring, 2):
        g.txn(s, r, 900.0, t)
        t += 1
    store = g.store()
    now = t
    view = store.view(set(ring), 0, (T0 + timedelta(seconds=now)).timestamp())
    joined = store.joined(set(ring))
    feats = extract_features(ctx_for(set(ring)), view, joined)
    evidence = {
        "detectors": ["hub_aware"],
        "by_detector": {"hub_aware": {"mean_edge_weight": 0.85}},
    }
    risk, exp = explain("R1", set(ring), feats, view, joined, evidence, scorer, 0.5)
    assert risk is not None and exp.score == pytest.approx(risk, abs=1e-6)
    assert "busy ip used by 30 accounts" in exp.reason_text
    assert "12 transfers between members" in exp.reason_text
    assert "4 accounts created within" in exp.reason_text
    users = [n for n in exp.evidence_subgraph["nodes"] if n["type"] == "user"]
    assert {n["id"] for n in users} == set(ring)
    assert sum(e["type"] == "transfer" for e in exp.evidence_subgraph["edges"]) == 12
    assert "hub-aware rule" in exp.counterfactual
    assert exp.calibration_bucket.startswith(calibration_bucket(risk))
    assert list(exp.feature_contributions)  # ranked contributions present


def test_explanation_without_model_still_gives_reason() -> None:
    g = G().users("a", "b", "c").share(["a", "b", "c"], "device", "d1")
    store = g.store()
    view = store.view({"a", "b", "c"}, 0, T0.timestamp() + 10)
    feats = extract_features(ctx_for({"a", "b", "c"}), view, store.joined({"a", "b", "c"}))
    risk, exp = explain("R1", {"a", "b", "c"}, feats, view, {}, {}, None, 0.5)
    assert risk is None
    assert "3 users share device d1" in exp.reason_text


# ─── Policy / feedback ────────────────────────────────────────────────


def test_alert_policy_adapts_and_logs() -> None:
    pol = AlertPolicy(default_threshold=0.5, learning_rate=0.5, margin=0.02)
    e = pol.feedback("R1", "shared_ip", 0.7, "dismiss")
    assert pol.threshold("shared_ip") == pytest.approx(0.5 + 0.5 * (0.72 - 0.5))
    assert e["threshold_before"] == 0.5 and e["action"] == "dismiss"
    pol.feedback("R2", "shared_card", 0.45, "confirm")
    assert pol.threshold("shared_card") < 0.5
    pol.feedback("R3", "shared_card", 0.95, "confirm")  # well above: no change
    assert len(pol.log) == 3
    assert pol.threshold("money_cycle") == 0.5
    with pytest.raises(ValueError):
        pol.feedback("R4", "x", 0.5, "maybe")


# ─── End to end ───────────────────────────────────────────────────────


def test_training_protocol_and_scored_closed_loop(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="disjoint"):
        train_scorer("logistic_regression", [1, 2], [2, 3], ("hub_heavy",))
    assert parse_seeds("1-3,7") == [1, 2, 3, 7]

    scorer, metrics = train_scorer(
        "logistic_regression",
        [11, 12, 13],
        [21],
        ("hub_heavy", "adversarial"),
        out_model=tmp_path / "lr.joblib",
        out_runs=tmp_path / "training",
        log=lambda *_: None,
    )
    assert metrics["test"]["n"] > 0 and "roc_auc" in metrics["test"]
    meta = scorer.meta.train
    assert meta["train_seeds"] == [11, 12, 13] and meta["test_seeds"] == [21]
    assert (tmp_path / "training" / meta["training_id"] / "train_rows.csv").exists()

    stage = ScoringStage(scorer=scorer)
    result = run_detection("adversarial", 31, None, scoring=stage)
    rings = list(result.engine.tracker.rings.values())
    scored = [r for r in rings if r.risk is not None]
    assert scored and all(0.0 <= r.risk <= 1.0 for r in scored)
    for r in scored:
        assert r.explanation and r.explanation["reason_text"]
        assert r.explanation["feature_contributions"]
    alerted = [r for r in rings if r.alerted_at is not None]
    assert all(r.status.value in ("confirmed", "dissolved") for r in alerted)
    leads = lead_times(result.generator.ledger, result.engine.tracker)
    assert len(leads) == len(result.generator.ledger.rings)
    assert any(x["lead_time_s"] is not None for x in leads)


def test_engine_scoring_stage_records_rows() -> None:
    events = G().users(*ids("r", 4)).share(ids("r", 4), "device", "dx").events
    store = InMemoryGraphStore()
    stage = ScoringStage(record=True)
    engine = DetectionEngine(
        store,
        build_detectors(store, {"community": {"enabled": False}}),
        config=EngineConfig(batch_size=4),
        scorer=stage,
    )
    engine.run(events)
    assert stage.rows and stage.rows[0]["features"]
    ring = next(iter(engine.tracker.rings.values()))
    assert ring.features["size"] == 4 and ring.risk is None
