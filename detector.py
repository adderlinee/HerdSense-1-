"""Subscribes to every phone, decides who is separated, publishes status + alerts.

Run on ONE device (laptop or a member's phone):  python detector.py
"""
from __future__ import annotations

import json
import threading
import time

import common as C
from rules import LOST, SEPARATED, TOGETHER, WAITING, Config, Member, ema, evaluate


def parse(topic: str, payload: bytes):
    """Validate one telemetry message. Returns None for anything malformed."""
    try:
        d = json.loads(payload)
        name = topic.split("/")[3]
        lat, lon, seq = float(d["lat"]), float(d["lon"]), int(d["sequence"])
        acc = d.get("accuracy_m")
        acc = 999.0 if acc is None else float(acc)  # unknown accuracy -> unusable
        rms = d.get("motion_rms")
        rms = None if rms is None else float(rms)
        fix_age = max(0.0, float(d.get("fix_age_s") or 0.0))  # how old the GPS fix already was on the phone
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return name, seq, lat, lon, acc, rms, fix_age


def main() -> None:
    cfg = Config(separation_m=float(C.env("SEPARATION_M", "100")),
                 stale_s=float(C.env("STALE_S", "45")),
                 min_accuracy_m=float(C.env("MIN_ACCURACY_M", "60")),
                 grace_s=float(C.env("GRACE_S", "60")),
                 leader=C.env("LEADER_NAME", ""))
    alpha = float(C.env("EMA_ALPHA", "0.5"))  # 1.0 = no smoothing
    cooldown = float(C.env("ALERT_COOLDOWN_S", "30"))
    members: dict[str, Member] = {}
    memory: dict = {}
    last_seq: dict[str, int] = {}
    smooth: dict[str, tuple[float, float]] = {}
    last_alert: dict[str, float] = {}
    lock = threading.Lock()

    def on_connect(c, u, flags, rc, props=None):
        c.subscribe(C.topic("edge", "+", "telemetry"))
        print(f"[detector] connected, group={C.GROUP_ID[:12]}..., limit={cfg.separation_m:.0f} m")

    def on_message(c, u, msg):
        r = parse(msg.topic, msg.payload)
        if not r:
            return
        name, seq, lat, lon, acc, rms, fix_age = r
        with lock:
            if last_seq.get(name) == seq:  # drop duplicates
                return
            last_seq[name] = seq
            if acc <= cfg.min_accuracy_m:  # smooth only usable fixes (Processing C: EMA)
                p = smooth.get(name)
                lat, lon = ema(p[0] if p else None, lat, alpha), ema(p[1] if p else None, lon, alpha)
                smooth[name] = (lat, lon)
            # an old GPS fix counts as old data: subtract its age from "last seen"
            members[name] = Member(name, lat, lon, acc, rms, time.time() - min(fix_age, 600.0))

    client = C.make_client("detector", on_connect, on_message)
    C.connect(client)

    def alert(level: str, who: str, text: str) -> None:
        client.publish(C.T_ALERTS, json.dumps(
            {"level": level, "member": who, "message": text, "ts": C.now_ms()}), qos=1)
        print(f"   !! [{level}] {text}")

    while True:
        time.sleep(1)
        now = time.time()
        with lock:
            snap = dict(members)
        if not snap:
            continue
        prevs = {n: memory.get(n, {}).get("status") for n in snap}
        res = evaluate(snap, now, cfg, memory)

        status = {"ts": C.now_ms(), "threshold_m": cfg.separation_m, "members": {}}
        for n, r in res.items():
            m = snap[n]
            status["members"][n] = {**r, "lat": m.lat, "lon": m.lon, "accuracy_m": m.accuracy_m,
                                    "motion_rms": m.motion_rms, "age_s": round(now - m.last_seen, 1)}
        client.publish(C.T_STATUS, json.dumps(status), qos=0, retain=True)

        for n, r in res.items():
            cur, prev, d = r["status"], prevs[n], r["distance_m"]
            if cur == SEPARATED and (prev != SEPARATED or now - last_alert.get(n, 0) >= cooldown):
                last_alert[n] = now
                alert("warn", n, f"{n} is {d:.0f} m from the group")
            elif cur == WAITING and prev not in (WAITING, SEPARATED):
                alert("info", n, f"{n} stopped {d:.0f} m away from the group")
            elif cur == TOGETHER and prev in (WAITING, SEPARATED):
                alert("info", n, f"{n} is back with the group")
            elif cur == LOST and prev not in (LOST, None):
                alert("info", n, f"No signal from {n}")

        print(time.strftime("%H:%M:%S"), " | ".join(
            f"{n}:{r['status']}:{'-' if r['distance_m'] is None else str(int(r['distance_m'])) + 'm'}"
            for n, r in res.items()))


if __name__ == "__main__":
    main()
