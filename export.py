"""
Flipbook export / encoding.

Turns a composed BGR sprite-sheet into a game-ready file in a variety of
formats, with VFX-friendly options:

  * Alpha from luminance  -> turns fire / smoke / explosions on black into a
    clean cutout with a real alpha channel.
  * Premultiplied alpha   -> what most engines expect for additive/soft VFX.
  * Power-of-two fit       -> resize the whole sheet to 1024/2048/4096 so it
    drops straight into a GPU without wasting a mip level.

PNG is the recommended default; TGA/WEBP/TIFF/EXR/JPG are also supported.
"""

from __future__ import annotations

import io
import os
from typing import Optional, Tuple

# Enable EXR before cv2 is imported (no-op if already set/imported).
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import cv2
import numpy as np
from PIL import Image

# fmt -> (file extension, Pillow format or None for EXR, supports alpha)
FORMATS = {
    "png":  (".png",  "PNG",  True),
    "tga":  (".tga",  "TGA",  True),
    "webp": (".webp", "WEBP", True),
    "tiff": (".tiff", "TIFF", True),
    "jpg":  (".jpg",  "JPEG", False),
    "exr":  (".exr",  None,   True),
}

ALPHA_MODES = ("none", "luminance", "source")
POT_SIZES = (0, 256, 512, 1024, 2048, 4096, 8192)


def extension_for(fmt: str) -> str:
    return FORMATS[fmt.lower()][0]


def build_alpha(bgr: np.ndarray, mode: str,
                source_alpha: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
    """Return a uint8 alpha plane, or None for a fully opaque image.

    'luminance' uses Rec.709 luma so dark pixels become transparent — ideal for
    additive VFX shot on a black background. 'source' keeps the alpha that came
    with the input (e.g. PNGs from an image-sequence flipbook).
    """
    mode = (mode or "none").lower()
    if mode == "none":
        return None
    if mode == "source":
        return source_alpha
    if mode == "luminance":
        b = bgr[..., 0].astype(np.float32)
        g = bgr[..., 1].astype(np.float32)
        r = bgr[..., 2].astype(np.float32)
        lum = 0.0722 * b + 0.7152 * g + 0.2126 * r
        return np.clip(lum, 0, 255).astype(np.uint8)
    raise ValueError(f"Unknown alpha mode: {mode}")


def _premultiply(bgr: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    a = alpha.astype(np.float32) / 255.0
    out = bgr.astype(np.float32) * a[..., None]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def fit_pot(img: np.ndarray, target: int) -> np.ndarray:
    """Resize the whole sheet to a `target`x`target` square (UV layout is
    preserved because engines divide by rows/cols, not pixels)."""
    if not target:
        return img
    interp = cv2.INTER_AREA if target < img.shape[0] else cv2.INTER_CUBIC
    return cv2.resize(img, (target, target), interpolation=interp)


def encode(
    sheet_bgr: np.ndarray,
    fmt: str = "png",
    alpha_mode: str = "none",
    premultiply: bool = False,
    pot: int = 0,
    jpg_quality: int = 95,
) -> Tuple[bytes, str]:
    """
    Encode a BGR (or BGRA) sheet to bytes in the requested format.

    Returns (file_bytes, file_extension).
    """
    fmt = (fmt or "png").lower()
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported format: {fmt}")
    if pot not in POT_SIZES:
        raise ValueError(f"Unsupported power-of-two size: {pot}")

    ext, pil_fmt, supports_alpha = FORMATS[fmt]

    # Split any incoming alpha (BGRA sheets from an image sequence) off first.
    if sheet_bgr.shape[-1] == 4:
        bgr = sheet_bgr[..., :3].copy()
        source_alpha = sheet_bgr[..., 3]
    else:
        bgr = sheet_bgr
        source_alpha = None

    # A BGRA sheet defaults to keeping its own alpha unless told otherwise.
    if source_alpha is not None and alpha_mode.lower() == "none":
        alpha_mode = "source"

    alpha = build_alpha(bgr, alpha_mode, source_alpha) if supports_alpha else None

    if alpha is not None and premultiply:
        bgr = _premultiply(bgr, alpha)

    if pot:
        bgr = fit_pot(bgr, pot)
        if alpha is not None:
            alpha = fit_pot(alpha, pot)

    if fmt == "exr":
        arr = bgr.astype(np.float32) / 255.0
        if alpha is not None:
            a = (alpha.astype(np.float32) / 255.0)[..., None]
            arr = np.concatenate([arr, a], axis=2)  # BGRA, cv2 convention
        ok, buf = cv2.imencode(".exr", arr)
        if not ok:
            raise ValueError("EXR encoding failed in this OpenCV build")
        return buf.tobytes(), ext

    # Pillow path (BGR -> RGB / RGBA)
    if alpha is not None:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    else:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb, "RGB")

    bio = io.BytesIO()
    kwargs = {}
    if fmt == "jpg":
        image = image.convert("RGB")
        kwargs["quality"] = int(jpg_quality)
    elif fmt == "webp":
        kwargs["quality"] = 95
        kwargs["lossless"] = False
    image.save(bio, format=pil_fmt, **kwargs)
    return bio.getvalue(), ext
