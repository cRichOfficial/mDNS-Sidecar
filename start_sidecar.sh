#!/usr/bin/env bash
# NeonBeam Discovery Sidecar — Linux / macOS launch script
# Run from the repo root:   ./discovery_sidecar/start_sidecar.sh
# Or from inside discovery_sidecar/:  ./start_sidecar.sh

set -euo pipefail

PORT="${DISCOVERY_PORT:-3001}"
HOST="${DISCOVERY_HOST:-0.0.0.0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  NeonBeam Discovery Sidecar"
echo "  --------------------------"

# Create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "  Creating .venv..."
    python3 -m venv .venv
fi

# Activate and install deps
# shellcheck disable=SC1091
source .venv/bin/activate

echo "  Installing / updating dependencies..."
pip install -q -r requirements.txt

echo ""
echo "  Starting sidecar on http://${HOST}:${PORT}"
echo "  Press Ctrl-C to stop."
echo ""

exec python run.py --host "$HOST" --port "$PORT"
