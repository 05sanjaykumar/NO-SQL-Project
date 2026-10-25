#!/usr/bin/env bash
# Stops the fraudsim stack. Data survives in the Docker volumes.
set -uo pipefail
cd "$(dirname "$0")"
docker compose --profile app down --remove-orphans
echo "Stopped. Data is kept - ./start.sh brings it all back. Use 'make reset' to wipe volumes."
