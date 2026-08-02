# 🚗 Pi GPS Car Tracker

A self-contained, self-healing car GPS tracker built on a Raspberry Pi 4B and a
u-blox NEO-6M. It broadcasts a live position to an OpenStreetMap page that can
be shared with anyone via a single HTTPS link — no app or account needed on
their end — and confirms it's broadcasting with an audible beep and spoken
announcement the moment a GPS fix is acquired.

Power it on in the car, wait for the beep, and anyone you've given the link to
can watch the car move in real time.

## Features

- **Live map** — Leaflet + OpenStreetMap, custom labeled marker, live speed
  and satellite count, 1-second updates
- **Share with a link** — Tailscale Funnel exposes the map at a stable public
  HTTPS URL that works from any browser on any network
- **Audible "I'm live" confirmation** — two-tone beep + espeak voice
  announcement through the 3.5mm jack on first GPS fix after boot
- **Fully headless** — systemd unit starts everything on boot; survives cold
  power cycles unattended
- **Network roaming** — hops automatically between home WiFi and a phone
  hotspot; Tailscale keeps the address stable either way
- **Stale-fix rejection** — GPS reports older than 10 s are discarded rather
  than shown as a phantom "live" position
- **Self-healing freeze watchdog** — detects a wedged receiver (bit-identical
  coordinates while claiming a fix), fires a u-blox factory cold start
  automatically, announces it over the speaker, and counts resets in the API

## Hardware

| Part | Notes |
|------|-------|
| Raspberry Pi 4B | any Pi with a UART should work |
| u-blox NEO-6M GPS module | common GY-GPS6MV2-style breakout |
| Jumper wires | 3 wires: power, ground, GPS TX |
| Passive 3.5mm speaker | for the broadcast beep |
| USB power bank | e.g. Voltaic V25 (~8–12 h of Pi 4 runtime) |
| Phone hotspot | in-car internet |

### Wiring

| NEO-6M pin | Pi pin | Function |
|------------|--------|----------|
| VCC | Pin 2 (5V) | power — see note below |
| GND | Pin 6 (GND) | ground |
| TX | Pin 10 (RXD / GPIO15) | GPS → Pi serial |

> **⚠️ Power the module from 5V, not 3.3V.** Most NEO-6M breakouts have an
> onboard 3.3V regulator and expect 5V input. Feeding them 3.3V leaves the
> regulator output marginal — the module *appears* to work, then intermittently
> wedges (see Troubleshooting). The module's TX output is 3.3V logic either
> way, so it's safe to connect directly to the Pi's RX.

Mount the module with the ceramic antenna facing **up** with sky view (car
dashboard works), and keep it a few inches away from the Pi — the Pi 4 is a
noisy RF neighbor.

## Software setup

Enable the UART and route audio to the 3.5mm jack:

```bash
sudo raspi-config
# Interface Options → Serial Port → login shell over serial: No
#                                 → serial hardware: Yes
# System Options → Audio → 3.5mm jack
sudo reboot
```

Install dependencies:

```bash
sudo apt update
sudo apt install -y gpsd gpsd-clients espeak alsa-utils python3-pip
pip3 install flask gpsd-py3 --break-system-packages
```

Point gpsd at the UART in `/etc/default/gpsd`:

```
DEVICES="/dev/ttyS0"
GPSD_OPTIONS="-n"
START_DAEMON="true"
```

```bash
sudo systemctl restart gpsd
cgps -s        # verify a fix (needs sky view; first fix can take a minute)
```

Deploy the app:

```bash
mkdir -p ~/gps-tracker
cp app.py ~/gps-tracker/
cp -r templates ~/gps-tracker/
cd ~/gps-tracker
python3 app.py
```

Open `http://<pi-ip>:5000` — when the fix lands you'll hear the beep and see
the marker go live.

### Run on boot

Copy `gps-tracker.service` to `/etc/systemd/system/` (adjust the username in
the paths if yours isn't `pi`), then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gps-tracker
```

### Share the link

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale funnel --bg 5000     # --bg = persists across reboots
```

The command prints your public `https://….ts.net` URL — that's the shareable
link. The URL is unguessable but technically public, so share deliberately.
Turn it off with `sudo tailscale funnel --bg=off 5000`.

To let the Pi roam onto a phone hotspot:

```bash
sudo nmcli device wifi connect "HotspotName" password "password"
```

(Pixel users: enable the hotspot's 2.4 GHz / "extend compatibility" mode or
the Pi may not see it.)

## API

`GET /position` returns:

```json
{
  "lat": 34.05, "lon": -118.44,
  "speed_kmh": 42.3, "satellites": 11,
  "fix": true, "broadcasting": true,
  "watchdog_resets": 0
}
```

`watchdog_resets` counts how many times the freeze watchdog has had to
cold-start the receiver since the app started — a handy health metric after a
long drive.

## Troubleshooting

**Position ~1 mile off despite many satellites "seen"** — check `cgps -s` for
satellites *used*, not seen. 3–5 used with low SNR = bad antenna placement.
Ceramic side up, real sky view.

**Map shows an old position as "live"** — gpsd can re-serve a stale cached
report. This app rejects reports older than 10 s (`MAX_REPORT_AGE_S`), so
you'll see "waiting for GPS fix…" instead of a phantom position.

**Coordinates frozen but timestamps ticking (the "wedge")** — the NEO-6M's
navigation solution can seize while the module keeps happily emitting NMEA
with fresh timestamps and a blinking fix LED. Diagnose with
`gpspipe -r -n 20`: bit-identical lat/lon across sentences = wedged. Fix with
`ubxtool -p COLDBOOT`. Note that power cycling does **not** clear it — the
board's backup battery preserves the corrupted state. The built-in watchdog
automates this recovery. Root cause in this build was powering the module
from 3.3V; see the wiring note.

**Very slow first fix after COLDBOOT** — a factory cold start erases the
almanac; rebuilding it needs several minutes of strong, uninterrupted signal.
Give it real outdoor sky for the first fix; subsequent starts are fast.

**No NMEA at all (`gpspipe` silent)** — wiring. Reseat all jumper connectors
at both ends; verify VCC landed on a 5V pin.

## Roadmap

- Auto-shutdown when parked (speed ≈ 0 for N minutes) to preserve the power
  bank
- Position history / breadcrumb trail on the map
- Hardware NMEA pre-filter on a coprocessor
- Upgrade to NEO-M8N for faster, more robust fixes
