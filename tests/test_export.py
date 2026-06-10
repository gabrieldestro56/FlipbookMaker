"""Unit tests for export.py (format encoding + VFX alpha/POT options)."""
import io

import cv2
import numpy as np
import pytest
from PIL import Image

import export


def _sheet(val=128, cell=32, cols=2, rows=2):
    return np.full((rows * cell, cols * cell, 3), val, np.uint8)


def _pil_open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


# --------------------------------------------------------------- alpha helpers
def test_build_alpha_none():
    assert export.build_alpha(_sheet(), "none") is None


def test_build_alpha_luminance_extremes():
    white = np.full((4, 4, 3), 255, np.uint8)
    black = np.zeros((4, 4, 3), np.uint8)
    assert export.build_alpha(white, "luminance").mean() == pytest.approx(255, abs=1)
    assert export.build_alpha(black, "luminance").mean() == pytest.approx(0, abs=1)


def test_build_alpha_unknown_raises():
    with pytest.raises(ValueError):
        export.build_alpha(_sheet(), "bogus")


def test_premultiply_darkens_low_alpha():
    bgr = np.full((4, 4, 3), 200, np.uint8)
    alpha = np.full((4, 4), 64, np.uint8)
    out = export._premultiply(bgr, alpha)
    assert out.mean() < bgr.mean()  # color scaled down by alpha


# ----------------------------------------------------------------- fit_pot
def test_fit_pot_off_is_noop():
    s = _sheet()
    assert export.fit_pot(s, 0) is s


def test_fit_pot_resizes_square():
    out = export.fit_pot(_sheet(cell=30), 512)
    assert out.shape == (512, 512, 3)


# ----------------------------------------------------------------- encode png
def test_encode_png_rgb_when_no_alpha():
    data, ext = export.encode(_sheet(), "png", alpha_mode="none")
    assert ext == ".png"
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert _pil_open(data).mode == "RGB"


def test_encode_png_rgba_with_alpha():
    data, _ = export.encode(_sheet(), "png", alpha_mode="luminance")
    im = _pil_open(data)
    assert im.mode == "RGBA"


def test_encode_preserves_color_channels():
    # Pure red sheet (BGR) must come out red (RGB) after the channel swap.
    bgr = np.zeros((16, 16, 3), np.uint8)
    bgr[..., 2] = 255  # red in BGR
    data, _ = export.encode(bgr, "png")
    arr = np.asarray(_pil_open(data).convert("RGB"))
    assert arr[..., 0].mean() > 250 and arr[..., 1].mean() < 5


# -------------------------------------------------------- all Pillow formats
@pytest.mark.parametrize("fmt", ["png", "tga", "webp", "tiff", "jpg"])
def test_encode_all_pillow_formats(fmt):
    data, ext = export.encode(_sheet(180), fmt, alpha_mode="luminance")
    assert ext == export.FORMATS[fmt][0]
    assert len(data) > 0
    im = _pil_open(data)
    assert im.size == (64, 64)
    # JPG cannot carry alpha; everything else should when requested.
    if fmt == "jpg":
        assert im.mode in ("RGB", "L")


# ------------------------------------------------------------------- EXR
def test_encode_exr_float_with_alpha():
    data, ext = export.encode(_sheet(255), "exr", alpha_mode="luminance")
    assert ext == ".exr"
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    assert arr is not None
    assert arr.dtype == np.float32
    assert arr.shape[2] == 4  # BGRA


# --------------------------------------------------------- power-of-two encode
def test_encode_with_pot():
    data, _ = export.encode(_sheet(cell=30, cols=3, rows=3), "png", pot=512)
    assert _pil_open(data).size == (512, 512)


# ------------------------------------------------------------------ errors
def test_encode_bad_format():
    with pytest.raises(ValueError):
        export.encode(_sheet(), "gif")


def test_encode_bad_pot():
    with pytest.raises(ValueError):
        export.encode(_sheet(), "png", pot=777)


def test_extension_for():
    assert export.extension_for("PNG") == ".png"
    assert export.extension_for("exr") == ".exr"
