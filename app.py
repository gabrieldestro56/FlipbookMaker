"""
Flipbook Maker - local web server.

Run:  python app.py        (or use run.bat / run.ps1)
Then open the printed URL in your browser.
"""

from __future__ import annotations

import json
import os
import time
import uuid
import webbrowser
from threading import Timer
from typing import Dict, List

# Enable EXR before cv2 is imported anywhere in the process.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import core
import export

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Default decode resolution. Frames are decoded once and downscaled per request
# so that changing the grid/loop settings is instant after the first decode.
DECODE_CELL = 512
# Largest sprite-sheet edge we allow (GPU max texture on most hardware), guards
# against accidentally requesting a multi-gigabyte image.
MAX_SHEET_DIM = 16384

app = FastAPI(title="Flipbook Maker")

# video_id -> {"path": str, "info": dict}
_videos: Dict[str, dict] = {}
# video_id -> {"frames": List[np.ndarray], "cell": int}  (decoded resolution)
_frame_cache: Dict[str, dict] = {}


def _register(path: str) -> dict:
    if not os.path.isfile(path):
        raise HTTPException(404, f"File not found: {path}")
    try:
        info = core.probe(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    vid = uuid.uuid4().hex[:12]
    _videos[vid] = {"path": path, "info": info.as_dict()}
    return {"video_id": vid, "info": info.as_dict(), "video_url": f"/api/video/{vid}"}


class LoadPath(BaseModel):
    path: str


@app.post("/api/load-path")
def load_path(body: LoadPath):
    return _register(body.path.strip().strip('"'))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1] or ".mp4"
    dest = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex[:12]}{ext}")
    with open(dest, "wb") as f:
        f.write(await file.read())
    return _register(dest)


@app.get("/api/video/{video_id}")
def get_video(video_id: str):
    v = _videos.get(video_id)
    if not v:
        raise HTTPException(404, "Unknown video id")
    return FileResponse(v["path"])


def _get_frames(video_id: str, min_cell: int) -> List[np.ndarray]:
    """Return decoded frames at a resolution >= min_cell, decoding (and caching)
    at a higher resolution if needed so output cells are never upscaled."""
    v = _videos.get(video_id)
    if not v:
        raise HTTPException(404, "Unknown video id")
    cached = _frame_cache.get(video_id)
    if cached is None or cached["cell"] < min_cell:
        decode_cell = max(DECODE_CELL, min_cell)
        frames = core.decode_square_frames(v["path"], decode_cell)
        _frame_cache[video_id] = {"frames": frames, "cell": decode_cell}
    return _frame_cache[video_id]["frames"]


class GenerateReq(BaseModel):
    video_id: str
    cols: int = 10
    rows: int = 10
    cell: int = 256
    loop: bool = True
    blend_frames: int = 4
    mode: str = "linear"          # "linear" | "optical"
    fmt: str = "png"              # png | tga | webp | tiff | jpg | exr
    alpha_mode: str = "none"      # "none" | "luminance"
    premultiply: bool = False
    pot: int = 0                  # 0 = off, else 256/512/1024/2048/4096
    fps: float = 24.0             # stored in metadata only


_MEDIA_TYPES = {
    ".png": "image/png", ".tga": "image/x-tga", ".webp": "image/webp",
    ".tiff": "image/tiff", ".jpg": "image/jpeg", ".exr": "image/x-exr",
    ".json": "application/json",
}


@app.post("/api/generate")
def generate(req: GenerateReq):
    if req.cols < 1 or req.rows < 1 or req.cols > 32 or req.rows > 32:
        raise HTTPException(400, "cols/rows must be between 1 and 32")
    if req.cell < 16 or req.cell > 1024:
        raise HTTPException(400, "cell must be between 16 and 1024")
    if req.blend_frames < 0:
        raise HTTPException(400, "blend_frames must be >= 0")
    if req.mode not in ("linear", "optical"):
        raise HTTPException(400, "mode must be 'linear' or 'optical'")
    if req.fmt.lower() not in export.FORMATS:
        raise HTTPException(400, f"Unsupported format: {req.fmt}")
    if req.alpha_mode.lower() not in export.ALPHA_MODES:
        raise HTTPException(400, f"Unsupported alpha mode: {req.alpha_mode}")
    if req.pot not in export.POT_SIZES:
        raise HTTPException(400, f"Unsupported power-of-two size: {req.pot}")
    if max(req.cols, req.rows) * req.cell > MAX_SHEET_DIM:
        raise HTTPException(
            400, f"Resulting sheet exceeds {MAX_SHEET_DIM}px per edge; "
                 "reduce grid size or cell resolution.")

    frames = _get_frames(req.video_id, req.cell)
    t0 = time.time()
    result = core.make_flipbook(
        path=_videos[req.video_id]["path"],
        cols=req.cols, rows=req.rows, cell=req.cell,
        loop=req.loop, blend_frames=req.blend_frames, mode=req.mode,
        frames=frames,
    )

    stem = f"flipbook_{req.cols}x{req.rows}_{uuid.uuid4().hex[:6]}"

    # Always emit a straight-alpha PNG the browser can display (preview/playback).
    preview_bytes, _ = export.encode(result.sheet_bgr, "png",
                                     alpha_mode=req.alpha_mode,
                                     premultiply=False, pot=req.pot)
    preview_name = stem + "_preview.png"
    with open(os.path.join(OUTPUT_DIR, preview_name), "wb") as f:
        f.write(preview_bytes)

    # The chosen export format (PNG by default), honoring premultiply.
    dl_bytes, ext = export.encode(result.sheet_bgr, req.fmt,
                                  alpha_mode=req.alpha_mode,
                                  premultiply=req.premultiply, pot=req.pot)
    download_name = stem + ext
    with open(os.path.join(OUTPUT_DIR, download_name), "wb") as f:
        f.write(dl_bytes)

    if req.pot:
        out_w = out_h = req.pot
    else:
        out_w, out_h = result.sheet_width, result.sheet_height
    metadata = {
        "name": stem,
        "format": req.fmt.lower(),
        "columns": result.cols,
        "rows": result.rows,
        "frame_count": result.frame_count,
        "fps": req.fps,
        "loop": req.loop,
        "blend_frames": req.blend_frames if req.loop else 0,
        "blend_mode": req.mode,
        "cell": result.cell,
        "sheet_width": out_w,
        "sheet_height": out_h,
        "alpha_mode": req.alpha_mode.lower(),
        "premultiplied_alpha": bool(req.premultiply and req.alpha_mode != "none"),
        "power_of_two": req.pot,
        "source": os.path.basename(_videos[req.video_id]["path"]),
    }
    meta_name = stem + ".json"
    with open(os.path.join(OUTPUT_DIR, meta_name), "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    elapsed = time.time() - t0
    return JSONResponse({
        "preview_url": f"/output/{preview_name}",
        "download_url": f"/api/download/{download_name}",
        "download_name": download_name,
        "metadata_url": f"/api/download/{meta_name}",
        "elapsed_ms": round(elapsed * 1000),
        **metadata,
    })


@app.get("/output/{name}")
def get_output(name: str):
    path = os.path.join(OUTPUT_DIR, os.path.basename(name))
    if not os.path.isfile(path):
        raise HTTPException(404, "Not found")
    ext = os.path.splitext(path)[1].lower()
    return FileResponse(path, media_type=_MEDIA_TYPES.get(ext, "application/octet-stream"))


@app.get("/api/download/{name}")
def download(name: str):
    path = os.path.join(OUTPUT_DIR, os.path.basename(name))
    if not os.path.isfile(path):
        raise HTTPException(404, "Not found")
    ext = os.path.splitext(path)[1].lower()
    return FileResponse(path,
                        media_type=_MEDIA_TYPES.get(ext, "application/octet-stream"),
                        filename=os.path.basename(name))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def _open_browser(url: str):
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn

    host, port = "127.0.0.1", 8000
    Timer(1.2, _open_browser, args=[f"http://{host}:{port}/"]).start()
    print(f"\n  Flipbook Maker running at  http://{host}:{port}/\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
