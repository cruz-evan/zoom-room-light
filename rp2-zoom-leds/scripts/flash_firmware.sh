#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/micropython.uf2" >&2
  exit 64
fi

UF2="$1"

if [[ ! -f "$UF2" ]]; then
  echo "UF2 file not found: $UF2" >&2
  exit 66
fi

case "$UF2" in
  *.uf2|*.UF2) ;;
  *)
    echo "Expected a .uf2 firmware file: $UF2" >&2
    exit 65
    ;;
esac

MOUNT=""
for candidate in /Volumes/RPI-RP2 /Volumes/RP2350; do
  if [[ -d "$candidate" ]]; then
    MOUNT="$candidate"
    break
  fi
done

if [[ -z "$MOUNT" ]]; then
  echo "Pico boot volume not found. Hold BOOTSEL while connecting USB, then retry." >&2
  exit 69
fi

echo "Copying $(basename "$UF2") -> $MOUNT"
cp "$UF2" "$MOUNT/"
sync
echo "Firmware copied. The board should reboot into MicroPython."

