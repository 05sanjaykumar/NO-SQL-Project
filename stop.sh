#!/usr/bin/env bash
# Shuts everything down. The graph survives in the Docker volume.
set -uo pipefail
cd "$(dirname "$0")"
lsof -tiTCP:8010 -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
docker compose down
echo "Stopped. Data is kept - ./start.sh brings it all back."
