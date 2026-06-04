# Zoom Room Light

A hackathon-friendly room status light for Zoom.

The laptop/server side receives Zoom webhooks through ngrok, polls the Zoom schedule API, and exposes a tiny live dashboard. The MicroPython side polls the local server state and drives the hardware light.

## Light Priority

The project always resolves state in this order:

```text
in use       -> red
starts soon  -> yellow
free         -> green
error        -> purple
```

That means if a `meeting.ended` webhook arrives while another meeting starts soon, the light stays yellow instead of going green.

## Server Setup

Copy `.env.example` to `.env` and fill in both Zoom app credential sets:

```bash
ZOOM_WEBHOOK_SECRET_TOKEN=...

ZOOM_ACCOUNT_ID=...
ZOOM_CLIENT_ID=...
ZOOM_CLIENT_SECRET=...
ZOOM_SCHEDULE_USER_ID=your.zoom.email@example.com

SCHEDULE_LOOKAHEAD_MINUTES=15
SCHEDULE_POLL_SECONDS=60
PORT=5050
```

Run the server:

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

The schedule polling does not come from a webhook. It uses the Server-to-Server OAuth app to poll Zoom and turn the light yellow when a meeting starts within `SCHEDULE_LOOKAHEAD_MINUTES`.

## Local Routes

```text
/                 live dashboard
/state            current state JSON for hardware
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
/simulate/clear-upcoming
```

## MicroPython Hardware Client

See [micropython_light/README.md](micropython_light/README.md).

Copy these files to the board:

```text
micropython_light/main.py
micropython_light/config.py
micropython_light/light_output.py
micropython_light/priority.py
micropython_light/state_client.py
micropython_light/wifi_connect.py
```

Create `config.py` from `config.example.py`, and set `STATE_URL` to your laptop's LAN IP:

```python
STATE_URL = "http://192.168.1.42:5050/state"
```

Do not use `localhost` on the board.

## Checks

```bash
PYTHONPYCACHEPREFIX=/private/tmp/codex_pycache python3 -m py_compile zoom_light_webhook.py zoom_light/*.py zoom_schedule.py
python3 micropython_light/test_priority.py
```
