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

for file in "$ROOT"/device/*.py; do
  name="$(basename "$file")"
  echo "Copying device/$name -> :$name"
  "${MPREMOTE_CMD[@]}" connect "$PORT" fs cp "$file" ":$name"
done

echo "Resetting board"
"${MPREMOTE_CMD[@]}" connect "$PORT" reset
