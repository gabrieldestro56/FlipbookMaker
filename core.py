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
import re
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

# Still-image formats we accept for an image-sequence flipbook.
IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".tga",
    ".tif", ".tiff", ".webp", ".exr",
}


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
# Image-sequence probing / decoding
# --------------------------------------------------------------------------- #

def _natural_key(path: str):
    """Sort key that orders `frame_0, frame_1, ... frame_10` numerically.

    We key on the *last* run of digits in the filename (e.g. the `7` in
    `Muzzle_Side_7.png`) so a sequence starting at 0 loads in true order,
    and fall back to a case-insensitive name compare when there's no number.
    """
    name = os.path.basename(path)
    nums = re.findall(r"\d+", name)
    return (int(nums[-1]) if nums else -1, name.lower())


def list_image_files(folder: str) -> List[str]:
    """Return absolute paths to the images in `folder`, in sequence order."""
    if not os.path.isdir(folder):
        raise ValueError(f"Not a folder: {folder}")
    files = [
        os.path.join(folder, n)
        for n in os.listdir(folder)
        if os.path.splitext(n)[1].lower() in IMAGE_EXTS
    ]
    if not files:
        raise ValueError(f"No supported images found in: {folder}")
    files.sort(key=_natural_key)
    return files


def probe_images(folder: str, fps: float = 24.0) -> VideoInfo:
    """Metadata for an image-sequence 'clip' (frame_count = number of images)."""
    files = list_image_files(folder)
    first = _imread_any(files[0])
    if first is None:
        raise ValueError(f"Could not read image: {files[0]}")
    h, w = first.shape[:2]
    count = len(files)
    duration = (count / fps) if fps > 0 else 0.0
    return VideoInfo(w, h, fps, count, duration)


def _imread_any(path: str) -> Optional[np.ndarray]:
    """Read an image as BGR or BGRA uint8, preserving any alpha channel.

    Uses np.fromfile so non-ASCII / Windows paths decode reliably.
    """
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    except Exception:
        img = None
    if img is None:
        return None
    if img.ndim == 2:  # grayscale -> BGR
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.dtype != np.uint8:  # normalize 16-bit / float sources to 8-bit
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    if img.shape[2] == 4:
        return img  # BGRA
    return img[..., :3]  # drop any extra planes, keep BGR


def decode_square_images(paths: List[str], cell: int) -> List[np.ndarray]:
    """
    Decode an image sequence into square uint8 frames sized `cell`x`cell`.

    If *any* image carries an alpha channel, every frame is returned as BGRA so
    the whole sheet keeps a real cutout (muzzle flashes, decals, etc.). Frames
    are center-square cropped then area-resampled, exactly like the video path.
    """
    if not paths:
        raise ValueError("No images to decode.")
    raw: List[np.ndarray] = []
    for p in paths:
        img = _imread_any(p)
        if img is None:
            raise ValueError(f"Could not read image: {p}")
        raw.append(img)

    want_alpha = any(im.shape[2] == 4 for im in raw)
    frames: List[np.ndarray] = []
    for im in raw:
        if want_alpha and im.shape[2] == 3:
            a = np.full(im.shape[:2] + (1,), 255, np.uint8)
            im = np.concatenate([im, a], axis=2)
        elif not want_alpha and im.shape[2] == 4:
            im = im[..., :3]
        sq = _center_square_crop(im)
        sq = cv2.resize(sq, (cell, cell), interpolation=cv2.INTER_AREA)
        frames.append(sq)
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


def _blend_rgb(a: np.ndarray, b: np.ndarray, wa: float) -> np.ndarray:
    af = _srgb_to_linear(a.astype(np.float32) / 255.0)
    bf = _srgb_to_linear(b.astype(np.float32) / 255.0)
    out = wa * af + (1.0 - wa) * bf
    out = _linear_to_srgb(out) * 255.0
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def blend(a: np.ndarray, b: np.ndarray, wa: float) -> np.ndarray:
    """
    Blend two uint8 frames: result = wa*a + (1-wa)*b.

    Color is mixed in linear light to avoid the classic cross-fade darkening.
    A 4th (alpha) channel, if present, is mixed linearly (alpha is already a
    coverage value, not a gamma-encoded color).
    """
    if a.shape[-1] == 4:
        color = _blend_rgb(a[..., :3], b[..., :3], wa)
        alpha = wa * a[..., 3].astype(np.float32) + (1.0 - wa) * b[..., 3].astype(np.float32)
        alpha = np.clip(alpha + 0.5, 0, 255).astype(np.uint8)
        return np.dstack([color, alpha])
    return _blend_rgb(a, b, wa)


def _warp_toward(src: np.ndarray, dst: np.ndarray, t: float) -> np.ndarray:
    """
    Motion-compensate `src` a fraction `t` of the way toward `dst` using
    dense optical flow. Used by the 'optical' loop mode for sharper cross-fades.
    Falls back to the unwarped frame if flow estimation fails.
    """
    try:
        g_src = cv2.cvtColor(src[..., :3], cv2.COLOR_BGR2GRAY)
        g_dst = cv2.cvtColor(dst[..., :3], cv2.COLOR_BGR2GRAY)
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


def chroma_key(img: np.ndarray, threshold: int = 60) -> np.ndarray:
    """Remove a green screen: return a BGRA copy of a BGR/BGRA uint8 image.

    "Green excess" = G - max(R, B) is brightness-independent, so lit and shadowed
    parts of the screen key alike. Pixels with excess >= `threshold` go fully
    transparent, <= threshold/2 stay opaque, with a soft ramp between for edges.
    Green spill is then clamped (G <= max(R, B)) so edges don't keep a green fringe.
    Any existing alpha is kept (the result is the min of both).
    """
    bgr = img[..., :3].astype(np.int16)
    b, g, r = bgr[..., 0], bgr[..., 1], bgr[..., 2]
    rb = np.maximum(r, b)
    lo = threshold / 2.0
    keep = 1.0 - np.clip((g - rb - lo) / max(threshold - lo, 1e-6), 0.0, 1.0)
    alpha = np.clip(keep * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if img.shape[-1] == 4:
        alpha = np.minimum(alpha, img[..., 3])
    out = img[..., :3].copy()
    out[..., 1] = np.minimum(g, rb).astype(np.uint8)
    return np.dstack([out, alpha])


def compose_sheet(seq: List[np.ndarray], cols: int, rows: int, cell: int) -> np.ndarray:
    """Arrange frames left-to-right, top-to-bottom into one sprite sheet.

    The channel count follows the frames (3 = BGR, 4 = BGRA). Any cell not
    covered by a frame is left as zeros — transparent when the sheet is BGRA.
    """
    channels = seq[0].shape[2] if seq else 3
    sheet = np.zeros((rows * cell, cols * cell, channels), dtype=np.uint8)
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


def suggest_grid(n: int) -> tuple[int, int]:
    """Smallest square grid that holds `n` frames, e.g. 8 -> 3x3, 16 -> 4x4."""
    if n <= 1:
        return 1, 1
    side = int(np.ceil(np.sqrt(n)))
    return side, side


def auto_cell(native: int, cols: int, rows: int, max_dim: int = 8192,
              min_cell: int = 16) -> int:
    """Pick a per-frame cell size that keeps the source's native resolution
    while ensuring the whole sheet fits inside `max_dim` (e.g. 8K).

    `native` is the source's square edge (min of width/height). The sheet's
    longest edge is max(cols, rows) * cell, so the largest cell that still fits
    is max_dim // max(cols, rows). We never upscale here (auto = keep size);
    users can request a larger cell explicitly to upscale.
    """
    native = max(1, int(native))
    fit = max_dim // max(1, max(cols, rows))
    return max(min_cell, min(native, fit))


def _resize_frames(frames: List[np.ndarray], cell: int) -> List[np.ndarray]:
    if not frames or frames[0].shape[0] == cell:
        return frames
    interp = cv2.INTER_AREA if frames[0].shape[0] > cell else cv2.INTER_CUBIC
    return [cv2.resize(f, (cell, cell), interpolation=interp) for f in frames]


def make_flipbook_images(
    paths: Optional[List[str]],
    cols: int,
    rows: int,
    cell: int,
    loop: bool = False,
    blend_frames: int = 0,
    mode: str = "linear",
    frames: Optional[List[np.ndarray]] = None,
) -> FlipbookResult:
    """
    Build a flipbook from an ordered image sequence.

    Placement rules (so a folder of N stills "just works"):
      * N == cols*rows -> one image per cell, in order (perfect fit).
      * N <  cols*rows -> images fill the leading cells; trailing cells stay
        transparent and only the N real frames play back.
      * N >  cols*rows -> the sequence is evenly sampled down to fit the grid.
    Enabling `loop` cross-blends the seam just like the video path.
    """
    if cols < 1 or rows < 1:
        raise ValueError("cols and rows must be >= 1")
    if cell < 1:
        raise ValueError("cell must be >= 1")
    if frames is None:
        frames = decode_square_images(paths or [], cell)
    if not frames:
        raise ValueError("No images available to build a flipbook.")
    frames = _resize_frames(frames, cell)

    n_cells = cols * rows
    n = len(frames)
    if loop:
        seq = _build_looping_sequence(frames, n_cells, blend_frames, mode)
        active = n_cells
    elif n > n_cells:
        seq = _sample(frames, n_cells)
        active = n_cells
    else:
        seq = frames  # n <= n_cells; extra cells stay transparent
        active = n

    sheet = compose_sheet(seq, cols, rows, cell)
    return FlipbookResult(
        sheet_bgr=sheet,
        cols=cols,
        rows=rows,
        cell=cell,
        frame_count=active,
        sheet_width=cols * cell,
        sheet_height=rows * cell,
    )
