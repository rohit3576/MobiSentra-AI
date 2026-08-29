"""Paho-mqtt v2 transport adapter (Phase 7, 7.1c).

Implements the publisher's synchronous :class:`~mobisentra.messaging.publisher.Transport`
protocol: ``deliver`` returns only after the QoS-1 PUBACK, raises on any
failure (not connected, queue refused, ack timeout) — the spool then
retains the row for replay.

Connection policy: no connect at construction (a broker that is down at
startup is just blackout-from-t=0 — rows spool locally). The network loop
thread starts immediately; ``deliver`` lazily connects when needed and
paho's ``reconnect_delay_set`` handles drops after a first successful
connect. Live behavior is proven against real EMQX in Step 7.2.
"""

from __future__ import annotations

import threading

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

_ALLOWED_SCHEMES = {"mqtt": 1883, "mqtts": 8883, "ws": 80, "wss": 443}


class PahoTransport:
    """QoS-1 ``deliver()`` over a lazily connected paho v2 client."""

    def __init__(self, *, url: str, client_id: str, puback_timeout_s: float = 10.0) -> None:
        scheme, _, rest = url.partition("://")
        if scheme not in _ALLOWED_SCHEMES or not rest:
            raise ValueError(f"unsupported mqtt url: {url!r}")
        host, _, port_raw = rest.partition(":")
        self._host = host or "localhost"
        self._port = int(port_raw) if port_raw.isdigit() else _ALLOWED_SCHEMES[scheme]
        self._puback_timeout_s = puback_timeout_s
        self._connect_lock = threading.Lock()
        self._client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=client_id)
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.loop_start()

    def deliver(self, topic: str, payload: str) -> None:
        self._ensure_connected()
        info = self._client.publish(topic, payload, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"mqtt publish refused (rc={info.rc}) for {topic}")
        info.wait_for_publish(timeout=self._puback_timeout_s)
        if not info.is_published():
            raise TimeoutError(f"PUBACK not received within {self._puback_timeout_s}s for {topic}")

    def close(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()

    def _ensure_connected(self) -> None:
        if self._client.is_connected():
            return
        with self._connect_lock:
            if self._client.is_connected():
                return
            try:
                self._client.connect(self._host, self._port)
            except (OSError, ValueError):
                if not self._client.is_connected():
                    raise ConnectionError(
                        f"mqtt broker unreachable at {self._host}:{self._port}"
                    ) from None
