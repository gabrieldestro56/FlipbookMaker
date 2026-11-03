"""Unit tests for core.py — every public and private helper is exercised."""
import os

import numpy as np
import pytest

import core
from conftest import ramp_frames, color_frames, seam_delta


# --------------------------------------------------------------- color helpers
def test_srgb_linear_roundtrip():
    x = np.linspace(0, 1, 50, dtype=np.float32)
    back = core._linear_to_srgb(core._srgb_to_linear(x))
    assert np.allclose(x, back, atol=1e-4)


def test_srgb_endpoints():
    assert core._srgb_to_linear(np.array([0.0]))[0] == pytest.approx(0.0, abs=1e-6)
    assert core._srgb_to_linear(np.array([1.0]))[0] == pytest.approx(1.0, abs=1e-6)
    assert core._linear_to_srgb(np.array([0.0]))[0] == pytest.approx(0.0, abs=1e-6)
    assert core._linear_to_srgb(np.array([1.0]))[0] == pytest.approx(1.0, abs=1e-6)


# ----------------------------------------------------------------------- blend
def test_blend_weight_extremes():
    a = np.full((4, 4, 3), 200, np.uint8)
    b = np.full((4, 4, 3), 50, np.uint8)
    assert np.array_equal(core.blend(a, b, 1.0), a)
    assert np.array_equal(core.blend(a, b, 0.0), b)


def test_blend_identical_is_identity():
    a = np.full((4, 4, 3), 123, np.uint8)
    assert np.array_equal(core.blend(a, a, 0.5), a)


def test_blend_linear_light_does_not_darken():
    # Mid cross-fade of black & white must be brighter than naive sRGB (~127);
    # linear-light blending yields ~188.
    white = np.full((2, 2, 3), 255, np.uint8)
    black = np.zeros((2, 2, 3), np.uint8)
    mid = core.blend(white, black, 0.5)
    assert mid.mean() > 180


def test_blend_shape_and_dtype():
    a = np.zeros((8, 8, 3), np.uint8)
    out = core.blend(a, a, 0.3)
    assert out.shape == (8, 8, 3) and out.dtype == np.uint8


# ----------------------------------------------------------- center square crop
def test_center_square_crop_landscape():
    frame = np.zeros((64, 96, 3), np.uint8)
    cropped = core._center_square_crop(frame)
    assert cropped.shape[0] == cropped.shape[1] == 64


def test_center_square_crop_portrait_is_centered():
    frame = np.zeros((96, 40, 3), np.uint8)
    frame[:, 18:22] = 255  # vertical stripe near the horizontal center
    cropped = core._center_square_crop(frame)
    assert cropped.shape[0] == cropped.shape[1] == 40
    assert cropped[:, 18:22].mean() == 255  # stripe survives the centered crop


# ---------------------------------------------------------------------- sample
def test_sample_count_endpoints_monotonic():
    frames = color_frames(20)
    s = core._sample(frames, 5)
    assert len(s) == 5
    assert np.array_equal(s[0], frames[0])
    assert np.array_equal(s[-1], frames[-1])


def test_sample_single_frame_duplicates():
    frames = color_frames(1)
    s = core._sample(frames, 7)
    assert len(s) == 7 and all(np.array_equal(f, frames[0]) for f in s)


def test_sample_zero():
    assert core._sample(color_frames(3), 0) == []


# ------------------------------------------------------------- compose_sheet
def test_compose_sheet_shape_and_placement():
    cell = 16
    seq = color_frames(6, cell)  # colors 0..5
    sheet = core.compose_sheet(seq, cols=3, rows=2, cell=cell)
    assert sheet.shape == (2 * cell, 3 * cell, 3)
    for i in range(6):
        r, c = divmod(i, 3)
        block = sheet[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell]
        assert int(block.mean()) == i  # frame i landed in cell i


def test_compose_sheet_ignores_extra_frames():
    cell = 8
    seq = color_frames(10, cell)
    sheet = core.compose_sheet(seq, cols=2, rows=2, cell=cell)  # only 4 cells
    assert sheet.shape == (2 * cell, 2 * cell, 3)


# ------------------------------------------------------------- build_sequence
def test_build_sequence_noloop_matches_sample():
    frames = color_frames(40)
    direct = core._sample(frames, 9)
    via = core.build_sequence(frames, 3, 3, loop=False, blend_frames=4, mode="linear")
    assert all(np.array_equal(a, b) for a, b in zip(direct, via))


def test_loop_sequence_length_and_dtype():
    frames = ramp_frames(60)
    seq = core.build_sequence(frames, 5, 5, loop=True, blend_frames=4, mode="linear")
    assert len(seq) == 25
    assert all(f.dtype == np.uint8 for f in seq)


def test_loop_reduces_seam_discontinuity():
    frames = ramp_frames(120)
    hard = core.build_sequence(frames, 10, 10, loop=False, blend_frames=0, mode="linear")
    soft = core.build_sequence(frames, 10, 10, loop=True, blend_frames=8, mode="linear")
    assert seam_delta(soft) < seam_delta(hard) * 0.6  # markedly smoother


def test_loop_blend_clamped_for_tiny_grid():
    frames = ramp_frames(10)
    seq = core.build_sequence(frames, 1, 1, loop=True, blend_frames=50, mode="linear")
    assert len(seq) == 1  # n=1 -> B clamped to 0, no crash


def test_optical_mode_runs():
    frames = ramp_frames(40, cell=32)
    seq = core.build_sequence(frames, 4, 4, loop=True, blend_frames=3, mode="optical")
    assert len(seq) == 16 and all(f.shape == (32, 32, 3) for f in seq)


# -------------------------------------------------------------- _warp_toward
def test_warp_toward_shape_and_zero_t():
    a = np.random.randint(0, 255, (32, 32, 3), np.uint8)
    b = np.random.randint(0, 255, (32, 32, 3), np.uint8)
    warped = core._warp_toward(a, b, 0.0)
    assert warped.shape == a.shape
    assert np.array_equal(warped, a)  # t=0 -> identity remap


# -------------------------------------------------------------- make_flipbook
def test_make_flipbook_shape_with_frames():
    frames = color_frames(50, cell=64)
    res = core.make_flipbook("ignored", cols=5, rows=5, cell=32, frames=frames)
    assert res.sheet_bgr.shape == (5 * 32, 5 * 32, 3)
    assert res.sheet_width == res.sheet_height == 160
    assert res.frame_count == 25 and res.cell == 32


def test_make_flipbook_no_resize_when_matching():
    frames = color_frames(16, cell=24)
    res = core.make_flipbook("ignored", cols=4, rows=4, cell=24, frames=frames,
                             loop=False)
    assert res.sheet_bgr.shape == (96, 96, 3)


def test_make_flipbook_empty_frames_raises():
    with pytest.raises(ValueError):
        core.make_flipbook("ignored", 2, 2, 16, frames=[])


def test_make_flipbook_invalid_grid_raises():
    frames = color_frames(4, 16)
    with pytest.raises(ValueError):
        core.make_flipbook("ignored", 0, 2, 16, frames=frames)


# ------------------------------------------------------------- probe / decode
def test_probe_real_video(video_path):
    info = core.probe(video_path)
    assert info.width > 0 and info.height > 0
    assert info.fps > 0
    assert info.frame_count > 0
    assert info.duration > 0
    assert info.as_dict()["width"] == info.width


def test_probe_missing_raises():
    with pytest.raises(ValueError):
        core.probe("does_not_exist_12345.mp4")


def test_decode_square_frames_real_video(video_path):
    frames = core.decode_square_frames(video_path, cell=48)
    assert len(frames) > 0
    assert all(f.shape == (48, 48, 3) for f in frames)
    assert all(f.dtype == np.uint8 for f in frames)


def test_decode_missing_raises():
    with pytest.raises(ValueError):
        core.decode_square_frames("nope_missing.mp4", 32)


def test_end_to_end_from_real_video(video_path):
    res = core.make_flipbook(video_path, cols=4, rows=4, cell=32,
                             loop=True, blend_frames=2)
    assert res.sheet_bgr.shape == (128, 128, 3)


# ------------------------------------------------------- image sequences
def test_natural_key_orders_numeric():
    names = ["Muzzle_10.png", "Muzzle_2.png", "Muzzle_0.png", "Muzzle_1.png"]
    ordered = sorted(names, key=core._natural_key)
    assert ordered == ["Muzzle_0.png", "Muzzle_1.png", "Muzzle_2.png", "Muzzle_10.png"]


def test_list_image_files_sorted(image_dir):
    files = core.list_image_files(image_dir)
    assert len(files) == 8
    assert os.path.basename(files[0]) == "Frame_0.png"
    assert os.path.basename(files[-1]) == "Frame_7.png"


def test_list_image_files_empty_raises(tmp_path):
    with pytest.raises(ValueError):
        core.list_image_files(str(tmp_path))


def test_probe_images(image_dir):
    info = core.probe_images(image_dir)
    assert info.frame_count == 8
    assert info.width > 0 and info.height > 0


def test_suggest_grid():
    assert core.suggest_grid(8) == (3, 3)
    assert core.suggest_grid(16) == (4, 4)
    assert core.suggest_grid(1) == (1, 1)


def test_auto_cell_keeps_native_within_cap():
    # Native fits comfortably -> keep the source resolution.
    assert core.auto_cell(2048, 3, 3, max_dim=8192) == 2048
    # Fits exactly at the 8K edge (4 * 2048 == 8192).
    assert core.auto_cell(2048, 4, 4, max_dim=8192) == 2048
    # Too big for the grid -> shrink so the sheet stays within 8K.
    assert core.auto_cell(2048, 5, 5, max_dim=8192) == 8192 // 5
    # Small source is preserved, never upscaled by auto.
    assert core.auto_cell(60, 3, 3, max_dim=8192) == 60
    # Never drops below the minimum cell.
    assert core.auto_cell(10000, 32, 32, max_dim=8192) == max(16, 8192 // 32)


def test_decode_square_images_keeps_alpha(image_dir):
    files = core.list_image_files(image_dir)
    frames = core.decode_square_images(files, cell=48)
    assert len(frames) == 8
    assert all(f.shape == (48, 48, 4) for f in frames)  # alpha preserved


def test_decode_square_images_no_alpha(image_dir_noalpha):
    files = core.list_image_files(image_dir_noalpha)
    frames = core.decode_square_images(files, cell=32)
    assert all(f.shape == (32, 32, 3) for f in frames)


def test_make_flipbook_images_underfilled_leaves_transparent(image_dir):
    files = core.list_image_files(image_dir)
    res = core.make_flipbook_images(files, cols=3, rows=3, cell=16)  # 9 cells, 8 imgs
    assert res.sheet_bgr.shape == (48, 48, 4)
    assert res.frame_count == 8            # only real frames play back
    # last cell (index 8) was never filled -> fully transparent
    assert res.sheet_bgr[32:48, 32:48, 3].max() == 0


def test_make_flipbook_images_exact_fit(image_dir):
    files = core.list_image_files(image_dir)
    res = core.make_flipbook_images(files, cols=4, rows=2, cell=16)  # 8 cells, 8 imgs
    assert res.frame_count == 8
    assert res.sheet_bgr[:, :, 3].min() >= 0


def test_make_flipbook_images_oversubscribed_samples(image_dir):
    files = core.list_image_files(image_dir)
    res = core.make_flipbook_images(files, cols=2, rows=2, cell=16)  # 4 cells, 8 imgs
    assert res.frame_count == 4


def test_make_flipbook_images_from_frames_and_loop():
    frames = [np.full((16, 16, 4), i, np.uint8) for i in range(4)]
    res = core.make_flipbook_images(None, cols=2, rows=2, cell=16,
                                    loop=True, blend_frames=1, frames=frames)
    assert res.sheet_bgr.shape == (32, 32, 4)
    assert res.frame_count == 4


# ----------------------------------------------------------------- chroma key
def test_chroma_key_removes_green_keeps_subject():
    img = np.zeros((2, 3, 3), np.uint8)
    img[:, 0] = (60, 180, 70)    # BGR: typical lit green screen
    img[:, 1] = (120, 150, 200)  # BGR: skin tone
    img[:, 2] = (50, 130, 60)    # edge: green excess 70, between threshold/2 and threshold
    out = core.chroma_key(img, threshold=80)
    assert out.shape == (2, 3, 4)
    assert (out[:, 0, 3] == 0).all()
    assert (out[:, 1, 3] == 255).all()
    assert 0 < out[0, 2, 3] < 255
    assert np.array_equal(out[:, 1, :3], img[:, 1])  # subject color untouched
    assert (out[:, 2, 1] <= out[:, 2, 2]).all()      # spill clamped to max(R, B)


def test_chroma_key_keeps_existing_alpha():
    img = np.zeros((1, 1, 4), np.uint8)
    img[0, 0] = (120, 150, 200, 40)  # opaque-colored but already mostly transparent
    assert core.chroma_key(img)[0, 0, 3] == 40
