"""
Loaders that replay public financial-crime datasets through the detection engine.

None of the datasets ships with this repository (licences and size); each loader
documents where to download it. All three are transaction-only: they carry no
device/card/IP links, so the shared-attribute detectors have nothing to work on
and only the transaction detectors (temporal cycles, smurfing) apply. Labels are
per account (AMLworld, PaySim) or per transaction node (Elliptic), and AMLworld
additionally lists laundering attempts as groups, which we treat as rings.
"""

from fraudsim.datasets.base import PublicDataset, evaluate_accounts, run_public

__all__ = ["PublicDataset", "evaluate_accounts", "load_dataset", "run_public"]


def load_dataset(name: str, path: str, **kw) -> PublicDataset:  # type: ignore[no-untyped-def]
    if name == "amlworld":
        from fraudsim.datasets.amlworld import load_amlworld

        return load_amlworld(path, **kw)
    if name == "elliptic":
        from fraudsim.datasets.elliptic import load_elliptic

        return load_elliptic(path, **kw)
    if name == "paysim":
        from fraudsim.datasets.paysim import load_paysim

        return load_paysim(path, **kw)
    if name == "amlsim":
        from fraudsim.datasets.amlsim import load_amlsim

        return load_amlsim(path, **kw)
    raise ValueError(f"unknown dataset {name!r} (amlworld | elliptic | paysim | amlsim)")
