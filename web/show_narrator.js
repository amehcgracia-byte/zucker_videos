// The narrator's track: the talking video as the edit shows it, and how each moment of it is
// framed (wide, a zoom on the face, a slow push, a camera move from the previous framing).
// A player plays the edit from the original video with the framing applied live (nothing is
// rendered); under it a timeline with time marks, the scenes, pictures of the edit, the zooms,
// the cuts and the voice. A zoom is moved by dragging it, extended or shortened by its edges,
// changed on the right and removed with Delete. Timing never changes: effects stay put.
(() => {
  let dialog = null, doc = null, selected = null, pps = 60, playing = false, frame = 0, rangeIndex = 0;
  let aiming = false, filmVersion = 0, peaks = null, poll = null, backedUp = false, editedAt = 0;
  const changedScenes = new Set();
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clock = t => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
  const $ = selector => dialog.querySelector(selector);
  const FILM_H = 54, CHUNK = 1440;
  const ZOOM_NAMES = [[1.001, "Wide"], [1.35, "Medium"], [1.8, "Close"], [99, "Closer"]];
  const zoomName = z => ZOOM_NAMES.find(([top]) => z < top)[1];
  const REASONS = { silence: "a pause", filler: "a hesitation", retake: "a repeated take", start: "the start" };

  // ---- The camera, as core/show_camera.py computes it ------------------------------------
  function shotAt(n) {
    const shots = doc.shots; let lo = 0, hi = shots.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (shots[mid].first <= n) lo = mid; else hi = mid - 1; }
    return shots[lo];
  }
  function cameraAt(n) {
    const s = shotAt(n); if (!s) return [1, 0.5, 0.42];
    const i = Math.max(0, Math.min(s.frames - 1, n - s.first));
    let z = s.zoom; if (s.zoom_end != null) z += (s.zoom_end - s.zoom) / Math.max(1, s.frames - 1) * i;
    let [cx, cy] = s.focus;
    if (s.glide) {
      const t = Math.min(1, Math.max(0, (i + s.glide.skip) / s.glide.frames)), e = t * t * (3 - 2 * t), [z0, x0, y0] = s.glide.from;
      z = z0 + (z - z0) * e; cx = x0 + (cx - x0) * e; cy = y0 + (cy - y0) * e;
    }
    return [z, cx, cy];
  }
  function windowOf([z, cx, cy]) {
    const w = 1 / Math.max(1, z);
    return [Math.min(Math.max(cx - w / 2, 0), 1 - w), Math.min(Math.max(cy - w / 2, 0), 1 - w), w];
  }

  // ---- The edit's stretches of the original video -----------------------------------------
  let ranges = [];
  function buildRanges() {
    ranges = [];
    for (const s of doc.shots) {
      const last = ranges.at(-1);
      if (last && Math.abs(last.src + last.len - s.source[0]) < 0.5 / doc.fps) last.len += s.duration;
      else ranges.push({ at: s.at, src: s.source[0], len: s.duration });
    }
  }
  const rangeAt = t => Math.max(0, ranges.findIndex((r, i) => t < r.at + r.len || i === ranges.length - 1));

  // ---- Opening and loading ----------------------------------------------------------------
  function ensure() {
    if (dialog) return;
    dialog = document.createElement("dialog");
    dialog.id = "showNarratorDialog";
    dialog.setAttribute("aria-label", "Narrator track");
    dialog.innerHTML = `<header><div><strong>Narrator track</strong><small>Your talking video as the edit shows it: zooms, where they aim, camera moves and cuts. Timing and effects stay where they are.</small></div>
      <div class="narr-tools"><button type="button" data-narr-undo title="Undo (⌘Z)">Undo</button><button type="button" data-narr-redo title="Redo (⇧⌘Z)">Redo</button>
      <button type="button" data-narr-aim-all title="Looks at every zoom again and aims it at the face in it; a zoom with no face goes wide. Zooms you aimed by hand stay.">Aim every zoom at the face</button>
      <button type="button" data-narr-moves="glide" title="Where the picture runs on, every change of framing becomes a smooth camera move">All as camera moves</button>
      <button type="button" data-narr-moves="cut" title="Every change of framing is a quick cut">All as cuts</button></div>
      <button type="button" class="narr-close" aria-label="Close narrator track" data-narr-close>×</button></header>
      <div class="narr-top"><div class="narr-player"><div class="narr-frame" data-narr-frame><video data-narr-video playsinline preload="auto"></video><div class="narr-aim-box" data-narr-aim-box hidden></div><div class="narr-aim-dot" data-narr-aim-dot hidden></div></div>
        <div class="narr-transport"><button type="button" data-narr-play>Play</button><output data-narr-clock>0:00.0</output><small data-narr-status role="status"></small></div></div>
        <aside class="narr-inspector" data-narr-inspector></aside></div>
      <div class="narr-zoombar"><span>Timeline</span><button type="button" data-narr-pps="-1" aria-label="Show more time">−</button><button type="button" data-narr-pps="1" aria-label="Show less time">+</button><small>Drag a zoom to move it, its edges to extend or shorten it. Double-click a wide stretch to zoom there. Space plays, ←/→ step a frame, Delete removes the zoom.</small></div>
      <div class="narr-timeline"><div class="narr-labels"><span class="narr-l-ruler">Time</span><span class="narr-l-scenes">Scenes</span><span class="narr-l-film">Pictures</span><span class="narr-l-camera">Zooms</span><span class="narr-l-cuts">Cuts</span><span class="narr-l-voice">Voice</span></div>
        <div class="narr-scroll" data-narr-scroll><div class="narr-lanes" data-narr-lanes></div></div></div>
      <details class="narr-list" open><summary data-narr-list-title>Edits on your video</summary><div data-narr-list></div></details>`;
    document.body.append(dialog);
    $("[data-narr-close]").onclick = () => dialog.close();
    dialog.addEventListener("close", closed);
    $("[data-narr-play]").onclick = () => toggle();
    $("[data-narr-undo]").onclick = () => change({ op: "undo" });
    $("[data-narr-redo]").onclick = () => change({ op: "redo" });
    $("[data-narr-aim-all]").onclick = () => aimAll();
    dialog.querySelectorAll("[data-narr-moves]").forEach(button => button.onclick = () => change({ op: "moves", kind: button.dataset.narrMoves },
      button.dataset.narrMoves === "glide" ? "Every change of framing on running footage is now a camera move." : "Every change of framing is now a quick cut."));
    dialog.querySelectorAll("[data-narr-pps]").forEach(button => button.onclick = () => {
      const steps = [15, 30, 60, 120, 240], i = Math.max(0, Math.min(steps.length - 1, steps.indexOf(pps) + Number(button.dataset.narrPps)));
      const keep = editedAt; pps = steps[i]; renderTimeline(); scrollTo(keep, true);
    });
    const video = $("[data-narr-video]");
    video.addEventListener("loadedmetadata", () => seek(editedAt));
    $("[data-narr-scroll]").addEventListener("scroll", () => loadVisible());
    $("[data-narr-frame]").addEventListener("click", aimClick);
    dialog.addEventListener("keydown", keys);
    $("[data-narr-scroll]").addEventListener("pointerdown", pointerDown);
    $("[data-narr-scroll]").addEventListener("dblclick", doubleClick);
  }

  async function open(at = 0) {
    ensure();
    changedScenes.clear(); backedUp = false; selected = null; aiming = false;
    try { doc = await api("/show/narrator"); }
    catch (error) { showToast(error.message, true); return; }
    buildRanges();
    editedAt = Math.max(0, Math.min(doc.duration - 1 / doc.fps, at));
    const video = $("[data-narr-video]");
    if (video.getAttribute("src") !== doc.source_url) video.src = doc.source_url;
    if (!dialog.open) dialog.showModal();
    selected = segmentIndexAt(editedAt);
    renderAll();
    scrollTo(editedAt, true);
    if (video.readyState >= 1) seek(editedAt);
    api(`/show/narrator/waveform?buckets=${Math.min(8000, Math.ceil(doc.duration * 25))}`).then(data => { peaks = data.peaks; drawVoice(); }).catch(() => {});
    if (doc.job?.status === "working") watchAim();
  }

  function closed() {
    pause(); clearTimeout(poll);
    if (changedScenes.size) window.ShowReview?.narratorChanged?.([...changedScenes]);
  }

  // ---- Changes ------------------------------------------------------------------------------
  async function change(body, done = "") {
    status("Saving…");
    try {
      const result = await api("/show/narrator/framing", { method: "POST", body: JSON.stringify({ ...body, project_id: doc.project_id, ...(!backedUp && !["undo", "redo"].includes(body.op) ? { backup: true } : {}) }) });
      if (!["undo", "redo"].includes(body.op)) backedUp = true;
      (result.changed_scenes || []).forEach(id => changedScenes.add(id));
      const keep = selected != null ? doc.segments[selected]?.start : null;
      doc = result; buildRanges(); filmVersion++;
      selected = keep != null ? segmentIndexAt(body.to ? body.to[0] : body.start ?? keep) : null;
      renderAll(); show(editedAt);
      status(done || (body.op === "undo" ? "Undone." : body.op === "redo" ? "Redone." : "Saved: the scene previews update when you open them."));
    } catch (error) { status(error.message); showToast(error.message, true); }
  }
  const status = text => { if (dialog) $("[data-narr-status]").textContent = text; };

  async function aimAll() {
    if (!confirm("Look at every zoom again and aim it at the face in it? A zoom with no face goes wide; zooms you aimed by hand stay. The current track is saved first, and Undo brings it back.")) return;
    try { await api("/show/narrator/aim", { method: "POST", body: "{}" }); watchAim(); }
    catch (error) { showToast(error.message, true); }
  }
  async function watchAim() {
    clearTimeout(poll);
    const job = await api("/show/narrator/aim").catch(() => null);
    if (!job || !dialog.open) return;
    if (job.status === "working") { status(`Looking for your face in every zoom… ${Math.round((job.progress || 0) * 100)} %`); poll = setTimeout(watchAim, 1200); return; }
    if (job.status === "failed") { status(job.error); showToast(job.error, true); return; }
    if (job.status === "done") {
      (job.changed_scenes || []).forEach(id => changedScenes.add(id));
      doc = await api("/show/narrator"); buildRanges(); filmVersion++; selected = segmentIndexAt(editedAt); renderAll(); show(editedAt);
      const r = job.result || {};
      status(`Aimed ${r.aimed || 0} zooms at the face; ${r.unzoomed || 0} without a face went wide; ${r.kept || 0} aimed by hand stayed.`);
    }
  }

  // ---- Segments (one zoom each) -------------------------------------------------------------
  const segmentIndexAt = t => Math.max(0, doc.segments.findIndex(s => t >= s.start - 1e-6 && t < s.end - 1e-6));
  const isWide = s => s.zoom <= 1.0001 && !s.zoom_end;
  const pushOf = s => !s.zoom_end ? "steady" : s.zoom_end > s.zoom ? "in" : "out";
  function framingBody(s, changes) {
    const zoom = changes.zoom ?? s.zoom, push = changes.push ?? pushOf(s);
    const zoom_end = push === "in" ? Math.min(2.5, Math.round((zoom + 0.2) * 100) / 100) : push === "out" ? Math.max(1, Math.round((zoom - 0.3) * 100) / 100) : null;
    return { op: "frame", start: s.start, end: s.end, zoom, zoom_end: zoom_end != null && Math.abs(zoom_end - zoom) > 1e-3 ? zoom_end : null,
      ...(changes.move ? { move: changes.move } : {}), ...(changes.face ? { face: changes.face } : {}) };
  }

  // ---- Drawing ------------------------------------------------------------------------------
  function renderAll() { renderTimeline(); renderInspector(); renderList(); $("[data-narr-undo]").disabled = !doc.can_undo; $("[data-narr-redo]").disabled = !doc.can_redo; }

  function renderTimeline() {
    const lanes = $("[data-narr-lanes]"), width = Math.ceil(doc.duration * pps);
    lanes.style.width = `${width}px`;
    const major = pps >= 100 ? 1 : pps >= 50 ? 2 : pps >= 25 ? 5 : 10, minor = major / (major === 2 ? 4 : 5);
    const labels = [];
    for (let t = 0; t <= doc.duration + 1e-6; t += major) labels.push(`<span class="narr-tick" style="left:${t * pps}px">${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}</span>`);
    const scenes = doc.scenes.map((scene, i) => `<button type="button" class="narr-scene" data-narr-seek="${scene.start}" style="left:${scene.start * pps}px;width:${(scene.end - scene.start) * pps}px" title="Scene ${i + 1} · ${esc(scene.title)} · ${clock(scene.start)}–${clock(scene.end)}"><b>S${i + 1}</b> ${esc(scene.title)}</button>`).join("");
    const chunks = [];
    for (let x = 0; x < width; x += CHUNK) chunks.push(`<img class="narr-film-chunk" data-narr-film="${x}" alt="" style="left:${x}px;width:${Math.min(CHUNK, width - x)}px" draggable="false">`);
    const segments = doc.segments.map((s, i) => {
      const wide = isWide(s), push = pushOf(s), glide = s.move === "glide" && i > 0;
      const name = wide ? "Wide" : `${zoomName(Math.max(s.zoom, s.zoom_end || 0))} ×${s.zoom.toFixed(2)}${s.zoom_end ? `→${s.zoom_end.toFixed(2)}` : ""}`;
      const moveWidth = glide ? Math.min((s.end - s.start) * pps, 0.5 * pps) : 0;
      return `<div class="narr-seg ${wide ? "wide" : ""} ${selected === i ? "selected" : ""} z${Math.min(3, Math.round((Math.max(s.zoom, s.zoom_end || 0) - 1) * 3.4))}" data-narr-seg="${i}" style="left:${s.start * pps}px;width:${Math.max(2, (s.end - s.start) * pps)}px"
        title="${esc(name)} · ${clock(s.start)}–${clock(s.end)}${glide ? " · camera move into it" : ""}${push !== "steady" ? ` · slow ${push === "in" ? "push in" : "pull out"}` : ""}${s.aimed ? " · aimed by hand" : s.face ? " · on the face" : ""}">
        ${glide ? `<i class="narr-glide" style="width:${moveWidth}px" aria-label="Camera move"></i>` : ""}${wide ? "" : '<span data-narr-edge="start"></span>'}<b>${esc(name)}${push === "in" ? " ↗" : push === "out" ? " ↘" : ""}</b>${wide ? "" : '<span data-narr-edge="end"></span>'}</div>`;
    }).join("");
    const cuts = doc.cuts.map(cut => `<button type="button" class="narr-cut" data-narr-seek="${cut.at}" style="left:${cut.at * pps}px" title="${clock(cut.at)} · ${cut.removed.toFixed(1)} s taken out (${REASONS[cut.reason] || cut.reason})">✂</button>`).join("");
    const voice = [];
    for (let x = 0; x < width; x += CHUNK) voice.push(`<canvas class="narr-voice-chunk" data-narr-voice="${x}" width="${Math.min(CHUNK, width - x)}" height="40" style="left:${x}px"></canvas>`);
    lanes.innerHTML = `<div class="narr-ruler" style="--major:${major * pps}px;--minor:${minor * pps}px">${labels.join("")}</div>
      <div class="narr-lane narr-scenes">${scenes}</div><div class="narr-lane narr-film">${chunks.join("")}</div>
      <div class="narr-lane narr-camera">${segments}</div><div class="narr-lane narr-cuts">${cuts}</div>
      <div class="narr-lane narr-voice">${voice.join("")}</div><div class="narr-playhead" data-narr-playhead></div>`;
    lanes.querySelectorAll("[data-narr-seek]").forEach(button => button.onclick = event => { event.stopPropagation(); pause(); seek(Number(button.dataset.narrSeek) + 0.01); });
    loadVisible(); drawVoice(); placePlayhead();
  }

  function loadVisible() {
    const scroll = $("[data-narr-scroll]"), from = scroll.scrollLeft - CHUNK, to = scroll.scrollLeft + scroll.clientWidth + CHUNK;
    dialog.querySelectorAll("[data-narr-film]").forEach(img => {
      const x = Number(img.dataset.narrFilm), w = parseFloat(img.style.width);
      if (x + w < from || x > to) return;
      const count = Math.max(1, Math.round(w / (FILM_H * 16 / 9))), url = `/api/v1/show/filmstrip?start=${(x / pps).toFixed(3)}&end=${((x + w) / pps).toFixed(3)}&count=${count}&h=${FILM_H}&v=${filmVersion}`;
      if (img.getAttribute("src") !== url) img.src = url;
    });
  }

  function drawVoice() {
    if (!peaks || !dialog) return;
    const color = getComputedStyle(dialog).getPropertyValue("--narr-voice").trim() || "#7fbf62";
    dialog.querySelectorAll("[data-narr-voice]").forEach(canvas => {
      const x0 = Number(canvas.dataset.narrVoice), ctx = canvas.getContext("2d"), h = canvas.height;
      ctx.clearRect(0, 0, canvas.width, h); ctx.fillStyle = color;
      const perPx = peaks.length / (doc.duration * pps);
      for (let x = 0; x < canvas.width; x++) {
        const a = Math.floor((x0 + x) * perPx), b = Math.max(a + 1, Math.floor((x0 + x + 1) * perPx));
        let v = 0; for (let i = a; i < b && i < peaks.length; i++) v = Math.max(v, peaks[i]);
        const bar = Math.max(1, v * (h - 2)); ctx.fillRect(x, (h - bar) / 2, 1, bar);
      }
    });
  }

  function renderInspector() {
    const box = $("[data-narr-inspector]"), s = doc.segments[selected];
    if (!s) { box.innerHTML = `<p class="show-hint">Click a stretch on the Zooms row to change its framing.</p>`; return; }
    const wide = isWide(s), push = pushOf(s), first = selected === 0;
    const words = s.shots.map(i => doc.shots[i]?.text).filter(Boolean).join(" ");
    const choice = (value, label, current, attr) => `<button type="button" class="${current ? "active" : ""}" ${attr}="${value}" aria-pressed="${current}">${label}</button>`;
    box.innerHTML = `<h3>${wide ? "Wide" : zoomName(Math.max(s.zoom, s.zoom_end || 0))} · ${clock(s.start)}–${clock(s.end)}</h3>
      ${words ? `<p class="narr-words">“${esc(words.length > 180 ? words.slice(0, 180) + "…" : words)}”</p>` : ""}
      <div class="narr-field"><span>Zoom</span><div class="narr-choices">${Object.entries({ wide: 1, medium: 1.3, close: 1.65, closer: 2 }).map(([key, z]) =>
        choice(z, key[0].toUpperCase() + key.slice(1), Math.abs(s.zoom - z) < 0.01, "data-narr-zoom")).join("")}</div>
        <label class="narr-slider">×<input type="range" min="1" max="2.5" step="0.05" value="${s.zoom}" data-narr-zoom-range aria-label="Zoom"><output>${s.zoom.toFixed(2)}</output></label></div>
      <div class="narr-field"><span>During it</span><div class="narr-choices">${choice("steady", "Steady", push === "steady", "data-narr-push")}${choice("in", "Slow push in", push === "in", "data-narr-push")}${s.zoom > 1.0001 ? choice("out", "Slow pull out", push === "out", "data-narr-push") : ""}</div></div>
      ${first ? "" : `<div class="narr-field"><span>Into it</span><div class="narr-choices">${choice("cut", "Cut", s.move !== "glide", "data-narr-move")}${choice("glide", "Camera move", s.move === "glide", "data-narr-move")}</div><small>A camera move travels from the previous framing in half a second; a cut changes at once.</small></div>`}
      ${wide && push === "steady" ? "" : `<div class="narr-field"><span>Aim</span><button type="button" data-narr-aim class="${aiming ? "active" : ""}">${aiming ? "Click what must stay in frame… (Esc)" : "Choose on the picture…"}</button><small>${s.aimed ? "Aimed by hand." : s.face ? "Aimed at your face." : "No face found here: centred."}</small></div>`}
      <div class="narr-actions">${wide ? "" : '<button type="button" data-narr-remove>Remove zoom</button>'}<button type="button" data-narr-preview-seg>Play it</button></div>`;
    box.querySelectorAll("[data-narr-zoom]").forEach(button => button.onclick = () => change(framingBody(s, { zoom: Number(button.dataset.narrZoom), ...(Number(button.dataset.narrZoom) <= 1 && pushOf(s) === "out" ? { push: "steady" } : {}) })));
    const range = box.querySelector("[data-narr-zoom-range]");
    range.oninput = () => { range.nextElementSibling.textContent = Number(range.value).toFixed(2); };
    range.onchange = () => change(framingBody(s, { zoom: Number(range.value) }));
    box.querySelectorAll("[data-narr-push]").forEach(button => button.onclick = () => change(framingBody(s, { push: button.dataset.narrPush })));
    box.querySelectorAll("[data-narr-move]").forEach(button => button.onclick = () => change({ op: "frame", start: s.start, end: s.end, move: button.dataset.narrMove }));
    box.querySelector("[data-narr-aim]")?.addEventListener("click", () => { aiming = !aiming; if (aiming) { pause(); seek(Math.max(editedAt, s.start + 0.02) < s.end ? Math.max(editedAt, s.start + 0.02) : s.start + 0.02); } renderInspector(); show(editedAt); });
    box.querySelector("[data-narr-remove]")?.addEventListener("click", () => removeZoom(s));
    box.querySelector("[data-narr-preview-seg]").onclick = () => { seek(Math.max(0, s.start - (s.move === "glide" ? 0.6 : 0.3))); play(s.end + 0.4); };
  }
  const removeZoom = s => change({ op: "frame", start: s.start, end: s.end, zoom: 1, zoom_end: null, move: "cut" }, "Zoom removed: that stretch is wide now.");

  function renderList() {
    const zooms = doc.segments.map((s, i) => [s, i]).filter(([s]) => !isWide(s));
    $("[data-narr-list-title]").textContent = `Edits on your video · ${zooms.length} zooms, ${doc.segments.filter(s => s.move === "glide").length} camera moves, ${doc.cuts.length} cuts`;
    $("[data-narr-list]").innerHTML = `<div class="narr-list-cols"><section><h4>Zooms</h4>${zooms.map(([s, i]) => `<div class="narr-row ${selected === i ? "selected" : ""}"><button type="button" data-narr-go="${i}">${clock(s.start)}–${clock(s.end)}</button>
        <span>${zoomName(Math.max(s.zoom, s.zoom_end || 0))} ×${s.zoom.toFixed(2)}${s.zoom_end ? `→${s.zoom_end.toFixed(2)}` : ""}${s.move === "glide" ? " · camera move" : ""}${s.aimed ? " · aimed by hand" : s.face ? " · on the face" : " · centred"}</span>
        <button type="button" data-narr-del="${i}" aria-label="Remove this zoom">Remove</button></div>`).join("") || '<p class="show-hint">No zooms.</p>'}</section>
      <section><h4>Cuts</h4>${doc.cuts.map(cut => `<div class="narr-row"><button type="button" data-narr-cut="${cut.at}">${clock(cut.at)}</button><span>✂ ${cut.removed.toFixed(1)} s taken out · ${REASONS[cut.reason] || cut.reason}</span></div>`).join("") || '<p class="show-hint">No cuts.</p>'}</section></div>`;
    dialog.querySelectorAll("[data-narr-go]").forEach(button => button.onclick = () => { const s = doc.segments[Number(button.dataset.narrGo)]; select(Number(button.dataset.narrGo)); pause(); seek(s.start + 0.02); scrollTo(s.start); });
    dialog.querySelectorAll("[data-narr-del]").forEach(button => button.onclick = () => removeZoom(doc.segments[Number(button.dataset.narrDel)]));
    dialog.querySelectorAll("[data-narr-cut]").forEach(button => button.onclick = () => { pause(); seek(Math.max(0, Number(button.dataset.narrCut) - 0.8)); scrollTo(Number(button.dataset.narrCut)); });
  }

  function select(index) {
    selected = index; aiming = false;
    dialog.querySelectorAll("[data-narr-seg]").forEach(el => el.classList.toggle("selected", Number(el.dataset.narrSeg) === index));
    renderInspector(); renderList();
  }

  // ---- Player -------------------------------------------------------------------------------
  function seek(t) {
    editedAt = Math.max(0, Math.min(doc.duration - 0.5 / doc.fps, t));
    rangeIndex = rangeAt(editedAt);
    const r = ranges[rangeIndex], video = $("[data-narr-video]");
    if (r && video.readyState >= 1) video.currentTime = r.src + Math.max(0, editedAt - r.at);
    show(editedAt);
  }
  let stopAt = null;
  function play(until = null) {
    const video = $("[data-narr-video]"); stopAt = until; aiming = false;
    playing = true; $("[data-narr-play]").textContent = "Pause";
    video.play().catch(() => {}); cancelAnimationFrame(frame); frame = requestAnimationFrame(tick);
  }
  function pause() {
    if (!dialog) return;
    playing = false; $("[data-narr-play]").textContent = "Play";
    $("[data-narr-video]").pause(); cancelAnimationFrame(frame);
  }
  const toggle = () => playing ? pause() : play();
  function tick() {
    if (!playing) return;
    const video = $("[data-narr-video]"), r = ranges[rangeIndex];
    if (r && !video.seeking) {
      if (video.currentTime >= r.src + r.len - 0.5 / doc.fps || video.ended) {
        if (rangeIndex + 1 < ranges.length) { rangeIndex++; video.currentTime = ranges[rangeIndex].src; }   // skip what was cut
        else { pause(); return; }
      }
      editedAt = ranges[rangeIndex].at + Math.max(0, video.currentTime - ranges[rangeIndex].src);
      if (stopAt != null && editedAt >= stopAt) { pause(); stopAt = null; }
      show(editedAt, true);
    }
    frame = requestAnimationFrame(tick);
  }
  function show(t, follow = false) {
    const n = Math.round(t * doc.fps), [x0, y0, w] = windowOf(cameraAt(n)), video = $("[data-narr-video]");
    video.style.transform = aiming ? "none" : `scale(${1 / w}) translate(${-x0 * 100}%, ${-y0 * 100}%)`;
    const box = $("[data-narr-aim-box]"), dot = $("[data-narr-aim-dot]"), s = doc.segments[selected];
    box.hidden = dot.hidden = !aiming;
    if (aiming) {
      Object.assign(box.style, { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${w * 100}%`, height: `${w * 100}%` });
      const face = s && (doc.shots[s.shots[0]]?.face);
      dot.hidden = !face; if (face) Object.assign(dot.style, { left: `${face[0] * 100}%`, top: `${face[1] * 100}%` });
    }
    $("[data-narr-clock]").textContent = `${clock(t)} / ${clock(doc.duration)}`;
    placePlayhead(); if (follow) scrollTo(t);
  }
  function placePlayhead() { const head = dialog?.querySelector("[data-narr-playhead]"); if (head) head.style.left = `${editedAt * pps}px`; }
  function scrollTo(t, centre = false) {
    const scroll = $("[data-narr-scroll]"), x = t * pps;
    if (centre || x < scroll.scrollLeft + 40 || x > scroll.scrollLeft + scroll.clientWidth - 40) scroll.scrollLeft = Math.max(0, x - scroll.clientWidth * (centre ? 0.5 : 0.15));
  }

  // Aiming: the whole picture with the crop box; a click puts that point in frame.
  function aimClick(event) {
    if (!aiming) { toggle(); return; }
    const s = doc.segments[selected]; if (!s) return;
    const bounds = $("[data-narr-frame]").getBoundingClientRect();
    const face = [(event.clientX - bounds.left) / bounds.width, (event.clientY - bounds.top) / bounds.height].map(v => Math.round(Math.min(1, Math.max(0, v)) * 1000) / 1000);
    aiming = false;
    change({ ...framingBody(s, {}), face }, "Aimed: that point stays in frame for this zoom.");
  }

  // ---- Timeline gestures --------------------------------------------------------------------
  function timeAt(event) { const lanes = $("[data-narr-lanes]").getBoundingClientRect(); return Math.max(0, Math.min(doc.duration, (event.clientX - lanes.left) / pps)); }
  function snapTimes() { return [0, doc.duration, ...doc.cuts.map(c => c.at), ...doc.segments.flatMap(s => [s.start, s.end]), ...doc.scenes.flatMap(s => [s.start, s.end])]; }
  function snapped(t, skip = []) {
    const near = snapTimes().filter(x => !skip.some(y => Math.abs(x - y) < 1e-6)).reduce((best, x) => Math.abs(x - t) < Math.abs(best - t) ? x : best, Infinity);
    return Math.abs(near - t) * pps <= 8 ? near : Math.round(t * doc.fps) / doc.fps;
  }
  function pointerDown(event) {
    if (event.button !== 0) return;
    const el = event.target.closest("[data-narr-seg]");
    if (!el) {
      if (event.target.closest("button")) return;
      pause(); seek(timeAt(event));
      const scroll = $("[data-narr-scroll]");
      scroll.setPointerCapture?.(event.pointerId);
      scroll.onpointermove = e => seek(timeAt(e));
      scroll.onpointerup = () => { scroll.onpointermove = scroll.onpointerup = null; };
      return;
    }
    const index = Number(el.dataset.narrSeg), s = doc.segments[index], edge = event.target.dataset.narrEdge, x = event.clientX;
    if (selected !== index) select(index);
    if (isWide(s)) { pause(); seek(timeAt(event)); return; }
    event.preventDefault(); el.setPointerCapture?.(event.pointerId);
    let moved = false, to = [s.start, s.end];
    el.onpointermove = e => {
      const delta = (e.clientX - x) / pps; moved ||= Math.abs(e.clientX - x) > 3; if (!moved) return;
      if (edge === "start") to = [Math.min(s.end - 2 / doc.fps, snapped(s.start + delta, [s.start])), s.end];
      else if (edge === "end") to = [s.start, Math.max(s.start + 2 / doc.fps, snapped(s.end + delta, [s.end]))];
      else { const length = s.end - s.start; let a = snapped(s.start + delta, [s.start, s.end]); if (Math.abs(a - (s.start + delta)) * pps > 8) a = s.start + delta; a = Math.max(0, Math.min(doc.duration - length, a)); to = [a, a + length]; }
      el.style.left = `${to[0] * pps}px`; el.style.width = `${(to[1] - to[0]) * pps}px`; el.classList.add("dragging");
      status(`${clock(to[0])}–${clock(to[1])}`);
    };
    el.onpointerup = () => {
      el.onpointermove = el.onpointerup = null; el.classList.remove("dragging");
      if (!moved) { pause(); seek(Math.max(s.start, Math.min(s.end - 0.02, timeAt(event)))); return; }
      change({ op: "move", from: [s.start, s.end], to }, edge ? "Zoom resized." : "Zoom moved: the stretch it left is wide now.");
    };
  }
  function doubleClick(event) {
    const el = event.target.closest("[data-narr-seg]"); if (!el) return;
    const s = doc.segments[Number(el.dataset.narrSeg)]; if (!isWide(s)) return;
    const t = timeAt(event), a = Math.max(s.start, t - 1), b = Math.min(s.end, a + 2);
    change({ op: "frame", start: a, end: b, zoom: 1.65, zoom_end: null }, "Zoom added on the face. Drag its edges to choose how long it lasts.");
  }
  function keys(event) {
    if (event.target.closest?.("input,textarea,select")) return;
    if (event.key === " ") { event.preventDefault(); toggle(); }
    else if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); pause(); seek(editedAt + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 10 : 1) / doc.fps); scrollTo(editedAt); }
    else if ((event.key === "Delete" || event.key === "Backspace") && selected != null && !isWide(doc.segments[selected])) { event.preventDefault(); removeZoom(doc.segments[selected]); }
    else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") { event.preventDefault(); change({ op: event.shiftKey ? "redo" : "undo" }); }
    else if (event.key === "Escape" && aiming) { event.preventDefault(); aiming = false; renderInspector(); show(editedAt); }
  }

  window.ShowNarrator = { open };
})();
