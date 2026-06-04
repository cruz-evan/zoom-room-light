# MicroPython Zoom Room Light

This is the hardware-side client. It does not talk to Zoom directly. Instead, it polls the Python server you already have running:

```text
http://YOUR_LAPTOP_IP:5050/state
```

Do not use `localhost` on the board. On MicroPython, `localhost` means the board itself, not your laptop.

## Files To Copy

Copy these files to the board:

```text
main.py
config.py
light_output.py
priority.py
state_client.py
wifi_connect.py
```

Start by copying `config.example.py` to `config.py` and filling it in.

## Priority Rules

The board applies the same rule every poll:

```text
in_use true         -> red
next meeting soon  -> yellow
otherwise          -> green
network/error      -> purple
```

That means if `meeting.ended` comes in while another meeting starts soon, the server state still contains `next_meeting_id`, so the board displays yellow instead of green.

## Hardware

By default this prints the light state, which is good for testing over serial.

For a NeoPixel, set:

```python
USE_NEOPIXEL = True
LED_PIN = 0
LED_COUNT = 1
```

For a plain onboard/status LED, set:

```python
USE_STATUS_LED = True
LED_PIN = "LED"
```

NeoPixel colors are best for the final demo because a plain LED cannot show red/yellow/green.
