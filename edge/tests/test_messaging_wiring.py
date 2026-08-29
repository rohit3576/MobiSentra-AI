"""Messaging wiring tests (Phase 7, Step 7.1c).

Loader strictness (incl. the Phase-0 dotted-topic gotcha as a hard error),
attach_messaging composition (one shared publisher, stub transport), and
the both-sinks run_frame path — with ``--publish`` off as the unchanged
default.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from mobisentra.ingestion.config import CameraConfig, Thresholds, ZoneConfig, ZoneType
from mobisentra.ingestion.frame import Frame
from mobisentra.messaging.config import (
    MessagingConfig,
    MessagingConfigError,
    load_messaging_config,
)
from mobisentra.pipeline import CameraAccumulator, attach_analytics, attach_messaging, run_frame

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "messaging.yaml"
SEVERITY_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "severity.yaml"
FULL_FRAME = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


class StubTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def deliver(self, topic: str, payload: str) -> None:
        self.calls.append((topic, payload))

    def close(self) -> None:
        self.closed = True


def tmp_config(tmp_path: Path, **overrides) -> MessagingConfig:
    """Config pointing at tmp dirs; overrides applied on the loaded default."""
    raw = yaml.safe_load(CONFIG.read_text())
    raw["spool"]["path"] = str(tmp_path / "edge.db")
    for key, value in overrides.items():
        raw[key] = value
    variant = tmp_path / "messaging.yaml"
    variant.write_text(yaml.safe_dump(raw))
    return load_messaging_config(variant)


# --- loader --------------------------------------------------------------------


def test_default_config_loads_with_approved_values():
    config = load_messaging_config(CONFIG)
    assert config.url == "mqtt://localhost:1883"
    assert config.topic == "mobisentra/events"
    assert config.client_id == "mobisentra-edge"
    assert config.spool_path == Path("runs/spool/edge.db")
    assert config.spool_max_entries == 100_000
    assert config.replay_batch == 500
    assert (config.backoff_initial_s, config.backoff_max_s) == (1.0, 60.0)
    assert config.puback_timeout_s == 10.0


def test_dotted_topic_rejected_the_phase0_gotcha(tmp_path):
    with pytest.raises(MessagingConfigError, match="slash"):
        tmp_config(tmp_path, topic="mobisentra.events")

    with pytest.raises(MessagingConfigError, match="mobisentra/"):
        tmp_config(tmp_path, topic="events/other")


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"url": "http://emqx:1883"}, "scheme"),
        ({"url": ""}, "non-empty string"),
        ({"client_id": ""}, "non-empty string"),
        ({"replay_batch": 0}, "positive"),
        ({"backoff_max_s": -1}, "positive"),
        ({"spool": {"path": "x.db"}}, "spool must be"),
        ({"mystery": 1}, "top level"),
    ],
)
def test_loader_rejects_bad_values(tmp_path, overrides, match):
    with pytest.raises(MessagingConfigError, match=match):
        tmp_config(tmp_path, **overrides)


def test_loader_rejects_malformed_yaml(tmp_path):
    broken = tmp_path / "m.yaml"
    broken.write_text("url: [unclosed")
    with pytest.raises(MessagingConfigError, match="invalid YAML"):
        load_messaging_config(broken)


# --- attach_messaging + run_frame ----------------------------------------------


def make_acc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CameraAccumulator:
    monkeypatch.chdir(tmp_path)
    camera = CameraConfig(
        id="MSG_CAM",
        source="sample://videos/x.mp4",
        vehicle_id="MSG_BUS",
        zones={
            "hall": ZoneConfig(
                name="hall", zone_type=ZoneType.OCCUPANCY, polygon=FULL_FRAME, max_capacity=2
            )
        },
        thresholds=Thresholds(occupancy_confirm_frames=1),
    )
    from mobisentra.vision.track_history import TrackHistory

    acc = CameraAccumulator(camera=camera, reader=object())
    acc.history = TrackHistory()
    attach_analytics([acc], {"model": "stub.pt"}, severity_path=SEVERITY_CONFIG)
    return acc


class StubDetector:
    produces_pose = False

    def __init__(self, people_by_frame: list[list]) -> None:
        self.people_by_frame = people_by_frame
        self.calls = 0

    def process_frame(self, image: np.ndarray) -> list:
        people = self.people_by_frame[min(self.calls, len(self.people_by_frame) - 1)]
        self.calls += 1
        return people


def run_two_frames(acc: CameraAccumulator) -> None:
    from mobisentra.vision.tracker import TrackedPerson

    acc.detector = StubDetector(
        [  # frame 0: 3/2 → overcrowded (silent first reading); frame 1: 0 → de-escalation row
            [
                TrackedPerson(track_id=i, bbox=(10.0, 10.0, 20.0, 30.0), confidence=0.9)
                for i in range(3)
            ],
            [],
        ]
    )
    for index in range(2):
        run_frame(
            acc,
            Frame(
                image=np.zeros((48, 64, 3), dtype=np.uint8),
                capture_ts=100.0 + index,
                frame_index=index,
                source_id="MSG_CAM",
            ),
            detect=True,
            draw_on=None,
        )


def test_publisher_attached_and_shared_with_stub_transport(tmp_path, monkeypatch):
    acc = make_acc(tmp_path, monkeypatch)
    other = CameraAccumulator(camera=acc.camera, reader=object())
    transport = StubTransport()
    config = tmp_config(tmp_path)
    handle = attach_messaging(
        [acc, other], config, transport_factory=lambda cfg: transport, start=False
    )
    assert acc.publisher is handle.publisher and other.publisher is handle.publisher

    run_two_frames(acc)
    assert handle.publisher.drain_once().sent >= 1
    assert len(transport.calls) >= 1
    topic, payload = transport.calls[0]
    assert topic == "mobisentra/events"
    assert payload.startswith('{"specversion"')

    handle.shutdown()
    assert transport.closed
    assert handle.publisher.stats().pending == 0


def test_envelopes_reach_both_sinks(tmp_path, monkeypatch):
    acc = make_acc(tmp_path, monkeypatch)
    transport = StubTransport()
    handle = attach_messaging(
        [acc], tmp_config(tmp_path), transport_factory=lambda cfg: transport, start=False
    )
    run_two_frames(acc)
    handle.publisher.drain_once()

    envelope_lines = (Path("runs/events") / "MSG_CAM.envelopes.jsonl").read_text().splitlines()
    assert len(envelope_lines) == len(transport.calls)  # JSONL sink AND the wire both got them


def test_publish_off_is_the_unchanged_default(tmp_path, monkeypatch):
    acc = make_acc(tmp_path, monkeypatch)
    assert acc.publisher is None  # attach_analytics never touches messaging
    run_two_frames(acc)
    assert (Path("runs/events") / "MSG_CAM.envelopes.jsonl").read_text().splitlines()
    assert not (tmp_path / "runs" / "spool").exists()  # no spool file materialized
