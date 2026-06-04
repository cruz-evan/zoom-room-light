#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-auto}"
MPREMOTE="${MPREMOTE:-$ROOT/.venv/bin/mpremote}"

if [[ ! -x "$MPREMOTE" ]]; then
  MPREMOTE="mpremote"
fi

for file in "$ROOT"/device/*.py; do
  name="$(basename "$file")"
  echo "Copying device/$name -> :$name"
  "$MPREMOTE" connect "$PORT" fs cp "$file" ":$name"
done

echo "Resetting board"
"$MPREMOTE" connect "$PORT" reset
