"""
Builds the demo graph for the fraud ring detection project.

Two phases:
  1. Generate ordinary noise  - every user gets a device, a card, an IP, some transactions.
  2. Plant the fraud rings    - overwrite a few of those links so known rings exist.

Keeping the planting separate is deliberate: the noise generator stays dumb, and
every fraud pattern lives in one place we can point at.

The dataset size is a parameter. Artefact pools and the legitimate hub scale
with the user count, so the planted rings stay detectable and the degree cap
stays meaningful at any size. At the default of 30 users the output is identical
to the fixed dataset the results in the paper were measured on.

Run:  python seed.py                  # 30 users, the documented dataset
      python seed.py --users 3000     # bigger graph, same planted structures
"""

import argparse
import math
import os
import random
import time
from collections import defaultdict

from neo4j import GraphDatabase

# Inside Docker the database is reachable by service name; natively it is on
# localhost. Both paths work without editing anything.
URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
AUTH = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "fraudring123"))

DEFAULT_USERS = 30
MIN_USERS = 30  # the planted rings occupy fixed positions up to index 28


def pool_sizes(n_users):
    """
    Artefact pool sizes for a given user count.

    They scale with the user count because background sharing is capped at two
    users per artefact - too small a pool and there are not enough slots to go
    round. The constants reproduce the documented 30-user dataset exactly.
    """
    return {
        "devices": max(18, math.ceil(n_users * 0.6)),
        "cards": max(18, math.ceil(n_users * 0.6)),
        "ips": max(20, math.ceil(n_users * 2 / 3)),
        "txns": max(40, math.ceil(n_users * 1.33)),
    }


def assign_with_cap(pool, n_needed, cap):
    """
    Hand out items from `pool` so no single item is used more than `cap` times.

    Plain random.choice does not work here. With many users over a small pool of
    devices, chance alone puts three or more users on the same device, and those
    accidental clusters look exactly like a planted ring to the queries. Capping
    the noise at two keeps every group of three or more genuinely deliberate.
    """
    slots = [x for x in pool for _ in range(cap)]
    if len(slots) < n_needed:
        raise ValueError(f"pool too small: {len(slots)} slots for {n_needed} needed")
    random.shuffle(slots)
    return slots[:n_needed]


def generate(n_users=DEFAULT_USERS, seed=42):
    """Build the graph description and the ground truth that goes with it."""
    if n_users < MIN_USERS:
        raise ValueError(f"need at least {MIN_USERS} users")

    random.seed(seed)
    sizes = pool_sizes(n_users)
    width = max(2, len(str(n_users)))

    users = [f"u{i + 1:0{width}d}" for i in range(n_users)]
    devices = [f"device_{i + 1:03d}" for i in range(sizes["devices"])]
    cards = [f"card_{i + 1:03d}" for i in range(sizes["cards"])]
    ips = [f"ip_{i + 1:03d}" for i in range(sizes["ips"])]

    # --- planted structures, by position so they survive any user count
    ring_a = [users[i] for i in (0, 1, 2, 3)]      # share one device
    ring_b = [users[i] for i in (9, 10, 11)]       # share one card
    ring_c = [users[i] for i in (26, 27, 28)]      # share one IP
    cycle = [users[i] for i in (20, 21, 22)]       # money goes round a loop

    ring_a_device, ring_b_card, ring_c_ip, hub_ip = devices[6], cards[4], ips[3], ips[0]

    # Legitimate shared endpoint - an office gateway, not fraud. Scales with the
    # population so it stays a genuine hub, and never swallows ring C.
    hub_size = max(12, n_users // 3)
    hub_users = [users[i] for i in range(12, min(12 + hub_size, n_users)) if users[i] not in ring_c]

    ring_of = {}
    for members, label in ((ring_a, "A"), (ring_b, "B"), (ring_c, "C"), (cycle, "cycle")):
        for u in members:
            ring_of[u] = label

    # --- phase 1: background noise
    free_devices = [d for d in devices if d != ring_a_device]
    free_cards = [c for c in cards if c != ring_b_card]
    free_ips = [i for i in ips if i not in (hub_ip, ring_c_ip)]

    used_device, has_card = {}, {}
    logged_in_from = defaultdict(list)

    for u, dev, card, ip in zip(
        users,
        assign_with_cap(free_devices, n_users, cap=2),
        assign_with_cap(free_cards, n_users, cap=2),
        assign_with_cap(free_ips, n_users, cap=2),
    ):
        used_device[u], has_card[u], logged_in_from[u] = dev, card, [ip]

    # --- phase 2: plant
    for u in ring_a:
        used_device[u] = ring_a_device
    for u in ring_b:
        has_card[u] = ring_b_card
    for u in ring_c:
        logged_in_from[u].append(ring_c_ip)
    for u in hub_users:
        logged_in_from[u].append(hub_ip)

    # --- transactions: the planted cycle first, then noise
    txns = []
    a, b, c = cycle
    for i, (sender, receiver, amount) in enumerate(
        [(a, b, 5000.0), (b, c, 4800.0), (c, a, 4600.0)]
    ):
        txns.append({"txn_id": f"t{i:05d}", "sender": sender, "receiver": receiver,
                     "amount": amount, "ring": "cycle"})

    # Noise transfers, but never ones that accidentally close a loop. Random
    # transfers will produce a stray three-hop cycle sooner or later, and the
    # cycle query cannot tell that apart from the ring we planted on purpose.
    out = defaultdict(set)
    for t in txns:
        out[t["sender"]].add(t["receiver"])

    def closes_a_cycle(sender, receiver):
        return any(sender in out[mid] for mid in out[receiver])

    made, attempts, want = 0, 0, sizes["txns"] - 3
    while made < want and attempts < want * 50:
        attempts += 1
        sender, receiver = random.sample(users, 2)
        if receiver in out[sender] or closes_a_cycle(sender, receiver):
            continue
        out[sender].add(receiver)
        txns.append({"txn_id": f"t{made + 3:05d}", "sender": sender, "receiver": receiver,
                     "amount": round(random.uniform(50, 2000), 2), "ring": None})
        made += 1

    data = {
        "users": [{"user_id": u, "name": f"User {u[1:]}", "ring": ring_of.get(u)} for u in users],
        "devices": [{"device_id": d} for d in devices],
        "cards": [{"card_id": c, "issuer": random.choice(["visa", "mastercard"])} for c in cards],
        "ips": [{"ip": i} for i in ips],
        "used_device": [{"user_id": u, "device_id": d} for u, d in used_device.items()],
        "has_card": [{"user_id": u, "card_id": c} for u, c in has_card.items()],
        "logged_in_from": [{"user_id": u, "ip": ip}
                           for u, ips_ in logged_in_from.items() for ip in ips_],
        "txns": txns,
    }

    truth = {
        "n_users": n_users,
        "ring_a": {"members": ring_a, "shared_on": ring_a_device},
        "ring_b": {"members": ring_b, "shared_on": ring_b_card},
        "ring_c": {"members": ring_c, "shared_on": ring_c_ip},
        "cycle": {"members": cycle},
        "hub": {"size": len(hub_users), "shared_on": hub_ip},
        "total_nodes": len(users) + len(devices) + len(cards) + len(ips) + len(txns),
    }
    return data, truth


CONSTRAINTS = [
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.user_id IS UNIQUE",
    "CREATE CONSTRAINT device_id IF NOT EXISTS FOR (d:Device) REQUIRE d.device_id IS UNIQUE",
    "CREATE CONSTRAINT card_id IF NOT EXISTS FOR (c:CreditCard) REQUIRE c.card_id IS UNIQUE",
    "CREATE CONSTRAINT ip_addr IF NOT EXISTS FOR (i:IPAddress) REQUIRE i.ip IS UNIQUE",
    "CREATE CONSTRAINT txn_id IF NOT EXISTS FOR (t:Transaction) REQUIRE t.txn_id IS UNIQUE",
]

LOAD = [
    ("users", "UNWIND $rows AS r CREATE (:User {user_id: r.user_id, name: r.name, ring: r.ring})"),
    ("devices", "UNWIND $rows AS r CREATE (:Device {device_id: r.device_id})"),
    ("cards", "UNWIND $rows AS r CREATE (:CreditCard {card_id: r.card_id, issuer: r.issuer})"),
    ("ips", "UNWIND $rows AS r CREATE (:IPAddress {ip: r.ip})"),
    ("used_device", """UNWIND $rows AS r
        MATCH (u:User {user_id: r.user_id}), (d:Device {device_id: r.device_id})
        CREATE (u)-[:USED_DEVICE]->(d)"""),
    ("has_card", """UNWIND $rows AS r
        MATCH (u:User {user_id: r.user_id}), (c:CreditCard {card_id: r.card_id})
        CREATE (u)-[:HAS_CARD]->(c)"""),
    ("logged_in_from", """UNWIND $rows AS r
        MATCH (u:User {user_id: r.user_id}), (i:IPAddress {ip: r.ip})
        CREATE (u)-[:LOGGED_IN_FROM]->(i)"""),
    ("txns", """UNWIND $rows AS r
        MATCH (s:User {user_id: r.sender}), (x:User {user_id: r.receiver})
        CREATE (s)-[:PERFORMED]->(t:Transaction {txn_id: r.txn_id, amount: r.amount, ring: r.ring})
        CREATE (t)-[:SENT_TO]->(x)"""),
]

BATCH = 5000


def load(driver, data, on_progress=None):
    """Wipe the database and load `data`. Returns seconds taken."""
    started = time.time()
    with driver.session() as session:
        session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
        for stmt in CONSTRAINTS:
            session.run(stmt)
        for key, cypher in LOAD:
            rows = data[key]
            for i in range(0, len(rows), BATCH):
                session.run(cypher, rows=rows[i:i + BATCH])
            if on_progress:
                on_progress(key, len(rows))
    return round(time.time() - started, 2)


def wait_for_neo4j(driver, attempts=15):
    for n in range(attempts):
        try:
            driver.verify_connectivity()
            return
        except Exception:
            print(f"  waiting for neo4j... ({n + 1}/{attempts})")
            time.sleep(2)
    raise SystemExit("could not reach neo4j - is `docker compose up -d` running?")


def main():
    ap = argparse.ArgumentParser(description="Seed the fraud detection graph.")
    ap.add_argument("--users", type=int, default=DEFAULT_USERS, help="number of users")
    ap.add_argument("--seed", type=int, default=42, help="random seed")
    args = ap.parse_args()

    data, truth = generate(args.users, args.seed)

    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        wait_for_neo4j(driver)
        print("wiping old data...")
        took = load(driver, data, on_progress=lambda k, n: print(f"  loaded {n:>6} {k}"))

    print(f"\ngraph loaded in {took}s - {truth['total_nodes']} nodes")
    print("\nplanted:")
    print(f"  ring A   {', '.join(truth['ring_a']['members'])} -> {truth['ring_a']['shared_on']}")
    print(f"  ring B   {', '.join(truth['ring_b']['members'])} -> {truth['ring_b']['shared_on']}")
    print(f"  ring C   {', '.join(truth['ring_c']['members'])} -> {truth['ring_c']['shared_on']}")
    print(f"  cycle    {' -> '.join(truth['cycle']['members'])} -> {truth['cycle']['members'][0]}")
    print(f"  hub      {truth['hub']['size']} users on {truth['hub']['shared_on']} (not fraud)")
    print("\nopen http://localhost:7474, or run the dashboard with: python app.py")


if __name__ == "__main__":
    main()
