"""
Scenario loader — reads YAML scenario configs and provides them as typed dicts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Default scenario directory relative to project root
_DEFAULT_SCENARIO_DIR = Path(__file__).resolve().parents[3] / "configs" / "scenarios"


def load_scenario(
    name: str,
    scenario_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Load a scenario configuration by name.

    Parameters
    ----------
    name
        Scenario name (without .yaml extension).
    scenario_dir
        Override path to the scenarios directory.

    Returns
    -------
    dict
        Parsed scenario configuration.
    """
    base = scenario_dir or _DEFAULT_SCENARIO_DIR
    path = base / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Scenario '{name}' not found at {path}. Available: {list_scenarios(base)}"
        )

    with open(path) as f:
        config: dict[str, Any] = yaml.safe_load(f)

    # Validate required top-level keys
    required = {"name", "simulation", "rings"}
    missing = required - set(config.keys())
    if missing:
        raise ValueError(f"Scenario '{name}' missing required keys: {missing}")

    return config


def list_scenarios(scenario_dir: Path | None = None) -> list[str]:
    """Return the names of all available scenarios."""
    base = scenario_dir or _DEFAULT_SCENARIO_DIR
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))
