"""
Dashboard for the fraud ring detection project.

Serves a single page that runs the detection queries against Neo4j, so the demo
is buttons and a slider rather than pasted Cypher. Also exposes a scaling
benchmark that regenerates the graph at several sizes and times two different
query shapes against each.

Run:  ./.venv/bin/python app.py      then open http://localhost:8010
"""

import os
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from neo4j import GraphDatabase

import seed

app = FastAPI(title="Fraud ring detection")
driver = GraphDatabase.driver(seed.URI, auth=seed.AUTH)

# ---------------------------------------------------------------- queries
#
# Each entry is one button on the dashboard. `anchored` marks queries that start
# from a single named node and expand outwards, as opposed to queries that scan
# every node of a label. The distinction matters for the scaling benchmark.

QUERIES = {
    "q0": {
        "label": "Whole graph",
        "note": "Everything at once. Deliberately unreadable.",
        "cypher": "MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 400",
    },
    "q1": {
        "truth_ring": "A",
        "label": "Shared device ring",
        "note": "Three or more users on one device.",
        "cypher": """MATCH p = (u:User)-[:USED_DEVICE]->(d:Device)
                     WHERE COUNT { (:User)-[:USED_DEVICE]->(d) } >= 3
                     RETURN p LIMIT 300""",
    },
    "q2": {
        "truth_ring": "B",
        "label": "Shared card ring",
        "note": "Three or more users on one payment credential.",
        "cypher": """MATCH p = (u:User)-[:HAS_CARD]->(c:CreditCard)
                     WHERE COUNT { (:User)-[:HAS_CARD]->(c) } >= 3
                     RETURN p LIMIT 300""",
    },
    "q3": {
        "truth_ring": "cycle",
        "label": "Money cycle",
        "note": "Transfers that return to the sender. Amounts decay each hop.",
        "cypher": """MATCH p = (a:User)-[:PERFORMED]->(:Transaction)-[:SENT_TO]->(b:User)
                       -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(c:User)
                       -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(a)
                     WHERE a.user_id < b.user_id AND a.user_id < c.user_id
                     RETURN p LIMIT 50""",
    },
    "q4": {
        "truth_ring": "C",
        "label": "Shared IP (degree bounded)",
        "note": "Drag the threshold. Above the hub size the office gateway floods the result.",
        "cypher": """MATCH p = (u:User)-[:LOGGED_IN_FROM]->(ip:IPAddress)
                     WHERE COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } >= 3
                       AND COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } < $maxDegree
                     RETURN p LIMIT 400""",
        "uses_degree": True,
    },
    "q5": {
        "label": "Ring around one user",
        "note": "Anchored at u01 and expanded outwards, rather than scanning every user.",
        "anchored": True,
        "cypher": """MATCH p = (s:User {user_id: $uid})-[:USED_DEVICE]->(:Device)
                       <-[:USED_DEVICE]-(:User)
                     RETURN p LIMIT 100""",
    },
}


def to_graph(result):
    """Flatten whatever the query returned into nodes and edges for the front end."""
    nodes, edges = {}, {}

    def add_node(n):
        nodes[n.element_id] = {
            "id": n.element_id,
            "label": list(n.labels)[0] if n.labels else "Node",
            "caption": (n.get("user_id") or n.get("device_id") or n.get("card_id")
                        or n.get("ip") or (f"{n.get('amount')}" if n.get("amount") else None)
                        or n.get("txn_id") or "?"),
            "ring": n.get("ring"),
        }

    def add_rel(r):
        edges[r.element_id] = {
            "id": r.element_id,
            "source": r.start_node.element_id,
            "target": r.end_node.element_id,
            "type": r.type,
        }
        add_node(r.start_node)
        add_node(r.end_node)

    def absorb(v):
        if hasattr(v, "labels"):
            add_node(v)
        elif hasattr(v, "type") and hasattr(v, "start_node"):
            add_rel(v)
        elif hasattr(v, "relationships"):
            for r in v.relationships:
                add_rel(r)
        elif isinstance(v, (list, tuple)):
            for item in v:
                absorb(item)

    for record in result:
        for value in record.values():
            absorb(value)

    return list(nodes.values()), list(edges.values())


@app.get("/api/queries")
def list_queries():
    return [{"id": k, **{x: y for x, y in v.items() if x != "cypher"}, "cypher": v["cypher"]}
            for k, v in QUERIES.items()]


@app.get("/api/stats")
def stats():
    with driver.session() as s:
        counts = s.run("""MATCH (n) WITH labels(n)[0] AS l, count(*) AS c
                          RETURN collect({label: l, count: c}) AS labels,
                                 sum(c) AS nodes""").single()
        rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    return {"nodes": counts["nodes"], "relationships": rels, "labels": counts["labels"]}


@app.get("/api/run/{qid}")
def run(qid: str, max_degree: int = 10, uid: str = "u01"):
    q = QUERIES.get(qid)
    if not q:
        return JSONResponse({"error": "unknown query"}, status_code=404)

    started = time.perf_counter()
    with driver.session() as s:
        result = s.run(q["cypher"], maxDegree=max_degree, uid=uid)
        nodes, edges = to_graph(result)
    ms = round((time.perf_counter() - started) * 1000, 1)

    # A detection only counts as correct if the user belongs to the structure
    # THIS query is meant to find. A cycle member caught by the shared-IP query
    # was caught for the wrong reason - it shares the office gateway - so it is
    # a false positive here, not a lucky hit.
    users = [n for n in nodes if n["label"] == "User"]
    flagged = len(users)
    target = q.get("truth_ring")
    true_pos = len([n for n in users if n["ring"] == target]) if target else None

    return {
        "nodes": nodes, "edges": edges, "ms": ms,
        "flagged_users": flagged,
        "true_positives": true_pos,
        "false_positives": (flagged - true_pos) if target else None,
        "precision": round(true_pos / flagged * 100, 1) if (target and flagged) else None,
    }


@app.post("/api/seed")
def reseed(users: int = seed.DEFAULT_USERS, rng: int = 42):
    if users < seed.MIN_USERS:
        return JSONResponse({"error": f"minimum {seed.MIN_USERS} users"}, status_code=400)
    data, truth = seed.generate(users, rng)
    took = seed.load(driver, data)
    return {"seconds": took, "truth": truth}


@app.get("/api/scale")
def scale(sizes: str = "30,200,1000,3000"):
    """
    Regenerate the graph at each size and time two query shapes against it.

    `scan` matches every (:User)-[:USED_DEVICE]->(:Device) pair before filtering,
    so its cost is bound to the number of users. `anchored` starts from a single
    user and expands outwards, so its cost is bound to the local neighbourhood
    and not to the size of the database. Plotting both together is the point:
    index-free adjacency makes local traversal size-independent, it does not make
    whole-label scans free.
    """
    rows = []
    for raw in sizes.split(","):
        n = int(raw)
        if n < seed.MIN_USERS:
            continue
        data, truth = seed.generate(n, 42)
        seed.load(driver, data)

        timings = {}
        for name, qid in (("scan", "q1"), ("anchored", "q5")):
            with driver.session() as s:
                s.run(QUERIES[qid]["cypher"], maxDegree=10, uid="u01" if n < 100 else
                      f"u{1:0{max(2, len(str(n)))}d}").consume()  # warm up
            samples = []
            for _ in range(5):
                started = time.perf_counter()
                with driver.session() as s:
                    s.run(QUERIES[qid]["cypher"], maxDegree=10,
                          uid=f"u{1:0{max(2, len(str(n)))}d}").consume()
                samples.append((time.perf_counter() - started) * 1000)
            samples.sort()
            timings[name] = round(samples[len(samples) // 2], 1)

        rows.append({"users": n, "nodes": truth["total_nodes"], **timings})
    return {"rows": rows}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8010)))
