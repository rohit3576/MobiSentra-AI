"""Strict loader for ``configs/messaging.yaml`` (Phase 7, Step 7.1c).

Same posture as the severity loader: malformed content fails **at load**
(startup), never mid-stream. The MQTT topic must use slashes and the
``mobisentra/`` prefix — the Phase-0 bridge gotcha (a dotted topic never
matches the ``mobisentra/#`` subscription) is a config error, not a
runtime surprise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

TOPIC_PREFIX: Final = "mobisentra/"
_ALLOWED_SCHEMES: Final = frozenset({"mqtt", "mqtts", "ws", "wss"})
_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "url",
        "topic",
        "client_id",
        "spool",
        "replay_batch",
        "backoff_initial_s",
        "backoff_max_s",
        "puback_timeout_s",
    }
)
_SPOOL_KEYS: Final = frozenset({"path", "max_entries"})


class MessagingConfigError(ValueError):
    """Raised at load for any malformed messaging.yaml content."""


def _fail(path: Path, message: str) -> None:
    raise MessagingConfigError(f"{path}: {message}")


def _string(path: Path, where: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, f"{where} must be a non-empty string")
    return value


def _positive_number(path: Path, where: str, value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        _fail(path, f"{where} must be a positive number")
    return float(value)


@dataclass(frozen=True, slots=True)
class MessagingConfig:
    url: str
    topic: str
    client_id: str
    spool_path: Path
    spool_max_entries: int
    replay_batch: int
    backoff_initial_s: float
    backoff_max_s: float
    puback_timeout_s: float


def load_messaging_config(path: Path) -> MessagingConfig:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        _fail(path, f"invalid YAML: {exc}")
    if not isinstance(raw, dict) or set(raw) != _TOP_LEVEL_KEYS:
        _fail(path, f"top level must be exactly {sorted(_TOP_LEVEL_KEYS)}")

    url = _string(path, "url", raw["url"])
    if "://" not in url or url.split("://", 1)[0] not in _ALLOWED_SCHEMES:
        _fail(path, f"url scheme must be one of {sorted(_ALLOWED_SCHEMES)}: {url!r}")
    topic = _string(path, "topic", raw["topic"])
    if not topic.startswith(TOPIC_PREFIX):
        _fail(
            path,
            f"topic must start with {TOPIC_PREFIX!r} (slash!) with '/' segments: {topic!r}",
        )

    spool = raw["spool"]
    if not isinstance(spool, dict) or set(spool) != _SPOOL_KEYS:
        _fail(path, f"spool must be exactly {sorted(_SPOOL_KEYS)}")
    max_entries = _positive_number(path, "spool.max_entries", spool["max_entries"])

    return MessagingConfig(
        url=url,
        topic=topic,
        client_id=_string(path, "client_id", raw["client_id"]),
        spool_path=Path(_string(path, "spool.path", spool["path"])),
        spool_max_entries=int(max_entries),
        replay_batch=int(_positive_number(path, "replay_batch", raw["replay_batch"])),
        backoff_initial_s=_positive_number(path, "backoff_initial_s", raw["backoff_initial_s"]),
        backoff_max_s=_positive_number(path, "backoff_max_s", raw["backoff_max_s"]),
        puback_timeout_s=_positive_number(path, "puback_timeout_s", raw["puback_timeout_s"]),
    )
