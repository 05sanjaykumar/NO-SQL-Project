// ============================================================================
//  Fraud ring detection - queries
//  Paste one block at a time into the Neo4j browser at http://localhost:7474
//  Queries returning `p` draw a graph. Queries returning columns give a table.
// ============================================================================


// ---------------------------------------------------------------- Q0
// The whole graph. Small enough to fit on one screen - good first screenshot.

MATCH (n)-[r]->(m) RETURN n, r, m;


// ---------------------------------------------------------------- Q1
// Shared device ring: three or more users on the same device.
// Should find ring A - u01, u02, u03, u04 on device_007.

MATCH (u:User)-[:USED_DEVICE]->(d:Device)
WITH d, collect(u.user_id) AS members, count(*) AS ring_size
WHERE ring_size >= 3
RETURN d.device_id AS shared_device, ring_size, members
ORDER BY ring_size DESC;

// Same thing as a picture.
MATCH p = (u:User)-[:USED_DEVICE]->(d:Device)
WHERE COUNT { (:User)-[:USED_DEVICE]->(d) } >= 3
RETURN p;


// ---------------------------------------------------------------- Q2
// Shared card ring: three or more users on the same credit card.
// Should find ring B - u10, u11, u12 on card_005.

MATCH p = (u:User)-[:HAS_CARD]->(c:CreditCard)
WHERE COUNT { (:User)-[:HAS_CARD]->(c) } >= 3
RETURN p;


// ---------------------------------------------------------------- Q3
// Circular money flow: A pays B, B pays C, C pays A.
// Should find the cycle u21 -> u22 -> u23 -> u21.
//
// The WHERE clause is not cosmetic. Without it the same cycle comes back three
// times, once starting from each member. Anchoring on the smallest id keeps one.

MATCH p = (a:User)-[:PERFORMED]->(:Transaction)-[:SENT_TO]->(b:User)
          -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(c:User)
          -[:PERFORMED]->(:Transaction)-[:SENT_TO]->(a)
WHERE a.user_id < b.user_id AND a.user_id < c.user_id
RETURN p;


// ---------------------------------------------------------------- Q4a
// Shared IP ring, NO degree cap.
//
// Finds ring C (u27, u28, u29 on ip_004) but also drags in ip_001, which is
// twelve perfectly normal users on the office wifi. Twelve false positives.

MATCH p = (u:User)-[:LOGGED_IN_FROM]->(ip:IPAddress)
WHERE COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } >= 3
  AND COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } < 100
RETURN p;


// ---------------------------------------------------------------- Q4b
// Shared IP ring, WITH the degree cap.
//
// One number changed: 100 -> 10. The office wifi hub is now skipped and only
// ring C comes back. This pair of results is the degree-bounded traversal
// claim from the report, demonstrated.
//
// The trade-off, and you will be asked: if a real ring happens to sit behind a
// busy node, this cap misses it too. Precision bought with recall.

MATCH p = (u:User)-[:LOGGED_IN_FROM]->(ip:IPAddress)
WHERE COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } >= 3
  AND COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } < 10
RETURN p;


// ---------------------------------------------------------------- Q5
// Everything flagged, as a summary table. Useful as a results screenshot.

MATCH (u:User)-[:USED_DEVICE]->(d:Device)
WHERE COUNT { (:User)-[:USED_DEVICE]->(d) } >= 3
RETURN 'shared device' AS pattern, d.device_id AS shared_on, collect(u.user_id) AS members
UNION
MATCH (u:User)-[:HAS_CARD]->(c:CreditCard)
WHERE COUNT { (:User)-[:HAS_CARD]->(c) } >= 3
RETURN 'shared card' AS pattern, c.card_id AS shared_on, collect(u.user_id) AS members
UNION
MATCH (u:User)-[:LOGGED_IN_FROM]->(ip:IPAddress)
WHERE COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } >= 3
  AND COUNT { (:User)-[:LOGGED_IN_FROM]->(ip) } < 10
RETURN 'shared ip' AS pattern, ip.ip AS shared_on, collect(u.user_id) AS members;
