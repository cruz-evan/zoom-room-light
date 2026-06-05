#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="auto"
PORT_SUPPLIED=0
COPY_SECRETS=0

usage() {
  cat <<'EOF'
Usage: ./scripts/deploy_device.sh [PORT] [--with-secrets]

Copies tracked app files to the board and resets it.

By default, device/secrets.py is preserved on the board. Use --with-secrets
only during USB provisioning or when intentionally updating board-local config.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --with-secrets|--secrets|--provision)
      COPY_SECRETS=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "Unknown option: $arg" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ "$PORT_SUPPLIED" == "1" ]]; then
        echo "Only one port can be supplied." >&2
        usage >&2
        exit 2
      fi
      PORT="$arg"
      PORT_SUPPLIED=1
      ;;
  esac
done

if [[ -n "${MPREMOTE:-}" ]]; then
  MPREMOTE_CMD=("$MPREMOTE")
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  MPREMOTE_CMD=("$ROOT/.venv/bin/python" -m mpremote)
else
  MPREMOTE_CMD=("mpremote")
fi

for file in "$ROOT"/device/*.py; do
  name="$(basename "$file")"
  if [[ "$name" == "secrets.py" || "$name" == "secrets.example.py" ]]; then
    continue
  fi
  echo "Copying device/$name -> :$name"
  "${MPREMOTE_CMD[@]}" connect "$PORT" fs cp "$file" ":$name"
done

if [[ "$COPY_SECRETS" == "1" ]]; then
  secrets_file="$ROOT/device/secrets.py"
  if [[ ! -f "$secrets_file" ]]; then
    echo "Cannot copy secrets: device/secrets.py does not exist." >&2
    exit 1
  fi
  echo "Copying device/secrets.py -> :secrets.py"
  "${MPREMOTE_CMD[@]}" connect "$PORT" fs cp "$secrets_file" ":secrets.py"
else
  echo "Preserving on-device secrets.py (use --with-secrets to provision over USB)"
fi

echo "Resetting board"
"${MPREMOTE_CMD[@]}" connect "$PORT" reset
