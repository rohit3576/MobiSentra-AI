"""Run-loop event wiring tests (Phase 6, Step 6.3a).

Drives ``attach_analytics`` + ``run_frame`` with a stub detector so real
``CameraAnalytics`` rows flow through the real severity policy + event
engine into the envelope JSONL — every emitted line must validate against
the shared schemas, with ``model_versions`` stamped.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft7Validator

from mobisentra.ingestion.config import CameraConfig, Thresholds, ZoneConfig, ZoneType
from mobisentra.ingestion.frame import Frame
from mobisentra.pipeline import CameraAccumulator, attach_analytics, build_model_versions, run_frame
from mobisentra.vision.tracker import TrackedPerson

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "events" / "v0"
SEVERITY_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "severity.yaml"

FULL_FRAME_POLYGON = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


class StubDetector:
    produces_pose = False

    def __init__(self, people_by_frame: list[list[TrackedPerson]]) -> None:
        self.people_by_frame = people_by_frame
        self.calls = 0

    def process_frame(self, image: np.ndarray) -> list[TrackedPerson]:
        people = self.people_by_frame[min(self.calls, len(self.people_by_frame) - 1)]
        self.calls += 1
        return people


def person(track_id: int) -> TrackedPerson:
    return TrackedPerson(track_id=track_id, bbox=(10.0, 10.0, 20.0, 30.0), confidence=0.9)


def make_acc() -> CameraAccumulator:
    from mobisentra.vision.track_history import TrackHistory

    camera = CameraConfig(
        id="TEST_CAM",
        source="sample://videos/test.mp4",
        vehicle_id="TEST_BUS",
        zones={
            "hall": ZoneConfig(
                name="hall",
                zone_type=ZoneType.OCCUPANCY,
                polygon=FULL_FRAME_POLYGON,
                max_capacity=2,
            )
        },
        thresholds=Thresholds(occupancy_confirm_frames=1),
    )
    accumulator = CameraAccumulator(camera=camera, reader=object())
    accumulator.history = TrackHistory()
    return accumulator


@pytest.fixture
def acc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CameraAccumulator:
    monkeypatch.chdir(tmp_path)
    accumulator = make_acc()
    attach_analytics([accumulator], {"model": "stub-model.pt"}, severity_path=SEVERITY_CONFIG)
    return accumulator


def run_frames(accumulator: CameraAccumulator, detector: StubDetector, counts: list[int]) -> None:
    accumulator.detector = detector
    for index in range(len(counts)):
        frame = Frame(
            image=np.zeros((48, 64, 3), dtype=np.uint8),
            capture_ts=100.0 + index,
            frame_index=index,
            source_id="TEST_CAM",
        )
        run_frame(accumulator, frame, detect=True, draw_on=None)


def envelope_lines(acc: CameraAccumulator) -> list[dict]:
    path = Path("runs/events") / f"{acc.camera.id}.envelopes.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def row_lines(acc: CameraAccumulator) -> list[dict]:
    path = Path("runs/events") / f"{acc.camera.id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_occupancy_flow_emits_schema_valid_envelopes(acc, envelope_validator, event_validator):
    # frame 0: 3/2 = ratio 1.5 → overcrowded band establishes silently
    # frame 1: 0 people → de-escalation (occupancy_level_change, LOW, exempt)
    # frame 2: 3 people again → fresh escalation (overcrowding, HIGH at 1.5)
    detector = StubDetector(
        [[person(1), person(2), person(3)], [], [person(1), person(2), person(3)]]
    )
    run_frames(acc, detector, counts=[3, 0, 3])

    envelopes = envelope_lines(acc)
    assert [(env["data"]["event_type"], env["data"]["severity"]) for env in envelopes] == [
        ("occupancy_level_change", "LOW"),
        ("overcrowding", "HIGH"),
    ]
    for envelope in envelopes:
        assert envelope_validator.is_valid(envelope), envelope
        assert event_validator.is_valid(envelope["data"]), envelope
    assert all(env["source"] == "/mobisentra/edge/TEST_BUS/TEST_CAM" for env in envelopes)
    assert all(env["type"].startswith("org.mobisentra.event.") for env in envelopes)


def test_model_versions_stamped_from_detection_config(acc):
    detector = StubDetector([[person(1), person(2), person(3)], []])
    run_frames(acc, detector, counts=[3, 0])
    for envelope in envelope_lines(acc):
        assert envelope["data"]["model_versions"] == {"detector": "stub-model.pt"}


def test_raw_candidate_rows_unchanged_shape(acc):
    detector = StubDetector([[person(1), person(2), person(3)], []])
    run_frames(acc, detector, counts=[3, 0])
    rows = row_lines(acc)
    assert len(rows) == 1  # same single event, pre-envelope shape
    assert rows[0]["kind"] == "occupancy_level_change"
    assert "severity" not in rows[0]
    assert "specversion" not in rows[0]


@pytest.fixture(scope="module")
def envelope_validator() -> Draft7Validator:
    schema = json.loads((SCHEMAS / "envelope.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


@pytest.fixture(scope="module")
def event_validator() -> Draft7Validator:
    schema = json.loads((SCHEMAS / "event.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


# --- build_model_versions ------------------------------------------------------


def test_model_versions_detector_only():
    assert build_model_versions({"model": "yolo26n-pose.pt"}) == {"detector": "yolo26n-pose.pt"}


def test_model_versions_unspecified_when_model_missing():
    assert build_model_versions({}) == {"detector": "unspecified"}


def test_model_versions_action_onnx_sha8(tmp_path):
    onnx = tmp_path / "movinet_a2_explicit_states.onnx"
    onnx.write_bytes(b"fake-weights")
    versions = build_model_versions({"model": "yolo26n-pose.pt"}, action_onnx=onnx)
    expected_sha = hashlib.sha256(b"fake-weights").hexdigest()[:8]
    assert versions == {
        "detector": "yolo26n-pose.pt",
        "action": f"movinet_a2_explicit_states@{expected_sha}",
    }


def test_model_versions_missing_action_onnx_fails_fast(tmp_path):
    with pytest.raises(ValueError, match="action model not found"):
        build_model_versions({"model": "m.pt"}, action_onnx=tmp_path / "absent.onnx")


# --- startup guards ------------------------------------------------------------


def test_missing_severity_config_clear_startup_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="severity config not found"):
        attach_analytics([make_acc()], {"model": "m.pt"}, severity_path=tmp_path / "absent.yaml")


def test_invalid_severity_config_aborts_startup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    broken = tmp_path / "severity.yaml"
    mutated = SEVERITY_CONFIG.read_text().replace("fall_detected: HIGH", "fall_detected: EXTREME")
    broken.write_text(mutated)
    with pytest.raises(SystemExit, match="EXTREME"):
        attach_analytics([make_acc()], {"model": "m.pt"}, severity_path=broken)
