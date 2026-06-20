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

When --with-secrets is used, WIFI_SSID and WIFI_PASSWORD environment variables
override those fields through a temporary secrets.py upload. This is intended
for GitHub Actions/self-hosted USB provisioning without hard-coding Wi-Fi in
device/secrets.py.
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

env_is_set_and_nonempty() {
  local name="$1"
  local value="${!name:-}"
  [[ -n "$value" ]]
}

tmp_secrets_dir=""
cleanup_tmp_secrets() {
  if [[ -n "$tmp_secrets_dir" ]]; then
    rm -rf "$tmp_secrets_dir"
    tmp_secrets_dir=""
  fi
}

render_secrets_from_env() {
  local source_file="$1"
  local rendered_file="$2"
  local env_args=()
  local override_names=()
  local env_names=(
    WIFI_SSID
    WIFI_PASSWORD
    OFFICE_WIFI_SSID
    OFFICE_WIFI_PASSWORD
    WIFI_FALLBACK_SSID
    WIFI_FALLBACK_PASSWORD
    FALLBACK_PHONE_HOTSPOT_WIFI_SSID
    FALLBACK_PHONE_HOTSPOT_WIFI_PASSWORD
    PHONE_HOTSPOT_SSID
    PHONE_HOTSPOT_PASSWORD
    DEVICE_ID
    ROOM_ID
    DEVICE_HOSTNAME
    DEVICE_HOSTNAME_PREFIX
    STATE_URL
    DEVICE_TOKEN
    OTA_MANIFEST_URL
    OTA_TOKEN
    OTA_ENABLED
    OTA_CONFIG_URL
    OTA_CONFIG_KEY
    OTA_CONFIG_ENABLED
  )

  if env_is_set_and_nonempty WIFI_SSID || env_is_set_and_nonempty WIFI_PASSWORD; then
    if ! env_is_set_and_nonempty WIFI_SSID || ! env_is_set_and_nonempty WIFI_PASSWORD; then
      echo "WIFI_SSID and WIFI_PASSWORD must both be set when overriding Wi-Fi from the environment." >&2
      exit 1
    fi
  fi

  for name in "${env_names[@]}"; do
    if env_is_set_and_nonempty "$name"; then
      env_args+=(--env "$name")
      override_names+=("$name")
    fi
  done

  if [[ "${#env_args[@]}" == "0" ]]; then
    return 1
  fi

  python3 "$ROOT/scripts/render_device_secrets.py" \
    --input "$source_file" \
    --output "$rendered_file" \
    "${env_args[@]}"

  echo "Rendered temporary secrets.py with environment overrides: ${override_names[*]}"
  return 0
}

for file in "$ROOT"/device/*.py; do
  name="$(basename "$file")"
  if [[ "$name" == "secrets.py" || "$name" == "secrets.example.py" ]]; then
    continue
  fi
  echo "Copying device/$name -> :$name"
  "${MPREMOTE_CMD[@]}" connect "$PORT" fs cp "$file" ":$name"
done

if [[ "$COPY_SECRETS" == "1" ]]; then
  secrets_file="${DEVICE_SECRETS_FILE:-$ROOT/device/secrets.py}"
  using_example_secrets=0
  if [[ ! -f "$secrets_file" ]]; then
    if env_is_set_and_nonempty WIFI_SSID || env_is_set_and_nonempty WIFI_PASSWORD; then
      secrets_file="$ROOT/device/secrets.example.py"
      using_example_secrets=1
    fi
  fi
  if [[ ! -f "$secrets_file" ]]; then
    echo "Cannot copy secrets: device/secrets.py does not exist." >&2
    exit 1
  fi
  if [[ "$using_example_secrets" == "1" ]]; then
    if ! env_is_set_and_nonempty STATE_URL && ! env_is_set_and_nonempty OTA_MANIFEST_URL; then
      echo "Cannot generate complete secrets from device/secrets.example.py: set STATE_URL or OTA_MANIFEST_URL." >&2
      exit 1
    fi
  fi

  upload_secrets_file="$secrets_file"
  tmp_secrets_file=""
  if env_is_set_and_nonempty WIFI_SSID || env_is_set_and_nonempty WIFI_PASSWORD; then
    tmp_secrets_dir="$(mktemp -d)"
    trap cleanup_tmp_secrets EXIT
    tmp_secrets_file="$tmp_secrets_dir/secrets.py"
    render_secrets_from_env "$secrets_file" "$tmp_secrets_file"
    upload_secrets_file="$tmp_secrets_file"
  fi

  echo "Copying secrets.py -> :secrets.py"
  "${MPREMOTE_CMD[@]}" connect "$PORT" fs cp "$upload_secrets_file" ":secrets.py"
  cleanup_tmp_secrets
  trap - EXIT
else
  echo "Preserving on-device secrets.py (use --with-secrets to provision over USB)"
fi

echo "Resetting board"
"${MPREMOTE_CMD[@]}" connect "$PORT" reset
