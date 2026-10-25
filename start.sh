#!/usr/bin/env bash
# One-command start for the fraudsim stack: Neo4j, Redis and the API + dashboard.
# Equivalent to `docker compose --profile app up -d`, plus the things that are
# easy to forget: launching Docker Desktop, and building web/dist so /ui works.
#
#   ./start.sh            start everything
#   ./start.sh --build    rebuild the API image first
set -euo pipefail
cd "$(dirname "$0")"

URL=http://localhost:8000

say() { printf '\033[36m==>\033[0m %s\n' "$1"; }

print_urls() {
  echo
  echo "  Dashboard     $URL/ui/"
  echo "  API docs      $URL/docs"
  echo "  Neo4j browser http://localhost:7474   (neo4j / fraudring123)"
  echo
  echo "  Logs:    docker compose logs -f app"
  echo "  Stop:    ./stop.sh"
}

if ! docker info >/dev/null 2>&1; then
  say "Starting Docker Desktop (takes ~30s)"
  open -a Docker 2>/dev/null || { echo "Docker Desktop not found. Install it first."; exit 1; }
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { echo "Docker did not start."; exit 1; }
fi

# Already serving? Nothing to do.
if curl -sf -o /dev/null --max-time 2 "$URL/ui/" 2>/dev/null; then
  say "Already running"
  print_urls
  exit 0
fi

# The dashboard is served from web/dist, which is not committed. Without it
# /ui returns 404 even though the API is healthy.
if [ ! -f web/dist/index.html ]; then
  say "Building the dashboard (web/dist)"
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm not found - install Node, or run 'make web' elsewhere and copy web/dist."
    exit 1
  fi
  (cd web && npm ci --silent && npm run build >/dev/null)
fi

if [ "${1:-}" = "--build" ]; then
  say "Rebuilding the API image"
  docker compose --profile app up -d --build
else
  say "Starting containers (Neo4j needs ~60s on a cold start)"
  docker compose --profile app up -d
fi

say "Waiting for the dashboard"
for _ in $(seq 1 90); do
  curl -sf -o /dev/null "$URL/ui/" && break
  sleep 2
done

curl -sf -o /dev/null "$URL/ui/" || { echo "Dashboard did not come up. Try: docker compose logs app"; exit 1; }
print_urls
