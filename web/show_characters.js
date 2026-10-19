// Character studio: a thing cut out of a photo with its own pixels (automatic
// cut-out, then keep/erase brush), rigged once (mouth, pivot, voice) and kept
// in the library to reuse in any scene.
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const baseName = (path) => String(path || "").split(/[\\/]/).pop();
  let state = null;
  const photoGroups = new Map();
  function openStudio() {
    const dialog = $("#showCharacterStudio");
    dialog.hidden = false;
    if (!dialog.open) dialog.showModal();
  }
  function closeStudio() {
    const dialog = $("#showCharacterStudio");
    dialog.close();
    dialog.hidden = true;
  }
  document.addEventListener("DOMContentLoaded", () => {
    $("#showCharacterStudio").addEventListener("cancel", (event) => {
      event.preventDefault(); closeStudio();
    });
  });

  function show(step) {
    openStudio();
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
    const groups = videos.filter((video) => !only || video === only).map((video, groupIndex) => {
      const items = photos.map((item, index) => ({ item, index })).filter(({ item }) => (item.video || "") === video)
        .sort((a, b) => (a.item.second ?? 0) - (b.item.second ?? 0));
      return `<details class="show-library-group" data-photo-group="${escape(video)}" ${(only || (photoGroups.get(video) ?? groupIndex === 0)) ? "open" : ""}><summary>${escape(video ? baseName(video) : "Added photos")} · ${items.length}</summary>
        <div class="show-photo-grid">${items.map(({ item, index }) => `<button type="button" data-photo="${index}" title="${escape(item.name)}"><img src="${item.url}" alt="" loading="lazy"><span>${escape(item.name)}</span></button>`).join("")}</div></details>`;
    }).join("");
    $("#showStudioBody").innerHTML = `<p class="show-hint">Choose a clear, focused photo of the whole thing. Take photos of every steady moment of your videos, or one at an exact second.</p>
      <div class="show-actions"><button type="button" class="primary icon-button small" id="showPhotoLibrary">Photos of every steady moment</button>
        ${videos.length > 1 ? `<select id="showPhotoFilter"><option value="">All videos</option>${videos.map((video) => `<option value="${escape(video)}" ${video === only ? "selected" : ""}>${escape(video ? baseName(video) : "Added photos")}</option>`).join("")}</select>` : ""}</div>
      <p id="showStudioStatus" class="show-hint" role="status" aria-live="polite"></p>
      ${groups ? '<div class="show-actions"><button type="button" data-photo-toggle="open">Expand all</button><button type="button" data-photo-toggle="close">Collapse all</button></div>' : ""}
      ${groups || "<p>No photos yet.</p>"}
      <form id="showTakePhoto" class="show-review-add"><strong>Take a photo</strong>
        <select name="source">${sources.map((path) => `<option value="${escape(path)}">${escape(baseName(path))}</option>`).join("")}</select>
        <label>at <input name="second" type="number" min="0" step="any" value="0"> s</label>
        <label>name <input name="name" type="text" value="thing"></label><button class="icon-button small" type="submit">Take it</button></form>`;
    $("#showStudioBody").querySelectorAll("[data-photo-group]").forEach((group) => {
      group.addEventListener("toggle", () => photoGroups.set(group.dataset.photoGroup, group.open));
    });
    $("#showStudioBody").querySelectorAll("[data-photo-toggle]").forEach((button) => button.onclick = () => {
      $("#showStudioBody").querySelectorAll("[data-photo-group]").forEach((group) => { group.open = button.dataset.photoToggle === "open"; });
    });
    $("#showStudioBody").querySelectorAll("[data-photo]").forEach((button) => button.onclick = () => {
      state.photo = photos[Number(button.dataset.photo)];
      state.strokes = []; state.undo = []; state.box = null; state.cut = null; state.eyes = []; state.mouth = null; state.background = false;
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
    const owner=state, request=Symbol('cutout');
    owner.cutRequest=request; owner.cutPending=true; owner.cut=null;
    const controls=[...document.querySelectorAll('#showStudioBody [data-studio="gallery"],#showStudioBody [data-studio="next3"]')];controls.forEach(button=>button.disabled=true);
    const status=$("#showStudioStatus"), preview=$("#showStudioPreview");
    if(status)status.textContent="Cutting out… (the first time can take half a minute)";
    try {
      const result=await api("/show/characters/cutout",{method:"POST",body:JSON.stringify({photo:owner.photo.photo,box:owner.box,strokes:owner.strokes,background:owner.background,silhouette:owner.silhouette})});
      if(state!==owner || owner.cutRequest!==request)return;
      owner.cut=result;
      if(status?.isConnected)status.textContent=`Cut out with ${result.method==='macos-vision'?'macOS subject lifting':'a box (GrabCut)'}. Keep or erase the edge; original pixels stay unchanged.`;
      drawPhoto(); if(preview?.isConnected)preview.src=result.url;
    } catch(error){if(state===owner&&owner.cutRequest===request&&status?.isConnected)status.textContent=error.message;}
    finally{if(owner.cutRequest===request){owner.cutPending=false;controls.forEach(button=>{if(button.isConnected)button.disabled=!owner.cut;});}}
  }

  function renderCutout() {
    $("#showStudioBody").innerHTML = `<div class="show-studio-tools">
        <label><input type="radio" name="brush" value="keep" checked> Keep brush</label><label><input type="radio" name="brush" value="erase"> Erase brush</label>
        <label><input type="radio" name="brush" value="box"> Box (cut inside it)</label>
        <label>Size <input type="range" id="showBrushSize" min="0.005" max="0.08" step="0.005" value="0.02"></label>
        <label><input type="checkbox" id="showKeepBackground" ${state.background ? "checked" : ""}> Keep background instead</label>
        <label><input type="checkbox" id="showBlackSilhouette" ${state.silhouette ? "checked" : ""}> Black silhouette (still image)</label><button type="button" class="icon-button small" data-studio="undo">Undo</button>
        <button type="button" class="icon-button small" data-studio="auto">Automatic again</button></div>
      <p id="showStudioStatus" class="show-hint"></p>
      <div class="show-studio-split"><canvas id="showStudioPhoto" class="show-canvas" width="960" height="540"></canvas><div class="show-checker"><img id="showStudioPreview" alt="Cut-out"></div></div>
      <div class="show-actions"><button type="button" class="icon-button small" data-studio="back1">Another photo</button><button type="button" class="primary icon-button small" data-studio="gallery">Save to gallery &amp; place in scene</button><button type="button" class="icon-button small" data-studio="next3">Animate original parts…</button></div>`;
    $("#showBlackSilhouette").onchange = event => {state.silhouette=event.target.checked;cut();};
    $("#showKeepBackground").onchange = (event) => { state.background = event.target.checked; cut(); };
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
    const voices = [...window.ShowReview.voices(), ...(state.savedVoice ? [state.savedVoice] : [])];
    $("#showStudioBody").innerHTML = `<p class="show-hint">Mark original parts, without replacing them. Draw a tight rectangle around each eye with Eyes, or mark a mouth/jaw with Mouth. Save, then choose Look left and right or Talk in the element’s animation menu. Pivot sets the turning point only. Choose eye and mouth movement direction separately below.</p>
      <div class="show-studio-tools"><label><input type="radio" name="rigtool" value="mouth" checked> Mouth</label><label><input type="radio" name="rigtool" value="eyes"> Eyes / original part</label><label><input type="radio" name="rigtool" value="pivot"> Pivot</label><button type="button" class="icon-button small" data-studio="clear-eyes">Clear eye regions</button><button type="button" class="icon-button small" data-studio="test-eyes">Test glance</button></div>
      <div class="show-fields"><label>Eye direction<select id="showEyeDirection"><option value="horizontal">Left / right</option><option value="vertical">Up / down</option><option value="diagonal">Diagonal down</option><option value="diagonal-up">Diagonal up</option></select></label><label>Mouth direction<select id="showMouthDirection"><option value="down">Down</option><option value="up">Up</option><option value="left">Left</option><option value="right">Right</option><option value="diagonal">Diagonal down</option><option value="diagonal-up">Diagonal up</option></select></label></div><div class="show-checker show-rig"><canvas id="showRigCanvas" class="show-canvas"></canvas></div>
      <video id="showEyePreview" controls playsinline hidden style="max-width:100%;max-height:240px"></video><p id="showEyeStatus" role="status"></p><div class="show-fields"><label class="show-field"><span>Name</span><input type="text" id="showCharacterName" value="${escape(state.name || state.photo.name)}"></label>
        <label class="show-field"><span>Its voice (from your videos)</span><select id="showCharacterVoice"><option value="">No voice yet</option>${voices.map((voice, index) => `<option value="${index}">${escape(voice.text.slice(0, 60))} (${escape(baseName(voice.source))} ${voice.start.toFixed(1)} s)</option>`).join("")}</select></label></div>
      <div class="show-fields show-voice-trim"><label class="show-field"><span>Its voice starts at (s)</span><input type="number" step="any" min="0" id="showVoiceFrom" value="0"></label>
        <label class="show-field"><span>…and ends at (s)</span><input type="number" step="any" min="0" id="showVoiceTo" value=""></label>
        <button type="button" class="icon-button small" data-studio="trim-voice" title="Leave out the silence before and after the speech">Trim the silence</button>
        <button type="button" class="icon-button small primary" data-studio="talk-preview" title="Eyes, mouth and voice together, as the video will show them">▶ Preview with voice</button></div>
      <video id="showTalkPreview" controls playsinline hidden style="max-width:100%;max-height:260px"></video><p id="showTalkStatus" role="status"></p>
      <div data-voice-input data-voice-character></div>
      <div class="show-actions"><button type="button" class="icon-button small" data-studio="back2">Back to the cut-out</button><button type="button" class="primary icon-button small" data-studio="save">${state.placement?.proposalId?"Apply to this close-up":"Save in the library and place it"}</button></div>`;
    if(state.editing)$('[data-studio="back2"]').disabled=true;
    state.voices = voices;
    $("#showEyeDirection").value=state.eye_direction||"horizontal";$("#showMouthDirection").value=state.mouth_direction||"down";
    if(state.initialMode==='eyes')$('[name="rigtool"][value="eyes"]').checked=true;
    if(state.savedVoice)$("#showCharacterVoice").value=String(voices.length-1);
    voiceBounds();
    $("#showCharacterVoice").addEventListener("change", voiceBounds);
    const canvas = $("#showRigCanvas");
    const image = new Image();
    image.onload = () => {
      const k = Math.min(520 / image.width, 420 / image.height);
      canvas.width = Math.round(image.width * k); canvas.height = Math.round(image.height * k);
      state.rigImage = image; drawRig();
    };
    image.src = state.cut.url;
    let drag = null, rigTool = null;
    const point = (event) => { const box = canvas.getBoundingClientRect(); return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height]; };
    canvas.onpointerdown = (event) => {
      const tool = document.querySelector("input[name=rigtool]:checked").value;
      if (tool === "pivot") { state.pivot = point(event); drawRig(); return; }
      canvas.setPointerCapture(event.pointerId); drag = point(event); rigTool = tool;
    };
    canvas.onpointermove = (event) => {
      if (!drag) return;
      const [x, y] = point(event);
      const region = { rect: [Math.min(x, drag[0]), Math.min(y, drag[1]), Math.abs(x - drag[0]), Math.abs(y - drag[1])] };
      if (rigTool === "eyes") state.liveEye = region; else state.mouth = region;
      drawRig();
    };
    canvas.onpointerup = () => { if (rigTool === "eyes" && state.liveEye) { state.eyes = [...(state.eyes || []), state.liveEye].slice(-8); state.liveEye = null; } drag = null; drawRig(); };
  }

  function drawRig() {
    const canvas = $("#showRigCanvas");
    if (!canvas || !state.rigImage) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(state.rigImage, 0, 0, canvas.width, canvas.height);
    if (state.mouth) { const [x, y, w, h] = state.mouth.rect; ctx.strokeStyle = "#f3d478"; ctx.lineWidth = 2; ctx.strokeRect(x * canvas.width, y * canvas.height, w * canvas.width, h * canvas.height); }
    for (const region of [...(state.eyes || []), ...(state.liveEye ? [state.liveEye] : [])]) { const [x,y,w,h] = region.rect; ctx.strokeStyle = "#70d9ed"; ctx.lineWidth = 2; ctx.strokeRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height); }
    if (state.pivot) { ctx.beginPath(); ctx.arc(state.pivot[0] * canvas.width, state.pivot[1] * canvas.height, 6, 0, Math.PI * 2); ctx.fillStyle = "#f472b6"; ctx.fill(); }
  }

  // The chosen voice, trimmed to its "starts at / ends at" (seconds inside that recording).
  function voiceBounds() {
    const index = $("#showCharacterVoice")?.value, voice = index === "" || index == null ? null : state.voices[Number(index)];
    $("#showVoiceFrom").value = "0";
    $("#showVoiceTo").value = voice ? voice.length.toFixed(2) : "";
  }
  function chosenVoice() {
    const index = $("#showCharacterVoice").value;
    if (index === "") return null;
    const voice = state.voices[Number(index)];
    const from = Math.max(0, Math.min(voice.length - 0.2, Number($("#showVoiceFrom").value) || 0));
    const to = Math.max(from + 0.2, Math.min(voice.length, Number($("#showVoiceTo").value) || voice.length));
    return { ...voice, start: voice.start + from, length: to - from };
  }

  async function save() {
    const voice = chosenVoice();
    const saved = await api("/show/characters", { method: state.editing ? "PATCH" : "POST", body: JSON.stringify({ folder:state.folder,
      photo: state.photo.photo, mask: state.cut.mask, method: state.cut.method, name: $("#showCharacterName").value,
      eye_direction:($("#showEyeDirection")?.value||state.eye_direction||"horizontal"),mouth_direction:($("#showMouthDirection")?.value||state.mouth_direction||"down"),eyes: state.eyes || [], mouth: state.mouth || null, pivot: state.pivot || [0.5, 1.0], voice: voice ? [voice.source, voice.start, voice.length] : null }) });
    closeStudio();
    await window.ShowReview.refreshLibrary();
    const tool=document.querySelector('[name=rigtool]:checked')?.value;
    // A recorded or chosen voice is heard: the character talks (its jaw moves too when a mouth was marked).
    const placement=state.placement?{...state.placement,action:voice||(tool==='mouth'&&state.mouth)?'talk':tool==='eyes'&&state.eyes?.length?'look':state.placement.action||'none'}:null;
    if(placement?.proposalId)await window.ShowReview.partsSaved(saved.cutout,placement);
    else if(placement?.replaceId)await window.ShowReview.rigSaved(saved.cutout,placement);
    else if (!state.editing) await window.ShowReview.addToScene(saved.cutout,placement);
    showToast(placement?.proposalId ? "Original parts updated in the same close-up." : state.editing ? `${saved.name} updated. Render its preview to check the changes.` : `${saved.name} is in the library and in this scene`);
    if(placement?.fromProposal)await window.ShowReview.characterTakesPlace(placement.fromProposal,saved.name);
  }

  window.ShowPhotoStudio = {rigFrame: async(source,second,settings={})=>{
    const photo=await api('/show/photos',{method:'POST',body:JSON.stringify({source,second,name:'Original parts'})});
    return ShowPhotoStudio.rigPicture(photo.photo,settings);
  },rigPicture: async(asset,settings={})=>{
    if(/[\\/]Characters[\\/][^\\/]+[\\/]cutout\.png$/.test(asset)){await ShowPhotoStudio.edit(asset);state.placement=settings.placement;state.initialMode=settings.mode;renderRig();return;}
    const projectId=settings.placement?.projectId||ShowReview.projectId();
    const result=await api('/show/characters/prepare-layer',{method:'POST',body:JSON.stringify({project_id:projectId,asset})});
    state={step:3,photo:result.photo,cut:result.cut,eyes:[],mouth:null,pivot:[.5,1],strokes:[],undo:[],initialMode:settings.mode,placement:settings.placement};
    show(3);
  },openPhoto: async (photo,settings={})=>{
    const owner={step:2,strokes:[],undo:[],eyes:[],mouth:null,background:!!settings.background,silhouette:!!settings.silhouette,placement:settings.placement,photo:{...photo,url:'/api/v1/show/photo?path='+encodeURIComponent(photo.photo)}};
    state=owner;openStudio();
    if(settings.head){const result=await api('/show/references/head-box',{method:'POST',body:JSON.stringify({photo:photo.photo})});if(state!==owner)return;owner.box=result.box||[.25,.05,.5,.45];}
    show(2);
    if(settings.head)showToast('Check the head outline. Use Box or the keep/erase brushes to correct it before saving.');
  },openAt: async (source,second,silhouette=false,placement=null) => {
    const owner={step:1,strokes:[],undo:[],eyes:[],mouth:null,background:false,silhouette,placement};state=owner;
    openStudio();$('#showStudioBody').innerHTML='<p role="status">Taking a photo at your selected words…</p>';
    try {const photo=await api('/show/photos',{method:'POST',body:JSON.stringify({source,second,name:silhouette?'Black silhouette':'Selected words cutout'})});
      if(state!==owner)return;state.photo={...photo,url:'/api/v1/show/photo?path='+encodeURIComponent(photo.photo)};show(2);
    } catch(error){if(state===owner)$('#showStudioBody').textContent=error.message;}
  },edit: async asset => {
    const result=await api('/show/characters/edit?asset='+encodeURIComponent(asset));
    const r=result.record;
    state={step:3,editing:true,folder:r.folder,name:r.name,photo:{...result.photo,url:'/api/v1/show/photo?path='+encodeURIComponent(result.photo.photo)},cut:result.cut,
      mouth:r.mouth,eyes:r.eyes||[],eye_direction:r.eye_direction,mouth_direction:r.mouth_direction,pivot:r.pivot,strokes:[],undo:[],savedVoice:r.voice?{source:r.voice[0],start:r.voice[1],length:r.voice[2],text:'Saved voice'}:null};
    show(3);
  }};

  document.addEventListener("show-voice-ready", event => {
    if (!event.target.matches('[data-voice-character]') || !state) return;
    state.voices.push(event.detail);
    const select = $("#showCharacterVoice"), option = document.createElement('option');
    option.value = state.voices.length - 1; option.textContent = event.detail.text;
    select.append(option); select.value = option.value;
    voiceBounds();
  });

  document.addEventListener("click", async (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-studio]") : null;
    if (!target) return;
    try {
      const action = target.dataset.studio;
      if (action === "open") { state = { step: 1, strokes: [], undo: [] }; show(1); }
      else if (action === "trim-voice") {
        const index = $("#showCharacterVoice").value;
        if (index === "") { $("#showTalkStatus").textContent = "Choose or record its voice first."; return; }
        const voice = state.voices[Number(index)];
        const trimmed = await api("/show/voices/trim", { method: "POST", body: JSON.stringify({ source: voice.source, start: voice.start, length: voice.length }) });
        $("#showVoiceFrom").value = (trimmed.start - voice.start).toFixed(2);
        $("#showVoiceTo").value = (trimmed.start - voice.start + trimmed.length).toFixed(2);
        $("#showTalkStatus").textContent = `Speech from ${(trimmed.start - voice.start).toFixed(2)} s to ${(trimmed.start - voice.start + trimmed.length).toFixed(2)} s; the silence around it is left out.`;
      }
      else if (action === "talk-preview") {
        const voice = chosenVoice();
        if (!voice) { $("#showTalkStatus").textContent = "Choose or record its voice first."; return; }
        target.disabled = true; $("#showTalkStatus").textContent = "Preparing the preview with its voice…";
        try {
          const result = await api("/show/characters/talk-preview", { method: "POST", body: JSON.stringify({ photo: state.photo.photo, mask: state.cut.mask,
            eye_direction:($("#showEyeDirection")?.value||state.eye_direction||"horizontal"),mouth_direction:($("#showMouthDirection")?.value||state.mouth_direction||"down"),eyes: state.eyes || [], mouth: state.mouth || null, voice: [voice.source, voice.start, voice.length] }) });
          const video = $("#showTalkPreview"); video.src = result.url; video.hidden = false; video.play().catch(() => {});
          $("#showTalkStatus").textContent = `${state.mouth ? "Its mouth moves with the voice" : "Mark its mouth to make it move with the voice"}${state.eyes?.length ? "; its eyes glance" : ""}. This is how the video will show it.`;
        } finally { target.disabled = false; }
      }
      else if (action === "close") closeStudio();
      else if (action === "gallery") {
        if (!state.cut || state.cutPending) return;
        target.disabled = true;
        const saved = await api("/show/characters", {method:"POST",body:JSON.stringify({photo:state.photo.photo,mask:state.cut.mask,gallery_only:true,silhouette:state.silhouette,name:state.photo.name})});
        await window.ShowReview.refreshLibrary();
        await window.ShowReview.addToScene(saved.cutout,state.placement);
        $("#showStudioStatus").textContent = `${saved.name} saved in this project’s gallery. You can reuse it in any scene.`;
        target.disabled = false;
      }
      else if (action === "clear-eyes") { state.eyes = []; state.liveEye = null; drawRig(); }
      else if (action === "test-eyes") {
        if (!state.eyes?.length) { $("#showEyeStatus").textContent = "Mark an eye region first."; return; }
        target.disabled = true; $("#showEyeStatus").textContent = "Preparing original-pixel glance…";
        try {
          const result = await api("/show/characters/eyes-preview",{method:"POST",body:JSON.stringify({photo:state.photo.photo,mask:state.cut.mask,eyes:state.eyes,eye_direction:$("#showEyeDirection").value})});
          const video = $("#showEyePreview"); video.src = result.url; video.hidden = false;
          $("#showEyeStatus").textContent = "Original pixels move; only the small holes behind them are filled from their surroundings.";
        } finally { target.disabled = false; }
      }
      else if (action === "back1") show(1);
      else if (action === "back2") show(2);
      else if (action === "next3") { if (!state.cut || state.cutPending) return; show(3); }
      else if (action === "undo" && state.undo.length) { const last = JSON.parse(state.undo.pop()); state.strokes = last.strokes; state.box = last.box; cut(); }
      else if (action === "auto") { state.undo.push(JSON.stringify({ strokes: state.strokes, box: state.box })); state.strokes = []; state.box = null; cut(); }
      else if (action === "save") { target.disabled = true; await save(); }
    } catch (error) { showToast(error.message, true); }
    finally { if (["save", "gallery", "test-eyes"].includes(target.dataset.studio)) target.disabled = false; }
  });
})();
