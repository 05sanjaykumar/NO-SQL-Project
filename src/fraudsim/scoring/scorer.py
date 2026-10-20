"""
Calibrated ring risk model (Milestone 4, stage two of the two-stage design).

Stage one (the detectors and the ensemble) proposes candidate rings. Stage
two turns each ring's feature vector into a calibrated risk score in [0, 1].

- `logistic_regression` (default, interpretable): StandardScaler +
  LogisticRegression. A feature's contribution to the log-odds is
  coef_i * (x_i - mean_i) / scale_i, so explanations are exact.
- `gradient_boosting` (comparison): HistGradientBoostingClassifier. Its
  contributions are computed by occlusion (the change in log-odds when one
  feature is replaced by its training mean), which is an approximation.

Calibration: an isotonic regression is fitted on the model's raw
probabilities over a later time slice of the training runs than the one the
model was fitted on (time-based split). Evaluation always uses different
seeds than training.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from fraudsim.scoring.features import FEATURE_NAMES

MODEL_TYPES = ("logistic_regression", "gradient_boosting")
BUCKETS = [i / 10 for i in range(11)]


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def calibration_bucket(p: float) -> str:
    i = min(int(p * 10), 9)
    return f"{i / 10:.1f}-{(i + 1) / 10:.1f}"


@dataclass(slots=True)
class ScorerMetadata:
    model_type: str
    feature_names: list[str]
    train: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    reliability: list[dict[str, Any]] = field(default_factory=list)  # per calibration bucket


class RingScorer:
    """Calibrated risk model over RingFeatures vectors."""

    def __init__(self, model_type: str = "logistic_regression", seed: int = 0) -> None:
        if model_type not in MODEL_TYPES:
            raise ValueError(f"model_type must be one of {MODEL_TYPES}")
        self.model_type = model_type
        self.seed = seed
        self.model: Any = None
        self.calibrator: IsotonicRegression | None = None
        self.means: np.ndarray | None = None
        self.meta = ScorerMetadata(model_type, list(FEATURE_NAMES))

    @property
    def fitted(self) -> bool:
        return self.model is not None

    # ─── Training ─────────────────────────────────────────────────────

    def fit(
        self,
        x_fit: np.ndarray,
        y_fit: np.ndarray,
        x_cal: np.ndarray,
        y_cal: np.ndarray,
    ) -> RingScorer:
        """Fit on one time slice, calibrate on a later one."""
        if len(set(y_fit.tolist())) < 2:
            raise ValueError("training slice needs both positive and negative rings")
        if self.model_type == "logistic_regression":
            self.model = Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("lr", LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced")),
                ]
            )
        else:
            self.model = HistGradientBoostingClassifier(
                max_iter=200, learning_rate=0.08, random_state=self.seed, class_weight="balanced"
            )
        self.model.fit(x_fit, y_fit)
        self.means = x_fit.mean(axis=0)
        raw = self.model.predict_proba(x_cal)[:, 1]
        self.calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        self.calibrator.fit(raw, y_cal)
        return self

    # ─── Inference ────────────────────────────────────────────────────

    def raw_proba(self, x: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(np.atleast_2d(x))[:, 1]

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Calibrated risk scores in [0, 1]."""
        raw = self.raw_proba(x)
        assert self.calibrator is not None
        return np.clip(self.calibrator.predict(raw), 0.0, 1.0)

    def contributions(self, x: np.ndarray) -> dict[str, float]:
        """Per-feature contribution to the raw log-odds for one ring, ranked by |value|."""
        x = np.asarray(x, dtype=float)
        if self.model_type == "logistic_regression":
            scaler: StandardScaler = self.model.named_steps["scale"]
            lr: LogisticRegression = self.model.named_steps["lr"]
            z = (x - scaler.mean_) / np.where(scaler.scale_ == 0, 1.0, scaler.scale_)
            contrib = lr.coef_[0] * z
        else:
            base = _logit(float(self.raw_proba(x)[0]))
            assert self.means is not None
            contrib = np.zeros(len(x))
            for i in range(len(x)):
                occluded = x.copy()
                occluded[i] = self.means[i]
                contrib[i] = base - _logit(float(self.raw_proba(occluded)[0]))
        pairs = sorted(
            zip(FEATURE_NAMES, contrib.tolist(), strict=True), key=lambda kv: -abs(kv[1])
        )
        return {k: round(v, 6) for k, v in pairs}

    def raw_threshold_for(self, calibrated_threshold: float) -> float | None:
        """Smallest raw probability whose calibrated score reaches the threshold."""
        assert self.calibrator is not None
        xs = self.calibrator.X_thresholds_
        ys = self.calibrator.y_thresholds_
        above = [x for x, y in zip(xs, ys, strict=True) if y >= calibrated_threshold]
        return float(min(above)) if above else None

    def counterfactual_value(self, x: np.ndarray, feature: str, threshold: float) -> float | None:
        """
        Value of `feature` (others fixed) at which the calibrated score would drop
        below `threshold`. Exact for logistic regression, None if no such value.
        """
        if self.model_type != "logistic_regression":
            return None
        raw_t = self.raw_threshold_for(threshold)
        if raw_t is None:
            return None
        i = FEATURE_NAMES.index(feature)
        scaler: StandardScaler = self.model.named_steps["scale"]
        lr: LogisticRegression = self.model.named_steps["lr"]
        coef = float(lr.coef_[0][i]) / (float(scaler.scale_[i]) or 1.0)
        if abs(coef) < 1e-9:
            return None
        current = _logit(float(self.raw_proba(x)[0]))
        target = _logit(raw_t) - 1e-3
        return float(x[i] + (target - current) / coef)

    # ─── Evaluation ───────────────────────────────────────────────────

    def evaluate(self, x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
        p = self.predict(x)
        out: dict[str, Any] = {
            "n": len(y),
            "positives": int(y.sum()),
            "brier": round(float(brier_score_loss(y, p)), 6),
        }
        if len(set(y.tolist())) == 2:
            out["roc_auc"] = round(float(roc_auc_score(y, p)), 6)
            out["pr_auc"] = round(float(average_precision_score(y, p)), 6)
        out["ece"] = round(_ece(p, y), 6)
        return out

    def reliability_table(self, x: np.ndarray, y: np.ndarray) -> list[dict[str, Any]]:
        p = self.predict(x)
        rows = []
        for lo, hi in itertools.pairwise(BUCKETS):
            mask = (p >= lo) & ((p < hi) if hi < 1.0 else (p <= hi))
            n = int(mask.sum())
            rows.append(
                {
                    "bucket": f"{lo:.1f}-{hi:.1f}",
                    "n": n,
                    "mean_score": round(float(p[mask].mean()), 4) if n else None,
                    "observed_rate": round(float(y[mask].mean()), 4) if n else None,
                }
            )
        return rows

    # ─── Persistence ──────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model_type": self.model_type,
                "model": self.model,
                "calibrator": self.calibrator,
                "means": self.means,
            },
            path,
        )
        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "model_type": self.meta.model_type,
                    "feature_names": self.meta.feature_names,
                    "train": self.meta.train,
                    "metrics": self.meta.metrics,
                    "reliability": self.meta.reliability,
                    **(
                        {"coefficients": self.coefficients()}
                        if self.model_type == "logistic_regression"
                        else {}
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def coefficients(self) -> dict[str, float]:
        lr: LogisticRegression = self.model.named_steps["lr"]
        return {k: round(float(v), 6) for k, v in zip(FEATURE_NAMES, lr.coef_[0], strict=True)}

    @classmethod
    def load(cls, path: Path) -> RingScorer:
        blob = joblib.load(path)
        scorer = cls(blob["model_type"])
        scorer.model = blob["model"]
        scorer.calibrator = blob["calibrator"]
        scorer.means = blob["means"]
        meta_path = Path(path).with_suffix(".json")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            scorer.meta = ScorerMetadata(
                meta["model_type"],
                meta["feature_names"],
                meta.get("train", {}),
                meta.get("metrics", {}),
                meta.get("reliability", []),
            )
        if list(scorer.meta.feature_names) != list(FEATURE_NAMES):
            raise ValueError("saved model was trained on a different feature set; retrain it")
        return scorer


def _ece(p: np.ndarray, y: np.ndarray) -> float:
    """Expected calibration error over equal-width bins."""
    total = 0.0
    for lo, hi in itertools.pairwise(BUCKETS):
        mask = (p >= lo) & ((p < hi) if hi < 1.0 else (p <= hi))
        if mask.any():
            total += mask.mean() * abs(float(p[mask].mean()) - float(y[mask].mean()))
    return total
