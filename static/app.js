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
  state.kind = "video";
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

  updateSheetHint();
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
    state.kind = "images";
    state.info = data.info;

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
  const native = Math.min(state.info.width, state.info.height);
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
$("chroma").addEventListener("change", syncAlphaControls);
$("chromaThreshold").addEventListener("input", (e) => {
  $("chromaVal").textContent = e.target.value;
});

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
      chroma_threshold: +$("chromaThreshold").value,
      pot: +$("pot").value,
      fps: state.fps,
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
