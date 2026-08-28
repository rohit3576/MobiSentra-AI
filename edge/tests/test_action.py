"""ActionScorer unit tests (Phase 5, Step 5.1c).

Session-stubbed: onnxruntime is never imported — FakeSession reproduces the
InferenceSession duck type (get_inputs/get_outputs/run) with deterministic
outputs that make streaming-state carry observable.
"""

from __future__ import annotations

import numpy as np
import pytest

from mobisentra.vision.action import ACTION_RES, ActionScorer, letterbox_bgr

BLACK = np.zeros((ACTION_RES, ACTION_RES, 3), dtype=np.uint8)


class _TensorSpec:
    def __init__(self, name: str, type_: str, shape: list[int]) -> None:
        self.name = name
        self.type = type_
        self.shape = shape


class FakeSession:
    """logits = [sum(fed states) + 10 * (image != 0).mean() rounded via frame
    marker, 0.0]; new state_i = fed state_i + 1. Call 1 feeds zeros, so the
    next call's feed exposes exactly what the scorer carried."""

    def __init__(self, n_states: int = 2, logits: tuple[float, float] | None = None) -> None:
        self.inputs = [
            _TensorSpec("image", "tensor(float)", [1, 1, ACTION_RES, ACTION_RES, 3]),
            *(
                _TensorSpec(
                    f"state_{i}",
                    "tensor(int32)" if i == 0 else "tensor(float)",
                    [1, 1],
                )
                for i in range(n_states)
            ),
        ]
        self.outputs = [
            _TensorSpec("logits", "tensor(float)", [1, 2]),
            *(_TensorSpec(f"state_out_{i}", "tensor(float)", [1, 1]) for i in range(n_states)),
        ]
        self.fixed_logits = logits
        self.calls: list[dict[str, np.ndarray]] = []

    def get_inputs(self) -> list[_TensorSpec]:
        return self.inputs

    def get_outputs(self) -> list[_TensorSpec]:
        return self.outputs

    def run(self, names: list[str], feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.calls.append(feed)
        state_sum = sum(int(np.sum(feed[f"state_{i}"])) for i in range(len(self.inputs) - 1))
        if self.fixed_logits is not None:
            logits = np.array(self.fixed_logits, dtype=np.float32)
        else:
            logits = np.array([float(state_sum), 0.0], dtype=np.float32)
        n_states = len(self.inputs) - 1
        new_states = [feed[f"state_{i}"].astype(np.float32) + 1.0 for i in range(n_states)]
        return [logits, *new_states]


def test_letterbox_landscape_letterboxes_vertically() -> None:
    frame = np.zeros((320, 640, 3), dtype=np.uint8)
    frame[:, :, 1] = 255
    out = letterbox_bgr(frame)
    assert out.shape == (ACTION_RES, ACTION_RES, 3)
    assert out.dtype == np.float32
    nh = round(320 * ACTION_RES / 640)
    top = (ACTION_RES - nh) // 2
    assert out[top + nh // 2, ACTION_RES // 2, 1] == pytest.approx(1.0)
    assert out[0, ACTION_RES // 2, 1] == pytest.approx(0.0)
    assert out[-1, ACTION_RES // 2, 1] == pytest.approx(0.0)


def test_letterbox_portrait_pillarboxes() -> None:
    frame = np.zeros((640, 320, 3), dtype=np.uint8)
    frame[:, :, 1] = 255
    out = letterbox_bgr(frame)
    nw = round(320 * ACTION_RES / 640)
    left = (ACTION_RES - nw) // 2
    assert out[ACTION_RES // 2, left + nw // 2, 1] == pytest.approx(1.0)
    assert out[ACTION_RES // 2, 0, 1] == pytest.approx(0.0)


def test_letterbox_bgr_to_rgb_and_scales() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[:, :, 2] = 255
    out = letterbox_bgr(frame)
    assert out[..., 0] == pytest.approx(1.0)
    assert out[..., 1] == pytest.approx(0.0)
    assert out[..., 2] == pytest.approx(0.0)


def test_state_carries_between_calls() -> None:
    fake = FakeSession(n_states=2)
    scorer = ActionScorer("unused.onnx", session=fake, warmup_steps=0)
    scorer.score(BLACK)
    scorer.score(BLACK)
    second_feed = fake.calls[1]
    assert int(np.sum(second_feed["state_0"])) == 1
    assert int(np.sum(second_feed["state_1"])) == 1


def test_logits_accumulate_with_carried_state() -> None:
    fake = FakeSession(n_states=2)
    scorer = ActionScorer("unused.onnx", session=fake, warmup_steps=0)
    first = scorer.score(BLACK)
    second = scorer.score(BLACK)
    assert first.logit_fight == pytest.approx(0.0)
    assert second.logit_fight == pytest.approx(2.0)


def test_reset_zeroes_state_window() -> None:
    fake = FakeSession(n_states=2)
    scorer = ActionScorer("unused.onnx", session=fake, warmup_steps=0)
    scorer.score(BLACK)
    scorer.score(BLACK)
    scorer.reset()
    after = scorer.score(BLACK)
    assert int(np.sum(fake.calls[-1]["state_0"])) == 0
    assert after.logit_fight == pytest.approx(0.0)


def test_probabilities_are_softmax_of_logits() -> None:
    fake = FakeSession(n_states=1, logits=(2.0, 0.0))
    scorer = ActionScorer("unused.onnx", session=fake, warmup_steps=0)
    score = scorer.score(BLACK)
    assert score.fight == pytest.approx(0.8807970779778823)
    assert score.no_fight == pytest.approx(1.0 - 0.8807970779778823)


def test_int32_state_keeps_dtype_in_feed() -> None:
    fake = FakeSession(n_states=2)
    scorer = ActionScorer("unused.onnx", session=fake, warmup_steps=0)
    scorer.score(BLACK)
    assert fake.calls[0]["state_0"].dtype == np.int32
    assert fake.calls[0]["state_1"].dtype == np.float32


def test_warmup_runs_then_resets() -> None:
    fake = FakeSession(n_states=1)
    scorer = ActionScorer("unused.onnx", session=fake, warmup_steps=2)
    assert len(fake.calls) == 2
    scorer.score(BLACK)
    assert len(fake.calls) == 3
    assert int(np.sum(fake.calls[-1]["state_0"])) == 0


def test_mismatched_outputs_rejected() -> None:
    fake = FakeSession(n_states=2)
    fake.outputs = fake.outputs[:2]
    with pytest.raises(ValueError, match="state outputs"):
        ActionScorer("unused.onnx", session=fake, warmup_steps=0)
