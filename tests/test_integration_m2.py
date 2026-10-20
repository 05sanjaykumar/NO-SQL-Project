"""
Integration tests for Milestone 2 against real Neo4j and Redis
(`docker compose up -d neo4j redis`). Skipped when the services are unreachable.

Run with: pytest -m integration
"""

from __future__ import annotations

import os
import uuid
from collections import defaultdict

import pytest

from fraudsim.graph.neo4j_client import Neo4jClient
from fraudsim.simulator.event_generator import EventGenerator
from fraudsim.simulator.player import StreamPlayer
from fraudsim.simulator.scenario_loader import load_scenario
from fraudsim.stream.event_bus import RedisStreamBus
from fraudsim.types import Event, EventType

pytestmark = pytest.mark.integration

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "fraudring123")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture(scope="module")
def neo4j() -> Neo4jClient:
    client = Neo4jClient(uri=NEO4J_URI, password=NEO4J_PASSWORD)
    try:
        client._driver.verify_connectivity()
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Neo4j not reachable at {NEO4J_URI}: {e}")
    client.wipe()
    client.ensure_schema()
    yield client
    client.wipe()
    client.close()


def expected_graph(events: list[Event]) -> dict[str, object]:
    """Replay events in plain Python to get the graph Neo4j should end up with."""
    users: dict[str, bool] = {}
    links: dict[str, set[tuple[str, str]]] = defaultdict(set)
    rel = {"device": "USED_DEVICE", "card": "HAS_CARD", "ip": "LOGGED_IN_FROM"}
    txns = 0
    for e in events:
        p = e.payload
        if e.event_type == EventType.USER_JOIN:
            users[p["user_id"]] = True
        elif e.event_type == EventType.USER_LEAVE:
            users[p["user_id"]] = False
        elif e.event_type == EventType.DEVICE_LINK:
            links["USED_DEVICE"].add((p["user_id"], p["device_id"]))
        elif e.event_type == EventType.CARD_LINK:
            links["HAS_CARD"].add((p["user_id"], p["card_id"]))
        elif e.event_type == EventType.IP_LINK:
            links["LOGGED_IN_FROM"].add((p["user_id"], p["ip"]))
        elif e.event_type == EventType.ATTRIBUTE_UNLINK:
            links[rel[p["attr_type"]]].discard((p["user_id"], p["attr_id"]))
        elif e.event_type == EventType.TRANSACTION:
            txns += 1
    return {
        "users": len(users),
        "inactive": sum(1 for active in users.values() if not active),
        "rels": {k: len(v) for k, v in links.items()},
        "txns": txns,
    }


@pytest.mark.parametrize(("scenario", "batch_size"), [("adversarial", 50), ("drift", 500)])
def test_simulated_stream_lands_in_neo4j_exactly(
    neo4j: Neo4jClient, scenario: str, batch_size: int
) -> None:
    neo4j.wipe()
    gen = EventGenerator(seed=42, run_id="it", config=load_scenario(scenario))
    events = gen.generate()
    for i in range(0, len(events), batch_size):
        neo4j.write_events_batch(events[i : i + batch_size])

    want = expected_graph(events)
    stats = neo4j.get_graph_stats()
    assert stats["User"] == want["users"]
    assert stats["Transaction"] == want["txns"]
    assert stats["_relationships"]["PERFORMED"] == want["txns"]
    assert stats["_relationships"]["SENT_TO"] == want["txns"]
    for rel_type, n in want["rels"].items():  # type: ignore[union-attr]
        assert stats["_relationships"].get(rel_type, 0) == n, rel_type

    inactive = neo4j.run_query("MATCH (u:User) WHERE u.active = false RETURN count(u) AS n")
    assert inactive[0]["n"] == want["inactive"]

    # Ground truth never reaches the graph.
    leaked = neo4j.run_query(
        "MATCH (n) WHERE n.ring IS NOT NULL OR n.ring_id IS NOT NULL RETURN count(n) AS n"
    )
    assert leaked[0]["n"] == 0

    # Every relationship and transaction carries event provenance and a timestamp.
    missing = neo4j.run_query(
        "MATCH ()-[r]->() WHERE type(r) IN ['USED_DEVICE','HAS_CARD','LOGGED_IN_FROM'] "
        "AND (r.ts IS NULL OR r.event_id IS NULL) RETURN count(r) AS n"
    )
    assert missing[0]["n"] == 0


def test_hub_hiding_ring_sits_on_a_real_hub_in_neo4j(neo4j: Neo4jClient) -> None:
    neo4j.wipe()
    gen = EventGenerator(seed=42, run_id="it", config=load_scenario("hub_heavy"))
    neo4j.write_events_batch(gen.generate())
    ring = gen.ledger.rings["ring_in_hub"]
    hub_ip = ring.shared_attributes["ip"]
    rows = neo4j.run_query(
        "MATCH (u:User)-[:LOGGED_IN_FROM]->(:IPAddress {ip: $ip}) RETURN u.user_id AS u",
        ip=hub_ip,
    )
    on_hub = {r["u"] for r in rows}
    assert ring.members <= on_hub
    assert len(on_hub) >= 50  # hidden among the office gateway's legitimate users


async def test_player_over_real_redis_stream() -> None:
    redis = pytest.importorskip("redis.asyncio")
    client = redis.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Redis not reachable at {REDIS_URL}: {e}")
    stream = f"fraudsim:test:{uuid.uuid4().hex[:8]}"
    try:
        bus = RedisStreamBus(client=client, stream_name=stream, block_ms=100)
        gen = EventGenerator(seed=7, run_id="it", config=load_scenario("baseline"))
        stats = await StreamPlayer(gen, bus, speed=0).run()
        received = [e async for e in await bus.subscribe()]
        assert len(received) == stats.events_published == gen.events_emitted
        reference = EventGenerator(seed=7, run_id="it", config=load_scenario("baseline"))
        assert [e.to_dict() for e in received] == [e.to_dict() for e in reference.generate()]
    finally:
        await client.delete(stream)
        await client.aclose()
