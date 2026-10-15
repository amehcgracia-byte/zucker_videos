// Character studio: a thing cut out of a photo with its own pixels (automatic
// cut-out, then keep/erase brush), rigged once (mouth, pivot, voice) and kept
// in the library to reuse in any scene.
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const baseName = (path) => String(path || "").split(/[\\/]/).pop();
  let state = null;

  function show(step) {
    $("#showCharacterStudio").hidden = false;
    document.querySelectorAll("[data-studio-step]").forEach((item) => item.classList.toggle("active", Number(item.dataset.studioStep) === step));
    state.step = step;
    if (step === 1) renderPhotos();
    if (step === 2) renderCutout();
    if (step === 3) renderRig();
  }

  async function renderPhotos() {
    const photos = (await api("/show/photos")).items || [];
    const sources = window.ShowReview.sources();
    const videos = [...new Set(photos.map((item) => item.video || ""))];
    const only = state.photoFilter && videos.includes(state.photoFilter) ? state.photoFilter : "";
    // One group per video, in the order of its moments.
    const groups = videos.filter((video) => !only || video === only).map((video) => {
      const items = photos.map((item, index) => ({ item, index })).filter(({ item }) => (item.video || "") === video)
        .sort((a, b) => (a.item.second ?? 0) - (b.item.second ?? 0));
      return `<h4 class="show-photo-group">${escape(video ? baseName(video) : "Added photos")} · ${items.length}</h4>
        <div class="show-photo-grid">${items.map(({ item, index }) => `<button type="button" data-photo="${index}" title="${escape(item.name)}"><img src="${item.url}" alt="" loading="lazy"><span>${escape(item.name)}</span></button>`).join("")}</div>`;
    }).join("");
    $("#showStudioBody").innerHTML = `<p class="show-hint">Choose a clear, focused photo of the whole thing. Take photos of every steady moment of your videos, or one at an exact second.</p>
      <div class="show-actions"><button type="button" class="primary icon-button small" id="showPhotoLibrary">Photos of every steady moment</button>
        ${videos.length > 1 ? `<select id="showPhotoFilter"><option value="">All videos</option>${videos.map((video) => `<option value="${escape(video)}" ${video === only ? "selected" : ""}>${escape(video ? baseName(video) : "Added photos")}</option>`).join("")}</select>` : ""}</div>
      <p id="showStudioStatus" class="show-hint" role="status" aria-live="polite"></p>
      ${groups || "<p>No photos yet.</p>"}
      <form id="showTakePhoto" class="show-review-add"><strong>Take a photo</strong>
        <select name="source">${sources.map((path) => `<option value="${escape(path)}">${escape(baseName(path))}</option>`).join("")}</select>
        <label>at <input name="second" type="number" min="0" step="0.1" value="0"> s</label>
        <label>name <input name="name" type="text" value="thing"></label><button class="icon-button small" type="submit">Take it</button></form>`;
    $("#showStudioBody").querySelectorAll("[data-photo]").forEach((button) => button.onclick = () => {
      state.photo = photos[Number(button.dataset.photo)];
      state.strokes = []; state.undo = []; state.box = null; state.cut = null;
      show(2);
    });
    const filter = $("#showPhotoFilter");
    if (filter) filter.onchange = () => { state.photoFilter = filter.value; renderPhotos(); };
    $("#showPhotoLibrary").onclick = async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      let added = 0;
      // The close-ups and other takes, not the talking video itself.
      const videos = window.ShowReview.resources();
      try {
        for (const [index, source] of videos.entries()) {
          $("#showStudioStatus").textContent = `Taking photos of ${baseName(source)}… (${index + 1} of ${videos.length})`;
          added += (await api("/show/photos/library", { method: "POST", body: JSON.stringify({ source }) })).added;
        }
        $("#showStudioStatus").textContent = added ? `${added} new photos.` : "No new photos: every steady moment already has one.";
        renderPhotos();
      } catch (error) { showToast(error.message, true); button.disabled = false; }
    };
    $("#showTakePhoto").onsubmit = async (event) => {
      event.preventDefault();
      const form = event.target;
      try {
        await api("/show/photos", { method: "POST", body: JSON.stringify({ source: form.elements.source.value, second: Number(form.elements.second.value), name: form.elements.name.value }) });
        renderPhotos();
      } catch (error) { showToast(error.message, true); }
    };
  }

  async function cut() {
    $("#showStudioStatus").textContent = "Cutting out… (the first time can take half a minute)";
    try {
      state.cut = await api("/show/characters/cutout", { method: "POST", body: JSON.stringify({ photo: state.photo.photo, box: state.box, strokes: state.strokes }) });
      $("#showStudioStatus").textContent = `Cut out with ${state.cut.method === "macos-vision" ? "macOS subject lifting" : "a box (GrabCut)"}. Paint to keep or erase; only the edge changes, never the pixels.`;
      drawPhoto();
      $("#showStudioPreview").src = state.cut.url;
    } catch (error) { $("#showStudioStatus").textContent = error.message; }
  }

  function renderCutout() {
    $("#showStudioBody").innerHTML = `<div class="show-studio-tools">
        <label><input type="radio" name="brush" value="keep" checked> Keep brush</label><label><input type="radio" name="brush" value="erase"> Erase brush</label>
        <label><input type="radio" name="brush" value="box"> Box (cut inside it)</label>
        <label>Size <input type="range" id="showBrushSize" min="0.005" max="0.08" step="0.005" value="0.02"></label>
        <button type="button" class="icon-button small" data-studio="undo">Undo</button>
        <button type="button" class="icon-button small" data-studio="auto">Automatic again</button></div>
      <p id="showStudioStatus" class="show-hint"></p>
      <div class="show-studio-split"><canvas id="showStudioPhoto" class="show-canvas" width="960" height="540"></canvas><div class="show-checker"><img id="showStudioPreview" alt="Cut-out"></div></div>
      <div class="show-actions"><button type="button" class="icon-button small" data-studio="back1">Another photo</button><button type="button" class="primary icon-button small" data-studio="next3">Looks right</button></div>`;
    state.image = new Image();
    state.image.onload = () => drawPhoto();       // not with the load event as a stroke
    state.image.src = state.photo.url;
    const canvas = $("#showStudioPhoto");
    let painting = null;
    const point = (event) => { const box = canvas.getBoundingClientRect(); return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height]; };
    canvas.onpointerdown = (event) => {
      canvas.setPointerCapture(event.pointerId);
      const mode = document.querySelector("input[name=brush]:checked").value;
      painting = { mode, start: point(event), points: [point(event)], radius: Number($("#showBrushSize").value) };
    };
    canvas.onpointermove = (event) => { if (painting) { painting.points.push(point(event)); drawPhoto(painting); } };
    canvas.onpointerup = () => {
      if (!painting) return;
      const done = painting;
      painting = null;
      state.undo.push(JSON.stringify({ strokes: state.strokes, box: state.box }));
      if (done.mode === "box") {
        const [x0, y0] = done.start, [x1, y1] = done.points.at(-1);
        state.box = [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)];
        state.strokes = [];
      } else state.strokes.push({ mode: done.mode, points: done.points, radius: done.radius });
      cut();
    };
    cut();
  }

  function drawPhoto(live) {
    const canvas = $("#showStudioPhoto");
    if (!canvas || !state.image?.complete) return;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);
    if (state.cut?.mask_url) {
      const mask = new Image();
      mask.onload = () => {
        ctx.save(); ctx.globalAlpha = 0.35; ctx.globalCompositeOperation = "multiply"; ctx.drawImage(mask, 0, 0, canvas.width, canvas.height); ctx.restore();
        if (live) strokePreview(ctx, live);
      };
      mask.src = state.cut.mask_url;
    }
    if (live) strokePreview(ctx, live);
  }

  function strokePreview(ctx, stroke) {
    const canvas = ctx.canvas;
    ctx.save();
    ctx.strokeStyle = stroke.mode === "keep" ? "rgba(80,220,120,.8)" : stroke.mode === "erase" ? "rgba(240,80,80,.8)" : "#f3d478";
    if (stroke.mode === "box") {
      const [x0, y0] = stroke.start, [x1, y1] = stroke.points.at(-1);
      ctx.lineWidth = 2; ctx.strokeRect(x0 * canvas.width, y0 * canvas.height, (x1 - x0) * canvas.width, (y1 - y0) * canvas.height);
    } else {
      ctx.lineWidth = stroke.radius * 2 * canvas.width; ctx.lineCap = "round"; ctx.beginPath();
      stroke.points.forEach(([x, y], i) => (i ? ctx.lineTo(x * canvas.width, y * canvas.height) : ctx.moveTo(x * canvas.width, y * canvas.height)));
      ctx.stroke();
    }
    ctx.restore();
  }

  function renderRig() {
    const voices = window.ShowReview.voices();
    $("#showStudioBody").innerHTML = `<p class="show-hint">Drag a rectangle over its mouth (or jaw): those pixels will move with its voice and black shows behind. Then click once with "Pivot" on the point it stands or turns on.</p>
      <div class="show-studio-tools"><label><input type="radio" name="rigtool" value="mouth" checked> Mouth</label><label><input type="radio" name="rigtool" value="pivot"> Pivot</label></div>
      <div class="show-checker show-rig"><canvas id="showRigCanvas" class="show-canvas"></canvas></div>
      <div class="show-fields"><label class="show-field"><span>Name</span><input type="text" id="showCharacterName" value="${escape(state.photo.name)}"></label>
        <label class="show-field"><span>Its voice (from your videos)</span><select id="showCharacterVoice"><option value="">No voice yet</option>${voices.map((voice, index) => `<option value="${index}">${escape(voice.text.slice(0, 60))} (${escape(baseName(voice.source))} ${voice.start.toFixed(1)} s)</option>`).join("")}</select></label></div>
      <div class="show-actions"><button type="button" class="icon-button small" data-studio="back2">Back to the cut-out</button><button type="button" class="primary icon-button small" data-studio="save">Save in the library and place it</button></div>`;
    state.voices = voices;
    const canvas = $("#showRigCanvas");
    const image = new Image();
    image.onload = () => {
      const k = Math.min(520 / image.width, 420 / image.height);
      canvas.width = Math.round(image.width * k); canvas.height = Math.round(image.height * k);
      state.rigImage = image; drawRig();
    };
    image.src = state.cut.url;
    let drag = null;
    const point = (event) => { const box = canvas.getBoundingClientRect(); return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height]; };
    canvas.onpointerdown = (event) => {
      const tool = document.querySelector("input[name=rigtool]:checked").value;
      if (tool === "pivot") { state.pivot = point(event); drawRig(); return; }
      canvas.setPointerCapture(event.pointerId); drag = point(event);
    };
    canvas.onpointermove = (event) => {
      if (!drag) return;
      const [x, y] = point(event);
      state.mouth = { rect: [Math.min(x, drag[0]), Math.min(y, drag[1]), Math.abs(x - drag[0]), Math.abs(y - drag[1])] };
      drawRig();
    };
    canvas.onpointerup = () => { drag = null; };
  }

  function drawRig() {
    const canvas = $("#showRigCanvas");
    if (!canvas || !state.rigImage) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(state.rigImage, 0, 0, canvas.width, canvas.height);
    if (state.mouth) { const [x, y, w, h] = state.mouth.rect; ctx.strokeStyle = "#f3d478"; ctx.lineWidth = 2; ctx.strokeRect(x * canvas.width, y * canvas.height, w * canvas.width, h * canvas.height); }
    if (state.pivot) { ctx.beginPath(); ctx.arc(state.pivot[0] * canvas.width, state.pivot[1] * canvas.height, 6, 0, Math.PI * 2); ctx.fillStyle = "#f472b6"; ctx.fill(); }
  }

  async function save() {
    const index = $("#showCharacterVoice").value;
    const voice = index === "" ? null : state.voices[Number(index)];
    const saved = await api("/show/characters", { method: "POST", body: JSON.stringify({
      photo: state.photo.photo, mask: state.cut.mask, method: state.cut.method, name: $("#showCharacterName").value,
      mouth: state.mouth || null, pivot: state.pivot || [0.5, 1.0], voice: voice ? [voice.source, voice.start, voice.length] : null }) });
    $("#showCharacterStudio").hidden = true;
    await window.ShowReview.refreshLibrary();
    await window.ShowReview.addToScene(saved.cutout);
    showToast(`${saved.name} is in the library and in this scene`);
  }

  document.addEventListener("click", async (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-studio]") : null;
    if (!target) return;
    try {
      const action = target.dataset.studio;
      if (action === "open") { state = { step: 1, strokes: [], undo: [] }; show(1); }
      else if (action === "close") $("#showCharacterStudio").hidden = true;
      else if (action === "back1") show(1);
      else if (action === "back2") show(2);
      else if (action === "next3") { if (!state.cut) return; show(3); }
      else if (action === "undo" && state.undo.length) { const last = JSON.parse(state.undo.pop()); state.strokes = last.strokes; state.box = last.box; cut(); }
      else if (action === "auto") { state.undo.push(JSON.stringify({ strokes: state.strokes, box: state.box })); state.strokes = []; state.box = null; cut(); }
      else if (action === "save") { target.disabled = true; await save(); }
    } catch (error) { showToast(error.message, true); }
    finally { if (target.dataset.studio === "save") target.disabled = false; }
  });
})();
