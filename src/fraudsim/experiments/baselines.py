"""
Baseline definitions and registry for comparative experiments.

Baselines:
1. StaticCypherBaseline (cap=100): Original Q1-Q5 queries with loose degree cap.
2. StaticCypherBaseline (cap=10): Hard degree cap (high precision, low recall on hub-hiding rings).
3. CommunityDetection (Louvain/WCC): Neo4j GDS based graph clustering.
4. PlainConnectedComponents: Unweighted graph connectivity.
"""

from __future__ import annotations

from typing import Any

BASELINES: dict[str, dict[str, Any]] = {
    "static_cypher_cap100": {
        "name": "Static Cypher (cap=100)",
        "detector": "static_cypher",
        "degree_cap": 100,
        "description": "Baseline queries with relaxed degree cap (susceptible to hub noise)",
    },
    "static_cypher_cap10": {
        "name": "Static Cypher (cap=10)",
        "detector": "static_cypher",
        "degree_cap": 10,
        "description": "Baseline queries with aggressive degree cap (misses rings hiding in hubs)",
    },
    "community_louvain": {
        "name": "Community Detection (Louvain)",
        "detector": "community",
        "algorithm": "louvain",
        "description": "Neo4j GDS modularity-based community detection",
    },
    "plain_cc": {
        "name": "Plain Connected Components",
        "detector": "community",
        "algorithm": "wcc",
        "description": "Unweighted weakly connected components on shared attributes",
    },
}
