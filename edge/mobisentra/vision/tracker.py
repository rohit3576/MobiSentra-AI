"""Detection + tracking wrapper (Phase 2).

Thin, model-agnostic wrapper around ultralytics YOLO with ByteTrack/BoT-SORT.
Downstream analytics consume :class:`TrackedPerson` — never raw Results —
so the model (yolo26n vs yolo11n) stays a config value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class TrackedPerson:
    track_id: int
    bbox: tuple[float, float, float, float]
    confidence: float


def resolve_device(configured: str) -> str:
    """Resolve 'auto' to a concrete device string for ultralytics."""
    if configured != "auto":
        return configured
    override = os.environ.get("MOBISENTRA_DEVICE")
    if override:
        return override
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


class DetectorTracker:
    """One instance per camera (tracker state must not be shared).

    ``process_frame`` feeds ``model.track(..., persist=True)`` so track IDs
    persist across calls within this instance.
    """

    def __init__(
        self,
        *,
        model: str = "yolo26n.pt",
        conf: float = 0.3,
        classes: list[int] | None = None,
        imgsz: int = 640,
        tracker: str = "bytetrack.yaml",
        track_buffer: int | None = None,
        device: str = "auto",
    ) -> None:
        self._model_ref = model
        self._conf = conf
        self._classes = classes
        self._imgsz = imgsz
        self._tracker = tracker
        self._track_buffer = track_buffer
        self._device = resolve_device(device)
        self._model = None

    @property
    def device(self) -> str:
        return self._device

    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO

            self._model = YOLO(self._model_ref)
        return self._model

    def process_frame(self, image: np.ndarray) -> list[TrackedPerson]:
        model = self._ensure_model()
        kwargs: dict = {
            "persist": True,
            "tracker": self._tracker,
            "conf": self._conf,
            "imgsz": self._imgsz,
            "verbose": False,
        }
        if self._classes is not None:
            kwargs["classes"] = self._classes
        if self._track_buffer is not None:
            override_yaml = _tracker_override_path(self._tracker, self._track_buffer)
            if override_yaml is not None:
                kwargs["tracker"] = override_yaml
        results = model.track(image, device=self._device, **kwargs)
        if not results:
            return []
        return self._postprocess(results[0])

    def _postprocess(self, result) -> list[TrackedPerson]:
        return postprocess_results(result, tracked_classes=self._classes)


def postprocess_results(result, tracked_classes: list[int] | None) -> list[TrackedPerson]:
    """Convert one ultralytics Results object to TrackedPerson list.

    Pure function (mockable): boxes without track ids (early frames) are
    dropped; classes outside ``tracked_classes`` are skipped.
    """
    people: list[TrackedPerson] = []
    boxes = getattr(result, "boxes", None)
    if boxes is None or boxes.id is None:
        return people
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    ids = boxes.id.cpu().numpy().astype(int)
    clss = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else np.zeros_like(ids)
    for (x1, y1, x2, y2), conf, track_id, cls_id in zip(xyxy, confs, ids, clss, strict=True):
        if tracked_classes is not None and int(cls_id) not in tracked_classes:
            continue
        people.append(
            TrackedPerson(
                track_id=int(track_id),
                bbox=(float(x1), float(y1), float(x2), float(y2)),
                confidence=float(conf),
            )
        )
    return people


_TRACKER_CACHE: dict[tuple[str, int], str] = {}


def _tracker_override_path(base_name: str, track_buffer: int) -> str | None:
    """Write a tracker config copy with a custom track_buffer.

    ``base_name`` may be a preset ("bytetrack.yaml") or a path to a custom
    config; caching avoids rewriting the yaml per frame. Returns None when no
    base config can be resolved (the built-in default is then used unchanged).
    """
    import tempfile

    import yaml
    from ultralytics.utils import ROOT

    explicit = Path(base_name)
    if explicit.is_file():
        base = explicit
        stem = explicit.stem
    else:
        stem = base_name.removesuffix(".yaml")
        base = Path(ROOT) / "cfg" / "trackers" / f"{stem}.yaml"
        if not base.is_file():
            return None
    key = (str(base), track_buffer)
    if key not in _TRACKER_CACHE:
        cfg = yaml.safe_load(base.read_text())
        if "tracker" in cfg:
            cfg["tracker"]["track_buffer"] = track_buffer
        else:
            cfg["track_buffer"] = track_buffer
        safe_stem = stem.replace("/", "-")
        tmp = tempfile.NamedTemporaryFile("w", suffix=f"-mobisentra-{safe_stem}.yaml", delete=False)
        yaml.safe_dump(cfg, tmp)
        tmp.close()
        _TRACKER_CACHE[key] = tmp.name
    return _TRACKER_CACHE[key]


def cleanup_tracker_overrides() -> None:
    for path in _TRACKER_CACHE.values():
        try:
            os.unlink(path)
        except OSError:
            pass
    _TRACKER_CACHE.clear()
