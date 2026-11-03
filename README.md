# Group Travel Tracker (IoT, multicommunication + multisensor)

Phones publish GPS + accelerometer data over MQTT. A detector decides who has drifted away from
the group and sends alerts back to every phone (vibration + notification). A web dashboard shows
live status.

```
phones (Termux)  <--MQTT-->  broker  <--MQTT-->  detector.py
                               |
                               +--MQTT(WebSocket)--> dashboard/index.html
```

| Piece | File |
|---|---|
| Edge: sensors, processing, publish, receive alerts | `phone.py` |
| Rules (distance + movement fusion), pure and tested | `rules.py`, `tests/` |
| Detector + alerts | `detector.py` |
| Dashboard | `dashboard/index.html` |
| Per-phone readiness check | `check_phone.py` |

## Setup (every device)
1. Termux + Termux:API from F-Droid, then `pkg install python git termux-api`
2. `git clone <repo> && cd <repo> && pip install -r requirements.txt`
3. `cp .env.example .env` and edit: shared `GROUP_ID` (long, random, share privately), unique `MEMBER_NAME`
4. `python check_phone.py` until it prints READY

## Run
- **Detector (one device):** `python detector.py`
- **Each phone:** `python phone.py` (run `termux-wake-lock` first so Android keeps it alive)
- **Dashboard:** open `dashboard/index.html` in a browser, enter the `GROUP_ID`, Connect
  (or `index.html?group=YOUR_GROUP_ID`; add `&host=...&port=...&user=...&pass=...` for another broker)

## Demo without being together (simulator, any PC)
Lower the limit in `.env` (`SEPARATION_M=30`, `GRACE_S=10`), then:
- **Someone walks away:** two phones `python phone.py --source simulator`, one with `--scenario drift --speed 3`
- **Someone stopped behind:** two phones `--scenario drift --speed 3`, one `--scenario still`

Each simulated phone needs its own `MEMBER_NAME` (bash: `MEMBER_NAME=ayu python phone.py ...`, PowerShell: `$env:MEMBER_NAME="ayu"; python phone.py ...`).

## MQTT topics
```
trip/<GROUP_ID>/edge/<member>/telemetry   phone -> broker    {device_id, sequence, lat, lon, accuracy_m, motion_rms, fix_age_s, sent_at_ms}
trip/<GROUP_ID>/alerts                    detector -> phones {level, member, message, ts}
trip/<GROUP_ID>/status                    detector -> dashboard (retained)
```

## Detector rules (`rules.py`)
Three processing steps run in the detector:
- **A. Haversine** distance between phones.
- **B. Movement state** from accelerometer RMS (still / moving).
- **C. EMA smoothing** of the GPS coordinates (`EMA_ALPHA`, 0.5 by default) to damp GPS jitter.

**Who is "the group"? Unsupervised clustering.** People are clustered by distance (DBSCAN-style single
linkage: two people within `SEPARATION_M` are linked, links chain). The biggest cluster, or the cluster of
`LEADER_NAME` if set, is the main group. Anyone outside it is "far". This catches several people left
behind together (e.g. 2 of 5), which "distance to the nearest groupmate" cannot.

| Condition | Status |
|---|---|
| no data for `STALE_S` or GPS accuracy worse than `MIN_ACCURACY_M` | LOST_SIGNAL (reason: no recent data / weak GPS) |
| inside the main group | TOGETHER |
| outside the group but standing still | STRAGGLER_WAITING (info alert) |
| outside the group and moving, or stopped-and-away longer than `GRACE_S` | SEPARATED (warn alert, repeated every `ALERT_COOLDOWN_S`) |

Coming back needs distance under 80% of the limit (hysteresis), so GPS jitter doesn't flap the status.
Limitations: with two equal clusters (2 vs 2) and no `LEADER_NAME`, nobody is "the" group so everybody is
flagged (same for a 2-person group). A long line of people stays one group while each neighbour is within the limit.

## Phone side: battery and signal
- GPS is read in a background thread; telemetry always carries the newest fix and its age (`fix_age_s`),
  so a slow fix no longer makes the phone look offline.
- GPS timeouts: 10 s + 6 s (was 20 s + 20 s).
- While the phone stands still it sends and reads GPS `STILL_INTERVAL_FACTOR` times less often.
- `SEND_INTERVAL_S` is 5 s by default (walking speed makes 3 s unnecessary).

Tests: `python -m unittest discover -s tests -v`   (PowerShell: `& $py -m unittest discover -s tests -v`)

## Notes
- The default broker is public: keep `GROUP_ID` long and random, and use simulated tracks for demos.
  For privacy use a broker with login (set `MQTT_USERNAME`/`MQTT_PASSWORD`, `MQTT_TLS=true`, port 8883).
- GPS is only accurate to ~5-20 m. Keep `SEPARATION_M` at least 50 m in real use.
- Plumbing (Termux sensor reading, MQTT structure) follows the course example
  https://github.com/myudak/contoh-project-iot1; the idea, rules and dashboard are original.
