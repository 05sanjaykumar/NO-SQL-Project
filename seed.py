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

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "fraudring123")

random.seed(42)  # reproducible - same graph every run

N_USERS = 30
N_DEVICES = 18
N_CARDS = 18
N_IPS = 20
N_RANDOM_TXNS = 37

users = [f"u{i:02d}" for i in range(1, N_USERS + 1)]
devices = [f"device_{i:03d}" for i in range(1, N_DEVICES + 1)]
cards = [f"card_{i:03d}" for i in range(1, N_CARDS + 1)]
ips = [f"ip_{i:03d}" for i in range(1, N_IPS + 1)]

# ---------------------------------------------------------------- planted rings

RING_A_USERS = ["u01", "u02", "u03", "u04"]  # all share one device
RING_A_DEVICE = "device_007"

RING_B_USERS = ["u10", "u11", "u12"]  # all share one card
RING_B_CARD = "card_005"

RING_C_USERS = ["u27", "u28", "u29"]  # all share one IP
RING_C_IP = "ip_004"

CYCLE_USERS = ["u21", "u22", "u23"]  # money goes round in a loop

# Not fraud. Office wifi. This is what the degree cap in Q3 has to filter out.
HUB_IP = "ip_001"
HUB_USERS = [f"u{i:02d}" for i in range(13, 25)]  # 12 users

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

    # --- anchor structures, at fixed positions so the documented 30-user
    # --- dataset is reproduced exactly whatever the size parameter is
    device_rings = [{"on": devices[6], "members": [users[i] for i in (0, 1, 2, 3)]}]
    card_rings = [{"on": cards[4], "members": [users[i] for i in (9, 10, 11)]}]
    ip_rings = [{"on": ips[3], "members": [users[i] for i in (26, 27, 28)]}]
    cycles = [[users[i] for i in (20, 21, 22)]]

    # Legitimate shared endpoint - an office gateway, not fraud. Scales with the
    # population so it stays a genuine hub.
    hub_ip = ips[0]
    hub_end = min(12 + max(12, n_users // 3), n_users)

    # --- extra structures, scaled with the population so the fraction of
    # --- fraudulent accounts stays roughly constant instead of thinning out
    extra_rings = max(0, n_users // 150 - 1)
    extra_cycles = max(0, n_users // 300 - 1)

    cursor = max(29, hub_end)        # first index free of every anchor and the hub
    taken = {"devices": 7, "cards": 5, "ips": 4}  # end-of-pool artefacts already used

    def claim(count):
        """Take `count` consecutive unused users, or None if the graph is too small."""
        nonlocal cursor
        if cursor + count > n_users:
            return None
        block = users[cursor:cursor + count]
        cursor += count
        return block

    for rings, pool, key in ((device_rings, devices, "devices"),
                             (card_rings, cards, "cards"),
                             (ip_rings, ips, "ips")):
        for _ in range(extra_rings):
            members = claim(random.randint(3, 5))
            if members is None:
                break
            taken[key] += 1
            rings.append({"on": pool[-taken[key]], "members": members})

    for _ in range(extra_cycles):
        members = claim(3)
        if members is None:
            break
        cycles.append(members)

    # Hub membership excludes every user planted in an IP ring, so a planted
    # ring is never also reachable through the gateway.
    in_ip_ring = {u for r in ip_rings for u in r["members"]}
    hub_users = [users[i] for i in range(12, hub_end) if users[i] not in in_ip_ring]

    ring_of = {}
    for kind, rings in (("device", device_rings), ("card", card_rings), ("ip", ip_rings)):
        for n, r in enumerate(rings, 1):
            for u in r["members"]:
                ring_of[u] = f"{kind}-{n}"
    for n, members in enumerate(cycles, 1):
        for u in members:
            ring_of[u] = f"cycle-{n}"

    # --- phase 1: background noise, over the artefacts no ring is using
    planted = {"devices": {r["on"] for r in device_rings},
               "cards": {r["on"] for r in card_rings},
               "ips": {r["on"] for r in ip_rings} | {hub_ip}}
    free_devices = [d for d in devices if d not in planted["devices"]]
    free_cards = [c for c in cards if c not in planted["cards"]]
    free_ips = [i for i in ips if i not in planted["ips"]]

    used_device, has_card = {}, {}
    logged_in_from = defaultdict(list)

    noise_devices = assign_with_cap(free_devices, len(users), cap=2)
    noise_cards = assign_with_cap(free_cards, len(users), cap=2)
    noise_ips = assign_with_cap(free_ips, len(users), cap=2)

    for u, dev, card, ip in zip(users, noise_devices, noise_cards, noise_ips, strict=True):
        used_device[u] = dev
        has_card[u] = card
        logged_in_from[u] = [ip]

    # --- phase 2: plant the rings
    for u in RING_A_USERS:
        used_device[u] = RING_A_DEVICE
    for u in RING_B_USERS:
        has_card[u] = RING_B_CARD
    for u in RING_C_USERS:
        logged_in_from[u].append(RING_C_IP)
    for u in HUB_USERS:
        logged_in_from[u].append(HUB_IP)

    # --- transactions: the cycle first, then noise
    txns = []
    a, b, c = CYCLE_USERS
    for i, (sender, receiver, amount) in enumerate(
        [(a, b, 5000.0), (b, c, 4800.0), (c, a, 4600.0)]
    ):
        used_device[u], has_card[u], logged_in_from[u] = dev, card, [ip]

    # --- phase 2: plant
    for r in device_rings:
        for u in r["members"]:
            used_device[u] = r["on"]
    for r in card_rings:
        for u in r["members"]:
            has_card[u] = r["on"]
    for r in ip_rings:
        for u in r["members"]:
            logged_in_from[u].append(r["on"])
    for u in hub_users:
        logged_in_from[u].append(hub_ip)

    # --- transactions: the planted cycles first, then noise
    txns = []
    for members in cycles:
        a, b, c = members
        amount = 5000.0
        for sender, receiver in ((a, b), (b, c), (c, a)):
            txns.append({"txn_id": f"t{len(txns):05d}", "sender": sender, "receiver": receiver,
                         "amount": amount, "ring": "cycle"})
            amount -= 200.0

    # Noise transfers, but never ones that accidentally close a loop. Random
    # transfers will produce a stray three-hop cycle sooner or later, and the
    # cycle query cannot tell that apart from the ones we planted on purpose.
    out = defaultdict(set)
    for t in txns:
        out[t["sender"]].add(t["receiver"])

    def closes_a_cycle(sender, receiver):
        return any(sender in out[mid] for mid in out[receiver])

    made, attempts, want = 0, 0, max(0, sizes["txns"] - len(txns))
    while made < want and attempts < want * 50:
        attempts += 1
        sender, receiver = random.sample(users, 2)
        if receiver in out[sender] or closes_a_cycle(sender, receiver):
            continue
        out[sender].add(receiver)
        txns.append({"txn_id": f"t{len(txns):05d}", "sender": sender, "receiver": receiver,
                     "amount": round(random.uniform(50, 2000), 2), "ring": None})
        made += 1

    return {
        "users": [{"user_id": u, "name": f"User {u[1:]}", "ring": RING_OF.get(u)} for u in users],
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
        "device_rings": device_rings,
        "card_rings": card_rings,
        "ip_rings": ip_rings,
        "cycles": cycles,
        "hub": {"size": len(hub_users), "shared_on": hub_ip},
        "planted_users": len(ring_of),
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
    (
        "used_device",
        """UNWIND $rows AS r
           MATCH (u:User {user_id: r.user_id}), (d:Device {device_id: r.device_id})
           CREATE (u)-[:USED_DEVICE]->(d)""",
    ),
    (
        "has_card",
        """UNWIND $rows AS r
           MATCH (u:User {user_id: r.user_id}), (c:CreditCard {card_id: r.card_id})
           CREATE (u)-[:HAS_CARD]->(c)""",
    ),
    (
        "logged_in_from",
        """UNWIND $rows AS r
           MATCH (u:User {user_id: r.user_id}), (i:IPAddress {ip: r.ip})
           CREATE (u)-[:LOGGED_IN_FROM]->(i)""",
    ),
    (
        "txns",
        """UNWIND $rows AS r
           MATCH (s:User {user_id: r.sender}), (x:User {user_id: r.receiver})
           CREATE (s)-[:PERFORMED]->(t:Transaction {
               txn_id: r.txn_id, amount: r.amount, ring: r.ring})
           CREATE (t)-[:SENT_TO]->(x)""",
    ),
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
    print(f"\nplanted ({truth['planted_users']} of {truth['n_users']} users):")
    for kind in ("device", "card", "ip"):
        for n, r in enumerate(truth[f"{kind}_rings"], 1):
            members = r["members"]
            shown = ", ".join(members[:4]) + ("..." if len(members) > 4 else "")
            print(f"  {kind + '-' + str(n):<10} {len(members)} users on {r['on']:<12} {shown}")
    for n, members in enumerate(truth["cycles"], 1):
        print(f"  {'cycle-' + str(n):<10} {' -> '.join(members)} -> {members[0]}")
    print(f"  {'hub':<10} {truth['hub']['size']} users on {truth['hub']['shared_on']} (not fraud)")
    print("\nopen http://localhost:7474, or run the dashboard with: python app.py")


if __name__ == "__main__":
    main()
