"""Adversarial search for attacks the fraud-ring detector misses."""

from fraudsim.adversary.space import AttackPlan, mutate, sample
from fraudsim.adversary.search import SearchReport, search
from fraudsim.adversary.trial import TrialResult, run_trial

__all__ = [
    "AttackPlan",
    "SearchReport",
    "TrialResult",
    "mutate",
    "run_trial",
    "sample",
    "search",
]
