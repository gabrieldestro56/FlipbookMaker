const $ = (id) => document.getElementById(id);

const MAX_SHEET_DIM = 8192;  // 8K — matches the server-side cap

const state = {
  videoId: null,
  kind: "video",     // "video" | "images"
  info: null,
  sheetImg: null,
  meta: null,        // { columns, rows, cell, frame_count }
  frame: 0,
  playing: true,
  lastTick: 0,
  fps: 24,
  srcKind: "",       // kind of the loaded source (state.kind follows the tab)
  crop: null,        // { x, y, s } square in source pixels; null = center square
  frameCount: 0,     // images only
  framesUrl: "",     // images only
};

function setStatus(msg, kind = "") {
  const el = $("status");
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
}

// ---------------------------------------------------------------- source mode
function setSourceMode(mode) {
  state.kind = mode;
  document.querySelectorAll("#sourceMode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === mode));
  $("videoSource").classList.toggle("hidden", mode !== "video");
  $("imageSource").classList.toggle("hidden", mode !== "images");

  if (mode === "images") {
    $("sourceHeading").textContent = "Source frames";
    $("alphaLabel").textContent = "Override alpha (luminance)";
    $("alphaHint").textContent = "Transparent PNGs keep their own alpha automatically.";
    $("loop").checked = false;                 // preserve exact frames by default
    $("blend").value = 0; $("blendVal").textContent = "0";
  } else {
    $("sourceHeading").textContent = "Source video";
    $("alphaLabel").textContent = "Alpha from luminance";
    $("alphaHint").textContent = "Cuts out fire / smoke / explosions shot on black.";
  }
}

document.querySelectorAll("#sourceMode button").forEach((btn) => {
  btn.addEventListener("click", () => setSourceMode(btn.dataset.mode));
});

// --------------------------------------------------------------- load (video)
async function registerResponse(res) {
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  const data = await res.json();
  state.videoId = data.video_id;
  state.kind = state.srcKind = "video";
  state.info = data.info;

  $("sourceImg").classList.add("hidden");
  const v = $("video");
  v.src = data.video_url + "?t=" + Date.now();
  v.classList.remove("hidden");
  $("videoPlaceholder").classList.add("hidden");
  v.play().catch(() => {});

  const i = data.info;
  $("videoMeta").textContent =
    `${i.width}×${i.height}  ·  ${i.fps.toFixed(2)} fps  ·  ` +
    `${i.frame_count} frames  ·  ${i.duration.toFixed(2)}s`;

  if (i.fps > 0) {
    state.fps = Math.min(60, Math.max(1, Math.round(i.fps)));
    $("fps").value = state.fps;
    $("fpsVal").textContent = state.fps;
  }

  resetEdit();
  refreshChromaPreview();
  $("generateBtn").disabled = false;
  setStatus("Video loaded — set your grid and generate", "ok");
}

// Native OS picker (server-side dialog) -> fills the path box and loads.
async function nativeBrowse(kind) {
  const res = await fetch("/api/browse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind }),
  });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return (await res.json()).path;
}

$("browseFileBtn").addEventListener("click", async () => {
  setStatus("Opening file picker…", "busy");
  try {
    const p = await nativeBrowse("file");
    if (!p) { setStatus("Pick cancelled"); return; }
    $("pathInput").value = p;
    $("loadPathBtn").click();
  } catch (err) {
    setStatus("Couldn't open picker: " + err.message, "err");
  }
});

$("browseFolderBtn").addEventListener("click", async () => {
  setStatus("Opening folder picker…", "busy");
  try {
    const p = await nativeBrowse("folder");
    if (!p) { setStatus("Pick cancelled"); return; }
    $("folderInput").value = p;
    $("loadFolderBtn").click();
  } catch (err) {
    setStatus("Couldn't open picker: " + err.message, "err");
  }
});

$("fileInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  setStatus("Uploading & decoding…", "busy");
  try {
    const fd = new FormData();
    fd.append("file", file);
    await registerResponse(await fetch("/api/upload", { method: "POST", body: fd }));
  } catch (err) {
    setStatus("Load failed: " + err.message, "err");
  }
});

$("loadPathBtn").addEventListener("click", async () => {
  const path = $("pathInput").value.trim();
  if (!path) return;
  setStatus("Loading…", "busy");
  try {
    await registerResponse(await fetch("/api/load-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }));
  } catch (err) {
    setStatus("Load failed: " + err.message, "err");
  }
});

// -------------------------------------------------------------- load (images)
function applyGrid(cols, rows) {
  $("cols").value = cols;
  $("rows").value = rows;
  $("square").checked = cols === rows;
  document.querySelectorAll("#presets button").forEach((b) =>
    b.classList.toggle("active", +b.dataset.grid === cols && cols === rows));
  updateSheetHint();
}

$("loadFolderBtn").addEventListener("click", async () => {
  const path = $("folderInput").value.trim();
  if (!path) return;
  setStatus("Scanning folder…", "busy");
  try {
    const res = await fetch("/api/load-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) throw new Error((await res.text()) || res.statusText);
    const data = await res.json();

    state.videoId = data.video_id;
    state.kind = state.srcKind = "images";
    state.info = data.info;
    state.frameCount = data.frame_count;
    state.framesUrl = data.frames_url;

    // Preview the first frame in the source stage.
    $("video").classList.add("hidden");
    $("video").pause?.();
    const img = $("sourceImg");
    img.src = data.sample_url + "?t=" + Date.now();
    img.classList.remove("hidden");
    $("videoPlaceholder").classList.add("hidden");

    const i = data.info;
    $("videoMeta").textContent =
      `${data.frame_count} images  ·  ${i.width}×${i.height} px  ·  ` +
      `suggested grid ${data.suggest_cols}×${data.suggest_rows}`;

    applyGrid(data.suggest_cols, data.suggest_rows);
    resetEdit();
    refreshChromaPreview();

    $("generateBtn").disabled = false;
    setStatus(
      `${data.frame_count} images loaded — grid set to ` +
      `${data.suggest_cols}×${data.suggest_rows}`, "ok");
  } catch (err) {
    setStatus("Load failed: " + err.message, "err");
  }
});

// ------------------------------------------------------------------ controls
function syncSquare(fromCols) {
  if ($("square").checked) {
    if (fromCols) $("rows").value = $("cols").value;
    else $("cols").value = $("rows").value;
  }
  updateSheetHint();
}

// Resolve the effective per-frame cell size, mirroring the server. When the
// user picks "Auto", we keep the source's native square size but shrink it so
// the whole sheet still fits within 8K.
function currentCell() {
  const cols = +$("cols").value, rows = +$("rows").value;
  const picked = +$("cell").value;
  if (picked !== 0) return picked;
  if (!state.info) return 0;
  const native = state.crop ? Math.floor(state.crop.s)
                            : Math.min(state.info.width, state.info.height);
  const fit = Math.floor(MAX_SHEET_DIM / Math.max(cols, rows));
  return Math.max(16, Math.min(native, fit));
}

function updateSheetHint() {
  const cols = +$("cols").value, rows = +$("rows").value;
  const cell = currentCell();
  const el = $("sheetSize");
  if (!cell) {
    el.textContent = "Load a source to size the sheet";
    el.classList.remove("warn");
    return;
  }
  const auto = +$("cell").value === 0;
  const w = cols * cell, h = rows * cell;
  const cap = Math.max(w, h) > MAX_SHEET_DIM;
  el.textContent =
    `Output: ${w} × ${h} px · ${cols * rows} cells · ${cell} px/frame` +
    (auto ? " (auto)" : "") + (cap ? "  ⚠ exceeds 8K" : "");
  el.classList.toggle("warn", cap);
}

$("cols").addEventListener("input", () => syncSquare(true));
$("rows").addEventListener("input", () => syncSquare(false));
$("cell").addEventListener("change", updateSheetHint);
$("square").addEventListener("change", () => syncSquare(true));

document.querySelectorAll("#presets button").forEach((btn) => {
  btn.addEventListener("click", () => {
    const g = +btn.dataset.grid;
    applyGrid(g, g);
  });
});

$("blend").addEventListener("input", (e) => { $("blendVal").textContent = e.target.value; });
$("fps").addEventListener("input", (e) => {
  state.fps = +e.target.value; $("fpsVal").textContent = e.target.value;
});

// Premultiply only makes sense with an alpha channel.
function syncAlphaControls() {
  const on = $("alpha").checked || $("chroma").checked;
  $("premultiply").disabled = !on;
  if (!on) $("premultiply").checked = false;
  $("chromaRow").classList.toggle("hidden", !$("chroma").checked);
}
$("alpha").addEventListener("change", syncAlphaControls);
$("chroma").addEventListener("change", () => { syncAlphaControls(); refreshChromaPreview(); });
$("chromaTolerance").addEventListener("input", (e) => {
  $("chromaVal").textContent = e.target.value;
  refreshChromaPreview();
});
$("chromaColor").addEventListener("input", refreshChromaPreview);

// Browser eyedropper (Chrome/Edge): sample the screen color straight off the source video.
if ("EyeDropper" in window) {
  $("eyedropBtn").classList.remove("hidden");
  $("eyedropBtn").addEventListener("click", async () => {
    try {
      $("chromaColor").value = (await new EyeDropper().open()).sRGBHex;
      refreshChromaPreview();
    } catch { /* user pressed Esc */ }
  });
}

// One keyed frame from the server, debounced while dragging the slider.
let chromaTimer = 0, chromaSeq = 0;
function refreshChromaPreview() {
  clearTimeout(chromaTimer);
  if (!$("chroma").checked || !state.videoId) return;
  chromaTimer = setTimeout(async () => {
    const seq = ++chromaSeq;
    const ph = $("chromaPlaceholder");
    const [trimStart, trimEnd] = trimFrac();
    const q = new URLSearchParams({
      color: $("chromaColor").value, tolerance: $("chromaTolerance").value,
      trim_start: trimStart, trim_end: trimEnd });
    const crop = cropFrac();
    if (crop) q.set("crop", crop.join(","));
    try {
      const res = await fetch(`/api/chroma-preview/${state.videoId}?${q}`);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      const url = URL.createObjectURL(await res.blob());
      if (seq !== chromaSeq) return URL.revokeObjectURL(url);  // a newer request won
      const img = $("chromaPreview");
      if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
      img.src = url;
      img.classList.remove("hidden");
      ph.classList.add("hidden");
    } catch (err) {
      if (seq !== chromaSeq) return;
      $("chromaPreview").classList.add("hidden");
      ph.textContent = "Preview failed: " + err.message;
      ph.classList.remove("hidden");
    }
  }, 150);
}

// ------------------------------------------------------------- trim & crop
// Trim sliders run 0..max: max = 1000 for video, the image count for sequences
// (so each step is one frame). Kept range is [start, end) of the clip.
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const trimFrac = () => [+$("trimStart").value / +$("trimStart").max,
                        +$("trimEnd").value / +$("trimEnd").max];
const clipDuration = () => {
  const d = $("video").duration;
  return isFinite(d) && d > 0 ? d : state.info.duration;
};

function resetEdit() {
  const max = state.srcKind === "images" ? state.frameCount : 1000;
  $("trimStart").max = $("trimEnd").max = max;
  $("trimStart").value = 0;
  $("trimEnd").value = max;
  $("cropOn").checked = false;
  state.crop = null;
  placeCropBox();
  updateTrimLabels();
  updateSheetHint();
  $("editBar").classList.remove("hidden");
}

function updateTrimLabels() {
  const a = +$("trimStart").value, b = +$("trimEnd").value;
  // Thumb centers travel from 9px to (width - 9px) for the 18px balls in style.css.
  const [fs, fe] = trimFrac();
  $("trimFill").style.left = `calc(${fs} * (100% - 18px) + 9px)`;
  $("trimFill").style.right = `calc(${1 - fe} * (100% - 18px) + 9px)`;
  if (state.srcKind === "images") {
    $("trimStartVal").textContent = `frame ${a}`;
    $("trimEndVal").textContent = `frame ${b - 1}`;
    $("trimLen").textContent = `${b - a} of ${state.frameCount} frames`;
  } else {
    const [s, e] = trimFrac(), d = clipDuration();
    $("trimStartVal").textContent = (s * d).toFixed(2) + "s";
    $("trimEndVal").textContent = (e * d).toFixed(2) + "s";
    $("trimLen").textContent = `${((e - s) * d).toFixed(2)}s kept`;
  }
}

// Dragging a handle shows that frame; the two handles can't cross.
function onTrimInput(isStart) {
  const a = $("trimStart"), b = $("trimEnd");
  if (isStart) a.value = Math.min(+a.value, +b.value - 1);
  else b.value = Math.max(+b.value, +a.value + 1);
  updateTrimLabels();
  if (state.srcKind === "images") {
    const i = isStart ? +a.value : +b.value - 1;
    $("sourceImg").src = `${state.framesUrl}/${i}`;
  } else {
    const v = $("video");
    v.pause();
    v.currentTime = Math.max(0, trimFrac()[isStart ? 0 : 1] * clipDuration() - (isStart ? 0 : 0.05));
  }
}
$("trimStart").addEventListener("input", () => onTrimInput(true));
$("trimEnd").addEventListener("input", () => onTrimInput(false));
for (const id of ["trimStart", "trimEnd"]) {
  $(id).addEventListener("change", () => {
    if (state.srcKind === "video") $("video").play().catch(() => {});
    refreshChromaPreview();
  });
}

// Keep video playback looping inside the trimmed range.
$("video").addEventListener("timeupdate", () => {
  const v = $("video");
  if (v.paused || !state.info || state.srcKind !== "video") return;
  const [s, e] = trimFrac(), d = clipDuration();
  if (v.currentTime >= e * d || v.currentTime < s * d - 0.1) v.currentTime = s * d;
});
$("video").addEventListener("loadedmetadata", () => { updateTrimLabels(); placeCropBox(); });

// Where the media is drawn inside the stage (object-fit: contain letterboxes it).
function mediaRect() {
  const el = state.srcKind === "images" ? $("sourceImg") : $("video");
  const W = state.info.width, H = state.info.height;
  const scale = Math.min(el.clientWidth / W, el.clientHeight / H);
  return {
    left: el.offsetLeft + (el.clientWidth - W * scale) / 2,
    top: el.offsetTop + (el.clientHeight - H * scale) / 2,
    scale,
  };
}

function placeCropBox() {
  const box = $("cropBox");
  box.classList.toggle("hidden", !state.crop);
  if (!state.crop) return;
  const r = mediaRect(), c = state.crop;
  box.style.left = r.left + c.x * r.scale + "px";
  box.style.top = r.top + c.y * r.scale + "px";
  box.style.width = box.style.height = c.s * r.scale + "px";
}

// Crop as [x, y, w, h] fractions of the source (what the server expects), or null.
function cropFrac() {
  const c = state.crop, W = state.info?.width, H = state.info?.height;
  return c && W ? [c.x / W, c.y / H, c.s / W, c.s / H] : null;
}

function defaultCrop() {
  const W = state.info.width, H = state.info.height;
  const s = Math.round(Math.min(W, H) * 0.8);
  return { x: (W - s) / 2, y: (H - s) / 2, s };
}

$("cropOn").addEventListener("change", (e) => {
  state.crop = e.target.checked && state.info ? defaultCrop() : null;
  placeCropBox();
  updateSheetHint();
  refreshChromaPreview();
});
$("cropReset").addEventListener("click", () => {
  if (!state.crop) return;
  state.crop = defaultCrop();
  placeCropBox();
  updateSheetHint();
  refreshChromaPreview();
});

let cropDrag = null;
$("cropBox").addEventListener("pointerdown", (e) => {
  e.preventDefault();
  cropDrag = { resize: e.target.id === "cropHandle", px: e.clientX, py: e.clientY,
               start: { ...state.crop } };
  $("cropBox").setPointerCapture(e.pointerId);
});
$("cropBox").addEventListener("pointermove", (e) => {
  if (!cropDrag) return;
  const k = mediaRect().scale;
  const dx = (e.clientX - cropDrag.px) / k, dy = (e.clientY - cropDrag.py) / k;
  const W = state.info.width, H = state.info.height, s0 = cropDrag.start, c = state.crop;
  if (cropDrag.resize) {
    c.s = clamp(s0.s + Math.max(dx, dy), 16, Math.min(W - s0.x, H - s0.y));
  } else {
    c.x = clamp(s0.x + dx, 0, W - c.s);
    c.y = clamp(s0.y + dy, 0, H - c.s);
  }
  placeCropBox();
  updateSheetHint();
});
for (const ev of ["pointerup", "pointercancel"]) {
  $("cropBox").addEventListener(ev, () => {
    if (cropDrag) refreshChromaPreview();  // on release: a new crop re-decodes the clip
    cropDrag = null;
  });
}
window.addEventListener("resize", placeCropBox);

// ------------------------------------------------------------------ generate
function resolveAlphaMode() {
  if ($("alpha").checked) return "luminance";
  return state.kind === "images" ? "source" : "none";
}

$("generateBtn").addEventListener("click", async () => {
  if (!state.videoId) return;
  setStatus("Generating flipbook…", "busy");
  $("generateBtn").disabled = true;
  try {
    const body = {
      video_id: state.videoId,
      cols: +$("cols").value,
      rows: +$("rows").value,
      cell: +$("cell").value,
      loop: $("loop").checked,
      blend_frames: +$("blend").value,
      mode: $("mode").value,
      fmt: $("fmt").value,
      alpha_mode: resolveAlphaMode(),
      premultiply: $("premultiply").checked,
      chroma_key: $("chroma").checked,
      chroma_color: $("chromaColor").value,
      chroma_tolerance: +$("chromaTolerance").value,
      pot: +$("pot").value,
      fps: state.fps,
      trim_start: trimFrac()[0],
      trim_end: trimFrac()[1],
      crop: cropFrac(),
    };
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    state.meta = data;
    const img = new Image();
    img.onload = () => {
      state.sheetImg = img;
      state.frame = 0;
      $("sheet").src = data.preview_url;
      $("sheet").classList.remove("hidden");
      $("sheetPlaceholder").classList.add("hidden");
      $("previewPlaceholder").classList.add("hidden");
      $("playBtn").disabled = false;
      state.playing = true;
      $("playBtn").textContent = "⏸ Pause";

      const dl = $("downloadBtn");
      dl.href = data.download_url;
      dl.textContent = "Download " + data.format.toUpperCase();
      dl.classList.remove("hidden");

      const meta = $("metaBtn");
      meta.href = data.metadata_url;
      meta.classList.remove("hidden");

      setStatus(
        `Done — ${data.columns}×${data.rows}, ${data.sheet_width}×` +
        `${data.sheet_height}px ${data.format.toUpperCase()} ` +
        `(${data.frame_count} frames, ${data.elapsed_ms} ms)`, "ok");
    };
    img.src = data.preview_url + "?t=" + Date.now();
  } catch (err) {
    setStatus("Generate failed: " + err.message, "err");
  } finally {
    $("generateBtn").disabled = false;
  }
});

// ------------------------------------------------------------------ playback
$("playBtn").addEventListener("click", () => {
  state.playing = !state.playing;
  $("playBtn").textContent = state.playing ? "⏸ Pause" : "▶ Play";
});

function tick(now) {
  requestAnimationFrame(tick);
  if (!state.sheetImg || !state.meta) return;

  const interval = 1000 / state.fps;
  if (state.playing && now - state.lastTick >= interval) {
    state.lastTick = now;
    state.frame = (state.frame + 1) % state.meta.frame_count;
    draw();
  }
}

function draw() {
  const cols = state.meta.columns;
  const rows = state.meta.rows;
  // Derive cell size from the actual image so power-of-two sheets still map.
  const cellW = state.sheetImg.naturalWidth / cols;
  const cellH = state.sheetImg.naturalHeight / rows;
  const f = state.frame % state.meta.frame_count;
  const sx = (f % cols) * cellW;
  const sy = Math.floor(f / cols) * cellH;

  const canvas = $("preview");
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = true;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.sheetImg, sx, sy, cellW, cellH,
                0, 0, canvas.width, canvas.height);
}

requestAnimationFrame(tick);
updateSheetHint();
