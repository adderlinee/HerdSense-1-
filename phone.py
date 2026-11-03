"""Edge device: reads GPS + accelerometer, publishes to MQTT, shows alerts.

Real phone (Termux):   python phone.py
Fake data (any PC):    python phone.py --source simulator --scenario drift
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import statistics
import subprocess
import threading
import time

import common as C

SIM_LAT = float(C.env("SIM_LAT", "-6.1754"))
SIM_LON = float(C.env("SIM_LON", "106.8272"))


# ---------- real sensors (Termux:API) ----------
def _parse_many(text: str) -> list[dict]:
    """termux-sensor prints several JSON objects back to back."""
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        try:
            obj, i = dec.raw_decode(text, i)
        except ValueError:
            break
        out.append(obj)
    return out


def read_motion_rms() -> float | None:
    """Std-dev of acceleration magnitude over ~1 s: ~0 standing still, 1-3 walking."""
    try:
        out = subprocess.run(["termux-sensor", "-s", "accelerometer", "-d", "100", "-n", "10"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    mags = []
    for obj in _parse_many(out):
        for key, val in obj.items():
            k = key.lower()
            if "accel" in k and not any(x in k for x in ("uncal", "linear", "gravity")):
                v = val.get("values", [])
                if len(v) >= 3:
                    mags.append(math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2))
                break
    return statistics.pstdev(mags) if len(mags) >= 3 else None


GPS_TIMEOUT_S = float(C.env("GPS_TIMEOUT_S", "10"))
NET_TIMEOUT_S = 6.0


def read_gps() -> dict | None:
    """Try real GPS first, fall back to network location. Worst case ~16 s (was ~40 s)."""
    for provider, timeout in (("gps", GPS_TIMEOUT_S), ("network", NET_TIMEOUT_S)):
        try:
            out = subprocess.run(["termux-location", "-p", provider, "-r", "once"],
                                 capture_output=True, text=True, timeout=timeout).stdout
            d = json.loads(out)
            return {"lat": d["latitude"], "lon": d["longitude"], "accuracy_m": d.get("accuracy")}
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            continue
    return None


class GpsReader(threading.Thread):
    """Reads GPS in the background so a slow fix never blocks or delays telemetry.

    The main loop always sends the newest fix together with its age (fix_age_s), so the
    detector knows how old the position is instead of declaring the phone offline.
    `period_s` is how often to ask for a fix (the main loop stretches it while standing still).
    """

    def __init__(self, period_s: float):
        super().__init__(daemon=True)
        self.period_s = period_s
        self.fix: dict | None = None
        self.fix_time = 0.0

    def run(self) -> None:
        while True:
            t0 = time.time()
            fix = read_gps()
            if fix is not None:
                self.fix, self.fix_time = fix, time.time()
            time.sleep(max(1.0, self.period_s - (time.time() - t0)))

    def latest(self, max_age_s: float) -> tuple[dict | None, float | None]:
        if self.fix is None:
            return None, None
        age = time.time() - self.fix_time
        return (dict(self.fix), age) if age <= max_age_s else (None, age)


# ---------- simulator ----------
def sim_reading(t: float, args, rng: random.Random, off: tuple[float, float]) -> dict:
    """Everyone stands around one spot. Scenarios:
    together  stays with the group (walking in place)
    drift     walks away from the spot at --speed after --drift-after seconds
    still     stands motionless (use it on one phone while the others 'drift' away)"""
    since = t - args.drift_after
    d = since * args.speed if args.scenario == "drift" and since > 0 else 0.0
    x = off[0] + d + rng.gauss(0, 2)
    y = off[1] + rng.gauss(0, 2)
    rms = 0.1 + abs(rng.gauss(0, 0.05)) if args.scenario == "still" else abs(rng.gauss(1.5, 0.3))
    return {"lat": SIM_LAT + y / 111320,
            "lon": SIM_LON + x / (111320 * math.cos(math.radians(SIM_LAT))),
            "accuracy_m": round(rng.uniform(4, 12), 1),
            "motion_rms": round(rms, 2)}


# ---------- main ----------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["termux", "simulator"], default="termux")
    ap.add_argument("--scenario", choices=["together", "drift", "still"], default="together")
    ap.add_argument("--speed", type=float, default=2.0, help="simulator: metres/second when moving away")
    ap.add_argument("--drift-after", type=float, default=20, help="simulator: seconds before leaving")
    args = ap.parse_args()
    interval = float(C.env("SEND_INTERVAL_S", "5"))
    still_factor = float(C.env("STILL_INTERVAL_FACTOR", "3"))  # send/read GPS this much less often while still
    still_rms = float(C.env("STILL_RMS", "0.35"))
    gps_max_age = float(C.env("GPS_MAX_AGE_S", "40"))  # older fixes are not sent
    termux = args.source == "termux"

    def on_connect(c, u, flags, rc, props=None):
        c.subscribe(C.T_ALERTS, qos=1)
        print(f"[{C.MEMBER}] connected to {C.HOST}, source={args.source}")

    def on_message(c, u, msg):
        try:
            a = json.loads(msg.payload)
            text, level = str(a["message"]), a.get("level", "info")
        except (ValueError, KeyError):
            return
        print(f"[{C.MEMBER}] ALERT ({level}): {text}")
        if termux and shutil.which("termux-notification"):
            subprocess.Popen(["termux-notification", "--id", "trip-alert", "--title",
                              "Group alert" if level == "warn" else "Group update", "--content", text])
            if level == "warn" and shutil.which("termux-vibrate"):
                subprocess.Popen(["termux-vibrate", "-d", "600"])

    client = C.make_client("phone", on_connect, on_message)
    C.connect(client)

    rng = random.Random()
    fixed = random.Random(C.MEMBER)
    off = (fixed.uniform(-5, 5), fixed.uniform(-5, 5))
    gps = GpsReader(interval) if termux else None
    if gps:
        gps.start()
    start, seq = time.time(), 0
    period = interval
    while True:
        loop_start = time.time()
        if termux:
            rms = read_motion_rms()
            still = rms is not None and rms < still_rms
            period = interval * (still_factor if still else 1.0)  # save battery while standing still
            gps.period_s = period
            fix, fix_age = gps.latest(gps_max_age)
            reading = None if fix is None else {**fix, "motion_rms": rms, "fix_age_s": round(fix_age, 1)}
        else:
            reading = sim_reading(loop_start - start, args, rng, off)
        if reading is None:
            print(f"[{C.MEMBER}] no usable GPS fix yet, skipping")
        else:
            payload = {"device_id": C.MEMBER, "sequence": seq, **reading, "sent_at_ms": C.now_ms()}
            client.publish(C.t_telemetry(C.MEMBER), json.dumps(payload), qos=0)
            print(f"[{C.MEMBER}] #{seq} lat={reading['lat']:.5f} lon={reading['lon']:.5f} "
                  f"acc={reading['accuracy_m']} rms={reading['motion_rms']}")
            seq += 1
        time.sleep(max(0.0, period - (time.time() - loop_start)))


if __name__ == "__main__":
    main()
