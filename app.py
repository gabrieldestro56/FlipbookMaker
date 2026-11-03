"""
Flipbook Maker - local web server.

Run:  python app.py        (or use run.bat / run.ps1)
Then open the printed URL in your browser.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
import webbrowser
from threading import Timer
from typing import Dict, List, Optional

# Enable EXR before cv2 is imported anywhere in the process.
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
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
# Largest sprite-sheet edge we support end-to-end: 8K. Cells are sized so the
# whole sheet stays within this, which also guards against multi-gigabyte images.
MAX_SHEET_DIM = 8192
# Hard cap on a single decoded frame so "auto"/native sizing can't blow up
# memory on very large sources before it's tiled into the sheet.
MAX_CELL = 8192

app = FastAPI(title="Flipbook Maker")


@app.middleware("http")
async def _no_cache(request, call_next):
    """Never let the browser serve a stale UI. Forces a fresh fetch of the
    HTML/JS/CSS on every load so updates always show up on refresh."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# video_id -> {"path": str, "info": dict}
_videos: Dict[str, dict] = {}
# video_id -> {"frames": List[np.ndarray], "cell": int, "crop": tuple|None}
_frame_cache: Dict[str, dict] = {}


def _register(path: str) -> dict:
    if not os.path.isfile(path):
        raise HTTPException(404, f"File not found: {path}")
    try:
        info = core.probe(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    vid = uuid.uuid4().hex[:12]
    _videos[vid] = {"path": path, "kind": "video", "info": info.as_dict()}
    return {
        "video_id": vid,
        "kind": "video",
        "info": info.as_dict(),
        "video_url": f"/api/video/{vid}",
    }


def _register_folder(folder: str) -> dict:
    if not os.path.isdir(folder):
        raise HTTPException(404, f"Folder not found: {folder}")
    try:
        files = core.list_image_files(folder)
        info = core.probe_images(folder)
    except ValueError as e:
        raise HTTPException(400, str(e))
    vid = uuid.uuid4().hex[:12]
    cols, rows = core.suggest_grid(len(files))
    _videos[vid] = {
        "path": folder,
        "kind": "images",
        "files": files,
        "name": os.path.basename(os.path.normpath(folder)),
        "info": info.as_dict(),
    }
    return {
        "video_id": vid,
        "kind": "images",
        "info": info.as_dict(),
        "frame_count": len(files),
        "suggest_cols": cols,
        "suggest_rows": rows,
        "frames_url": f"/api/frames/{vid}",
        "sample_url": f"/api/frames/{vid}/0",
    }


class LoadPath(BaseModel):
    path: str


class BrowseReq(BaseModel):
    kind: str = "folder"        # "folder" | "file"


# Runs in a short-lived subprocess so the native Tk dialog never fights with
# the server's event loop / worker threads.
_BROWSE_SCRIPT = r"""
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
if sys.argv[1] == "file":
    path = filedialog.askopenfilename(
        title="Select a video",
        filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"),
                   ("All files", "*.*")])
else:
    path = filedialog.askdirectory(title="Select an image-sequence folder")
root.destroy()
sys.stdout.write(path or "")
"""


@app.post("/api/browse")
def browse(body: BrowseReq):
    """Open a native OS picker on the machine running the server and return the
    chosen path. Only useful when the app is used locally (its intended mode)."""
    kind = "file" if body.kind == "file" else "folder"
    try:
        out = subprocess.run(
            [sys.executable, "-c", _BROWSE_SCRIPT, kind],
            capture_output=True, text=True, timeout=300,
        )
    except Exception as e:  # pragma: no cover - environment dependent
        raise HTTPException(500, f"Could not open the picker: {e}")
    return {"path": out.stdout.strip() or None}


@app.post("/api/load-path")
def load_path(body: LoadPath):
    return _register(body.path.strip().strip('"'))


@app.post("/api/load-folder")
def load_folder(body: LoadPath):
    return _register_folder(body.path.strip().strip('"'))


@app.get("/api/frames/{video_id}/{index}")
def get_frame(video_id: str, index: int):
    """Serve one original image from a loaded sequence (preview thumbnails)."""
    v = _videos.get(video_id)
    if not v or v.get("kind") != "images":
        raise HTTPException(404, "Unknown image sequence")
    files = v["files"]
    if index < 0 or index >= len(files):
        raise HTTPException(404, "Frame index out of range")
    return FileResponse(files[index])


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


def _get_frames(video_id: str, min_cell: int, crop=None) -> List[np.ndarray]:
    """Return decoded frames at a resolution >= min_cell, decoding (and caching)
    at a higher resolution if needed so output cells are never upscaled.
    A different crop rect forces a re-decode."""
    v = _videos.get(video_id)
    if not v:
        raise HTTPException(404, "Unknown video id")
    cached = _frame_cache.get(video_id)
    if cached is None or cached["cell"] < min_cell or cached.get("crop") != crop:
        decode_cell = max(DECODE_CELL, min_cell)
        if v.get("kind") == "images":
            frames = core.decode_square_images(v["files"], decode_cell, crop)
        else:
            frames = core.decode_square_frames(v["path"], decode_cell, crop)
        _frame_cache[video_id] = {"frames": frames, "cell": decode_cell, "crop": crop}
    return _frame_cache[video_id]["frames"]


def _chroma(img: np.ndarray, color: str, tolerance: int) -> np.ndarray:
    """Key out `color`, turning bad user input into a 400."""
    if not 0 <= tolerance <= 100:
        raise HTTPException(400, "chroma_tolerance must be between 0 and 100")
    try:
        return core.chroma_key(img, core.parse_hex_color(color), tolerance)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _check_edit(crop: Optional[List[float]], trim_start: float, trim_end: float):
    """Validate trim + crop (0..1 fractions); return the crop as a tuple or None."""
    if not 0.0 <= trim_start < trim_end <= 1.0:
        raise HTTPException(400, "trim must satisfy 0 <= trim_start < trim_end <= 1")
    if crop is None:
        return None
    x, y, w, h = crop if len(crop) == 4 else (-1, -1, 0, 0)
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.0001 or y + h > 1.0001:
        raise HTTPException(400, "crop must be [x, y, w, h] fractions inside the frame")
    return (x, y, w, h)


def _trim(frames: List[np.ndarray], trim_start: float, trim_end: float) -> List[np.ndarray]:
    n = len(frames)
    a = min(n - 1, int(round(trim_start * n)))
    return frames[a:max(a + 1, int(round(trim_end * n)))]


@app.get("/api/chroma-preview/{video_id}")
def chroma_preview(video_id: str, color: str = "#00ff00", tolerance: int = 60,
                   trim_start: float = 0.0, trim_end: float = 1.0, crop: str = ""):
    """One keyed frame (middle of the trimmed, cropped clip) as PNG, so the key
    can be tuned live. Same crop as /api/generate, so both share the frame cache."""
    try:
        crop_list = [float(v) for v in crop.split(",")] if crop else None
    except ValueError:
        raise HTTPException(400, "crop must be x,y,w,h")
    c = _check_edit(crop_list, trim_start, trim_end)
    frames = _trim(_get_frames(video_id, 1, c), trim_start, trim_end)
    keyed = _chroma(frames[len(frames) // 2], color, tolerance)
    data, _ = export.encode(keyed, "png")
    return Response(data, media_type="image/png")


class GenerateReq(BaseModel):
    video_id: str
    cols: int = 10
    rows: int = 10
    cell: int = 0                 # 0 = auto: match source, fit within 8K
    loop: bool = True
    blend_frames: int = 4
    mode: str = "linear"          # "linear" | "optical"
    fmt: str = "png"              # png | tga | webp | tiff | jpg | exr
    alpha_mode: str = "none"      # "none" | "luminance"
    premultiply: bool = False
    chroma_key: bool = False      # remove a green screen (overrides alpha_mode)
    chroma_color: str = "#00ff00" # screen color to key out
    chroma_tolerance: int = 60    # 0-100, higher removes more
    pot: int = 0                  # 0 = off, else 256/512/1024/2048/4096
    fps: float = 24.0             # stored in metadata only
    trim_start: float = 0.0       # keep [trim_start, trim_end) of the clip, as 0..1 fractions
    trim_end: float = 1.0
    crop: Optional[List[float]] = None  # [x, y, w, h] as 0..1 fractions; None = center square


_MEDIA_TYPES = {
    ".png": "image/png", ".tga": "image/x-tga", ".webp": "image/webp",
    ".tiff": "image/tiff", ".jpg": "image/jpeg", ".exr": "image/x-exr",
    ".json": "application/json",
}


@app.post("/api/generate")
def generate(req: GenerateReq):
    if req.cols < 1 or req.rows < 1 or req.cols > 32 or req.rows > 32:
        raise HTTPException(400, "cols/rows must be between 1 and 32")
    if req.cell != 0 and (req.cell < 16 or req.cell > MAX_CELL):
        raise HTTPException(400, f"cell must be 0 (auto) or between 16 and {MAX_CELL}")
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
    crop = _check_edit(req.crop, req.trim_start, req.trim_end)

    src = _videos.get(req.video_id)
    if not src:
        raise HTTPException(404, "Unknown video id")

    # Resolve "auto" (cell == 0): keep the source's native resolution, sized so
    # the whole sheet fits within 8K. Any explicit value is used as-is.
    info = src["info"]
    cw, ch = (crop[2], crop[3]) if crop else (1.0, 1.0)
    native = int(min(info["width"] * cw, info["height"] * ch)) or MAX_CELL
    cell = core.auto_cell(native, req.cols, req.rows, MAX_SHEET_DIM) if req.cell == 0 else req.cell

    if max(req.cols, req.rows) * cell > MAX_SHEET_DIM:
        raise HTTPException(
            400, f"Resulting sheet exceeds {MAX_SHEET_DIM}px per edge (8K); "
                 "reduce grid size or cell resolution.")

    frames = _trim(_get_frames(req.video_id, cell, crop), req.trim_start, req.trim_end)
    t0 = time.time()
    if src.get("kind") == "images":
        result = core.make_flipbook_images(
            paths=src["files"],
            cols=req.cols, rows=req.rows, cell=cell,
            loop=req.loop, blend_frames=req.blend_frames, mode=req.mode,
            frames=frames,
        )
    else:
        result = core.make_flipbook(
            path=src["path"],
            cols=req.cols, rows=req.rows, cell=cell,
            loop=req.loop, blend_frames=req.blend_frames, mode=req.mode,
            frames=frames,
        )

    sheet = result.sheet_bgr
    alpha_mode = req.alpha_mode.lower()
    if req.chroma_key:
        sheet = _chroma(sheet, req.chroma_color, req.chroma_tolerance)
        alpha_mode = "source"

    stem = f"flipbook_{req.cols}x{req.rows}_{uuid.uuid4().hex[:6]}"

    # Always emit a straight-alpha PNG the browser can display (preview/playback).
    preview_bytes, _ = export.encode(sheet, "png",
                                     alpha_mode=alpha_mode,
                                     premultiply=False, pot=req.pot)
    preview_name = stem + "_preview.png"
    with open(os.path.join(OUTPUT_DIR, preview_name), "wb") as f:
        f.write(preview_bytes)

    # The chosen export format (PNG by default), honoring premultiply.
    dl_bytes, ext = export.encode(sheet, req.fmt,
                                  alpha_mode=alpha_mode,
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
        "alpha_mode": alpha_mode,
        "chroma_key": req.chroma_key,
        **({"chroma_color": req.chroma_color, "chroma_tolerance": req.chroma_tolerance}
           if req.chroma_key else {}),
        "premultiplied_alpha": bool(req.premultiply and alpha_mode != "none"),
        "power_of_two": req.pot,
        "source_kind": src.get("kind", "video"),
        "source": src.get("name") or os.path.basename(src["path"]),
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


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
def index():
    """Serve the UI with cache-busting version stamps on its assets, so a
    browser can never show a stale copy of the tool after an update."""
    path = os.path.join(STATIC_DIR, "index.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    # Bust caches whenever any UI file changes.
    version = str(int(max(
        os.path.getmtime(os.path.join(STATIC_DIR, n))
        for n in ("index.html", "app.js", "style.css")
    )))
    html = (html.replace("/style.css", f"/style.css?v={version}")
                .replace("/app.js", f"/app.js?v={version}"))
    return HTMLResponse(html)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def _open_browser(url: str):
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("FLIPBOOK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLIPBOOK_PORT", "8090"))
    Timer(1.2, _open_browser, args=[f"http://{host}:{port}/"]).start()
    print(f"\n  Flipbook Maker running at  http://{host}:{port}/\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
