"""Pure group-separation logic (no I/O) so it can be unit-tested."""
from __future__ import annotations

import math
from dataclasses import dataclass

TOGETHER, WAITING, SEPARATED, LOST = "TOGETHER", "STRAGGLER_WAITING", "SEPARATED", "LOST_SIGNAL"


@dataclass
class Config:
    separation_m: float = 100.0   # "far from group" distance
    hysteresis: float = 0.8       # must come back inside 80% of the limit to be TOGETHER again
    stale_s: float = 30.0         # no data this long -> LOST_SIGNAL
    min_accuracy_m: float = 60.0  # worse GPS than this -> LOST_SIGNAL
    still_rms: float = 0.35       # accelerometer std-dev below this = standing still
    grace_s: float = 60.0         # stopped + far this long -> SEPARATED
    leader: str = ""              # optional: the leader's cluster is always "the group" (breaks 2-vs-2 ties)


@dataclass
class Member:
    name: str
    lat: float
    lon: float
    accuracy_m: float
    motion_rms: float | None
    last_seen: float  # time the detector RECEIVED it (avoids phone clock differences)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Processing A: great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def is_usable(m: Member, now: float, cfg: Config) -> bool:
    return (now - m.last_seen) <= cfg.stale_s and m.accuracy_m <= cfg.min_accuracy_m


def is_moving(m: Member, cfg: Config) -> bool:
    """Processing B: movement state from accelerometer RMS. Unknown counts as moving."""
    return m.motion_rms is None or m.motion_rms >= cfg.still_rms


def ema(prev: float | None, new: float, alpha: float) -> float:
    """Processing C: exponential moving average, smooths GPS jitter (alpha=1 disables it)."""
    if prev is None or alpha >= 1.0:
        return new
    return alpha * new + (1 - alpha) * prev


def find_clusters(usable: dict[str, Member], memory: dict, cfg: Config) -> list[list[str]]:
    """Unsupervised clustering (DBSCAN-style single linkage, eps = separation limit, min_samples = 1).

    Two people are linked if they are within the limit; links chain (A-B and B-C => one cluster).
    A pair that was already flagged must come back inside hysteresis * limit to link again.
    """
    names = sorted(usable)
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            flagged = any(memory.get(x, {}).get("status") in (WAITING, SEPARATED) for x in (a, b))
            limit = cfg.separation_m * (cfg.hysteresis if flagged else 1.0)
            if haversine_m(usable[a].lat, usable[a].lon, usable[b].lat, usable[b].lon) <= limit:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    return list(groups.values())


def pick_main(clusters: list[list[str]], cfg: Config) -> list[str] | None:
    """The cluster that counts as "the group": the leader's, else the single biggest. None on a tie."""
    if cfg.leader:
        for c in clusters:
            if cfg.leader in c:
                return c
    biggest = max(len(c) for c in clusters)
    tops = [c for c in clusters if len(c) == biggest]
    return tops[0] if len(tops) == 1 else None


def evaluate(members: dict[str, Member], now: float, cfg: Config, memory: dict) -> dict[str, dict]:
    """Algorithm: unsupervised clustering (who is "the group") + rule-based fusion of distance and movement.

    1. Members are clustered by distance (see find_clusters). The biggest cluster (or the leader's)
       is the main group. So 2 of 4 people left behind are caught: they form their own small cluster.
    2. Anyone outside the main group is "far". Far + moving = SEPARATED; far + standing still waits
       for grace_s first (probably stopped at a shop).
    3. If two clusters are the same size and there is no leader, nobody is "the" group:
       everybody is treated as far (a 2-person group that splits flags both people).

    LOST_SIGNAL   stale data or poor GPS accuracy (reason: "stale" or "poor_gps")
    TOGETHER      inside the main group
    WAITING       far, but standing still - soft warning
    SEPARATED     far AND moving, or stopped-and-far longer than grace_s
    `memory` keeps per-member state between calls (previous status, when they went far).
    """
    usable = {n: m for n, m in members.items() if is_usable(m, now, cfg)}
    clusters = find_clusters(usable, memory, cfg) if len(usable) >= 2 else []
    main = pick_main(clusters, cfg) if clusters else None
    cluster_of = {n: c for c in clusters for n in c}

    out: dict[str, dict] = {}
    for name, m in members.items():
        mem = memory.setdefault(name, {})
        prev = mem.get("status")
        moving = is_moving(m, cfg)
        dist, nearest, reason, group_size = None, None, None, None

        if name not in usable:
            status = LOST
            reason = "stale" if (now - m.last_seen) > cfg.stale_s else "poor_gps"
            mem.pop("over_since", None)
        elif len(usable) < 2:
            status = TOGETHER  # nobody to compare with
        else:
            mine = cluster_of[name]
            group_size = len(mine)
            if main is not None and name in main:
                status = TOGETHER
                mem.pop("over_since", None)
                mates = [usable[o] for o in mine if o != name]
                if mates:
                    dist, nearest = min((haversine_m(m.lat, m.lon, o.lat, o.lon), o.name) for o in mates)
            else:
                targets = main if main is not None else [n for n in usable if n not in mine]
                dist, nearest = min((haversine_m(m.lat, m.lon, usable[o].lat, usable[o].lon), o)
                                    for o in targets)
                since = mem.setdefault("over_since", now)
                if prev == SEPARATED or moving or now - since >= cfg.grace_s:
                    status = SEPARATED
                else:
                    status = WAITING
        mem["status"] = status
        out[name] = {"status": status,
                     "distance_m": None if dist is None else round(dist, 1),
                     "nearest": nearest,
                     "moving": moving,
                     "reason": reason,
                     "group_size": group_size}
    return out
