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
app for live meeting started/ended events. Use Microsoft Graph application
credentials for schedule polling, and make `DEVICE_TOKEN` a random
low-privilege value if you want the Pico polling endpoint protected.

```bash
npx wrangler secret put ZOOM_WEBHOOK_SECRET_TOKEN
npx wrangler secret put MICROSOFT_TENANT_ID
npx wrangler secret put MICROSOFT_CLIENT_ID
npx wrangler secret put MICROSOFT_CLIENT_SECRET
npx wrangler secret put MICROSOFT_CALENDAR_USER_ID
npx wrangler secret put DEVICE_TOKEN
npx wrangler secret put ADMIN_TOKEN
```

`ADMIN_TOKEN` protects the simulate endpoints and `/schedule/check`. If it is
unset, those routes are disabled.

If the Zoom app receives org-wide meeting webhooks, set per-device topic filters
with `ZOOM_WEBHOOK_TOPIC_FILTERS` and identify the target device in the webhook
URL, for example `/zoom/board-room-a/webhook` or
`/zoom/webhook?device_id=board-room-a`.

```text
ZOOM_WEBHOOK_TOPIC_FILTERS={"board-room-a":["Board Room A"],"board-room-b":["Board Room B"]}
```

All valid Zoom webhooks are still retained in seven-day KV history. Only matching
topics update the live room state. If `ZOOM_WEBHOOK_TOPIC_FILTERS`,
`ZOOM_WEBHOOK_TOPIC_FILTER`, or a specific device entry is missing, blank, or
null, no topic filtering is applied for that request. `ZOOM_WEBHOOK_TOPIC_FILTER`
is still supported as a global fallback.

The scheduled poller runs every minute from the Cron Trigger in `wrangler.toml`.
It uses Microsoft Graph client-credentials auth to read the configured calendar
user's `calendarView`. The Worker caches only compact schedule timing metadata
in KV state: meeting IDs/topics plus start/end timestamps. The Pico does not
receive or cache the schedule list; it still receives only the current command.
The poller then emits:

```text
starting_soon  when the next meeting starts within ACTIVE_MEETING_LOOKAHEAD_MINUTES during an active meeting,
               or EMPTY_ROOM_LOOKAHEAD_MINUTES when the room is empty
ending_soon    when the active scheduled meeting ends within ENDING_SOON_MINUTES
in_progress    while Zoom says the current meeting is running
off            after meeting.ended when no next meeting is inside the empty-room window,
               when schedule warnings clear,
               when a Zoom-active meeting is still active SCHEDULE_END_CLEAR_GRACE_MINUTES
               after the cached scheduled end,
               or at the cached scheduled end if Zoom never sent meeting.started
```

Set `SCHEDULE_END_CLEAR_GRACE_MINUTES` as a GitHub Actions repository variable
and Cloudflare Worker variable. It is not secret. The default is `5`.

The Microsoft app registration needs application permission to read the target
calendar, for example `Calendars.Read`, with admin consent granted. Prefer an
application access policy or equivalent tenant restriction so the app can read
only the room calendar it needs.

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
drives `starting_soon` and `ending_soon` from Microsoft calendar events. Cron
never turns an active meeting off; `meeting.ended` is the source of truth for
that transition.

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

From the repository root, the compatibility stub CLI maps the old command
words to those same `/simulate/*` routes:

```bash
ZOOM_ROOM_RELAY_URL=$RELAY ADMIN_TOKEN=$ADMIN_TOKEN python3 zoom_room_stub.py status starting-soon --starts-in 5
ZOOM_ROOM_RELAY_URL=$RELAY ADMIN_TOKEN=$ADMIN_TOKEN python3 zoom_room_stub.py status in-progress
ZOOM_ROOM_RELAY_URL=$RELAY ADMIN_TOKEN=$ADMIN_TOKEN python3 zoom_room_stub.py status ending-soon --ends-in 3
ZOOM_ROOM_RELAY_URL=$RELAY ADMIN_TOKEN=$ADMIN_TOKEN python3 zoom_room_stub.py status free
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
