"""
Builds the demo graph for the fraud ring detection project.

Two phases:
  1. Generate ordinary noise  - every user gets a device, a card, some IPs, some transactions.
  2. Plant the fraud rings    - overwrite a few of those links so known rings exist.

Keeping the planting separate is deliberate: the noise generator stays dumb, and
every fraud pattern lives in one place we can point at.

Run:  python seed.py
"""

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

RING_A_USERS = ["u01", "u02", "u03", "u04"]      # all share one device
RING_A_DEVICE = "device_007"

RING_B_USERS = ["u10", "u11", "u12"]             # all share one card
RING_B_CARD = "card_005"

RING_C_USERS = ["u27", "u28", "u29"]             # all share one IP
RING_C_IP = "ip_004"

CYCLE_USERS = ["u21", "u22", "u23"]              # money goes round in a loop

# Not fraud. Office wifi. This is what the degree cap in Q3 has to filter out.
HUB_IP = "ip_001"
HUB_USERS = [f"u{i:02d}" for i in range(13, 25)]  # 12 users

RING_OF = {}
for u in RING_A_USERS:
    RING_OF[u] = "A"
for u in RING_B_USERS:
    RING_OF[u] = "B"
for u in RING_C_USERS:
    RING_OF[u] = "C"
for u in CYCLE_USERS:
    RING_OF[u] = "cycle"


def assign_with_cap(pool, n_needed, cap):
    """
    Hand out items from `pool` so no single item is used more than `cap` times.

    Plain random.choice does not work here. With 30 users over a small pool of
    devices, chance alone puts three or more users on the same device, and those
    accidental clusters look exactly like a planted ring to the queries. Capping
    the noise at two keeps every group of three or more genuinely deliberate.
    """
    slots = [x for x in pool for _ in range(cap)]
    if len(slots) < n_needed:
        raise ValueError(f"pool too small: {len(slots)} slots for {n_needed} needed")
    random.shuffle(slots)
    return slots[:n_needed]


def build_graph():
    """Returns plain dicts/lists describing the graph, ready to load."""

    # Hold back the ring attributes so random assignment can never touch them.
    free_devices = [d for d in devices if d != RING_A_DEVICE]
    free_cards = [c for c in cards if c != RING_B_CARD]
    free_ips = [i for i in ips if i not in (HUB_IP, RING_C_IP)]

    # --- phase 1: noise
    used_device = {}
    has_card = {}
    logged_in_from = defaultdict(list)

    noise_devices = assign_with_cap(free_devices, len(users), cap=2)
    noise_cards = assign_with_cap(free_cards, len(users), cap=2)
    noise_ips = assign_with_cap(free_ips, len(users), cap=2)

    for u, dev, card, ip in zip(users, noise_devices, noise_cards, noise_ips):
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
        txns.append(
            {
                "txn_id": f"t{i:03d}",
                "sender": sender,
                "receiver": receiver,
                "amount": amount,
                "ring": "cycle",
            }
        )

    # Noise transfers, but never ones that accidentally close a loop. Random
    # transfers between 30 users will produce a stray three-hop cycle sooner or
    # later, and Q3 cannot tell that apart from the ring we planted on purpose.
    out = defaultdict(set)
    for t in txns:
        out[t["sender"]].add(t["receiver"])

    def closes_a_cycle(sender, receiver):
        return any(sender in out[mid] for mid in out[receiver])

    made = 0
    while made < N_RANDOM_TXNS:
        sender, receiver = random.sample(users, 2)
        if receiver in out[sender] or closes_a_cycle(sender, receiver):
            continue
        out[sender].add(receiver)
        txns.append(
            {
                "txn_id": f"t{made + 3:03d}",
                "sender": sender,
                "receiver": receiver,
                "amount": round(random.uniform(50, 2000), 2),
                "ring": None,
            }
        )
        made += 1

    return {
        "users": [
            {"user_id": u, "name": f"User {u[1:]}", "ring": RING_OF.get(u)}
            for u in users
        ],
        "devices": [{"device_id": d} for d in devices],
        "cards": [{"card_id": c, "issuer": random.choice(["visa", "mastercard"])} for c in cards],
        "ips": [{"ip": i} for i in ips],
        "used_device": [{"user_id": u, "device_id": d} for u, d in used_device.items()],
        "has_card": [{"user_id": u, "card_id": c} for u, c in has_card.items()],
        "logged_in_from": [
            {"user_id": u, "ip": ip} for u, ip_list in logged_in_from.items() for ip in ip_list
        ],
        "txns": txns,
    }


# ---------------------------------------------------------------- loading

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
           CREATE (s)-[:PERFORMED]->(t:Transaction {txn_id: r.txn_id, amount: r.amount, ring: r.ring})
           CREATE (t)-[:SENT_TO]->(x)""",
    ),
]


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
    data = build_graph()

    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        wait_for_neo4j(driver)

        with driver.session() as session:
            print("wiping old data...")
            session.run("MATCH (n) DETACH DELETE n")

            for stmt in CONSTRAINTS:
                session.run(stmt)

            for key, cypher in LOAD:
                rows = data[key]
                session.run(cypher, rows=rows)
                print(f"  loaded {len(rows):>3} {key}")

            counts = session.run(
                """MATCH (n) WITH labels(n)[0] AS label, count(*) AS n
                   RETURN label, n ORDER BY label"""
            ).values()

    print("\ngraph loaded:")
    for label, n in counts:
        print(f"  {label:<12} {n}")

    print("\nplanted:")
    print(f"  ring A   {', '.join(RING_A_USERS)}  -> {RING_A_DEVICE}")
    print(f"  ring B   {', '.join(RING_B_USERS)}       -> {RING_B_CARD}")
    print(f"  ring C   {', '.join(RING_C_USERS)}       -> {RING_C_IP}")
    print(f"  cycle    {' -> '.join(CYCLE_USERS)} -> {CYCLE_USERS[0]}")
    print(f"  hub      {len(HUB_USERS)} users on {HUB_IP}  (not fraud - office wifi)")
    print("\nopen http://localhost:7474 and run the queries in queries.cypher")


if __name__ == "__main__":
    main()
