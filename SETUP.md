# Setup guide

Written for someone who has not used Docker or Neo4j before. Follow it top to
bottom the first time; after that you only need the "Everyday use" section.

---

## What the pieces actually are

**Docker** runs software in a sealed box called a **container**. We use it so
nobody has to install Neo4j properly. The box either runs or it doesn't, and
deleting it leaves no mess behind.

**Docker Desktop** is the Mac app that keeps Docker itself running. If it isn't
open, every `docker` command fails. Look for the whale icon in your menu bar.

**Neo4j** is the graph database. Our container runs it and exposes two ports:

| Port | What it's for |
|---|---|
| 7474 | The web page you look at — http://localhost:7474 |
| 7687 | The data connection `seed.py` uses (the "bolt" protocol) |

**The volume** (`no_sql_neo4j-data`) is where Neo4j keeps the actual data. It
lives outside the container, which is why stopping the container doesn't lose
your graph.

**`docker-compose.yml`** describes all of the above so one command sets it up.

---

## The password

**Username:** `neo4j`
**Password:** `fraudring123`

It comes from this line in `docker-compose.yml`:

```yaml
NEO4J_AUTH: neo4j/fraudring123
```

Neo4j reads that line **only the very first time the container starts** and
creates the account. After that the password lives in the volume, and editing
the compose file has no effect.

To genuinely change it: run `docker compose down -v` (the `-v` wipes the volume),
edit the password in `docker-compose.yml`, change `AUTH` in `seed.py` to match,
then `docker compose up -d` and `python seed.py`.

The same password appears in three places and all three must agree:
`docker-compose.yml`, the `AUTH` line in `seed.py`, and whatever you type into
the browser login form.

---

## Quickest way

```bash
./start.sh
```

That does everything in one command: starts Docker Desktop if it is not
running, brings up Neo4j and the dashboard, waits for the database to accept
queries, and seeds the graph on first run. Then open
http://localhost:8010. `./stop.sh` shuts it down.

Both services run in containers, so **nothing needs installing on your machine
except Docker Desktop** - no Python, no virtualenv, no packages.

Useful afterwards:

| Command | What it does |
|---|---|
| `docker compose logs -f dashboard` | Watch the dashboard's output |
| `docker compose restart dashboard` | Pick up code edits (no rebuild needed) |
| `docker compose up -d --build` | Rebuild after changing `requirements.txt` |
| `docker compose down -v` | Wipe the graph and start over |

The rest of this file explains what those steps actually do, and what to try
when one of them fails.

## First time setup

Run these from inside the project folder.

**1. Start Docker Desktop.** Open it from Applications and wait for the whale
icon to stop animating.

**2. Start the database.**

```bash
docker compose up -d
```

First run downloads Neo4j (~500 MB, a few minutes). Later runs take seconds.
The `-d` means "detached" — it runs in the background instead of taking over
your terminal.

**3. Install the Python driver.**

```bash
pip install -r requirements.txt
```

This is the library `seed.py` uses to talk to Neo4j. One-time.

**4. Load the data.**

```bash
python seed.py
```

Wipes anything already there, creates 126 nodes and 185 relationships, and
plants the fraud rings. It prints what it planted so you can check the queries
against it.

**5. Open the browser.** Go to http://localhost:7474, enter `neo4j` /
`fraudring123`, click Connect. You only log in once per machine.

**6. Run a query.** Paste this into the bar at the top and press `Cmd+Enter`:

```
MATCH (n)-[r]->(m) RETURN n, r, m
```

If circles and lines appear, everything works.

---

## Everyday use

**Starting a work session:**

```bash
docker compose up -d
```

Give it about 20 seconds, then open http://localhost:7474. Your data is still
there — no need to reseed.

**Ending a session:**

```bash
docker compose down
```

Stops the container, keeps the data. This is the one you want almost always.

**Leaving it running** is fine too. It uses a little memory and nothing else.

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up -d` | Start the database in the background |
| `docker compose down` | Stop it, **keep** the data |
| `docker compose down -v` | Stop it and **delete** the data and the password |
| `docker ps` | List running containers — `fraud-neo4j` should appear |
| `docker compose logs neo4j` | Show Neo4j's own log, useful when it won't start |
| `python seed.py` | Wipe and rebuild the graph |

`seed.py` is safe to run as often as you like. The random seed is fixed, so you
get a byte-identical graph every time.

---

## Running a query without the browser

Handy for a quick check:

```bash
docker exec fraud-neo4j cypher-shell -u neo4j -p fraudring123 "MATCH (n) RETURN count(n);"
```

Should print `126`.

---

## Troubleshooting

**`Cannot connect to the Docker daemon`**
Docker Desktop isn't running. Open it, wait for the whale icon, try again.

**http://localhost:7474 won't load**
The container is stopped. Run `docker compose up -d` and wait ~20 seconds —
Neo4j takes a moment to boot even after Docker reports it as started.

**Login says "authentication failure"**
The password must be exactly `fraudring123`. If you changed it in
`docker-compose.yml` after the first startup, that change did nothing — see the
password section above.

**A query returns "no changes, no records"**
The database is empty. Run `python seed.py`.

**`ModuleNotFoundError: No module named 'neo4j'`**
Run `pip install -r requirements.txt`. If you use `python3`, use
`pip3 install -r requirements.txt`.

**A query gives a syntax error**
You probably pasted more than one query. `queries.cypher` holds several,
separated by comment headers — select and paste just one block at a time.

**"Port is already allocated"**
Something else is using 7474 or 7687, usually an older Neo4j. Run
`docker ps` to find it and `docker stop <name>`.

**Everything is broken and you want a clean slate**

```bash
docker compose down -v
docker compose up -d
python seed.py
```

Nuclear but reliable — it deletes the volume and rebuilds from scratch.

---

## Removing it completely

```bash
docker compose down -v
docker rmi neo4j:5.26-community
```

The first removes the container and data, the second removes the downloaded
Neo4j image. Nothing is left on your machine.
