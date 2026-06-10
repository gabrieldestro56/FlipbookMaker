"""Shared pytest fixtures and helpers."""
import os
import sys

# Enable EXR support before cv2 is imported anywhere in the test process.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import cv2
import numpy as np
import pytest

# Make the project root importable (core, app live one level up).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_video(path_stem: str, n: int = 30, w: int = 96, h: int = 64,
               fps: float = 24.0) -> str:
    """
    Write a short, deliberately NON-square test clip whose brightness ramps
    from dark to bright (so start/end differ for loop tests) with a moving
    red marker (so optical flow has something to track). Returns the actual
    path written (extension depends on the codec that worked).
    """
    for fourcc_name, ext in (("mp4v", ".mp4"), ("MJPG", ".avi")):
        path = path_stem + ext
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*fourcc_name),
                                 fps, (w, h))
        if not writer.isOpened():
            writer.release()
            continue
        for i in range(n):
            val = int(255 * i / (n - 1))
            frame = np.full((h, w, 3), val, dtype=np.uint8)
            x = int((w - 10) * i / max(1, n - 1))
            frame[h // 2 - 4:h // 2 + 4, x:x + 10] = (0, 0, 255)
            writer.write(frame)
        writer.release()
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    raise RuntimeError("No usable OpenCV video codec available for tests")


def ramp_frames(n: int, cell: int = 32) -> list:
    """A list of solid-gray frames ramping 0..255 — handy for seam math tests."""
    out = []
    for i in range(n):
        val = int(round(255 * i / max(1, n - 1)))
        out.append(np.full((cell, cell, 3), val, dtype=np.uint8))
    return out


def color_frames(n: int, cell: int = 16) -> list:
    """Frames with a unique, recoverable color per index (for placement tests)."""
    return [np.full((cell, cell, 3), (i % 256), dtype=np.uint8) for i in range(n)]


def seam_delta(seq: list) -> float:
    """Mean absolute difference across the loop seam (last frame -> first)."""
    a = seq[-1].astype(np.float32)
    b = seq[0].astype(np.float32)
    return float(np.mean(np.abs(a - b)))


@pytest.fixture(scope="session")
def video_path(tmp_path_factory) -> str:
    stem = str(tmp_path_factory.mktemp("media") / "clip")
    return make_video(stem)


@pytest.fixture(scope="session")
def not_a_video(tmp_path_factory) -> str:
    p = str(tmp_path_factory.mktemp("media") / "notes.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("this is not a video file")
    return p
