"""Generate the bundled synthetic sample clips (runs-without-hardware).

Usage: uv run python tools/make_sample_clips.py [--outdir sample_data/videos]

Clips are project-generated synthetic footage (no license restrictions):
moving "passenger" blocks over static interior backgrounds, three lighting
conditions. Deterministic seeds keep regeneration reproducible.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

SCENES = {
    "bus_interior_01.mp4": dict(size=(1280, 720), fps=30.0, seconds=30, theme="day"),
    "metro_coach_01.mp4": dict(size=(1920, 1080), fps=24.0, seconds=20, theme="night"),
    "platform_02.mp4": dict(size=(1280, 720), fps=30.0, seconds=30, theme="dusk"),
}

THEMES = {
    "day": dict(bg=(52, 50, 46), fixture=(80, 78, 72), people=(200, 180, 160), n=14),
    "night": dict(bg=(14, 12, 10), fixture=(24, 22, 20), people=(90, 80, 70), n=10),
    "dusk": dict(bg=(32, 30, 34), fixture=(52, 48, 50), people=(170, 150, 140), n=18),
}


@dataclass
class Mover:
    x: float
    y: float
    w: int
    h: int
    vx: float
    vy: float

    def step(self, bounds: tuple[int, int]) -> None:
        self.x += self.vx
        self.y += self.vy
        if self.x < 0 or self.x + self.w > bounds[0]:
            self.vx *= -1
            self.x = max(0, min(self.x, bounds[0] - self.w))
        if self.y < 0 or self.y + self.h > bounds[1]:
            self.vy *= -1
            self.y = max(0, min(self.y, bounds[1] - self.h))


def draw_background(frame: np.ndarray, rng: random.Random, theme: dict) -> None:
    h, w = frame.shape[:2]
    frame[:] = theme["bg"]
    for _ in range(6):
        x = rng.randrange(0, w - 120)
        y = rng.randrange(0, max(1, h - 160))
        cv2.rectangle(
            frame,
            (x, y),
            (x + rng.randrange(60, 120), y + rng.randrange(40, 90)),
            theme["fixture"],
            -1,
        )
    for y in range(0, h, 90):
        cv2.line(frame, (0, y), (w, y), theme["fixture"], 1)


def make_clip(path: Path, size: tuple[int, int], fps: float, seconds: int, theme_name: str) -> None:
    rng = random.Random(hash(path.name) & 0xFFFF)
    theme = THEMES[theme_name]
    w, h = size
    movers = [
        Mover(
            x=rng.randrange(0, w - 60),
            y=rng.randrange(0, h - 120),
            w=rng.randrange(24, 46),
            h=rng.randrange(60, 110),
            vx=rng.uniform(-3.5, 3.5),
            vy=rng.uniform(-1.2, 1.2),
        )
        for _ in range(theme["n"])
    ]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    background = np.empty((h, w, 3), dtype=np.uint8)
    draw_background(background, rng, theme)
    for _ in range(int(fps * seconds)):
        frame = background.copy()
        for m in movers:
            m.step((w, h))
            cv2.rectangle(
                frame, (int(m.x), int(m.y)), (int(m.x) + m.w, int(m.y) + m.h), theme["people"], -1
            )
        writer.write(frame)
    writer.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = Path(__file__).parents[1] / "sample_data" / "videos"
    parser.add_argument("--outdir", type=Path, default=default_out)
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    for name, cfg in SCENES.items():
        path = args.outdir / name
        make_clip(path, cfg["size"], cfg["fps"], cfg["seconds"], cfg["theme"])
        print(f"{path} ({path.stat().st_size / 1e6:.1f} MB, {cfg['theme']})")


if __name__ == "__main__":
    main()
