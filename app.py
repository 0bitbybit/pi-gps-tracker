"""
Minimal car GPS tracker — MVP scope
NEO-6M -> Pi UART -> gpsd -> Flask -> Leaflet map (OpenStreetMap tiles)

Features:
  - Live position at /  (share via Tailscale link)
  - Marker labeled "GJ's Car"
  - Audible beep + voice announcement through 3.5mm jack the moment
    the first GPS fix is acquired (i.e. you are live on the map)
"""

from flask import Flask, jsonify, render_template
import gpsd
import subprocess
import threading
import time
import wave
from datetime import datetime, timezone
import struct
import math
import os

app = Flask(__name__)

MARKER_LABEL = "GJ's Car"
BEEP_WAV = "/home/pi/gps-tracker/beep.wav"

# ── STATE ────────────────────────────────────────────────
broadcasting = False          # flips True on first GPS fix
last = {"lat": None, "lon": None, "speed_kmh": None,
        "satellites": None, "fix": False}
lock = threading.Lock()


# ── BEEP ─────────────────────────────────────────────────
def make_beep_wav():
    """Generate a cheerful two-tone beep (no extra packages needed)."""
    if os.path.exists(BEEP_WAV):
        return
    rate = 44100
    frames = []
    for freq, secs in [(880, 0.15), (0, 0.05), (1320, 0.25)]:
        for i in range(int(rate * secs)):
            val = int(12000 * math.sin(2 * math.pi * freq * i / rate)) if freq else 0
            frames.append(struct.pack('<h', val))
    with wave.open(BEEP_WAV, 'w') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b''.join(frames))


def announce_live():
    """Beep + spoken confirmation out the 3.5mm jack."""
    subprocess.Popen(['aplay', '-q', BEEP_WAV])
    time.sleep(0.6)
    # Optional voice line — comment out if you only want the beep.
    subprocess.Popen(['espeak', '-s', '130', 'G P S live. Broadcasting position.'])


# ── STALENESS CHECK ──────────────────────────────────────
MAX_REPORT_AGE_S = 10   # ignore GPS reports older than this

# ── FREEZE WATCHDOG ──────────────────────────────────────
# A healthy receiver jitters in the last decimals constantly, even parked.
# Bit-identical coordinates for this long while claiming a fix = wedged
# navigation solution -> force a factory cold start automatically.
FREEZE_LIMIT_S = 180
_wd = {"coords": None, "since": time.time(), "resets": 0}


def reset_receiver():
    """Fire a u-blox factory cold start via gpsd, announce it."""
    subprocess.Popen(['ubxtool', '-p', 'COLDBOOT'])
    subprocess.Popen(['espeak', '-s', '130',
                      'G P S frozen. Resetting receiver.'])


def watchdog(lat, lon):
    """Track coordinate changes; trigger reset if frozen too long."""
    now = time.time()
    if (lat, lon) != _wd["coords"]:
        _wd["coords"] = (lat, lon)
        _wd["since"] = now
    elif now - _wd["since"] > FREEZE_LIMIT_S:
        _wd["resets"] += 1
        _wd["since"] = now          # don't re-fire while it re-acquires
        threading.Thread(target=reset_receiver, daemon=True).start()

def report_age_seconds(packet):
    """Age of a gpsd report in seconds; large number if unparseable."""
    try:
        t = datetime.fromisoformat(str(packet.time).replace('Z', '+00:00'))
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return 9999


# ── GPS POLLING THREAD ───────────────────────────────────
def poll_gps():
    global broadcasting
    while True:
        try:
            packet = gpsd.get_current()
            with lock:
                if packet.mode >= 2 and report_age_seconds(packet) < MAX_REPORT_AGE_S:
                    # fresh 2D/3D fix only — stale cached reports are rejected
                    last["lat"] = packet.lat
                    last["lon"] = packet.lon
                    last["speed_kmh"] = round(packet.hspeed * 3.6, 1) if packet.hspeed else 0.0
                    last["satellites"] = packet.sats
                    last["fix"] = True
                    watchdog(packet.lat, packet.lon)
                    if not broadcasting:
                        broadcasting = True
                        threading.Thread(target=announce_live, daemon=True).start()
                else:
                    last["fix"] = False
        except Exception:
            with lock:
                last["fix"] = False
        time.sleep(1)


# ── ROUTES ───────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('map.html', label=MARKER_LABEL)


@app.route('/position')
def position():
    with lock:
        return jsonify(dict(last, broadcasting=broadcasting,
                            watchdog_resets=_wd["resets"]))


if __name__ == '__main__':
    make_beep_wav()
    gpsd.connect()
    threading.Thread(target=poll_gps, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, threaded=True)
