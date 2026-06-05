# Zoom Room Light

A Zoom room status indicator for Raspberry Pi Pico W LED strips.

The supported runtime is intentionally small:

- `cloudflare-worker/` receives Zoom webhooks, polls Zoom schedules from
  Cloudflare Cron, stores the current room state in Workers KV, and exposes the
  Pico polling endpoint.
- `rp2-zoom-leds/device/` runs on the Pico W, polls `/device/state`, maps the
  reduced command to LED behavior, and supports OTA app-file updates.
- `rp2-zoom-leds/host/` contains USB serial and telemetry tools for bench
  testing and recovery.

The old laptop Python webhook relay, early cloud wrapper, and
standalone legacy Wi-Fi client have been removed. Testing should use the
protected Worker simulation endpoints or the `rp2-zoom-leds` host tools.

## Light States

The relay always resolves state in this priority order:

```text
ending soon  -> amber/orange
in use       -> active meeting
starts soon  -> upcoming meeting
free         -> off
error        -> retry / no state change
```

The device polling contract is the same in production and tests:

```json
{
  "v": 1,
  "command": { "mode": "meeting_status", "state": "in_progress" },
  "poll_seconds": 5,
  "updated_at": "2026-06-04T00:00:00.000Z",
  "last_event": "meeting.started"
}
```

Supported commands:

```json
{"mode":"meeting_status","state":"starting_soon","minutes":5}
{"mode":"meeting_status","state":"in_progress"}
{"mode":"meeting_status","state":"ending_soon","minutes":5}
{"mode":"off"}
```

## Cloud Relay

Current deployment:

```text
https://zoom-led-room-light.connor-zoom-led-room-light.workers.dev
```

Worker setup and deploy docs live in
[`cloudflare-worker/README.md`](cloudflare-worker/README.md). The short path is:

```bash
cd cloudflare-worker
npm ci
npm test
npm run deploy
```

Production routes:

```text
POST /zoom/webhook       Zoom webhook receiver
GET  /device/state       Pico polling endpoint
GET  /device/state?device_id=board-room-a
GET  /device/board-room-a/state
GET  /health
POST /schedule/check     protected immediate schedule poll
```

Protected simulation routes remain available for status-indicator testing when
`ADMIN_TOKEN` is configured:

```text
GET  /simulate/start
GET  /simulate/end
GET  /simulate/upcoming?minutes=5
GET  /simulate/ending-soon?minutes=5
POST /simulate/reset
```

## Pico Firmware

Firmware setup, deployment, OTA, telemetry, and hardware testing docs live in
[`rp2-zoom-leds/README.md`](rp2-zoom-leds/README.md).

Typical local setup:

```bash
cd rp2-zoom-leds
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
PYTHONPATH=. pytest
```

Create local device secrets only on your machine:

```bash
cp device/secrets.example.py device/secrets.py
```

For production polling, set the Pico's `STATE_URL` to the Worker endpoint:

```python
STATE_URL = "https://zoom-led-room-light.<your-subdomain>.workers.dev/device/state"
DEVICE_TOKEN = "same-low-privilege-device-token-if-configured"
```

Deploy app files over USB while preserving board-local secrets:

```bash
./scripts/deploy_device.sh
```

## Verification

Run the supported test suites:

```bash
cd cloudflare-worker
npm test

cd ../rp2-zoom-leds
PYTHONPATH=. pytest
```

Dry-run the LED command simulator without hardware:

```bash
cd rp2-zoom-leds
python host/simulate_zoom.py --dry-run --loops 1
```

The Cloudflare deployment workflow at
`.github/workflows/deploy-cloudflare.yml` installs, tests, and deploys only
`cloudflare-worker/`. The OTA workflow at `.github/workflows/pico-ota.yml`
builds and publishes only `rp2-zoom-leds/device/` app files.
