# Cloudflare Worker Relay

Cloud relay for the Pico W polling mode. Zoom webhook secrets live in
Cloudflare Worker secrets; the Pico stores only Wi-Fi credentials, the
`STATE_URL`, and optionally a low-privilege `DEVICE_TOKEN`.

Current deployment:

```text
https://zoom-led-room-light.connor-zoom-led-room-light.workers.dev
```

## Routes

```text
POST /zoom/webhook       Zoom webhook receiver
GET  /device/state       Pico polling endpoint
GET  /health             basic health check
POST /schedule/check     protected immediate schedule poll
GET  /simulate/start     protected demo state: in_progress
GET  /simulate/end       protected demo state: off
GET  /simulate/upcoming  protected demo state: starting_soon
GET  /simulate/ending-soon?minutes=5
POST /simulate/reset     protected demo state: off
```

`/device/state` returns the same reduced command contract as the local relay:

```json
{
  "v": 1,
  "command": { "mode": "meeting_status", "state": "in_progress" },
  "poll_seconds": 5,
  "updated_at": "2026-06-04T00:00:00.000Z",
  "last_event": "meeting.started"
}
```

## One-time Cloudflare Setup

Install dependencies:

```bash
cd /Users/connor/Documents/Hackathon/zoom-room-light/cloudflare-worker
npm install
```

Log in if Wrangler has not been authorized on this machine:

```bash
npx wrangler login
```

Create a KV namespace for persisted room state:

```bash
npx wrangler kv namespace create STATE_KV
npx wrangler kv namespace create STATE_KV --preview
```

Copy the generated `id` values into `wrangler.toml`.

Set Worker secrets. Use the same Zoom webhook secret token shown in the Zoom
app, and make `DEVICE_TOKEN` a random low-privilege value if you want the Pico
polling endpoint protected.

```bash
npx wrangler secret put ZOOM_WEBHOOK_SECRET_TOKEN
npx wrangler secret put ZOOM_ACCOUNT_ID
npx wrangler secret put ZOOM_CLIENT_ID
npx wrangler secret put ZOOM_CLIENT_SECRET
npx wrangler secret put ZOOM_SCHEDULE_USER_ID
npx wrangler secret put DEVICE_TOKEN
npx wrangler secret put ADMIN_TOKEN
```

`ADMIN_TOKEN` protects the simulate endpoints and `/schedule/check`. If it is
unset, those routes are disabled.

The scheduled poller runs every minute from the Cron Trigger in `wrangler.toml`.
It uses Zoom Server-to-Server OAuth to list the configured user's scheduled and
upcoming meetings, then emits:

```text
starting_soon  when the next meeting starts within SCHEDULE_LOOKAHEAD_MINUTES
ending_soon    when the active scheduled meeting ends within ENDING_SOON_MINUTES
in_progress    while Zoom says the current meeting is running
off            after meeting.ended or when schedule warnings clear
```

The Zoom app needs meeting read scopes for schedule polling, such as
`meeting:read:admin` or the granular list-meetings/list-upcoming-meetings admin
scopes shown by Zoom for the meetings APIs.

## Deploy

```bash
npm test
npm run deploy
```

The deploy output prints the Worker URL, usually:

```text
https://zoom-led-room-light.<your-subdomain>.workers.dev
```

For this deployment, use this Zoom webhook URL:

```text
https://zoom-led-room-light.connor-zoom-led-room-light.workers.dev/zoom/webhook
```

Subscribe to at least:

```text
meeting.started
meeting.ended
```

The Worker supports Zoom `endpoint.url_validation` and verifies
`x-zm-signature` for normal Zoom events.

Real Zoom webhooks drive `meeting.started` and `meeting.ended`. Cloudflare Cron
drives `starting_soon` and `ending_soon` from the Zoom schedule API. Cron never
turns an active meeting off; `meeting.ended` is the source of truth for that
transition.

## Verify Without Zoom

Replace `$RELAY` and `$ADMIN_TOKEN` locally:

```bash
RELAY=https://zoom-led-room-light.<your-subdomain>.workers.dev
ADMIN_TOKEN=...
DEVICE_TOKEN=...
```

Simulate each Pico command:

```bash
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" "$RELAY/simulate/upcoming?minutes=5"
curl -sS -H "Authorization: Bearer $DEVICE_TOKEN" "$RELAY/device/state"

curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" "$RELAY/simulate/start"
curl -sS -H "Authorization: Bearer $DEVICE_TOKEN" "$RELAY/device/state"

curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" "$RELAY/simulate/ending-soon?minutes=5"
curl -sS -H "Authorization: Bearer $DEVICE_TOKEN" "$RELAY/device/state"

curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" "$RELAY/simulate/end"
curl -sS -H "Authorization: Bearer $DEVICE_TOKEN" "$RELAY/device/state"
```

If `DEVICE_TOKEN` is unset in Cloudflare, omit the `Authorization` header when
reading `/device/state`.

Force one schedule poll:

```bash
curl -sS -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$RELAY/schedule/check"
curl -sS -H "Authorization: Bearer $DEVICE_TOKEN" "$RELAY/device/state"
```

## Update The Pico

In `/Users/connor/Documents/Hackathon/rp2-zoom-leds/device/secrets.py`, set:

```python
STATE_URL = "https://zoom-led-room-light.<your-subdomain>.workers.dev/device/state"
DEVICE_TOKEN = "same-low-privilege-device-token-if-configured"
```

Then redeploy:

```bash
cd /Users/connor/Documents/Hackathon/rp2-zoom-leds
./scripts/deploy_device.sh
```

Do not put Zoom OAuth credentials or the Zoom webhook secret in
`device/secrets.py`.
