# RP2 Zoom LEDs

MicroPython firmware and host-side Python tools for driving WS2812/NeoPixel LED strips from Zoom meeting state.

The first milestone does not require Zoom. Run the host simulator, send fake meeting states over USB serial, and verify that the Raspberry Pi Pico / Pico W changes the LED strip.

## Architecture

- The RP2 board runs MicroPython and controls LEDs only.
- The Pico W can poll the Cloudflare relay directly over Wi-Fi, so the installed light does not need a laptop.
- USB serial JSON-lines control remains available for local testing and emergency recovery.
- GitHub Pages can publish OTA app-file bundles for wireless Pico W updates.
- Zoom and Cloudflare secrets stay off the device. The Pico stores only Wi-Fi credentials, the relay `STATE_URL`, the optional low-privilege `DEVICE_TOKEN`, and optional OTA endpoint config in ignored `device/secrets.py`.
- One shared firmware tree can run on multiple boards. Board identity, room mapping, hostname, and LED wiring live in ignored per-board `device/secrets.py` files.
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

- `starting_soon`: cyan moving block with a short tail for an impending meeting.
- `in_progress`: full-strip slow blue pulse for an active meeting.
- `ending_soon`: amber pulse that can become more urgent as `minutes` approaches 0.

On boot, the Pico W runs a short startup sequence after network connectivity is
confirmed. The default sequence exercises `solid`, `pulse`, `meeting`,
`meeting_status`, and `off`, then restores the current room state fetched from
the relay. If `STATE_URL` is unavailable but OTA is enabled, a successful OTA
manifest check can also confirm connectivity. Configure this in
`device/config.py` with `STARTUP_SEQUENCE_ENABLED`,
`STARTUP_SEQUENCE_REQUIRES_NETWORK`, `STARTUP_SEQUENCE_STEP_MS`, and
`STARTUP_SEQUENCE_COMMANDS`.

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

Copy tracked app files to the board while preserving the board-local
`secrets.py`:

```bash
./scripts/deploy_device.sh
```

Provision or intentionally update `secrets.py` over USB only:

```bash
./scripts/deploy_device.sh --with-secrets
```

To avoid hard-coding Wi-Fi credentials in local `device/secrets.py`, provide
them as environment variables during provisioning. The deploy script renders a
temporary `secrets.py`, uploads it to the board, and leaves the local file
unchanged:

```bash
WIFI_SSID="your-wifi-name" \
WIFI_PASSWORD="your-wifi-password" \
./scripts/deploy_device.sh --with-secrets
```

`secrets.py` and `secrets.example.py` are never included in OTA bundles. The
default USB deploy also skips them so app updates do not accidentally overwrite
per-board Wi-Fi, token, identity, or hardware settings.

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

Create the local secrets file for non-secret per-board settings:

```bash
cp device/secrets.example.py device/secrets.py
```

Fill in. If you provision with `WIFI_SSID` and `WIFI_PASSWORD` environment
variables, those two local values can stay as placeholders because the USB
deploy script injects the real values into a temporary upload:

```python
WIFI_SSID = "your-wifi-name"
WIFI_PASSWORD = "your-wifi-password"
DEVICE_ID = "auto"
ROOM_ID = "zoom-room-a"
DEVICE_HOSTNAME = "zoom-light-board-room-a"
DEVICE_HOSTNAME_PREFIX = "zoom-light"
DEVICE_HARDWARE = {
    "board-room-a": {"led_pin": 0, "led_count": 144},
    "board-room-b": {"led_pin": 2, "led_count": 144},
    "board-room-c": {"led_pin": 4, "led_count": 144},
    "board-room-d": {"led_pin": 6, "led_count": 144},
}
STATE_URL = "http://YOUR_LAPTOP_LAN_IP:5050/device/state"
DEVICE_TOKEN = ""
```

Use `DEVICE_TOKEN` only as a low-privilege device token for the relay. Do not put
Zoom OAuth credentials or the Zoom webhook secret on the Pico W.

When `device/secrets.py` is missing or incomplete, network mode stays disabled
and the board behaves as the original USB-serial LED controller.

## Multi-Board Config

Use `DEVICE_ID` as the stable board identity. Do not use IP address to decide
which Zoom Room a board belongs to. The intended mapping is:

```text
DEVICE_ID -> board hardware config -> Zoom Room mapping -> current LED state
```

Tracked defaults stay in `device/config.py`:

```python
LED_COUNT = 144
BRIGHTNESS = 0.12
LED_MAX_REFRESH_FPS = 30
STATE_POLL_SECONDS = 5
```

Per-board differences stay in ignored `device/secrets.py` on each board:

```python
DEVICE_ID = "auto"
ROOM_ID = "zoom-room-a"
DEVICE_HOSTNAME = "zoom-light-board-room-a"
DEVICE_HOSTNAME_PREFIX = "zoom-light"
DEVICE_HARDWARE = {
    "board-room-a": {"led_pin": 0, "led_count": 144},
    "board-room-b": {"led_pin": 2, "led_count": 144},
    "board-room-c": {"led_pin": 4, "led_count": 144},
    "board-room-d": {"led_pin": 6, "led_count": 144},
}

WIFI_SSID = "..."
WIFI_PASSWORD = "..."
STATE_URL = "https://your-relay.example.com/device/state"
DEVICE_TOKEN = "..."

TELEMETRY_DEVICE_ID = DEVICE_ID
```

`DEVICE_HARDWARE` is keyed by `DEVICE_ID`; the selected entry supplies
`LED_PIN`, `LED_COUNT`, and optional `BRIGHTNESS`. Direct `LED_PIN` and
`LED_COUNT` values in `device/secrets.py` still work for a one-board setup, but
the map is preferred for four boards because it makes the `DEVICE_ID -> hardware`
relationship explicit.

`DEVICE_ID = "auto"` derives a stable board identity from
`machine.unique_id()`, for example `pico-e66430a64b2a8d32`. Legacy placeholder
values `pico-w` and `zoom-led-pico` are also treated as automatic IDs. Set an
explicit `DEVICE_ID` such as `board-room-a` after assigning a board to a room, or
use the generated `pico-...` ID as the permanent identity.

When `STATE_URL` does not already include `device_id`, firmware appends the
resolved `DEVICE_ID` automatically:

```text
https://your-relay.example.com/device/state?device_id=pico-e66430a64b2a8d32
```

`DEVICE_ID`, `ROOM_ID`, `DEVICE_HOSTNAME`, `DEVICE_HOSTNAME_PREFIX`,
`DEVICE_HARDWARE`, and `TELEMETRY_DEVICE_ID` are overrideable from
`device/secrets.py`. This lets the same tracked firmware run on all four boards
while each board keeps its own LED pin and room assignment.

`DEVICE_HOSTNAME = "auto"` derives a unique hostname from the Wi-Fi MAC address
suffix, using `DEVICE_HOSTNAME_PREFIX`. For example, a board whose Wi-Fi MAC
ends in `dd:ee:ff` becomes:

```text
zoom-light-ddeeff.local
```

You can also use a template:

```python
DEVICE_HOSTNAME = "zoom-light-{mac}"
```

Use a MAC-derived hostname when you need uniqueness before assigning a room.
Use an explicit room-friendly hostname like `zoom-light-board-room-a` once the
board is installed in a known room.

`devices.example.json` is a tracked inventory template for four boards. It is
documentation/sample config only; do not put private tokens or Zoom OAuth
credentials in it. IP addresses in that file are operational metadata for
deployment, WebREPL, telemetry, and troubleshooting. They are not identity.

When DHCP reservations are unavailable, prefer a stable hostname per board and
resolve it with DNS/mDNS:

```bash
mpremote connect zoom-light-board-room-a.local exec "import sys; print(sys.implementation)"
mpremote connect zoom-light-ddeeff.local exec "import sys; print(sys.implementation)"
```

For WebREPL or other deployment tooling, use names like:

```text
zoom-light-board-room-a.local
zoom-light-board-room-b.local
zoom-light-board-room-c.local
zoom-light-board-room-d.local
```

If hostname lookup is unavailable, run the telemetry listener and use telemetry
as a fallback `DEVICE_ID -> current IP` discovery source. Firmware boot and
Wi-Fi events include the stable `DEVICE_ID`; Wi-Fi connection telemetry includes
the current LAN IP.

The current relay still returns the same single reduced LED state for all
boards. It now accepts both future addressing forms as extension points:

```text
/device/state?device_id=board-room-a
/device/board-room-a/state
```

Future relay routing should use `DEVICE_ID` to find `ROOM_ID` or the Zoom Room
mapping and return only that room's LED command.

Observed USB board during this implementation:

```text
port: /dev/cu.usbmodem1101
usb_vendor: MicroPython
usb_product: Board in FS mode
usb_vid: 0x2e8a
usb_pid: 0x0005
usb_serial: e66430a64b2a8d32
tracked_default_led_pin: 0
tracked_default_led_count: 144
```

`mpremote` could not read the live on-board config because the serial port was
not available for opening during the probe. The inventory records the USB-visible
hardware facts and the tracked firmware defaults; confirm any physical LED pin
differences in each board's local `device/secrets.py`.

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
TELEMETRY_DEVICE_ID = DEVICE_ID
RESOURCE_MONITOR_ENABLED = True
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

To focus on CPU and memory pressure, watch only resource samples:

```bash
python3 host/telemetry_listener.py --event resource_sample --out logs/device-resources.jsonl
```

The resource monitor emits `resource_sample` events every
`RESOURCE_MONITOR_SAMPLE_SECONDS` seconds. Each sample includes heap free/used
bytes, estimated main-loop CPU busy percent, peak loop busy percent,
over-budget loop counts, CPU clock speed, and Pico CPU temperature when
available. The CPU number is an estimate based on loop active time versus the
configured `LOOP_DELAY_MS`; desktop-style per-process CPU usage is not exposed
by MicroPython on the Pico W, so the over-budget loop fields are the best signal
for "we are not keeping up."

Useful knobs in `device/secrets.py`:

```python
RESOURCE_MONITOR_ENABLED = TELEMETRY_ENABLED
RESOURCE_MONITOR_SAMPLE_SECONDS = 10
RESOURCE_MONITOR_CPU_WARN_PERCENT = 80
RESOURCE_MONITOR_MIN_FREE_BYTES = 24000
RESOURCE_MONITOR_GC_COLLECT = False
RESOURCE_MONITOR_INCLUDE_TEMP = True
```

## GitHub OTA Updates

OTA updates are for MicroPython app files in `device/*.py`. They do not update
the MicroPython UF2 runtime and they deliberately exclude `device/secrets.py`
and `device/secrets.example.py`.

GitHub repository secrets named `WIFI_SSID` and `WIFI_PASSWORD` can be used by
the manual `Provision Pico over USB` workflow and by the encrypted Wi-Fi config
OTA artifact. That USB workflow must run on a self-hosted runner with the Pico
connected over USB; GitHub-hosted runners cannot access your local board. Set at
least one repository variable, `STATE_URL` or `OTA_MANIFEST_URL`, so the
workflow does not upload placeholder network config from `secrets.example.py`.
Optional secrets `DEVICE_TOKEN` and `OTA_TOKEN` are also injected when present.

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
OTA_CONFIG_KEY = "long-random-shared-key"
```

Leave `OTA_TOKEN` blank when using public GitHub Pages. Only set it if you route
OTA through a protected endpoint that expects a low-privilege bearer token.
`OTA_CONFIG_KEY` is different: it is a local decryption key for encrypted Wi-Fi
config. Generate it once, store the same value as the GitHub repository secret
`OTA_CONFIG_KEY`, and provision it to each board over USB.

Then deploy over USB once:

```bash
WIFI_SSID="your-wifi-name" WIFI_PASSWORD="your-wifi-password" ./scripts/deploy_device.sh --with-secrets
```

After that, normal app changes are wireless:

1. Edit non-secret files in `device/`.
2. Commit and push to `main`.
3. GitHub Actions runs tests, builds `manifest.json`, and publishes the OTA site
   with GitHub Pages.
4. The Pico checks `OTA_MANIFEST_URL` every `OTA_CHECK_SECONDS` seconds
   (default: 60), downloads changed files, verifies SHA-256 and size, commits
   the update, then resets. On the same cadence it also checks
   `wifi-config.json`, decrypts it with `OTA_CONFIG_KEY`, writes
   `wifi_profiles.json` when changed, and resets.

## Encrypted Wi-Fi Config OTA

The Pages site is public, so Wi-Fi credentials are never published in plain
text. The OTA workflow writes `_site/wifi-config.json` containing only nonce,
ciphertext, and authentication tag. The Pico decrypts it with the
board-local `OTA_CONFIG_KEY`.

Required GitHub repository secret:

```text
OTA_CONFIG_KEY      same long random value provisioned on every Pico
```

At least one complete Wi-Fi secret pair is required for the workflow to publish
`wifi-config.json`. Recommended office-plus-hotspot setup:

```text
OFFICE_WIFI_SSID
OFFICE_WIFI_PASSWORD
PHONE_HOTSPOT_SSID
PHONE_HOTSPOT_PASSWORD
```

The existing generic pair is still supported and works as the primary profile
when no explicit office pair is set:

```text
WIFI_SSID
WIFI_PASSWORD
WIFI_FALLBACK_SSID
WIFI_FALLBACK_PASSWORD
```

These phone-hotspot fallback names are also supported:

```text
FALLBACK_PHONE_HOTSPOT_WIFI_SSID
FALLBACK_PHONE_HOTSPOT_WIFI_PASSWORD
```

The Pico tries saved encrypted OTA profiles first, in workflow order, then
falls back to the bootstrap `WIFI_SSID` and `WIFI_PASSWORD` in `secrets.py`.
Changing a GitHub secret does not automatically start a workflow. After changing
a Wi-Fi secret, manually run `Pico W OTA`; the config payload version includes
the workflow run ID so a rerun publishes a new encrypted payload even when app
code did not change.

Office password recovery flow:

1. Turn on a hotspot already present in the Pico's saved profile list.
2. Update the office Wi-Fi GitHub secret pair.
3. Manually run the `Pico W OTA` workflow on `main`.
4. Wait for the Pico to poll OTA over the hotspot, decrypt the new profile, and
   reset.
5. Turn off the hotspot after the Pico reconnects to office Wi-Fi.

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
- Max LED refresh: `30 fps`

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
