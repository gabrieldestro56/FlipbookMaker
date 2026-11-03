# Flipbook Maker

Turn any video **or folder of images** into a **game-ready square sprite sheet**
("flipbook") with a simple browser UI. Built for VFX / tech-art teams who need
looping flipbooks for particles, decals, and animated textures.

**[▶ Live demo &amp; landing page](https://mradfo21.github.io/FlipbookMaker/)** — see the tool in action (a real flipbook loops right in your browser).

## Features

- **Two source types** — a **video** clip *or* an **image sequence** (a folder
  of numbered stills like `Frame_0.png … Frame_7.png`). Switch with the
  segmented toggle in the Source panel.
- **Image sequences preserve source alpha** — transparent PNGs (muzzle flashes,
  decals, smoke cutouts) keep their real alpha channel all the way to the sheet;
  the grid is auto-suggested from the frame count and images load in numeric
  order starting at 0.
- **Custom grids** — `4×4`, `8×8`, `10×10`, `16×16`, or any size up to `32×32`.
- **Perfect squares** — every frame is center-cropped to a square cell, so the
  sheet is power-of-two friendly and engine ready.
- **Cell resolution** control (64 / 128 / 256 / 512 px) with a live readout of
  the final sheet dimensions.
- **Seamless looping** via a motion-aware cross-blend at the loop seam.
  - *Linear cross-fade* — fast, blended in **linear light** so it never darkens.
  - *Optical-flow morph* — warps frames along motion for sharper loops.
- **Live preview** — see the source video, the generated sheet, and the
  flipbook **playing back** side by side, with an FPS scrubber. Transparency is
  shown over a checkerboard so you can judge the cutout.
- **Game-ready export controls:**
  - Formats: **PNG** (recommended), **TGA**, **WebP**, **TIFF**, **EXR** (HDR),
    **JPG**.
  - **Alpha from luminance** — turns fire / smoke / explosions shot on black
    into a clean cutout with a real alpha channel.
  - **Premultiplied alpha** — what most engines expect for additive/soft VFX.
  - **Power-of-two fit** — resize the whole sheet to 512 / 1024 / 2048 / 4096 so
    it drops straight onto the GPU (the row/column UV layout is preserved).
  - **Metadata sidecar** (`.json`) with columns, rows, frame count, fps, loop
    settings, alpha, and source — ready for engine import tooling.
- **Runs locally**, no cloud, no accounts. Easy to share with the team.

## Quick start

### Windows (one click)

Double-click **`run.bat`** (or right-click **`run.ps1`** → *Run with
PowerShell*). It finds your Python, creates a virtual environment on first
launch, installs dependencies (and re-installs automatically whenever
`requirements.txt` changes), then opens the app in your browser at
<http://127.0.0.1:8090/>. If anything goes wrong the window stays open with the
error. To run the tests the same way, use **`test.bat`** / **`test.ps1`**.

> The app serves with no-cache headers and version-stamped assets, so refreshing
> always shows the latest UI. Set `FLIPBOOK_PORT` to use a different port.

### Manual

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:8090/>.

## How to make a perfect looping 10×10

1. Load your video (file picker or paste a full path).
2. Click the **10×10** preset.
3. Pick a cell resolution (256 px → a 2560×2560 sheet).
4. Keep **Seamless loop** on; set **Blend frames** to ~4 (raise it if the seam
   still pops, lower it to preserve more unique motion).
5. Click **Generate flipbook**, watch the right-hand preview loop, then
   **Download PNG**.

## Making a flipbook from an image folder

1. In **1 · Source**, click the **Image sequence** toggle.
2. Paste the folder path (e.g. `C:\Users\me\Downloads\Muzzle_Side`) and click
   **Load**. Images are picked up in numeric order (`…_0`, `…_1`, `…_2`, …) and
   the grid is auto-set to the smallest square that fits (8 frames → 3×3).
3. Adjust the grid if you like — e.g. bump it to **4×4** for an engine-friendly
   square. Frames fill the leading cells in order; any leftover cells stay
   fully transparent, and only the real frames play back in the preview.
4. Leave **Alpha from luminance** *off* to keep the source PNG transparency
   (recommended for muzzle flashes / decals), or turn it on to rebuild alpha
   from brightness instead.
5. **Generate flipbook** → **Download PNG**. A `.json` sidecar records the grid,
   frame count, and `source_kind: images`.

If a folder has *more* images than cells, the sequence is evenly sampled down to
fit; if it has fewer, the extra cells are left transparent.

## Game-ready export

Use the **4 · Export** panel:

| Control | What it does |
| --- | --- |
| **Format** | `PNG` is recommended (lossless + alpha). `TGA`/`TIFF` are common engine formats, `WebP` is small, `EXR` is 32-bit HDR, `JPG` is lossy with no alpha. |
| **Alpha from luminance** | Builds an alpha channel from brightness (Rec.709). Perfect for additive VFX (fire, smoke, sparks, explosions) captured on black. |
| **Remove green screen** | Keys out green (alpha from how much green exceeds red/blue) and removes green spill from edges. Lower the *Key threshold* to remove more green. Overrides the alpha option. |
| **Premultiply alpha** | Multiplies color by alpha — the form most engines/Niagara expect. Enabled once alpha is on. |
| **Power-of-two** | Resizes the final sheet to a POT square. Cell UVs stay valid because engines divide the texture by rows × columns. |

Each export also writes a `<name>.json` sidecar, e.g.:

```json
{
  "columns": 10, "rows": 10, "frame_count": 100, "fps": 24.0,
  "loop": true, "blend_frames": 8, "blend_mode": "linear",
  "sheet_width": 2048, "sheet_height": 2048,
  "alpha_mode": "luminance", "premultiplied_alpha": true,
  "power_of_two": 2048, "source": "explosion.mp4"
}
```

The on-page preview is always a straight-alpha PNG so the checkerboard shows the
true cutout; the downloaded file is in your chosen format with your alpha/POT
options applied.

## How the seamless loop works

The clip is sampled into `N = cols × rows` frames. For a loop we sample
`N + B` frames (`B` = blend frames). The trailing `B` frames are the *natural
motion continuation* just past the seam; the first `B` output frames cross-fade
from that continuation back into the start of the clip:

```
result[i] = blend(head = P[i], tail = P[N+i], w = (i+1)/(B+1))   for i < B
result[i] = P[i]                                                  otherwise
```

So at the seam `result[N-1]` is followed by `result[0] ≈ P[N]` (the frame that
naturally comes next) — motion never pops — and over the next `B` frames the
dissolve returns to the clip's start, hiding any end/start mismatch.

## Testing

A full headless test suite (47 tests) covers the engine and every API endpoint:

```bash
pip install -r requirements-dev.txt
pytest
```

The tests synthesize their own throwaway video, so they need no external assets
and run in ~1–2 seconds.

## Landing page / GitHub Pages

A self-contained marketing + demo site lives in [`docs/`](docs/). It includes a
**live in-browser flipbook player** (a real 8×8 loop generated by this tool), a
feature tour, and install instructions — no backend required.

Preview it locally:

```bash
python -m http.server 8080 --directory docs
# open http://127.0.0.1:8080/
```

Publish it on GitHub Pages:

1. Push this repo to GitHub.
2. **Settings → Pages → Build and deployment → Deploy from a branch.**
3. Choose your default branch and the **`/docs`** folder, then **Save**.
4. Your site goes live at `https://<user>.github.io/<repo>/`.

## Project layout

```
app.py            FastAPI server + REST endpoints
core.py           Flipbook engine (sampling, crop, blend, compose)
export.py         Format encoding + alpha / premultiply / power-of-two
static/           Web UI (index.html, app.js, style.css)
docs/             GitHub Pages landing page + live demo
tests/            Headless unit + integration tests
requirements.txt  Pinned dependencies (+ requirements-dev.txt for tests)
run.bat / run.ps1 One-click launchers
```

## Tech

Python · FastAPI · OpenCV · NumPy. Video decoding uses OpenCV's bundled
FFmpeg, so **no separate FFmpeg install is required**.
