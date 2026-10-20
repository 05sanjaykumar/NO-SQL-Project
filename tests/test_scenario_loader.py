"""
Unit tests for the Scenario Loader.
"""

from __future__ import annotations

import pytest

from fraudsim.simulator.scenario_loader import list_scenarios, load_scenario


def test_list_scenarios():
    scenarios = list_scenarios()
    expected = {"baseline", "high_noise", "hub_heavy", "adversarial", "scale_sweep", "drift"}
    assert expected.issubset(set(scenarios))


@pytest.mark.parametrize(
    "scenario_name",
    [
        "baseline",
        "high_noise",
        "hub_heavy",
        "adversarial",
        "scale_sweep",
        "drift",
    ],
)
def test_load_all_scenarios(scenario_name: str):
    cfg = load_scenario(scenario_name)
    assert cfg["name"] == scenario_name
    assert "simulation" in cfg
    assert "rings" in cfg
    assert isinstance(cfg["rings"], list)
    assert len(cfg["rings"]) > 0


def test_load_nonexistent_scenario():
    with pytest.raises(FileNotFoundError, match="not found"):
        load_scenario("non_existent_scenario_12345")
