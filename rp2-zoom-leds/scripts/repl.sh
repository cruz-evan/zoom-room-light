#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-auto}"

if [[ -n "${MPREMOTE:-}" ]]; then
  MPREMOTE_CMD=("$MPREMOTE")
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  MPREMOTE_CMD=("$ROOT/.venv/bin/python" -m mpremote)
else
  MPREMOTE_CMD=("mpremote")
fi

exec "${MPREMOTE_CMD[@]}" connect "$PORT" repl
