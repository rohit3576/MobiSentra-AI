"""Streaming violence-action scoring via exported ONNX MoViNet (Phase 5, Step 5.1c).

Public surface mirrors the other vision wrappers: analytics consume
:class:`ActionScore` and never touch onnxruntime — the runtime is a config
value (the ``movinet_a2_explicit_states.onnx`` artifact exported by
``tools/export_movinet_onnx.py`` with the Step 5.1b winning recipe:
streaming state tensors flattened into the graph signature, fed back per
call). TensorFlow never enters the edge environment.

One instance per stream (or pair-crop stream): the 73 internal state
tensors carry temporal evidence between :meth:`ActionScorer.score` calls;
:meth:`ActionScorer.reset` starts a fresh evidence window — call it when
the tracked subject/clip changes, or evidence from the previous subject
leaks into the new one. Logit order is the engares training order
``['Fight', 'No_Fight']``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ACTION_RES = 224
LOGIT_FIGHT = 0
LOGIT_NO_FIGHT = 1

ORT_DTYPES: dict[str, np.dtype] = {
    "tensor(float)": np.dtype(np.float32),
    "tensor(double)": np.dtype(np.float64),
    "tensor(int32)": np.dtype(np.int32),
    "tensor(int64)": np.dtype(np.int64),
    "tensor(bool)": np.dtype(np.bool_),
}


@dataclass(frozen=True)
class ActionScore:
    fight: float
    no_fight: float
    logit_fight: float


def letterbox_bgr(frame_bgr: np.ndarray, size: int = ACTION_RES) -> np.ndarray:
    """cv2 mirror of tf.image.resize_with_pad: aspect-preserving resize,
    centered pad, RGB, float32 /255 (the engares training preprocessing)."""
    h, w = frame_bgr.shape[:2]
    scale = size / max(h, w)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    top, left = (size - nh) // 2, (size - nw) // 2
    canvas[top : top + nh, left : left + nw] = resized
    return canvas[..., ::-1].astype(np.float32) / np.float32(255.0)


def _softmax_pair(l_fight: float, l_no_fight: float) -> tuple[float, float]:
    m = max(l_fight, l_no_fight)
    ef, en = np.exp(l_fight - m), np.exp(l_no_fight - m)
    s = ef + en
    return float(ef / s), float(en / s)


class ActionScorer:
    """Streaming Fight/No_Fight scorer over one video stream.

    ``session`` injection is the test seam: production builds an
    onnxruntime InferenceSession from ``onnx_path`` (imported lazily —
    importing this module never pulls the runtime); tests pass any object
    with ``get_inputs``/``get_outputs``/``run``. The first few calls on a
    fresh real session include onnxruntime graph-optimization work —
    ``warmup_steps`` (default 3) runs black dummy frames at construction
    and resets, so first real score is steady-state.
    """

    def __init__(
        self,
        onnx_path: str | Path,
        *,
        session=None,
        warmup_steps: int = 3,
    ) -> None:
        self.onnx_path = Path(onnx_path)
        if session is None:
            import onnxruntime as ort

            session = ort.InferenceSession(
                str(self.onnx_path), providers=["CPUExecutionProvider"]
            )
        self._session = session
        inputs = list(session.get_inputs())
        self._image_input = inputs[0]
        state_inputs = inputs[1:]
        outputs = list(session.get_outputs())
        if len(state_inputs) + 1 != len(outputs):
            raise ValueError(
                f"expected logits + {len(state_inputs)} state outputs, got {len(outputs)}"
            )
        self._state_inputs = state_inputs
        self._output_names = [o.name for o in outputs]
        self._states: dict[str, np.ndarray] = {}
        provenance = self.onnx_path.with_suffix(".onnx.provenance.json")
        self.variant = ""
        if provenance.exists():
            meta = json.loads(provenance.read_text())
            self.variant = str(meta.get("variant", ""))
        self.reset()
        if warmup_steps:
            black = np.zeros((ACTION_RES, ACTION_RES, 3), dtype=np.uint8)
            for _ in range(warmup_steps):
                self.score(black)
            self.reset()

    def reset(self) -> None:
        """Zero all streaming state — start a fresh evidence window."""
        self._states = {
            inp.name: np.zeros(inp.shape, dtype=ORT_DTYPES[inp.type])
            for inp in self._state_inputs
        }

    def score(self, frame_bgr: np.ndarray) -> ActionScore:
        """One streaming step over a BGR frame; evidence accumulates until reset."""
        image = letterbox_bgr(frame_bgr)[np.newaxis, np.newaxis]
        feed = {self._image_input.name: image, **self._states}
        results = self._session.run(self._output_names, feed)
        logits = np.asarray(results[0]).ravel()
        for inp, value in zip(self._state_inputs, results[1:], strict=True):
            self._states[inp.name] = np.asarray(value)
        l_fight = float(logits[LOGIT_FIGHT])
        l_no_fight = float(logits[LOGIT_NO_FIGHT])
        fight, no_fight = _softmax_pair(l_fight, l_no_fight)
        return ActionScore(fight=fight, no_fight=no_fight, logit_fight=l_fight)
