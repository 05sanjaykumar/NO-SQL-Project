"""
Unit tests for the StaticCypherBaseline detector configuration and structure.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fraudsim.detectors.static_cypher import StaticCypherBaseline, StaticCypherConfig
from fraudsim.types import DetectorType


def test_static_cypher_config():
    cfg = StaticCypherConfig(
        max_degree_cap=100,
        enable_q4_shared_ip=False,
    )
    assert cfg.max_degree_cap == 100
    assert not cfg.enable_q4_shared_ip
    assert cfg.detector_type == DetectorType.STATIC_CYPHER


def test_static_cypher_init():
    mock_client = MagicMock()
    detector = StaticCypherBaseline(client=mock_client)
    assert detector.name == "StaticCypherBaseline"
    assert detector.config.detector_type == DetectorType.STATIC_CYPHER
