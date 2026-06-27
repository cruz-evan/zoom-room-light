# Zoom Room Light

A Zoom room status indicator for Raspberry Pi Pico W LED strips.

The supported runtime is intentionally small:

- `cloudflare-worker/` receives Zoom webhooks, polls Microsoft calendar events
  from Cloudflare Cron, stores the current room state in Workers KV, and exposes
  the Pico polling endpoint.
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
GET  /simulate/off
GET  /simulate/upcoming?minutes=5
GET  /simulate/starting-soon?minutes=5
GET  /simulate/ending-soon?minutes=5
POST /simulate/reset
```

## Webhook Status Map

Zoom should point at only one webhook receiver:

```text
POST /zoom/webhook
```

Subscribe the Zoom app to at least:

```text
meeting.started
meeting.ended
```

The Worker maps webhook and schedule inputs to the reduced Pico command
contract:

| Source | Input | Pico command |
| --- | --- | --- |
| Zoom webhook | `meeting.started` | `{"mode":"meeting_status","state":"in_progress"}` |
| Zoom webhook | `meeting.ended` | `{"mode":"off"}` or `starting_soon` when the next scheduled meeting is within `EMPTY_ROOM_LOOKAHEAD_MINUTES` |
| Schedule poll | upcoming meeting inside `ACTIVE_MEETING_LOOKAHEAD_MINUTES` or `EMPTY_ROOM_LOOKAHEAD_MINUTES` | `{"mode":"meeting_status","state":"starting_soon","minutes":5}` or `minutes:15` |
| Schedule poll | active scheduled meeting | `{"mode":"meeting_status","state":"in_progress"}` |
| Schedule poll | active scheduled meeting inside `ENDING_SOON_MINUTES` | `{"mode":"meeting_status","state":"ending_soon","minutes":5}` |

At a high level, the Worker keeps persisted room state in Workers KV. With
`PICO_ROOM_ASSIGNMENTS`, each Pico gets its own `current-state:<device_id>` key;
without it, the Worker falls back to the legacy single `current-state` key. Zoom
webhooks are the real-time signal, and Microsoft Graph schedule polls are the
calendar fallback plus empty-room signal. The Pico does not decide meeting
state; it only polls `/device/state` and renders the persisted command.

Zoom state changes:

- `meeting.started` writes `meeting_status / in_progress`, sets
  `source: "zoom"`, records `last_event: "meeting.started"`, and marks
  `in_use: true`.
- `meeting.ended` normally writes `off`, sets `source: "zoom"`, records
  `last_event: "meeting.ended"`, and marks `in_use: false`.
- After `meeting.ended`, the Worker may immediately check Microsoft Graph. If a
  different meeting is starting soon, it can write `starting_soon` instead of
  staying `off`.
- Accepted Zoom `meeting.started` and `meeting.ended` webhooks also update
  persisted Zoom lifecycle fields such as `zoom_active`, `zoom_started_at`, and
  `zoom_ended_at`. These fields are used only by the Worker and are not part of
  the Pico polling response.
- Zoom events use `zoom_event_ts` stale-event protection so an older
  `meeting.started` cannot resurrect a meeting after a newer `meeting.ended`.

Cloudflare Cron runs the Microsoft Graph schedule check every minute. With
assigned Microsoft calendar users, the schedule check computes three signals for
each assigned room calendar:

- `upcoming`: a meeting starts inside the configured lookahead window.
- `active`: the current time is inside a scheduled meeting window.
- `ending`: an active scheduled meeting is inside `ENDING_SOON_MINUTES`.

Schedule state changes:

- no active Zoom state plus `upcoming` writes `meeting_status / starting_soon`
  with `last_event: "schedule.upcoming"`.
- no active Zoom state plus `active` writes `meeting_status / in_progress` with
  `last_event: "schedule.active"`.
- active state plus `ending` writes `meeting_status / ending_soon` with
  `last_event: "schedule.ending_soon"`.
- a schedule-driven meeting that reaches its scheduled end writes `off` with
  `last_event: "schedule.end_clear"` unless `zoom_active` is still true.
- if `zoom_active` is still true when the scheduled event ends, the Worker keeps
  the active/ending state for `SCHEDULE_END_CLEAR_GRACE_MINUTES`, then writes
  `off` with `last_event: "schedule.end_grace_clear"`.

One important priority rule: after an accepted Zoom `meeting.ended`, the
immediate Graph follow-up check can surface a future `starting_soon` meeting but
must not resurrect the current active Outlook window as `in_progress`.

The protected simulation routes set the same states directly:

| Route | Pico command |
| --- | --- |
| `/simulate/upcoming?minutes=5` or `/simulate/starting-soon?minutes=5` | `meeting_status / starting_soon` |
| `/simulate/start` | `meeting_status / in_progress` |
| `/simulate/ending-soon?minutes=5` | `meeting_status / ending_soon` |
| `/simulate/end`, `/simulate/off`, or `/simulate/reset` | `off` |

Legacy Python stub terminology, for older notes and logs:

| Stub status | Aliases | Pico command |
| --- | --- | --- |
| `starting-soon` | `upcoming` | `meeting_status / starting_soon` |
| `in-progress` | `busy` | `meeting_status / in_progress` |
| `ending-soon` | | `meeting_status / ending_soon` |
| `free` | `ended`, `reset` | `off` |

The old stub command words are available again as a compatibility CLI. For a
local Worker stub, run:

```bash
python3 zoom_room_stub.py serve --port 5050
```

Then drive it from another terminal:

```bash
python3 zoom_room_stub.py status starting-soon --starts-in 3
python3 zoom_room_stub.py status in-progress
python3 zoom_room_stub.py status ending-soon --ends-in 3
python3 zoom_room_stub.py status free
python3 zoom_room_stub.py scenario --step-seconds 8
```

Request an immediate OTA check from devices polling the Worker:

```bash
ZOOM_ROOM_RELAY_URL=$RELAY ADMIN_TOKEN=$ADMIN_TOKEN python3 zoom_room_stub.py ota force
```

Publish a fresh encrypted OTA Wi-Fi/config payload for devices to detect:

```bash
python3 zoom_room_stub.py ota config --repo cruz-evan/zoom-room-light --ref main
```

To drive the deployed Worker instead, set `ZOOM_ROOM_RELAY_URL` or pass
`--server`. Simulation commands require `ADMIN_TOKEN`,
`ZOOM_ROOM_ADMIN_TOKEN`, or `--admin-token`.

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
STATE_URL = "http://zoom-led-room-light.<your-subdomain>.workers.dev/device/state"
DEVICE_POLL_TOKEN = "same-low-privilege-poll-token-if-configured"
OTA_MANIFEST_URL = "http://zoom-led-room-light.<your-subdomain>.workers.dev/ota/manifest.json"
```

Deploy app files over USB while preserving board-local secrets:

```bash
./scripts/deploy_device.sh
```

Provision Wi-Fi over USB without hard-coding it in `device/secrets.py`:

```bash
WIFI_SSID="your-wifi-name" WIFI_PASSWORD="your-wifi-password" ./scripts/deploy_device.sh --with-secrets
```

The manual GitHub Actions workflow `Provision Pico over USB` uses repository
secrets named `WIFI_SSID` and `WIFI_PASSWORD` the same way when run on a
self-hosted runner connected to the Pico. Set repository variable `STATE_URL`
or `OTA_MANIFEST_URL` before running it.

For Wi-Fi password rotation, add repository secret `OTA_CONFIG_KEY` and
provision the same value to each Pico once over USB. The `Pico W OTA` workflow
then publishes encrypted `wifi-config.json` to GitHub Pages, and the Worker
serves it to the Pico through `/ota/wifi-config.json`; the Pages artifact does
not contain plaintext Wi-Fi credentials.

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
