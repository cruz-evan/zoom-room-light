# RP2 Zoom LEDs Hardware Test Routine

Use this routine to verify the Raspberry Pi Pico / RP2040 board, USB serial link,
and WS2812 / NeoPixel LED strip before connecting the real Zoom bridge.

## Hardware Under Test

- Raspberry Pi Pico, Pico W, or compatible RP2040 / RP2350 board
- MicroPython firmware on the board
- WS2812 / NeoPixel strip data input connected to `GP0`
- Pico `GND` connected to LED power supply `GND`
- Default firmware settings: `LED_COUNT = 144`, `BRIGHTNESS = 0.12`, RGB order

## Safety Checks

1. Keep LED brightness low during bench testing.
2. Do not send full-white commands unless the strip has an adequate external 5V
   supply.
3. For a 144 LED strip, budget up to about 8.6A at 5V for worst-case full white.
4. Use common ground between the Pico and LED supply.
5. If available, use a 330 ohm resistor on the data line and a large capacitor
   across the LED power rails.

## Setup

Run these commands from the `rp2-zoom-leds` project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

If multiple USB serial devices are attached, find the Pico port:

```bash
python host/serial_bridge.py --list-ports
```

Use `--port /dev/cu.usbmodemXXXX` in later commands if `auto` chooses the wrong
device.

## Test 1: Verify MicroPython

Connect the Pico by USB, then run:

```bash
./scripts/verify_device.sh
```

Expected result:

- Command prints `micropython` in `sys.implementation`.
- No connection or permission error appears.

If this fails, reflash the board with the correct MicroPython UF2 and try again.

## Test 2: Deploy Device Firmware

Copy the device code to the board:

```bash
./scripts/deploy_device.sh
```

Expected result:

- Files from `device/*.py` copy to the board.
- The board resets at the end.
- The onboard LED should not continuously blink after reset. Continuous blinking
  means the firmware could not write to the LED strip.

## Test 3: Basic RGB Output

Send simple solid colors:

```bash
python host/serial_bridge.py --mode solid --rgb 255,0,0
python host/serial_bridge.py --mode solid --rgb 0,255,0
python host/serial_bridge.py --mode solid --rgb 0,0,255
python host/serial_bridge.py --mode off
```

Expected result:

- Strip turns dim red, then dim green, then dim blue.
- `off` turns all LEDs off.
- Colors should match RGB order. If red and green or blue are swapped, note the
  observed order.

## Test 4: Pulse Mode

Run:

```bash
python host/serial_bridge.py --mode pulse --rgb 0,120,255 --speed 0.6
```

Expected result:

- Strip pulses blue smoothly.
- No flicker, random colors, or board reset occurs.

Turn the strip off after observing:

```bash
python host/serial_bridge.py --mode off
```

## Test 5: Product State Commands

Send the final Zoom-state command contract:

```bash
python host/serial_bridge.py --mode meeting_status --state starting_soon --minutes 5
python host/serial_bridge.py --mode meeting_status --state in_progress
python host/serial_bridge.py --mode meeting_status --state ending_soon --minutes 5
python host/serial_bridge.py --mode meeting_status --state ending_soon --minutes 1
python host/serial_bridge.py --mode off
```

Expected result:

- `starting_soon`: blue moving cue across the strip.
- `in_progress`: calm green / teal indication.
- `ending_soon --minutes 5`: amber pulse.
- `ending_soon --minutes 1`: faster, more urgent amber / orange pulse.
- `off`: all LEDs off.

## Test 6: Automated Fake Zoom Cycle

Run one complete simulated Zoom cycle:

```bash
python host/simulate_zoom.py --port auto --interval 3 --loops 1
```

Expected result:

- The script prints each fake state.
- The strip changes through starting soon, in progress, ending soon, urgent
  ending soon, and off.
- The board remains connected for the full cycle.

## Optional Dry Run Without Hardware

This only verifies the JSON command sequence:

```bash
python host/simulate_zoom.py --dry-run --loops 1
```

Expected output includes:

```text
starting in 5m: {"mode": "meeting_status", "state": "starting_soon", "minutes": 5.0, "threshold": 5.0}
in progress: {"mode": "meeting_status", "state": "in_progress", "minutes": 5.0, "threshold": 5.0}
ending in 5m: {"mode": "meeting_status", "state": "ending_soon", "minutes": 5.0, "threshold": 5.0}
ending in 1m: {"mode": "meeting_status", "state": "ending_soon", "minutes": 1.0, "threshold": 5.0}
off: {"mode": "off"}
```

## Results

| Check | Pass / Fail | Notes |
| --- | --- | --- |
| Pico appears as USB serial device |  |  |
| `verify_device.sh` prints MicroPython |  |  |
| Device firmware deploys successfully |  |  |
| Red / green / blue commands show correct colors |  |  |
| `off` command turns LEDs off |  |  |
| Pulse mode is smooth |  |  |
| `starting_soon` shows blue moving cue |  |  |
| `in_progress` shows green / teal |  |  |
| `ending_soon` shows amber pulse |  |  |
| One-loop simulator completes |  |  |

## Report Back

Send back:

- Board model used
- MicroPython version printed by `verify_device.sh`
- Serial port path used, if not `auto`
- Power supply voltage and current rating
- LED strip count and observed RGB color order
- Completed results table
- Any photos or short video of unexpected LED behavior
