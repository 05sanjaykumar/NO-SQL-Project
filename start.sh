#!/usr/bin/env bash
# Brings the whole project up: Neo4j, data, dashboard.
# Safe to re-run. Usage:  ./start.sh  [users]
set -euo pipefail
cd "$(dirname "$0")"

USERS="${1:-30}"
PORT=8010

say() { printf '\033[36m==>\033[0m %s\n' "$1"; }

if ! docker info >/dev/null 2>&1; then
  say "Starting Docker Desktop (this takes ~30s the first time)"
  open -a Docker 2>/dev/null || { echo "Docker Desktop not found. Install it first."; exit 1; }
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { echo "Docker did not start."; exit 1; }
fi

say "Starting Neo4j"
docker compose up -d >/dev/null

say "Waiting for Neo4j to accept connections"
for _ in $(seq 1 60); do
  docker exec fraud-neo4j cypher-shell -u neo4j -p fraudring123 "RETURN 1;" >/dev/null 2>&1 && break
  sleep 2
done

if [ ! -d .venv ]; then
  say "Creating virtualenv and installing dependencies"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

say "Seeding the graph with $USERS users"
./.venv/bin/python seed.py --users "$USERS" >/dev/null

# Free the port if a previous dashboard is still holding it.
if lsof -tiTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  say "Stopping the dashboard already on port $PORT"
  lsof -tiTCP:$PORT -sTCP:LISTEN | xargs kill 2>/dev/null || true
  sleep 1
fi

say "Starting the dashboard"
./.venv/bin/python app.py > /tmp/fraud-dashboard.log 2>&1 &
for _ in $(seq 1 30); do
  curl -sf -o /dev/null "http://localhost:$PORT/" && break
  sleep 1
done

echo
echo "  Dashboard     http://localhost:$PORT"
echo "  Neo4j browser http://localhost:7474   (neo4j / fraudring123)"
echo
echo "  Stop with: ./stop.sh"
