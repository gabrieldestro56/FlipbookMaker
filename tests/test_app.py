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


# -------------------------------------------------------------- output 404s
def test_output_not_found():
    assert client.get("/output/nope_missing.png").status_code == 404
    assert client.get("/api/download/nope_missing.png").status_code == 404
