"""
Flipbook Maker - core engine.

Turns a video into a square sprite-sheet ("flipbook") ready for game engines.

Key features:
  * Even temporal frame sampling across the whole clip.
  * Center-square crop so every cell is a perfect square.
  * Linear-light (gamma correct) blending so cross-fades don't darken.
  * Seamless looping via a motion-aware cross-blend at the loop seam.
  * Optional optical-flow motion compensation for sharper loops.

The math behind the seamless loop is documented in `_build_looping_sequence`.
"""

from __future__ import annotations

import dataclasses
import os
from typing import List, Optional

# Must be set before cv2 is imported anywhere in the process to enable .exr I/O.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import cv2
import numpy as np


# --------------------------------------------------------------------------- #
# Video probing / decoding
# --------------------------------------------------------------------------- #

# Hard cap on decoded frames so a very long clip can't exhaust memory. The
# source is strided down to this many frames before any sampling happens.
MAX_DECODE_FRAMES = 2400


@dataclasses.dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def probe(path: str) -> VideoInfo:
    """Return basic metadata about a video file."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    duration = (count / fps) if fps > 0 else 0.0
    return VideoInfo(width, height, fps, count, duration)


def _center_square_crop(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    side = min(h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    return frame[y0:y0 + side, x0:x0 + side]


def decode_square_frames(path: str, cell: int) -> List[np.ndarray]:
    """
    Decode the entire clip into a list of square BGR uint8 frames sized
    `cell` x `cell`. Sequential decoding (not seeking) keeps this robust
    across codecs that report inaccurate frame indices.
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {path}")

    reported = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    # If the clip is huge, decode only every Nth frame to stay under the cap.
    stride = max(1, reported // MAX_DECODE_FRAMES) if reported else 1

    frames: List[np.ndarray] = []
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride == 0:
                sq = _center_square_crop(frame)
                sq = cv2.resize(sq, (cell, cell), interpolation=cv2.INTER_AREA)
                frames.append(sq)
            idx += 1
    finally:
        cap.release()

    if not frames:
        raise ValueError("No frames could be decoded from the video.")
    return frames


# --------------------------------------------------------------------------- #
# Linear-light blending helpers
# --------------------------------------------------------------------------- #

def _srgb_to_linear(x: np.ndarray) -> np.ndarray:
    a = 0.055
    return np.where(x <= 0.04045, x / 12.92, ((x + a) / (1 + a)) ** 2.4)


def _linear_to_srgb(x: np.ndarray) -> np.ndarray:
    a = 0.055
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, (1 + a) * (x ** (1 / 2.4)) - a)


def blend(a: np.ndarray, b: np.ndarray, wa: float) -> np.ndarray:
    """
    Blend two uint8 BGR frames in linear light: result = wa*a + (1-wa)*b.
    Working in linear light avoids the classic cross-fade darkening.
    """
    af = _srgb_to_linear(a.astype(np.float32) / 255.0)
    bf = _srgb_to_linear(b.astype(np.float32) / 255.0)
    out = wa * af + (1.0 - wa) * bf
    out = _linear_to_srgb(out) * 255.0
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def _warp_toward(src: np.ndarray, dst: np.ndarray, t: float) -> np.ndarray:
    """
    Motion-compensate `src` a fraction `t` of the way toward `dst` using
    dense optical flow. Used by the 'optical' loop mode for sharper cross-fades.
    Falls back to the unwarped frame if flow estimation fails.
    """
    try:
        g_src = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
        g_dst = cv2.cvtColor(dst, cv2.COLOR_BGR2GRAY)
        flow = cv2.calcOpticalFlowFarneback(
            g_src, g_dst, None,
            pyr_scale=0.5, levels=3, winsize=21,
            iterations=3, poly_n=5, poly_sigma=1.2, flags=0,
        )
        h, w = src.shape[:2]
        grid_x, grid_y = np.meshgrid(np.arange(w), np.arange(h))
        map_x = (grid_x + flow[..., 0] * t).astype(np.float32)
        map_y = (grid_y + flow[..., 1] * t).astype(np.float32)
        return cv2.remap(src, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
    except Exception:
        return src


# --------------------------------------------------------------------------- #
# Frame sequence construction
# --------------------------------------------------------------------------- #

def _sample(frames: List[np.ndarray], n: int) -> List[np.ndarray]:
    """Evenly sample `n` frames across the whole clip."""
    if n <= 0:
        return []
    if len(frames) == 1:
        return [frames[0]] * n
    idx = np.linspace(0, len(frames) - 1, n)
    return [frames[int(round(i))] for i in idx]


def _build_looping_sequence(
    frames: List[np.ndarray],
    n: int,
    blend_frames: int,
    mode: str,
) -> List[np.ndarray]:
    """
    Build `n` frames that loop seamlessly using a cross-blend at the seam.

    We sample M = n + B frames evenly across the clip (B = blend_frames). The
    first n frames are the loop body; the trailing B frames are the *natural
    motion continuation* just past the seam. For the first B output frames we
    cross-fade from that continuation back into the head of the clip:

        result[i] = blend(head=P[i], tail=P[n+i], wa = (i+1)/(B+1))   for i < B
        result[i] = P[i]                                              otherwise

    At the seam result[n-1]=P[n-1] is followed by result[0] ~= P[n] (the frame
    that naturally comes next), so motion never pops; over the next B frames the
    cross-fade dissolves back to the clip's start. This hides any mismatch
    between the clip's end and beginning.
    """
    B = max(0, min(blend_frames, n - 1))
    if B == 0:
        return _sample(frames, n)

    picks = _sample(frames, n + B)
    out: List[np.ndarray] = []
    for i in range(n):
        if i < B:
            wa = (i + 1) / (B + 1)
            head = picks[i]
            tail = picks[n + i]
            if mode == "optical":
                # Warp both toward the midpoint for a sharper dissolve.
                head_w = _warp_toward(head, tail, 1.0 - wa)
                tail_w = _warp_toward(tail, head, wa)
                out.append(blend(head_w, tail_w, wa))
            else:
                out.append(blend(head, tail, wa))
        else:
            out.append(picks[i])
    return out


def build_sequence(
    frames: List[np.ndarray],
    cols: int,
    rows: int,
    loop: bool,
    blend_frames: int,
    mode: str,
) -> List[np.ndarray]:
    n = cols * rows
    if loop:
        return _build_looping_sequence(frames, n, blend_frames, mode)
    return _sample(frames, n)


def compose_sheet(seq: List[np.ndarray], cols: int, rows: int, cell: int) -> np.ndarray:
    """Arrange frames left-to-right, top-to-bottom into one sprite sheet."""
    sheet = np.zeros((rows * cell, cols * cell, 3), dtype=np.uint8)
    for i, f in enumerate(seq):
        if i >= cols * rows:
            break
        r, c = divmod(i, cols)
        sheet[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = f
    return sheet


# --------------------------------------------------------------------------- #
# Top-level entry point
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class FlipbookResult:
    sheet_bgr: np.ndarray
    cols: int
    rows: int
    cell: int
    frame_count: int
    sheet_width: int
    sheet_height: int


def make_flipbook(
    path: str,
    cols: int,
    rows: int,
    cell: int,
    loop: bool = True,
    blend_frames: int = 4,
    mode: str = "linear",
    frames: Optional[List[np.ndarray]] = None,
) -> FlipbookResult:
    """
    Build a flipbook sprite sheet from a video.

    `frames` may be passed in to reuse an already-decoded clip (the server
    caches these so tweaking grid/loop settings is instant).
    """
    if cols < 1 or rows < 1:
        raise ValueError("cols and rows must be >= 1")
    if cell < 1:
        raise ValueError("cell must be >= 1")
    if frames is None:
        frames = decode_square_frames(path, cell)
    if not frames:
        raise ValueError("No frames available to build a flipbook.")
    if frames[0].shape[0] != cell:
        interp = cv2.INTER_AREA if frames[0].shape[0] > cell else cv2.INTER_CUBIC
        frames = [cv2.resize(f, (cell, cell), interpolation=interp)
                  for f in frames]

    seq = build_sequence(frames, cols, rows, loop, blend_frames, mode)
    sheet = compose_sheet(seq, cols, rows, cell)
    return FlipbookResult(
        sheet_bgr=sheet,
        cols=cols,
        rows=rows,
        cell=cell,
        frame_count=cols * rows,
        sheet_width=cols * cell,
        sheet_height=rows * cell,
    )
