# MicroPython Zoom Room Light

This is the hardware-side client. It does not talk to Zoom directly. Instead, it polls the Python server you already have running:

```text
http://YOUR_LAPTOP_IP:5050/state
```

Do not use `localhost` on the board. On MicroPython, `localhost` means the board itself, not your laptop.

## Files To Copy

Copy these files to the board:

```text
boot.py
main.py
config.py
light_output.py
priority.py
state_client.py
wifi_connect.py
```

Start by copying `config.example.py` to `config.py` and filling it in.

## WebREPL Wi-Fi Deploys

The first setup still needs USB serial, Thonny, `mpremote`, or another direct
copy method so the board has `boot.py` and `config.py`. After that, `boot.py`
connects the Pico W to Wi-Fi and starts WebREPL on port `8266`.

In `config.py`, set:

```python
WIFI_SSID = "your-wifi-name"
WIFI_PASSWORD = "your-wifi-password"
WEBREPL_ENABLED = True
WEBREPL_PASSWORD = "use-a-real-password"
```

Then deploy from this repo:

```bash
WEBREPL_PASSWORD='use-a-real-password' ./scripts/deploy_micropython_webrepl.py 192.168.1.123
```

The deploy script copies:

```text
boot.py
main.py
config.py
light_output.py
priority.py
state_client.py
wifi_connect.py
```

If `micropython_light/config.py` does not exist locally, the script skips it so
you do not accidentally overwrite device-specific secrets.

After all files are uploaded, the script opens a new WebREPL session, sends
`Ctrl-C` to stop the running app loop, then sends `Ctrl-D` to soft-reset the
MicroPython interpreter. That reruns `boot.py` and `main.py` without a physical
power cycle.

To upload without restarting the interpreter:

```bash
WEBREPL_PASSWORD='use-a-real-password' ./scripts/deploy_micropython_webrepl.py 192.168.1.123 --no-reset
```

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
