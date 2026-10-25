#!/usr/bin/env bash
# Brings the whole project up with Docker: Neo4j and the dashboard.
# Safe to re-run. Usage:  ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[36m==>\033[0m %s\n' "$1"; }

if ! docker info >/dev/null 2>&1; then
  say "Starting Docker Desktop (takes ~30s)"
  open -a Docker 2>/dev/null || { echo "Docker Desktop not found. Install it first."; exit 1; }
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { echo "Docker did not start."; exit 1; }
fi

# A dashboard left running outside Docker would hold the port.
if lsof -tiTCP:8010 -sTCP:LISTEN >/dev/null 2>&1; then
  lsof -tiTCP:8010 -sTCP:LISTEN | xargs kill 2>/dev/null || true
  sleep 1
fi

say "Building and starting containers"
docker compose up -d --build

say "Waiting for the dashboard"
for _ in $(seq 1 90); do
  curl -sf -o /dev/null http://localhost:8010/ && break
  sleep 2
done

echo
echo "  Dashboard     http://localhost:8010"
echo "  Neo4j browser http://localhost:7474   (neo4j / fraudring123)"
echo
echo "  Logs:  docker compose logs -f dashboard"
echo "  Stop:  ./stop.sh"
