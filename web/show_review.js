// Show storyboard: identity -> intro -> scenes -> review and render.
// Each scene explains what it proposes; watch it (a real preview, prepared in
// the background), then accept it, modify it or leave it without effect. Inside
// "Modify", every effect has its own editor: regions are kept in the
// coordinates of their own picture, outlines exist only in the editor, and the
// pixels that move are the originals. Nothing is rendered until the last step.
(() => {
  const KIND_LABELS = { sticker:"Sticker", cutaway: "Close-up", character: "Character speaks", drawing: "Drawing", word: "Word", warp: "Warp", steam: "Texture · rainbow smoke", texture: "Texture", math: "Formulas around you", clip: "Video clip", jingle:"Audio", clay:"Clay story", background: "Moving video layers" };
  const TEXTURES = {rainbow:"Rainbow smoke",smoke:"Smoke",fog:"Mist",water:"Water",electricity:"Electricity",fire:"Fire",snow:"Snow",rain:"Rain",sparks:"Sparks",leaves:"Leaves"};
  const isTexture = item => !!item && (["steam","texture"].includes(item.kind) || !!item.params?.texture_footage);
  const textureField = p => field("Texture", `<select data-field="texture">${Object.entries(TEXTURES).map(([key,name])=>`<option value="${key}" ${(p.texture||"rainbow")===key?"selected":""}>${name}</option>`).join("")}</select>`);
  const STATUS_LABELS = { proposed: "Proposed", approved: "Approved", discarded: "Discarded" };
  const DECISION_LABELS = { pending: "To decide", accepted: "Accepted", modified: "Needs review", plain: "Without effect" };
  const ACTIONS = { mouth: "Mouth (with the voice)", nod: "Nod", side: "Sideways", turn: "Turn" };
  const DRAWING_STYLES = { pencil: "Pencil", crayon: "Wax crayons", marker: "Felt-tip marker" };
  // Typefaces for words (core.gag_stickers.WORD_FONTS): one plain, the rest wild; shown in themselves.
  const WORD_FONTS = { plain: ["Plain (Arial)", "Arial, Helvetica, sans-serif"], marker: ["Marker", "'Marker Felt', 'Segoe Print', cursive"],
    chalk: ["Chalk", "Chalkduster, 'Ink Free', cursive"], note: ["Notebook", "Noteworthy, 'Segoe Print', cursive"], board: ["Chalkboard", "'Chalkboard SE', 'Comic Sans MS', cursive"],
    bradley: ["Bradley hand", "'Bradley Hand', cursive"], signpainter: ["Sign painter", "SignPainter, cursive"], brush: ["Brush script", "'Brush Script MT', cursive"],
    comic: ["Comic", "'Comic Sans MS', cursive"], impact: ["Impact poster", "Impact, sans-serif"], papyrus: ["Papyrus", "Papyrus, fantasy"], phosphate: ["Phosphate", "Phosphate, fantasy"],
    luminari: ["Luminari", "Luminari, fantasy"], trattatello: ["Trattatello", "Trattatello, fantasy"], herculanum: ["Herculanum", "Herculanum, fantasy"], party: ["Party", "'Party LET', fantasy"],
    zapfino: ["Zapfino", "Zapfino, cursive"], typewriter: ["Typewriter", "'American Typewriter', 'Courier New', monospace"], rockwell: ["Rockwell", "Rockwell, serif"], chancery: ["Chancery", "'Apple Chancery', cursive"] };
  const WORD_COLOURS = [["#fff4d6", "Cream"], ["#ffffff", "White"], ["#ffd400", "Yellow"], ["#ff4fa3", "Pink"], ["#ff3b30", "Red"], ["#7dd3fc", "Sky"], ["#111111", "Black"]];
  const wordStyle = (p) => `font-family:${(WORD_FONTS[p.font || "note"] || WORD_FONTS.note)[1]};font-weight:${p.bold ? 800 : 400};font-style:${p.italic ? "italic" : "normal"};color:${/^#/.test(p.colour || "") ? p.colour : "#fff4d6"}`;
  const ENTRANCES = { cut: "Straight cut: it talks at once", zoom: "Push in, then it talks", bounce: "The photo bounces in over the frozen host" };
  let review = null;          // the storyboard: identity, intro, scenes, proposals, resources
  let library = [];
  let showRetired=false,retiredCount=0;
  let step = 0;
  let poll = null;
  let selectedOverlay = null;
  let previewOrigin = 0;
  let timelineDialog = null, stageHome = null;
  let selectedEffect = null, placingWords = false, firstWord = null, pickedWords = null;
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
    if (item.kind === "background" && p.picture && !isPhoto(p.picture)) return "/video-layer-thumb.svg";
    if (item.kind === "clip") return still(p.clip, Math.min(0.3, (p.clip_length || 1) / 2));
    if (["drawing", "word", "background"].includes(item.kind) && p.picture) return `/api/v1/show/library/file?path=${encodeURIComponent(p.picture)}`;
    return `/api/v1/show/frame?t=${encodeURIComponent(item.at)}`;
  }

  async function openReview() {
    review = await api("/show/scenes");
    await loadLibrary();
    for (const scene of review.scenes || []) {
      if (["queued", "rendering", "ready"].includes(scene.preview?.status)) wantEffects.add(scene.id);
    }
    const resume=review.scenes.findIndex(scene=>scene.id===review.resume_scene);
    step = resume>=0?resume+2:0;
    render();
    // Every scene's preview with effects is prepared in the background, from the first on;
    // the scene being edited always goes first.
    api("/show/scenes/prefetch", { method: "POST", body: JSON.stringify({ from: review.resume_scene || review.scenes?.[0]?.id }) }).catch(() => {});
  }

  async function openWorkspace({sceneId=null}={}) {
    const state = await api("/wizard/status");
    if (["running", "cancelling"].includes(state.status)) throw new Error("Wait for the current job before editing the storyboard.");
    await openReview();
    if (sceneId) {
      const index=review.scenes.findIndex(scene=>scene.id===sceneId);
      if (index>=0) { step=index+2; render(); }
    }
    window.enterShowWorkspace();
  }
  document.getElementById("showWorkspaceOpen")?.addEventListener("click",()=>openWorkspace().catch(error=>showToast(error.message,true)));

  // ---- Storyboard -------------------------------------------------------------

  const dirtyScenes = new Set();
  const viewedScenes = new Set();
  const wantQuick = new Set();
  const wantSaved = new Set();        // the person chose the saved render for this scene
  const useSaved = scene => !!review.saved_render && !wantEffects.has(scene.id) && !wantQuick.has(scene.id);
  const wantEffects = new Set();      // scenes whose preview with effects was asked for
  const scenes = () => review.scenes || [];
  const stepNames = () => ["Identity", "Intro", ...scenes().map((scene) => scene.name), "Review & render"];
  const currentScene = () => (step >= 2 && step < 2 + scenes().length ? scenes()[step - 2] : null);

  // Wide windows: the storyboard fits the window and its two sides scroll on their own,
  // so the wheel moves what is under the pointer (the video side or the scene panel), never the other.
  const FITS = "(min-width: 1000px) and (min-height: 600px)";
  function spaceBelow(element) {
    let total = 0;
    for (let node = element; node && node !== document.documentElement; node = node.parentElement) {
      const style = getComputedStyle(node);
      total += parseFloat(style.marginBottom) || 0;
      if (node !== element) total += (parseFloat(style.paddingBottom) || 0) + (parseFloat(style.borderBottomWidth) || 0);
      for (let next = node.nextElementSibling; next; next = next.nextElementSibling) {
        const after = getComputedStyle(next);
        if (after.display === "none" || after.position === "fixed" || after.position === "absolute") continue;
        total += next.offsetHeight + (parseFloat(after.marginTop) || 0) + (parseFloat(after.marginBottom) || 0);
      }
    }
    return total;
  }
  function fitBoard() {
    const box = $("#showReviewBox");
    if (!box || box.hidden) return;
    box.classList.toggle("show-fit", matchMedia(FITS).matches);      // first: its tighter margins move the box
    const height = Math.floor(innerHeight - (box.getBoundingClientRect().top + scrollY) - spaceBelow(box));
    if (height < 460) box.classList.remove("show-fit");
    if (!box.classList.contains("show-fit")) return;
    box.style.setProperty("--show-fit-h", `${height}px`);
    // The picture as large as the video side allows, with the lines under it still in view.
    const wrap = box.querySelector(".show-stage-wrap"), stage = $("#showStage");
    // The scene editor temporarily owns the player; there is no board pane to size.
    if (!wrap) return;
    const others = [...wrap.children].filter((child) => child !== stage && child.offsetParent)
      .reduce((sum, child) => sum + child.offsetHeight + (parseFloat(getComputedStyle(child).marginTop) || 0) + (parseFloat(getComputedStyle(child).marginBottom) || 0), 0);
    box.style.setProperty("--show-stage-h", `${Math.max(160, wrap.clientHeight - others - 4)}px`);
  }
  addEventListener("resize", () => fitBoard());
  matchMedia(FITS).addEventListener?.("change", () => fitBoard());
  document.addEventListener("toggle", (event) => { if (event.target.closest?.("#showReviewBox")) fitBoard(); }, true);

  function render() {
    setTimeout(fitBoard);
    if (!review) return;
    clearTimeout(poll);
    closeTimeline();
    stopWatching();
    stopQuick();
    const current=step>=2?scenes()[step-2]:null;
    if(current&&review.resume_scene!==current.id){review.resume_scene=current.id;api("/show/scenes/resume",{method:"POST",body:JSON.stringify({scene_id:current.id})}).catch(()=>{});}
    const names = stepNames();
    $("#showSteps").innerHTML = names.map((name, index) => {
      const scene = index >= 2 && index < names.length - 1 ? scenes()[index - 2] : null;
      const mark = scene ? ` <small class="show-decision show-${scene.decision}">${DECISION_LABELS[scene.decision]}</small>` : "";
      const done = scene && ["accepted", "plain"].includes(scene.decision) ? "✓ " : "";
      return `<button type="button" data-show-step="${index}" class="${index === step ? "active" : ""}">${done}${escape(name)}${mark}</button>`;
    }).join("");
    // How far along the show is: every decided scene counts, whatever was decided.
    const decided = scenes().filter((scene) => ["accepted", "plain"].includes(scene.decision)).length;
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
    document.getElementById("showTimeline")?.remove();
    document.getElementById("showTimelineLauncher")?.remove();
    document.getElementById("showSceneTransport")?.remove();
    if (step === 0) renderIdentity();
    else if (step === 1) renderIntro();
    else if (currentScene()) renderScene();
    else renderFinal();
  }

  function showVideo(url, status = "") {
    stopQuick();
    $("#showStageLayer").querySelectorAll(".show-picture-placement").forEach(element=>element.remove());
    previewOrigin=currentScene()?.start||0;
    const video = $("#showStageVideo");
    $("#showStageCard").hidden = true;
    video.hidden = !url;
    video.controls=true;
    if($("#showSceneTransport"))$("#showSceneTransport").hidden=true;
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

  async function loadLibrary(){const data=await api('/show/library'+(showRetired?'?retired=1':''));library=data.items||[];retiredCount=data.retired_count||0;}
  function timelineItems(scene){
    if(!scene)return [];
    return [...sceneEffects(scene),...(clay?.placements||[]).filter(p=>p.enabled!==false && p.edited?.some(([a,b])=>a<scene.end&&b>scene.start)).map(p=>{
      const at=Math.min(...p.edited.map(e=>e[0])),end=Math.max(...p.edited.map(e=>e[1]));
      return {id:'clay:'+p.id,clay:p,kind:'clay',at,duration:end-at,status:'approved',params:{subject:p.name||'Simón · '+(CLAY_MODES[p.mode]||p.mode),group:p.group||p.id}};
    }),...scene.overlays.map(overlay=>{
      overlay.id ||= crypto.randomUUID();
      const asset=library.find(item=>item.path===overlay.asset);
      return {id:'sticker:'+overlay.id,overlay,kind:'sticker',at:overlay.at??scene.start,duration:overlay.duration??4,status:'approved',params:{subject:asset?.name||baseName(overlay.asset),selected_text:overlay.selected_text||''}};
    })];
  }
  const timelineItem=id=>timelineItems(currentScene()).find(item=>item.id===id);
  function sceneEffects(scene) {
    return review.proposals.filter((item) => scene.effects.includes(item.id));
  }

  function renderScene() {
    setTimeout(fitBoard);
    const scene = currentScene();
    if(selectedEffect && !timelineItems(scene).some(item=>item.id===selectedEffect)){selectedEffect=null;placingWords=false;firstWord=null;pickedWords=null;}
    const effects = sceneEffects(scene);
    const preview = scene.preview || { status: "none" };
    const words = scene.words || [];
    // A pin before the word where each placed picture enters.
    const pins = new Set((scene.overlays || []).map((overlay) => words.findIndex(([, start]) => start >= Number(overlay.at ?? scene.start) - 0.05)).filter((index) => index >= 0));
    $("#showPanel").innerHTML = `<p id="showSaveStatus" class="show-needs" role="alert" hidden></p><h3>${escape(scene.name)} <small>${clock(scene.start)}–${clock(scene.end)}</small></h3>
      <div class="show-said" id="showSaid"><strong>What you say</strong> <small>Click a word to go there. Select words, then choose Animation for selection. Ordinary right-click keeps the text menu.</small><button type="button" class="icon-button small" id="showSayTools">Animation for selection…</button>
        <div id="showWordPlacement" class="show-actions"></div><p class="show-said-text">${words.length ? words.map(([text, start, end], index) => `${pins.has(index) ? `<span class="show-pin" title="A picture enters here">📍</span>` : ""}<span data-word="${index}" data-start="${start}" data-end="${end}">${escape(text)}</span>`).join(" ") : escape(scene.text)}</p></div>
      <div class="show-preview-mode">
        ${review.saved_render ? `<button type="button" class="icon-button small ${useSaved(scene) ? "active" : ""}" data-preview-mode="saved" title="The last completed export, including its captions and effects">Saved render</button>` : ""}
        <button type="button" class="icon-button small ${!wantEffects.has(scene.id) && !useSaved(scene) ? "active" : ""}" data-preview-mode="quick" title="Your cut and chosen pictures, with immediate timing updates">Live timing preview</button>
        <button type="button" class="icon-button small ${wantEffects.has(scene.id) ? "active" : ""}" data-preview-mode="effects" title="Generate this scene with your current proposed effects before accepting">Generate scene preview</button>
      </div>
      <p class="show-proposal">${escape(scene.proposal)}</p>
      <p class="show-hint" role="status">${dirtyScenes.has(scene.id) ? "Changes saved. Generate a new scene preview to see them before accepting." : "Generate this scene with its effects, watch it, then accept or change it."}</p>
      <div class="show-decide">
        <button type="button" class="primary icon-button small" data-scene-decision="accepted" data-then-next ${viewedScenes.has(scene.id) ? "" : "disabled"}>Accept and next →</button>
        <button type="button" class="icon-button small" data-scene-decision="accepted" ${viewedScenes.has(scene.id) ? "" : "disabled"}>Accept</button>
        <button type="button" class="icon-button small" data-scene-modify>Modify</button>
        <button type="button" class="icon-button small" data-scene-decision="plain">Leave without effect</button>
        <span class="show-decision show-${scene.decision}">${DECISION_LABELS[scene.decision]}</span>
        <button type="button" class="icon-button small" data-open-library>Library</button>
      </div>
      ${(scene.references||[]).length?`<details class="show-scene-drawings" open><summary>Video references (${scene.references.length})</summary>${scene.references.map(ref=>`<p>${escape(ref.url)} · ${ref.start}–${ref.end} s<br>${ref.status==='downloaded'?'<small>Downloaded: it plays on its words with its own sound (see the timeline).</small>':`<small>Only the link is saved.</small> <button type="button" class="icon-button small" data-ref-download="${escape(ref.id)}">Download these seconds &amp; put them in</button>`}</p>`).join('')}</details>`:''}
      ${sceneDrawings(scene, effects)}
      <div id="showClay" class="show-clay"></div>
      <div id="showSceneEffects" class="show-review-list">${groupedEffects(effects.filter((item) => !(item.status === "discarded" && item.params.group))) || "<p class=\"show-hint\">No effect in this scene.</p>"}
        <form id="showSceneAdd" class="show-review-add"><strong>Add</strong>
          <select name="kind"><option value="drawing">A drawing</option><optgroup label="Textures">${Object.entries(TEXTURES).map(([key,name])=>`<option value="texture_${key}">${name}</option>`).join("")}</optgroup><option value="character">A character that speaks</option><option value="cutaway">A close-up</option><option value="math">Formulas around you</option><option value="word">A shouted word</option><option value="library">Take from the library</option><option value="original_eyes">Move original eyes</option><option value="original_mouth">Animate original mouth</option><option value="music_library">Add audio · music / jingles</option><option value="original_part">Move original part (sideways)</option><option value="references">Search internet references</option><option value="local_media">Video / audio from this computer</option><option value="video_reference">Video clip from a link</option><option value="video_library">Video cutouts library</option><option value="face_clip">Put my face on a clip</option><option value="generate_video">Create video · external prompt</option><option value="photo_edit">Realistic photo of me</option><option value="cutout">Cut out a person / background (photo)</option><option value="silhouette">Black silhouette (photo)</option><option value="video_cutout">Moving person · change photo / video background</option><option value="video_silhouette">Moving black silhouette · video</option><option value="clay">Clay memory · Simón</option><option value="captions">Normal speech captions</option></select>
          <label>at <input name="at" type="number" step="any" min="${scene.start}" max="${scene.end}" value="${scene.start.toFixed(1)}"> s</label>
          <label>of <input name="subject" type="text" maxlength="80" placeholder="a guitar"></label>
          <select name="style">${Object.entries(DRAWING_STYLES).map(([key, label]) => `<option value="${key}" ${key === "crayon" ? "selected" : ""}>${label}</option>`).join("")}</select>
          <select name="source">${[review.source, ...(review.resources || []).map((item) => item.path)].map((path) => `<option value="${escape(path)}">${escape(baseName(path))}</option>`).join("")}</select>
          <label>from <input name="source_time" type="number" min="0" step="any" value="0"> s of it</label>
          <button class="icon-button small" type="submit">Add &amp; preview</button></form>
        <h4>Your drawings &amp; stickers</h4><p class="show-hint">Choose a picture to add it as an editable timeline clip. Right-click a picture to remove it.</p><button type="button" class="icon-button small" data-open-library>Open the whole library…</button><button type="button" data-add-audio>Add audio…</button>

        <p class="show-hint">Drag a picture on the video to move it, its corner to resize it; click it for its tools.</p>
        <button type="button" class="icon-button small" data-studio="open">Cut out a photo / create a layer</button>
      </div>`;
    for (const id of open) if (scene.effects.includes(id)) mountEditor(id);
    groupAddOptions();
    bindAddForm(scene);
    drawOverlays(scene);
    showScenePicture(scene);
    clayBlock(scene);
    renderTimeline(scene);
    wordPlacementControls();
    showSaveStatus();
    fillSuggestions();
  }

  // The production storyboard uses edited seconds, including transcript and bars.
  const fitTiming=ShowClock.fit;
  function highlightWords(start,end) {
    document.querySelectorAll('#showSaid [data-word]').forEach(word=>word.classList.toggle('placement-word',Number(word.dataset.start)<end && Number(word.dataset.end)>start));
  }
  function wordPlacementControls() {
    const box=$('#showWordPlacement'), scene=currentScene(); if(!box||!scene)return;
    const item=timelineItem(selectedEffect);
    if(!item){box.innerHTML='<small>Select an effect in the timeline to place it on words.</small>';return;}
    box.innerHTML=`<strong>${escape(item.params.name||item.params.subject||item.params.text||KIND_LABELS[item.kind]||item.kind)}</strong>
      <button type="button" data-start-word-placement>Choose words</button>
      <button type="button" data-use-word-placement ${pickedWords?'':'disabled'}>Use these words</button>
      ${placingWords?'<button type="button" data-cancel-word-placement>Cancel</button>':''}
      <small>${pickedWords?`“${escape(pickedWords.text)}”`:placingWords?(firstWord===null?'Select a phrase, or click its first word, then its last word.':'Now click the last word.'):item.params.selected_text?`On: “${escape(item.params.selected_text)}”`:'Drag the bar or choose words.'}</small>`;
    highlightWords(pickedWords?.start??item.at,pickedWords?.end??item.at+item.duration);
  }
  async function savePlacement(item, changes) {
    const owner=currentScene()?.id;
    await flushEdits();
    if(item.clay){
      const placement=clay.placements.find(p=>p.id===item.clay.id);if(!placement)throw Error('This clay placement was removed.');
      Object.assign(placement,{timeline_at:changes.at??item.at,timeline_duration:changes.duration??item.duration});
      await saveClay(currentScene());return timelineItem(item.id);
    }
    if(item.overlay){
      const old={...item.overlay};Object.assign(item.overlay,changes);
      if(!await saveOverlays(currentScene())){Object.assign(item.overlay,old);throw Error('Sticker changes were not saved');}
      drawOverlays(currentScene());renderTimeline(currentScene());wordPlacementControls();return timelineItem(item.id);
    }
    const updated=await patch(item.id,changes);
    await markModified(item.id);
    if(currentScene()?.id===owner){renderTimeline(currentScene()); refreshCard(item.id); wordPlacementControls();}
    return updated;
  }
  async function placeOnWords(picked) {
    const scene=currentScene(), item=timelineItem(selectedEffect);
    if(!scene||!item||!picked)return;
    const timing=fitTiming(scene,picked.start,picked.end-picked.start);
    await savePlacement(item,{...timing,selected_text:picked.text});
    placingWords=false;firstWord=null;pickedWords=null;wordPlacementControls();
    locateEffect(timelineItem(item.id));
    showToast('Placed on your selected words. Drag the bar edges to extend or shorten it.');
  }
  function enterLiveTiming(scene, at=editedPlayhead()) {
    if(quick && !quick.saved)return;
    wantEffects.delete(scene.id);wantQuick.add(scene.id);
    $('#showPanel').querySelectorAll('[data-preview-mode]').forEach(button=>button.classList.toggle('active',button.dataset.previewMode==='quick'));
    playQuick(scene,Math.max(0,Math.min(scene.end-scene.start-.04,at-scene.start)));
  }
  function openTimeline() {
    if(!currentScene())return;
    enterLiveTiming(currentScene());
    if(!timelineDialog){
      timelineDialog=document.createElement('dialog');timelineDialog.id='showTimelineDialog';timelineDialog.setAttribute('aria-label','Scene editor');
      timelineDialog.innerHTML='<header><div><strong>Scene editor</strong><small>Video, voice and effect timing</small></div><button type="button" aria-label="Close scene editor" data-close-scene-editor>×</button></header><div data-scene-editor-body></div>';
      document.body.append(timelineDialog);
      timelineDialog.querySelector('[data-close-scene-editor]').onclick=closeTimeline;
      timelineDialog.addEventListener('cancel',event=>{event.preventDefault();closeTimeline();});
      timelineDialog.addEventListener('click',event=>{if(event.target!==timelineDialog)return;const r=timelineDialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeTimeline();});
      timelineDialog.addEventListener('close',()=>{if(!timelineDialog.open)restoreStage();});
    }
    if(!timelineDialog.open){
      const wrap=$('.show-stage-wrap');stageHome=document.createComment('Scene player home');wrap.before(stageHome);
      timelineDialog.querySelector('[data-scene-editor-body]').append(wrap);
      $('#showTimeline').hidden=false;$('#showTimelineLauncher').hidden=true;
      timelineDialog.showModal();
    }
    const scene=currentScene(),at=editedPlayhead(),cue=scene.captions?.find(c=>c.start<scene.end&&c.end>scene.start);
    if(!selectedEffect && cue && !scene.captions.some(c=>c.start<=at&&at<c.end)){
      wantEffects.delete(scene.id);wantQuick.add(scene.id);
      playQuick(scene,Math.max(0,cue.start-scene.start)+.02);syncPlayback();
    }
    editorAsked=null;watchEditorEffects(currentScene());
  }
  function restoreStage() {
    if(stageHome){stageHome.replaceWith($('.show-stage-wrap'));stageHome=null;}
    if($('#showTimeline'))$('#showTimeline').hidden=true;
    if($('#showTimelineLauncher'))$('#showTimelineLauncher').hidden=false;
  }
  function closeTimeline() {
    $("#showContextMenu").hidden=true;
    clearTimeout(editorPoll);
    if(quick?.effects){const scene=quick.scene;playQuick(scene,Math.max(0,editedPlayhead()-scene.start));$('#showStageVideo').pause();}
    if(timelineDialog?.open)timelineDialog.close();
    restoreStage();
    // Context menus mounted on body cannot stay above a modal's top layer.
    $('#showEffectMenu')?.setAttribute('hidden','');
  }
  function locateEffect(item, popup=true) {
    const scene=currentScene();if(!scene||!item||!timelineItems(scene).some(entry=>entry.id===item.id))return;
    selectedEffect=item.id;
    wantEffects.delete(scene.id);wantQuick.add(scene.id);
    $('#showPanel').querySelectorAll('[data-preview-mode]').forEach(button=>button.classList.toggle('active',button.dataset.previewMode==='quick'));
    // A little inside the effect: the source video lands on whole frames, and its first
    // frame can fall a few milliseconds before the effect begins (it would look absent).
    const inside=Math.min(.06,Math.max(0,(item.duration||0)/2));
    playQuick(scene,Math.max(0,Math.min(scene.end-.04,item.at+inside)-scene.start));
    $('#showStageVideo').pause();
    drawOverlays(scene);
    renderTimeline(scene);wordPlacementControls();
    if(popup)openTimeline();
  }
  function drawingPosition(item) {
    const p=item.params,vertical=p.position?.startsWith('top')?'top':p.position?.startsWith('bottom')?'bottom':'';
    const preset={top:[.2,.24],bottom:[.22,.72],'':[.21,.42]}[vertical];
    // Where the render draws it (the server sends params.shown), so both agree.
    return p.image_center || (p.shown ? p.shown.slice(0,2) : [p.position?.endsWith('right')?1-preset[0]:preset[0],preset[1]]);
  }
  const drawingWidth=item=>(item.params.image_center?item.params.size:item.params.shown?.[2]??item.params.size)||.25;
  function liveDrawings(scene) {
    const layer=$('#showStageLayer');
    layer.querySelectorAll('.show-picture-placement').forEach(element=>element.remove());
    if(!quick || quick.saved)return;
    for(const item of sceneEffects(scene).filter(item=>item.kind==='background' && item.status!=='discarded')) {
      const element=document.createElement('div');element.className='show-picture-placement show-background-live';element.dataset.liveEffect=item.id;
      element.innerHTML=item.params.picture?`<img src="${picture(item)}" draggable="false" alt=""><span>New background · you appear in front of it in the effects preview</span>`:`<span>New background · choose its picture</span>`;
      element.onpointerdown=event=>{event.preventDefault();event.stopPropagation();selectedEffect=item.id;wordPlacementControls();renderTimeline(scene);};
      layer.append(element);
    }
    for(const item of sceneEffects(scene).filter(item=>['drawing','word'].includes(item.kind) && item.status!=='discarded')) {
      const p=item.params,pos=drawingPosition(item);
      const element=document.createElement('div');element.className='show-picture-placement';element.dataset.liveEffect=item.id;
      element.classList.toggle('selected',item.id===selectedEffect);
      element.style.cssText=`left:${pos[0]*100}%;top:${pos[1]*100}%;width:${drawingWidth(item)*100}%`;
      element.innerHTML=p.picture?`<img src="${picture(item)}" draggable="false" style="${shapeStyle(p)}" alt="${escape(p.subject||p.text||'Drawing')}"><span class="picture-corner" title="Drag to resize"></span>`
        :item.kind==='word'?`<span class="show-word-live-text" style="${wordStyle(p)}">${escape(p.text)}</span><span class="picture-corner" title="Drag to resize"></span>`:'';
      if(!element.innerHTML)continue;
      element.insertAdjacentHTML('beforeend',ShowGeometry.handles(p));
      layer.append(element);
      element.onpointerdown=event=>{
        event.preventDefault();event.stopPropagation();element.setPointerCapture(event.pointerId);
        selectedEffect=item.id;wordPlacementControls();
        layer.querySelectorAll('.show-picture-placement').forEach(el=>el.classList.toggle('selected',el===element));
        const bounds=$('#showStage').getBoundingClientRect(),start=[event.clientX,event.clientY],initial=[...drawingPosition(item)],width=drawingWidth(item),resize=event.target.classList.contains('picture-corner');
        const playing=!$('#showStageVideo').paused;$('#showStageVideo').pause();
        let changes={},moved=false;
        element.onpointermove=e=>{
          moved ||= Math.abs(e.clientX-start[0])+Math.abs(e.clientY-start[1])>2;
          if(resize){changes={size:Math.max(.001,width+2*(e.clientX-start[0])/bounds.width),image_center:initial};element.style.width=`${changes.size*100}%`;}
          else {changes={image_center:[Math.max(-1,Math.min(2,initial[0]+(e.clientX-start[0])/bounds.width)),Math.max(-1,Math.min(2,initial[1]+(e.clientY-start[1])/bounds.height))]};element.style.left=`${changes.image_center[0]*100}%`;element.style.top=`${changes.image_center[1]*100}%`;}
        };
        const finish=()=>{element.onpointermove=null;element.onpointerup=null;element.onpointercancel=null;};
        element.onpointerup=async()=>{
          finish();
          try{if(moved)await savePlacement(item,changes);liveDrawings(scene);if(playing)$('#showStageVideo').play().catch(()=>{});}
          catch(error){liveDrawings(scene);showToast(error.message,true);}
        };
        element.onpointercancel=()=>{finish();liveDrawings(scene);};
      };
    }
    syncPlayback();
  }
  function editedPlayhead() {
    const scene=currentScene(),video=$('#showStageVideo');if(!scene)return 0;
    if(quick?.scene.id===scene.id){
      if(quick.pending!=null)return quick.pending;
      if(quick.effects)return scene.start+ShowCaptionClock.unshift(video.currentTime,scene.preview?.inserted||[]);
      return ShowClock.editedAt(scene,quick.segments,video.currentTime,quick.index,quick.pending);
    }
    const rendered=scene.preview?.url && video.getAttribute('src')===scene.preview.url;
    return previewOrigin+(rendered?ShowCaptionClock.unshift(video.currentTime,scene.preview.inserted||[]):video.currentTime);
  }
  function syncPlayback() {
    const scene=currentScene();if(!scene)return;
    const at=Math.max(scene.start,Math.min(scene.end,editedPlayhead())),box=$('#showTimeline');
    const transport=$('#showSceneTransport');
    if(transport){transport.hidden=!quick;transport.querySelector('[data-scene-play]').textContent=$('#showStageVideo').paused?'Play scene':'Pause';transport.querySelector('[data-scene-clock]').textContent=`${clock(Math.max(scene.start,Math.min(scene.end,at)))} / ${clock(scene.end)} · edited time`;}

    $('#showStageLayer').querySelectorAll('[data-live-effect]').forEach(element=>{
      const item=review.proposals.find(p=>p.id===element.dataset.liveEffect);
      element.hidden=!quick || quick.scene.id!==scene.id || quick.saved || !ShowClock.visible(item,at);
    });
    $('#showStageLayer').querySelectorAll('[data-overlay]').forEach(element=>{
      const item=scene.overlays[Number(element.dataset.overlay)];
      const start=item?.at??scene.start,end=start+(item?.duration??scene.end-scene.start);
      element.hidden=!item || !quick || quick.scene.id!==scene.id || quick.saved || at<start || at>=end;
    });
    const video=$('#showStageVideo'),layer=$('#showStageLayer');
    const editing=!!timelineDialog?.open && video.paused && !quick?.saved;
    layer.classList.toggle('show-editing',editing);
    for(const node of layer.querySelectorAll('.show-cutout-box,.show-steam-box,.show-steam-cross,#showSteamDrawing')){
      const item=timelineItem(selectedEffect);
      node.hidden=!editing || !item || item.status==='discarded' || !ShowClock.visible(item,at);
    }
    window.ShowLiveCaptions?.sync({scene,at,video:$('#showStageVideo'),projectId:review.project_id,revision:review.caption_revision,
      saved:!!quick?.saved&&!quick?.effects,
      replaced:sceneEffects(scene).filter(item=>item.kind==='word'&&item.status!=='discarded'&&item.params.picture&&ShowClock.visible(item,at)).map(item=>item.params.replaces_caption)});
    const input=box?.querySelector('[data-timeline-seek]');
    if(input){input.value=at;box.querySelector('[data-playhead-label]').textContent=clock(at);box.style.setProperty('--playhead',`${Math.max(0,Math.min(100,100*(at-scene.start)/(scene.end-scene.start)))}%`);}
  }
  function renderTimeline(scene) {
    let box=$('#showTimeline');if(!box){box=document.createElement('section');box.id='showTimeline';box.setAttribute('aria-label','Scene timeline');$('.show-stage-wrap').append(box);}
    box.hidden=!timelineDialog?.open;
    let launcher=$('#showTimelineLauncher');if(!launcher){launcher=document.createElement('button');launcher.type='button';launcher.id='showTimelineLauncher';launcher.className='icon-button';launcher.textContent='Open scene editor · video & timeline';launcher.onclick=()=>{openTimeline();};$('.show-stage-wrap').append(launcher);}
    launcher.hidden=!!timelineDialog?.open;
    const duration=scene.end-scene.start;
    const pct=t=>100*(t-scene.start)/duration;
    const selected=timelineItems(scene).find(item=>item.id===selectedEffect);
    const rows=timelineItems(scene).filter(item=>item.status!=='discarded');
    box.innerHTML=`<header><strong>Scene timeline</strong><small>${clock(scene.start)}–${clock(scene.end)}</small></header>
      <button type="button" class="icon-button small primary" data-editor-add>Add ▸</button> <button type="button" class="icon-button small" data-open-library>Library</button>
      <details class="show-sticker-picker"><summary>Add sticker</summary><div class="show-library">${library.map((asset,index)=>asset.retired?'':`<button type="button" data-library="${index}" aria-label="Add ${escape(asset.name)}"><img src="${asset.url}" alt="" loading="lazy">${escape(asset.name)}</button>`).join('')}</div></details>
      <p>Play or scrub to check when a picture appears and disappears. Drag a bar to move it; drag either edge to trim.</p>
      <div class="show-timeline-edit" role="toolbar" aria-label="Edit the timeline">${[['split','Split at playhead','⌘B'],['cut','Cut','⌘X'],['copy','Copy','⌘C'],['paste','Paste at playhead','⌘V'],['duplicate','Duplicate','⌘D'],['delete','Delete','⌫'],['undo','Undo','⌘Z'],['redo','Redo','⇧⌘Z']].map(([action,label,keys])=>`<button type="button" class="icon-button small" data-timeline-command="${action}" title="${label} (${keys})" ${timelineCommandOff(action,scene)?'disabled':''}>${label}</button>`).join('')}</div>
      ${selected?`<div class="show-timeline-tools"><strong>${escape(selected.params.name||selected.params.subject||selected.params.text||KIND_LABELS[selected.kind]||selected.kind)}</strong><button type="button" data-place-words="${selected.id}">Choose words</button><button type="button" data-locate-effect="${selected.id}">Go to effect</button><button type="button" data-show-preview="${selected.id}">Render effect preview</button>${selected.clay?`<button type="button" data-clay-remove="${selected.clay.id}">Remove clay</button>`:selected.overlay?`<button type="button" data-remove-sticker="${selected.id}">Remove sticker</button>`:`<button type="button" data-show-status="discarded" data-id="${selected.id}">Remove effect</button>`}${isTexture(selected)?steamTools(selected):''}${selected.kind==='drawing'?`<button type="button" data-open-library="${selected.id}">Choose from library</button>`:''}${selected.kind==='background'?`<button type="button" data-video-layers="${selected.id}">Edit moving video layers</button><button type="button" data-open-library="${selected.id}">Background from library</button>`:''}${selected.overlay||['drawing','word'].includes(selected.kind)?`<label class="show-movement">Movement <select data-movement="${selected.id}">${movementChoices(selected).map(([key,label])=>`<option value="${key}" ${movementOf(selected)===key?'selected':''}>${label}</option>`).join('')}</select></label>`:''}${selected.kind==='clay'?`<button type="button" data-clay-settings="${selected.clay.id}">Clay settings…</button>`:''}${selected.kind==='clip'?`<button type="button" data-clip-settings="${selected.id}">Clip settings…</button><button type="button" data-face-clip="${selected.id}">Put my face on it</button>${selected.params.clip_original?`<button type="button" data-face-original="${selected.id}">Use the original clip</button>`:''}`:''}${selected.kind==='drawing'&&!selected.params.picture?'<small>Choose an existing picture or generate and select one first. Rendering will not invent a replacement.</small>':''}</div>`:''}<label class="show-timeline-seek">Playhead <input type="range" aria-label="Scene playhead" data-timeline-seek min="${scene.start}" max="${scene.end-.04}" step="0.04" value="${scene.start}"><output data-playhead-label>${clock(scene.start)}</output></label>
      <div class="show-time-row"><span>Video</span><div class="show-time-source" data-scene-seek>Original video · your cut</div></div>
      <div class="show-time-row"><span>Audio</span><div class="show-time-source audio" data-scene-seek>Original voice · linked to video</div></div>

      <div class="show-time-row"><span>Speech captions <button type="button" class="show-caption-switch" data-captions-on="${review.captions_enabled?'0':'1'}" title="Subtitles of everything you say, at the bottom. Off unless you want them.">${review.captions_enabled?'On · turn off':'Off · turn on'}</button></span><div class="show-time-track show-caption-track">${(review.captions_enabled?scene.captions||[]:[]).map(cue=>`<button type="button" class="show-caption-time" data-caption-at="${Math.max(scene.start,cue.start)}" style="left:${pct(Math.max(scene.start,cue.start))}%;width:${Math.max(.2,pct(Math.min(scene.end,cue.end))-pct(Math.max(scene.start,cue.start)))}%" title="${escape(cue.lines.join(' '))} · click to edit on the video">${escape(cue.lines.join(' '))}</button>`).join('')}</div></div>
      ${timelineRows(rows).map(group=>{
        // One row per effect: its pieces (split, pasted) sit side by side on it.
        const first=group[0],label=KIND_LABELS[first.kind]||first.kind,name=first.params.name||first.params.subject||first.params.text||label;
        const inside=group.filter(item=>Math.min(scene.end,item.at+item.duration)>Math.max(scene.start,item.at));
        return `<div class="show-time-row"><button type="button" class="show-time-name" data-locate-effect="${first.id}" title="${escape(name)}${group.length>1?` · ${group.length} pieces`:''}">${escape(name)}${group.length>1?` <small>×${group.length}</small>`:''}</button><div class="show-time-track" data-track="${first.id}">
        ${!inside.length?first.kind==='character'&&first.duration<=0?`<button type="button" data-show-adjust="${first.id}" data-time-clip="${first.id}" class="show-time-outside">Voice &amp; hold timing · Adjust</button>`:`<button type="button" data-place-words="${first.id}" class="show-time-outside">Outside this scene · choose words</button>`:inside.map(item=>{
          const a=Math.max(scene.start,item.at),b=Math.min(scene.end,item.at+item.duration);
          return `<div tabindex="0" role="button" aria-label="${escape(name)} timeline clip" class="show-time-clip ${selectedEffect===item.id?'selected':''}" data-time-clip="${item.id}" style="left:${pct(a)}%;width:${100*(b-a)/duration}%" title="${escape(name)} · ${clock(item.at)}–${clock(item.at+item.duration)} · right-click to cut, copy, paste or split"><span data-trim="start" aria-label="Trim start"></span><b>${escape(name)}</b><span data-trim="end" aria-label="Trim end"></span></div>`;}).join('')}</div></div>`;
      }).join('')}`;
    const seekTo=time=>{
      wantEffects.delete(scene.id);wantQuick.add(scene.id);
      playQuick(scene,Math.max(0,Math.min(duration-.04,time-scene.start)));
      syncPlayback();
    };
    box.querySelectorAll('[data-caption-at]').forEach(button=>button.onclick=()=>{
      wantEffects.delete(scene.id);wantQuick.add(scene.id);
      playQuick(scene,Math.max(0,Number(button.dataset.captionAt)-scene.start+.02));$('#showStageVideo').pause();
      syncPlayback();
    });
    box.querySelectorAll('[data-clay-at]').forEach(button=>button.onclick=()=>{seekTo(Number(button.dataset.clayAt));showToast('Clay timing and display controls are in Clay memory below the scene.');});
    box.querySelector('[data-timeline-seek]').oninput=event=>seekTo(Number(event.target.value));
    box.onpointerdown=event=>{
      if(event.button!==0)return;
      const clip=event.target.closest('[data-time-clip]');if(!clip)return;
      enterLiveTiming(scene);
      event.preventDefault();clip.setPointerCapture(event.pointerId);
      const item=timelineItem(clip.dataset.timeClip), track=clip.parentElement.getBoundingClientRect(),x=event.clientX,at=item.at,end=at+item.duration,edge=event.target.dataset.trim;
      selectedEffect=item.id;wordPlacementControls();let moved=false,timing={at,duration:item.duration};
      clip.onpointermove=e=>{
        const delta=(e.clientX-x)/track.width*duration;moved ||= Math.abs(e.clientX-x)>2;
        if(edge==='start'){const start=Math.max(scene.start,Math.min(end-.04,at+delta));timing={at:start,duration:end-start,...(item.kind==='jingle'?{start:Math.max(0,(item.params.start||0)+start-at)}:{})};}
        else if(edge==='end')timing={at,duration:Math.max(.04,Math.min(scene.end,end+delta)-at)};
        else timing=fitTiming(scene,at+delta,end-at);
        clip.style.left=`${pct(timing.at)}%`;clip.style.width=`${100*timing.duration/duration}%`;
        highlightWords(timing.at,timing.at+timing.duration);
      };
      clip.onpointerup=async()=>{
        clip.onpointermove=null;clip.onpointerup=null;
        try {if(moved){await timelineEdit(scene,()=>savePlacement(item,{...fitTiming(scene,timing.at,timing.duration),...(timing.start!=null?{start:timing.start}:{}),selected_text:''}));liveDrawings(scene);}
          else locateEffect(item,false);}
        catch(error){showToast(error.message,true);renderTimeline(scene);}
      };
      clip.onpointercancel=()=>{clip.onpointermove=null;renderTimeline(scene);};
    };
    box.onclick=event=>{
      const track=event.target.closest('[data-scene-seek],[data-track]');
      if(!track||event.target.closest('button,[data-time-clip]'))return;
      const bounds=track.getBoundingClientRect();seekTo(scene.start+(event.clientX-bounds.left)/bounds.width*duration);
    };
    syncPlayback();
    box.onkeydown=event=>{const clip=event.target.closest('[data-time-clip]');if(clip && event.key==='Enter'){event.preventDefault();locateEffect(timelineItem(clip.dataset.timeClip));}};
  }
  // A clip's speed: 100 % normal, down to 10 % (slow motion), up to 200 %. The same seconds of
  // the clip play slower or faster, so its bar gets longer or shorter.
  const CLIP_SPEEDS = [10, 25, 50, 75, 100, 125, 150, 200];
  async function setClipSpeed(scene, item, speed) {
    await flushEdits();
    await timelineEdit(scene, () => patch(item.id, { speed }));
    await markModified(item.id);
    renderTimeline(currentScene()); refreshCard(item.id);
    showToast(`Speed ${Math.round(speed * 100)} %: it plays like that in the effects preview and the video.`);
  }

  // Movement for any picture: a placed sticker or an effect (drawing, word, library picture).
  function movementChoices(item){
    const talkable=item.overlay&&isCharacter(item.overlay.asset);
    return Object.entries(ANIMATIONS).filter(([key])=>item.overlay?(!['talk','look'].includes(key)||talkable):!['talk','look','path'].includes(key)&&(key!=='draw'||item.params.strokes));
  }
  const movementOf=item=>(item.overlay?item.overlay.action:item.params.action)||'none';
  async function setMovement(scene,item,key){
    if(item.overlay){const index=scene.overlays.indexOf(item.overlay);await timelineEdit(scene,()=>animateOverlay(scene,index,key));}
    else {await flushEdits();await patch(item.id,{action:key});await markModified(item.id);refreshCard(item.id);}
    renderTimeline(scene);liveDrawings(scene);
    showToast(`${ANIMATIONS[key]}: ${key==='path'?'click the points on the video':'it shows in the effects preview (Render effect preview, or Play scene with every effect)'}.`);
  }
  function movementItems(scene,item){
    return movementChoices(item).map(([key,label])=>({label,checked:movementOf(item)===key,action:()=>setMovement(scene,item,key)}));
  }
  document.addEventListener('change',event=>{
    const select=event.target instanceof Element?event.target.closest('[data-movement]'):null;
    if(!select||!review)return;
    const scene=currentScene(),item=timelineItem(select.dataset.movement);
    if(scene&&item)setMovement(scene,item,select.value).catch(error=>showToast(error.message,true));
  });

  // Earlier versions of the storyboard: making the Show again saves the one it replaces.
  async function storyboardHistory(){
    const {items}=await api('/show/history');
    const when=id=>`${id.slice(6,8)}/${id.slice(4,6)} ${id.slice(9,11)}:${id.slice(11,13)}`;
    ShowMenu.open(innerWidth/2-160,120,items.length?[{heading:'Earlier versions of this storyboard'},...items.slice(0,20).map(item=>({
      label:`${when(item.id)} · ${item.decided??'?'} of ${item.scenes??'?'} scenes decided · ${item.effects??'?'} effects · ${item.pictures??0} pictures`,
      title:item.reason,
      action:async()=>{
        if(!confirm(`Go back to the version of ${when(item.id)}? The current storyboard is saved first, so you can come back to it.`))return;
        await api(`/show/history/${encodeURIComponent(item.id)}/restore`,{method:'POST',body:'{}'});
        showToast('Earlier version restored. Reloading the storyboard…');
        setTimeout(()=>location.reload(),600);
      }}))]:[{heading:'No earlier versions yet: one is saved each time the Show is made again.'}],{host:menuHost()});
  }
  document.addEventListener('click',event=>{
    if(event.target instanceof Element&&event.target.closest('[data-show-history]'))storyboardHistory().catch(error=>showToast(error.message,true));
  });

  // A new background behind the person on the picked words; its picture is chosen next.
  async function addBackground(scene,picked,style="person"){
    const at=Math.max(scene.start,Math.min(scene.end-.5,picked.start));
    const duration=Math.round(Math.min(scene.end-at,Math.max(3,picked.end-picked.start))*1000)/1000;
    const made=await timelineEdit(scene,async()=>{
      const item=await api('/show/review/add',{method:'POST',body:JSON.stringify({kind:'background',at:Math.round(at*1000)/1000,duration,subject:picked.text||'',selected_text:picked.text||''})});
      review.proposals.push(item);if(!scene.effects.includes(item.id))scene.effects.push(item.id);
      await markModified(item.id);return item;
    });
    selectedEffect=made.id;renderTimeline(scene);liveDrawings(scene);
    await openVideoLayers(made.id,style);
  }


  let videoLayerDialog=null;
  async function openVideoLayers(id,style="person") {
    if(videoLayerDialog)videoLayerDialog.remove();
    videoLayerDialog=document.createElement('dialog');
    videoLayerDialog.style.cssText='width:96vw;max-width:1600px;height:94vh;padding:0;border:1px solid #475044;background:#151916';
    videoLayerDialog.innerHTML='<button type="button" aria-label="Close video layers" style="position:absolute;right:10px;top:5px;z-index:2">×</button><iframe title="Moving video layers" style="width:100%;height:100%;border:0"></iframe>';
    videoLayerDialog.querySelector('iframe').src='/api/v1/show/video-layers/'+encodeURIComponent(id)+'/editor?style='+encodeURIComponent(style);
    videoLayerDialog.querySelector('button').onclick=()=>videoLayerDialog.close();
    document.body.append(videoLayerDialog);videoLayerDialog.showModal();
  }
  document.addEventListener('click',event=>{const target=event.target.closest?.('[data-video-layers]');if(target)openVideoLayers(target.dataset.videoLayers,review.proposals.find(p=>p.id===target.dataset.videoLayers)?.params.person_style||'person');});
  window.addEventListener('message',async event=>{
    if(event.origin!==location.origin||event.data?.type!=='show-video-layers-saved'||event.source!==videoLayerDialog?.querySelector('iframe')?.contentWindow)return;
    const item=review?.proposals.find(p=>p.id===event.data.item?.id);if(!item)return;
    Object.assign(item,event.data.item);videoLayerDialog.close();const scene=currentScene();
    await markModified(item.id);invalidateLocal(scene);renderScene();await requestScenePreview(scene);
    showToast('Moving video layers saved. Their placement is used in the scene preview and final export.');
  });

  // ---- Timeline editing: cut, copy, paste at the playhead, split, delete, undo ----------
  // A pasted or split-off piece is another effect with the same settings that stays on its
  // effect's row (params.group / overlay.group): the render needs nothing new.
  let timelineClipboard=null;
  const timelineUndo=[],timelineRedo=[];
  const rowKey=item=>item.overlay?(item.overlay.group||item.overlay.id):(item.params.group||item.id);
  function timelineRows(items){
    const rows=new Map();
    for(const item of items){const key=rowKey(item);if(!rows.has(key))rows.set(key,[]);rows.get(key).push(item);}
    return [...rows.values()].map(group=>group.sort((a,b)=>a.at-b.at));
  }
  function timelineSnapshot(scene){
    return {scene:scene.id,effects:sceneEffects(scene).map(item=>({id:item.id,at:item.at,duration:item.duration,status:item.status,start:item.params.start})),overlays:structuredClone(scene.overlays),clay:clay?structuredClone(clay.placements):null};
  }
  async function timelineEdit(scene,change){
    // Kept for ⌘Z only once the change is saved.
    const before=timelineSnapshot(scene);
    const result=await change();
    timelineUndo.push(before);if(timelineUndo.length>60)timelineUndo.shift();
    timelineRedo.length=0;
    renderTimeline(scene);
    return result;
  }
  async function restoreTimeline(snapshot){
    const scene=scenes().find(entry=>entry.id===snapshot.scene);if(!scene)return;
    await flushEdits();
    const failed=[];
    for(const item of sceneEffects(scene)){
      const was=snapshot.effects.find(entry=>entry.id===item.id);
      const changes=!was?(item.status!=='discarded'?{status:'discarded'}:{}):{
        ...(Math.abs(was.at-item.at)>1e-4||Math.abs(was.duration-item.duration)>1e-4?{at:was.at,duration:was.duration}:{}),
        ...(was.status!==item.status?{status:was.status}:{}),...(was.start!=null&&was.start!==item.params.start?{start:was.start}:{})};
      if(Object.keys(changes).length)await patch(item.id,changes).catch(error=>failed.push(error.message));
    }
    if(JSON.stringify(scene.overlays)!==JSON.stringify(snapshot.overlays)){scene.overlays=structuredClone(snapshot.overlays);selectedOverlay=null;drawOverlays(scene);await saveOverlays(scene);}
    if(snapshot.clay && clay){clay.placements=structuredClone(snapshot.clay);await saveClay(scene);}
    await markModified();
    if(failed.length)showToast('Some changes could not be undone: '+failed[0],true);
  }
  function selectedTimelineItem(scene){
    const overlay=selectedOverlay!=null?scene.overlays[selectedOverlay]:null;
    return timelineItem(selectedEffect)||(overlay?timelineItem('sticker:'+overlay.id):null);
  }
  function timelineCommandOff(action,scene){
    if(action==='undo')return !timelineUndo.some(entry=>entry.scene===scene.id);
    if(action==='redo')return !timelineRedo.some(entry=>entry.scene===scene.id);
    if(action==='paste')return !timelineClipboard;
    return !selectedTimelineItem(scene);
  }
  async function pastePiece(scene,clip,at){
    const start=Math.max(scene.start,Math.min(scene.end-.1,at));
    const length=clip.duration>0?Math.min(clip.duration,scene.end-start):0;
    if(clip.clay){
      const placement={...structuredClone(clip.clay),id:crypto.randomUUID(),group:clip.clay.group||clip.clay.id,timeline_at:start,timeline_duration:Math.max(.04,length)};
      clay.placements.push(placement);selectedEffect='clay:'+placement.id;await saveClay(scene);return;
    }
    if(clip.overlay){
      const overlay={...structuredClone(clip.overlay),id:crypto.randomUUID(),group:clip.overlay.group||clip.overlay.id,at:Math.round(start*1000)/1000,duration:Math.round(Math.max(.04,length)*1000)/1000,selected_text:''};
      scene.overlays.push(overlay);selectedOverlay=scene.overlays.length-1;selectedEffect='sticker:'+overlay.id;
      drawOverlays(scene);if(!await saveOverlays(scene))throw Error('The pasted picture was not saved');
      return;
    }
    const made=await api(`/show/review/${encodeURIComponent(clip.id)}/piece`,{method:'POST',body:JSON.stringify({at:Math.round(start*1000)/1000,duration:Math.round(length*1000)/1000})});
    review.proposals.push(made);
    const owner=scenes().find(entry=>entry.start<=made.at&&made.at<entry.end)||scene;
    if(!owner.effects.includes(made.id))owner.effects.push(made.id);
    selectedEffect=made.id;
    await markModified(made.id);
  }
  async function splitPiece(scene,item,at){
    const start=item.at,end=item.at+item.duration;
    if(!(at>start+.05&&at<end-.05))throw Error('Put the playhead inside the selected piece, where it should be cut');
    const at3=Math.round(at*1000)/1000;
    if(item.clay){
      const p=item.clay,mid=p.start+(p.end-p.start)*(at3-start)/(end-start);p.group ||=p.id;
      clay.placements.push({...structuredClone(p),id:crypto.randomUUID(),start:mid,timeline_at:at3,timeline_duration:end-at3});
      Object.assign(p,{end:mid,timeline_at:start,timeline_duration:at3-start});await saveClay(scene);return;
    }
    if(item.overlay){
      const overlay=item.overlay;overlay.group ||= overlay.id;
      const after={...structuredClone(overlay),id:crypto.randomUUID(),at:at3,duration:Math.round((end-at3)*1000)/1000,selected_text:''};
      overlay.duration=Math.round((at3-start)*1000)/1000;
      scene.overlays.push(after);drawOverlays(scene);
      if(!await saveOverlays(scene))throw Error('The split was not saved');
      return;
    }
    const made=await api(`/show/review/${encodeURIComponent(item.id)}/piece`,{method:'POST',body:JSON.stringify({at:at3,duration:Math.round((end-at3)*1000)/1000,offset:Math.round((at3-start)*1000)/1000})});
    review.proposals.push(made);if(!scene.effects.includes(made.id))scene.effects.push(made.id);
    await patch(item.id,{duration:Math.round((at3-start)*1000)/1000});
    await markModified(item.id);
  }
  async function removePiece(scene,item){
    if(item.clay){item.clay.enabled=false;await saveClay(scene);selectedEffect=null;return;}
    if(item.overlay){
      scene.overlays=scene.overlays.filter(overlay=>overlay!==item.overlay);selectedOverlay=null;
      drawOverlays(scene);if(!await saveOverlays(scene))throw Error('The picture was not removed');
    } else {await patch(item.id,{status:'discarded'});await markModified(item.id);refreshCard(item.id);}
    selectedEffect=null;
  }
  async function timelineCommand(action,scene=currentScene()){
    if(!scene||!review)return;
    const item=selectedTimelineItem(scene),at=editedPlayhead();
    const clipOf=entry=>entry.clay?{clay:structuredClone(entry.clay),duration:entry.duration}:entry.overlay?{overlay:structuredClone(entry.overlay),duration:entry.duration}:{id:entry.id,duration:entry.duration};
    if(action==='undo'||action==='redo'){
      const from=action==='undo'?timelineUndo:timelineRedo,to=action==='undo'?timelineRedo:timelineUndo;
      const index=from.findLastIndex(entry=>entry.scene===scene.id);if(index<0)return;
      const snapshot=from.splice(index,1)[0];to.push(timelineSnapshot(scene));
      await restoreTimeline(snapshot);
    } else if(action==='paste'){
      if(!timelineClipboard)return;
      await timelineEdit(scene,()=>pastePiece(scene,timelineClipboard,at));
    } else {
      if(!item){if(action!=='delete')showToast('Select a piece in the timeline first.');return;}
      if(action==='copy'){timelineClipboard=clipOf(item);showToast('Copied. Move the playhead and paste (⌘V): it goes on the same row.');renderTimeline(scene);return;}
      if(action==='cut'){timelineClipboard=clipOf(item);await timelineEdit(scene,()=>removePiece(scene,item));}
      else if(action==='delete')await timelineEdit(scene,()=>removePiece(scene,item));
      else if(action==='duplicate')await timelineEdit(scene,()=>pastePiece(scene,clipOf(item),item.at+item.duration));
      else if(action==='split')await timelineEdit(scene,()=>splitPiece(scene,item,at));
    }
    renderTimeline(currentScene());liveDrawings(currentScene());refreshSceneDrawings();wordPlacementControls();
  }
  function pieceActions(scene,item) {
    const run=action=>()=>{selectedEffect=item.id;selectedOverlay=item.overlay?scene.overlays.indexOf(item.overlay):null;return timelineCommand(action,scene);};
    return [
      {label:'Cut',hint:'⌘X',action:run('cut')},{label:'Copy',hint:'⌘C',action:run('copy')},
      {label:`Paste at playhead (${clock(editedPlayhead())})`,hint:'⌘V',disabled:!timelineClipboard,action:run('paste')},
      {label:'Duplicate after it',hint:'⌘D',action:run('duplicate')},
      {label:`Split at playhead (${clock(editedPlayhead())})`,hint:'⌘B',disabled:!item.overlay&&['character','clip'].includes(item.kind),action:run('split')},
      {label:'Delete',hint:'⌫',action:run('delete')},
      {separator:true},
      ...(item.overlay||['drawing','word'].includes(item.kind)?[{label:'Movement',submenu:()=>movementItems(scene,item)}]:[]),
      {label:'Undo',hint:'⌘Z',disabled:timelineCommandOff('undo',scene),action:run('undo')},
      {label:'Redo',hint:'⇧⌘Z',disabled:timelineCommandOff('redo',scene),action:run('redo')},
    ];
  }
  function timelineMenu(x,y,id){
    const scene=currentScene(),item=timelineItem(id);if(!scene||!item)return;
    selectedEffect=item.id;selectedOverlay=item.overlay?scene.overlays.indexOf(item.overlay):null;renderTimeline(scene);
    const name=item.params.name||item.params.subject||item.params.text||KIND_LABELS[item.kind]||item.kind;
    ShowMenu.open(x,y,[
      {heading:`${KIND_LABELS[item.kind]||item.kind} · ${name} · ${clock(item.at)}–${clock(item.at+item.duration)}`},
      ...pieceActions(scene,item),
      {separator:true},
      ...effectItems(scene,item),
    ],{host:menuHost()});
  }
  document.addEventListener('click',event=>{
    const button=event.target instanceof Element?event.target.closest('[data-timeline-command]'):null;
    if(button)timelineCommand(button.dataset.timelineCommand).catch(error=>showToast(error.message,true));
  });
  document.addEventListener('keydown',event=>{
    // In the scene editor; typing in a field, or copying selected transcript text, stays native.
    if(!timelineDialog?.open||!review||event.target.closest?.('input,textarea,select,[contenteditable="true"],.zmenu') || (event.target.closest?.('dialog') && event.target.closest('dialog')!==timelineDialog))return;
    const mod=event.metaKey||event.ctrlKey,key=event.key.toLowerCase();
    let action=null;
    if(mod&&key==='z')action=event.shiftKey?'redo':'undo';
    else if(mod&&key==='y')action='redo';
    else if(mod)action={c:'copy',x:'cut',v:'paste',d:'duplicate',b:'split',k:'split'}[key]||null;
    else if(['Delete','Backspace'].includes(event.key))action='delete';
    if(!action)return;
    if(['copy','cut'].includes(action)&&String(window.getSelection?.()||'').trim())return;
    event.preventDefault();event.stopImmediatePropagation();
    timelineCommand(action).catch(error=>showToast(error.message,true));
  },true);
  for(const name of ['timeupdate','loadeddata','play','pause'])$('#showStageVideo').addEventListener(name,syncPlayback);
  $('#showStageVideo').addEventListener('seeked',()=>{if(quick)quick.pending=null;syncPlayback();});
  document.addEventListener('selectionchange',()=>{
    if(!selectedEffect)return;const picked=saidSelection();if(!picked)return;
    pickedWords=picked;wordPlacementControls();
  });
  document.addEventListener('click',async event=>{
    const target=event.target.closest?.('[data-place-words],[data-locate-effect],[data-start-word-placement],[data-use-word-placement],[data-cancel-word-placement]');if(!target||!review)return;
    try {
      if(target.hasAttribute('data-cancel-word-placement')){placingWords=false;firstWord=null;pickedWords=null;wordPlacementControls();return;}
      if(target.hasAttribute('data-use-word-placement')){await placeOnWords(pickedWords);return;}
      selectedEffect=target.dataset.placeWords||target.dataset.locateEffect||selectedEffect;
      if(target.dataset.locateEffect){locateEffect(timelineItem(selectedEffect));return;}
      closeTimeline();placingWords=true;firstWord=null;pickedWords=null;wordPlacementControls();$('#showSaid').scrollIntoView({block:'nearest'});
    }catch(error){showToast(error.message,true);}
  });

  // ---- Clay memories: placements of an approved clay story over this scene ----------

  const CLAY_MODES = { full: "Full clay shot", pip: "Small clay window", bubble: "Thought bubble", blend: "Brief blend over you" };
  let clay = null;
  let claySaving = null;

  async function saveClay(scene) {
    try {clay=await api('/show/clay/placements',{method:'PUT',body:JSON.stringify({revision:clay.revision,placements:clay.placements})});}
    catch(error){clay=await api('/show/clay');renderTimeline(scene);throw error;}
    invalidateLocal(scene);renderTimeline(scene);wordPlacementControls();prepareInBackground(scene);
  }
  function openClaySettings(placement){ShowClayEditor.open(currentScene(),null,placement);}
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('[data-clay-settings],[data-clay-remove]');if(!button)return;
    const p=clay?.placements.find(p=>p.id===(button.dataset.claySettings||button.dataset.clayRemove));if(!p)return;
    if(button.dataset.clayRemove)removePiece(currentScene(),{clay:p}).catch(e=>showToast(e.message,true));else openClaySettings(p);
  });
  async function clayBlock(scene) {
    const box = $("#showClay");
    if (!box) return;
    const loaded = await api("/show/clay").catch(() => null);
    if(currentScene()?.id !== scene.id)return;
    clay = loaded;
    renderTimeline(scene);
    if (!clay || !clay.stories.length || currentScene()?.id !== scene.id) { box.innerHTML = ""; return; }
    const inScene = (item) => item.edited.some(([a, b]) => a < scene.end && b > scene.start);
    const shown = clay.placements.map((item, index) => [item, index]).filter(([item]) => inScene(item));
    const story = clay.stories.find((item) => clay.placements.some((p) => p.story === item.id && inScene(p))) || null;
    if (!story && !shown.length) { box.innerHTML = ""; return; }
    const span = (item) => item.edited.length ? `${clock(item.edited[0][0])}–${clock(item.edited[item.edited.length - 1][1])} in the show` : "cut from the show";
    box.innerHTML = `<h4>Clay memory <small>${escape(story?.quote || "")}</small></h4>
      <p class="show-hint">Over your own voice and captions. Drag the Clay story bar or its edges in the scene editor to move, shorten or lengthen it. It plays once, fitting its held poses to the bar, without looping. Source start/end below selects the part of the story to use.</p>
      ${shown.map(([item, index]) => `<fieldset class="show-clay-row" data-clay="${index}">
        <legend><button type="button" data-clay-settings="${item.id}">Clay settings…</button> <label><input type="checkbox" data-clay-field="enabled" ${item.enabled !== false ? "checked" : ""}> ${escape(CLAY_MODES[item.mode] || item.mode)}</label> <small>${span(item)}</small></legend>
        ${field("Mode", `<select data-clay-field="mode">${clay.modes.map((mode) => `<option value="${mode}" ${mode === item.mode ? "selected" : ""}>${CLAY_MODES[mode] || mode}</option>`).join("")}</select>`)}
        <div class="show-fields">
          ${field("From (s)", `<input type="number" data-clay-field="start" value="${item.start}" step="any" min="0" max="${clay.stories.find(s=>s.id===item.story)?.duration ?? 60}">`)}
          ${field("To (s)", `<input type="number" data-clay-field="end" value="${item.end}" step="any" min="0" max="${clay.stories.find(s=>s.id===item.story)?.duration ?? 60}">`)}
          ${field("Fade (s)", `<input type="number" data-clay-field="fade" value="${item.fade ?? 0.3}" step="any" min="0" max="2">`)}
        </div>
        <div class="show-fields">
          ${field("Across", `<input type="range" data-clay-field="x" value="${item.x}" min="0" max="1" step="0.01">`)}
          ${field("Down", `<input type="range" data-clay-field="y" value="${item.y}" min="0" max="1" step="0.01">`)}
          ${field("Size", `<input type="range" data-clay-field="size" value="${item.size}" min="0.1" max="1" step="0.01">`)}
          ${field("Opacity", `<input type="range" data-clay-field="opacity" value="${item.opacity}" min="0" max="1" step="0.05">`)}
        </div>
        ${["pip", "bubble"].includes(item.mode) ? `<label class="show-field"><input type="checkbox" data-clay-field="avoidFace" ${item.avoidFace !== false ? "checked" : ""}> Move aside when it would cover your face</label>` : ""}
      </fieldset>`).join("") || "<p class=\"show-hint\">No clay placement reaches this scene.</p>"}
      <p class="show-hint">${story ? story.shots.map((shot) => `${escape(shot.title || "")}: ${shot.cues.map(([time, , cue]) => `${time.toFixed(2)} s ${escape(cue)}`).join(", ")}`).join(" · ") : ""}</p>
      <p class="show-hint" id="showClayStatus" role="status">The poses change on these word cues and hold still in between. Ask for “Preview with effects” to see it.</p>`;
    box.querySelectorAll("[data-clay-field]").forEach((input) => input.addEventListener("change", () => clayChange(scene, input)));
  }

  function clayChange(scene, input) {
    const index = Number(input.closest("[data-clay]").dataset.clay);
    const item = clay.placements[index];
    const name = input.dataset.clayField;
    item[name] = name === "enabled" || name === "avoidFace" ? input.checked : name === "mode" ? input.value : Number(input.value);
    clearTimeout(claySaving);
    $("#showClayStatus").textContent = "Saving…";
    claySaving = setTimeout(async () => {
      try {
        clay = await api("/show/clay/placements", { method: "PUT", body: JSON.stringify({ revision: clay.revision, placements: clay.placements }) });
        $("#showClayStatus").textContent = "Saved in the project. A new preview with effects is being prepared.";
        invalidateLocal(scene);
        prepareInBackground(scene);
        if (name === "mode" || name === "start" || name === "end") clayBlock(scene);
      } catch (error) {
        $("#showClayStatus").textContent = `Not saved: ${error.message}`;
        clayBlock(scene);
      }
    }, 350);
  }

  // A scene opened without a chosen view prepares its preview with effects on its own
  // (and the next scene's), small and quick, and shows it when ready; until then its cut plays.
  function autoPreview(scene) {
    const preview = scene.preview || { status: "none" };
    if (wantEffects.has(scene.id) || wantQuick.has(scene.id) || wantSaved.has(scene.id)) return;
    if (preview.status === "failed" || preview.status === "stopped") return;
    wantEffects.add(scene.id);
    if (preview.status === "ready") return;
    const index = scenes().indexOf(scene);
    const next = scenes().slice(index + 1, index + 2).map((item) => item.id);
    scene.preview = { status: "queued", message: "Preparing the preview with effects (about half a minute)" };
    api(`/show/scenes/${encodeURIComponent(scene.id)}/preview`, { method: "POST", body: JSON.stringify({ next }) })
      .then((answer) => { if (answer?.status === "ready") refreshBoard(); }).catch(() => {});
  }

  // After edits the live view stays; the new preview is prepared in the background meanwhile.
  let backgroundPreview = null;
  function prepareInBackground(scene) {
    clearTimeout(backgroundPreview);
    const stamp=editStamp;
    backgroundPreview = setTimeout(async () => {
      await flushEdits().catch(() => {});
      if(stamp!==editStamp)return;
      await api(`/show/scenes/${encodeURIComponent(scene.id)}/preview`, { method: "POST", body: "{}" }).catch(() => {});
      refreshLivePreview(scene,stamp);
    }, 1000);
  }

  async function refreshLivePreview(scene,stamp) {
    if(stamp!==editStamp||currentScene()?.id!==scene.id)return;
    const fresh=await api(`/show/scenes?current=${encodeURIComponent(scene.id)}`).catch(()=>null);
    if(stamp!==editStamp||currentScene()?.id!==scene.id)return;
    const updated=fresh?.scenes?.find(entry=>entry.id===scene.id);
    if(updated){scene=currentScene();scene.preview=updated.preview;}       // the current object, not a copy from before a board reload
    editorEffectsLabel();
    if(scene.preview?.status==='ready'){
      if(!timelineDialog?.open)$('#showStageStatus').textContent='Effects preview ready. Press Play scene to watch your current edit.'+leftOut(scene.preview);
      return;
    }
    if(['failed','stopped'].includes(scene.preview?.status)){
      $('#showStageStatus').textContent=`Effects preview failed: ${scene.preview.error||'try Generate scene preview again'}`;return;
    }
    backgroundPreview=setTimeout(()=>refreshLivePreview(scene,stamp),1200);
  }

  function showScenePicture(scene) {
    autoPreview(scene);
    const preview = scene.preview || { status: "none" };
    if (!wantEffects.has(scene.id)) {
      if (!playQuick(scene)) showVideo("", "This scene has no picture to play.");
      return;
    }
    if (preview.status === "ready") { showVideo(preview.url, "Current scene preview (low quality, to be quick). Watch it, then accept or change it." + leftOut(preview)); return; }
    playQuick(scene); // Keep the source picture usable while the new effect is prepared.
    $("#showStageStatus").textContent = ["failed", "stopped"].includes(preview.status)
      ? `The preview is not ready: ${preview.error || "Stopped; generate it again to retry."}`
      : `${preview.message || "Queued: preparing this scene and its drawings"}. Showing the source cut until it is ready.`;
    if (!["failed", "stopped"].includes(preview.status)) poll = setTimeout(refreshBoard, 1500);
  }

  // ---- Quick view: the scene's cut played from the original, nothing rendered ----------
  let quick = null;
  function stopQuick() { if (quick) { cancelAnimationFrame(quick.frame); quick = null; } }
  function playQuick(scene, from = 0, onEnd = null, withEffects = false) {
    stopQuick();
    const saved = useSaved(scene);
    // In the scene editor, the scene's own preview with every effect, when it is ready for what the timeline holds.
    const effects = !saved && withEffects && effectsReady(scene);
    // In the saved render the scene sits later by what was inserted before it (title card, intro, characters).
    const later = (t) => (review.saved_render?.inserted || []).reduce((sum, [at, length]) => sum + (at <= t ? length : 0), 0);
    const segments = saved ? [[scene.start + later(scene.start), scene.end + later(scene.start)]]
      : effects ? [[0, scene.end - scene.start+(scene.preview.inserted||[]).reduce((n,[a,d])=>n+d,0)]] : (scene.segments?.length ? scene.segments : [[scene.start, scene.end]]);
    const source = saved ? review.saved_render.url : effects ? scene.preview.url : review.source_url;
    if (!source || !segments.length) {
      $('#showStageStatus').textContent = 'Cannot play: the original video is missing. Relink the project video to continue.';
      showToast('The original video is missing from this project.', true); return false;
    }
    const video = $("#showStageVideo");
    $("#showStageCard").hidden = true;
    video.hidden = false;
    if (video.getAttribute("src") !== source) { video.src = source; video.load(); }
    quick = { scene, segments, saved: saved || effects, effects, index: 0, frame: 0, pending:scene.start+from };
    const session=quick;
    liveDrawings(scene);
    video.controls=false;
    let transport=$('#showSceneTransport');
    if(!transport){transport=document.createElement('div');transport.id='showSceneTransport';transport.className='show-scene-transport';transport.innerHTML='<button type="button" data-scene-play>Play scene</button><output data-scene-clock></output>';$('#showStageStatus').after(transport);transport.querySelector('button').onclick=()=>playScene();}
    transport.hidden=false;
    editorEffectsLabel();
    const seek = (index, offset = 0) => { if(quick!==session)return;quick.index = index;quick.pending=scene.start+(effects?ShowCaptionClock.unshift(offset,scene.preview.inserted||[]):segments.slice(0,index).reduce((sum,[a,b])=>sum+b-a,0)+offset);video.currentTime = segments[index][0] + offset;syncPlayback(); };
    const start = () => { if (quick===session) { const [index, offset] = sourceAt(segments, effects?ShowCaptionClock.shift(from,scene.preview.inserted||[]):from); seek(index, offset); } };
    if (video.readyState >= 1) start(); else video.addEventListener("loadedmetadata", start, { once: true });
    const tick = () => {
      if (quick !== session) return;
      // duration is zero before metadata arrives. It is not the end of the scene:
      // treating it as an end cancels a cold Play request before the file can load.
      if(video.readyState < 1){quick.frame=requestAnimationFrame(tick);return;}
      if(!video.seeking){
        const actual=segments.findIndex(([a,b])=>a<=video.currentTime && video.currentTime<b);
        if(actual>=0)quick.index=actual;
      }
      const end = Math.min(segments[quick.index][1], Number.isFinite(video.duration) ? video.duration : Infinity);
      if (!video.seeking && (video.ended || video.currentTime >= end - 0.04)) {
        if (quick.index + 1 < segments.length) seek(quick.index + 1);          // skip what was cut
        else if (onEnd) { stopQuick(); onEnd(); return; }
        else { video.pause(); if(video.currentTime>end+.04)seek(segments.length-1,Math.max(0,end-segments.at(-1)[0]-.04)); }
      }
      syncPlayback();
      quick.frame = requestAnimationFrame(tick);
    };
    quick.frame = requestAnimationFrame(tick);
    $("#showStageStatus").textContent = effects ? "Playing with every effect (this scene's preview). Pause to move or resize pictures." + leftOut(scene.preview)
      : saved ? "Saved render: the last completed edit, with its captions and effects. New decisions need a new effects preview/render."
      : timelineDialog?.open ? "Live view: your cut and chosen pictures, updated as you edit. Press Play scene to watch it with every effect."
      : "Live timing preview: your cut and chosen pictures. Pictures appear only inside their bars; render a preview to check all camera and animation effects.";
    return true;
  }

  // ---- The scene editor plays the real effects --------------------------------------------
  // Play shows the scene's preview with every effect (words, zooms, animations, painted
  // drawings, steam…) when it is ready for what the timeline holds; pausing returns to the
  // live view, where pictures can be dragged. An edit shows at once in the live view while
  // the effects are prepared again in the background (seconds: the scene's cut is kept).
  let editorPoll = null, editorAsked = null, editorAskedAt = 0, editStamp = 0;
  // What a ready preview could not show yet (an effect needing settings, a drawing the painter could not make).
  const leftOut = (preview) => preview?.left_out?.length ? ` Not in it yet: ${preview.left_out.join("; ")}.` : "";      // editStamp: an answer asked before an edit is old news
  const failedPreviewURLs=new Set();
  const effectsReady = (scene) => !!(scene && scene.preview?.status === "ready" && scene.preview.url && !failedPreviewURLs.has(scene.preview.url));
  function editorEffectsLabel() {
    const transport = $("#showSceneTransport"), scene = currentScene();
    if (!transport || !scene) return;
    let label = transport.querySelector("[data-effects-state]");
    if (!label) { label = document.createElement("small"); label.dataset.effectsState = ""; transport.append(label); }
    const preview = scene.preview || {};
    label.hidden = !timelineDialog?.open;
    label.textContent = quick?.effects ? "With every effect" : effectsReady(scene) ? "Effects ready: press Play scene"
      : preview.status === "failed" ? `Effects preview failed: ${preview.error || "try again"}`
      : !["queued","rendering"].includes(preview.status) ? "Original video available; effects preview has not been rendered"
      : `Updating the effects… ${preview.message && /%/.test(preview.message) ? preview.message.replace(/^.*?(\d+%).*$/, "$1") : ""}`.trim();
  }
  function playScene() {
    const scene = currentScene(), video = $("#showStageVideo");
    if (!video) return;
    if (!scene) {
      if(video.hidden || !video.getAttribute('src')){showToast('This is a still title card. Open the intro or a scene to play video.');return;}
      if(!video.paused)video.pause();else video.play().catch(error=>showToast(`Cannot play: ${error.message}`,true));
      return;
    }
    if (!video.paused) {
      const at=editedPlayhead(),editing=quick?.effects&&timelineDialog?.open;
      video.pause();
      if(editing)playQuick(scene,Math.max(0,Math.min(scene.end-scene.start-.04,at-scene.start)));
      return;
    }
    let at = Math.max(scene.start, Math.min(scene.end, editedPlayhead()));
    if (at >= scene.end - 0.05) at = scene.start;
    if (!quick || quick.scene.id !== scene.id || !video.getAttribute('src') || video.error || at === scene.start || (effectsReady(scene) && !quick.effects)) {
      if(!playQuick(scene, at-scene.start, null, effectsReady(scene) && !video.error))return;
    }
    video.play().catch(error => {
      if(error.name === 'AbortError')return; // A new seek/source replaces the previous play request.
      $('#showStageStatus').textContent = `Cannot play this video: ${error.message}. Check that its file is still available.`;
      showToast($('#showStageStatus').textContent, true);
    });
  }
  function stepVideo(direction, count=1) {
    const video=$('#showStageVideo'), scene=currentScene();
    if(!scene && (!video || video.hidden || !video.getAttribute('src')))return;
    if(scene && !quick && !playQuick(scene))return;
    video.pause();
    const fps=quick?.saved?30:(Number(review.fps || review.plan?.fps) || 30);
    video.currentTime=Math.max(0,Math.min(Number.isFinite(video.duration)?video.duration:Infinity,video.currentTime+direction*count/fps));
    syncPlayback();
  }
  $('#showStageVideo').addEventListener('error', () => {
    const video=$('#showStageVideo'), scene=currentScene();
    if(!scene)return;
    const wasPreview=video.getAttribute('src') !== review.source_url;
    if(wasPreview && review.source_url) {
      failedPreviewURLs.add(video.getAttribute('src'));
      wantQuick.add(scene.id);wantEffects.delete(scene.id);
      playQuick(scene);
      $('#showStageStatus').textContent='The effects preview could not be loaded. Play is available on the original video; regenerate the preview to retry.';
    } else $('#showStageStatus').textContent='Cannot play: the original file is missing or this video format is not supported. Relink or convert the source video.';
  });
  $('#showStageVideo').addEventListener('playing', () => {
    if($('#showStageStatus').textContent.startsWith('Buffering'))$('#showStageStatus').textContent=quick?.effects?'Playing the effects preview.':'Playing the original video. Effects can continue preparing in the background.';
  });
  $('#showStageVideo').addEventListener('waiting', () => {
    $('#showStageStatus').textContent='Buffering the video… Playback will continue when more frames arrive.';
  });
  async function watchEditorEffects(scene) {
    clearTimeout(editorPoll);
    // The board may have been reloaded meanwhile (refreshBoard): always the current scene object,
    // or a ready preview would be written on an old copy and Play scene would never see it.
    scene = scene && currentScene()?.id === scene.id ? currentScene() : null;
    if (!timelineDialog?.open || !scene) return;
    editorEffectsLabel();
    if (effectsReady(scene)) return;
    // Asked when the editor opened or after an edit (2 s later); asked again if nothing started.
    if (!["queued", "rendering", "failed"].includes(scene.preview?.status) && (editorAsked !== scene.id || Date.now() - editorAskedAt > 6000)) {
      editorAsked = scene.id; editorAskedAt = Date.now();
      await flushEdits().catch(() => {});
      await api(`/show/scenes/${encodeURIComponent(scene.id)}/preview`, { method: "POST", body: "{}" }).catch(() => {});
    }
    const asked = editStamp;
    const fresh = await api(`/show/scenes?current=${encodeURIComponent(scene.id)}`).catch(() => null);
    const updated = fresh?.scenes?.find((entry) => entry.id === scene.id);
    if (updated && asked === editStamp && currentScene()?.id === scene.id && !editQueue.pending.size) { scene = currentScene(); scene.preview = updated.preview; }
    if (effectsReady(scene)) {
      editorAsked = null;
      // Already playing the live view: carry on, now with the effects.
      const video = $("#showStageVideo");
      if (quick && !quick.saved && !video.paused) { playQuick(scene, editedPlayhead() - scene.start, null, true); video.play().catch(() => {}); }
      editorEffectsLabel();
      return;
    }
    if (scene.preview?.status === "failed") { editorAsked = null; editorEffectsLabel(); return; }
    editorPoll = setTimeout(() => watchEditorEffects(scene), 1200);
  }
  $("#showStageVideo").addEventListener("pause", () => {
    // load() also queues pause. Only an explicit Pause command may switch back
    // to the editable source; a delayed event from the old source must not do it.
    syncPlayback();
  });
  // Seconds into the scene's edit → (segment, offset in it).
  const sourceAt=ShowClock.sourceAt;

  async function refreshBoard() {
    const scene = currentScene();
    if (!scene) return;
    const fresh = await api(`/show/scenes?current=${encodeURIComponent(scene.id)}`).catch(() => null);
    if (!fresh) return;
    review = fresh;
    const updated = currentScene();
    if (!updated || !wantEffects.has(updated.id)) return;
    if (["ready", "failed", "stopped"].includes(updated.preview?.status)) renderScene();
    else {
      $("#showStageStatus").textContent = `${updated.preview?.message || "Queued: preparing this scene and its drawings"}. Showing the source cut until it is ready.`;
      poll = setTimeout(refreshBoard, 1500);
    }
    $("#showSteps").querySelectorAll("[data-show-step]").forEach((button) => button.classList.toggle("active", Number(button.dataset.showStep) === step));
  }

  $("#showStageVideo").addEventListener("loadeddata", () => {
    const scene = currentScene();
    if (!scene || (!wantEffects.has(scene.id)&&!quick?.effects) || scene.preview?.status !== "ready") return;
    if ($("#showStageVideo").getAttribute("src") !== scene.preview.url) return;
    viewedScenes.add(scene.id);
    dirtyScenes.delete(scene.id);
    $("#showPanel").querySelectorAll('[data-show-status="approved"]').forEach(button => {
      const item=review.proposals.find(item=>item.id===button.dataset.id);
      button.disabled=!!item?.needs?.length || (item?.kind==='drawing' && !!item.params.subject && !item.params.picture);
    });
    $("#showPanel").querySelectorAll('[data-scene-decision="accepted"]').forEach(button => button.disabled = false);
  });

  function invalidateLocal(scene) {
    if (!scene) return;
    editStamp++;
    dirtyScenes.add(scene.id); viewedScenes.delete(scene.id);
    scene.preview = {status:"none"};
    wantEffects.delete(scene.id); wantQuick.add(scene.id);
    if (currentScene()?.id === scene.id) {
      clearTimeout(poll);
      $("#showPanel").querySelectorAll('[data-scene-decision="accepted"]').forEach(button => button.disabled = true);
      $("#showStageStatus").textContent = quick && !quick.saved ? "Changes saved. Live picture timing is updated; a new preview with effects is being prepared — press “Preview with effects” to watch it." : "Changes saved. This rendered video is now out of date; a new preview is being prepared — open the scene editor for live picture timing meanwhile.";
    }
    prepareInBackground(scene);
    if (timelineDialog?.open && currentScene()?.id === scene.id) {
      editorAsked = scene.id; editorAskedAt = Date.now();            // prepareInBackground asks for it
      if (quick?.effects) {
        const video = $("#showStageVideo"), at = editedPlayhead(), playing = !video.paused;
        playQuick(scene, Math.max(0, at - scene.start));
        if (playing) video.play().catch(() => {});
      }
      watchEditorEffects(scene);
    }
  }

  async function requestScenePreview(scene) {
    await flushEdits();
    wantQuick.delete(scene.id); wantEffects.add(scene.id);
    if (scene.preview?.status !== "ready") {
      try {
        await api(`/show/scenes/${encodeURIComponent(scene.id)}/preview`, {method:"POST",body:"{}"});
        scene.preview = {status:"queued"};
      } catch(error) {
        scene.preview={status:"failed",error:error.message};
        wantEffects.delete(scene.id);wantQuick.add(scene.id);
        renderScene();
        $('#showStageStatus').textContent=`Your edits are saved. Effects preview failed: ${error.message}. Play is available on the original video.`;
        showToast($('#showStageStatus').textContent,true);return false;
      }
    }
    renderScene();
  }

  async function decideScene(decision, thenNext = false) {
    await flushEdits();
    const scene = currentScene();
    if (decision === "accepted" && !viewedScenes.has(scene.id)) throw new Error("Generate and watch this scene preview before accepting it.");
    const updated = await api(`/show/scenes/${encodeURIComponent(scene.id)}/decision`, { method: "POST", body: JSON.stringify({ decision }) });
    if (updated.drafts) showToast(`${updated.drafts} drawing${updated.drafts > 1 ? "s are" : " is"} being drawn as drafts. They appear in the scene's preview; approve each one when you have seen it.`);
    Object.assign(scene, updated, { preview: { status: "none" } });
    if (thenNext && !updated.drafts) step = Math.min(stepNames().length - 1, step + 1);   // stay to see the drafts
    const next = currentScene();
    review = await api(`/show/scenes?current=${encodeURIComponent((next || scene).id)}`);
    render();
    watchDrafts();
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
      url = wantEffects.has(scene.id) && scene.preview?.status === "ready" ? scene.preview.url : "";
      if (!url) {                                   // the cut itself, at once, from the original video
        if (!playQuick(scene, 0, playNext)) { watching.timer = setTimeout(playNext, 500); return; }
        $("#showStageStatus").textContent = `${scene.name} · ${DECISION_LABELS[scene.decision]} · its cut, without effects · ${where}`;
        video.play().catch(() => { $("#showStageStatus").textContent = `${label}: press play to continue.`; });
        return;
      }
    }
    showVideo(url, `${label} · ${where}`);
    video.play().catch(() => { $("#showStageStatus").textContent = `${label}: press play to continue.`; });
  }

  $("#showStageVideo").addEventListener("ended", () => { if (watching) playNext(); });

  async function markModified(proposalId = null) {
    const scene = proposalId ? scenes().find(scene => scene.effects.includes(proposalId)) : currentScene();
    invalidateLocal(scene);
    if (scene && scene.decision !== "modified") {
      await api(`/show/scenes/${encodeURIComponent(scene.id)}/decision`, { method: "POST", body: JSON.stringify({ decision: "modified" }) }).catch(() => {});
      scene.decision = "modified";
      if (currentScene()?.id === scene.id) $("#showPanel .show-decide .show-decision").textContent = DECISION_LABELS.modified;
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
      if (kind === "drawing") {
        ShowEinstein.open(scene,{text:scene.text,start:body.at,end:Math.min(scene.end,body.at+3)},form.elements.subject.value,null,{style:form.elements.style.value});
        return;
      }
      if(await extraTool(kind,scene,{text:scene.text,start:body.at,end:Math.min(scene.end,body.at+3)}))return;
      await addEffect(scene, body);
    };
  }

  // Speech captions (subtitles of everything said) are off unless the person turns them on.
  async function setCaptions(on) {
    const result = await api("/show/captions/enabled", { method: "POST", body: JSON.stringify({ enabled: on }) });
    const fresh = await api(`/show/scenes?current=${encodeURIComponent(currentScene()?.id || "")}`);
    review.captions_enabled = result.enabled; review.caption_revision = fresh.caption_revision;
    for (const scene of scenes()) scene.captions = fresh.scenes.find((entry) => entry.id === scene.id)?.captions || [];
    renderTimeline(currentScene()); syncPlayback();
    showToast(on ? "Speech captions on: they follow what you say. Drag one on the video to move it; turn them off in the scene editor." : "Speech captions off. Your drawn words and comments stay.");
  }
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-captions-on]") : null;
    if (!button || !review) return;
    button.disabled = true;
    setCaptions(button.dataset.captionsOn === "1").catch((error) => { button.disabled = false; showToast(error.message, true); });
  });

  async function extraTool(kind,scene,picked) {
    if(kind.startsWith('original_')){
      const mode=kind==='original_mouth'?'mouth':'eyes';
      const selected=timelineItem(selectedEffect),p=selected?.params||{};
      const placement={sceneId:scene.id,at:picked.start,duration:picked.end-picked.start,action:mode==='eyes'?'look':'talk',x:0,y:0,width:100,
        proposalId:selected&&!selected.overlay&&selected.kind==='cutaway'?selected.id:null,
        fromProposal:selected&&!selected.overlay&&selected.kind==='character'?selected.id:null};
      if(selected?.overlay){await ShowPhotoStudio.rigPicture(selected.overlay.asset,{mode,placement:{...placement,replaceId:selected.overlay.id}});return true;}
      if(p.pixel_parts?.asset){await ShowPhotoStudio.rigPicture(p.pixel_parts.asset,{mode,placement});return true;}
      if(p.picture){await ShowPhotoStudio.rigPicture(p.picture,{mode,placement:{...placement,width:30}});return true;}
      const [index,offset]=sourceAt(scene.segments,picked.start-scene.start);
      const source=p.source||review.source,second=p.source?(p.still_time??p.source_start??0):scene.segments[index][0]+offset;
      await ShowPhotoStudio.rigFrame(source,second,{mode,placement});return true;
    }

    if(kind==="video_cutout"||kind==="video_silhouette"){await addBackground(scene,picked,kind==="video_silhouette"?"silhouette":"person");return true;}
    if(kind.startsWith("texture_")){await addEffect(scene,{kind:"texture",texture:kind.slice(8),at:picked.start,duration:Math.max(.2,picked.end-picked.start)});return true;}
    if(kind==='music_library'){await MusicLibrary.open(true);return true;}
    if(kind==='local_media'){
      if(window.pywebview?.api?.pick_videos){
        const paths=await window.pywebview.api.pick_videos();
        for(const path of paths||[])await importLocalMedia({name:path.split(/[\\/]/).pop()},true,path,picked);
        return true;
      }
      const input=document.createElement('input');input.type='file';input.accept='video/*,audio/*';
      input.onchange=()=>{if(input.files[0])importLocalMedia(input.files[0],true,null,picked).catch(error=>showToast(error.message,true));};input.click();return true;
    }
    if(kind==='library'){await openLibrary(null,picked);return true;}
    if(kind==='lettering'){ShowEinstein.open(scene,picked,'',null,{purpose:'lettering',letteringText:picked.text,style:'marker'});return true;}
    if(kind.startsWith('warp_')){await addEffect(scene,{kind:'warp',warp:kind.slice(5),at:picked.start,duration:Math.max(1.5,picked.end-picked.start)});return true;}
    if(kind==='face_clip'){
      const clips=sceneEffects(scene).filter(item=>item.kind==='clip'&&item.status!=='discarded');
      if(!clips.length){showToast('First put a clip on these words: Video clip from a link.');ShowReferences.open(scene,picked,review.project_id,'video');return true;}
      const near=clips.reduce((best,item)=>Math.abs(item.at-picked.start)<Math.abs(best.at-picked.start)?item:best);
      await openLibrary(null,null,near.id);return true;
    }
    if(kind==='video_library'){ShowReferences.open(scene,picked,review.project_id,'library');return true;}
    if(["references","video_reference","generate_video"].includes(kind)){ShowReferences.open(scene,picked,review.project_id,kind==='references'?'photos':kind==='video_reference'?'video':'generate');return true;}
    if(kind==='photo_edit') {ShowEinstein.open(scene,picked,'A realistic photo of me with long hair',null,{purpose:'photo_edit'});return true;}
    if(kind==='cutout'||kind==='silhouette') {const [index,offset]=sourceAt(scene.segments,picked.start-scene.start);await ShowPhotoStudio.openAt(review.source,scene.segments[index][0]+offset,kind==='silhouette',{sceneId:scene.id,at:picked.start,duration:picked.end-picked.start});return true;}
    if(kind==='captions') {if(!review.captions_enabled)await setCaptions(true);enterLiveTiming(scene,picked.start);playQuick(scene,Math.max(0,picked.start-scene.start));openTimeline();showToast('Speech captions follow the words. Drag the caption on the video or resize its corner.');return true;}
    if(kind==='clay') { ShowClayEditor.open(scene,picked);return true; }
    return false;
  }

  async function addEffect(scene, body) {
    try {
      const item = await api("/show/review/add", { method: "POST", body: JSON.stringify(body) });
      review.proposals.push(item);
      scene.effects.push(item.id);
      scene.decision = "modified";
      invalidateLocal(scene);
      open.add(scene.id); open.add(item.id);
      renderScene();
      if (!(item.needs || []).length) await requestScenePreview(scene);
      return item;
    } catch (error) { showToast(error.message, true); return null; }
  }

  // ---- "What you say": click a word to go there; select words, right-click to animate them --
  function saidSelection() {
    const said = $("#showSaid .show-said-text");
    const selection = window.getSelection();
    if (!said || !selection || selection.isCollapsed || !said.contains(selection.anchorNode)) return null;
    const spans = [...said.querySelectorAll("[data-word]")].filter((span) => selection.containsNode(span, true));
    if (!spans.length) return null;
    return { text: selection.toString().trim().replace(/\s+/g, " ").slice(0, 80),
      start: Number(spans[0].dataset.start), end: Number(spans[spans.length - 1].dataset.end) };
  }

  function sayMenu(x, y, picked) {
    const scene = currentScene();
    let menu = $("#showSayMenu");
    if (!menu) {
      menu = document.createElement("div");
      menu.id = "showSayMenu";
      menu.className = "show-context-menu";
      menu.setAttribute("role", "menu");
    }
    // Inside the scene editor while it is open: an element on the page body sits under the modal.
    const home = timelineDialog?.open ? timelineDialog : document.body;
    if (menu.parentElement !== home) home.append(menu);
    const span = Math.max(0.5, picked.end - picked.start);
    const quoted = escape(picked.text.length > 34 ? `${picked.text.slice(0, 32)}…` : picked.text);
    const choices = [
      ...(selectedEffect ? [["place", "Place selected effect on these words", null]] : []),
      ["ai", "AI · create with Einstein…", null],
      ["library", "Take from the library…", null],
      ["background", "Change the background behind me…", null],
      ["original_eyes", "Move original eyes…", null],
      ["original_mouth", "Animate original mouth…", null],
      ["original_part", "Move original part (sideways)…", null],
      ["references", "Search internet references…", null],
      ["local_media", "Video / audio from this computer…", null], ["music_library", "Add audio · music / jingles…", null],
      ["video_reference", "Video clip from a link…", null],
      ["video_library", "Video cutouts library…", null],
      ["face_clip", "Put my face on a clip…", null],
      ["generate_video", "Create video · external prompt…", null],
      ["video_cutout", "Moving person · photo / video background…", null],
      ["video_silhouette", "Moving black silhouette…", null],
      ["photo_edit", "Realistic photo of me…", null],
      ["cutout", "Cut out a person / background (photo)…", null],
      ["silhouette", "Black silhouette (photo)…", null],
      ["clay", "Clay memory · Simón…", null],
      ["captions", "Speech captions (subtitles) · turn on & edit", null],
      ["drawing", `Draw “${quoted}”`, { kind: "drawing", subject: picked.text, style: "crayon", duration: Math.max(2, span) }],
      ["word", `Show “${quoted}” big`, { kind: "word", text: picked.text.toUpperCase().slice(0, 40), duration: Math.max(1.5, span) }],
      ["math", "Formulas around me here", { kind: "math", duration: Math.max(2, span) }],
      ...Object.entries(TEXTURES).map(([key,name])=>["texture_"+key, "Texture · "+name, null]),
      ["cutaway", "A close-up here…", null],
      ["character", "A character speaks here…", null],
    ];
    menu.innerHTML = `<strong>Create an animation over this selection</strong><small>${clock(picked.start)}–${clock(picked.end)}</small>
      ${[...new Set(choices.map(([key])=>mediaCategory(key)))].map(group=>`<details class="show-effect-group"><summary>${escape(group)}</summary><div class="show-actions-grid">${choices.filter(([key])=>mediaCategory(key)===group).map(([key,label])=>`<button type="button" data-say="${key}">${label}</button>`).join('')}</div></details>`).join('')}
      <label>Your drawing idea for this selection<textarea aria-label="Drawing idea for selected words" maxlength="2000" placeholder="For example: a clumsy hand-drawn sun peeking from the corner"></textarea></label>
      <small>Generate this proposal in the scene first. Watch the result before accepting it.</small>
      <div class="show-actions"><button type="button" data-say="idea">Create with Einstein…</button><button type="button" data-say="copy">Copy</button></div>`;
    menu.hidden = false;
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8))}px`;
    menu.onclick = async (event) => {
      const button = event.target instanceof Element ? event.target.closest("[data-say]") : null;
      if (!button) return;
      menu.hidden = true;
      const key = button.dataset.say;
      if (key === "background") { await addBackground(scene, picked); return; }
      if(await extraTool(key,scene,picked))return;
      if (key === "place") { await placeOnWords(picked); return; }
      if (key === "ai") { ShowEinstein.open(scene,picked); return; }
      if (key === "drawing") { ShowEinstein.open(scene,picked,`Draw ${picked.text}`); return; }
      if (key === "idea") {
        const input=menu.querySelector('textarea'),direction=input.value.trim();
        if(!direction){menu.hidden=false;input.focus();return;}
        ShowEinstein.open(scene,picked,direction);
        return;
      }
      if (key === "copy") { await navigator.clipboard?.writeText(picked.text).catch(() => {}); return; }
      const choice = choices.find(([name]) => name === key);
      if (choice[2]) { await addEffect(scene, { ...choice[2], at: picked.start }); return; }
      // A close-up or a character needs its video: chosen right here, without leaving the editor.
      const sources = [review.source, ...(review.resources || []).map((item) => item.path)].filter(Boolean);
      menu.innerHTML = `<strong>${key === "cutaway" ? "A close-up" : "A character that speaks"} at ${clock(picked.start)}</strong>
        <label>From video <select data-say-source>${sources.map((path) => `<option value="${escape(path)}">${escape(baseName(path))}</option>`).join("")}</select></label>
        <label>at second <input type="number" step="any" min="0" value="0" data-say-second></label>
        <div class="show-actions"><button type="button" class="primary" data-say-add>Add</button><button type="button" data-say-cancel>Cancel</button></div>`;
      menu.hidden = false;
      menu.querySelector("[data-say-cancel]").onclick = () => { menu.hidden = true; };
      menu.querySelector("[data-say-add]").onclick = async () => {
        const source = menu.querySelector("[data-say-source]").value, second = Number(menu.querySelector("[data-say-second]").value) || 0;
        menu.hidden = true;
        await addEffect(scene, key === "cutaway" ? { kind: "cutaway", at: picked.start, source, source_start: second } : { kind: "character", at: picked.start, source, still_time: second });
      };
    };
  }

  document.addEventListener('click',event=>{const button=event.target.closest?.('[data-clip-settings]');if(button&&review){const item=timelineItem(button.dataset.clipSettings);if(item)ShowReferences.openClip(currentScene(),item,review.project_id);}});
  document.addEventListener('click',event=>{const button=event.target.closest?.('[data-original-parts]');if(!button)return;const [partsId,partsMode]=button.dataset.originalParts.split(':');selectedEffect=partsId;const item=timelineItem(selectedEffect),scene=currentScene();
    // A "character speaks" opens on its mouth (record its voice there); every effect gets a visible duration, not its zero-length mark.
    extraTool(partsMode==='mouth'?'original_mouth':partsMode==='eyes'?'original_eyes':item.kind==='character'?'original_mouth':'original_eyes',scene,{text:scene.text,start:item.at,end:Math.min(scene.end,item.at+Math.max(3,item.duration||0))}).catch(error=>showToast(error.message,true));});
  // Add, at the playhead (the words being said there, else the next 3 seconds).
  function pickedHere(scene) {
    const at = Math.max(scene.start, Math.min(scene.end - 0.1, editedPlayhead()));
    const word = (scene.words || []).find(([, a, b]) => a <= at + 0.05 && at < b);
    return { text: word ? word[0] : "", start: at, end: Math.min(scene.end, word ? Math.max(word[2], at + 1.5) : at + 3) };
  }
  function addHere(x, y, direct = null) {
    const scene = currentScene();
    if (!scene) return;
    chooseAdd(direct, pickedHere(scene), x, y);
  }
  // One of the selection menu's choices, run at once (the menu's own logic, in the editor if open).
  function chooseAdd(key, picked, x, y) {
    sayMenu(x, y, picked);
    if (key) $("#showSayMenu").querySelector(`[data-say="${key}"]`)?.click();
  }
  const menuHost = () => (timelineDialog?.open ? timelineDialog : document.body);

  // ---- Right-click menus: like the system's, with submenus beside them (web/show_menu.js) ----------
  // Everything you can add, by kind: pictures, drawings & words, characters, video, effects.
  function addItems(scene, picked, x, y) {
    const run = (key) => () => (key === "background" ? addBackground(scene, picked) : key === "lettering" || key.startsWith("warp_") ? extraTool(key, scene, picked) : chooseAdd(key, picked, x, y));
    const entry = ([key, label]) => ({ label, action: run(key) });
    const quoted = picked.text ? `“${picked.text.length > 24 ? `${picked.text.slice(0, 22)}…` : picked.text}”` : "here";
    return [
      { label: "Pictures", submenu: [["library", "From the library…"], ["background", "Change the background behind me…"], ["references", "Search the internet…"], ["cutout", "Cut out a person / background…"],
        ["silhouette", "Black silhouette…"], ["photo_edit", "Realistic photo of me…"]].map(entry) },
      { label: "Drawings & words", submenu: [["ai", "Create with Einstein…"], ["drawing", `Draw ${quoted}…`], ["lettering", `Hand-letter ${quoted}…`],
        ["word", `Show ${quoted} big`]].map(entry) },
      { label: "Characters", submenu: [["character", "A character that speaks…"], ["original_eyes", "Move original eyes…"], ["original_mouth", "Animate original mouth…"],
        ["original_part", "Move original part (sideways)…"], ["clay", "Clay memory · Simón…"]].map(entry) },
      { label: "Video", submenu: [["local_media", "Video / audio from this computer…"], ["cutaway", "A close-up…"], ["video_reference", "Clip from a link…"], ["video_library", "Video cutouts library…"], ["face_clip", "Put my face on a clip…"],
        ["generate_video", "Create video · external prompt…"], ["video_cutout", "Moving person over a photo or video…"], ["video_silhouette", "Moving black silhouette…"]].map(entry) },
      { label: "Effects", submenu: [entry(["math", "Formulas around me"]), {label: "Textures",submenu:[{label:"Filmed texture from a link…",action:()=>ShowReferences.openTexture(scene,picked,review.project_id)},...Object.entries(TEXTURES).map(([key,name])=>({label:name,action:()=>extraTool("texture_"+key,scene,picked)}))]},
        { label: "Bend the picture", submenu: [["warp_wave", "Wave"], ["warp_swirl", "Swirl"], ["warp_stretch", "Stretch"]].map(entry) }] },
      { separator: true },
      entry(["captions", "Speech captions (subtitles)…"]),
    ];
  }
  // An effect's own tools, without leaving the scene editor.
  function followItems(item) {
    const anchor=item.overlay?.follow_anchor||item.params.follow_anchor;
    return [{label:anchor?'Mark following object again…':'Follow an object: mark it…',action:()=>{
      selectedEffect=item.id;steamBoxFor=item.id;locateEffect(timelineItem(item.id));drawOverlays(currentScene());
      showToast('At the effect start, draw a tight box around the hand or object. The picture will be attached to the marked object. Effects preview shows the tracked movement.');
    }},...(anchor?[{label:'Release at playhead · leave it here',action:()=>{const seconds=Math.max(0,editedPlayhead()-item.at);steamSave(item,{follow_seconds:seconds});showToast('Following stops here; the object stays at its last tracked position. Preview to check the placement.');}},{label:'Follow for the whole effect',action:()=>steamSave(item,{follow_seconds:null})},{label:'Stop following',action:()=>steamSave(item,{follow_anchor:null,follow_seconds:null})}]:[])];
  }
  function layerColourDialog(item){
    const dialog=document.createElement('dialog');dialog.className='show-layer-colours';
    dialog.innerHTML='<h2>Foreground colour & light</h2>'+[['brightness','Brightness',0,3,1],['saturation','Saturation',0,3,1],['hue','Hue',-180,180,0]].map(([name,label,min,max,value])=>`<label>${label}<input data-colour="${name}" type="range" min="${min}" max="${max}" step=".05" value="${item.params[name]??value}"></label>`).join('')+'<p>Applies to this foreground only. Preview the scene to see the result.</p><button data-apply>Apply & preview</button><button data-cancel>Cancel</button>';
    document.body.append(dialog);dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();dialog.showModal();
    dialog.querySelector('[data-apply]').onclick=async event=>{event.target.disabled=true;try{await patch(item.id,Object.fromEntries([...dialog.querySelectorAll('[data-colour]')].map(v=>[v.dataset.colour,Number(v.value)])));await markModified(item.id);dialog.close();invalidateLocal(currentScene());await requestScenePreview(currentScene());}catch(error){showToast(error.message,true);event.target.disabled=false;}};
  }
  function cutoutHandles(){
    const item=timelineItem(selectedEffect),p=item?.params;
    if(!item||item.status==='discarded'||isTexture(item)||!timelineDialog?.open||!(item.kind==='background'||item.kind==='clip'&&p.matte&&p.matte!=='none'))return '';
    const scale=p.person_scale??1,[x,y]=p.person_offset||[0,0];
    return `<div class="show-cutout-box" data-cutout-id="${item.id}" style="left:${((1-scale)/2+x)*100}%;top:${(1-scale+y)*100}%;width:${scale*100}%;height:${scale*100}%">${ShowGeometry.handles(p)}<small>Moving cutout · drag to move</small><span class="show-resize" data-cutout-resize="${item.id}"></span></div>`;
  }
  let geometryDrag=null;
  document.addEventListener('pointerdown',event=>{
    const host=event.target.closest?.('.show-overlay,.show-picture-placement,.show-cutout-box');
    if(!host||!$('#showStageLayer').classList.contains('show-editing'))return;
    const edge=event.target.closest('[data-crop-edge]')?.dataset.cropEdge,modified=event.metaKey||event.ctrlKey;
    if(!edge&&!modified)return;
    const scene=currentScene(),item=host.dataset.overlay!=null?timelineItem('sticker:'+scene.overlays[Number(host.dataset.overlay)].id):timelineItem(host.dataset.liveEffect||host.dataset.cutoutId);
    if(!item)return;
    event.preventDefault();event.stopImmediatePropagation();host.setPointerCapture(event.pointerId);
    const bounds=host.getBoundingClientRect(),initial=structuredClone(item.overlay||item.params),cx=bounds.left+bounds.width/2,cy=bounds.top+bounds.height/2;
    geometryDrag={host,item,initial,bounds,edge,modified,x:event.clientX,y:event.clientY,cx,cy,angle:Math.atan2(event.clientY-cy,event.clientX-cx),changes:{}};
  },true);
  document.addEventListener('pointermove',event=>{
    const d=geometryDrag;if(!d)return;event.preventDefault();event.stopImmediatePropagation();
    d.changes=ShowGeometry.change(d.initial,d.edge,(event.clientX-d.x)/d.bounds.width,(event.clientY-d.y)/d.bounds.height,d.modified,d.angle,Math.atan2(event.clientY-d.cy,event.clientX-d.cx));
    const updated={...d.initial,...d.changes},img=d.host.querySelector('img');if(img)img.style.cssText=shapeStyle(updated);
    d.host.querySelector('.show-crop-frame')?.remove();d.host.insertAdjacentHTML('beforeend',ShowGeometry.handles(updated));
  },true);
  document.addEventListener('pointerup',async event=>{
    const d=geometryDrag;if(!d)return;geometryDrag=null;event.preventDefault();event.stopImmediatePropagation();
    try{if(Object.keys(d.changes).length)await savePlacement(d.item,d.changes);drawOverlays(currentScene());}catch(error){drawOverlays(currentScene());showToast(error.message,true);}
  },true);
  document.addEventListener('pointercancel',()=>{geometryDrag=null;});
  let cutoutDrag=null;
  document.addEventListener('pointerdown',event=>{
    const box=event.target.closest?.('[data-cutout-id]');if(!box||steamBoxFor)return;
    const item=timelineItem(box.dataset.cutoutId);if(!item)return;
    event.preventDefault();event.stopPropagation();box.setPointerCapture(event.pointerId);
    cutoutDrag={item,box,bounds:$('#showStageLayer').getBoundingClientRect(),x:event.clientX,y:event.clientY,scale:item.params.person_scale??1,offset:[...(item.params.person_offset||[0,0])],resize:!!event.target.closest('[data-cutout-resize]')};
  },true);
  document.addEventListener('pointermove',event=>{
    if(!cutoutDrag)return;const d=cutoutDrag,dx=(event.clientX-d.x)/d.bounds.width,dy=(event.clientY-d.y)/d.bounds.height;
    const scale=d.resize?Math.max(.001,d.scale+dx*2):d.scale,offset=d.resize?d.offset:[d.offset[0]+dx,d.offset[1]+dy];
    d.changes={person_scale:scale,person_offset:offset};Object.assign(d.box.style,{left:((1-scale)/2+offset[0])*100+'%',top:(1-scale+offset[1])*100+'%',width:scale*100+'%',height:scale*100+'%'});
  });
  document.addEventListener('pointerup',async()=>{
    if(!cutoutDrag)return;const d=cutoutDrag;cutoutDrag=null;if(!d.changes)return;
    try{await patch(d.item.id,d.changes);await markModified(d.item.id);invalidateLocal(currentScene());drawOverlays(currentScene());prepareInBackground(currentScene());}catch(error){showToast(error.message,true);}
  });
  async function removePictureBackground(item) {
    const scene=currentScene(), path=item.overlay?.asset||item.params.picture;
    if(!path)return;
    showToast('Removing background…');
    try {
      const cut=await api('/show/library/pictures/cutout',{method:'POST',body:JSON.stringify({path,keep_canvas:true})});
      await loadLibrary();
      if(item.overlay){item.overlay.original_asset ||= path;await savePlacement(item,{asset:cut.path});}
      else await savePlacement(item,{picture:cut.path});
      drawOverlays(scene);invalidateLocal(scene);renderTimeline(scene);
      showToast('Background removed. The original stays in Library.');
    } catch(error){showToast('Could not remove background: '+error.message,true);}
  }

  // One entry point from both the picture and its timeline piece. Specialist
  // editors keep their behaviour; placement controls no longer live in separate menus.
  function editMedia(item) {
    if(!item)return;
    if(item.kind==='jingle'){ShowAudio.open(item.id);return;}
    selectedEffect=item.id;
    if(item.overlay)selectedOverlay=currentScene().overlays.indexOf(item.overlay);
    const p=item.overlay||item.params, isVideo=['clip','background'].includes(item.kind);
    if(!item.overlay&&!p.picture&&!isVideo){effectSettings(item.id);return;}
    const dialog=document.createElement('dialog');dialog.className='show-media-editor';
    const initial=structuredClone(p);
    const position=item.overlay?[p.x||0,p.y||0]:isVideo?(p.person_offset||[0,0]).map(v=>v*100):drawingPosition(item).map(v=>v*100);
    const size=item.overlay?p.width:isVideo?(p.person_scale??1)*100:(p.size??.25)*100;
    const number=(name,label,value,step='1')=>`<label>${label}<input type="number" data-media-field="${name}" value="${Number(value)}" step="${step}"></label>`;
    dialog.innerHTML=`<header><strong>Edit media · ${escape(p.name||p.subject||p.text||baseName(p.asset||p.picture||p.clip)||'Layer')}</strong><button type="button" data-media-close aria-label="Close media editor">×</button></header>
      <p>Drag the media on the paused video to position it. Drag its edges to crop, its corner to resize; Command / Ctrl-drag to rotate.</p>
      <div data-media-preview></div><div class="show-media-fields">${number('scale','Size (%)',size)}${number('x','Horizontal position (%)',position[0])}${number('y','Vertical position (%)',position[1])}${number('rotate','Rotation (°)',p.rotate||0)}${number('stretch_x','Horizontal stretch',p.stretch_x??1,'.1')}${number('stretch','Vertical stretch',p.stretch??1,'.1')}
      ${['Left','Top','Right','Bottom'].map((label,i)=>number('crop'+i,'Crop '+label+' (%)',(p.crop||[0,0,0,0])[i]*100)).join('')}</div>
      <div class="show-media-actions"><button type="button" data-media-place>Position on video</button><button type="button" data-media-more>Animation &amp; other tools…</button>${isVideo?'<button type="button" data-media-background>Person cutout / change background…</button>':'<button type="button" data-media-remove>Remove background</button>'}<button type="button" data-media-reset>Reset framing</button><button type="button" data-media-library>Save in library</button></div>
      <p data-media-status role="status">Changes apply to this piece only. The original stays in Library.</p><footer><button type="button" data-media-cancel>Cancel</button><button type="button" class="primary" data-media-save>Save changes</button></footer>`;
    document.body.append(dialog);dialog.showModal();
    let pending={},saved=false,preview=null;
    const status=dialog.querySelector('[data-media-status]');
    function read(){
      const v=Object.fromEntries([...dialog.querySelectorAll('[data-media-field]')].map(el=>[el.dataset.mediaField,Number(el.value)]));
      if(!Object.values(v).every(Number.isFinite)||v.scale<=0)throw Error('Choose a positive size and valid numbers.');
      const crop=[0,1,2,3].map(i=>v['crop'+i]/100);
      if(crop.some(c=>c<0)||crop[0]+crop[2]>.98||crop[1]+crop[3]>.98)throw Error('Leave at least 2% of the image visible.');
      return {crop,stretch_x:Math.max(.1,Math.min(10,v.stretch_x)),stretch:Math.max(.1,Math.min(10,v.stretch)),rotate:((v.rotate+180)%360+360)%360-180,...(item.overlay?{width:v.scale,x:v.x,y:v.y}:isVideo?{person_scale:v.scale/100,person_offset:[v.x/100,v.y/100]}:{size:v.scale/100,image_center:[v.x/100,v.y/100]})};
    }
    dialog.oninput=()=>{try{pending=read();preview?.draw();Object.assign(p,pending);drawOverlays(currentScene());status.textContent=isVideo?'Framing updated. Save, then preview the scene to check the moving video.':'Preview updated. Save changes to keep this framing.';}catch(e){status.textContent=e.message;}};
    const restore=()=>{for(const key of Object.keys(p))delete p[key];Object.assign(p,initial);drawOverlays(currentScene());};
    dialog.onclose=()=>{preview?.close();if(!saved)restore();dialog.remove();};
    if(item.kind!=='background' && (!isVideo || (p.matte||'none')==='none')) {
      preview=ShowMediaPreview.mount(dialog.querySelector('[data-media-preview]'),{
        src:isVideo?`/api/v1/show/clips/${encodeURIComponent(item.id)}/media`:`/api/v1/show/library/file?path=${encodeURIComponent(p.asset||p.picture)}`,video:isVideo,centered:!item.overlay&&!isVideo,
        read:()=>{const v=Object.fromEntries([...dialog.querySelectorAll('[data-media-field]')].map(el=>[el.dataset.mediaField,Number(el.value)]));return {...v,flip:!!p.flip,opacity:p.opacity??1,crop:[0,1,2,3].map(i=>v['crop'+i]/100)};},
        change:values=>{for(const el of dialog.querySelectorAll('[data-media-field]')){const key=el.dataset.mediaField;el.value=key.startsWith('crop')?values.crop[Number(key.slice(4))]*100:values[key];}pending=read();},
        onError:message=>status.textContent=message
      });
    }
    dialog.querySelector('[data-media-close]').onclick=()=>dialog.close();dialog.querySelector('[data-media-cancel]').onclick=()=>dialog.close();
    async function save(){pending=read();restore();await savePlacement(item,pending);saved=true;dialog.close();}
    dialog.querySelector('[data-media-save]').onclick=()=>save().catch(e=>status.textContent=e.message);
    dialog.querySelector('[data-media-reset]').onclick=()=>{const values={scale:item.overlay?100:isVideo?100:25,x:item.overlay?0:isVideo?0:50,y:item.overlay?0:isVideo?0:50,rotate:0,stretch_x:1,stretch:1,crop0:0,crop1:0,crop2:0,crop3:0};for(const input of dialog.querySelectorAll('[data-media-field]'))input.value=values[input.dataset.mediaField];dialog.oninput();};
    dialog.querySelector('[data-media-place]').onclick=()=>save().then(()=>locateEffect(timelineItem(item.id))).catch(e=>status.textContent=e.message);
    dialog.querySelector('[data-media-more]').onclick=()=>save().then(()=>{
      const r=$('#showStage').getBoundingClientRect();
      timelineMenu(r.left+20,r.top+20,item.id);
    }).catch(e=>showToast(e.message,true));
    dialog.querySelector('[data-media-library]').onclick=async()=>{
      try {
        pending=read();
        if(isVideo){if(item.kind==='background'){status.textContent='Use the moving-layers editor to export this composition; saving a background picture alone would lose your moving foreground.';return;}await save();await api('/show/clips/'+encodeURIComponent(item.id)+'/save-edit',{method:'POST',body:JSON.stringify({project_id:review.project_id})});await loadLibraryAll();showToast('Edited video saved in Library → Videos. The source and previous versions are kept.');}
        else {const result=await api('/show/library/pictures/edited-copy',{method:'POST',body:JSON.stringify({...p,path:p.asset||p.picture,...pending})});await loadLibrary();status.textContent='Edited copy saved in Library. Your original is kept.';}
      }catch(e){if(dialog.isConnected)status.textContent=e.message;else showToast('Could not save the library copy: '+e.message,true);}
    };
    dialog.querySelector('[data-media-remove]')?.addEventListener('click',()=>save().then(()=>removePictureBackground(timelineItem(item.id))).catch(e=>showToast(e.message,true)));
    dialog.querySelector('[data-media-background]')?.addEventListener('click',()=>save().then(()=>openVideoLayers(item.id,item.params.person_style||'person')).catch(e=>showToast(e.message,true)));
  }

  function mediaCategory(kind) {
    if(kind==='texture'||kind==='steam'||kind.startsWith('texture_'))return 'Textures & nature';
    if(['clip','local_media','video_reference','video_library','generate_video','references'].includes(kind))return 'Videos & references';
    if(['cutaway','background','video_cutout','video_silhouette','cutout','silhouette','photo_edit','face_clip'].includes(kind))return 'Camera, cutouts & backgrounds';
    if(['jingle','music_library','captions','word'].includes(kind))return 'Audio & words';
    return 'Drawings & animation';
  }
  function groupedEffects(items) {
    const groups=new Map();
    for(const item of items){const group=mediaCategory(item.kind);if(!groups.has(group))groups.set(group,[]);groups.get(group).push(item);}
    return [...groups].map(([name,items])=>`<details class="show-effect-group" open><summary>${escape(name)} · ${items.length}</summary>${items.map(card).join('')}</details>`).join('');
  }
  function groupAddOptions() {
    const select=$('#showSceneAdd select[name="kind"]');if(!select)return;
    const options=[...select.options],groups=new Map();select.replaceChildren();
    for(const option of options){const name=mediaCategory(option.value);if(!groups.has(name)){const group=document.createElement('optgroup');group.label=name;groups.set(name,group);select.append(group);}groups.get(name).append(option);}
  }

  function effectItems(scene, item) {
    if(item.overlay)return overlayItems(scene,item);
    if(item.clay)return [{label:'Clay settings…',action:()=>openClaySettings(item.clay)},
      {label:'Create a new clay story…',action:()=>ShowClayEditor.open(currentScene(),{start:item.at,end:item.at+item.duration,text:currentScene().text})},
      {label:'Remove effect',action:()=>removePiece(scene,item)}];
    const id = item.id;
    return [
      { label: "Edit media…", action: () => editMedia(item) },
      ...(item.params.picture?[{label:"Remove background",action:()=>removePictureBackground(item)}]:[]),
      ...((item.kind==='background'||item.kind==='clip'&&item.params.matte!=='none')?[{label:"Colour & light…",action:()=>layerColourDialog(item)}]:[]),
      ...((item.params.picture || item.kind==='clip' && item.params.matte!=='none')?followItems(item):[]),
      { label: "Show on the video and timeline", action: () => locateEffect(timelineItem(id)) },
      { label: "Choose its words…", action: () => { selectedEffect = id; placingWords = true; firstWord = null; pickedWords = null; closeTimeline(); wordPlacementControls(); $("#showSaid")?.scrollIntoView({ block: "nearest" }); } },
      { label: "Preview it", action: () => clickLike("data-show-preview", id) },
      ...(["cutaway", "character"].includes(item.kind) || item.params.picture ? [{ label: "Animate", submenu: () => proposalAnimationItems(scene,item) }] : []),
      ...(item.kind === "drawing" ? [{ label: "Choose from library…", action: () => openLibrary(id) }] : []),
      ...(item.kind === "background" ? [{ label: "Edit moving video layers…", action: () => openVideoLayers(id,item.params.person_style||"person") }, {label:"Background from library…", action:()=>openLibrary(id)}] : []),
      ...(["drawing", "word"].includes(item.kind) ? [{ label: "Movement", submenu: () => movementItems(scene, item) }] : []),
      ...(item.kind === "word" && !item.params.picture ? wordItems(item) : []),
      ...(["clip","jingle","character"].includes(item.kind)?[{label:"Sound mixer…",action:()=>ShowAudio.open(item.id)}]:[]),
      ...(item.kind === "clip" ? [{label:"Clip settings…",action:()=>ShowReferences.openClip(currentScene(),item,review.project_id)},
        { label: "Speed", submenu: () => CLIP_SPEEDS.map((pct) => ({ label: pct === 100 ? "100 % · normal" : `${pct} %${pct < 100 ? " · slow motion" : " · faster"}`,
          checked: Math.round((item.params.speed || 1) * 100) === pct, action: () => setClipSpeed(scene, item, pct / 100) })) },
        { label: "Put my face on it…", action: () => openLibrary(null, null, id) }] : []),
      { separator: true },
      { label: item.status === "discarded" ? "Restore effect" : "Remove effect", action: async () => { await patch(id, { status: item.status === "discarded" ? "proposed" : "discarded" }); await markModified(); renderScene(); } },
    ];
  }
  const MOVEMENT_STRENGTHS = [[.5,"Gentle"],[1,"Normal"],[1.5,"Strong"],[2,"Very strong"],[3,"Wild"]];
  async function pictureAsLayer(scene,item,action,intensity=item.params.intensity??1) {
    // Reuse the selected image, timing and position; retire its previous proposal.
    // The renderer already supports every native layer animation, including paths.
    const pos=drawingPosition(item);
    await addToScene(item.params.picture,{sceneId:scene.id,at:item.at,duration:item.duration,
      x:pos[0]*100,y:pos[1]*100,width:(item.params.size||.25)*100,action:'none'});
    const index=scene.overlays.length-1;scene.overlays[index].intensity=intensity;
    try {await patch(item.id,{status:'discarded'});} catch(error) {
      scene.overlays.splice(index,1);await saveOverlays(scene);throw error;
    }
    await markModified(item.id);
    selectedOverlay=index;selectedEffect='sticker:'+scene.overlays[index].id;
    await animateOverlay(scene,index,action);renderScene();
  }
  function proposalAnimationItems(scene,item) {
    const edit=mode=>()=>clickLike('data-original-parts',item.id+':'+mode);
    return [
      {label:'Move mouth…',action:edit('mouth')},{label:'Move eyes…',action:edit('eyes')},
      {label:'Mark another original part…',action:edit('eyes')},
      ...(item.params.picture?[{label:'Movement',submenu:Object.entries(ANIMATIONS).filter(([key])=>!['talk','look'].includes(key)).map(([key,label])=>({label,action:()=>pictureAsLayer(scene,item,key)}))}]:[]),
      {label:'Movement strength',submenu:MOVEMENT_STRENGTHS.map(([value,label])=>({label,checked:Number(item.params.intensity??1)===value,action:async()=>{await patch(item.id,{intensity:value});await markModified(item.id);invalidateLocal(scene);prepareInBackground(scene);}}))}
    ];
  }
  // A word's text: typeface (each shown in itself), bold, italic, size, colour.
  function wordItems(item) {
    const p = item.params;
    const set = (changes) => () => { Object.assign(p, changes); saveSoon(item.id, changes); liveDrawings(currentScene()); };
    return [
      { separator: true },
      { label: "Typeface", submenu: Object.entries(WORD_FONTS).map(([key, [label, family]]) => ({ label, style: `font-family:${family};font-size:15px`, checked: (p.font || "note") === key, action: set({ font: key }) })) },
      { label: "Bold", hint: "⌘B", checked: !!p.bold, action: set({ bold: !p.bold }) },
      { label: "Italic", hint: "⌘I", checked: !!p.italic, action: set({ italic: !p.italic }) },
      { label: "Size", submenu: [[0.15, "Small"], [0.25, "Medium"], [0.4, "Big"], [0.6, "Huge"], [0.9, "Whole screen"]].map(([size, label]) => ({ label, checked: Math.abs((p.size || 0.25) - size) < 0.01, action: set({ size }) })) },
      { label: "Colour", submenu: WORD_COLOURS.map(([colour, label]) => ({ label, style: `color:${colour === "#111111" ? "inherit" : colour}`, checked: (p.colour || "#fff4d6") === colour, action: set({ colour }) })) },
      { label: "Hand-letter it instead (Claude or ChatGPT)…", action: () => ShowEinstein.open(currentScene(), { text: p.text, start: item.at, end: item.at + item.duration }, "", item.id, { purpose: "lettering", letteringText: p.text, style: "marker" }) },
    ];
  }

  // Reuse a button's existing handler (the same code path as clicking it).
  function clickLike(attribute, value) {
    const button = document.createElement("button");
    button.type = "button"; button.hidden = true; button.setAttribute(attribute, value);
    if (attribute === "data-show-preview") button.dataset.id = value;
    menuHost().append(button); button.click(); button.remove();
  }
  function sceneMenu(x, y, ownId = null) {
    const scene = currentScene();
    if (!scene) return;
    const picked = pickedHere(scene);
    const own = ownId ? review.proposals.find((entry) => entry.id === ownId) : null;
    const effects = sceneEffects(scene);
    const name = (item) => item.params.subject || item.params.text || KIND_LABELS[item.kind] || item.kind;
    ShowMenu.open(x, y, [
      ...(own ? [{ heading: `${KIND_LABELS[own.kind] || own.kind} · ${name(own)}` }, ...pieceActions(scene,own), { separator: true }, ...effectItems(scene, own), { separator: true }] : []),
      { label: `Paste at playhead (${clock(editedPlayhead())})`, hint: "⌘V", disabled: !timelineClipboard, action: () => timelineCommand("paste", scene) },
      { label: "Undo", hint: "⌘Z", disabled: timelineCommandOff("undo", scene), action: () => timelineCommand("undo", scene) },
      { label: "Redo", hint: "⇧⌘Z", disabled: timelineCommandOff("redo", scene), action: () => timelineCommand("redo", scene) },
      { separator: true },
      { label: `Add at ${clock(picked.start)}`, submenu: () => addItems(scene, picked, x, y) },
      { label: "Effects in this scene", disabled: !effects.length, submenu: () => effects.map((item) => ({ label: `${KIND_LABELS[item.kind] || item.kind} · ${name(item)}${item.status === "discarded" ? " (removed)" : ""}`, submenu: () => effectItems(scene, item) })) },
      { label: "Speech captions", submenu: [{ label: "On", checked: !!review.captions_enabled, action: () => setCaptions(true) }, { label: "Off", checked: !review.captions_enabled, action: () => setCaptions(false) }] },
      { label: "Cuts and audio", submenu: [{label:"Sound mixer…",action:()=>ShowAudio.open()}, { label: "Add a close-up…", action: () => addHere(x, y, "cutaway") }, { label: "Open the video / audio cutting desk…", action: () => { window.location.href = "/show-studio/"; } }] },
      { separator: true },
      { label: "Generate scene preview", action: () => requestScenePreview(scene) },
    ], { host: menuHost() });
  }
  function overlayMenu(index, x, y) {
    const scene = currentScene();
    const overlay = scene?.overlays[index];
    if (!overlay) return;
    selectedOverlay = index; selectedEffect = "sticker:" + overlay.id;
    drawOverlays(scene); renderTimeline(scene);
    const item=timelineItem(selectedEffect);
    ShowMenu.open(x,y,[...pieceActions(scene,item),{separator:true},...effectItems(scene,item)],{host:menuHost()});
  }
  function overlayItems(scene,item) {
    const overlay=item.overlay,index=scene.overlays.indexOf(overlay);
    const stage=$('#showStage').getBoundingClientRect(),x=stage.left+20,y=stage.top+20;
    const talkable=isCharacter(overlay.asset);
    const select=()=>{selectedOverlay=index;selectedEffect=item.id;};
    const command=action=>{select();return layerCommand(action,scene);};
    const set=changes=>async()=>{select();await timelineEdit(scene,async()=>{Object.assign(overlay,changes);drawOverlays(scene);if(!await saveOverlays(scene))throw Error('Changes could not be saved');});};
    const save=async()=>{drawOverlays(scene);if(!await saveOverlays(scene))throw Error('Changes could not be saved');};
    const timing=(at,duration)=>set(fitTiming(scene,at,duration));
    return [
      {label:"Edit media…",action:()=>editMedia(timelineItem("sticker:"+overlay.id))},
      {label:"Remove background",action:()=>removePictureBackground(timelineItem("sticker:"+overlay.id))},
      ...followItems(timelineItem("sticker:"+overlay.id)),
      ...(overlay.action==='talk'?[{label:'Sound mixer…',action:()=>window.ShowAudio?.open(item.id)}]:[]),
      { label: "Animate", submenu: () => [
        {label:"Move mouth…",action:()=>command("mouth-regions")},
        {label:"Move eyes…",action:()=>command("regions")},
        {separator:true},
        ...Object.entries(ANIMATIONS).filter(([key]) => !["talk", "look"].includes(key) || talkable)
          .map(([key, label]) => ({ label, checked: (overlay.action || "none") === key, action: () => animateOverlay(scene, index, key) })),
        {separator:true},{label:"Movement strength",submenu:MOVEMENT_STRENGTHS.map(([value,label])=>({label,checked:Number(overlay.intensity??1)===value,action:set({intensity:value})}))}
      ] },
      ...(talkable && overlay.action === "talk" ? [{ label: "Voice", submenu: [
          { label: "Its own voice (you pause)", checked: overlay.voice_mode !== "mine", action: async () => { delete overlay.voice_mode; await save(); } },
          { label: "Your voice: it mouths what you say", checked: overlay.voice_mode === "mine", action: set({ voice_mode: "mine" }) }] }] : []),
      ...(talkable && overlay.action === "talk" && overlay.voice_mode !== "mine" ? [{ label: "Enters", submenu: [["cut", "Straight cut: talks at once"], ["zoom", "The camera pushes in on it"], ["bounce", "Rises from below with a jump"]]
          .map(([key, label]) => ({ label, checked: (overlay.entrance || "cut") === key, action: set({ entrance: key }) })) }] : []),

      { label: "Size and shape", submenu: [
          { label: "Bigger", hint: "+", action: () => command("bigger") }, { label: "Smaller", hint: "−", action: () => command("smaller") },
          { label: "Fill the frame", action: set({ x: 0, y: 0, width: 100 }) },
          { label: "Mirror", checked: !!overlay.flip, action: set({ flip: !overlay.flip }) },
          { label: "Turn", submenu: [-45, -15, 0, 15, 45, 90, 180].map((deg) => ({ label: `${deg}°`, checked: Number(overlay.rotate || 0) === deg, action: set({ rotate: deg }) })) },
          { label: "See-through", submenu: [1, 0.8, 0.6, 0.4, 0.2].map((value) => ({ label: `${Math.round(value * 100)} %`, checked: Number(overlay.opacity ?? 1) === value, action: set({ opacity: value }) })) },
          { label: "Reset shape", action: set({ stretch: 1, rotate: 0, flip: false, opacity: 1 }) },
          { separator: true }, { label: "Fine adjustments…", action: () => openTools(index, x, y) }] },
      { label: "Timing", submenu: [
          { label: "Starts at the playhead", action: () => timing(editedPlayhead(), overlay.duration)() },
          { label: "Ends at the playhead", disabled: editedPlayhead() <= overlay.at + 0.1, action: () => timing(overlay.at, editedPlayhead() - overlay.at)() },
          { label: "Lasts the whole scene", action: timing(scene.start, scene.end - scene.start) },
          { label: "Exact times…", action: () => openTools(index, x, y) }] },

      { separator: true },
      { label: "Preview it", action: () => clickLike("data-show-preview", "sticker:" + overlay.id) },
    ];
  }
  async function animateOverlay(scene, index, key) {
    const overlay = scene.overlays[index];
    if (!overlay) return;
    if (key === "path") {
      drawingPath = { index, points: [] };
      $("#showStageStatus").textContent = "Click the points of the path on the video; Enter or double-click to finish.";
      return;
    }
    await timelineEdit(scene,async()=>{
      overlay.action = key;
      delete overlay.path;
      if(!await saveOverlays(scene))throw Error('Changes could not be saved');
      drawOverlays(scene);
    });
  }
  // An effect's settings in a window over the scene editor (the editor stays open behind it).
  function effectSettings(id) {
    const current=timelineItem(id);if(current?.clay){openClaySettings(current.clay);return;}
    const item = review.proposals.find((entry) => entry.id === id);
    if (!item) return;
    let dialog = $("#showEffectSettings");
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "showEffectSettings";
      document.body.append(dialog);
      dialog.addEventListener("close", () => { const scene = currentScene(); if (scene) { drawOverlays(scene); renderTimeline(scene); } });
    }
    dialog.innerHTML = `<header><strong>${escape(KIND_LABELS[item.kind] || item.kind)} · ${escape(item.params.subject || item.params.text || "settings")}</strong><button type="button" aria-label="Close settings" data-close-settings>×</button></header><div class="show-editor" data-settings-host></div>`;
    dialog.querySelector("[data-close-settings]").onclick = () => dialog.close();
    dialog.showModal();
    mountEditor(id, dialog.querySelector("[data-settings-host]"));
  }

  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-editor-add]") : null;
    if (!button || !review) return;
    const box = button.getBoundingClientRect();
    addHere(box.left, box.bottom);
  });

  document.addEventListener('pointerdown',event=>{if(event.target.closest?.('#showSayTools'))event.preventDefault();});
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('#showSayTools');if(!button)return;
    const picked=saidSelection();if(!picked){showToast('Select some words first.');return;}
    const box=button.getBoundingClientRect();sayMenu(box.left,box.bottom,picked);
  });

  document.addEventListener("contextmenu", (event) => {
    if (!(event.target instanceof Element) || !event.target.closest("#showSaid")) return;
    const picked = saidSelection();
    if (!picked || !event.altKey) return;        // Native Copy/Select remains available; Alt-right-click opens animation tools.
    event.preventDefault();
    sayMenu(event.clientX, event.clientY, picked);
  });
  document.addEventListener("click", (event) => {
    const menu = $("#showSayMenu");
    if (menu && !menu.hidden && !(event.target instanceof Element && event.target.closest("#showSayMenu, #showSayTools, [data-editor-add], [data-effect-action]"))) menu.hidden = true;
    const word = event.target instanceof Element ? event.target.closest("#showSaid [data-word]") : null;
    const scene = currentScene();
    if (!word || !scene || !window.getSelection()?.isCollapsed) return;
    if (placingWords && selectedEffect) {
      const index=Number(word.dataset.word);
      if(firstWord===null) {firstWord=index; word.classList.add("placement-word"); wordPlacementControls();}
      else {
        const a=Math.min(firstWord,index), b=Math.max(firstWord,index), words=scene.words.slice(a,b+1);
        pickedWords={text:words.map(w=>w[0]).join(" "),start:words[0][1],end:words.at(-1)[2]};
        firstWord=null; wordPlacementControls(); highlightWords(pickedWords.start,pickedWords.end);
      }
      return;
    }
    if (wantEffects.has(scene.id)) { wantEffects.delete(scene.id); renderScene(); }
    playQuick(scene, Number(word.dataset.start) - scene.start);
    $("#showStageVideo").play().catch(() => {});
  });

  // Pictures placed on the scene: drag to move, corner to resize, right-click menu.
  // Stretch, turn, mirror and see-through, as the render applies them to the picture.
  function shapeStyle(overlay) {
    return ShowGeometry.style(overlay);
  }

  // ---- Steam on the stage: a cross where it rises, and the mug's box it follows ------------------
  let steamBoxFor = null, steamDrag = null;
  function steamTools(item) {
    const p = item.params;
    return `<div class="show-steam-tools" data-steam-tools="${item.id}"><button type="button" data-filmed-texture="${item.id}">Replace with filmed texture…</button><small>${p.anchor ? "Follows the marked object. Hides when lost and searches for it again, including after cuts. This is visual matching, not automatic cup recognition." : "Drag + to place the texture. Mark a box to follow an object; without a box the texture stays at its screen position."}</small>
      <button type="button" data-steam-box="${item.id}">${steamBoxFor === item.id ? "Drag a box around the object on the video…" : p.anchor ? "Mark the object again" : "Follow an object: mark it"}</button>${p.anchor ? `<button type="button" data-steam-unbox="${item.id}">Stop following</button>` : ""}
      ${`<div class="show-fields">${textureField(p)}${field("Density", range("density", p.density ?? 35, 5, 120, 1))}${field("Thickness", range("strength", p.strength ?? 0.3, 0.05, 0.9, 0.05))}${field("Height", range("rise", p.rise ?? 1, 0.3, 3, 0.05))}${field("Size", range("scale", p.scale ?? 1, 0.4, 2, 0.05))}${field("Colours", `<select data-field="colour"><option value="rainbow" ${(p.colour || "rainbow") === "rainbow" ? "selected" : ""}>Preset / rainbow</option><option value="white" ${p.colour === "white" ? "selected" : ""}>White</option><option value="${/^#/.test(p.colour || "") ? p.colour : "#ff66cc"}" ${/^#/.test(p.colour || "") ? "selected" : ""}>One colour</option></select>`)}</div>`}</div>`;
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('[data-filmed-texture]');if(!button)return;
    const item=timelineItem(button.dataset.filmedTexture);if(!item)return;
    ShowReferences.openTexture(currentScene(),{start:item.at,end:item.at+item.duration,text:currentScene().text},review.project_id,item.params.texture||'smoke',item.id);
  });
  function steamHandles(scene) {
    const item = timelineItem(selectedEffect);
    if (!item || !timelineDialog?.open || item.status==='discarded') return "";
    const [x, y] = item.params.point || [0.5, 0.5], a = item.overlay?.follow_anchor || item.params.follow_anchor || item.params.anchor;
    return `${a ? `<div class="show-steam-box" style="left:${a[0] * 100}%;top:${a[1] * 100}%;width:${a[2] * 100}%;height:${a[3] * 100}%"></div>` : ""}
      ${isTexture(item)?`<div class="show-steam-cross" data-steam-cross="${item.id}" style="left:${x * 100}%;top:${y * 100}%" title="Texture origin: drag it">+</div>`:""}`;
  }
  function steamSave(item, changes) {
    if(item.params?.texture_footage){if(changes.anchor!==undefined)changes.follow_anchor=changes.anchor;if(changes.point)changes.person_offset=[changes.point[0]-.5,changes.point[1]-1];}
    if(item.overlay){Object.assign(item.overlay,changes);saveOverlays(currentScene()).then(()=>prepareInBackground(currentScene()));}
    else {Object.assign(item.params, changes);saveSoon(item.id, changes);}
    drawOverlays(currentScene());
    renderTimeline(currentScene());
  }
  document.addEventListener("pointerdown", (event) => {
    if (!review || !(event.target instanceof Element)) return;
    const layer = $("#showStageLayer"), box = layer?.getBoundingClientRect();
    const cross = event.target.closest("[data-steam-cross]");
    const inside = box && event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
    if (!cross && !(steamBoxFor && inside)) return;
    event.preventDefault(); event.stopPropagation();
    const item = timelineItem(cross ? cross.dataset.steamCross : steamBoxFor);
    if(!item)return;
    const at = { x: (event.clientX - box.left) / box.width, y: (event.clientY - box.top) / box.height };
    const video=$('#showStageVideo');
    if(!cross){
      if(!video || video.readyState<2){showToast('Wait for the video picture, then mark the object again.',true);return;}
      video.pause();
    }
    steamDrag = { item, box, cross: !!cross, start: at, now: at,
      projectId:review.project_id,sceneId:currentScene()?.id,videoTime:video?.currentTime };
  }, true);
  document.addEventListener("pointermove", (event) => {
    if (!steamDrag) return;
    const { box } = steamDrag;
    steamDrag.now = { x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)), y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)) };
    const el = steamDrag.cross ? $(`[data-steam-cross="${CSS.escape(steamDrag.item.id)}"]`) : null;
    if (el) { el.style.left = `${steamDrag.now.x * 100}%`; el.style.top = `${steamDrag.now.y * 100}%`; }
    else {
      let rect = $("#showSteamDrawing");
      if (!rect) { rect = document.createElement("div"); rect.id = "showSteamDrawing"; rect.className = "show-steam-box"; $("#showStageLayer").append(rect); }
      const a = steamDrag.start, b = steamDrag.now;
      Object.assign(rect.style, { left: `${Math.min(a.x, b.x) * 100}%`, top: `${Math.min(a.y, b.y) * 100}%`, width: `${Math.abs(b.x - a.x) * 100}%`, height: `${Math.abs(b.y - a.y) * 100}%` });
    }
  });
  document.addEventListener("pointerup", async () => {
    if (!steamDrag) return;
    const { item, cross, start: a, now: b, projectId, sceneId, videoTime } = steamDrag;
    steamDrag = null;
    $('#showSteamDrawing')?.remove();
    if(review?.project_id!==projectId || currentScene()?.id!==sceneId){steamBoxFor=null;return;}
    if (cross) { steamSave(item, { point: [Number(b.x.toFixed(4)), Number(b.y.toFixed(4))] }); return; }
    steamBoxFor = null;
    const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y), w = Math.abs(b.x - a.x), h = Math.abs(b.y - a.y);
    if (w < 0.02 || h < 0.02) { drawOverlays(currentScene()); renderTimeline(currentScene()); return; }
    const video=$('#showStageVideo');
    if(video.readyState<2 || Math.abs(video.currentTime-videoTime)>.001){showToast('The video frame changed. Mark the object again on the paused frame.',true);return;}
    const canvas=document.createElement('canvas'),k=Math.min(1,512/(w*video.videoWidth),512/(h*video.videoHeight));
    canvas.width=Math.max(6,Math.round(w*video.videoWidth*k));canvas.height=Math.max(6,Math.round(h*video.videoHeight*k));
    canvas.getContext('2d').drawImage(video,x*video.videoWidth,y*video.videoHeight,w*video.videoWidth,h*video.videoHeight,0,0,canvas.width,canvas.height);
    try {
      const template=await api('/show/follow-template',{method:'POST',body:JSON.stringify({image:canvas.toDataURL('image/png')})});
      if(review?.project_id!==projectId || currentScene()?.id!==sceneId)return;
      const anchor=[x,y,w,h].map(v=>Number(v.toFixed(4)));
      if(isTexture(item)) steamSave(item,{anchor,point:[x+w/2,y],follow_template:template.path});
      else {
        // Marking means attach here, rather than keeping the old screen position.
        const element=item.overlay?$(`[data-overlay="${currentScene().overlays.indexOf(item.overlay)}"]`):null;
        const height=(element?.getBoundingClientRect().height||0)/$('#showStageLayer').getBoundingClientRect().height;
        const placement=item.overlay?{x:(x+w/2)*100-item.overlay.width/2,y:(y+h/2-height/2)*100,action:['talk','look'].includes(item.overlay.action)?item.overlay.action:'none',path:[],effect:'none'}:item.kind==='clip'
          ?{person_offset:[0,0],follow_place:true}:{image_center:[x+w/2,y+h/2],effect:'none'};
        steamSave(item,{follow_anchor:anchor,follow_template:template.path,follow_seconds:null,...placement});
      }
    } catch(error){showToast(error.message,true);return;}
    showToast('Attached to this object. Preview the effects to check the tracking across cuts.');
  });
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-steam-box],[data-steam-unbox]") : null;
    if (!target || !review) return;
    const item = review.proposals.find((entry) => entry.id === (target.dataset.steamBox || target.dataset.steamUnbox));
    if (!item) return;
    if (target.dataset.steamUnbox) { steamSave(item, { anchor: null }); return; }
    steamBoxFor = steamBoxFor === item.id ? null : item.id;
    renderTimeline(currentScene());
    if (steamBoxFor) { locateEffect(timelineItem(item.id)); $("#showStage").scrollIntoView({block:"center"}); showToast("At the effect start, drag a tight box around the visible object. Different appearances may need another mark."); }
  });
  document.addEventListener("input", (event) => {
    const host = event.target instanceof Element ? event.target.closest("[data-steam-tools]") : null;
    const name = event.target.dataset?.field;
    if (!host || !name) return;
    const item = review.proposals.find((entry) => entry.id === host.dataset.steamTools);
    if (!item) return;
    const value = ["colour","texture"].includes(name) ? event.target.value : Number(event.target.value);
    item.params[name] = value;
    saveSoon(item.id, { [name]: value, ...(item.params.texture_footage && name==='scale'?{person_scale:value*.4}:{}) });
  });

  function drawOverlays(scene) {
    const layer = $("#showStageLayer");
    layer.innerHTML = scene.overlays.map((overlay, index) => {
      const item = library.find((entry) => entry.path === overlay.asset);
      return `<div class="show-overlay ${selectedOverlay === index || selectedEffect==='sticker:'+overlay.id ? "selected" : ""}" data-overlay="${index}" style="left:${overlay.x}%;top:${overlay.y}%;width:${overlay.width}%">
        <img src="${item ? item.url : `/api/v1/show/library/file?path=${encodeURIComponent(overlay.asset)}`}" alt="" draggable="false" style="${shapeStyle(overlay)}">${ShowGeometry.handles(overlay)}<span class="show-resize" data-resize="${index}"></span></div>`;
    }).join("") + cutoutHandles() + steamHandles(scene);
    liveDrawings(scene);
    syncPlayback();
  }

  async function saveOverlays(scene) {
    try {
      const sent = JSON.stringify({ overlays: scene.overlays });
      const updated = await api(`/show/scenes/${encodeURIComponent(scene.id)}/overlays`, { method: "POST", body: sent });
      // Keep what the person changed while this save travelled; take the server's copy otherwise.
      if (JSON.stringify({ overlays: scene.overlays }) === sent) scene.overlays = updated.overlays;
      scene.decision = updated.decision;
      invalidateLocal(scene);
      renderTimeline(scene);wordPlacementControls();return true;
    } catch (error) { showToast(error.message, true);return false; }
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
    const stage = $("#showStage"); if(stage){stage.tabIndex=0;stage.focus({preventScroll:true});}
    dragging = { index, resize: target.hasAttribute("data-resize"), box, x: event.clientX, y: event.clientY, start: { ...scene.overlays[index] } };
  });
  const releasePointerTools=()=>{steamDrag=null;steamBoxFor=null;cutoutDrag=null;dragging=null;$('#showSteamDrawing')?.remove();if(review&&currentScene())drawOverlays(currentScene());};
  document.addEventListener('pointercancel',releasePointerTools);window.addEventListener('blur',releasePointerTools);
  document.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const scene = currentScene();
    const overlay = scene.overlays[dragging.index];
    const dx = (event.clientX - dragging.x) / dragging.box.width * 100, dy = (event.clientY - dragging.y) / dragging.box.height * 100;
    if (dragging.resize) overlay.width = Math.max(.1, dragging.start.width + dx);
    else { overlay.x = dragging.start.x + dx; overlay.y = dragging.start.y + dy; }
    drawOverlays(scene);
  });
  document.addEventListener("pointerup", (event) => {
    if (!dragging) return;
    const moved = Math.hypot(event.clientX - dragging.x, event.clientY - dragging.y) > 3;
    const index = dragging.index;
    dragging = null;
    if (moved) saveOverlays(currentScene());
    else editMedia(timelineItem("sticker:"+currentScene().overlays[index].id));     // a plain click shows the element's tools beside it
  });
  const ANIMATIONS = { none: "Still", up: "Move up", down: "Move down", jump: "Jump", grow: "Grow", grow_return: "Grow and back",
    pulse: "Grow and shrink", swing: "Swing", spin: "Spin", talk: "Talk", look: "Look left and right", path: "Follow a path", draw: "Draws itself" };
  let drawingPath = null;
  async function layerCommand(action, scene=currentScene()) {
    if(!scene)return;
    const overlay=scene.overlays[selectedOverlay];
    if(overlay)selectedEffect='sticker:'+overlay.id;
    if(action==='regions'||action==='mouth-regions'){if(overlay)await window.ShowPhotoStudio.rigPicture(overlay.asset,{placement:{sceneId:scene.id,replaceId:overlay.id},mode:action==='mouth-regions'?'mouth':'eyes'});return;}
    if(action==='bigger'||action==='smaller'){
      if(!overlay)return;
      await timelineEdit(scene,async()=>{overlay.width=Math.max(.01,overlay.width*(action==='bigger'?1.2:1/1.2));drawOverlays(scene);if(!await saveOverlays(scene))throw Error('Size could not be saved');});return;
    }
    return timelineCommand(action==='remove'?'delete':action,scene);
  }

  document.addEventListener('show-voice-ready', event => {
    const id = event.target.dataset.voiceEffect;
    const item = review?.proposals.find(item => item.id === id);
    if (!item) return;
    item.params.voices = [...(item.params.voices || []), event.detail];
    item.params.voice = event.detail;
    const select = event.target.parentElement.querySelector('[data-field="voice"]');
    if (select) { const option = document.createElement('option'); option.value = item.params.voices.length-1; option.textContent=event.detail.text; select.append(option);select.value=option.value; }
    saveSoon(id, {voice:event.detail});
  });

  // A talking character interrupts the host: how it comes in.
  function showEntrance(overlay, talkable) {
    // A talking character either speaks with its own recorded voice (you pause), or mouths yours.
    $("#showVoiceModeField").hidden = !(talkable && overlay.action === "talk");
    $("#showVoiceMode").value = overlay.voice_mode === "mine" ? "mine" : "own";
    $("#showEntranceField").hidden = !(talkable && overlay.action === "talk" && overlay.voice_mode !== "mine");
    $("#showEntrance").value = overlay.entrance || "cut";
  }

  function isCharacter(asset) { return /[\\/]Characters[\\/][^\\/]+[\\/]cutout\.png$/.test(String(asset || "")); }

  function openTools(index, x, y) {
    const scene = currentScene();
    const overlay = scene.overlays[index];
    selectedOverlay = index;
    const stage = $("#showStage"); if(stage){stage.tabIndex=0;stage.focus({preventScroll:true});}
    const menu = $("#showContextMenu");
    (timelineDialog?.open?timelineDialog:document.body).append(menu);
    const talkable = isCharacter(overlay.asset);
    $("#showActionButtons").innerHTML = Object.entries(ANIMATIONS)
      .filter(([key]) => !["talk", "look"].includes(key) || talkable)
      .map(([key, label]) => `<button type="button" data-animate="${key}" class="${(overlay.action || "none") === key ? "active" : ""}">${label}</button>`).join("");
    $("#showIntensity").value = overlay.intensity ?? 1;
    showEntrance(overlay, talkable);
    $("#showOverlayAt").value = Number(overlay.at ?? scene.start).toFixed(1);
    $("#showOverlayStretch").value = overlay.stretch ?? 1;
    $("#showOverlayRotate").value = overlay.rotate ?? 0;
    $("#showOverlayOpacity").value = overlay.opacity ?? 1;
    $("#showOverlayDuration").value = Number(overlay.duration ?? 4).toFixed(1);
    menu.classList.toggle("show-menu-left", x + 600 > innerWidth);
    menu.querySelector('[data-menu="regions"]').hidden = false;
    menu.querySelector('[data-menu="paste"]').disabled = !timelineClipboard;
    menu.querySelector('[data-menu="undo"]').disabled = timelineCommandOff('undo',scene);
    menu.hidden = false;
    // Beside the element, but always whole on screen (an element low in the frame opened it off the bottom).
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8))}px`;
  }

  document.addEventListener('keydown', event => {
    if (event.target.closest?.('input,textarea,select,[contenteditable="true"]')) return;
    if (!event.target.closest?.('#showStage,#showContextMenu')) return;
    if (!(event.ctrlKey || event.metaKey)) return;
    const action = event.key.toLowerCase()==='z'?(event.shiftKey?'redo':'undo'):{c:'copy',x:'cut',v:'paste',d:'duplicate',y:'redo',b:'split'}[event.key.toLowerCase()];
    if(action && (selectedOverlay!=null || ['paste','undo','redo'].includes(action))){event.preventDefault();layerCommand(action).catch(error=>showToast(error.message,true));}
  });

  document.addEventListener("contextmenu", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-overlay]") : null;
    if (!target || !review) return;
    event.preventDefault();
    overlayMenu(Number(target.dataset.overlay), event.clientX, event.clientY);
  });

  document.addEventListener("change", (event) => {
    if (!(event.target instanceof Element) || !event.target.closest("#showContextMenu")) return;
    const scene = currentScene();
    const overlay = scene && selectedOverlay != null ? scene.overlays[selectedOverlay] : null;
    if (!overlay) return;
    if (event.target.id === "showIntensity") overlay.intensity = Number(event.target.value);
    if (event.target.id === "showEntrance") overlay.entrance = event.target.value;
    if (event.target.id === "showVoiceMode") { overlay.voice_mode = event.target.value; showEntrance(overlay, isCharacter(overlay.asset)); }
    if (event.target.id === "showOverlayAt") Object.assign(overlay,fitTiming(scene,Number(event.target.value),overlay.duration));
    if (event.target.id === "showOverlayDuration") Object.assign(overlay,fitTiming(scene,overlay.at,Number(event.target.value)));
    // One save after the person stops adjusting (saves sent one after another overwrote each other).
    clearTimeout(shapeSave);
    shapeSave = setTimeout(() => saveOverlays(scene), 400);
  });
  let shapeSave = null;
  // Shape sliders change the picture on the stage as they move; saved when let go.
  document.addEventListener("input", (event) => {
    if (!(event.target instanceof Element) || !["showOverlayStretch", "showOverlayRotate", "showOverlayOpacity"].includes(event.target.id)) return;
    const scene = currentScene();
    const overlay = scene && selectedOverlay != null ? scene.overlays[selectedOverlay] : null;
    if (!overlay) return;
    const key = { showOverlayStretch: "stretch", showOverlayRotate: "rotate", showOverlayOpacity: "opacity" }[event.target.id];
    overlay[key] = Number(event.target.value);
    const image = $(`[data-overlay="${selectedOverlay}"] img`);
    if (image) image.style.cssText = shapeStyle(overlay);
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
      <div id="showStudioEffects" class="show-studio-effects" aria-live="polite"></div>
      <button type="button" id="showRenderNow" class="primary icon-button">Render the show</button>`;
    showVideo("", "The final render joins the title card, the intro and every scene with what you accepted.");
    studioEffects();
  }

  const STUDIO_STATUS = {
    applied: "Drawn in the render", disabled: "Off or hidden in Show Studio", superseded: "The automatic edit's version is kept",
    failed: "Could not be drawn", unsupported: "Not drawn by the render yet",
  };

  // What the render does with each Show Studio effect, so nothing is replaced or dropped silently.
  async function studioEffects() {
    const box = $("#showStudioEffects");
    if (!box) return;
    const data = await api("/show/studio-effects").catch(() => null);
    if (!data || !data.effects.length) { box.innerHTML = ""; return; }
    const rows = [];
    const captions = new Map();
    for (const item of data.effects) {
      if (item.type === "caption") {
        const key = `${item.section}|${item.status}|${item.detail}`;
        captions.set(key, { ...item, count: (captions.get(key)?.count || 0) + 1 });
      } else rows.push(item);
    }
    for (const item of captions.values()) rows.push({ ...item, name: `Captions · ${item.count}` });
    const order = ["failed", "unsupported", "applied", "disabled", "superseded"];
    rows.sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
    box.innerHTML = `<h3>Show Studio edits</h3>
      <ul class="show-studio-list">${rows.map((item) => `<li class="studio-${item.status}${item.warning ? " studio-warning" : ""}">
        <strong>${escape(item.name)}</strong> <span class="show-hint">${escape(item.section)}</span>
        <span class="studio-status">${escape(STUDIO_STATUS[item.status] || item.status)}</span>
        <span class="show-hint">${escape(item.detail)}</span></li>`).join("")}</ul>`;
  }

  // ---- Effects (inside "Modify") ------------------------------------------------

  function card(item) {
    const scene=currentScene();
    const outside=scene && (item.at<scene.start || item.at>=scene.end || (item.duration>0 && item.at+item.duration<=scene.start));
    const needs = (item.needs || []).length ? `<p class="show-needs">Still needed: ${escape(item.needs.join(", "))}</p>` : "";
    const confidence = item.kind === "cutaway" ? `<span class="show-confidence" title="How clearly the picture matches what is said">match ${(item.confidence * 100).toFixed(1)}</span>` : "";
    // A drawing is seen before it is approved: drawn as a draft (by Claude), then approved or drawn again.
    const drawable = item.kind === "drawing" && item.params.subject && ["drawing", "character", undefined].includes(item.params.kind);
    const drawing = item.drafting?.status === "drawing", unseen = drawable && !item.params.picture;
    const draft = !drawable ? "" : drawing ? `<p class="show-hint">Being drawn… it appears here and in the preview when it is ready.</p>`
      : item.drafting?.status === "failed" ? `<p class="show-needs">Could not be drawn: ${escape(item.drafting.error || "try again")}</p>`
      : unseen ? `<p class="show-hint">Not drawn yet: draw it to see it before approving it.</p>`
      : item.params.draft && item.status !== "approved" ? `<p class="show-hint">${String(item.params.image_engine || "").startsWith("Your library") ? "From your library: approve it, choose another one, or draw a new one." : "Draft: approve it to keep it in the video, or draw another."}</p>` : "";
    return `<article class="show-card show-${item.status}" data-show-id="${item.id}">
      <header><time>${clock(item.at)}</time><span class="show-kind">${item.params.reaction ? "Reaction" : (KIND_LABELS[item.kind] || item.kind)}</span>${confidence}<span class="show-status">${STATUS_LABELS[item.status]}</span></header>
      <div class="show-card-body">
        <img src="${picture(item)}" alt="" loading="lazy">
        <div>
          <p class="show-reason">${escape(item.reason)}</p>${item.kind === "clip" ? `<p class="show-hint" data-face-status="${item.id}">${item.params.face_frames ? `Your face is on it (${item.params.face_frames[0]} of ${item.params.face_frames[1]} frames).` : item.params.clip_audio===false?"Picture only; your narration continues.":item.params.audio_mode==='mix'||item.params.matte&&item.params.matte!=='none'?"Clip sound mixed with your narration. Open Sound mixer to balance levels.":"Plays with its own sound while you pause."}</p>` : ""}${item.kind === "drawing" ? `<p class="show-hint">Image source: ${escape(item.params.picture ? (item.params.image_engine || "Your library") : "No picture selected")}</p>` : ""}${draft}${needs}${outside?'<p class="show-needs">Outside this scene. Choose words to bring this effect back.</p>':''}
          <div class="show-actions">
            ${drawable ? `<button type="button" class="icon-button small" data-draw-it="${item.id}" ${drawing ? "disabled" : ""} ${item.params.picture ? 'data-again="1"' : ""}>${drawing ? "Drawing…" : item.params.picture ? "Draw another" : "Draw it"}</button>` : ""}
            ${["drawing","word"].includes(item.kind) ? `<button type="button" class="icon-button small" data-ai-edit="${item.id}">${item.kind==='word'?'Draw lettering…':'AI · change image…'}</button>` : ""}
            ${item.kind === "drawing" ? `<button type="button" class="icon-button small" data-open-library="${item.id}">Choose from library</button>` : ""}
            ${item.kind === "background" ? `<button type="button" class="icon-button small" data-video-layers="${item.id}">Edit moving video layers</button><button type="button" class="icon-button small" data-open-library="${item.id}">Background from library</button>` : ""}
            ${item.kind === "clip" ? `<select data-clip-sound="${item.id}" aria-label="Clip sound"><option value="own" ${item.params.clip_audio === false || item.params.audio_mode === "mix" ? "" : "selected"}>Its own sound (you pause)</option><option value="mine" ${item.params.clip_audio === false ? "selected" : ""}>Your voice goes on (picture only)</option><option value="mix" ${item.params.audio_mode === "mix" && item.params.clip_audio!==false ? "selected" : ""}>Mix with narration</option></select>` : ""}
            ${item.kind === "clip" ? `<button type="button" data-clip-settings="${item.id}">Clip settings…</button><button type="button" class="icon-button small" data-face-clip="${item.id}">Put my face on it</button>${item.params.clip_original ? `<button type="button" class="icon-button small" data-face-original="${item.id}">Use the original clip</button>` : ""}` : ""}
            ${["cutaway","character"].includes(item.kind)||item.params.picture ? `<button type="button" class="icon-button small" data-original-parts="${item.id}">Animate original eyes / mouth…</button>` : ""}
            <button type="button" class="icon-button small" data-place-words="${item.id}">Choose words</button>
            <button type="button" class="icon-button small" data-locate-effect="${item.id}">Show on video & timeline</button>
            <button type="button" class="icon-button small" data-show-preview="${item.id}">Preview in scene</button>
            <button type="button" class="icon-button small" data-show-adjust="${item.id}">${open.has(item.id) ? "Close" : "Adjust"}</button>
            <button type="button" class="icon-button small" data-show-status="approved" data-id="${item.id}" ${(item.needs || []).length || outside || unseen ? "disabled" : ""} title="${unseen ? "Draw it first: you approve the drawing you have seen" : "Keep this effect for the render. Review the full scene separately."}">Approve</button>
            <button type="button" class="icon-button small" data-show-status="discarded" data-id="${item.id}">Discard</button>
          </div>
        </div>
      </div>
      ${drawable && (item.status !== "approved" || !item.params.picture) ? `<div class="show-lib-suggest" data-suggest-for="${item.id}"></div>` : ""}
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
    const editorState=editors.get(id);if(editorState){editorState.item=item;editorState.draw();}
  }

  let saveTimer=null,lastSaveError='';
  const editQueue=new ShowEditQueue(async(id,changes)=>{
    await patch(id,changes);refreshCard(id);await markModified(id);
  });
  function showSaveStatus(){
    const box=$('#showSaveStatus');if(!box)return;
    box.hidden=!lastSaveError;
    box.innerHTML=lastSaveError?`${escape(lastSaveError)} <button type="button" data-retry-show-save>Retry saving</button>`:'';
  }
  async function flushEdits(){
    clearTimeout(saveTimer);
    try{await editQueue.flush();lastSaveError='';}
    catch(error){lastSaveError=error.message;throw error;}
    finally{showSaveStatus();}
  }
  function saveSoon(id,changes){
    editQueue.enqueue(id,changes);invalidateLocal(currentScene());
    clearTimeout(saveTimer);
    saveTimer=setTimeout(()=>flushEdits().catch(error=>showToast(error.message,true)),350);
  }
  document.addEventListener('click',event=>{if(event.target.closest?.('[data-retry-show-save]'))flushEdits().catch(error=>showToast(error.message,true));});

  // ---- Editors -----------------------------------------------------------

  function field(label, html) { return `<label class="show-field"><span>${label}</span>${html}</label>`; }
  function number(name, value, step = "any", min = 0) { return `<input type="number" data-field="${name}" value="${Math.round(Number(value ?? 0) * 1000) / 1000}" step="${step}" min="${min}">`; }
  function range(name, value, min, max, step) { return `<input type="range" data-field="${name}" value="${Number(value ?? 0)}" min="${min}" max="${max}" step="${step}">`; }

  function mountEditor(id, hostOverride = null) {
    const item = review.proposals.find((entry) => entry.id === id);
    const host = hostOverride || $(`[data-show-id="${id}"] .show-editor`);
    if (!item || !host) return;
    host.hidden = false;
    const p = item.params;
    let html = `<p class="show-hint">Select words to place this effect, or drag its bar below the video. Drag either edge to change its duration.</p><details><summary>Exact timing (optional)</summary><div class="show-fields">${field("Starts at (s)", number("at", item.at))}${item.kind === "character" ? "" : field("Lasts (s)", number("duration", item.duration))}</div></details>`;
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
        ${field("Its voice", `<select data-field="voice"><option value="">— choose or record a voice —</option>${voices}</select>`)}<div data-voice-input data-voice-effect="${escape(item.id)}"></div>
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
        ${field("Size", range("size", p.size ?? 0.25, 0.02, 2, 0.01))}</div>`;
      if (item.kind === "word" && !p.picture) {
        html += `<div class="show-fields">${field("Typeface", `<select data-field="font">${Object.entries(WORD_FONTS).map(([key, [label, family]]) => `<option value="${key}" style="font-family:${family}" ${(p.font || "note") === key ? "selected" : ""}>${label}</option>`).join("")}</select>`)}
          ${field("Bold", `<input type="checkbox" data-field="bold" ${p.bold ? "checked" : ""}>`)}${field("Italic", `<input type="checkbox" data-field="italic" ${p.italic ? "checked" : ""}>`)}
          ${field("Colour", `<input type="color" data-field="colour" value="${/^#/.test(p.colour || "") ? p.colour : "#fff4d6"}">`)}</div>
          <p class="show-hint" style="${wordStyle(p)};font-size:28px;background:#0006;padding:6px 10px;border-radius:8px">${escape(p.text)}</p>`;
      }
      if (item.kind === "drawing") {
        html += field("Appears", `<select data-field="effect">${[["pop", "Pops in"], ["draw", "Draws itself, stroke by stroke"], ["rise", "Rises from below"], ["drop", "Drops from above"], ["none", "Just appears"]]
          .map(([key, label]) => `<option value="${key}" ${(p.effect || "pop") === key ? "selected" : ""}>${label}</option>`).join("")}</select>`);
        const pictures = library.filter((entry) => /\.(png|jpe?g|webp)$/i.test(entry.path));
        const current = p.picture && !pictures.some((entry) => entry.path === p.picture) ? `<option value="${escape(p.picture)}" selected>${escape(p.draft ? "This draft" : baseName(p.picture))}</option>` : "";
        html += field("Picture", `<select data-field="picture"><option value="">Choose a picture first</option>${current}${pictures.map((entry) => `<option value="${escape(entry.path)}" ${p.picture === entry.path ? "selected" : ""}>${escape(entry.name)}</option>`).join("")}</select>`);
      }
      if (item.kind === "drawing" && p.subject) {
        html += `<div class="show-fields">${field("Style", `<select data-field="style">${Object.entries(DRAWING_STYLES).map(([key, label]) => `<option value="${key}" ${(p.style || "pencil") === key ? "selected" : ""}>${label}</option>`).join("")}</select>`)}</div>
          ${field("Image request (for a separate generation)", `<textarea data-field="prompt" rows="3" maxlength="600"></textarea>`)}
          <p class="show-hint">Choose a library picture or use Einstein to create one, then select the result. Changing these request notes does not regenerate or replace an attached picture. Scene previews only use saved pictures.</p>
          <button type="button" class="icon-button small" data-preview-mode="effects">Preview saved pictures in scene</button>`;
      }
    } else if (item.kind === "warp") {
      html += field("Warp", `<select data-field="warp">${["stretch", "swirl", "wave"].map((name) => `<option ${p.warp === name ? "selected" : ""}>${name}</option>`).join("")}</select>`);
    } else if (isTexture(item)) {
      html += `<p class="show-hint">Click the texture origin (for smoke, the mug rim). Mark the object at the start of this effect to follow it. The tracker hides the texture on loss and searches for the same appearance again. Different angles may need a new mark.</p><canvas class="show-canvas" data-canvas="steam"></canvas>
        ${`<div class="show-fields">${textureField(p)}${field("Density", range("density", p.density ?? 35, 5, 120, 1))}${field("Thickness", range("strength", p.strength ?? 0.3, 0.05, 0.9, 0.05))}${field("Height", range("rise", p.rise ?? 1, 0.3, 3, 0.05))}${field("Size", range("scale", p.scale ?? 1, 0.4, 2, 0.05))}${field("Colours", `<select data-field="colour"><option value="rainbow" ${(p.colour || "rainbow") === "rainbow" ? "selected" : ""}>Preset / rainbow</option><option value="white" ${p.colour === "white" ? "selected" : ""}>White</option><option value="${/^#/.test(p.colour || "") ? p.colour : "#ff66cc"}" ${/^#/.test(p.colour || "") ? "selected" : ""}>One colour</option></select>`)}</div>`}`;
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
    let value = target.type === "checkbox" ? target.checked : target.type === "number" || target.type === "range" ? Number(target.value) : target.value;
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
    if ("duration" in changes) item.duration = changes.duration;
    if (name === "position") {changes.image_center = null;item.params.image_center=null;}
    if (name === "at" || name === "duration") {
      const timing=fitTiming(currentScene(),item.at,item.duration); Object.assign(item,timing); Object.assign(changes,timing,{selected_text:''}); renderTimeline(currentScene());
    }
    if ((name === "subject" || name === "style") && !item.params.prompt) {
      const host = $(`[data-show-id="${id}"] .show-editor`);
      if (host) fillPrompt(item, host);
    }
    const editorState=editors.get(id);if(editorState){editorState.item=item;editorState.draw();}
    liveDrawings(currentScene());
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
      if (kind === "steam" || kind === "texture") { this.drag = { start: p, steam: true }; this.canvas.setPointerCapture(event.pointerId); return; }
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

  // Older effect panels share the same wheel boundary as the cascading menus.
  document.addEventListener('wheel', event => {
    const menu=event.target.closest?.('.show-context-menu');
    if(!menu)return;
    let scroll=event.target;
    while(scroll!==menu && !(scroll.scrollHeight>scroll.clientHeight && /auto|scroll/.test(getComputedStyle(scroll).overflowY)))scroll=scroll.parentElement;
    event.preventDefault();event.stopPropagation();
    scroll.scrollTop+=event.deltaY*(event.deltaMode===1?24:event.deltaMode===2?scroll.clientHeight:1);
  },{capture:true,passive:false});

  // Pixels accumulate into deliberate steps. Tiny trackpad reversals are inertia, not a new seek.
  let wheelSeek={at:0,total:0,direction:0};
  document.addEventListener('wheel', event => {
    const stage=event.target.closest?.('#showStage'),video=stage?.querySelector('video');
    if (!video || video.hidden || event.target.closest?.('input,textarea,select,button') || !Number.isFinite(video.duration) || !video.duration || event.ctrlKey) return;
    event.preventDefault(); event.stopPropagation();
    const now=performance.now();
    if(now-wheelSeek.at>220)wheelSeek={at:now,total:0,direction:0};
    let delta=Math.abs(event.deltaY)>=Math.abs(event.deltaX)?event.deltaY:event.deltaX;
    delta*=event.deltaMode===1?32:event.deltaMode===2?240:1;
    if(!delta)return;
    wheelSeek.at=now;
    if(wheelSeek.direction && Math.sign(delta)!==wheelSeek.direction && Math.abs(delta)<12)return;
    wheelSeek.total+=delta;
    const steps=Math.trunc(wheelSeek.total/32);
    if(!steps)return;
    wheelSeek.direction=Math.sign(steps);wheelSeek.total-=steps*32;
    stepVideo(Math.sign(steps),Math.min(6,Math.abs(steps)));
  }, {passive:false});

  document.addEventListener("contextmenu", event => {
    if (!review || $("#showReviewBox")?.hidden || !(event.target instanceof Element)) return;
    if (event.target.closest("#showSaid, [data-overlay], canvas, input, textarea")) return;
    const scene = currentScene();
    if (!scene || !event.target.closest("#showStage, [data-show-id], [data-cutout-id], [data-time-clip], [data-track], [data-locate-effect], [data-scene-seek]")) return;
    event.preventDefault();
    const barId=event.target.closest("[data-time-clip]")?.dataset.timeClip || event.target.closest("[data-track]")?.dataset.track || event.target.closest("[data-locate-effect]")?.dataset.locateEffect;
    if(barId){timelineMenu(event.clientX,event.clientY,barId);return;}
    const ownId=event.target.closest("[data-cutout-id]")?.dataset.cutoutId || event.target.closest("[data-show-id]")?.dataset.showId || event.target.closest("[data-live-effect]")?.dataset.liveEffect;
    if(ownId?.startsWith('sticker:')){overlayMenu(currentScene().overlays.findIndex(overlay=>'sticker:'+overlay.id===ownId),event.clientX,event.clientY);return;}
    sceneMenu(event.clientX,event.clientY,ownId);return;
  });
  document.addEventListener("click", event => { const menu=$("#showEffectMenu"); if(menu && !event.target.closest?.("#showEffectMenu"))menu.hidden=true; });
  document.addEventListener("keydown", event => { if(event.key==="Escape"){ const menu=$("#showEffectMenu");if(menu)menu.hidden=true;} });

  // Drafts: a drawing is drawn on request (or when its scene is accepted), shown, then approved.
  let draftPoll = null;
  function watchDrafts() {
    clearTimeout(draftPoll);
    const scene = currentScene();
    if (!scene || !review.proposals.some((item) => scene.effects.includes(item.id) && item.drafting?.status === "drawing")) return;
    draftPoll = setTimeout(async () => {
      const fresh = await api(`/show/scenes?current=${encodeURIComponent(scene.id)}`).catch(() => null);
      if (fresh && currentScene()?.id === scene.id && !editQueue.pending.size) {
        const was = review.proposals.filter((item) => item.drafting?.status === "drawing").map((item) => item.id);
        review.proposals = fresh.proposals;
        if (was.some((id) => review.proposals.find((item) => item.id === id)?.drafting?.status !== "drawing")) renderScene();
      }
      watchDrafts();
    }, 3000);
  }
  document.addEventListener("click", async (event) => {
    const button = event.target.closest?.("[data-draw-it]");
    if (!button || !review) return;
    const item = review.proposals.find((entry) => entry.id === button.dataset.drawIt);
    if (!item) return;
    try {
      await flushEdits();
      await api(`/show/review/${encodeURIComponent(item.id)}/draft`, { method: "POST", body: JSON.stringify({ again: !!button.dataset.again }) });
      item.drafting = { status: "drawing" };
      renderScene();
      watchDrafts();
    } catch (error) { showToast(error.message, true); }
  });

  document.addEventListener('click',event=>{
    const button=event.target.closest?.('[data-ai-edit]');if(!button)return;
    const item=review.proposals.find(item=>item.id===button.dataset.aiEdit);
    const scene=currentScene();if(!item||!scene)return;
    ShowEinstein.open(scene,{text:item.params.selected_text||scene.text,start:item.at,end:Math.min(scene.end,item.at+item.duration)},item.kind==='word'?'Hand-painted lettering, like a spontaneous protest placard.':item.params.ai_direction || item.params.subject,item.id,
      {style:item.params.style,position:item.params.position,...(item.kind==='word'?{purpose:'lettering',letteringText:item.params.text}: {})});
  });

  document.addEventListener('click',async event=>{
    const target=event.target.closest?.('[data-toggle-retired],[data-library-retire],[data-remove-sticker]');if(!target)return;
    try{
      if(target.hasAttribute('data-toggle-retired')){showRetired=!showRetired;await loadLibrary();renderScene();}
      else if(target.hasAttribute('data-library-retire')){
        const item=library[Number(target.dataset.libraryRetire)];
        await api('/show/library/visibility',{method:'POST',body:JSON.stringify({path:item.path,retired:!item.retired})});
        await loadLibrary();renderScene();
      }else{
        const scene=currentScene(),item=timelineItem(target.dataset.removeSticker);if(!item?.overlay)return;
        scene.overlays=scene.overlays.filter(overlay=>overlay.id!==item.overlay.id);selectedEffect=null;
        await saveOverlays(scene);drawOverlays(scene);renderTimeline(scene);
      }
    }catch(error){showToast(error.message,true);}
  });

  // ---- Actions -----------------------------------------------------------------

  document.addEventListener("click", async (event) => {
    const menu = $("#showContextMenu");
    // The click that ends a plain press on an element is the one that opened its tools.
    if (menu && !menu.hidden && !(event.target instanceof Element && event.target.closest("#showContextMenu, [data-overlay]"))) menu.hidden = true;
    const target = event.target instanceof Element ? event.target.closest("[data-show-step], #showBack, #showNext, [data-scene-decision], [data-scene-modify], [data-library], [data-menu], [data-animate], #showRenderNow, [data-show-preview], [data-show-adjust], [data-show-status], [data-watch-all], [data-first-pending], [data-preview-mode]") : null;
    if (!target || !review) return;
    try {
      if (target.dataset.showStep) { step = Number(target.dataset.showStep); render(); }
      else if (target.id === "showBack") { step = Math.max(0, step - 1); render(); }
      else if (target.id === "showNext") { step = Math.min(stepNames().length - 1, step + 1); render(); }
      else if (target.dataset.sceneDecision) await decideScene(target.dataset.sceneDecision, target.hasAttribute("data-then-next"));
      else if (target.hasAttribute("data-watch-all")) watchAll();
      else if (target.dataset.previewMode) {
        const scene = currentScene();
        if (target.dataset.previewMode === "effects") { await requestScenePreview(scene); return; }
        else { wantEffects.delete(scene.id); if(target.dataset.previewMode === "quick") { wantQuick.add(scene.id); wantSaved.delete(scene.id); } else { wantQuick.delete(scene.id); wantSaved.add(scene.id); } }
        $("#showPanel").querySelectorAll("[data-preview-mode]").forEach((button) => button.classList.toggle("active", button === target));
        showScenePicture(scene);
      }
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
        const own = await projectCopy(library[Number(target.dataset.library)]);
        scene.overlays.push({ id:crypto.randomUUID(),asset: own.path, x: 8, y: 10, width: 22, at: scene.start, duration: Math.min(4, scene.end - scene.start), effect: "pop" });
        if (own !== library[Number(target.dataset.library)]) await loadLibrary();
        if(await saveOverlays(scene))locateEffect(timelineItems(scene).at(-1));
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
        menu.hidden = true;
        const scene = currentScene();
        const overlay = scene?.overlays[selectedOverlay];
        if (overlay && (target.dataset.menu === "flip" || target.dataset.menu === "reset")) {
          if (target.dataset.menu === "flip") overlay.flip = !overlay.flip;
          else Object.assign(overlay, { stretch: 1, rotate: 0, flip: false, opacity: 1 });
          menu.hidden = false;                                                  // keep the tools open
          drawOverlays(scene);
          await saveOverlays(scene);
          return;
        }
        await layerCommand(target.dataset.menu);
      } else if (target.id === "showRenderNow") {
        await flushEdits();
        target.disabled = true;
        const started = await api("/show/review/render", { method: "POST", body: "{}" });
        window.resumeAfterShowReview?.(started);
      } else if (target.dataset.showAdjust) {
        closeTimeline();
        open.add(currentScene().id);
        const id = target.dataset.showAdjust;
        if (open.has(id)) { open.delete(id); editors.delete(id); } else open.add(id);
        renderScene();
      } else if (target.dataset.showStatus) {
        await flushEdits();
        const owner = scenes().find((entry) => entry.effects.includes(target.dataset.id));
        if (timelineDialog?.open && owner) await timelineEdit(owner, () => patch(target.dataset.id, { status: target.dataset.showStatus }));
        else await patch(target.dataset.id, { status: target.dataset.showStatus });
        refreshCard(target.dataset.id);
        refreshSceneDrawings();
        if (target.dataset.showStatus === "approved") suggestions.clear();
        await markModified();
        renderTimeline(currentScene());liveDrawings(currentScene());
      } else if (target.dataset.showPreview) {
        await flushEdits();
        const item=timelineItem(target.dataset.showPreview);
        if(item.needs?.length)throw Error('Complete this effect first: '+item.needs.join(', '));
        target.disabled=true;target.textContent='Preparing effect preview…';
        const previewRoute=item.overlay?`/show/scenes/${currentScene().id}/stickers/${encodeURIComponent(item.overlay.id)}/preview`:`/show/review/${encodeURIComponent(item.id)}/preview`;
        const result=await api(previewRoute,{method:'POST',body:'{}'});
        showVideo(result.url,'This effect, rendered with a little context. Generate scene preview to review every effect together.');
        previewOrigin=result.origin??Math.max(0,item.at-1.5);
        $('#showStageLayer').querySelector('.show-picture-placement')?.remove();
        $('#showStageVideo').play().catch(()=>{});
      }
    } catch (error) {
      showToast(error.message, true);
      if (target.dataset.showPreview) target.textContent = "Preview";
    } finally {
      if (target.dataset.showPreview || target.id === "showRenderNow") target.disabled = false;
      if (target.dataset.showPreview) target.textContent=target.closest("#showTimeline")?"Render effect preview":"Preview in scene";
    }
  });

  document.addEventListener("keydown", event => {
    if(event.defaultPrevented || !review || $('#showReviewBox')?.hidden || event.isComposing)return;
    const target=event.target;
    if(target.closest?.('input,textarea,select,[contenteditable="true"],.zmenu,canvas'))return;
    const modal=target.closest?.('dialog');
    if(modal && modal!==timelineDialog)return;
    if(event.metaKey || event.ctrlKey || event.altKey)return;
    if(event.key===' ' && target.closest?.('button') && !target.closest('[data-scene-play]'))return;
    if(event.key===' ' && !event.repeat) { event.preventDefault(); playScene(); }
    else if(['ArrowLeft','ArrowRight'].includes(event.key) && (timelineDialog?.open || target.closest?.('#showStage'))) {
      event.preventDefault();stepVideo(event.key==='ArrowLeft'?-1:1,event.shiftKey?10:1);
    }
  });

  async function refreshLibrary() {
    await loadLibrary();
    if (currentScene()) renderScene();
  }

  async function addToScene(asset, placement=null) {
    const scene = currentScene();
    if (!scene) { showToast("Open a scene to place it", true); return; }
    if(placement?.sceneId && placement.sceneId!==scene.id)throw Error('The photo is saved. Reopen its original scene before placing it.');
    const timing=fitTiming(scene,placement?.at??scene.start,placement?.duration??Math.min(4,scene.end-scene.start));
    const placed={ id:crypto.randomUUID(),asset, x: placement?.x??62, y: placement?.y??35, width: placement?.width??12, ...timing, action: placement?.action||"none", intensity: 1 };
    scene.overlays.push(placed);
    if (!await saveOverlays(scene)) {
      scene.overlays=scene.overlays.filter(item=>item.id!==placed.id);
      throw Error("The image is saved in the gallery, but could not be placed in this scene. Check its existing library references and try again.");
    }
    open.add(scene.id);
    renderScene();
    return timelineItem('sticker:'+placed.id);
  }

  // The Show editing desk works on the open project, in this same window.
  document.getElementById("showStudioOpen")?.addEventListener("click", () => { window.location.href = "/show-studio/"; });

  // ---- Your own pictures: drop one from the computer onto the scene ------------------------
  // It is kept in the project's library and placed where it was dropped, at the playhead.
  // A cut-out picture (see-through around its subject) goes on the video as it is; a plain
  // photo too, with an offer to cut out its subject (macOS subject lifting).
  function nativeFilePath(native, files, file) {
    if(!native)return null;
    const matches=native.filter(item=>item.name===file.name);
    if(matches.length===1)return matches[0].path;
    const index=files.indexOf(file);
    return native.length===files.length && native[index]?.name===file.name ? native[index].path : null;
  }
  const fileDrag = (event) => [...(event.dataTransfer?.types || [])].includes("Files");
  for(const type of ['dragover','drop'])document.addEventListener(type,event=>{
    if(fileDrag(event))event.preventDefault();
  },true);
  const dropArea = event => event.target instanceof Element && (!!event.target.closest("#showReviewBox, #showTimelineDialog") || (!$("#showReviewBox")?.hidden && !document.querySelector("dialog[open]")));
  document.addEventListener("dragover", (event) => {
    if (!review || !currentScene() || !fileDrag(event) || !dropArea(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    $("#showStage")?.classList.add("show-drop-ready");
  });
  document.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget || !(event.relatedTarget instanceof Element) || !event.relatedTarget.closest("#showReviewBox, #showTimelineDialog"))
      $("#showStage")?.classList.remove("show-drop-ready");
  });
  document.addEventListener("drop", async (event) => {
    $("#showStage")?.classList.remove("show-drop-ready");
    if (!review || !currentScene() || !fileDrag(event) || !dropArea(event)) return;
    event.preventDefault();
    const files=[...event.dataTransfer.files];
    if(files.some(file=>/\.(mp4|mov|m4v|mkv|webm|avi|mp3|wav|m4a|aac|flac|ogg|opus)$/i.test(file.name))) {
      try {const native=await window.waitForNativeDrop?.(files);for(const file of files){
        if(/\.(png|jpe?g|webp)$/i.test(file.name))await addOwnPicture(currentScene(),file,.5,.5);
        else await importLocalMedia(file,true,nativeFilePath(native,files,file));
      }}
      catch(error){showToast(error.message,true);}return;
    }
    const file = files.find((item) => /\.(png|jpe?g|webp)$/i.test(item.name));
    if (!file) { showToast("Drop a video, audio file, PNG, WEBP or JPEG picture here.", true); return; }
    const box = $("#showStage").getBoundingClientRect();
    const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
    addOwnPicture(currentScene(), file, inside ? (event.clientX - box.left) / box.width : 0.5, inside ? (event.clientY - box.top) / box.height : 0.5)
      .catch((error) => showToast(error.message, true));
  });

  async function addOwnPicture(scene, file, cx, cy) {
    $("#showStageStatus").textContent = `Adding ${file.name}…`;
    const form = new FormData();
    form.append("file", file, file.name);
    const response = await fetch("/api/v1/show/library/pictures", { method: "POST", body: form });
    const kept = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(kept.error?.message || `Could not add ${file.name}`);
    await loadLibrary().catch(() => {});
    const width = kept.cutout ? 22 : 30, height = width * 16 / 9 * kept.height / kept.width;
    const at = Math.max(scene.start, Math.min(scene.end - 0.1, editedPlayhead()));
    const overlay = { id: crypto.randomUUID(), asset: kept.path, width, at, duration: Math.min(3, scene.end - at), effect: "pop",
      x: Math.max(0, Math.min(100 - width, cx * 100 - width / 2)), y: Math.max(0, Math.min(Math.max(0, 100 - height), cy * 100 - height / 2)) };
    scene.overlays.push(overlay);
    if (!await saveOverlays(scene)) return;
    locateEffect(timelineItems(scene).find((item) => item.overlay?.id === overlay.id) || timelineItems(scene).at(-1));
    if (kept.cutout) showToast(`${kept.name} is on the video from ${clock(at)}. Drag it to move it; drag its bar to time it.`);
    else offerCutout(scene, overlay.id, kept);
  }

  function offerCutout(scene, overlayId, kept) {
    let offer = $("#showCutoutOffer");
    if (!offer) { offer = document.createElement("div"); offer.id = "showCutoutOffer"; offer.className = "show-cutout-offer"; }
    $("#showStage").after(offer);
    offer.hidden = false;
    offer.innerHTML = `<span>${escape(kept.name)} is on the video as a whole photo.</span>
      <button type="button" class="icon-button small primary" data-cutout-yes>Cut out the subject</button>
      <button type="button" class="icon-button small" data-cutout-no>Keep the whole photo</button>`;
    offer.querySelector("[data-cutout-no]").onclick = () => { offer.hidden = true; };
    offer.querySelector("[data-cutout-yes]").onclick = async () => {
      offer.querySelectorAll("button").forEach((button) => { button.disabled = true; });
      offer.querySelector("span").textContent = "Cutting out the subject…";
      try {
        const cut = await api("/show/library/pictures/cutout", { method: "POST", body: JSON.stringify({ path: kept.path }) });
        const overlay = scene.overlays.find((item) => item.id === overlayId);
        if (overlay) {
          // Same width and centre, the cut-out's own height.
          const centre = overlay.y + overlay.width * 16 / 9 * kept.height / kept.width / 2;
          Object.assign(overlay, { asset: cut.path, y: Math.max(0, centre - overlay.width * 16 / 9 * cut.height / cut.width / 2) });
          await loadLibrary().catch(() => {});
          if (await saveOverlays(scene)) { drawOverlays(scene); showToast("Subject cut out. To use the whole photo instead, drop it again and choose Keep the whole photo."); }
        }
        offer.hidden = true;
      } catch (error) {
        offer.querySelector("span").textContent = `Could not cut it out: ${error.message}`;
        offer.querySelectorAll("button").forEach((button) => { button.disabled = false; });
      }
    };
  }

  // ---- Library: what you already have, to use again instead of drawing it again ------------
  // This project (your pictures and every drawing you approved), the recurring shelves (there
  // in every project) and the other projects' libraries. A picture from a shelf or another
  // project is copied into this project when you use it. Matching is by name: offered, never
  // approved for you.
  const libraryOpenGroups = new Map();
  const LIBRARY_TABS = { project: "This project", recurring: "Recurring", other: "Other projects", videos:"Videos", audio:"Audio", effects:"Effects", hidden: "Hidden" };
  let libraryDialog = null, libraryAll = [], libraryShelves = [], libraryTab = "project", libraryFor = null, libraryQuery = "";
  let libraryMedia = [], libraryAudio = [];
  let libraryWords = null;             // selected words a chosen picture goes on: {text, start, end}
  let libraryFace = null;              // a clip that gets the face of the chosen photo
  const suggestions = new Map();       // subject -> Promise of matching pictures

  const libraryKey = (text) => String(text || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  function libraryGroups() {
    const visible = libraryAll.filter((item) => !item.retired);
    return {
      project: visible.filter((item) => item.section === "project"),
      recurring: visible.filter((item) => item.section === "recurring"),
      other: visible.filter((item) => item.section === "other"),
      hidden: libraryAll.filter((item) => item.retired),
      videos: libraryFace || libraryFor ? [] : libraryMedia,
      audio: libraryFace || libraryFor ? [] : libraryAudio,
      effects: libraryFace || libraryFor ? [] : [
        ['drawing','Drawing'],['cutaway','Close-up'],['original_eyes','Original eyes'],['original_mouth','Original mouth'],
        ['video_cutout','Moving person cutout'],['video_silhouette','Moving silhouette'],['clay','Clay memory · Simón'],
        ['math','Thinking formulas'],['word','Shouted word'],['captions','Speech captions'],['original_part','Move original part'],
        ['video_reference','Clip from a link'],...Object.entries(TEXTURES).map(([key,name])=>['texture_'+key,name])
      ].map(([effect,name])=>({effect,name,folder:'Add an effect'})),
    };
  }

  async function loadLibraryAll() {
    const [data, videos, audio] = await Promise.all([api("/show/library?others=1&retired=1"),api('/show/video-library'),api('/show/audio-library')]);
    libraryMedia=(videos.items||[]).map(item=>({...item,media_kind:'video',folder:item.face_swapped?'Face-swapped clips':'Videos'}));
    libraryAudio=(audio.items||[]).map(item=>({...item,media_kind:'audio',folder:'Audio'}));
    libraryAll = data.items || [];
    libraryShelves = data.shelves || [];
  }

  async function libraryChanged() {
    suggestions.clear();
    await Promise.all([loadLibrary(), libraryDialog?.open ? loadLibraryAll() : null]).catch(() => {});
    if (libraryDialog?.open) drawLibrary();
    if (currentScene()) renderScene();
  }

  async function openLibrary(forProposal = null, words = null, faceClip = null) {
    libraryFor = forProposal ? review?.proposals.find((item) => item.id === forProposal) || null : null;
    libraryWords = !libraryFor && words?.end > words?.start ? words : null;
    libraryFace = !libraryFor && !libraryWords && faceClip ? review?.proposals.find((item) => item.id === faceClip) || null : null;
    if (!libraryDialog) {
      libraryDialog = document.createElement("dialog");
      libraryDialog.id = "showLibraryDialog";
      libraryDialog.setAttribute("aria-label", "Library");
      libraryDialog.innerHTML = `<header><div><strong>Library</strong><small>Your pictures, videos, audio and effects. Imported media stays in this project; original and face-swapped clips remain reusable.</small></div>
        <input type="search" data-lib-search placeholder="Search: sofa, plant, fridge…" aria-label="Search the library">
        <label class="icon-button small show-lib-add">Add media…<input type="file" data-lib-files accept="image/png,image/jpeg,image/webp,video/*,audio/*" multiple hidden></label>
        <button type="button" aria-label="Close library" data-lib-close>×</button></header>
        <nav class="show-lib-tabs" data-lib-tabs></nav><div class="show-lib-body" data-lib-body></div>`;
      document.body.append(libraryDialog);
      libraryDialog.addEventListener("click", (event) => onLibraryClick(event).catch((error) => showToast(error.message, true)));
      libraryDialog.addEventListener("toggle", (event) => {
        if (event.target.matches?.("[data-lib-group]")) libraryOpenGroups.set(event.target.dataset.libGroup, event.target.open);
      }, true);
      libraryDialog.addEventListener("change", (event) => onLibraryChange(event).catch((error) => showToast(error.message, true)));
      libraryDialog.querySelector("[data-lib-search]").addEventListener("input", (event) => { libraryQuery = event.target.value; drawLibrary(); });
      libraryDialog.addEventListener("dragover", (event) => { if (fileDrag(event)) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } });
      libraryDialog.addEventListener("drop", (event) => { if (!fileDrag(event)) return; event.preventDefault(); addLibraryFiles([...event.dataTransfer.files],true).catch(error=>showToast(error.message,true)); });
    }
    libraryDialog.querySelector("[data-lib-body]").innerHTML = `<p class="show-hint">Reading your library…</p>`;
    if (!libraryDialog.open) libraryDialog.showModal();
    try { await loadLibraryAll(); } catch (error) { libraryDialog.querySelector("[data-lib-body]").innerHTML = `<p class="show-needs">${escape(error.message)}</p>`; return; }
    if (libraryFor || libraryWords || libraryFace) { const groups = libraryGroups(); libraryTab = groups.project.length ? "project" : groups.recurring.length ? "recurring" : "other"; }
    drawLibrary();
    if (libraryWords) libraryDialog.querySelector("[data-lib-search]").focus();
  }

  function libraryCard(item) {
    const scene = currentScene();
    if(item.effect)return `<figure class="show-lib-card"><figcaption><b>${escape(item.name)}</b></figcaption><button type="button" data-lib-effect="${escape(item.effect)}" ${scene?'':'disabled'}>Add to scene…</button></figure>`;
    if(item.media_kind) {
      const id=encodeURIComponent(item.id), url=`/api/v1/show/video-library/${id}/media`;
      return `<figure class="show-lib-card">${item.media_kind==='video'?`<video controls preload="none" poster="/api/v1/show/video-library/${id}/thumbnail" src="${url}" aria-label="Preview ${escape(item.name)}"></video>`:`<audio controls preload="none" src="${url}" aria-label="Preview ${escape(item.name)}"></audio>`}<figcaption><b>${escape(item.name)}</b><small>${item.saved_edit?'Saved edit · ':''}${item.face_swapped?'Face-swapped · ':''}${item.transparent?'Cutout · ':''}${Number(item.duration).toFixed(1)} s</small></figcaption><button type="button" data-lib-media="${escape(item.id)}" ${scene?'':'disabled'}>${libraryWords?'Place on selected words':'Place at playhead'}</button><button type="button" data-lib-media="${escape(item.id)}" data-lib-edit ${scene?'':'disabled'}>Place &amp; edit…</button></figure>`;
    }
    const index = libraryAll.indexOf(item);
    const where = item.section === "other" ? `from ${item.project}` : item.section === "recurring" ? `Recurring · ${item.shelf}` : (item.origin || "Your library");
    const shelfControl = item.section === "recurring"
      ? (libraryShelves.includes(item.shelf) ? `<button type="button" class="icon-button small" data-lib-unrecur="${index}">Take off the shelf</button>` : "")
      : item.recurring ? `<span class="show-lib-badge">★ Recurring · ${escape(item.recurring)}</span>`
      : `<select data-lib-recur="${index}" aria-label="Make ${escape(item.name)} recurring"><option value="">★ Make recurring…</option>${libraryShelves.map((shelf) => `<option value="${escape(shelf)}" ${item.character && shelf === "Characters" ? "selected" : ""}>${escape(shelf)}</option>`).join("")}</select>`;
    return `<figure class="show-lib-card ${item.retired ? "hidden-item" : ""}">
      <img src="${item.url}" alt="" loading="lazy">
      ${item.section!=="other"?`<button type="button" data-lib-cutout="${index}">Remove background</button>`:""}
      <figcaption><b title="${escape(item.name)}">${escape(item.name)}</b><small>${escape(where)}${item.approved ? " · approved" : ""}</small></figcaption>
      <div class="show-lib-actions">
        ${libraryFor ? `<button type="button" class="icon-button small primary" data-lib-use-for="${escape(libraryFor.id)}" data-lib-path="${escape(item.path)}">${libraryFor.kind === "background" ? "Use as the background" : "Use for this drawing"}</button>` : ""}
        ${libraryFace && !item.retired ? `<button type="button" class="icon-button small primary" data-lib-face="${index}">Use this face</button>` : ""}
        ${libraryWords && !item.retired ? `<button type="button" class="icon-button small primary" data-lib-words="${index}">Put on “${escape(libraryWords.text.length > 24 ? `${libraryWords.text.slice(0, 22)}…` : libraryWords.text)}”</button>`
          : scene && !item.retired ? `<button type="button" class="icon-button small" data-lib-place="${index}">Place in scene</button>` : ""}
        ${scene && !item.retired && !libraryFor && !libraryFace ? `<button type="button" class="icon-button small" data-lib-place="${index}" data-lib-edit>Place &amp; edit…</button>` : ""}
        ${item.retired ? "" : shelfControl}
        ${item.section === "other" ? "" : `<button type="button" class="icon-button small" data-lib-hide="${index}">${item.retired ? "Show again" : "Hide"}</button>`}
      </div></figure>`;
  }

  function drawLibrary() {
    if (!libraryDialog) return;
    const groups = libraryGroups();
    libraryDialog.querySelector("[data-lib-tabs]").innerHTML = Object.entries(LIBRARY_TABS).map(([key, label]) =>
      `<button type="button" class="icon-button small ${libraryTab === key ? "active" : ""}" data-lib-tab="${key}">${label} (${groups[key].length})</button>`).join("");
    const query = libraryKey(libraryQuery).trim();
    // A search looks through this project, the shelves and the other projects at once.
    const pool = query && !["hidden","videos","audio","effects"].includes(libraryTab) ? [...groups.project, ...groups.recurring, ...groups.other] : groups[libraryTab];
    const shown = pool.filter((item) => !query || libraryKey(`${item.name} ${item.origin} ${item.project || ""} ${item.shelf || ""}`).includes(query));
    const place = (item) => item.section === "other" ? `From ${item.project}` : item.section === "recurring" ? `Recurring · ${item.shelf}` : "This project";
    const by = query && libraryTab !== "hidden" ? "place" : libraryTab === "recurring" ? "shelf" : libraryTab === "other" ? "project" : "category";
    shown.forEach((item) => { item.place = place(item); item.category = item.folder || (item.character ? "Characters" : item.origin || "Pictures"); });
    const heads = by ? [...new Set(shown.map((item) => item[by]))] : [null];
    const empty = { project: "Nothing here yet. Drop pictures on this window or on the video, or approve a drawing: it stays here.",
      recurring: "No recurring pictures yet. Choose “★ Make recurring…” on any picture to keep it on a shelf for every project.",
      other: "No pictures in your other projects.", videos:"No videos yet. Use Add media… or drop a local video here. Downloaded clips and face-swapped versions also stay here.", audio:"No audio yet. Use Add media… or drop an audio file here.", effects:"Open a scene to add effects.", hidden: "Nothing hidden." }[libraryTab];
    const banner = libraryFor?.kind === "background" ? `<div class="show-lib-for"><p>Choose the place to put behind you${libraryFor.params.subject ? ` on <b>“${escape(libraryFor.params.subject)}”</b>` : ""}: a photo of your kitchen, outside, your atelier… (drop your own photos on this window). You stay in front, talking; the Mac cuts you out of every frame, nothing is uploaded.</p><div class="show-lib-grid" data-lib-matches><p class="show-hint">Looking for matches…</p></div></div>`
      : libraryFor ? `<div class="show-lib-for"><p>Choosing the picture for <b>“${escape(libraryFor.params.subject || "this drawing")}”</b>.</p><div class="show-lib-grid" data-lib-matches><p class="show-hint">Looking for matches…</p></div></div>`
      : libraryFace ? `<div class="show-lib-for"><p>Choose a photo of you for the clip on <b>“${escape(libraryFace.params.selected_text || "these words")}”</b>. Your face goes on the person seen most in the clip; a clear photo, facing the camera, works best. It is done on this Mac, nothing is uploaded.</p></div>`
      : libraryWords ? `<div class="show-lib-for"><p>Choose a picture to put on <b>“${escape(libraryWords.text)}”</b> (${clock(libraryWords.start)}–${clock(libraryWords.end)}). Search by name or look through the tabs.</p><div class="show-lib-grid" data-lib-matches></div></div>` : "";
    libraryDialog.querySelector("[data-lib-body]").innerHTML = (libraryTab==='audio'?'<p><button type="button" data-library-music>Find music / jingles · MP3</button></p>':'') + banner + (shown.length ? '<div class="show-actions"><button type="button" data-lib-toggle="open">Expand all</button><button type="button" data-lib-toggle="close">Collapse all</button></div>' + heads.map((head, index) => {
      const key = `${libraryTab}:${head}`;
      const items = shown.filter((item) => !by || item[by] === head);
      return `<details class="show-library-group" data-lib-group="${escape(key)}" ${(query || (libraryOpenGroups.get(key) ?? index === 0)) ? "open" : ""}><summary>${escape(head || "Pictures")} · ${items.length}</summary><div class="show-lib-grid">${items.map(libraryCard).join("")}</div></details>`;
    }).join("")
      : `<p class="show-hint">${query ? "Nothing with that name in your library." : empty}</p>`);
    libraryDialog.querySelector('[data-library-music]')?.addEventListener('click',()=>{libraryDialog.close();MusicLibrary.open(true);});
    if (libraryFor || (libraryWords && !query)) {
      matchesFor(libraryFor ? libraryFor.params.subject : libraryWords.text).then((found) => {
        const box = libraryDialog.querySelector("[data-lib-matches]");
        if (!box) return;
        const cards = found.map((match) => libraryAll.find((item) => item.path === match.path) || { ...match }).filter(Boolean);
        cards.forEach((item) => { if (!libraryAll.includes(item)) libraryAll.push(item); });
        box.innerHTML = cards.length ? cards.map(libraryCard).join("") : libraryFor ? `<p class="show-hint">Nothing in your library is called like that yet. Look through the tabs, or close this and draw it.</p>` : "";
      });
    }
  }

  function matchesFor(subject) {
    const key = libraryKey(subject).trim();
    if (!key) return Promise.resolve([]);
    if (!suggestions.has(key)) suggestions.set(key, api(`/show/library/suggest?subject=${encodeURIComponent(subject)}`).then((data) => data.items || []).catch(() => []));
    return suggestions.get(key);
  }

  // A library picture as one of this project's own (copied in once when it comes from elsewhere).
  async function projectCopy(item) {
    if (item.section === "project" || !item.section) return item;
    return api("/show/library/use", { method: "POST", body: JSON.stringify({ path: item.path }) });
  }

  async function useForDrawing(proposalId, path) {
    const updated = await api(`/show/review/${encodeURIComponent(proposalId)}/picture`, { method: "POST", body: JSON.stringify({ path }) });
    const index = review.proposals.findIndex((item) => item.id === proposalId);
    if (index >= 0) review.proposals[index] = updated;
    const scene = scenes().find((entry) => entry.effects.includes(proposalId));
    if (scene) { scene.decision = "modified"; invalidateLocal(scene); }
    libraryDialog?.open && libraryDialog.close();
    await libraryChanged();
    showToast(updated.kind === "background" ? `New background: ${updated.params.image_engine.replace(/^Your library · /, "")}. You appear in front of it in the scene's effects preview (the first time, the Mac cuts you out of each frame: it takes a little while).`
      : `“${updated.params.subject}” now uses ${updated.params.image_engine.replace(/^Your library · /, "")}. It plays in the scene's preview${updated.status === "approved" ? "" : "; approve it when you have seen it"}.`);
  }

  // A library picture on the selected words: it enters with the first word and stays while
  // they are said (at least two seconds), then the scene editor opens on its clip.
  async function putOnWords(item, words) {
    const scene = currentScene();
    if (!scene || !words) return;
    const own = await projectCopy(item);
    const at = Math.max(scene.start, Math.min(scene.end - 0.1, words.start));
    const duration = Math.min(scene.end - at, Math.max(2, words.end - words.start));
    const overlay = { id: crypto.randomUUID(), asset: own.path, x: 62, y: 12, width: 22, at, duration, effect: "pop", selected_text: words.text };
    scene.overlays.push(overlay);
    if (!await saveOverlays(scene)) { scene.overlays = scene.overlays.filter((entry) => entry.id !== overlay.id); return; }
    libraryDialog.close();
    await libraryChanged();
    locateEffect(timelineItems(scene).find((entry) => entry.overlay?.id === overlay.id));
    showToast(`${item.name} is on “${words.text}” (${clock(at)}). Drag it on the video to move it, its corner to resize it, its bar to time it.`);
  }

  async function importLocalMedia(file, place=false, nativePath=null, words=null) {
    if(!/\.(mp4|mov|m4v|mkv|webm|avi|mp3|wav|m4a|aac|flac|ogg|opus)$/i.test(file.name))throw Error(`Unsupported media: ${file.name}`);
    const owner=review.project_id, scene=currentScene(), at=words?.start ?? libraryWords?.start ?? editedPlayhead();
    showToast(`Importing ${file.name}… Large files may take a moment.`);
    const record=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST','/api/v1/show/library/media');
      xhr.upload.onprogress=event=>{if(event.lengthComputable)$('#showStageStatus').textContent=`Importing ${file.name}: ${Math.round(100*event.loaded/event.total)}%`;};
      xhr.upload.onload=()=>{$('#showStageStatus').textContent=`Checking ${file.name} and preparing playback…`;};
      xhr.onerror=()=>reject(Error('Import connection failed. Your existing edits have been kept; retry the file.'));
      xhr.onload=()=>{let body;try{body=JSON.parse(xhr.responseText);}catch{reject(Error(`Import failed (${xhr.status}). Try the native file picker or a smaller file.`));return;}
        if(xhr.status>=400)reject(Error(body.error?.message||'Could not import this file'));else resolve(body.asset);};
      if(nativePath){xhr.setRequestHeader('Content-Type','application/json');xhr.send(JSON.stringify({path:nativePath,project_id:owner}));}
      else{const form=new FormData();form.append('file',file,file.name);form.append('project_id',owner);xhr.send(form);}
    });
    if(review?.project_id!==owner){showToast(`${file.name} was saved in the original project's library.`);return record;}
    if(place && scene && currentScene()?.id===scene.id)await placeLibraryMedia(record.id,scene,at,words);
    libraryTab=record.media_kind==='audio'?'audio':'videos';
    await libraryChanged();showToast(`${file.name} is in this project's library${place?' and timeline':''}.`);return record;
  }
  async function placeLibraryMedia(id, scene=currentScene(), at=libraryWords?.start??editedPlayhead(), words=libraryWords) {
    if(!scene)throw Error('Open a scene before placing media.');
    at=Math.max(scene.start,Math.min(scene.end-.04,at));
    const owner=review.project_id;
    const result=await api('/show/video-library/'+encodeURIComponent(id)+'/place',{method:'POST',body:JSON.stringify({project_id:owner,scene_id:scene.id,at,...(words?{duration:words.end-words.start}:{})})});
    if(review.project_id!==owner){showToast('Media placed in the original project.');return;}
    libraryDialog?.close();clipAdded(result);
    return timelineItem(result.proposal.id);
  }
  async function addLibraryFiles(files, nativeDrop=false) {
    const native=nativeDrop?await window.waitForNativeDrop?.(files):null;
    let added=0;
    for (const file of files) {
      if(/\.(mp4|mov|m4v|mkv|webm|avi|mp3|wav|m4a|aac|flac|ogg|opus)$/i.test(file.name)) {
        await importLocalMedia(file,false,nativeFilePath(native,files,file));added++;continue;
      }
      if(!/\.(png|jpe?g|webp)$/i.test(file.name)){showToast(`Unsupported file: ${file.name}`,true);continue;}
      const form=new FormData();form.append('file',file,file.name);
      const response=await fetch('/api/v1/show/library/pictures',{method:'POST',body:form});
      if(!response.ok){const answer=await response.json().catch(()=>({}));throw Error(answer.error?.message||`Could not add ${file.name}`);}
      added++;libraryTab='project';
    }
    await libraryChanged();showToast(`${added} file${added===1?'':'s'} added to this project's library.`);
  }

  async function onLibraryClick(event) {
    const target = event.target instanceof Element ? event.target.closest("[data-lib-cutout],[data-lib-media],[data-lib-effect],[data-lib-toggle],[data-lib-close],[data-lib-tab],[data-lib-use-for],[data-lib-face],[data-lib-words],[data-lib-place],[data-lib-unrecur],[data-lib-hide]") : null;
    if (!target) return;
    if(target.dataset.libMedia){target.disabled=true;try{const placed=await placeLibraryMedia(target.dataset.libMedia);if(placed&&target.hasAttribute('data-lib-edit'))editMedia(placed);}finally{target.disabled=false;}return;}
    if(target.dataset.libEffect){libraryDialog.close();const key=target.dataset.libEffect,picked=libraryWords||pickedHere(currentScene());if(key.startsWith('texture_'))await extraTool(key,currentScene(),picked);else chooseAdd(key,picked,innerWidth/2,innerHeight/2);return;}
    if (target.dataset.libToggle) {
      libraryDialog.querySelectorAll("[data-lib-group]").forEach((group) => { group.open = target.dataset.libToggle === "open"; });
      return;
    }
    if(target.hasAttribute('data-lib-cutout')){
      const item=libraryAll[Number(target.dataset.libCutout)];target.disabled=true;
      try {await api('/show/library/pictures/cutout',{method:'POST',body:JSON.stringify({path:item.path,keep_canvas:true})});await loadLibrary();drawLibrary();showToast('Transparent copy saved. Original kept.');}
      catch(error){showToast(error.message,true);target.disabled=false;}return;
    }
    if (target.hasAttribute("data-lib-close")) { libraryDialog.close(); return; }
    if (target.dataset.libTab) { libraryTab = target.dataset.libTab; drawLibrary(); return; }
    if (target.dataset.libUseFor) { target.disabled = true; await useForDrawing(target.dataset.libUseFor, target.dataset.libPath); return; }
    const item = libraryAll[Number(target.dataset.libFace ?? target.dataset.libWords ?? target.dataset.libPlace ?? target.dataset.libUnrecur ?? target.dataset.libHide)];
    if (!item) return;
    if (target.dataset.libFace !== undefined) {
      const clip = libraryFace;
      libraryDialog.close();
      await putFace(clip, item);
    } else if (target.dataset.libWords !== undefined) {
      target.disabled = true;
      await putOnWords(item, libraryWords);
    } else if (target.dataset.libPlace !== undefined) {
      target.disabled = true;
      const owner=review.project_id, scene=currentScene(), words=libraryWords;
      const at=words?.start??editedPlayhead();
      const own = await projectCopy(item);
      if(review.project_id!==owner||currentScene()?.id!==scene?.id)throw Error('The scene changed. Reopen the library in the intended scene.');
      libraryDialog.close();
      const placed=await addToScene(own.path,{sceneId:scene.id,at,...(words?{duration:words.end-words.start}:{})});
      await libraryChanged();
      if(target.hasAttribute('data-lib-edit'))editMedia(placed);
      showToast(`${item.name} is on the video. Drag it to move it; drag its bar to time it.`);
    } else if (target.dataset.libUnrecur !== undefined) {
      if (!confirm(`Take “${item.name}” off the ${item.shelf} shelf? It goes to the Trash (recoverable); projects that used it keep their own copy.`)) return;
      await api("/show/library/recurring", { method: "POST", body: JSON.stringify({ path: item.path, recurring: false }) });
      await libraryChanged();
    } else {
      await api("/show/library/visibility", { method: "POST", body: JSON.stringify({ path: item.path, retired: !item.retired }) });
      await libraryChanged();
    }
  }

  async function onLibraryChange(event) {
    if (event.target.matches?.("[data-lib-files]")) { await addLibraryFiles([...event.target.files]); event.target.value = ""; return; }
    const select = event.target.closest?.("[data-lib-recur]");
    if (!select || !select.value) return;
    const item = libraryAll[Number(select.dataset.libRecur)];
    select.disabled = true;
    await api("/show/library/recurring", { method: "POST", body: JSON.stringify({ path: item.path, shelf: select.value, name: item.name }) });
    await libraryChanged();
    showToast(`${item.name} is on the ${select.value} shelf: it is in the library of every project now.`);
  }

  // Under each drawing still to be pictured: what the library already has with that name.
  function fillSuggestions() {
    document.querySelectorAll("[data-suggest-for]").forEach((box) => {
      const item = review?.proposals.find((entry) => entry.id === box.dataset.suggestFor);
      if (!item?.params.subject) return;
      matchesFor(item.params.subject).then((found) => {
        const others = found.filter((match) => match.path !== item.params.picture).slice(0, 3);
        box.innerHTML = others.length ? `<span>In your library:</span>${others.map((match) => `<button type="button" class="show-lib-chip" data-lib-use-for="${escape(item.id)}" data-lib-path="${escape(match.path)}" title="Use ${escape(match.name)} for this drawing${match.section === "other" ? ` (from ${escape(match.project)})` : ""}"><img src="${match.url}" alt="" loading="lazy">${escape(match.name)}</button>`).join("")}` : "";
      });
    });
  }
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-open-library],.show-lib-chip") : null;
    if (!target || !review || target.closest("#showLibraryDialog")) return;
    if (target.classList.contains("show-lib-chip")) { target.disabled = true; useForDrawing(target.dataset.libUseFor, target.dataset.libPath).catch((error) => { target.disabled = false; showToast(error.message, true); }); }
    else openLibrary(target.dataset.openLibrary || null).catch((error) => showToast(error.message, true));
  });

  function refreshSceneDrawings() {
    const box = $("#showSceneDrawings"), scene = currentScene();
    if (!box || !scene) return;
    box.outerHTML = sceneDrawings(scene, sceneEffects(scene));
    fillSuggestions();
  }

  // The drawings of this scene, with what the library has for each (always in sight).
  function sceneDrawings(scene, effects) {
    const drawings = effects.filter((item) => item.kind === "drawing" && item.status !== "discarded" && item.params.subject && ["drawing", "character", undefined].includes(item.params.kind));
    if (!drawings.length) return `<div id="showSceneDrawings" hidden></div>`;
    return `<div id="showSceneDrawings" class="show-scene-drawings"><strong>Drawings in this scene</strong>${drawings.map((item) => {
      const pictured = !!item.params.picture, done = item.status === "approved" && pictured;
      const state = item.status === "approved" ? (pictured ? "Approved" : "Approved, but it has no picture: it is left out of the video") : item.drafting?.status === "drawing" ? "Being drawn…" : item.params.picture ? (item.params.draft ? "Draft to approve" : "Picture chosen, to approve") : "No picture yet";
      return `<div class="show-scene-drawing">${item.params.picture ? `<img src="/api/v1/show/library/file?path=${encodeURIComponent(item.params.picture)}" alt="" loading="lazy">` : `<span class="show-scene-drawing-empty">?</span>`}
        <div><b>“${escape(item.params.subject)}”</b> <small>${escape(state)}${item.params.image_engine ? ` · ${escape(item.params.image_engine)}` : ""}</small>
        <div class="show-actions"><button type="button" class="icon-button small" data-open-library="${escape(item.id)}">Choose from library</button>${done ? "" : `<button type="button" class="icon-button small" data-draw-it="${escape(item.id)}" ${item.drafting?.status === "drawing" ? "disabled" : ""} ${item.params.picture ? 'data-again="1"' : ""}>${item.params.picture ? "Draw another" : "Draw it"}</button>${pictured && item.status !== "approved" ? `<button type="button" class="icon-button small" data-show-status="approved" data-id="${escape(item.id)}" ${(item.needs || []).length ? "disabled" : ""}>Approve</button>` : ""}`}</div>
        ${done ? "" : `<div class="show-lib-suggest" data-suggest-for="${escape(item.id)}"></div>`}</div></div>`;
    }).join("")}</div>`;
  }

  document.addEventListener("change", (event) => {
    const select = event.target instanceof Element ? event.target.closest("[data-clip-sound]") : null;
    if (!select || !review) return;
    const id = select.dataset.clipSound, item = review.proposals.find((entry) => entry.id === id);
    if (!item) return;
    item.params.clip_audio = select.value !== "mine";
    item.params.audio_mode=select.value==='mix'?'mix':select.value==='mine'?'mute':'own';
    saveSoon(id, { clip_audio: item.params.clip_audio, audio_mode:item.params.audio_mode });
    showToast(item.params.audio_mode==='mix'?"Clip sound is mixed with your narration. Open Sound mixer to balance the levels.":item.params.clip_audio ? "The clip plays with its own sound; you pause meanwhile." : "The clip's picture plays over your voice, which goes on.");
  });

  // Put my face on a clip: done on this Mac in the background (the models are downloaded once,
  // after asking); the clip then plays with the face of the chosen photo.
  async function putFace(clip, photo) {
    const models = await api("/show/faceswap/models");
    let download = false;
    if (models.missing) {
      if (!confirm(`Putting your face on a clip needs its face models first: ${models.size_mb} MB, downloaded once from FaceFusion's public copies on Hugging Face. They stay on this Mac and nothing of yours is uploaded. Download them now?`)) return;
      download = true;
    }
    await api("/show/faceswap", { method: "POST", body: JSON.stringify({ proposal_id: clip.id, photo: photo.path, download_models: download }) });
    showToast(download ? "Downloading the face models, then putting your face on the clip…" : "Putting your face on the clip… (a few minutes for a few seconds of video)");
    watchFace(clip.id);
  }
  function faceStatus(id, text) {
    document.querySelectorAll(`[data-face-status="${CSS.escape(id)}"]`).forEach((line) => { line.textContent = text; });
    if (timelineDialog?.open) $("#showStageStatus").textContent = text;
  }
  async function watchFace(id) {
    const job = await api(`/show/faceswap/${encodeURIComponent(id)}`).catch(() => null);
    if (!job || job.status === "none") return;
    if (job.status === "downloading" || job.status === "working") {
      faceStatus(id, `${job.status === "downloading" ? "Downloading the face models" : "Putting your face on the clip"} · ${Math.round(100 * job.progress)} %`);
      setTimeout(() => watchFace(id), 2000);
      return;
    }
    if (job.status === "failed") { faceStatus(id, `Could not put your face on it: ${job.error}`); showToast(`Could not put your face on the clip: ${job.error}`, true); return; }
    const index = review.proposals.findIndex((item) => item.id === id);
    if (index >= 0 && job.proposal) review.proposals[index] = job.proposal;
    const scene = scenes().find((entry) => entry.effects.includes(id));
    if (scene) { scene.decision = "modified"; invalidateLocal(scene); }
    renderScene();
    showToast(`Your face is on the clip (${job.changed} of ${job.total} frames). Watch it in the scene preview.`);
  }
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-face-clip],[data-face-original]") : null;
    if (!target || !review) return;
    if (target.dataset.faceClip) { openLibrary(null, null, target.dataset.faceClip).catch((error) => showToast(error.message, true)); return; }
    target.disabled = true;
    api(`/show/faceswap/${encodeURIComponent(target.dataset.faceOriginal)}/original`, { method: "POST", body: "{}" }).then((item) => {
      const index = review.proposals.findIndex((entry) => entry.id === item.id);
      if (index >= 0) review.proposals[index] = item;
      const scene = scenes().find((entry) => entry.effects.includes(item.id));
      if (scene) { scene.decision = "modified"; invalidateLocal(scene); }
      renderScene();
      showToast("Back to the original clip. The version with your face is kept: put it on again any time.");
    }).catch((error) => { target.disabled = false; showToast(error.message, true); });
  });

  // A video reference's seconds downloaded into the project: a clip on its words, with its own
  // sound (the host pauses). The timeline opens on it so it is clear that it was created.
  async function downloadReference(id) {
    showToast("Downloading only the chosen seconds of the clip…");
    clipAdded(await api(`/show/references/video/${encodeURIComponent(id)}/download`, { method: "POST", body: "{}" }));
  }
  function clipAdded({ reference, proposal }) {
    const scene = scenes().find((entry) => entry.id === reference.scene_id) || scenes().find((entry) => entry.start <= proposal.at && proposal.at < entry.end);
    if (scene) scene.references = (scene.references || []).map((ref) => ref.id === reference.id ? reference : ref);
    const existing=review.proposals.findIndex(item=>item.id===proposal.id);
    if(existing>=0)review.proposals[existing]=proposal;else review.proposals.push(proposal);
    if (scene && !scene.effects.includes(proposal.id)) scene.effects.push(proposal.id);
    if (scene) { scene.decision = "modified"; invalidateLocal(scene); }
    if (scene && currentScene()?.id !== scene.id) step = scenes().indexOf(scene) + 2;
    render();
    locateEffect(timelineItem(proposal.id));
    showToast(`${proposal.kind==='jingle'?'The audio':'The clip'} is on “${reference.selected_text || "these words"}”: ${proposal.params.matte&&proposal.params.matte!=="none"?"it is layered over your video":"drag its timeline edges to choose its length"}. Watch it in the scene preview.`);
  }
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-ref-download]") : null;
    if (!button || !review) return;
    button.disabled = true;
    downloadReference(button.dataset.refDownload).catch((error) => { button.disabled = false; showToast(error.message, true); });
  });

  document.addEventListener('click',event=>{if(event.target.closest?.('[data-add-audio]'))MusicLibrary.open(true);});
  window.ShowReview = {
    audioTarget(){const scene=currentScene();if(!scene)return null;const picked=pickedWords||pickedHere(scene);return {project_id:review.project_id,scene_id:scene.id,at:picked.start,duration:Math.max(.04,picked.end-picked.start),text:picked.text};},
    addAudioLink(){const scene=currentScene();if(scene)ShowReferences.openAudio(scene,pickedWords||pickedHere(scene),review.project_id);},
    open: openReview, openWorkspace, hull, refreshLibrary, addToScene, openLibrary, openVideoLayers,
    async clipSpeed(id, speed) { const scene = scenes().find((entry) => entry.effects.includes(id)); const item = review?.proposals.find((entry) => entry.id === id); if (scene && item) await setClipSpeed(scene, item, speed); },
    shortcuts(){
      let dialog=$('#showKeyboardShortcuts');
      if(!dialog){dialog=document.createElement('dialog');dialog.id='showKeyboardShortcuts';dialog.setAttribute('aria-label','Show Studio keyboard shortcuts');
        dialog.innerHTML='<h2>Show Studio keyboard shortcuts</h2><p>When editing text, the usual text shortcuts stay active.</p><dl><dt>Space</dt><dd>Play / pause the scene or intro</dd><dt>← / → over the preview or timeline</dt><dd>Previous / next frame; Shift moves 10 frames</dd><dt>Ctrl / ⌘ C, X, V</dt><dd>Copy, cut, paste the selected timeline clip</dd><dt>Ctrl / ⌘ Z · Shift Z / Ctrl Y</dt><dd>Undo / redo in the timeline</dd><dt>Ctrl / ⌘ B or K</dt><dd>Split the selected clip at the playhead</dd><dt>Delete / Backspace</dt><dd>Remove the selected timeline clip</dd><dt>Enter in video link settings</dt><dd>Download the link or apply the clip settings</dd><dt>Escape</dt><dd>Close the current menu or dialog</dd></dl><form method="dialog"><button autofocus>Close</button></form>';
        document.body.append(dialog);
      }
      if(!dialog.open)dialog.showModal();
    },
    playbackState:()=>({original:!!quick&&!quick.saved,at:editedPlayhead()}),
    async audioChanged(preview=false,item=null){
      if(item?.overlay){const scene=scenes().find(s=>s.id===item.scene_id);const overlay=scene?.overlays.find(o=>o.id===item.overlay.id);if(overlay)Object.assign(overlay,item.overlay);item=null;}
      if(item){const index=review.proposals.findIndex(p=>p.id===item.id);if(index>=0)review.proposals[index]=item;}
      for(const scene of scenes()){scene.preview={status:'none'};dirtyScenes.add(scene.id);}
      const scene=currentScene();if(!scene)return;
      invalidateLocal(scene);renderScene();
      if(preview)await requestScenePreview(scene);
    },
    active:()=>!!review && !$('#showReviewBox')?.hidden,
    transport:action=>action==='play'?playScene():stepVideo(action==='previousFrame'?-1:1),
    async clipUpdated(item){const i=review.proposals.findIndex(p=>p.id===item.id);if(i>=0)review.proposals[i]=item;selectedEffect=item.id;await markModified(item.id);invalidateLocal(currentScene());renderScene();await requestScenePreview(currentScene());},
    // A character made in the studio from a "character speaks" card takes that effect's place: it is
    // what speaks there now (with the voice recorded in the studio), so the effect stops asking for one.
    async characterTakesPlace(id,name){
      const item=review?.proposals.find(entry=>entry.id===id);
      if(!item||item.kind!=='character'||item.status==='discarded')return;
      await patch(id,{status:'discarded'});
      renderScene();
      showToast(`${name} takes the place of the proposed character: it speaks there with the voice you recorded.`);
    },
    async clayChanged(data){clay=data;const scene=currentScene();if(!scene)return;invalidateLocal(scene);await clayBlock(scene);renderTimeline(scene);prepareInBackground(scene);},
    async partsSaved(asset,placement){
      const item=await api('/show/review/'+encodeURIComponent(placement.proposalId)+'/parts',{method:'POST',body:JSON.stringify({asset})});
      const index=review.proposals.findIndex(p=>p.id===item.id);if(index>=0)review.proposals[index]=item;
      selectedEffect=item.id;await markModified(item.id);invalidateLocal(currentScene());renderScene();await requestScenePreview(currentScene());
      showToast('Original eyes saved in the same close-up. No duplicate layer was added.');
    },
    async rigSaved(asset,placement){
      const scene=currentScene();if(!scene||scene.id!==placement.sceneId)throw Error('Reopen the original scene to place this saved character.');
      const overlay=scene.overlays.find(o=>o.id===placement.replaceId);
      if(!overlay)throw Error('The original layer was removed; the character is saved in your library.');
      const before={...overlay};Object.assign(overlay,{asset,...(placement.action?{action:placement.action}:{}),...(placement.at!=null?fitTiming(scene,placement.at,placement.duration):{})});
      if(!await saveOverlays(scene)){Object.assign(overlay,before);throw Error('The character is saved, but the layer update failed.');}
      renderScene();
    },
    clipAdded,
    referenceSaved(record){const scene=scenes().find(s=>s.id===record.scene_id);if(!scene)return;scene.references=[...(scene.references||[]).filter(r=>r.id!==record.id),record];if(currentScene()?.id===scene.id)renderScene();},
    captionSaved(result){
      review.caption_revision=result.revision;
      for(const scene of scenes())scene.captions=(scene.captions||[]).map(c=>c.id===result.cue.id?result.cue:c);
    },
    async imageApplied(item,sceneId) {
      const scene=scenes().find(s=>s.id===sceneId); if(!scene)throw Error("Open the image's scene before applying it");
      const index=review.proposals.findIndex(p=>p.id===item.id);
      if(index>=0)review.proposals[index]=item;else review.proposals.push(item);
      if(!scene.effects.includes(item.id))scene.effects.push(item.id);
      scene.decision="modified"; invalidateLocal(scene);
      step=scenes().indexOf(scene)+2; open.add(scene.id); open.add(item.id); render();
      if(item.kind==='word'&&item.params.lettering_text){selectedEffect=item.id;locateEffect(timelineItem(item.id),true);return;}
      selectedEffect=item.id; placingWords=true; firstWord=null; pickedWords=null;
      wordPlacementControls();
      $("#showSaid").scrollIntoView({block:"nearest"});
    },
    async localImageIdea(sceneId,picked,direction,style) {
      const scene=scenes().find(s=>s.id===sceneId);if(!scene)throw Error("This scene is no longer open");
      step=scenes().indexOf(scene)+2;
      return addEffect(scene,{kind:"drawing",subject:direction.slice(0,80),direction,selected_text:picked.text,style,at:picked.start,duration:Math.max(.1,picked.end-picked.start)});
    },
    voices: () => (review?.resources || []).flatMap((resource) => (resource.speech || []).map((line) => ({ source: resource.path, start: line.start, length: Math.round((line.end - line.start) * 100) / 100, text: line.text }))),
    projectId:()=>review?.project_id,
    sources: () => [review?.source, ...(review?.resources || []).map((item) => item.path)].filter(Boolean),
    resources: () => (review?.resources || []).filter((item) => item.kind !== "photo").map((item) => item.path),
  };
})();
