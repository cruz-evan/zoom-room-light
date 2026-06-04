# RP2 Zoom LEDs

MicroPython firmware and host-side Python tools for driving WS2812/NeoPixel LED strips from Zoom meeting state.

The first milestone does not require Zoom. Run the host simulator, send fake meeting states over USB serial, and verify that the Raspberry Pi Pico / Pico W changes the LED strip.

## Architecture

- The RP2 board runs MicroPython and controls LEDs only.
- The Pico W can poll the Cloudflare relay directly over Wi-Fi, so the installed light does not need a laptop.
- USB serial JSON-lines control remains available for local testing and emergency recovery.
- GitHub Pages can publish OTA app-file bundles for wireless Pico W updates.
- Zoom and Cloudflare secrets stay off the device. The Pico stores only Wi-Fi credentials, the relay `STATE_URL`, the optional low-privilege `DEVICE_TOKEN`, and optional OTA endpoint config in ignored `device/secrets.py`.
- `mpremote` handles REPL access, file deployment, and basic device checks.

Supported serial commands:

```json
{"mode":"solid","rgb":[255,0,0]}
{"mode":"off"}
{"mode":"pulse","rgb":[0,120,255],"speed":0.6}
{"mode":"meeting","active":true,"participants":3}
{"mode":"meeting_status","state":"starting_soon","minutes":5}
{"mode":"meeting_status","state":"in_progress"}
{"mode":"meeting_status","state":"ending_soon","minutes":5}
```

The newer `meeting_status` mode is the preferred final-product contract. The host/Zoom side decides whether a meeting is within the 5 minute start/end window, then the RP2 renders the behavior:

- `starting_soon`: blue moving cue for an impending meeting.
- `in_progress`: calm green/teal in-meeting indication.
- `ending_soon`: amber pulse that can become more urgent as `minutes` approaches 0.

## IntelliJ Setup

1. Open the `rp2-zoom-leds` folder in IntelliJ IDEA.
2. Make sure the Python plugin is installed and enabled.
3. Create a local virtual environment:

   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r requirements-dev.txt
   ```

   If `python3.11` is not installed, use your local Python 3.11+ executable.

4. Select `.venv` as the project interpreter.
5. Use the checked-in run configurations:
   - `Host simulator`
   - `Host Zoom bridge`
   - `Pytest`

The `.idea/externalTools.xml` file also adds MPRemote helpers for REPL, copying `main.py`, and deploying device files.

## Flash MicroPython

1. Download the correct MicroPython UF2 for your board:
   - Raspberry Pi Pico
   - Raspberry Pi Pico W
   - Pico 2 / Pico 2 W if you are using RP2350 hardware
2. Hold the Pico `BOOTSEL` button while connecting USB.
3. Release `BOOTSEL` when the board mounts as `RPI-RP2` or `RP2350`.
4. Drag the UF2 file onto the mounted drive.
5. The board reboots into MicroPython.

## Verify The RP2

From the project root:

```bash
mpremote connect auto exec "import sys; print(sys.implementation)"
```

On macOS the serial port is usually something like `/dev/cu.usbmodem1101`. The host scripts use `auto` by default, but you can pass `--port /dev/cu.usbmodemXXXX` if auto-detection picks the wrong device.

## Deploy Device Code

Copy all MicroPython files to the board:

```bash
./scripts/deploy_device.sh
```

Open the MicroPython REPL:

```bash
./scripts/repl.sh
```

Copy only `main.py` manually if needed:

```bash
mpremote connect auto fs cp device/main.py :main.py
```

## Pico W Network Mode

The firmware still accepts USB serial JSON-lines, and it can also poll a relay
over Wi-Fi when `device/secrets.py` is present locally. `device/secrets.py` is
ignored by git and must stay local/private.

Create the local secrets file:

```bash
cp device/secrets.example.py device/secrets.py
```

Fill in:

```python
WIFI_SSID = "your-wifi-name"
WIFI_PASSWORD = "your-wifi-password"
STATE_URL = "http://YOUR_LAPTOP_LAN_IP:5050/device/state"
DEVICE_TOKEN = ""
```

Use `DEVICE_TOKEN` only as a low-privilege device token for the relay. Do not put
Zoom OAuth credentials or the Zoom webhook secret on the Pico W.

When `device/secrets.py` is missing or incomplete, network mode stays disabled
and the board behaves as the original USB-serial LED controller.

## On-Device Telemetry

For debugging installed behavior without a USB tether, the Pico W can stream
compact UDP JSON logs over Wi-Fi while powered from the wall. This is useful for
state lag, missed transitions, Wi-Fi reconnects, and command application timing.

On the laptop, start a listener:

```bash
python3 host/telemetry_listener.py --out logs/device-telemetry.jsonl
```

In local `device/secrets.py`, enable telemetry:

```python
TELEMETRY_ENABLED = True
TELEMETRY_HOST = "255.255.255.255"
TELEMETRY_PORT = 9977
TELEMETRY_DEVICE_ID = "zoom-led-pico"
```

Broadcast is convenient because the Pico does not need to know your laptop IP.
If your network drops broadcast traffic, set `TELEMETRY_HOST` to the laptop's
LAN IP instead, for example `192.168.1.42`.

After changing `device/secrets.py`, deploy once over USB:

```bash
./scripts/deploy_device.sh
```

Once deployed, the board can run on its normal power supply. Keep the listener
running on the laptop to capture live logs.

## GitHub OTA Updates

OTA updates are for MicroPython app files in `device/*.py`. They do not update
the MicroPython UF2 runtime and they deliberately exclude `device/secrets.py`
and `device/secrets.example.py`.

First-time OTA provisioning still needs USB because the board must already have
`ota_client.py`, the updated `main.py`, and local secrets:

```bash
cp device/secrets.example.py device/secrets.py
```

Set the relay fields and the GitHub Pages manifest URL in `device/secrets.py`:

```python
STATE_URL = "https://zoom-led-room-light.connor-zoom-led-room-light.workers.dev/device/state"
DEVICE_TOKEN = ""
OTA_MANIFEST_URL = "https://cruz-evan.github.io/zoom-room-light/manifest.json"
OTA_TOKEN = ""
```

Leave `OTA_TOKEN` blank when using public GitHub Pages. Only set it if you route
OTA through a protected endpoint that expects a low-privilege bearer token.

Then deploy over USB once:

```bash
./scripts/deploy_device.sh
```

After that, normal app changes are wireless:

1. Edit non-secret files in `device/`.
2. Commit and push to `main`.
3. GitHub Actions runs tests, builds `manifest.json`, and publishes the OTA site
   with GitHub Pages.
4. The Pico checks `OTA_MANIFEST_URL` every `OTA_CHECK_SECONDS` seconds
   (default: 300), downloads changed files, verifies SHA-256 and size, commits
   the update, then resets.

One-time GitHub repo setup: an admin for `cruz-evan/zoom-room-light` must enable
Pages with GitHub Actions as the build source. From an admin-authenticated `gh`
CLI:

```bash
gh api -X POST repos/cruz-evan/zoom-room-light/pages -f build_type=workflow
```

Then rerun the OTA workflow:

```bash
gh workflow run pico-ota.yml --repo cruz-evan/zoom-room-light --ref main
```

The workflow lives at
`.github/workflows/pico-ota.yml` in the repository root and runs its commands
from `rp2-zoom-leds`.

This repo's OTA base URL is:

```text
https://cruz-evan.github.io/zoom-room-light
```

The repository variable `OTA_BASE_URL` should be set to that public base URL.
The Pico's `OTA_MANIFEST_URL` should be that base URL plus `/manifest.json`.

Build the OTA site locally without publishing:

```bash
python scripts/build_ota_site.py \
  --base-url "https://example.com/rp2-zoom-leds" \
  --version "local-test" \
  --output build/ota-site
```

USB remains the recovery path. If a bad app update prevents booting or network
updates, plug the Pico back in and run:

```bash
./scripts/deploy_device.sh
```

## Run The Simulator

With the virtual environment active:

```bash
python host/simulate_zoom.py --port auto --interval 2
```

This cycles through fake idle, waiting, active, busy, and off states. Use this before configuring Zoom.
The current simulator focuses on the three product states: starting soon, in progress, and ending soon.

Dry-run without hardware:

```bash
python host/simulate_zoom.py --dry-run --loops 1
```

## Zoom Bridge

Copy the example env file and fill in host-side credentials:

```bash
cp .env.example .env
```

Then run:

```bash
python host/zoom_client.py --port auto
```

The bridge uses Zoom Server-to-Server OAuth from environment variables:

- `ZOOM_ACCOUNT_ID`
- `ZOOM_CLIENT_ID`
- `ZOOM_CLIENT_SECRET`
- `ZOOM_USER_ID` or `ZOOM_MEETING_ID`
- `ZOOM_POLL_INTERVAL_SECONDS`

Polling is intentionally host-side. The RP2 receives only simplified lighting commands, so credentials and OAuth tokens never touch the device.

Zoom endpoint availability depends on your account type and app scopes. The current bridge includes basic `429` handling and uses a configurable polling interval; for production, prefer Zoom webhooks where possible to reduce polling.

## Hardware Wiring

- LED strip data input to Pico `GP0`.
- Pico `GND` and LED power supply `GND` must be common.
- Use an external 5V supply for longer strips; do not power long strips from the Pico.
- Add a 330 ohm resistor in series with the data line if available.
- Add a large capacitor across LED power rails if available.
- For long strips or noisy setups, use a 3.3V-to-5V level shifter on the data line.

Default firmware settings live in `device/config.py`:

- LED data pin: `GP0`
- LED count: `144`
- Brightness: `0.12`

The firmware blinks the onboard LED if NeoPixel setup or writes fail. A physically disconnected WS2812 strip may not be detectable by software, so use the simulator and visible LED output as the hardware acceptance test.

## 144 LED Strip Testing

A 1m strip with 144 WS2812/NeoPixel LEDs is fine for testing, but it needs more power discipline than a short 30 LED strip.

Worst-case full white is roughly:

```text
144 LEDs * 60mA = 8.64A at 5V
```

The firmware defaults to `BRIGHTNESS = 0.12`, so early tests stay intentionally dim. Keep brightness low until the strip has a proper external 5V supply. A 5V 10A supply is a comfortable target for using the full strip brightly; a smaller supply is fine for dim testing.

Good first commands:

```bash
python host/serial_bridge.py --mode solid --rgb 255,0,0
python host/serial_bridge.py --mode solid --rgb 0,255,0
python host/serial_bridge.py --mode solid --rgb 0,0,255
python host/serial_bridge.py --mode off
```

Avoid `255,255,255` while bench testing unless the strip is on an adequate external supply.

## Tests

```bash
pytest
```

Acceptance target:

- The host simulator runs from IntelliJ.
- The RP2 receives serial commands.
- The LED strip changes color for fake Zoom states.
- No Zoom credentials are stored on the device.
