// Show storyboard: identity -> intro -> scenes -> review and render.
// Each scene explains what it proposes; watch it (a real preview, prepared in
// the background), then accept it, modify it or leave it without effect. Inside
// "Modify", every effect has its own editor: regions are kept in the
// coordinates of their own picture, outlines exist only in the editor, and the
// pixels that move are the originals. Nothing is rendered until the last step.
(() => {
  const KIND_LABELS = { cutaway: "Close-up", character: "Character speaks", drawing: "Drawing", word: "Word", warp: "Warp", steam: "Rainbow steam", math: "Formulas around you" };
  const STATUS_LABELS = { proposed: "Proposed", approved: "Approved", discarded: "Discarded" };
  const DECISION_LABELS = { pending: "To decide", accepted: "Accepted", modified: "Modified", plain: "Without effect" };
  const ACTIONS = { mouth: "Mouth (with the voice)", nod: "Nod", side: "Sideways", turn: "Turn" };
  const DRAWING_STYLES = { pencil: "Pencil", crayon: "Wax crayons", marker: "Felt-tip marker" };
  const ENTRANCES = { cut: "Straight cut: it talks at once", zoom: "Push in, then it talks", bounce: "The photo bounces in over the frozen host" };
  let review = null;          // the storyboard: identity, intro, scenes, proposals, resources
  let library = [];
  let step = 0;
  let poll = null;
  let selectedOverlay = null;
  const open = new Set();
  const editors = new Map();

  const $ = (selector, root = document) => root.querySelector(selector);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const clock = (seconds) => { const s = Math.max(0, Number(seconds) || 0); return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`; };
  const media = (path) => `/api/v1/show/media?path=${encodeURIComponent(path)}`;
  const still = (source, t) => `/api/v1/show/still?source=${encodeURIComponent(source)}&t=${encodeURIComponent(Number(t) || 0)}`;
  const isPhoto = (path) => /\.(jpe?g|png|webp|heic)$/i.test(String(path || ""));
  const baseName = (path) => String(path || "").split(/[\\/]/).pop();

  function picture(item) {
    const p = item.params;
    if (item.kind === "cutaway") return p.thumb ? media(p.thumb) : still(p.source, p.source_start);
    if (item.kind === "character") return still(p.source, p.still_time);
    return `/api/v1/show/frame?t=${encodeURIComponent(item.at)}`;
  }

  async function openReview() {
    review = await api("/show/scenes");
    library = (await api("/show/library").catch(() => ({ items: [] }))).items || [];
    step = 0;
    render();
  }

  // ---- Storyboard -------------------------------------------------------------

  const scenes = () => review.scenes || [];
  const stepNames = () => ["Identity", "Intro", ...scenes().map((scene) => scene.name), "Review & render"];
  const currentScene = () => (step >= 2 && step < 2 + scenes().length ? scenes()[step - 2] : null);

  function render() {
    if (!review) return;
    clearTimeout(poll);
    stopWatching();
    const names = stepNames();
    $("#showSteps").innerHTML = names.map((name, index) => {
      const scene = index >= 2 && index < names.length - 1 ? scenes()[index - 2] : null;
      const mark = scene ? ` <small class="show-decision show-${scene.decision}">${DECISION_LABELS[scene.decision]}</small>` : "";
      const done = scene && scene.decision !== "pending" ? "✓ " : "";
      return `<button type="button" data-show-step="${index}" class="${index === step ? "active" : ""}">${done}${escape(name)}${mark}</button>`;
    }).join("");
    // How far along the show is: every decided scene counts, whatever was decided.
    const decided = scenes().filter((scene) => scene.decision !== "pending").length;
    const total = scenes().length;
    $("#showProgress span").textContent = decided === total ? `All ${total} scenes decided ✓` : `${decided} of ${total} scenes decided · ${total - decided} to decide`;
    Object.assign($("#showProgress progress"), { max: Math.max(1, total), value: decided });
    $("#showStepCount").textContent = `${step + 1} of ${names.length}`;
    $("#showBack").disabled = step === 0;
    $("#showNext").hidden = step === names.length - 1;
    const video = $("#showStageVideo");
    video.pause();
    $("#showStageLayer").innerHTML = "";
    $("#showStageStatus").textContent = "";
    selectedOverlay = null;
    if (step === 0) renderIdentity();
    else if (step === 1) renderIntro();
    else if (currentScene()) renderScene();
    else renderFinal();
  }

  function showVideo(url, status = "") {
    const video = $("#showStageVideo");
    $("#showStageCard").hidden = true;
    video.hidden = !url;
    if (url && video.getAttribute("src") !== url) { video.src = url; video.load(); }
    if (!url) video.removeAttribute("src");
    $("#showStageStatus").textContent = status;
  }

  function drawCard() {
    const canvas = $("#showStageCard");
    const ctx = canvas.getContext("2d");
    const identity = review.identity;
    canvas.hidden = false;
    $("#showStageVideo").hidden = true;
    ctx.fillStyle = "#101214";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const title = identity.title || "";
    const writeTitle = (y) => {
      if (!title) return;
      ctx.font = "bold 64px 'Marker Felt', 'Segoe Print', 'Comic Sans MS', sans-serif";
      ctx.textAlign = "center";
      ctx.lineWidth = 8; ctx.strokeStyle = "#000"; ctx.strokeText(title, canvas.width / 2, y);
      ctx.fillStyle = "#faf6eb"; ctx.fillText(title, canvas.width / 2, y);
    };
    if (identity.logo) {
      const image = new Image();
      image.onload = () => {
        const k = Math.min(canvas.width * 0.32 / image.width, canvas.height * 0.38 / image.height);
        ctx.drawImage(image, (canvas.width - image.width * k) / 2, canvas.height * 0.44 - image.height * k, image.width * k, image.height * k);
        writeTitle(canvas.height * 0.62);
      };
      image.src = "/api/v1/wizard/logo/project";
    } else writeTitle(canvas.height / 2 + 20);
    $("#showStageStatus").textContent = title || identity.logo ? `Title card · ${identity.seconds || 2} s` : "No title card: the show starts straight away.";
  }

  function renderIdentity() {
    const identity = review.identity;
    $("#showPanel").innerHTML = `<h3>Your identity</h3>
      <label class="show-field"><span>Title</span><input type="text" id="showTitle" maxlength="80" value="${escape(identity.title)}" placeholder="${escape(identity.suggested_title || "")}"></label>
      <label><input type="checkbox" id="showUseLogo" ${identity.logo ? "checked" : ""} ${review.project_logo ? "" : "disabled"}> Show the project's logo ${review.project_logo ? "" : "(add one in Overlay &amp; Captions first)"}</label>
      <label class="show-field"><span>Seconds on screen</span><input type="number" id="showCardSeconds" min="1" max="6" step="0.5" value="${identity.seconds || 2}"></label>
      <p class="show-hint">The title card opens the show. Leave the title empty and the logo off to start straight away. The logo is shown as it is, never redrawn.</p>`;
    const save = async (changes) => {
      const result = await api("/show/scenes/identity", { method: "PATCH", body: JSON.stringify(changes) });
      review.identity = result.identity;
      drawCard();
    };
    $("#showTitle").oninput = (event) => { review.identity.title = event.target.value; drawCard(); clearTimeout(poll); poll = setTimeout(() => save({ title: event.target.value }).catch((error) => showToast(error.message, true)), 400); };
    $("#showUseLogo").onchange = (event) => save({ use_logo: event.target.checked }).catch((error) => showToast(error.message, true));
    $("#showCardSeconds").onchange = (event) => save({ seconds: Number(event.target.value) }).catch((error) => showToast(error.message, true));
    drawCard();
  }

  function renderIntro() {
    const videos = [review.source, ...(review.resources || []).map((item) => item.path)].filter(Boolean);
    const chosen = review.intro?.path || "";
    $("#showPanel").innerHTML = `<h3>Intro</h3>
      <label class="show-field"><span>An intro you already made (one of this project's videos)</span>
        <select id="showIntro"><option value="">No intro: go straight to the first scene</option>${videos.map((path) => `<option value="${escape(path)}" ${path === chosen ? "selected" : ""}>${escape(baseName(path))}</option>`).join("")}</select></label>
      <p class="show-hint">It plays after the title card, with its own sound, framed to the show's size (never cropped).</p>`;
    $("#showIntro").onchange = async (event) => {
      try {
        const result = await api("/show/scenes/identity", { method: "PATCH", body: JSON.stringify({ intro: event.target.value }) });
        review.intro = result.intro;
        renderIntro();
      } catch (error) { showToast(error.message, true); }
    };
    showVideo(chosen ? `/api/v1/show/source?path=${encodeURIComponent(chosen)}` : "", chosen ? "" : "No intro.");
  }

  function sceneEffects(scene) {
    return review.proposals.filter((item) => scene.effects.includes(item.id));
  }

  function renderScene() {
    const scene = currentScene();
    const effects = sceneEffects(scene);
    const preview = scene.preview || { status: "none" };
    $("#showPanel").innerHTML = `<h3>${escape(scene.name)} <small>${clock(scene.start)}–${clock(scene.end)}</small></h3>
      <p class="show-proposal">${escape(scene.proposal)}</p>
      <details class="show-said"><summary>What you say</summary><p>${escape(scene.text)}</p></details>
      <div class="show-decide">
        <button type="button" class="primary icon-button small" data-scene-decision="accepted" data-then-next>Accept and next →</button>
        <button type="button" class="icon-button small" data-scene-decision="accepted">Accept</button>
        <button type="button" class="icon-button small" data-scene-modify>Modify</button>
        <button type="button" class="icon-button small" data-scene-decision="plain">Leave without effect</button>
        <span class="show-decision show-${scene.decision}">${DECISION_LABELS[scene.decision]}</span>
      </div>
      <div id="showSceneEffects" class="show-review-list" ${open.has(scene.id) ? "" : "hidden"}>${effects.map(card).join("") || "<p class=\"show-hint\">No effect in this scene.</p>"}
        <form id="showSceneAdd" class="show-review-add"><strong>Add</strong>
          <select name="kind"><option value="drawing">A drawing</option><option value="steam">Rainbow steam over a mug</option><option value="character">A character that speaks</option><option value="cutaway">A close-up</option><option value="math">Formulas around you</option><option value="word">A shouted word</option></select>
          <label>at <input name="at" type="number" step="0.1" min="${scene.start}" max="${scene.end}" value="${scene.start.toFixed(1)}"> s</label>
          <label>of <input name="subject" type="text" maxlength="80" placeholder="a guitar"></label>
          <select name="style">${Object.entries(DRAWING_STYLES).map(([key, label]) => `<option value="${key}" ${key === "crayon" ? "selected" : ""}>${label}</option>`).join("")}</select>
          <select name="source">${[review.source, ...(review.resources || []).map((item) => item.path)].map((path) => `<option value="${escape(path)}">${escape(baseName(path))}</option>`).join("")}</select>
          <label>from <input name="source_time" type="number" min="0" step="0.1" value="0"> s of it</label>
          <button class="icon-button small" type="submit">Add</button></form>
        <h4>Pictures from the library</h4>
        <div class="show-library">${library.slice(0, 60).map((item, index) => `<button type="button" data-library="${index}" title="${escape(item.name)}"><img src="${item.url}" alt="${escape(item.name)}" loading="lazy"></button>`).join("") || "<p class=\"show-hint\">The library is empty: add images in Overlay &amp; Captions, or keep painted drawings.</p>"}</div>
        <p class="show-hint">Drag a picture on the video to move it, its corner to resize it; click it for its tools.</p>
        <button type="button" class="icon-button small" data-studio="open">New character from a photo</button>
      </div>`;
    for (const id of open) if (scene.effects.includes(id)) mountEditor(id);
    bindAddForm(scene);
    drawOverlays(scene);
    if (preview.status === "ready") showVideo(preview.url);
    else {
      showVideo("", preview.status === "failed" ? `The preview failed: ${preview.error}` : "Preparing this scene's preview…");
      poll = setTimeout(refreshBoard, 3000);
    }
  }

  async function refreshBoard() {
    const scene = currentScene();
    if (!scene) return;
    const fresh = await api(`/show/scenes?current=${encodeURIComponent(scene.id)}`).catch(() => null);
    if (!fresh) return;
    review = fresh;
    const updated = currentScene();
    if (updated?.preview?.status === "ready") showVideo(updated.preview.url);
    else if (updated?.preview?.status === "failed") showVideo("", `The preview failed: ${updated.preview.error}`);
    else poll = setTimeout(refreshBoard, 3000);
    $("#showSteps").querySelectorAll("[data-show-step]").forEach((button) => button.classList.toggle("active", Number(button.dataset.showStep) === step));
  }

  async function decideScene(decision, thenNext = false) {
    const scene = currentScene();
    const updated = await api(`/show/scenes/${encodeURIComponent(scene.id)}/decision`, { method: "POST", body: JSON.stringify({ decision }) });
    Object.assign(scene, updated, { preview: { status: "none" } });
    if (thenNext) step = Math.min(stepNames().length - 1, step + 1);
    const next = currentScene();
    review = await api(`/show/scenes?current=${encodeURIComponent((next || scene).id)}`);
    render();
  }

  // ---- The whole show, one step after another --------------------------------------

  let watching = null;

  function stopWatching() {
    if (!watching) return;
    clearTimeout(watching.timer);
    watching = null;
    $("#showStageVideo").pause();
  }

  function watchAll() {
    stopWatching();
    const identity = review.identity;
    const parts = [];
    if (identity.title || identity.logo) parts.push({ kind: "card" });
    if (review.intro?.path) parts.push({ kind: "video", label: "Intro", url: `/api/v1/show/source?path=${encodeURIComponent(review.intro.path)}` });
    scenes().forEach((scene) => parts.push({ kind: "scene", scene }));
    watching = { parts, index: -1, timer: null };
    playNext();
  }

  function playNext() {
    if (!watching) return;
    const index = ++watching.index;
    const part = watching.parts[index];
    if (!part) { stopWatching(); showVideo("", "End of the show. Render it when it looks right."); return; }
    const video = $("#showStageVideo");
    const where = `${index + 1} of ${watching.parts.length}`;
    if (part.kind === "card") {
      drawCard();
      $("#showStageStatus").textContent = `Title card · ${where}`;
      watching.timer = setTimeout(playNext, (review.identity.seconds || 2) * 1000);
      return;
    }
    let url = part.url, label = part.label;
    if (part.kind === "scene") {
      const scene = part.scene;
      label = `${scene.name} · ${DECISION_LABELS[scene.decision]}${scene.decision === "pending" ? " (shown with its proposals; rendered without them until you decide)" : ""}`;
      url = scene.preview?.status === "ready" ? scene.preview.url : "";
      if (!url) {
        showVideo("", `${scene.name}: its preview is still being prepared, skipped · ${where}`);
        watching.timer = setTimeout(playNext, 1500);
        return;
      }
    }
    showVideo(url, `${label} · ${where}`);
    video.play().catch(() => { $("#showStageStatus").textContent = `${label}: press play to continue.`; });
  }

  $("#showStageVideo").addEventListener("ended", () => { if (watching) playNext(); });

  async function markModified() {
    const scene = currentScene();
    if (scene && scene.decision !== "modified") {
      await api(`/show/scenes/${encodeURIComponent(scene.id)}/decision`, { method: "POST", body: JSON.stringify({ decision: "modified" }) }).catch(() => {});
      scene.decision = "modified";
      $("#showPanel .show-decide .show-decision").textContent = DECISION_LABELS.modified;
    }
  }

  function bindAddForm(scene) {
    const form = $("#showSceneAdd");
    if (!form) return;
    const sync = () => {
      const needsSource = ["character", "cutaway"].includes(form.elements.kind.value);
      form.elements.source.hidden = !needsSource;
      form.elements.source_time.parentElement.hidden = !needsSource;
      const drawing = form.elements.kind.value === "drawing";
      form.elements.subject.parentElement.hidden = !drawing;
      form.elements.style.hidden = !drawing;
    };
    form.elements.kind.onchange = sync;
    sync();
    form.onsubmit = async (event) => {
      event.preventDefault();
      const kind = form.elements.kind.value;
      const body = { kind, at: Number(form.elements.at.value) || scene.start };
      if (kind === "character") Object.assign(body, { source: form.elements.source.value, still_time: Number(form.elements.source_time.value) || 0 });
      if (kind === "cutaway") Object.assign(body, { source: form.elements.source.value, source_start: Number(form.elements.source_time.value) || 0 });
      if (kind === "drawing") Object.assign(body, { subject: form.elements.subject.value || "a guitar", style: form.elements.style.value });
      try {
        const item = await api("/show/review/add", { method: "POST", body: JSON.stringify(body) });
        review.proposals.push(item);
        scene.effects.push(item.id);
        scene.decision = "modified";
        open.add(scene.id); open.add(item.id);
        renderScene();
      } catch (error) { showToast(error.message, true); }
    };
  }

  // Pictures placed on the scene: drag to move, corner to resize, right-click menu.
  function drawOverlays(scene) {
    const layer = $("#showStageLayer");
    layer.innerHTML = scene.overlays.map((overlay, index) => {
      const item = library.find((entry) => entry.path === overlay.asset);
      return `<div class="show-overlay ${selectedOverlay === index ? "selected" : ""}" data-overlay="${index}" style="left:${overlay.x}%;top:${overlay.y}%;width:${overlay.width}%">
        <img src="${item ? item.url : ""}" alt="" draggable="false"><span class="show-resize" data-resize="${index}"></span></div>`;
    }).join("");
  }

  async function saveOverlays(scene) {
    try {
      const updated = await api(`/show/scenes/${encodeURIComponent(scene.id)}/overlays`, { method: "POST", body: JSON.stringify({ overlays: scene.overlays }) });
      scene.overlays = updated.overlays;
      scene.decision = updated.decision;
      scene.preview = { status: "none" };
    } catch (error) { showToast(error.message, true); }
  }

  let dragging = null;
  document.addEventListener("pointerdown", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-overlay], [data-resize]") : null;
    const scene = review && currentScene();
    if (!target || !scene) return;
    event.preventDefault();
    const index = Number(target.dataset.overlay ?? target.dataset.resize);
    const box = $("#showStageLayer").getBoundingClientRect();
    selectedOverlay = index;
    dragging = { index, resize: target.hasAttribute("data-resize"), box, x: event.clientX, y: event.clientY, start: { ...scene.overlays[index] } };
  });
  document.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const scene = currentScene();
    const overlay = scene.overlays[dragging.index];
    const dx = (event.clientX - dragging.x) / dragging.box.width * 100, dy = (event.clientY - dragging.y) / dragging.box.height * 100;
    if (dragging.resize) overlay.width = Math.max(3, Math.min(100, dragging.start.width + dx));
    else { overlay.x = dragging.start.x + dx; overlay.y = dragging.start.y + dy; }
    drawOverlays(scene);
  });
  document.addEventListener("pointerup", (event) => {
    if (!dragging) return;
    const moved = Math.hypot(event.clientX - dragging.x, event.clientY - dragging.y) > 3;
    const index = dragging.index;
    dragging = null;
    if (moved) saveOverlays(currentScene());
    else openTools(index, event.clientX, event.clientY);     // a plain click shows the element's tools beside it
  });
  const ANIMATIONS = { none: "Still", up: "Move up", down: "Move down", jump: "Jump", grow: "Grow", grow_return: "Grow and back",
    pulse: "Grow and shrink", swing: "Swing", spin: "Spin", talk: "Talk", path: "Follow a path" };
  let drawingPath = null;

  // A talking character interrupts the host: how it comes in.
  function showEntrance(overlay, talkable) {
    $("#showEntranceField").hidden = !(talkable && overlay.action === "talk");
    $("#showEntrance").value = overlay.entrance || "cut";
  }

  function isCharacter(asset) { return /[\\/]Characters[\\/][^\\/]+[\\/]cutout\.png$/.test(String(asset || "")); }

  function openTools(index, x, y) {
    const scene = currentScene();
    const overlay = scene.overlays[index];
    selectedOverlay = index;
    const menu = $("#showContextMenu");
    const talkable = isCharacter(overlay.asset);
    $("#showActionButtons").innerHTML = Object.entries(ANIMATIONS)
      .filter(([key]) => key !== "talk" || talkable)
      .map(([key, label]) => `<button type="button" data-animate="${key}" class="${(overlay.action || "none") === key ? "active" : ""}">${label}</button>`).join("");
    $("#showIntensity").value = overlay.intensity ?? 1;
    showEntrance(overlay, talkable);
    $("#showOverlayAt").value = Number(overlay.at ?? scene.start).toFixed(1);
    $("#showOverlayDuration").value = Number(overlay.duration ?? 4).toFixed(1);
    menu.hidden = false;
    // Beside the element, but always whole on screen (an element low in the frame opened it off the bottom).
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8))}px`;
  }

  document.addEventListener("contextmenu", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-overlay]") : null;
    if (!target || !review) return;
    event.preventDefault();
    openTools(Number(target.dataset.overlay), event.clientX, event.clientY);
  });

  document.addEventListener("change", (event) => {
    if (!(event.target instanceof Element) || !event.target.closest("#showContextMenu")) return;
    const scene = currentScene();
    const overlay = scene && selectedOverlay != null ? scene.overlays[selectedOverlay] : null;
    if (!overlay) return;
    if (event.target.id === "showIntensity") overlay.intensity = Number(event.target.value);
    if (event.target.id === "showEntrance") overlay.entrance = event.target.value;
    if (event.target.id === "showOverlayAt") overlay.at = Math.max(scene.start, Math.min(scene.end - 0.5, Number(event.target.value)));
    if (event.target.id === "showOverlayDuration") overlay.duration = Math.max(0.5, Number(event.target.value));
    saveOverlays(scene);
  });

  // A path: click points on the video, Enter (or double-click) to finish.
  document.addEventListener("click", (event) => {
    if (!drawingPath || !(event.target instanceof Element) || !event.target.closest("#showStage")) return;
    const box = $("#showStageLayer").getBoundingClientRect();
    drawingPath.points.push([(event.clientX - box.left) / box.width * 100, (event.clientY - box.top) / box.height * 100]);
    $("#showStageStatus").textContent = `Path: ${drawingPath.points.length} point(s). Enter or double-click to finish, Escape to cancel.`;
  }, true);
  async function finishPath(cancelled = false) {
    if (!drawingPath) return;
    const { index, points } = drawingPath;
    drawingPath = null;
    const scene = currentScene();
    if (cancelled || points.length < 2) { $("#showStageStatus").textContent = cancelled ? "" : "A path needs two points."; return; }
    const overlay = scene.overlays[index];
    overlay.action = "path";
    overlay.path = points.map(([x, y], i) => [i / (points.length - 1), x, y]);
    overlay.hops = overlay.hops ?? 4;
    $("#showStageStatus").textContent = "Path saved.";
    await saveOverlays(scene);
  }
  document.addEventListener("dblclick", (event) => { if (drawingPath && event.target instanceof Element && event.target.closest("#showStage")) finishPath(); });
  document.addEventListener("keydown", (event) => {
    if (!drawingPath) return;
    if (event.key === "Enter") { event.preventDefault(); finishPath(); }
    if (event.key === "Escape") finishPath(true);
  });

  function renderFinal() {
    const identity = review.identity;
    const order = [identity.title || identity.logo ? `Title card${identity.title ? `: ${identity.title}` : ""}` : "No title card",
      review.intro ? `Intro: ${baseName(review.intro.path)}` : "No intro",
      ...scenes().map((scene) => `${scene.name} · ${DECISION_LABELS[scene.decision]}`)];
    const pending = scenes().filter((scene) => scene.decision === "pending").length;
    $("#showPanel").innerHTML = `<h3>Order of the show</h3><ol class="show-order">${order.map((line) => `<li>${escape(line)}</li>`).join("")}</ol>
      ${pending ? `<p class="show-needs">${pending} scene${pending === 1 ? "" : "s"} still to decide: they will be rendered without their proposals.</p>` : ""}
      <p><a class="icon-button small" href="/api/v1/show/scenes/decisions" download="show-storyboard.json">Download decisions (.json)</a></p>
      <p class="show-hint">The decisions are saved in the project. The JSON is a copy without any media.</p>
      <div class="show-actions"><button type="button" class="icon-button" data-watch-all>▶ Watch the whole show</button>
        ${pending ? `<button type="button" class="icon-button" data-first-pending>Go to the first scene to decide</button>` : ""}</div>
      <p class="show-hint">The whole show plays the title card, the intro and each scene's preview one after another.</p>
      <button type="button" id="showRenderNow" class="primary icon-button">Render the show</button>`;
    showVideo("", "The final render joins the title card, the intro and every scene with what you accepted.");
  }

  // ---- Effects (inside "Modify") ------------------------------------------------

  function card(item) {
    const needs = (item.needs || []).length ? `<p class="show-needs">Still needed: ${escape(item.needs.join(", "))}</p>` : "";
    const confidence = item.kind === "cutaway" ? `<span class="show-confidence" title="How clearly the picture matches what is said">match ${(item.confidence * 100).toFixed(1)}</span>` : "";
    return `<article class="show-card show-${item.status}" data-show-id="${item.id}">
      <header><time>${clock(item.at)}</time><span class="show-kind">${item.params.reaction ? "Reaction" : (KIND_LABELS[item.kind] || item.kind)}</span>${confidence}<span class="show-status">${STATUS_LABELS[item.status]}</span></header>
      <div class="show-card-body">
        <img src="${picture(item)}" alt="" loading="lazy">
        <div>
          <p class="show-reason">${escape(item.reason)}</p>${needs}
          <div class="show-actions">
            <button type="button" class="icon-button small" data-show-preview="${item.id}">Preview</button>
            <button type="button" class="icon-button small" data-show-adjust="${item.id}">${open.has(item.id) ? "Close" : "Adjust"}</button>
            <button type="button" class="icon-button small" data-show-status="approved" data-id="${item.id}" ${(item.needs || []).length ? "disabled" : ""}>Approve</button>
            <button type="button" class="icon-button small" data-show-status="discarded" data-id="${item.id}">Discard</button>
          </div>
        </div>
      </div>
      <video class="show-preview" controls playsinline hidden></video>
      <div class="show-editor" ${open.has(item.id) ? "" : "hidden"}></div>
    </article>`;
  }

  async function patch(id, changes) {
    const updated = await api(`/show/review/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(changes) });
    const index = review.proposals.findIndex((item) => item.id === id);
    if (index >= 0) review.proposals[index] = updated;
    return updated;
  }

  function refreshCard(id) {
    const item = review.proposals.find((entry) => entry.id === id);
    const old = $(`[data-show-id="${id}"]`);
    if (!item || !old) return;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = card(item);
    const fresh = wrapper.firstElementChild;
    const editor = old.querySelector(".show-editor");
    fresh.querySelector(".show-editor").replaceWith(editor);   // keep the open editor and its canvas
    const preview = old.querySelector(".show-preview");
    fresh.querySelector(".show-preview").replaceWith(preview);
    old.replaceWith(fresh);
  }

  let saveTimer = null;
  function saveSoon(id, changes) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => patch(id, changes).then(() => { refreshCard(id); markModified(); }).catch((error) => showToast(error.message, true)), 350);
  }

  // ---- Editors -----------------------------------------------------------

  function field(label, html) { return `<label class="show-field"><span>${label}</span>${html}</label>`; }
  function number(name, value, step = 0.1, min = 0) { return `<input type="number" data-field="${name}" value="${Number(value ?? 0)}" step="${step}" min="${min}">`; }
  function range(name, value, min, max, step) { return `<input type="range" data-field="${name}" value="${Number(value ?? 0)}" min="${min}" max="${max}" step="${step}">`; }

  function mountEditor(id) {
    const item = review.proposals.find((entry) => entry.id === id);
    const host = $(`[data-show-id="${id}"] .show-editor`);
    if (!item || !host) return;
    host.hidden = false;
    const p = item.params;
    let html = `<div class="show-fields">${field("Starts at (s)", number("at", item.at))}${item.kind === "character" ? "" : field("Lasts (s)", number("duration", item.duration))}</div>`;
    if (item.kind === "cutaway") {
      const alternatives = (p.alternatives || []).map((alt, index) => `<button type="button" class="show-alt" data-alt="${index}" title="Use this close-up instead"><img src="${alt.thumb ? media(alt.thumb) : ""}" alt=""></button>`).join("");
      html += `<div class="show-fields">${field("From second", number("source_start", p.source_start))}${field("Zoom", range("zoom", p.zoom ?? 1, 1, 2.5, 0.05))}</div>
        <p class="show-hint">Click the picture to centre the zoom.</p><canvas class="show-canvas" data-canvas="focus"></canvas>
        ${alternatives ? `<p class="show-hint">Other close-ups that may fit:</p><div class="show-alts">${alternatives}</div>` : ""}`;
    } else if (item.kind === "character") {
      const voices = (p.voices || []).map((voice, index) => `<option value="${index}" ${p.voice && p.voice.start === voice.start && p.voice.source === voice.source ? "selected" : ""}>${escape(voice.text.slice(0, 70))} (${clock(voice.start)}, ${voice.length.toFixed(1)} s)</option>`).join("");
      html += `<div class="show-fields">${isPhoto(p.source) ? field("Holds (s)", number("hold", p.hold ?? 2)) : field("Frozen frame at (s)", number("still_time", p.still_time))}
          ${field("Push-in (s)", number("zoom_sec", p.zoom_sec ?? 1.2))}${field("Zoom", range("zoom", p.zoom ?? 1.25, 1, 2.5, 0.05))}</div>
        <div class="show-fields">${field("Enters", `<select data-field="entrance">${Object.entries(ENTRANCES).map(([key, label]) => `<option value="${key}" ${(p.entrance || "zoom") === key ? "selected" : ""}>${label}</option>`).join("")}</select>`)}</div>
        <div class="show-fields">${field("Moves", `<select data-field="action">${Object.entries(ACTIONS).map(([key, label]) => `<option value="${key}" ${p.action === key ? "selected" : ""}>${label}</option>`).join("")}</select>`)}
          ${field("How much", range("amount", p.amount ?? 0.02, 0, 0.1, 0.005))}${field("Shift X", range("offset_x", (p.offset || [0, 0])[0], -0.15, 0.15, 0.005))}${field("Shift Y", range("offset_y", (p.offset || [0, 0])[1], -0.15, 0.15, 0.005))}</div>
        ${isPhoto(p.source) ? "<p class=\"show-hint\">A photo has no voice of its own.</p>" : field("Its voice", `<select data-field="voice"><option value="">— choose a recorded line —</option>${voices}</select>`)}
        <div class="show-tools">
          <select data-tool><option value="rect">Rectangle (drag)</option><option value="points">Points (advanced)</option></select>
          <button type="button" class="icon-button small" data-region="close">Close shape</button>
          <button type="button" class="icon-button small" data-region="undo">Undo</button>
          <button type="button" class="icon-button small" data-region="redo">Redo</button>
          <button type="button" class="icon-button small" data-region="clear">New selection</button>
        </div>
        <p class="show-hint">Select what moves (the mouth or jaw): drag a rectangle, or click points and close the shape (first point, Enter or the button; it keeps the outer outline). Its own pixels move and black shows behind. Shift-click sets the zoom centre. The dashed box is what the zoom shows. Cmd/Ctrl+Z undoes; copy, cut and paste work on the picture.</p>
        <canvas class="show-canvas" tabindex="0" data-canvas="region"></canvas>`;
    } else if (item.kind === "drawing" || item.kind === "word") {
      html += `<div class="show-fields">${item.kind === "word" ? field("Text", `<input type="text" data-field="text" value="${escape(p.text)}">`) : field("What is drawn", `<input type="text" data-field="subject" value="${escape(p.subject)}">`)}
        ${field("Position", `<select data-field="position">${["left", "top-left", "bottom-left", "right", "top-right", "bottom-right"].map((pos) => `<option ${p.position === pos ? "selected" : ""}>${pos}</option>`).join("")}</select>`)}
        ${field("Size", range("size", p.size ?? 0.25, 0.1, 0.45, 0.01))}</div>`;
      if (item.kind === "drawing" && p.subject) {
        html += `<div class="show-fields">${field("Style", `<select data-field="style">${Object.entries(DRAWING_STYLES).map(([key, label]) => `<option value="${key}" ${(p.style || "pencil") === key ? "selected" : ""}>${label}</option>`).join("")}</select>`)}</div>
          ${field("What the painter is asked (change it before it is painted)", `<textarea data-field="prompt" rows="3" maxlength="600"></textarea>`)}
          <p class="show-hint">Nothing is painted until you press Preview or render the show. Each wording is a new drawing; the earlier one stays in the library. Empty: the style's own words.</p>`;
      }
    } else if (item.kind === "warp") {
      html += field("Warp", `<select data-field="warp">${["stretch", "swirl", "wave"].map((name) => `<option ${p.warp === name ? "selected" : ""}>${name}</option>`).join("")}</select>`);
    } else if (item.kind === "steam") {
      html += `<p class="show-hint">Click where the steam rises (the top of the mug); drag a box around the mug so the steam follows it.</p><canvas class="show-canvas" data-canvas="steam"></canvas>
        ${field("Size", range("scale", p.scale ?? 1, 0.4, 2, 0.05))}`;
    }
    host.innerHTML = html;
    fillPrompt(item, host);
    host.addEventListener("input", (event) => onField(item.id, event));
    host.addEventListener("change", (event) => onField(item.id, event));
    const canvas = host.querySelector("canvas");
    if (canvas) editors.set(item.id, new PictureEditor(canvas, item));
    host.querySelectorAll("[data-alt]").forEach((button) => button.addEventListener("click", () => useAlternative(item.id, Number(button.dataset.alt))));
  }

  // The words the painter will get: the person's own, or the style's for this subject.
  async function fillPrompt(item, host) {
    const box = host.querySelector("textarea[data-field=prompt]");
    if (!box) return;
    if (item.params.prompt) { box.value = item.params.prompt; return; }
    const query = new URLSearchParams({ subject: item.params.subject || "", style: item.params.style || "pencil" });
    try { box.value = (await api(`/show/drawing-prompt?${query}`)).prompt; } catch (error) { box.placeholder = error.message; }
  }

  function onField(id, event) {
    const target = event.target;
    if (!target.dataset || !target.dataset.field) return;
    const item = review.proposals.find((entry) => entry.id === id);
    const name = target.dataset.field;
    let value = target.type === "number" || target.type === "range" ? Number(target.value) : target.value;
    const changes = {};
    if (name === "offset_x" || name === "offset_y") {
      const offset = [...(item.params.offset || [0, 0])];
      offset[name === "offset_x" ? 0 : 1] = value;
      changes.offset = offset;
    } else if (name === "voice") {
      changes.voice = value === "" ? null : item.params.voices[Number(value)];
    } else {
      changes[name] = value;
    }
    Object.assign(item.params, changes);
    if ("at" in changes) item.at = changes.at;
    if ((name === "subject" || name === "style") && !item.params.prompt) {
      const host = $(`[data-show-id="${id}"] .show-editor`);
      if (host) fillPrompt(item, host);
    }
    editors.get(id)?.draw();
    saveSoon(id, changes);
  }

  async function useAlternative(id, index) {
    const item = review.proposals.find((entry) => entry.id === id);
    const alt = item.params.alternatives[index];
    const resource = review.resources[alt.resource];
    const interval = resource.intervals[alt.interval];
    const length = interval.end - interval.start;
    const changes = { source: resource.path, source_start: Math.round((interval.start + Math.max(0, length - item.duration) / 2) * 100) / 100,
      thumb: interval.thumb, alternatives: item.params.alternatives.filter((_, i) => i !== index) };
    await patch(id, changes).catch((error) => showToast(error.message, true));
    open.add(id);
    renderScene();
  }

  // Picture editor: the still (or a frame) with the region, focus or steam on it.
  class PictureEditor {
    constructor(canvas, item) {
      this.canvas = canvas; this.item = item; this.image = new Image(); this.drag = null;
      this.points = []; this.closed = false; this.undo = []; this.redo = [];
      const region = item.params.region;
      if (region?.rect) { const [x, y, w, h] = region.rect; this.points = [{ x, y }, { x: x + w, y }, { x: x + w, y: y + h }, { x, y: y + h }]; this.closed = true; }
      else if (region?.polygon) { this.points = region.polygon.map(([x, y]) => ({ x, y })); this.closed = true; }
      this.image.onload = () => { this.canvas.width = 960; this.canvas.height = Math.round(960 * this.image.height / this.image.width) || 540; this.draw(); };
      this.image.src = picture(item);
      canvas.addEventListener("pointerdown", (event) => this.down(event));
      canvas.addEventListener("pointermove", (event) => this.move(event));
      canvas.addEventListener("pointerup", () => this.up());
      canvas.addEventListener("keydown", (event) => this.key(event));
      canvas.addEventListener("copy", (event) => this.copy(event, false));
      canvas.addEventListener("cut", (event) => this.copy(event, true));
      canvas.addEventListener("paste", (event) => this.paste(event));
      const tools = canvas.parentElement.querySelectorAll("[data-region]");
      tools.forEach((button) => button.addEventListener("click", () => this.command(button.dataset.region)));
    }
    get tool() { return this.canvas.parentElement.querySelector("[data-tool]")?.value || "rect"; }
    point(event) { const box = this.canvas.getBoundingClientRect(); return { x: Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)), y: Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)) }; }
    snapshot() { return JSON.stringify({ points: this.points, closed: this.closed }); }
    remember() { this.undo.push(this.snapshot()); this.redo.length = 0; }
    restore(value) { const state = JSON.parse(value); this.points = state.points; this.closed = state.closed; this.draw(); this.save(); }
    command(name) {
      if (name === "undo" && this.undo.length) { this.redo.push(this.snapshot()); this.restore(this.undo.pop()); }
      if (name === "redo" && this.redo.length) { this.undo.push(this.snapshot()); this.restore(this.redo.pop()); }
      if (name === "clear") { this.remember(); this.points = []; this.closed = false; this.draw(); this.save(); }
      if (name === "close") this.close();
      this.canvas.focus();
    }
    close() {
      const outline = hull(this.points);
      if (outline.length < 3) { showToast("Three points that enclose an area are needed", true); return; }
      this.remember(); this.points = outline; this.closed = true; this.draw(); this.save();
    }
    down(event) {
      this.canvas.focus();
      const p = this.point(event);
      const kind = this.canvas.dataset.canvas;
      if (kind === "focus" || (kind === "region" && event.shiftKey)) { this.setFocus(p); return; }
      if (kind === "steam") { this.drag = { start: p, steam: true }; this.canvas.setPointerCapture(event.pointerId); return; }
      if (this.tool === "rect") { this.remember(); this.drag = { start: p }; this.closed = false; this.points = []; this.canvas.setPointerCapture(event.pointerId); return; }
      if (this.closed) { showToast("Choose New selection to start another", true); return; }
      if (this.points.length >= 3) {
        const box = this.canvas.getBoundingClientRect();
        if (Math.hypot((p.x - this.points[0].x) * box.width, (p.y - this.points[0].y) * box.height) < 12) { this.close(); return; }
      }
      this.remember(); this.points.push(p); this.draw();
    }
    move(event) {
      if (!this.drag) return;
      const p = this.point(event), s = this.drag.start;
      const x = Math.min(p.x, s.x), y = Math.min(p.y, s.y), xx = Math.max(p.x, s.x), yy = Math.max(p.y, s.y);
      this.points = [{ x, y }, { x: xx, y }, { x: xx, y: yy }, { x, y: yy }];
      this.draw();
    }
    up() {
      if (!this.drag) return;
      const wasSteam = this.drag.steam, start = this.drag.start;
      this.drag = null;
      if (wasSteam) {
        const [a, , c] = this.points.length ? this.points : [start, start, start];
        const small = Math.abs(c.x - a.x) < 0.01 && Math.abs(c.y - a.y) < 0.01;
        const changes = small ? { point: [start.x, start.y] } : { anchor: [a.x, a.y, c.x - a.x, c.y - a.y], point: [(a.x + c.x) / 2, a.y] };
        Object.assign(this.item.params, changes); this.points = []; this.draw(); saveSoon(this.item.id, changes);
        return;
      }
      if (hull(this.points).length >= 3 && Math.abs(this.points[2].x - this.points[0].x) > 0.003) { this.closed = true; this.save(); }
      else if (this.undo.length) { this.restore(this.undo.pop()); }
      this.draw();
    }
    key(event) {
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === "z") { event.preventDefault(); this.command(event.shiftKey ? "redo" : "undo"); }
      else if (mod && event.key.toLowerCase() === "y") { event.preventDefault(); this.command("redo"); }
      else if (event.key === "Enter") { event.preventDefault(); this.close(); }
      else if (event.key === "Escape" && !this.closed) { this.remember(); this.points = []; this.draw(); }
    }
    copy(event, cut) {
      if (!this.points.length) return;
      event.preventDefault();
      event.clipboardData.setData("text/plain", JSON.stringify({ type: "show-selection-v1", polygon: this.points, closed: this.closed }));
      if (cut) { this.remember(); this.points = []; this.closed = false; this.draw(); this.save(); }
    }
    paste(event) {
      try {
        const data = JSON.parse(event.clipboardData.getData("text/plain"));
        if (data.type !== "show-selection-v1" || !Array.isArray(data.polygon) || !data.polygon.every((p) => p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1)) return;
        event.preventDefault(); this.remember(); this.points = data.polygon; this.closed = Boolean(data.closed) && data.polygon.length >= 3; this.draw(); this.save();
      } catch { /* not a selection */ }
    }
    setFocus(p) { this.item.params.focus = [p.x, p.y]; this.draw(); saveSoon(this.item.id, { focus: [p.x, p.y] }); }
    save() {
      if (this.canvas.dataset.canvas !== "region") return;
      const region = this.closed && this.points.length >= 3 ? { polygon: this.points.map((p) => [p.x, p.y]) } : null;
      this.item.params.region = region;
      saveSoon(this.item.id, { region });
    }
    draw() {
      const ctx = this.canvas.getContext("2d"), w = this.canvas.width, h = this.canvas.height;
      ctx.clearRect(0, 0, w, h);
      if (this.image.complete && this.image.naturalWidth) ctx.drawImage(this.image, 0, 0, w, h);
      const p = this.item.params;
      const zoom = Number(p.zoom || 1), focus = p.focus || [0.5, 0.5];
      if ((this.canvas.dataset.canvas === "region" || this.canvas.dataset.canvas === "focus") && zoom > 1) {
        const ww = 1 / zoom, x0 = Math.min(Math.max(focus[0] - ww / 2, 0), 1 - ww), y0 = Math.min(Math.max(focus[1] - ww / 2, 0), 1 - ww);
        ctx.save(); ctx.setLineDash([8, 6]); ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2; ctx.strokeRect(x0 * w, y0 * h, ww * w, ww * h); ctx.restore();
      }
      if (this.points.length) {
        ctx.save(); ctx.beginPath();
        this.points.forEach((q, i) => (i ? ctx.lineTo(q.x * w, q.y * h) : ctx.moveTo(q.x * w, q.y * h)));
        if (this.closed || this.drag) ctx.closePath();
        ctx.strokeStyle = "#f3d478"; ctx.lineWidth = 2; ctx.stroke();
        this.points.forEach((q) => { ctx.beginPath(); ctx.arc(q.x * w, q.y * h, 3.5, 0, Math.PI * 2); ctx.fillStyle = "#f3d478"; ctx.fill(); });
        ctx.restore();
      }
      if (this.canvas.dataset.canvas === "steam") {
        if (p.anchor) { ctx.save(); ctx.strokeStyle = "#7dd3fc"; ctx.lineWidth = 2; ctx.strokeRect(p.anchor[0] * w, p.anchor[1] * h, p.anchor[2] * w, p.anchor[3] * h); ctx.restore(); }
        if (p.point) { ctx.beginPath(); ctx.arc(p.point[0] * w, p.point[1] * h, 6, 0, Math.PI * 2); ctx.fillStyle = "#f472b6"; ctx.fill(); }
      }
    }
  }

  function hull(points) {
    const unique = [...new Map(points.map((p) => [`${p.x},${p.y}`, p])).values()].sort((a, b) => a.x - b.x || a.y - b.y);
    if (unique.length < 3) return unique;
    const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
    const lower = [], upper = [];
    for (const p of unique) { while (lower.length > 1 && cross(lower.at(-2), lower.at(-1), p) <= 0) lower.pop(); lower.push(p); }
    for (const p of [...unique].reverse()) { while (upper.length > 1 && cross(upper.at(-2), upper.at(-1), p) <= 0) upper.pop(); upper.push(p); }
    return lower.slice(0, -1).concat(upper.slice(0, -1));
  }

  // ---- Actions -----------------------------------------------------------------

  document.addEventListener("click", async (event) => {
    const menu = $("#showContextMenu");
    // The click that ends a plain press on an element is the one that opened its tools.
    if (menu && !menu.hidden && !(event.target instanceof Element && event.target.closest("#showContextMenu, [data-overlay]"))) menu.hidden = true;
    const target = event.target instanceof Element ? event.target.closest("[data-show-step], #showBack, #showNext, [data-scene-decision], [data-scene-modify], [data-library], [data-menu], [data-animate], #showRenderNow, [data-show-preview], [data-show-adjust], [data-show-status], [data-watch-all], [data-first-pending]") : null;
    if (!target || !review) return;
    try {
      if (target.dataset.showStep) { step = Number(target.dataset.showStep); render(); }
      else if (target.id === "showBack") { step = Math.max(0, step - 1); render(); }
      else if (target.id === "showNext") { step = Math.min(stepNames().length - 1, step + 1); render(); }
      else if (target.dataset.sceneDecision) await decideScene(target.dataset.sceneDecision, target.hasAttribute("data-then-next"));
      else if (target.hasAttribute("data-watch-all")) watchAll();
      else if (target.hasAttribute("data-first-pending")) {
        const first = scenes().findIndex((scene) => scene.decision === "pending");
        if (first >= 0) { step = first + 2; render(); }
      }
      else if (target.hasAttribute("data-scene-modify")) {
        const scene = currentScene();
        if (open.has(scene.id)) open.delete(scene.id); else open.add(scene.id);
        renderScene();
      } else if (target.dataset.library) {
        const scene = currentScene();
        scene.overlays.push({ asset: library[Number(target.dataset.library)].path, x: 8, y: 10, width: 22, at: scene.start, duration: Math.min(4, scene.end - scene.start), effect: "pop" });
        drawOverlays(scene);
        await saveOverlays(scene);
      } else if (target.dataset.animate) {
        const scene = currentScene();
        const overlay = scene.overlays[selectedOverlay];
        if (!overlay) return;
        if (target.dataset.animate === "path") {
          menu.hidden = true;
          drawingPath = { index: selectedOverlay, points: [] };
          $("#showStageStatus").textContent = "Click the points of the path on the video; Enter or double-click to finish.";
          return;
        }
        overlay.action = target.dataset.animate;
        delete overlay.path;
        menu.querySelectorAll("[data-animate]").forEach((button) => button.classList.toggle("active", button === target));
        showEntrance(overlay, isCharacter(overlay.asset));
        await saveOverlays(scene);
      } else if (target.dataset.menu) {
        const scene = currentScene();
        menu.hidden = true;
        if (selectedOverlay == null || !scene) return;
        const overlay = scene.overlays[selectedOverlay];
        if (target.dataset.menu === "remove") scene.overlays.splice(selectedOverlay, 1);
        if (target.dataset.menu === "bigger") overlay.width = Math.min(100, overlay.width * 1.2);
        if (target.dataset.menu === "smaller") overlay.width = Math.max(3, overlay.width / 1.2);
        selectedOverlay = null;
        drawOverlays(scene);
        await saveOverlays(scene);
      } else if (target.id === "showRenderNow") {
        target.disabled = true;
        const started = await api("/show/review/render", { method: "POST", body: "{}" });
        window.resumeAfterShowReview?.(started);
      } else if (target.dataset.showAdjust) {
        const id = target.dataset.showAdjust;
        if (open.has(id)) { open.delete(id); editors.delete(id); } else open.add(id);
        renderScene();
      } else if (target.dataset.showStatus) {
        await patch(target.dataset.id, { status: target.dataset.showStatus });
        refreshCard(target.dataset.id);
        await markModified();
      } else if (target.dataset.showPreview) {
        const id = target.dataset.showPreview;
        const video = $(`[data-show-id="${id}"] .show-preview`);
        target.disabled = true; target.textContent = "Rendering preview…";
        const made = await api(`/show/review/${encodeURIComponent(id)}/preview`, { method: "POST", body: "{}" });
        video.hidden = false; video.src = made.url; video.play().catch(() => {});
        target.textContent = "Preview";
      }
    } catch (error) {
      showToast(error.message, true);
      if (target.dataset.showPreview) target.textContent = "Preview";
    } finally {
      if (target.dataset.showPreview || target.id === "showRenderNow") target.disabled = false;
    }
  });

  document.addEventListener("keydown", (event) => {
    if (!review || $("#showReviewBox")?.hidden) return;
    if (event.target instanceof Element && event.target.closest("input, textarea, select, canvas, [contenteditable='true']")) return;
    if (event.key === "ArrowRight" && !event.metaKey && !event.ctrlKey) { step = Math.min(stepNames().length - 1, step + 1); render(); }
    if (event.key === "ArrowLeft" && !event.metaKey && !event.ctrlKey) { step = Math.max(0, step - 1); render(); }
  });

  async function refreshLibrary() {
    library = (await api("/show/library").catch(() => ({ items: [] }))).items || [];
    if (currentScene()) renderScene();
  }

  async function addToScene(asset) {
    const scene = currentScene();
    if (!scene) { showToast("Open a scene to place it", true); return; }
    scene.overlays.push({ asset, x: 62, y: 35, width: 12, at: scene.start, duration: Math.min(4, scene.end - scene.start), action: "none", intensity: 1 });
    await saveOverlays(scene);
    open.add(scene.id);
    renderScene();
  }

  window.ShowReview = {
    open: openReview, hull, refreshLibrary, addToScene,
    voices: () => (review?.resources || []).flatMap((resource) => (resource.speech || []).map((line) => ({ source: resource.path, start: line.start, length: Math.round((line.end - line.start) * 100) / 100, text: line.text }))),
    sources: () => [review?.source, ...(review?.resources || []).map((item) => item.path)].filter(Boolean),
    resources: () => (review?.resources || []).filter((item) => item.kind !== "photo").map((item) => item.path),
  };
})();
