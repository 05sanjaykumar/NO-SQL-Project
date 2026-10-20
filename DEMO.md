# What to say to the teacher

A script. Read it once before the review. The lines in quotes are meant to be
said more or less as written.

---

## 1. The opener (memorise this much)

> "Fraud is hard to see when you look at one account at a time. A fraudster
> makes eight fake accounts, and each one looks perfectly normal on its own.
> What gives them away is what the accounts *share* - the same phone, the same
> card, the same IP.
>
> So the problem is not about the accounts. It is about the connections between
> them. Relational databases store rows well but store connections badly, so
> asking 'who shares what with whom, three steps out' turns into many joins and
> gets slow.
>
> A graph database stores the connections directly. That is why we used Neo4j.
>
> We built a small bank dataset, hid fraud rings inside it on purpose, and wrote
> Cypher queries that find them."

If you only remember one sentence: **fraud hides in the connections, not in the
accounts, and graph databases are built for connections.**

---

## 2. The demo, screen by screen

Open Neo4j Browser. Run the queries in this order.

### Q0 - the whole graph

> "This is all 126 records at once. Thirty customers, their devices, cards, IP
> addresses and transactions. You cannot see anything in it, and that is the
> point - this is what raw data looks like to a human."

### Q1 - shared device

> "Now one query. Out of 126 records it returned 5. Four customers, all logging
> in from the same device. Four different names, four different accounts, one
> phone. That is one person running four fake identities."

Point at the star shape. This is the strongest single image in the project.

### Q2 - shared card

> "Same idea on payment credentials. Three accounts, one credit card."

### Q3 - money cycle

> "Here the money leaves user 21, goes to 22, then to 23, and comes back to 21.
> And notice the amounts: 5000, then 4800, then 4600. It shrinks at every hop.
>
> That is money laundering. Money is moved through accounts to disguise where it
> came from, and each hop takes a cut. Legitimate money does not travel in a
> circle."

### Q4a - where the naive approach breaks

> "Now the interesting part. If we simply say 'three or more users on one IP is
> suspicious', we get this - fifteen users flagged. But twelve of them share one
> IP address which is just an office wifi. They are colleagues, not criminals.
> The rule is too blunt."

### Q4b - our fix

> "Same query, one rule added: ignore any node that more than ten people connect
> to. The idea is that the more people share something, the less it tells you.
> Four people on one phone is suspicious. Ten thousand people on a public wifi
> means nothing.
>
> False positives went from twelve to zero. And the query got about four times
> faster, 302 milliseconds down to 74, because it stops expanding that huge hub
> at all."

This is your contribution. Spend the most time here.

### Q5 - summary

> "And this is everything the system flags, in one table. Three rings, and all
> three are the ones we planted."

---

## 3. Questions they will ask

**"Why not just use SQL?"**
> "In SQL the connections live in join tables, so every extra hop is another
> join, and the work multiplies with each one. In Neo4j each record physically
> holds the address of the records it connects to, so following a connection is
> a pointer jump. That costs the same whether there are thirty users or thirty
> million."

**"What is index-free adjacency?"**
> "That is the name for it. Connections are pre-wired into the records, so you
> never search an index to find a neighbour. It is what makes the cost per hop
> constant."

**"Why is your dataset so small?"**
> "It is a controlled dataset. We planted the rings ourselves, so we know the
> correct answer in advance and can prove the queries are right rather than just
> claiming it. With random large data we could not verify correctness."

This is a good answer. Do not apologise for the size.

**"What does your degree cap cost you?"**
> "Recall. If a genuine ring happens to sit behind a busy node, the same rule
> that removes the false positives will skip the real ring too. We bought
> precision and speed by accepting we will miss some cases."

**Say this honestly.** It is the answer that shows you understand the method
rather than repeating it.

**"What would you do next?"**
Point at the Future Scope section in `docs/PLAN.md`. Streaming ingestion,
testing at large scale, adaptive thresholds instead of a fixed cap, graph
algorithms like community detection, and a comparison against a relational
schema.

---

## 4. If asked about something you did not build

Be straight about it.

> "We implemented and measured the core detection model. Streaming ingestion,
> the relational benchmark and the machine learning scoring are documented as
> future scope rather than claimed as done."

Do not claim account takeover detection or an SQL comparison. Neither exists.

---

## 5. Your numbers

Know these three.

| | Value |
|---|---|
| Graph size | 126 nodes, 185 relationships |
| Precision, no cap vs cap | 20% to 100% |
| Query time, no cap vs cap | 302 ms to 74 ms |
