#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-auto}"
MPREMOTE="${MPREMOTE:-$ROOT/.venv/bin/mpremote}"

if [[ ! -x "$MPREMOTE" ]]; then
  MPREMOTE="mpremote"
fi

exec "$MPREMOTE" connect "$PORT" exec "import sys; print(sys.implementation)"

