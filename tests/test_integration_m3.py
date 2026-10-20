"""
Milestone 3 integration tests against real Neo4j (with GDS).

- Neo4jGraphStore and InMemoryGraphStore return identical regions and views.
- StaticCypherBaseline (real Cypher) matches its in-memory evaluation.
- GDS Louvain community detection runs and finds a planted dense group.
- The full engine runs on Neo4j and agrees with the in-memory engine.

Run with: pytest -m integration
"""

from __future__ import annotations

import os

import pytest

from fraudsim.detectors.community import CommunityDetector
from fraudsim.detectors.engine import DetectionEngine, EngineConfig, build_detectors
from fraudsim.detectors.static_cypher import StaticCypherBaseline, StaticCypherConfig
from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.graph.neo4j_store import Neo4jGraphStore
from fraudsim.graph.store import InMemoryGraphStore
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.scenario_loader import load_scenario

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client() -> Neo4jClient:
    c = Neo4jClient(
        uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        password=os.environ.get("NEO4J_PASSWORD", "fraudring123"),
    )
    try:
        c._driver.verify_connectivity()
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Neo4j not reachable: {e}")
    c.ensure_schema()
    yield c
    c.wipe()
    c.close()


def load_both(client: Neo4jClient, events: list) -> tuple[Neo4jGraphStore, InMemoryGraphStore]:
    client.wipe()
    neo, mem = Neo4jGraphStore(client), InMemoryGraphStore()
    for i in range(0, len(events), 500):
        neo.apply(events[i : i + 500])
    mem.apply(events)
    return neo, mem


def test_stores_agree_on_expansion_and_views(client: Neo4jClient) -> None:
    events = EventGenerator(seed=3, run_id="it", config=load_scenario("hub_heavy")).generate()
    neo, mem = load_both(client, events)
    now = events[-1].ts.timestamp()
    since = now - 30.0
    seeds = {events[-1].payload.get("sender") or events[-1].payload["user_id"], "u000005"}
    for k, fanout, far in [(1, 10, None), (2, 250, None), (3, 40, None), (2, 250, 20)]:
        got = neo.expand(seeds, k, fanout, since, far_fanout=far)
        assert got == mem.expand(seeds, k, fanout, since, far_fanout=far), (k, fanout, far)
    region = mem.expand(seeds, 2, 250, since)
    assert neo.view(region, since, now).canonical() == mem.view(region, since, now).canonical()
    assert neo.full_view(since, now).canonical() == mem.full_view(since, now).canonical()
    assert neo.stats() == mem.stats()

    cutoff = now - 20.0
    assert neo.prune(cutoff) == mem.prune(cutoff)
    assert neo.full_view(0, now).canonical() == mem.full_view(0, now).canonical()


@pytest.mark.parametrize("cap", [10, 100])
def test_static_cypher_matches_in_memory_rules(client: Neo4jClient, cap: int) -> None:
    events = EventGenerator(seed=4, run_id="it", config=load_scenario("baseline")).generate()
    neo, mem = load_both(client, events)
    cfg = StaticCypherConfig(max_degree_cap=cap)
    view = mem.full_view(0, 0)
    by_neo = StaticCypherBaseline(config=cfg, store=neo).detect(view)
    by_mem = StaticCypherBaseline(config=cfg, store=mem).detect(view)
    key = lambda cs: sorted((c.evidence["query"], tuple(sorted(c.members))) for c in cs)  # noqa: E731
    assert key(by_neo) == key(by_mem)
    assert by_neo  # the scenario has planted rings, so something is found


def test_seed_graph_static_results_on_neo4j(client: Neo4jClient) -> None:
    neo, _ = load_both(client, EventGenerator(seed=42, run_id="seed").generate_seed_events())
    found = StaticCypherBaseline(config=StaticCypherConfig(max_degree_cap=10), store=neo).detect(
        InMemoryGraphStore().full_view(0, 0)
    )
    groups = {frozenset(c.members) for c in found}
    assert frozenset({"u01", "u02", "u03", "u04"}) in groups
    assert frozenset({"u10", "u11", "u12"}) in groups
    assert frozenset({"u21", "u22", "u23"}) in groups
    assert frozenset({"u27", "u28", "u29"}) in groups


def test_gds_louvain_finds_planted_ring(client: Neo4jClient) -> None:
    events = EventGenerator(seed=42, run_id="it", config=load_scenario("baseline")).generate()
    gen = EventGenerator(seed=42, run_id="it", config=load_scenario("baseline"))
    gen.generate()
    neo, mem = load_both(client, events)
    now = events[-1].ts.timestamp()
    view = mem.full_view(now - 900, now)
    found = CommunityDetector(store=neo).detect(view)
    assert all(c.evidence["backend"] == "neo4j" for c in found)
    ring_a = gen.ledger.rings["ring_a"].members
    assert any(len(ring_a & c.members) / len(ring_a | c.members) >= 0.5 for c in found)


def test_engine_on_neo4j_agrees_with_in_memory(client: Neo4jClient) -> None:
    events = EventGenerator(seed=8, run_id="it", config=load_scenario("adversarial")).generate()
    overrides = {"community": {"enabled": False}}
    client.wipe()
    neo = Neo4jGraphStore(client)
    mem = InMemoryGraphStore()
    cfg = EngineConfig(batch_size=100, full_interval_s=30.0)
    eng_neo = DetectionEngine(neo, build_detectors(neo, overrides), config=cfg)
    eng_mem = DetectionEngine(mem, build_detectors(mem, overrides), config=cfg)
    eng_neo.run(events)
    eng_mem.run(events)
    rings = lambda e: sorted(  # noqa: E731
        (tuple(sorted(r.members)), r.status.value) for r in e.tracker.rings.values()
    )
    assert rings(eng_neo) == rings(eng_mem)
    assert eng_neo.reports[-1].store_queries is not None


def test_gds_community_on_graph_without_pairs(client: Neo4jClient) -> None:
    client.wipe()
    neo = Neo4jGraphStore(client)
    first = EventGenerator(seed=1, run_id="it", config=load_scenario("baseline")).generate()[:4]
    neo.apply(first)  # one user and their own attributes: no user-user pairs
    assert CommunityDetector(store=neo).detect(InMemoryGraphStore().full_view(0, 0)) == []
