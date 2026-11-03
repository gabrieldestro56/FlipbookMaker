"""Integration tests for the FastAPI server using an in-process test client."""
import os

import pytest
from fastapi.testclient import TestClient

import app as appmod

client = TestClient(appmod.app)


# --------------------------------------------------------------- static / UI
def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "Flipbook Maker" in r.text


def test_static_assets_served():
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


# ----------------------------------------------------------------- load-path
def test_load_path_ok(video_path):
    r = client.post("/api/load-path", json={"path": video_path})
    assert r.status_code == 200
    data = r.json()
    assert "video_id" in data
    assert data["info"]["frame_count"] > 0
    assert data["video_url"].endswith(data["video_id"])


def test_load_path_strips_quotes(video_path):
    r = client.post("/api/load-path", json={"path": f'"{video_path}"'})
    assert r.status_code == 200


def test_load_path_missing():
    r = client.post("/api/load-path", json={"path": "C:/nope/missing.mp4"})
    assert r.status_code == 404


def test_load_path_not_a_video(not_a_video):
    r = client.post("/api/load-path", json={"path": not_a_video})
    assert r.status_code == 400


# -------------------------------------------------------------------- upload
def test_upload_ok(video_path):
    with open(video_path, "rb") as f:
        r = client.post("/api/upload",
                        files={"file": ("clip" + os.path.splitext(video_path)[1],
                                        f, "video/mp4")})
    assert r.status_code == 200
    assert r.json()["info"]["width"] > 0


# ------------------------------------------------------------- video stream
def test_video_stream(video_path):
    vid = client.post("/api/load-path", json={"path": video_path}).json()["video_id"]
    r = client.get(f"/api/video/{vid}")
    assert r.status_code == 200
    assert len(r.content) > 0


def test_video_unknown_id():
    assert client.get("/api/video/deadbeef").status_code == 404


# ----------------------------------------------------------------- generate
@pytest.fixture
def loaded_video(video_path):
    return client.post("/api/load-path",
                       json={"path": video_path}).json()["video_id"]


def test_generate_ok_and_outputs_fetchable(loaded_video):
    r = client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 6, "rows": 6, "cell": 64,
        "loop": True, "blend_frames": 3, "mode": "linear",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["sheet_width"] == data["sheet_height"] == 6 * 64
    assert data["frame_count"] == 36
    assert data["columns"] == 6 and data["rows"] == 6
    assert data["format"] == "png"

    # Preview PNG, the download file, and metadata must all be retrievable.
    assert client.get(data["preview_url"]).status_code == 200
    dl = client.get(data["download_url"])
    assert dl.status_code == 200
    assert dl.content[:8] == b"\x89PNG\r\n\x1a\n"  # valid PNG signature
    meta = client.get(data["metadata_url"])
    assert meta.status_code == 200
    assert meta.json()["columns"] == 6 and meta.json()["frame_count"] == 36


def test_generate_non_square(loaded_video):
    r = client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 8, "rows": 4, "cell": 32, "loop": False,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["sheet_width"] == 8 * 32 and d["sheet_height"] == 4 * 32


def test_generate_alpha_preview_is_rgba(loaded_video):
    import io
    from PIL import Image
    r = client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "cell": 32,
        "alpha_mode": "luminance",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["alpha_mode"] == "luminance"
    img = Image.open(io.BytesIO(client.get(d["preview_url"]).content))
    assert img.mode == "RGBA"


@pytest.mark.parametrize("fmt,sig", [
    ("png", b"\x89PNG"), ("tga", None), ("webp", b"RIFF"),
    ("tiff", None), ("jpg", b"\xff\xd8"), ("exr", b"\x76\x2f\x31\x01"),
])
def test_generate_each_format(loaded_video, fmt, sig):
    r = client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "cell": 32, "fmt": fmt,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["download_name"].endswith("." + fmt)
    content = client.get(d["download_url"]).content
    assert len(content) > 0
    if sig:
        assert content[:len(sig)] == sig


def test_generate_power_of_two(loaded_video):
    r = client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 5, "rows": 5, "cell": 64, "pot": 512,
    })
    assert r.status_code == 200
    d = r.json()
    assert d["sheet_width"] == d["sheet_height"] == 512
    assert d["power_of_two"] == 512


def test_generate_bad_format(loaded_video):
    assert client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "fmt": "gif"
    }).status_code == 400


def test_generate_bad_alpha(loaded_video):
    assert client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "alpha_mode": "magic"
    }).status_code == 400


def test_generate_bad_pot(loaded_video):
    assert client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "pot": 777
    }).status_code == 400


def test_generate_optical_mode(loaded_video):
    r = client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "cell": 32,
        "loop": True, "blend_frames": 2, "mode": "optical",
    })
    assert r.status_code == 200


def test_generate_unknown_video():
    r = client.post("/api/generate", json={"video_id": "nope", "cols": 4, "rows": 4})
    assert r.status_code == 404


def test_generate_bad_grid(loaded_video):
    assert client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 0, "rows": 4}).status_code == 400
    assert client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 99, "rows": 4}).status_code == 400


def test_generate_bad_cell(loaded_video):
    assert client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "cell": 4}).status_code == 400
    assert client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "cell": 9999}).status_code == 400


def test_generate_bad_mode(loaded_video):
    assert client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "mode": "bogus"
    }).status_code == 400


def test_generate_negative_blend(loaded_video):
    assert client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "blend_frames": -1
    }).status_code == 400


def test_generate_sheet_too_large(loaded_video):
    # 32 * 1024 = 32768 > MAX_SHEET_DIM (16384)
    r = client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 32, "rows": 32, "cell": 1024})
    assert r.status_code == 400


# --------------------------------------------------------- image sequences
def test_load_folder_ok(image_dir):
    r = client.post("/api/load-folder", json={"path": image_dir})
    assert r.status_code == 200
    d = r.json()
    assert d["kind"] == "images"
    assert d["frame_count"] == 8
    assert d["suggest_cols"] == 3 and d["suggest_rows"] == 3
    # First-frame preview is fetchable.
    assert client.get(d["sample_url"]).status_code == 200


def test_load_folder_missing():
    r = client.post("/api/load-folder", json={"path": "C:/nope/not_a_dir"})
    assert r.status_code == 404


def test_load_folder_empty(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    r = client.post("/api/load-folder", json={"path": str(d)})
    assert r.status_code == 400


def test_generate_from_images_keeps_alpha(image_dir):
    import io
    from PIL import Image
    vid = client.post("/api/load-folder", json={"path": image_dir}).json()["video_id"]
    r = client.post("/api/generate", json={
        "video_id": vid, "cols": 3, "rows": 3, "cell": 32,
        "loop": False, "alpha_mode": "source",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["source_kind"] == "images"
    assert d["frame_count"] == 8          # 8 real frames in a 9-cell sheet
    assert d["columns"] == 3 and d["rows"] == 3
    img = Image.open(io.BytesIO(client.get(d["preview_url"]).content))
    assert img.mode == "RGBA"


def test_frame_out_of_range(image_dir):
    vid = client.post("/api/load-folder", json={"path": image_dir}).json()["video_id"]
    assert client.get(f"/api/frames/{vid}/999").status_code == 404


def test_browse_returns_path(monkeypatch):
    class Fake:
        stdout = "C:/some/folder\n"
    monkeypatch.setattr(appmod.subprocess, "run", lambda *a, **k: Fake())
    r = client.post("/api/browse", json={"kind": "folder"})
    assert r.status_code == 200
    assert r.json()["path"] == "C:/some/folder"


def test_browse_cancel_returns_none(monkeypatch):
    class Fake:
        stdout = ""
    monkeypatch.setattr(appmod.subprocess, "run", lambda *a, **k: Fake())
    r = client.post("/api/browse", json={"kind": "file"})
    assert r.status_code == 200
    assert r.json()["path"] is None


def test_generate_auto_cell_images(image_dir):
    # cell omitted -> auto. Fixtures are 80x60, so the native square is 60.
    vid = client.post("/api/load-folder", json={"path": image_dir}).json()["video_id"]
    r = client.post("/api/generate", json={"video_id": vid, "cols": 3, "rows": 3})
    assert r.status_code == 200
    d = r.json()
    assert d["cell"] == 60
    assert d["sheet_width"] == d["sheet_height"] == 180


def test_generate_auto_cell_video_fits_8k(loaded_video):
    r = client.post("/api/generate", json={"video_id": loaded_video, "cols": 4, "rows": 4})
    assert r.status_code == 200
    d = r.json()
    assert d["cell"] > 0
    assert max(d["sheet_width"], d["sheet_height"]) <= 8192


def test_generate_images_upscale(image_dir):
    # Explicit cell larger than the source upscales each frame.
    vid = client.post("/api/load-folder", json={"path": image_dir}).json()["video_id"]
    r = client.post("/api/generate", json={
        "video_id": vid, "cols": 3, "rows": 3, "cell": 512})
    assert r.status_code == 200
    d = r.json()
    assert d["cell"] == 512
    assert d["sheet_width"] == 1536


def test_generate_pot_8k(loaded_video):
    r = client.post("/api/generate", json={
        "video_id": loaded_video, "cols": 4, "rows": 4, "cell": 64, "pot": 8192})
    assert r.status_code == 200
    assert r.json()["sheet_width"] == 8192


# -------------------------------------------------------------- output 404s
def test_output_not_found():
    assert client.get("/output/nope_missing.png").status_code == 404
    assert client.get("/api/download/nope_missing.png").status_code == 404


# ------------------------------------------------------------- trim / crop
def test_generate_trim_images_keeps_range(image_dir):
    # 8 images, keep [2/8, 5/8) -> 3 frames in an underfilled 3x3 grid.
    vid = client.post("/api/load-folder", json={"path": image_dir}).json()["video_id"]
    r = client.post("/api/generate", json={"video_id": vid, "cols": 3, "rows": 3, "loop": False,
                                           "trim_start": 2 / 8, "trim_end": 5 / 8})
    assert r.status_code == 200
    assert r.json()["frame_count"] == 3


def test_generate_trim_video(loaded_video):
    r = client.post("/api/generate", json={"video_id": loaded_video, "cols": 2, "rows": 2,
                                           "trim_start": 0.25, "trim_end": 0.5})
    assert r.status_code == 200
    assert r.json()["frame_count"] == 4


@pytest.mark.parametrize("trim", [(0.5, 0.5), (-0.1, 1.0), (0.0, 1.5)])
def test_generate_bad_trim(loaded_video, trim):
    r = client.post("/api/generate", json={"video_id": loaded_video,
                                           "trim_start": trim[0], "trim_end": trim[1]})
    assert r.status_code == 400


def test_generate_crop_sets_auto_cell(image_dir):
    # 80x60 fixtures; crop a 30x30 px square -> auto cell 30.
    vid = client.post("/api/load-folder", json={"path": image_dir}).json()["video_id"]
    r = client.post("/api/generate", json={"video_id": vid, "cols": 3, "rows": 3,
                                           "crop": [0.1, 0.1, 30 / 80, 30 / 60]})
    assert r.status_code == 200
    assert r.json()["cell"] == 30
    # Back to no crop: cache must re-decode the full center square.
    r = client.post("/api/generate", json={"video_id": vid, "cols": 3, "rows": 3})
    assert r.json()["cell"] == 60


@pytest.mark.parametrize("crop", [[0.5, 0.5, 0.6, 0.2], [0, 0, 0, 0.5], [0, 0, 1]])
def test_generate_bad_crop(loaded_video, crop):
    r = client.post("/api/generate", json={"video_id": loaded_video, "crop": crop})
    assert r.status_code == 400
