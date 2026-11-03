"""Shared config + MQTT helpers used by every script."""
from __future__ import annotations

import os
import ssl
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parent


def load_env(path: Path = ROOT / ".env") -> None:
    """Tiny .env reader (no extra dependency). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


HOST = env("MQTT_HOST", "broker.hivemq.com")
PORT = int(env("MQTT_PORT", "1883"))
TLS = env("MQTT_TLS", "false").lower() == "true"
USER = env("MQTT_USERNAME")
PASSWORD = env("MQTT_PASSWORD")
GROUP_ID = env("GROUP_ID", "change-me")
MEMBER = env("MEMBER_NAME", "member1")


def topic(*parts: str) -> str:
    return "/".join(("trip", GROUP_ID) + parts)


def t_telemetry(member: str) -> str:
    return topic("edge", member, "telemetry")


T_ALERTS = topic("alerts")
T_STATUS = topic("status")


def now_ms() -> int:
    return int(time.time() * 1000)


def make_client(role: str, on_connect=None, on_message=None) -> mqtt.Client:
    cid = f"trk-{role}-{MEMBER}-{uuid.uuid4().hex[:6]}"  # unique: public brokers kick duplicate ids
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
    if USER:
        c.username_pw_set(USER, PASSWORD)
    if TLS:
        c.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    c.reconnect_delay_set(1, 15)
    c.on_connect = on_connect
    c.on_message = on_message
    return c


def connect(c: mqtt.Client) -> None:
    c.connect(HOST, PORT, keepalive=30)
    c.loop_start()
