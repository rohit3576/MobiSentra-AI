"""Fight wiring tests (Phase 5, Step 5.3).

End-to-end minus the real model: two animated tracks through
``CameraAnalytics.process`` with a session-stubbed ``ActionScorer``
(high fight score) → ``altercation_suspected`` row; the same motion with a
low-score stub stays silent (the model alone never alerts, and the
geometry alone never alerts). Also: forget propagation to the pair finder
and fight detector.
"""

from __future__ import annotations

import numpy as np
from test_action import FakeSession

from mobisentra.analytics.engine import CameraAnalytics
from mobisentra.ingestion.config import CameraConfig
from mobisentra.vision.action import ActionScorer
from mobisentra.vision.tracker import TrackedPerson

FRAME = np.full((200, 300, 3), 40, dtype=np.uint8)
DT = 0.2


def high_score_factory():
    return ActionScorer(
        "unused.onnx", session=FakeSession(n_states=1, logits=(2.0, 0.0)), warmup_steps=0
    )


def low_score_factory():
    return ActionScorer(
        "unused.onnx", session=FakeSession(n_states=1, logits=(-2.0, 2.0)), warmup_steps=0
    )


def make_analytics(factory) -> CameraAnalytics:
    return CameraAnalytics(
        CameraConfig(id="CAM_FIGHT", source="sample://videos/f.mp4", vehicle_id="V", zones={}),
        action_scorer_factory=factory,
    )


def fight_motion_people(step: int, jitter: float) -> list[TrackedPerson]:
    sign = 1.0 if step % 2 == 0 else -1.0
    base = 100.0 + sign * jitter * step
    contact_dx = 20.0 if (step // 2) % 2 == 0 else 90.0
    return [
        TrackedPerson(track_id=1, bbox=(base, 40.0, base + 80.0, 160.0), confidence=0.9),
        TrackedPerson(
            track_id=2, bbox=(base + contact_dx, 40.0, base + contact_dx + 80.0, 160.0),
            confidence=0.9,
        ),
    ]


def feed(analytics: CameraAnalytics, frames: int) -> list[dict]:
    rows: list[dict] = []
    for step in range(frames):
        ts = step * DT
        rows.extend(analytics.process(ts, FRAME, fight_motion_people(step, jitter=6.0)))
    return rows


def test_fight_row_emitted_with_high_score_stub() -> None:
    analytics = make_analytics(high_score_factory)
    rows = feed(analytics, 30)
    fights = [row for row in rows if row["kind"] == "altercation_suspected"]
    assert len(fights) == 1
    row = fights[0]
    assert row["camera_id"] == "CAM_FIGHT"
    assert {row["track_a"], row["track_b"]} == {1, 2}
    assert row["confidence"] > 0.5
    assert row["action_score"] > 0.6


def test_same_motion_low_score_stays_silent() -> None:
    analytics = make_analytics(low_score_factory)
    rows = feed(analytics, 30)
    assert [row for row in rows if row["kind"] == "altercation_suspected"] == []


def test_no_factory_means_no_fight_path() -> None:
    analytics = CameraAnalytics(
        CameraConfig(id="CAM_PLAIN", source="sample://videos/f.mp4", vehicle_id="V", zones={})
    )
    assert analytics.pending_fight_pair_ids() == []
    rows = feed(analytics, 5)
    assert rows == []


def test_forget_propagates_to_fight_state() -> None:
    analytics = make_analytics(high_score_factory)
    feed(analytics, 30)
    assert analytics.pending_fight_pair_ids() == [(1, 2)]
    analytics.forget([1])
    assert analytics.pending_fight_pair_ids() == []
    assert analytics._scorers == {}


def test_fight_row_carries_pair_evidence_clip(tmp_path) -> None:
    """Phase 6, 6.3b — fight fires stamp a playable evidence_ref + sidecar
    (the buffer runs even without a pose history)."""
    import json
    from pathlib import Path

    analytics = CameraAnalytics(
        CameraConfig(id="CAM_FIGHT", source="sample://videos/f.mp4", vehicle_id="V", zones={}),
        action_scorer_factory=high_score_factory,
        evidence_root=tmp_path,
    )
    rows = feed(analytics, 30)
    fights = [row for row in rows if row["kind"] == "altercation_suspected"]
    assert len(fights) == 1
    evidence_ref = fights[0].get("evidence_ref")
    assert evidence_ref is not None
    clip = Path(evidence_ref)
    assert clip.name.startswith("fight_track")
    assert clip.suffix == ".mp4"
    assert clip.is_file()
    sidecar = clip.with_suffix(".keypoints.json")
    payload = json.loads(sidecar.read_text())
    assert payload["kind"] == "fight"
    assert sorted(payload["tracks"]) == [1, 2]
    assert payload["samples"] == []  # no pose history in this composition


def test_fight_row_without_writer_has_no_evidence_ref() -> None:
    analytics = make_analytics(high_score_factory)
    rows = feed(analytics, 30)
    fights = [row for row in rows if row["kind"] == "altercation_suspected"]
    assert len(fights) == 1
    assert "evidence_ref" not in fights[0]
