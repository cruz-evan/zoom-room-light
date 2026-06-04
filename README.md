# Zoom Room Light

A hackathon-friendly room status light for Zoom, configured for the
`rp2-zoom-leds` Raspberry Pi Pico / RP2040 LED strip firmware.

The laptop/server side receives Zoom webhooks through ngrok, polls the Zoom
schedule API, exposes a tiny live dashboard, and sends one JSON object per line
over USB serial to the Pico. Zoom OAuth credentials stay on the laptop. The Pico
only receives simplified LED commands and drives the WS2812/NeoPixel strip on
GP0.

## Light Priority

The project always resolves state in this order:

```text
ending soon  -> amber/orange
in use       -> active meeting
starts soon  -> upcoming meeting
free         -> off
error        -> dashboard warning
```

That means if a `meeting.ended` webhook arrives while another meeting starts
soon, the strip stays in `starting_soon` instead of turning off.

The serial command contract matches the Pico firmware:

```json
{"mode":"meeting_status","state":"starting_soon","minutes":5}
{"mode":"meeting_status","state":"in_progress"}
{"mode":"meeting_status","state":"ending_soon","minutes":5}
{"mode":"off"}
```

## Server Setup

Create a virtual environment and install the serial dependency:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in both Zoom app credential sets:

```bash
ZOOM_WEBHOOK_SECRET_TOKEN=...

ZOOM_ACCOUNT_ID=...
ZOOM_CLIENT_ID=...
ZOOM_CLIENT_SECRET=...
ZOOM_SCHEDULE_USER_ID=your.zoom.email@example.com

SCHEDULE_LOOKAHEAD_MINUTES=5
ENDING_SOON_MINUTES=5
SCHEDULE_POLL_SECONDS=60
PORT=5050

RP2_SERIAL_ENABLED=true
RP2_SERIAL_PORT=auto
RP2_SERIAL_BAUD=115200
RP2_SERIAL_DRY_RUN=false
DEVICE_TOKEN=
```

Use `RP2_SERIAL_PORT=auto` for the usual macOS Pico path. If multiple USB
serial devices are attached, set the exact port, for example
`/dev/cu.usbmodem1101`.

Run the server from this repo:

```bash
python3 zoom_light_webhook.py --port 5050
```

Expose it with ngrok:

```bash
ngrok http 5050
```

Use this Zoom webhook URL:

```text
https://YOUR-NGROK-URL/zoom/webhook
```

Subscribe to:

```text
meeting.started
meeting.ended
meeting.created
meeting.updated
meeting.deleted
meeting.participant_joined
meeting.participant_left
```

The schedule polling does not come from a webhook. It uses the Server-to-Server
OAuth app to poll Zoom and send `starting_soon` when a meeting starts within
`SCHEDULE_LOOKAHEAD_MINUTES`. If Zoom returns duration data for the active
scheduled meeting, the server sends `ending_soon` within `ENDING_SOON_MINUTES`.

## Local Routes

```text
/                 live dashboard
/state            current state JSON for hardware
/device/state     reduced LED command JSON for Pico W polling
/events           Server-Sent Events stream for browser dashboard
/schedule/check   force a schedule poll
/reset            reset to free
```

Demo routes:

```text
/simulate/start
/simulate/end
/simulate/join
/simulate/leave
/simulate/upcoming
/simulate/ending-soon
/simulate/clear-upcoming
```

## Pico / RP2040 Hardware

This repo includes the `rp2-zoom-leds` Pico firmware project:

```text
rp2-zoom-leds/
```

Use that project's device deploy scripts for the Pico. The expected hardware
state is:

```text
MicroPython v1.28.0
WS2812/NeoPixel data on GP0
LED_COUNT = 144
BRIGHTNESS = 0.12
RGB color order
```

To test the full host-to-Pico path without real Zoom events, start the server
and visit the demo routes in the dashboard:

```text
http://localhost:5050/
```

Or dry-run the JSON serial output without hardware:

```bash
RP2_SERIAL_ENABLED=true RP2_SERIAL_DRY_RUN=true python3 zoom_light_webhook.py --port 5050
```

Then click `Simulate Upcoming`, `Simulate Start`, `Simulate Ending Soon`, and
`Simulate End`.

## Legacy Wi-Fi Client

The original project also includes a polling MicroPython client in
[micropython_light/README.md](micropython_light/README.md). That path is not
needed for the Pico strip build because `rp2-zoom-leds` already owns the
hardware control and reads USB serial commands.

For that legacy client, `micropython_light/boot.py` can start WebREPL on the
Pico W after Wi-Fi connects. After the initial USB setup, deploy updates over
Wi-Fi and soft-reset MicroPython with:

```bash
WEBREPL_PASSWORD='use-a-real-password' ./scripts/deploy_micropython_webrepl.py 192.168.1.123
```

The script uploads the MicroPython files, sends `Ctrl-C` to stop the running
loop, and sends `Ctrl-D` to soft-reset the interpreter so the new files take
effect without a physical power cycle.

## Pico W Polling Mode

The `rp2-zoom-leds` firmware can also poll this server directly from a Pico W.
For local testing, copy `rp2-zoom-leds/device/secrets.example.py` to
`rp2-zoom-leds/device/secrets.py` and set:

```python
WIFI_SSID = "..."
WIFI_PASSWORD = "..."
STATE_URL = "http://YOUR_LAPTOP_LAN_IP:5050/device/state"
DEVICE_TOKEN = ""
```

If you set `DEVICE_TOKEN` in this server's `.env`, set the same value in the
Pico's `device/secrets.py`. The token only authorizes the board to read reduced
LED state; Zoom account secrets stay on the server or cloud relay.

## Cloud Relay

The laptop-free relay lives in
[`cloudflare-worker/`](cloudflare-worker/). It is a Cloudflare Worker that:

- receives `POST /zoom/webhook`,
- answers Zoom `endpoint.url_validation`,
- verifies Zoom `x-zm-signature` on normal webhook events,
- polls Zoom schedules from Cloudflare Cron for `starting_soon` and `ending_soon`,
- stores the current reduced Pico command in Workers KV,
- exposes `GET /device/state` for Pico W polling, and
- provides protected `/simulate/*` routes for testing without Zoom.

Follow [`cloudflare-worker/README.md`](cloudflare-worker/README.md) for the KV,
secret, deploy, Zoom, simulate, and Pico update steps.

## Checks

```bash
PYTHONPYCACHEPREFIX=/private/tmp/codex_pycache python3 -m py_compile zoom_light_webhook.py zoom_light/*.py zoom_schedule.py
python3 - <<'PY'
from zoom_light.serial_output import command_from_state, encode_command
for state in (
    {"next_meeting_id": "demo", "minutes_until_next": 5},
    {"in_use": True},
    {"in_use": True, "minutes_until_end": 5},
    {},
):
    print(encode_command(command_from_state(state)).strip())
PY
python3 micropython_light/test_priority.py
```

## Cloudflare Deployment

This repo includes an early Cloudflare Containers deployment setup:

```text
.github/workflows/deploy-cloudflare.yml
wrangler.jsonc
cloudflare/worker.ts
Dockerfile
```

The GitHub Action is safe to merge before Cloudflare is fully configured. It
skips deployment until these repository secrets exist:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
```

Add these GitHub repository secrets for the Zoom runtime config:

```text
ZOOM_WEBHOOK_SECRET_TOKEN
ZOOM_ACCOUNT_ID
ZOOM_CLIENT_ID
ZOOM_CLIENT_SECRET
ZOOM_SCHEDULE_USER_ID
SCHEDULE_LOOKAHEAD_MINUTES
SCHEDULE_POLL_SECONDS
```

Cloudflare notes:

- Use an API token with Workers edit/deploy permissions scoped to the target Cloudflare account.
- Cloudflare Containers deploy via Wrangler, which builds the `Dockerfile` image and pushes it to Cloudflare's managed registry.
- The Worker proxies requests to the Python container, so the public Cloudflare Worker URL replaces the ngrok URL once deployed.
- Update the Zoom webhook endpoint to:

```text
https://YOUR-CLOUDFLARE-WORKER-URL/zoom/webhook
```

Local validation:

```bash
npm install
npm run typecheck
```
