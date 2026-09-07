const detected = { master: [], songs: [], videos: [], ignored: [] };
const S = window.UI_STRINGS || {};
let selectedPlatform = null;
let lastPipelineStage = null;
let transitionTimer = null;
let transitionVersion = 0;
let selectedSong = null;
let selectedMasterPath = null;
// Inbox clips set aside because they belong to a different session than the
// chosen song. Kept (not discarded) so "Show all clips" can restore them.
let setAsideVideos = [];
let analysisSetAsideVideos = [];
let inboxAnalysis = null;
let inboxAnalysisTimer = null;
let sourceFolders = [];
let masterAudioExtensions = [".mp3"];
const COARSE_MATCH_THRESHOLD = 6.0;
let sessionFilterDisabled = false;
let currentSongs = [];
let latestResult = null;
let latestStatus = null;
let pollTimer = null;
let currentVariationSeed = String(Date.now());
// The wizard API is process-global, so status responses must be tied to the
// project active when the request started. This also invalidates old fetches
// that resolve after Open/New project changes state.
let activeProjectId = null;
let statusPollGeneration = 0;
let prepareHandoffInProgress = false;
let appConfig = { dev: true, desktop: false };
let progressStartedAt = null;
let progressSamples = [];
let progressFloor = 0;
let etaSmoothedSeconds = null;
let rescueClipId = null;
let currentStep = 1;
let captionCues = [];
let captionMarkIndex = 0;
let captionPendingStart = null;
let captionActiveIndex = null;
let lastProgressReportAt = 0;
let trimDefaultsAppliedFor = "";
let cameraRoleWeights = { "360": 50, handheld: 30, fixed_rear: 20 };
let fixedRearMotion = true;
let reelTextOverlays = [];
let reelImageOverlays = [];
let reelVideoOverlays = [];
let reelPlayhead = 0;
let projectLogo = { choice: "none", path: "", url: "", name: "", defaultPath: "", defaultUrl: "" };
let reelDrag = null;
let timelineDrag = null;
let timelineSuppressClick = false;
let selectedComposeOverlay = null;
let selectedComposeLogo = false;
let composePreviewDrag = null;
let copiedComposeOverlay = null;
let copiedCaption = null;
const reelPreviewImages = new Map();
let shotReviewItems = [];
let paperEditCuts = [];
let inputWarningsShown = new Set();
let savedAudioTrim = {};
// Read-only 360 result viewer. The exported file already has its audio embedded
// and needs no separate sync or Director state.
let result360 = {
  ready: false,
  three: null,
  renderer: null,
  scene: null,
  camera: null,
  sphere: null,
  texture: null,
  gl: null,
  yaw: 0,
  pitch: 0,
  fov: 100,
  dragging: false,
  dragX: 0,
  dragY: 0,
  animation: null,
};

const LANDMARK_LABELS = {
  full_stage: "Full stage",
  singer: "Singer",
  drummer: "Drummer",
  left: "Left side",
  right: "Right side",
  audience: "Audience",
  audience_stage_wide: "Audience + stage",
  planet: "Planet",
};

function logFrontendError(message, stack = "") {
  fetch("/api/v1/wizard/frontend-log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, stack, url: window.location.href }),
  }).catch(() => {});
}

function auditBackdropRuntime() {
  const backdrop = document.querySelector("#backdropFlow");
  const logo = document.querySelector("#backdropLogo");
  if (!backdrop) return null;
  const chain = [];
  let node = backdrop;
  while (node) {
    const style = getComputedStyle(node);
    chain.push({
      id: node.id || null,
      tag: node.tagName,
      position: style.position,
      overflowY: style.overflowY,
      scrollTop: node.scrollTop,
      scrollHeight: node.scrollHeight,
      clientHeight: node.clientHeight,
    });
    node = node.parentElement;
  }
  const audit = {
    backdrop: { position: getComputedStyle(backdrop).position, offsetParent: backdrop.offsetParent?.id || backdrop.offsetParent?.tagName || null },
    logo: { position: logo ? getComputedStyle(logo).position : null },
    scrollChain: chain,
    document: { scrollTop: document.documentElement.scrollTop, scrollHeight: document.documentElement.scrollHeight, clientHeight: document.documentElement.clientHeight },
  };
  const scrollingElement = document.scrollingElement || document.documentElement;
  const scrollBefore = scrollingElement.scrollTop;
  const rectBefore = logo?.getBoundingClientRect() ?? null;
  const topBefore = rectBefore?.top ?? null;
  const maxScroll = Math.max(0, scrollingElement.scrollHeight - scrollingElement.clientHeight);
  scrollingElement.scrollTop = maxScroll;
  const rectAfter = logo?.getBoundingClientRect() ?? null;
  const topAfter = rectAfter?.top ?? null;
  const allBackdropNodes = [...document.querySelectorAll('[id*="backdrop"], [class*="backdrop"]')].map((element) => {
    const style = getComputedStyle(element);
    return { id: element.id || null, className: element.className || null, position: style.position, backgroundImage: style.backgroundImage, backgroundAttachment: style.backgroundAttachment, opacity: style.opacity };
  });
  audit.scrollTest = { scrollingElement: scrollingElement.tagName, scrollBefore, scrollAfter: scrollingElement.scrollTop, scrollDelta: scrollingElement.scrollTop - scrollBefore, logoTopBefore: topBefore, logoTopAfter: topAfter, logoTopDelta: topAfter == null || topBefore == null ? null : topAfter - topBefore, logoSizeBefore: rectBefore ? { width: rectBefore.width, height: rectBefore.height } : null, logoSizeAfter: rectAfter ? { width: rectAfter.width, height: rectAfter.height } : null, maxScroll };
  audit.backdropNodes = allBackdropNodes;
  audit.ancestorChecks = chain.map((item) => ({ id: item.id, tag: item.tag, position: item.position, overflowY: item.overflowY }));
  scrollingElement.scrollTop = scrollBefore;
  window.__zuckerBackdropAudit = () => auditBackdropRuntime();
  logFrontendError(`backdrop-runtime-audit: ${JSON.stringify(audit)}`);
  return audit;
}

window.onerror = (message, source, line, column, error) => {
  logFrontendError(`window.onerror: ${message} (${source}:${line}:${column})`, error?.stack || "");
};

window.onunhandledrejection = (event) => {
  const reason = event.reason;
  logFrontendError(`unhandledrejection: ${reason?.message || reason}`, reason?.stack || "");
};

const icons = {
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 12h14"></path><path d="m13 6 6 6-6 6"></path></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"></path></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m8 5 11 7-11 7Z"></path></svg>',
  wand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m15 4 5 5"></path><path d="M14 5 3 16l5 5L19 10Z"></path><path d="M4 4h.01M9 2h.01M2 9h.01M20 15h.01M15 22h.01"></path></svg>',
  doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"></path><path d="M14 2v6h6"></path><path d="M8 13h8M8 17h8"></path></svg>',
  clipboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M8 4h8l1 2h3v14H4V6h3Z"></path><path d="M9 4a3 3 0 0 1 6 0"></path></svg>',
  retry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M20 12a8 8 0 1 1-2.34-5.66"></path><path d="M20 4v6h-6"></path></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="m6 6 1 15h10l1-15"></path><path d="M10 11v6M14 11v6"></path></svg>',
  youtube: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="6" width="18" height="12" rx="4"></rect><path d="m10 9 5 3-5 3Z"></path></svg>',
  instagram: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="4" y="4" width="16" height="16" rx="5"></rect><circle cx="12" cy="12" r="3"></circle><path d="M17 7h.01"></path></svg>',
  tiktok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M14 4v10.5a3.5 3.5 0 1 1-3-3.46"></path><path d="M14 4c1 3 2.7 4.7 5 5"></path></svg>',
  sphere: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="9"></circle><path d="M3 12h18"></path><path d="M12 3c3 2.4 4.5 5.4 4.5 9S15 18.6 12 21"></path><path d="M12 3c-3 2.4-4.5 5.4-4.5 9S9 18.6 12 21"></path></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M6 6l12 12M18 6 6 18"></path></svg>',
};

function todayName() {
  return `Jam ${new Date().toISOString().slice(0, 10)}`;
}

async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || S.error || "Error");
  return data;
}

async function apiForm(path, formData) {
  const response = await fetch(`/api/v1${path}`, { method: "POST", body: formData });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || S.error || "Error");
  return data;
}

async function loadAppConfig() {
  appConfig = await api("/app/config");
  sourceFolders = appConfig.source_folders || [];
  masterAudioExtensions = appConfig.master_audio_extensions || [".mp3"];
  renderSourceFolders();
  renderMasterAudioFilter();
  cameraRoleWeights = normalizeCameraRoleWeights(appConfig.camera_role_weights || cameraRoleWeights);
  applyCameraRoleWeights(cameraRoleWeights);
  fixedRearMotion = appConfig.fixed_rear_motion !== false;
  applyFixedRearMotion(fixedRearMotion);
  savedAudioTrim = appConfig.audio_trim_by_master || {};
  const logoStatus = document.querySelector("#personalLogoStatus");
  if (logoStatus && appConfig.personal_logo_path) logoStatus.textContent = `Using ${filename(appConfig.personal_logo_path)}`;
  window.NativeBridge?.configure(appConfig);
}

function showToast(message, isError = false) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.hidden = false;
  setTimeout(() => {
    toast.hidden = true;
  }, 4500);
}

function setStep(number) {
  currentStep = Math.max(1, Math.min(6, Number(number) || 1));
  document.querySelectorAll(".step").forEach((step, index) => step.classList.toggle("active", index === currentStep - 1));
  document.querySelectorAll("[data-step-nav]").forEach((button) => button.classList.toggle("active", Number(button.dataset.stepNav) === currentStep));
}

function captionBlocksFromText() {
  const text = document.querySelector("#captionText")?.value || "";
  return text.split(/(?:\r?\n){2,}/).filter((block) => block !== "");
}

function renderCaptionBlocks() {
  const root = document.querySelector("#captionBlocks");
  if (!root) return;
  const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const focusedKey = focused ? Object.keys(focused.dataset).find((key) => key.startsWith("caption")) : null;
  const focusedValue = focusedKey ? focused.dataset[focusedKey] : null;
  const selectionStart = typeof focused?.selectionStart === "number" ? focused.selectionStart : null;
  const selectionEnd = typeof focused?.selectionEnd === "number" ? focused.selectionEnd : null;
  const videoDuration = Number(document.querySelector("#composeVideo")?.duration || 0);
  root.innerHTML = captionCues.map((cue, index) => {
    const end = cue.end == null ? (videoDuration || Number(cue.start) || 0) : Number(cue.end);
    const active = captionActiveIndex === index ? " active" : "";
    const override = cue.style_override || {};
    const animationOptions = (value) => ["none", "fade", "slide", "scale"].map((name) => `<option value="${name}" ${String(value || "none") === name ? "selected" : ""}>${name}</option>`).join("");
    return `<div class="caption-block${active}" data-caption-seek="${index}" role="button" tabindex="0"><div class="caption-block-main"><strong>${index + 1}.</strong><input class="caption-row-text" data-caption-text="${index}" value="${escapeHtml(cue.lines.join("\n"))}" aria-label="Caption ${index + 1} text" /><div class="caption-time-fields"><label>Start <input data-caption-start="${index}" type="number" min="0" step="0.01" value="${Number(cue.start).toFixed(2)}" /></label><label>End <input data-caption-end="${index}" type="number" min="0" step="0.01" value="${end.toFixed(2)}" /></label><label>Duration <input data-caption-duration="${index}" type="number" min="0.05" step="0.01" value="${Math.max(0.05, end - Number(cue.start)).toFixed(2)}" /></label></div><div class="caption-style-overrides"><label>Color <input data-caption-color="${index}" type="color" value="${override.color || "#ffffff"}" /></label><label>Size <input data-caption-size="${index}" type="number" min="8" max="180" step="1" value="${override.size ?? ""}" /></label><label>Vertical % <input data-caption-vertical="${index}" type="number" min="0" max="100" step="1" value="${override.vertical ?? ""}" /></label><label>Outline <input data-caption-outline-color="${index}" type="color" value="${override.outline_color || "#101010"}" /></label><label>Stroke <input data-caption-outline-width="${index}" type="number" min="0" max="20" step="0.5" value="${override.outline_width ?? ""}" /></label><label>Shadow distance <input data-caption-shadow-distance="${index}" type="number" min="0" max="30" step="1" value="${override.shadow_distance ?? ""}" /></label><label>Shadow opacity <input data-caption-shadow-opacity="${index}" type="number" min="0" max="1" step="0.05" value="${override.shadow_opacity ?? ""}" /></label><label>Glow color <input data-caption-glow-color="${index}" type="color" value="${override.glow_color || "#ffff00"}" /></label><label>Glow blur <input data-caption-glow-blur="${index}" type="number" min="0" max="40" step="1" value="${override.glow_blur ?? ""}" /></label><label>Glow layers <input data-caption-glow-layers="${index}" type="number" min="0" max="8" step="1" value="${override.glow_layers ?? ""}" /></label><label>Glow intensity <input data-caption-glow-intensity="${index}" type="number" min="0" max="1" step="0.05" value="${override.glow_intensity ?? ""}" /></label><label>Enter <select data-caption-animation-in="${index}">${animationOptions(override.animation_in || "fade")}</select></label><label>Exit <select data-caption-animation-out="${index}">${animationOptions(override.animation_out || "fade")}</select></label></div></div><div class="caption-block-actions"><button type="button" class="icon-button small" data-caption-edit="${index}">Edit</button><button type="button" class="icon-button small" data-caption-delete="${index}">Delete</button></div></div>`;
  }).join("");
  root.querySelectorAll('input[type="number"][data-caption-start], input[type="number"][data-caption-end], input[type="number"][data-caption-duration], input[type="number"][data-caption-size], input[type="number"][data-caption-vertical], input[type="number"][data-caption-outline-width], input[type="number"][data-caption-shadow-distance], input[type="number"][data-caption-shadow-opacity], input[type="number"][data-caption-glow-blur], input[type="number"][data-caption-glow-layers], input[type="number"][data-caption-glow-intensity]').forEach((input) => {
    const key = Object.keys(input.dataset).find((name) => name.startsWith("caption"));
    const style = ["captionSize", "captionVertical", "captionOutlineWidth", "captionShadowDistance", "captionShadowOpacity", "captionGlowBlur", "captionGlowLayers", "captionGlowIntensity"].includes(key);
    const label = input.closest("label");
    const labelText = label?.textContent?.trim() || key || "Value";
    if (label) { label.title = labelText; label.setAttribute("aria-label", labelText); const first = label.firstChild; if (first?.nodeType === Node.TEXT_NODE) first.textContent = `${style ? "◈" : labelText} `; }
    const oldValue = input.value;
    input.type = "range";
    if (["captionStart", "captionEnd", "captionDuration"].includes(key)) input.max = String(Math.max(1, videoDuration || 600));
    if (style && input.value === "") input.value = input.min || "0";
    const output = document.createElement("output");
    output.textContent = oldValue === "" ? "auto" : oldValue;
    label?.append(output);
    input.setAttribute("aria-label", labelText);
    input.classList.add("compact-range");
  });
  if (focusedKey && focusedValue != null) {
    const selector = `[data-${focusedKey.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}="${focusedValue}"]`;
    const restored = root.querySelector(selector);
    if (restored instanceof HTMLElement) {
      restored.focus({ preventScroll: true });
      if (selectionStart != null && "setSelectionRange" in restored) restored.setSelectionRange(selectionStart, selectionEnd ?? selectionStart);
    }
  }
  const preview = document.querySelector("#captionPreviewText");
  if (preview) preview.textContent = captionCues[0]?.lines?.join("\n") || "Caption preview";
  const lane = document.querySelector("#composeCaptionLane");
  if (lane) lane.innerHTML = captionCues.map((cue, index) => { const duration = Math.max(1, videoDuration || 30); const end = cue.end == null ? duration : Number(cue.end); return `<span class="compose-timeline-block caption" data-caption-seek="${index}" style="left:${Math.max(0, Number(cue.start) / duration * 100)}%;width:${Math.max(1, (end - Number(cue.start)) / duration * 100)}%">${index + 1}</span>`; }).join("");
  renderComposeTimeline();
  renderComposeOverlayLayer();
}

function migrateTextOverlaysToCaptions() {
  if (!reelTextOverlays.length) return;
  const migrated = reelTextOverlays.map((item) => ({
    lines: String(item.text || "").split(/\r?\n/),
    start: Number(item.start_sec || 0),
    end: Number(item.start_sec || 0) + Math.max(0.1, Number(item.duration_sec || 3)),
    style_override: {
      color: item.color || "#ffffff",
      size: Number(item.size || 54),
      vertical: item.y != null ? Number(item.y) * 100 : 50,
      animation_in: item.animation || "fade",
      animation_out: item.animation || "fade",
    },
  })).filter((cue) => cue.lines.join("").trim());
  captionCues = [...captionCues, ...migrated];
  reelTextOverlays = [];
  syncCaptionTextArea();
}

function syncCaptionTextArea() {
  const textarea = document.querySelector("#captionText");
  if (textarea) textarea.value = captionCues.map((cue) => cue.lines.join("\n")).join("\n\n");
}

function closeActiveCaption(endTime) {
  if (captionActiveIndex == null || !captionCues[captionActiveIndex]) return;
  const cue = captionCues[captionActiveIndex];
  cue.end = Math.max(Number(cue.start) + 0.05, Number(endTime));
  captionActiveIndex = null;
  captionPendingStart = null;
  captionMarkIndex = captionCues.length;
  renderCaptionBlocks();
}

function addCaptionFromPlayback() {
  const video = document.querySelector("#composeVideo");
  if (!video) return;
  const now = Number(video.currentTime || 0);
  if (captionActiveIndex != null) closeActiveCaption(now);
  const index = captionCues.length;
  const nextStart = captionCues.slice(index).map((cue) => Number(cue.start)).filter((start) => start > now).sort((a, b) => a - b)[0];
  const defaultEnd = Math.min(Number(video.duration || Infinity), nextStart ?? now + 1.0);
  captionCues.push({ lines: [""], start: now, end: Math.max(now + 0.05, defaultEnd) });
  captionActiveIndex = index;
  captionMarkIndex = index;
  captionPendingStart = now;
  video.pause();
  syncCaptionTextArea();
  renderCaptionBlocks();
  const field = document.querySelector(`[data-caption-text="${index}"]`);
  if (field) { field.focus(); field.select(); }
  const status = document.querySelector("#captionTapStatus");
  if (status) status.textContent = `Caption ${index + 1} starts at ${now.toFixed(2)}s. Type it, then press Enter or play.`;
}

function composeTimelineDuration() {
  return Math.max(1, Number(document.querySelector("#composeVideo")?.duration || document.querySelector("#reelDuration")?.value || 30));
}

function renderComposeTimeline() {
  const duration = composeTimelineDuration();
  const zoom = Number(document.querySelector("#composeTimelineZoom")?.value || 1);
  const overlayLane = document.querySelector("#composeOverlayLane");
  const captionLane = document.querySelector("#composeCaptionLane");
  const overlayItems = [...reelImageOverlays.map((item, index) => ({ ...item, _kind: "image", _index: index })), ...reelVideoOverlays.map((item, index) => ({ ...item, _kind: "video", _index: index }))];
  const block = (item) => {
    const selected = selectedComposeOverlay?.kind === item._kind && selectedComposeOverlay.index === item._index ? " selected" : "";
    const start = Math.max(0, Number(item.start_sec || 0));
    const width = Math.max(0.1, Number(item.duration_sec || 0.1));
    return `<span class="compose-timeline-block overlay ${item._kind}${selected}" data-overlay-kind="${item._kind}" data-overlay-index="${item._index}" tabindex="0" style="left:${start / duration * 100}%;width:${width / duration * 100}%"><span class="timeline-handle left" data-timeline-resize="left"></span><span class="timeline-block-label">${item._kind === "video" ? "Video" : "Image"}</span><span class="timeline-handle right" data-timeline-resize="right"></span></span>`;
  };
  if (overlayLane) overlayLane.innerHTML = `${overlayItems.map(block).join("")}<span class="compose-timeline-playhead"></span>`;
  if (captionLane) { const captions = captionCues.map((cue, index) => { const end = cue.end == null ? duration : Number(cue.end); return `<span class="compose-timeline-block caption" data-caption-seek="${index}" style="left:${Math.max(0, Number(cue.start) / duration * 100)}%;width:${Math.max(0.1, (end - Number(cue.start)) / duration * 100)}%">${index + 1}</span>`; }).join(""); captionLane.innerHTML = `${captions}<span class="compose-timeline-playhead"></span>`; }
  document.querySelectorAll(".compose-lane").forEach((lane) => { lane.style.width = `${zoom * 100}%`; lane.style.minWidth = "100%"; });
  updateComposeTimelinePlayhead();
}

function updateComposeTimelinePlayhead() {
  const video = document.querySelector("#composeVideo");
  const duration = composeTimelineDuration();
  const now = Number(video?.currentTime || 0);
  const percent = Math.max(0, Math.min(100, now / duration * 100));
  document.querySelectorAll(".compose-timeline-playhead").forEach((head) => { head.style.left = `${percent}%`; });
  document.querySelectorAll("[data-compose-timeline]").forEach((scroll) => {
    const x = percent / 100 * scroll.scrollWidth;
    if (x < scroll.scrollLeft || x > scroll.scrollLeft + scroll.clientWidth) scroll.scrollLeft = Math.max(0, x - scroll.clientWidth * .25);
  });
}

function seekFromComposeTimeline(event, scroll) {
  const video = document.querySelector("#composeVideo");
  if (!video) return;
  const lane = scroll.querySelector(".compose-lane");
  const rect = lane.getBoundingClientRect();
  const position = Math.max(0, Math.min(lane.scrollWidth, event.clientX - rect.left + scroll.scrollLeft));
  video.currentTime = position / lane.scrollWidth * composeTimelineDuration();
  renderComposeOverlayLayer();
}

function editableOverlayMarkup(kind, item, index, selected, content) {
  const position = `left:${Number(item.x ?? .5) * 100}%;top:${Number(item.y ?? .5) * 100}%;width:${Number(item.width ?? .35) * 100}%;opacity:${item.opacity ?? 1}`;
  const identity = kind === "logo" ? `data-compose-preview-kind="logo"` : `data-compose-preview-kind="${kind}" data-compose-preview-index="${index}"`;
  return `<div class="compose-live-overlay compose-live-${kind}-wrap${selected ? " selected" : ""}" ${identity} style="${position}">${content}<i class="compose-overlay-handle nw" data-compose-resize="nw" aria-label="Resize ${kind} top left"></i><i class="compose-overlay-handle ne" data-compose-resize="ne" aria-label="Resize ${kind} top right"></i><i class="compose-overlay-handle sw" data-compose-resize="sw" aria-label="Resize ${kind} bottom left"></i><i class="compose-overlay-handle se" data-compose-resize="se" aria-label="Resize ${kind} bottom right"></i></div>`;
}

function imageEffectMarkup(item) {
  const tint = item.tint_color || "#ffffff";
  const tintOpacity = Math.max(0, Math.min(1, Number(item.tint_opacity || 0)));
  const shadowDistance = Math.max(0, Number(item.shadow_distance || 0));
  const shadowBlur = Math.max(0, Number(item.shadow_blur || 0));
  const shadowOpacity = Math.max(0, Math.min(1, Number(item.shadow_opacity || 0)));
  const glowBlur = Math.max(0, Number(item.glow_blur || 0));
  const glowLayers = Math.max(0, Math.min(8, Number(item.glow_layers || 0)));
  const shadow = shadowOpacity && (shadowDistance || shadowBlur) ? `drop-shadow(${shadowDistance}px ${shadowDistance}px ${shadowBlur}px ${item.shadow_color || "#000000"}${Math.round(shadowOpacity * 255).toString(16).padStart(2, "0")})` : "";
  const glow = glowLayers && glowBlur ? Array.from({ length: glowLayers }, (_, i) => `drop-shadow(0 0 ${Math.max(1, glowBlur * (i + 1) / glowLayers)}px ${item.glow_color || tint})`).join(" ") : "";
  const filter = [shadow, glow].filter(Boolean).join(" ");
  return `<span class="compose-image-effect" style="--tint-color:${escapeHtml(tint)};--tint-opacity:${tintOpacity};filter:${escapeHtml(filter)}">${content}</span>`;
}

function renderComposeOverlayLayer() {
  const layer = document.querySelector("#composeOverlayLayer");
  const video = document.querySelector("#composeVideo");
  if (!layer || !video) return;
  const now = Number(video.currentTime || 0);
  const caption = captionCues.find((cue) => now >= Number(cue.start) && now <= (cue.end == null ? Number(video.duration || Infinity) : Number(cue.end)));
  const captionStyle = document.querySelector("#captionStyle")?.value || "karaoke_word";
  const texts = reelTextOverlays.filter((item) => now >= Number(item.start_sec || 0) && now <= Number(item.start_sec || 0) + Number(item.duration_sec || 0));
  const images = reelImageOverlays.map((item, index) => ({ ...item, _index: index })).filter((item) => now >= Number(item.start_sec || 0) && now <= Number(item.start_sec || 0) + Number(item.duration_sec || 0));
  const videos = reelVideoOverlays.map((item, index) => ({ ...item, _index: index })).filter((item) => now >= Number(item.start_sec || 0) && now <= Number(item.start_sec || 0) + Number(item.duration_sec || 0));
  const override = caption?.style_override || {};
  const shadowDistance = Number(override.shadow_distance ?? 3);
  const glowLayers = Math.max(0, Math.min(8, Number(override.glow_layers || 0)));
  const glowBlur = Math.max(0, Number(override.glow_blur || 0));
  const glow = glowLayers && glowBlur ? Array.from({ length: glowLayers }, () => `0 0 ${glowBlur}px ${override.glow_color || "#ffff00"}`).join(",") : "";
  const shadow = shadowDistance > 0 ? `${shadowDistance}px ${shadowDistance}px ${override.shadow_color || "#000"}` : "";
  const captionInline = [`color:${override.color || "#fff"}`, override.size ? `font-size:${Number(override.size) / 3}px` : "", override.vertical != null ? `top:${Number(override.vertical)}%` : "", override.outline_width != null ? `-webkit-text-stroke:${Number(override.outline_width)}px ${override.outline_color || "#101010"}` : "", glow || shadow ? `text-shadow:${[glow, shadow].filter(Boolean).join(",")}` : ""].filter(Boolean).join(";");
  const captionOverride = caption?.style_override || {};
  const captionAnimation = ` caption-animation-in-${escapeHtml(captionOverride.animation_in || "fade")} caption-animation-out-${escapeHtml(captionOverride.animation_out || "fade")}`;
  const logoSource = selectedLogoSource();
  const logo = logoSource === "custom" ? projectLogo.url : logoSource === "default" ? projectLogo.defaultUrl : "";
  const logoOverlay = projectLogo.overlay || { x: .5, y: .08, width: .22 };
  const logoMarkup = logo ? editableOverlayMarkup("logo", logoOverlay, null, selectedComposeLogo, `<img class="compose-live-logo" src="${escapeHtml(logo)}" alt="Project logo" />`) : "";
  const imageMarkup = images.map((item) => editableOverlayMarkup("image", item, item._index, selectedComposeOverlay?.kind === "image" && selectedComposeOverlay.index === item._index, imageEffectMarkup(item).replace("</span>", `<img class="compose-live-image" src="${escapeHtml(item.preview_url || item.path || '')}" alt="Flyer overlay" /></span>`))).join("");
  const videoMarkup = videos.map((item) => editableOverlayMarkup("video", item, item._index, selectedComposeOverlay?.kind === "video" && selectedComposeOverlay.index === item._index, `<video class="compose-live-video" src="${escapeHtml(item.preview_url || item.path || '')}" autoplay muted loop playsinline aria-label="Video overlay"></video>`)).join("");
  layer.innerHTML = logoMarkup + imageMarkup + videoMarkup + (caption ? `<span class="compose-live-caption caption-style-${escapeHtml(captionStyle)}${captionAnimation}" data-caption-preview-index="${captionCues.indexOf(caption)}" tabindex="0" style="${captionInline}">${escapeHtml(caption.lines.join("\n"))}</span>` : "");
  const editing = Boolean(selectedComposeLogo || selectedComposeOverlay || captionActiveIndex != null);
  document.querySelector(".compose-player-wrap")?.classList.toggle("overlay-editing", editing);
  video.controls = !editing;
  const time = document.querySelector("#composeTime"); if (time) time.textContent = `${Math.floor(now / 60).toString().padStart(2, '0')}:${(now % 60).toFixed(2).padStart(5, '0')}`;
  const scrub = document.querySelector("#composeScrub"); if (scrub && Number.isFinite(video.duration)) { scrub.max = String(video.duration); scrub.value = String(now); }
}

async function openCaptions() {
  setStep(5);
  const video = document.querySelector("#composeVideo");
  if (video) {
    video.src = latestResult?.media_url ? `${latestResult.media_url}?t=${Date.now()}` : "/api/v1/wizard/result";
    video.load();
    video.onplay = () => { const button = document.querySelector("#composePlayPause"); if (button) button.textContent = "Pause"; };
    video.onpause = () => { const button = document.querySelector("#composePlayPause"); if (button) button.textContent = "Play"; };
    video.onloadedmetadata = () => { const scrub = document.querySelector("#composeScrub"); if (scrub) scrub.max = String(video.duration || 1); renderCaptionBlocks(); renderComposeOverlayLayer(); };
    video.ontimeupdate = () => { renderComposeOverlayLayer(); updateComposeTimelinePlayhead(); };
  }
  migrateTextOverlaysToCaptions();
  let savedComposeStyle = null;
  if (!captionCues.length && !reelTextOverlays.length && !reelImageOverlays.length && !reelVideoOverlays.length) {
    try {
      const saved = await api("/wizard/compose");
      const overlaySpec = saved.overlay_spec || {};
      const cueTrack = saved.cue_track || {};
      reelTextOverlays = Array.isArray(overlaySpec.texts) ? overlaySpec.texts : [];
      reelImageOverlays = Array.isArray(overlaySpec.images) ? overlaySpec.images : [];
      reelVideoOverlays = Array.isArray(overlaySpec.videos) ? overlaySpec.videos : [];
      captionCues = Array.isArray(cueTrack.cues) ? cueTrack.cues : [];
      savedComposeStyle = cueTrack.style || null;
      const textarea = document.querySelector("#captionText");
      if (textarea) textarea.value = captionCues.map((cue) => (cue.lines || []).join("\n")).join("\n\n");
    } catch (error) {
      logFrontendError(`saved composition state failed: ${error.message}`, error.stack || "");
    }
  }
  loadProjectLogo().catch(() => {});
  loadFlyerLibrary().catch(() => {});
  const styles = await api("/captions/styles");
  const select = document.querySelector("#captionStyle");
  if (select && !select.options.length) select.innerHTML = (styles.styles || []).map((style) => `<option value="${escapeHtml(style.name)}">${escapeHtml(style.name.replaceAll("_", " "))}</option>`).join("");
  if (select) select.onchange = () => document.querySelector("#captionPreview")?.setAttribute("data-style", select.value);
  if (select && !select.dataset.userChoice) select.value = savedComposeStyle || "karaoke_word";
  const blocks = captionBlocksFromText();
  if (!captionCues.length && blocks.length) captionCues = blocks.map((text, index) => ({ lines: text.split(/\r?\n/), start: index * 4, end: index * 4 + 4 }));
  renderCaptionBlocks();
  renderReelOptions();
}

function captionTap() {
  const video = document.querySelector("#composeVideo");
  if (!video) return;
  if (captionPendingStart == null) {
    captionPendingStart = video.currentTime;
    document.querySelector("#captionTapStatus").textContent = `Block ${captionMarkIndex + 1} start: ${video.currentTime.toFixed(2)}s — press Space again for end.`;
    return;
  }
  const blocks = captionBlocksFromText();
  const text = blocks[captionMarkIndex] || "";
  captionCues[captionMarkIndex] = { lines: text.split(/\r?\n/), start: captionPendingStart, end: Math.max(video.currentTime, captionPendingStart + 0.1) };
  captionMarkIndex += 1; captionPendingStart = null;
  document.querySelector("#captionTapStatus").textContent = `Block ${captionMarkIndex} marked. Press Space to mark the next block.`;
  renderCaptionBlocks();
}

function exportCaptionSrt() {
  const stamp = (seconds) => { const ms = Math.max(0, Math.round(seconds * 1000)); const h = Math.floor(ms / 3600000); const m = Math.floor(ms % 3600000 / 60000); const s = Math.floor(ms % 60000 / 1000); return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")},${String(ms % 1000).padStart(3,"0")}`; };
  const duration = composeTimelineDuration();
  const text = captionCues.map((cue, i) => `${i + 1}\n${stamp(cue.start)} --> ${stamp(cue.end == null ? duration : cue.end)}\n${cue.lines.join("\n")}`).join("\n\n") + "\n";
  const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([text], { type: "text/srt" })); link.download = "captions.srt"; link.click(); URL.revokeObjectURL(link.href);
}

async function burnCaptionTrack() {
  const blocks = captionBlocksFromText();
  if (!captionCues.length || captionCues.length !== blocks.length) captionCues = blocks.map((text, index) => ({ lines: text.split(/\r?\n/), start: index * 4, end: index * 4 + 4 }));
  const style = document.querySelector("#captionStyle")?.value || "clean_bottom";
  const cues = captionCues.map((cue) => ({ ...cue, end: cue.end == null ? composeTimelineDuration() : cue.end }));
  const result = await api("/captions/burn", { method: "POST", body: JSON.stringify({ style, cues, header: { title_enabled: false, title: "", logo_source: selectedLogoSource(), logo_height: 120, logo_overlay: projectLogo.overlay || { x: .5, y: .08, width: .22 } }, letterbox: { enabled: true, blur: 18 } }) });
  const video = document.querySelector("#composeVideo"); if (video) { video.src = `${result.media_url}?t=${Date.now()}`; video.load(); }
  document.querySelector("#captionBurnStatus").textContent = `Created ${result.filename}`;
}

async function saveComposition() {
  const cues = captionCues.map((cue) => ({ ...cue, end: cue.end == null ? composeTimelineDuration() : cue.end }));
  const header = { title_enabled: false, title: "", logo_source: selectedLogoSource(), logo_overlay: projectLogo.overlay || { x: .5, y: .08, width: .22 } };
  return api("/wizard/compose", {
    method: "POST",
    body: JSON.stringify({ texts: reelTextOverlays, images: reelImageOverlays, videos: reelVideoOverlays, cues, style: document.querySelector("#captionStyle")?.value || "karaoke_word", header, letterbox: { enabled: true, blur: 18 } })
  });
}

function projectIdFromStatus(status) {
  return status?.project_id || status?.project_path || status?.result?.project_path || null;
}

function sameProjectId(left, right) {
  if (!left || !right) return left === right;
  return String(left).replace(/\\/g, "/") === String(right).replace(/\\/g, "/");
}

function stopStatusPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
  statusPollGeneration += 1;
}

function resetProgressTiming() {
  progressStartedAt = null;
  progressSamples = [];
  progressFloor = 0;
  etaSmoothedSeconds = null;
}

function choosePlatformInUi(platform) {
  if (!platform || !["youtube", "reel", "360", "backstage"].includes(platform)) return;
  selectedPlatform = platform;
  try { sessionStorage.setItem("zucker.selectedPlatform", platform); } catch (_error) { /* private mode */ }
  document.querySelectorAll(".platform-card").forEach((card) => card.classList.toggle("selected", card.dataset.platform === platform));
  document.querySelector("#startWizard").disabled = false;
  applyEditTypeMode();
}

function restoreSelectedPlatform() {
  try { choosePlatformInUi(sessionStorage.getItem("zucker.selectedPlatform")); } catch (_error) { /* private mode */ }
}

function hideStageTransition() {
  transitionVersion += 1;
  if (transitionTimer) clearTimeout(transitionTimer);
  transitionTimer = null;
  document.querySelector("#stageTransition")?.setAttribute("hidden", "");
  document.querySelector("#progressBox")?.removeAttribute("hidden");
}

function stopAllPreviewAudio() {
  // Phase changes are terminal for previews.  Pause every media element so a
  // hidden master/director preview cannot keep playing underneath the next
  // stage (or become impossible to reach again after the automatic advance).
  document.querySelectorAll("audio, video").forEach((media) => {
    try { media.pause(); } catch (_error) { /* stale media element */ }
  });
  const resultPlay = document.querySelector("#result360Play");
  if (resultPlay) resultPlay.textContent = "Play";
}

function showStageTransition(title, detail, mode, onDone = null) {
  const box = document.querySelector("#stageTransition");
  if (!box) return;
  stopAllPreviewAudio();
  const version = ++transitionVersion;
  if (transitionTimer) clearTimeout(transitionTimer);
  document.querySelector("#stageTransitionMode").textContent = mode;
  document.querySelector("#stageTransitionTitle").textContent = title;
  document.querySelector("#stageTransitionDetail").textContent = detail;
  document.querySelector("#progressBox")?.setAttribute("hidden", "");
  document.querySelector("#reviewBox")?.setAttribute("hidden", "");
  box.removeAttribute("hidden");
  transitionTimer = setTimeout(() => {
    if (transitionVersion !== version) return;
    box.setAttribute("hidden", "");
    document.querySelector("#progressBox")?.removeAttribute("hidden");
    if (onDone) onDone();
  }, 1800);
}

// Keep the transition copy mode-specific. These functions intentionally do
// not share any chooser, coverage, or render decision between pipelines.
function showYouTubeSyncToCut() {
  showStageTransition("Sync complete — Cut is starting", "Your YouTube mode is locked. Building the synchronized coverage now.", "YouTube");
}

function showReelSyncToCut() {
  showStageTransition("Sync complete — Cut is starting", "Your Reel mode is locked. Building the Reel coverage now.", "Reel");
}

function show360SyncToCut() {
  showStageTransition("Sync complete — Cut is starting", "Your 360 mode is locked. Preparing the single-camera coverage now.", "360");
}

function showYouTubeReviewReady() {
  showStageTransition("Review shots are ready", "Your synchronized YouTube edit is prepared. Opening Review shots now — choose your takes.", "YouTube", () => {
    setStep(4);
    document.querySelector("#progressBox")?.setAttribute("hidden", "");
    const reviewBox = document.querySelector("#reviewBox");
    reviewBox?.removeAttribute("hidden");
    document.querySelector("#reviewReadyNotice")?.removeAttribute("hidden");
    reviewBox?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function showReelReviewReady() {
  showStageTransition("Review shots are ready", "Your Reel edit is prepared. Opening Review shots now — choose your takes.", "Reel", () => {
    setStep(4);
    document.querySelector("#progressBox")?.setAttribute("hidden", "");
    const reviewBox = document.querySelector("#reviewBox");
    reviewBox?.removeAttribute("hidden");
    document.querySelector("#reviewReadyNotice")?.removeAttribute("hidden");
    reviewBox?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function showReviewReadyForPlatform(platform) {
  if (platform === "youtube") showYouTubeReviewReady();
  else if (platform === "reel") showReelReviewReady();
}

function show360EditToExport() {
  showStageTransition("Edit complete — Export is starting", "Your 360 passthrough edit is ready. Exporting the single equirectangular video now.", "360");
}

function injectIcons() {
  document.querySelectorAll("[data-icon]").forEach((node) => {
    const svg = icons[node.dataset.icon];
    if (svg && !node.querySelector("svg")) node.insertAdjacentHTML("afterbegin", svg);
  });
  document.querySelectorAll("[data-glyph]").forEach((node) => {
    node.innerHTML = icons[node.dataset.glyph] || "";
  });
}

function parseLocaleNumber(value) {
  const text = String(value ?? "").trim().replace(/\s/g, "");
  if (!text) return null;
  let normalized = text;
  if (text.includes(",") && text.includes(".")) {
    normalized = text.lastIndexOf(",") > text.lastIndexOf(".") ? text.replace(/\./g, "").replace(",", ".") : text.replace(/,/g, "");
  } else if (text.includes(",")) {
    normalized = text.replace(",", ".");
  }
  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

function formatCanonicalNumber(value) {
  const number = parseLocaleNumber(value);
  if (number == null) return "";
  return Number(number.toFixed(3)).toString();
}

function normalizeYaw(value) {
  const number = parseLocaleNumber(value);
  return number == null ? null : ((number % 360) + 360) % 360;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

// Pointer-drag-to-camera sensitivity for the 360 viewers (Director capture
// and the Result preview). Calibrated at REFERENCE_FOV_DEG (the default
// "shot width" of 100deg): a 100px drag there yields 10deg yaw / 7.5deg
// pitch. Scaled by dragSensitivityScale() below so the same physical mouse
// movement always sweeps the same PROPORTION of the current view, instead
// of a fixed degrees-per-pixel value that feels increasingly "hard"/twitchy
// the more a user zooms in (a narrower FOV means the same fixed degree
// change covers a much bigger fraction of what's actually visible).
const YAW_DEG_PER_PX = 0.10;
const PITCH_DEG_PER_PX = 0.075;
const REFERENCE_FOV_DEG = 100;
// Shots can be pulled right back to a full-sphere / tiny-planet look. The
// stored value (and the server-side preview + export) goes this wide via a
// stereographic projection; the live WebGL viewers clamp their own perspective
// camera to <180° internally (verticalFovFromHorizontal), so this only widens
// what can be authored, not what a rectilinear camera is asked to render.
const MIN_SHOT_FOV = 30;
const MAX_SHOT_FOV = 300;

function dragSensitivityScale(currentFov) {
  const fov = Number(currentFov) || REFERENCE_FOV_DEG;
  return clamp(fov / REFERENCE_FOV_DEG, 0.3, 2.0);
}

function signedYawDelta(yaw, center) {
  return ((yaw - center + 540) % 360) - 180;
}

function stageCenterYaw() {
  const input = document.querySelector('fieldset[data-spherical-landmark="full_stage"] [data-field="yaw"]');
  const value = normalizeYaw(input?.value);
  return value == null ? 0 : value;
}

function weightToFrequency(weight) {
  const value = parseLocaleNumber(weight);
  if (value == null || value <= 0) return "0";
  if (value <= 2) return "1";
  if (value <= 8) return "5";
  if (value <= 24) return "15";
  return "40";
}

function selectedSphericalSourcePath() {
  return detected.videos.find(isSphericalVideo)?.path || "";
}

function mergeDetected(result, source = "") {
  if (result.analysis) inboxAnalysis = result.analysis;
  if (result.source_folders) {
    sourceFolders = result.source_folders;
    renderSourceFolders();
  }
  if (result.master_audio_extensions) {
    masterAudioExtensions = result.master_audio_extensions;
    renderMasterAudioFilter();
  }
  for (const key of ["master", "songs", "videos", "ignored"]) {
    const existing = new Set(detected[key].map((item) => item.path));
    for (const item of result[key] || []) {
      if (existing.has(item.path)) continue;
      if (key === "master" && !masterAudioExtensions.includes(`.${filename(item.path).split(".").pop().toLowerCase()}`)) continue;
      detected[key].push({ ...item, source });
    }
  }
  chooseDefaultMaster();
  renderChips();
}

function clearDetected() {
  for (const key of ["master", "songs", "videos", "ignored"]) detected[key] = [];
  selectedMasterPath = null;
  analysisSetAsideVideos = [];
  inboxAnalysis = null;
}

function resetSphericalSetupToGlobal() {
  // 360 landmarks are persisted configuration, not editable per-export UI.
}

function recordToDetectedItem(record, kind, source = "project") {
  if (!record?.path) return null;
  return {
    ...record,
    kind,
    filename: filename(record.path),
    note: kind === "master" ? S.registeredMaster : kind === "songs" ? S.registeredSongs : S.preparedVideo,
    source,
  };
}

async function resumeInputsFromProject() {
  const project = await api("/project");
  currentVariationSeed = String(project.settings?.wizard?.variation_seed || currentVariationSeed);
  const savedPlatform = project.settings?.wizard?.platform;
  if (savedPlatform) choosePlatformInUi(savedPlatform);
  const inputs = project.inputs || {};
  for (const warning of inputs.warnings || []) {
    if (!inputWarningsShown.has(warning)) {
      inputWarningsShown.add(warning);
      showToast(warning, true);
    }
  }
  clearDetected();
  const master = recordToDetectedItem(inputs.master, "master");
  const songs = recordToDetectedItem(inputs.songs, "songs");
  if (master) detected.master.push(master);
  if (songs) detected.songs.push(songs);
  for (const record of inputs.videos || []) {
    if (record.status === "not_a_video") {
      detected.ignored.push({ ...recordToDetectedItem(record, "ignored"), note: record.not_a_video_reason || S.ignored });
    } else {
      detected.videos.push(recordToDetectedItem(record, "videos"));
    }
  }
  chooseDefaultMaster();
  renderChips();
  document.querySelector("#videoName").value = project.name || document.querySelector("#videoName").value || todayName();
  if (project.settings?.wizard?.audio_trim && inputs.master?.path) {
    savedAudioTrim[inputs.master.path] = project.settings.wizard.audio_trim;
    trimDefaultsAppliedFor = "";
  }
  resetSphericalSetupToGlobal();
  if (project.settings?.edit?.camera_role_weights) {
    cameraRoleWeights = normalizeCameraRoleWeights(project.settings.edit.camera_role_weights);
    applyCameraRoleWeights(cameraRoleWeights);
  }
  if (project.settings?.edit && "fixed_rear_motion" in project.settings.edit) {
    fixedRearMotion = project.settings.edit.fixed_rear_motion !== false;
    applyFixedRearMotion(fixedRearMotion);
  }
  if (inputs.songs?.path) {
    const result = await api("/wizard/songs", { method: "POST", body: JSON.stringify({ songs: inputs.songs.path }) });
    renderSongOptions(result.songs || []);
  } else {
    renderSongOptions([]);
  }
}

function chooseDefaultMaster() {
  if (selectedMasterPath && detected.master.some((item) => item.path === selectedMasterPath)) return;
  const sorted = [...detected.master].sort((a, b) => Number(b.duration || 0) - Number(a.duration || 0));
  selectedMasterPath = sorted[0]?.path || null;
}

function iconFor(item) {
  if (item.kind === "videos") return "🎬";
  if (item.kind === "master") return "🎵";
  if (item.kind === "songs") return "📄";
  return "•";
}

function isHelpfulWarning(item) {
  const suffix = filename(item.path).toLowerCase().split(".").pop();
  return item.kind === "ignored" && ["insv", "insp", "lrv"].includes(suffix);
}

function isRaw360(item) {
  return Boolean(item.raw_360 || item.projection === "raw_insv" || item.probe?.projection === "raw_insv");
}

function isSphericalVideo(item) {
  return Boolean(isRaw360(item) || item.projection === "equirect" || item.probe?.projection === "equirect");
}

function filename(path) {
  return String(path || "").split(/[\\/]/).pop();
}

function renderChips() {
  const root = document.querySelector("#chips");
  applyInboxAnalysisFilter();
  applySessionFilter();
  const items = [...detected.videos, ...detected.master, ...detected.songs, ...detected.ignored].sort((a, b) =>
    String(a.source_folder || "￿").localeCompare(String(b.source_folder || "￿"))
  );
  let previousFolder = "";
  root.innerHTML = items
    .map(
      (item) => {
        const folder = item.source_folder || "";
        const heading = folder && folder !== previousFolder
          ? `<div class="source-folder-heading">${escapeHtml(sourceFolderName(folder))}</div>`
          : "";
        previousFolder = folder;
        return `${heading}
        <span class="chip ${item.kind === "ignored" ? "muted" : ""} ${isHelpfulWarning(item) ? "warning" : ""} ${isRaw360(item) ? "info" : ""}" title="${escapeHtml(
        item.path
      )}">
          ${iconFor(item)} ${escapeHtml(item.filename || filename(item.path))}
          ${isSphericalVideo(item) ? "<small>360°</small>" : ""}
          ${item.source === "inbox" ? "<small>from Inbox</small>" : ""}
          ${item.sync_confidence != null ? `<small>sync ${Number(item.sync_confidence).toFixed(1)} · offset ${formatDuration(item.sync_offset_sec || 0)}</small>` : ""}
          ${isRaw360(item) ? `<small>${escapeHtml(item.info || "360 stitched automatically")}</small>` : ""}
          ${item.kind === "ignored" ? `<small>${escapeHtml(item.note || S.ignored)}</small>` : ""}
          <button class="chip-remove" data-remove-kind="${escapeHtml(item.kind)}" data-remove-path="${escapeHtml(item.path)}" aria-label="Remove ${escapeHtml(
        item.filename || filename(item.path)
      )}">×</button>
        </span>`;
      }
    )
    .join("");
  if (detected.master.length > 1 && !document.querySelector("#inboxMasterSelect")) {
    const selected = selectedMasterPath || detected.master[0].path;
    root.insertAdjacentHTML(
      "afterbegin",
      `<label class="master-select-chip">🎵 ${escapeHtml(S.masterAudio)}
        <select id="masterSelect">
          ${detected.master
            .map((item) => {
              const duration = item.duration ? ` · ${formatDuration(item.duration)}` : "";
              return `<option value="${escapeHtml(item.path)}" ${item.path === selected ? "selected" : ""}>${escapeHtml(
                `${item.filename || filename(item.path)}${duration}`
              )}</option>`;
            })
            .join("")}
        </select>
      </label>`
    );
  }
  if (setAsideVideos.length) {
    root.insertAdjacentHTML(
      "beforeend",
      `<span class="chip info session-filter-chip">
        Showing the ${detected.videos.length} clip${detected.videos.length === 1 ? "" : "s"} recorded around this song
        <small>${setAsideVideos.length} other Inbox clip${setAsideVideos.length === 1 ? "" : "s"} hidden</small>
        <button type="button" id="showAllClips">Show all</button>
      </span>`
    );
  }
  const hasVideo = detected.videos.length > 0;
  const hasMaster = detected.master.length > 0;
  const hasSongs = detected.songs.length > 0;
  renderRaw360Callout();
  const note = document.querySelector("#softRule");
  const button = document.querySelector("#confirmFiles");
  // Backstage is video-led and deliberately has no required master track.
  button.disabled = !hasVideo;
  if (!hasVideo) note.textContent = S.missingVideo;
  else if (!hasMaster) note.textContent = "Choose Backstage to edit with the original camera audio (no master required)";
  else if (!hasSongs) note.textContent = S.noSongsContinuous;
  else note.textContent = S.ready;
}

function renderRaw360Callout() {
  const callout = document.querySelector("#insvCallout");
  if (!callout) return;
  const hasRaw = detected.videos.some(isRaw360) || detected.ignored.some((item) => [".insv", ".insp"].some((suffix) => String(item.path || "").toLowerCase().endsWith(suffix)));
  const hasStudioExport = detected.videos.some((item) => item.projection === "equirect" || item.probe?.projection === "equirect");
  callout.hidden = !(hasRaw && !hasStudioExport);
}

function hasSphericalInput() {
  return detected.videos.some(isSphericalVideo);
}

// --- Inbox filtering by the chosen song -------------------------------------
// The Inbox accumulates every clip the user has ever dropped in, but only one
// song is processed at a time. A clip's wall-clock recording range is derived
// from its file mtime (when writing finished, i.e. the end of the recording)
// and its duration; the chosen master audio gives the session's own range.
// Clips whose range doesn't overlap the session are set aside.
//
// Two deliberate safety rules, because silently hiding a user's footage is far
// worse than showing too much: the filter is only applied when it both keeps
// at least one clip AND actually excludes something, and it is always
// reversible from the banner it puts on screen.
const SESSION_MATCH_TOLERANCE_SEC = 2 * 60 * 60; // generous: clocks and copy times drift

function itemTimeRange(item) {
  const mtime = Number(item?.mtime || 0);
  if (!mtime) return null;
  const duration = Number(item?.duration || item?.probe?.duration || 0) || 0;
  const end = mtime * 1000;
  return { start: end - duration * 1000, end };
}

function clipsMatchingSession(videos, master) {
  const session = itemTimeRange(master);
  if (!session) return null;
  const keep = [];
  const setAside = [];
  for (const video of videos) {
    const range = itemTimeRange(video);
    // A clip we can't place in time is always kept -- never discard on ignorance.
    const overlaps =
      !range ||
      (range.start - SESSION_MATCH_TOLERANCE_SEC * 1000 <= session.end &&
        range.end + SESSION_MATCH_TOLERANCE_SEC * 1000 >= session.start);
    (overlaps ? keep : setAside).push(video);
  }
  if (!keep.length || !setAside.length) return null;
  return { keep, setAside };
}

function applySessionFilter() {
  if (sessionFilterDisabled) return;
  const master = detected.master.find((item) => item.path === selectedMasterPath) || detected.master[0];
  if (!master) return;
  const split = clipsMatchingSession(detected.videos, master);
  if (!split) return;
  setAsideVideos = [...setAsideVideos, ...split.setAside];
  detected.videos = split.keep;
}

function restoreSetAsideVideos() {
  if (!setAsideVideos.length) return;
  sessionFilterDisabled = true;
  detected.videos = [...detected.videos, ...setAsideVideos];
  setAsideVideos = [];
  renderChips();
}

function removeDetectedItem(kind, path) {
  for (const key of ["master", "songs", "videos", "ignored"]) {
    detected[key] = detected[key].filter((item) => !(item.kind === kind && item.path === path));
  }
  if (selectedMasterPath === path) selectedMasterPath = null;
  chooseDefaultMaster();
  renderChips();
}

function selectedInputs() {
  return {
    master: selectedMasterPath || detected.master[0]?.path || "",
    songs: detected.songs[0]?.path || "",
    videos: detected.videos.map((item) => item.path),
  };
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

async function loadInbox() {
  const result = await api("/inbox");
  mergeDetected(result, "inbox");
  renderInboxAnalysisStatus();
  if (result.analysis?.status === "idle") {
    await api("/inbox/analysis/start", { method: "POST" });
    scheduleInboxAnalysisRefresh();
  } else if (result.analysis?.status === "running") {
    scheduleInboxAnalysisRefresh();
  }
}

async function savePersonalLogo(file) {
  if (!file) return;
  const path = file.path || "";
  let result;
  if (path) {
    result = await api("/settings/personal-logo", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
  } else {
    const form = new FormData();
    form.append("file", file, file.name);
    result = await apiForm("/settings/personal-logo", form);
  }
  const status = document.querySelector("#personalLogoStatus");
  if (status) status.textContent = result.personal_logo_path ? `Using ${filename(result.personal_logo_path)}` : "The app logo is used by default";
  showToast(result.personal_logo_path ? "Personal logo saved" : "Using the app logo");
}

function sourceFolderName(path) {
  const item = sourceFolders
    .map((folder) => typeof folder === "string" ? { path: folder, name: filename(folder) } : folder)
    .find((folder) => folder.path === path);
  return item?.name || path;
}

function renderSourceFolders() {
  const root = document.querySelector("#sourceFolderList");
  if (!root) return;
  root.innerHTML = sourceFolders.map((rawFolder) => {
    const folder = typeof rawFolder === "string" ? { path: rawFolder, name: filename(rawFolder), available: true } : rawFolder;
    return `
    <div class="source-folder-row ${folder.available === false ? "unavailable" : ""}">
      <span title="${escapeHtml(folder.path)}">${escapeHtml(folder.name || folder.path)}</span>
      <small>${escapeHtml(folder.message || folder.path || "Drive not mounted")}</small>
      <button type="button" data-rescan-folder="${escapeHtml(folder.path)}">Rescan</button>
      <button type="button" data-remove-folder="${escapeHtml(folder.path)}" aria-label="Remove source folder">×</button>
    </div>`;
  }).join("");
}

function renderMasterAudioFilter() {
  const select = document.querySelector("#masterAudioFilter");
  if (select) select.value = masterAudioExtensions.length === 1 && masterAudioExtensions[0] === ".mp3" ? "mp3" : "all";
}

async function saveSourceFolders(folders) {
  const result = await api("/settings/source-folders", { method: "POST", body: JSON.stringify({ source_folders: folders }) });
  sourceFolders = result.source_folders || [];
  renderSourceFolders();
  await loadInbox();
}

function scheduleInboxAnalysisRefresh() {
  clearTimeout(inboxAnalysisTimer);
  inboxAnalysisTimer = setTimeout(async () => {
    try {
      const result = await api("/inbox");
      mergeDetected(result, "inbox");
      renderInboxAnalysisStatus();
      if (result.analysis?.status === "running") scheduleInboxAnalysisRefresh();
    } catch (_error) {
      // The ordinary Inbox scan remains usable if pre-analysis is unavailable.
    }
  }, 1500);
}

function renderInboxAnalysisStatus() {
  const node = document.querySelector("#inboxAnalysisStatus");
  if (!node) return;
  const selector = document.querySelector("#inboxMasterSelect");
  if (selector) {
    const selected = selectedMasterPath || detected.master[0]?.path || "";
    selector.innerHTML = detected.master.length
      ? detected.master.map((item) => {
          const duration = item.duration ? ` · ${formatDuration(item.duration)}` : "";
          return `<option value="${escapeHtml(item.path)}" ${item.path === selected ? "selected" : ""}>${escapeHtml(
            `${item.filename || filename(item.path)}${duration}`
          )}</option>`;
        }).join("")
      : '<option value="">Scan Inbox to load audio masters…</option>';
  }
  const addButton = document.querySelector("#addMasterVideos");
  const selectedMaster = (inboxAnalysis?.masters || []).find((item) => item.path === selectedMasterPath) || (inboxAnalysis?.masters || [])[0];
  const matches = selectedMaster?.matches || [];
  const usableMatches = matches.filter((match) => !match.master_mismatch && !match.no_audio && match.master_overlap && Number(match.coarse_confidence || 0) >= COARSE_MATCH_THRESHOLD);
  if (addButton) {
    addButton.disabled = !selectedMaster || inboxAnalysis?.status !== "done" || !usableMatches.length;
    addButton.textContent = selectedMaster ? `Add videos for ${selectedMaster.filename || filename(selectedMaster.path)}` : "Add videos for this master";
  }
  const mismatch = matches.filter((match) => match.master_mismatch);
  const notice = document.querySelector("#masterMismatchNotice");
  if (notice && inboxAnalysis?.status === "done" && selectedMaster && mismatch.length) {
    notice.hidden = false;
    notice.innerHTML = `<strong>Master mismatch detected</strong><p>${mismatch.length} video${mismatch.length === 1 ? "" : "s"} do not appear to match <em>${escapeHtml(selectedMaster.filename || filename(selectedMaster.path))}</em>. Check the selected master before waiting for Sync.</p>`;
  } else if (notice) {
    notice.hidden = true;
    notice.textContent = "";
  }
  const status = inboxAnalysis?.status || "idle";
  if (status === "running") {
    node.textContent = `${inboxAnalysis.detail || "Scanning Inbox"} · ${Number(inboxAnalysis.progress || 0)}%`;
  } else if (status === "done") {
    const master = (inboxAnalysis.masters || []).find((item) => item.path === selectedMasterPath) || (inboxAnalysis.masters || [])[0];
    const matches = (master?.matches || []).filter((match) =>
      !match.master_mismatch && !match.no_audio && match.master_overlap &&
      match.coarse_reasonable_peak !== false && Number(match.coarse_confidence || 0) >= COARSE_MATCH_THRESHOLD
    );
    node.textContent = master
      ? `${inboxAnalysis.detail || "Inbox pre-analysis ready"} · ${matches.length ? `${matches.length} matching videos` : "No matching videos found for this mix"}`
      : (inboxAnalysis.detail || "No mixes found — masters are expected as .mp3");
  } else if (status === "failed") {
    node.textContent = `Inbox pre-analysis unavailable: ${inboxAnalysis.detail || "manual selection remains available"}`;
  } else {
    node.textContent = "Inbox pre-analysis has not run yet";
  }
}

function addVideosForSelectedMaster() {
  const master = (inboxAnalysis?.masters || []).find((item) => item.path === selectedMasterPath) || (inboxAnalysis?.masters || [])[0];
  if (!master) return;
  const matches = new Map((master.matches || []).filter((match) =>
    !match.master_mismatch && !match.no_audio && match.master_overlap &&
    match.coarse_reasonable_peak !== false && Number(match.coarse_confidence || 0) >= COARSE_MATCH_THRESHOLD
  ).map((match) => [match.path, match]));
  const existing = new Set(detected.videos.map((item) => item.path));
  const sourceEntries = (inboxAnalysis.files || []).filter((item) => item.kind === "videos" && matches.has(item.path));
  const additions = sourceEntries.filter((item) => !existing.has(item.path)).map((video) => ({ ...video, source: "inbox", sync_master_path: master.path, sync_confidence: matches.get(video.path).confidence, sync_offset_sec: matches.get(video.path).offset_sec }));
  detected.videos = [...detected.videos, ...additions];
  analysisSetAsideVideos = analysisSetAsideVideos.filter((item) => !matches.has(item.path));
  setAsideVideos = setAsideVideos.filter((item) => !matches.has(item.path));
  renderChips();
  showToast(additions.length ? `Added ${additions.length} video${additions.length === 1 ? "" : "s"} for this master` : "Matching videos are already added");
}

function applyInboxAnalysisFilter() {
  // Inbox videos are provisional until the selected master's coarse audio
  // analysis has decided whether they belong to that song. Never leave the
  // whole Inbox visible merely because analysis is still running or because
  // the selected master has no matches.
  const inboxVideos = [...detected.videos.filter((video) => video.source === "inbox"), ...analysisSetAsideVideos];
  analysisSetAsideVideos = [];
  if (!inboxVideos.length) return;
  if (!inboxAnalysis || inboxAnalysis.status !== "done") {
    analysisSetAsideVideos = inboxVideos;
    detected.videos = detected.videos.filter((video) => video.source !== "inbox");
    return;
  }
  const master = (inboxAnalysis.masters || []).find((item) => item.path === selectedMasterPath) || (inboxAnalysis.masters || [])[0];
  if (!master) {
    analysisSetAsideVideos = inboxVideos;
    detected.videos = detected.videos.filter((video) => video.source !== "inbox");
    return;
  }
  const acceptedMatches = (master.matches || []).filter((match) =>
    !match.master_mismatch && !match.no_audio && match.master_overlap &&
    match.coarse_reasonable_peak !== false && Number(match.coarse_confidence || 0) >= COARSE_MATCH_THRESHOLD
  );
  const matchByPath = new Map(acceptedMatches.map((match) => [match.path, match]));
  const keepInbox = [];
  for (const video of inboxVideos) {
    const match = matchByPath.get(video.path);
    if (match) keepInbox.push({ ...video, sync_confidence: match.confidence, sync_offset_sec: match.offset_sec, sync_master_path: master.path });
    else analysisSetAsideVideos.push(video);
  }
  detected.videos = [...detected.videos.filter((video) => video.source !== "inbox"), ...keepInbox];
}

async function loadProjects() {
  const result = await api("/wizard/projects");
  renderProjects(result.projects || []);
}

function renderProjects(projects) {
  const shelf = document.querySelector("#projectShelf");
  const root = document.querySelector("#projectList");
  shelf.hidden = projects.length === 0;
  root.innerHTML = projects
    .map(
      (project) => `
        <div class="project-row">
          <div>
            <strong>${escapeHtml(project.name)}</strong>
            <span>${escapeHtml(formatProjectDate(project.modified_at))} · ${escapeHtml(project.status || "new")} · ${formatBytes(project.size_bytes || 0)}</span>
          </div>
          ${project.has_export ? `<small>${escapeHtml(S.hasExport || "export")}</small>` : ""}
          <button data-open-project="${escapeHtml(project.path)}">${escapeHtml(S.openProject || "Open")}</button>
          <button class="danger" data-delete-project="${escapeHtml(project.path)}" data-has-export="${project.has_export ? "1" : ""}" data-name="${escapeHtml(
        project.name
      )}" data-icon="trash">${escapeHtml(S.deleteProject || "Delete")}</button>
        </div>`
    )
    .join("");
  injectIcons();
}

function formatProjectDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  if (value < 1024 * 1024 * 1024) return `${Math.round(value / (1024 * 1024))} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

async function openProject(path) {
  stopStatusPolling();
  const requestGeneration = statusPollGeneration;
  activeProjectId = null;
  const status = await api("/wizard/projects/open", { method: "POST", body: JSON.stringify({ path }) });
  if (requestGeneration !== statusPollGeneration) return;
  activeProjectId = projectIdFromStatus(status) || path;
  await resumeInputsFromProject().catch(() => {});
  renderWizardStatus(status);
  if (status.status === "running") {
    setStep(3);
    ensureStatusPolling();
  } else if (status.status === "waiting_review") {
    renderWizardStatus(status);
  } else if (status.status === "waiting_paper_edit") {
    setStep(4);
    document.querySelector("#progressBox")?.setAttribute("hidden", "");
    document.querySelector("#paperEditBox")?.removeAttribute("hidden");
    openPaperEdit().catch((error) => showToast(error.message, true));
  } else if (status.status === "done") {
    setStep(5);
    openCaptions().catch((error) => showToast(error.message, true));
  } else if (status.status === "failed") {
    setStep(6);
  } else if (status.status === "waiting_choice") {
    setStep(2);
    loadSavedReelOverlays().catch(() => {});
  } else {
    setStep(1);
  }
}

async function newProject() {
  if (latestStatus?.status === "running" && !confirm("A job is still running for the current project. Start a new project view anyway?")) return;
  stopStatusPolling();
  activeProjectId = null;
  await api("/wizard/projects/new", { method: "POST", body: JSON.stringify({}) });
  latestStatus = null;
  latestResult = null;
  resetProgressTiming();
  selectedPlatform = null;
  lastPipelineStage = null;
  try { sessionStorage.removeItem("zucker.selectedPlatform"); } catch (_error) { /* private mode */ }
  selectedSong = null;
  currentSongs = [];
  captionCues = [];
  captionMarkIndex = 0;
  captionPendingStart = null;
  captionActiveIndex = null;
  reelTextOverlays = [];
  reelImageOverlays = [];
  reelVideoOverlays = [];
  projectLogo = { choice: "none", path: "", url: "", name: "", defaultPath: "", defaultUrl: "" };
  selectedComposeOverlay = null;
  copiedComposeOverlay = null;
  copiedCaption = null;
  document.querySelector("#captionText")?.replaceChildren();
  if (document.querySelector("#captionText")) document.querySelector("#captionText").value = "";
  document.querySelector("#captionLogoNone")?.click();
  clearDetected();
  resetSphericalSetupToGlobal();
  document.querySelector("#videoName").value = todayName();
  document.querySelector("#errorBox").hidden = true;
  document.querySelector("#resultBox").hidden = true;
  document.querySelector("#progressTitle").textContent = "Creating your video";
  document.querySelector("#startWizard").disabled = true;
  document.querySelectorAll(".platform-card").forEach((card) => card.classList.remove("selected"));
  await loadProjects().catch(() => {});
  setStep(1);
}

async function deleteProject(path, name, hasExport) {
  if (!confirm(`Delete "${name || "this project"}"? This removes the project folder. Global cache stays untouched.`)) return;
  const keepExports = hasExport && confirm("This project has exports. Move them to ~/ZuckerVideos/Exports before deleting?");
  await api("/wizard/projects/delete", { method: "POST", body: JSON.stringify({ path, keep_exports: Boolean(keepExports) }) });
  showToast(S.projectDeleted || "Project deleted");
  await loadProjects();
}

function supportedDropFile(file) {
  const name = file.name || "";
  return Boolean(name) && !name.startsWith(".") && name !== ".DS_Store";
}

function readAllDirectoryEntries(reader) {
  return new Promise((resolve, reject) => {
    const entries = [];
    function readBatch() {
      reader.readEntries((batch) => {
        if (!batch.length) {
          resolve(entries);
          return;
        }
        entries.push(...batch);
        readBatch();
      }, reject);
    }
    readBatch();
  });
}

function entryFile(entry) {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function filesFromEntry(entry) {
  if (entry.isFile) {
    const file = await entryFile(entry);
    return supportedDropFile(file) ? [file] : [];
  }
  if (!entry.isDirectory) return [];
  const entries = await readAllDirectoryEntries(entry.createReader());
  const nested = await Promise.all(entries.map(filesFromEntry));
  return nested.flat();
}

async function browserDropFiles(dataTransfer) {
  const items = [...(dataTransfer.items || [])];
  const entries = items.map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
  if (entries.length) {
    const nested = await Promise.all(entries.map(filesFromEntry));
    return nested.flat();
  }
  return [...dataTransfer.files].filter(supportedDropFile);
}

async function handleDrop(event) {
  event.preventDefault();
  document.querySelector("#dropZone").classList.remove("dragging");
  const paths = [...event.dataTransfer.files].map((file) => file.path || file.webkitRelativePath).filter(Boolean);
  if (paths.length) {
    mergeDetected(await api("/inputs/classify-paths", { method: "POST", body: JSON.stringify({ paths }) }));
    return;
  }
  const files = await browserDropFiles(event.dataTransfer);
  if (!files.length) {
    showToast(S.noCompatibleFiles, true);
    return;
  }
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  mergeDetected(await apiForm("/wizard/upload", form));
}

async function prepareStep2() {
  const inputs = selectedInputs();
  progressFloor = 0;
  progressStartedAt = Date.now();
  progressSamples = [];
  if ((!inputs.master && selectedPlatform !== "backstage") || !inputs.videos.length) return;
  setStep(2);
  if (inputs.master) setupTrimControls(inputs.master);
  applyEditTypeMode();
  await loadSavedReelOverlays();
  if (inputs.songs) {
    const result = await api("/wizard/songs", { method: "POST", body: JSON.stringify({ songs: inputs.songs }) });
    renderSongOptions(result.songs || []);
  } else {
    renderSongOptions([]);
  }
}

async function loadSavedReelOverlays() {
  // New projects start empty. Reuse is an explicit action in Overlay & Captions.
  loadFlyerLibrary().catch(() => {});
}

async function loadFlyerLibrary() {
  const root = document.querySelector("#reelFlyerLibrary");
  if (!root) return;
  const result = await api("/wizard/flyers");
  root.innerHTML = (result.items || []).map((item) => `<article class="flyer-library-item"><button type="button" class="flyer-library-thumb" data-flyer-preview="${escapeHtml(item.url)}" aria-label="Preview ${escapeHtml(item.name)}"><img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}" loading="lazy" /></button><button type="button" class="flyer-library-delete" data-delete-flyer="${escapeHtml(item.name)}" aria-label="Delete ${escapeHtml(item.name)}">×</button><span class="flyer-library-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span><button type="button" class="flyer-library-use" data-add-flyer="${escapeHtml(item.path)}" data-flyer-url="${escapeHtml(item.url)}">Use</button></article>`).join("");
}

async function deleteFlyerFromLibrary(name) {
  if (!name || !confirm(`Delete ${name} from the flyer library?`)) return;
  const result = await api(`/wizard/flyers/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (result.retained) showToast("Flyer removed from the library; it is still used by a project.");
  else showToast("Flyer deleted from the library.");
  loadFlyerLibrary().catch(() => {});
}

function selectedLogoSource() {
  return document.querySelector("input[name='captionLogoChoice']:checked")?.value || "none";
}

function renderProjectLogo() {
  const choice = selectedLogoSource();
  const preview = document.querySelector("#composeLogoPreview");
  const image = document.querySelector("#composeLogoThumbnail");
  const name = document.querySelector("#composeLogoName");
  const status = document.querySelector("#composeLogoStatus");
  const active = choice === "custom" ? projectLogo : choice === "default" ? { url: projectLogo.defaultUrl, name: "Default brand logo" } : projectLogo.url ? projectLogo : null;
  if (preview) preview.hidden = !active?.url;
  if (image && active?.url) image.src = `${active.url}?t=${Date.now()}`;
  if (name) name.textContent = active?.name || "";
  if (status) status.textContent = choice === "none" ? "No logo will be added to this project." : active?.url ? "Selected for this project." : "Choose or upload a logo.";
}

async function loadProjectLogo() {
  const result = await api("/wizard/logo");
  projectLogo = {
    choice: result.mode || "none",
    path: result.custom?.path || "",
    url: result.custom?.url || "",
    name: result.custom?.path ? filename(result.custom.path) : "",
    defaultPath: result.default?.path || "",
    defaultUrl: result.default?.url || "",
    overlay: result.overlay || { x: .5, y: .08, width: .22 },
  };
  const custom = document.querySelector("#captionLogoCustom");
  const defaultChoice = document.querySelector("#captionLogoDefault");
  const none = document.querySelector("#captionLogoNone");
  if (custom) custom.disabled = !projectLogo.url;
  if (defaultChoice) defaultChoice.disabled = !projectLogo.defaultUrl;
  if (none) none.checked = projectLogo.choice === "none";
  if (custom) custom.checked = projectLogo.choice === "custom" && Boolean(projectLogo.url);
  if (defaultChoice) defaultChoice.checked = projectLogo.choice === "default" && Boolean(projectLogo.defaultUrl);
  renderProjectLogo();
}

async function uploadProjectLogo(file) {
  if (!file) return;
  const form = new FormData();
  if (file.path) form.append("path", file.path);
  else form.append("file", file, file.name);
  const result = await apiForm("/wizard/logo", form);
  projectLogo.path = result.path || "";
  projectLogo.url = result.url || "/api/v1/wizard/logo/project";
  projectLogo.name = result.name || filename(projectLogo.path);
  const custom = document.querySelector("#captionLogoCustom");
  if (custom) { custom.disabled = false; custom.checked = true; }
  renderProjectLogo();
  renderComposeOverlayLayer();
}

async function removeProjectLogo() {
  await api("/wizard/logo", { method: "DELETE" });
  projectLogo.path = "";
  projectLogo.url = "";
  const none = document.querySelector("#captionLogoNone");
  if (none) none.checked = true;
  const custom = document.querySelector("#captionLogoCustom");
  if (custom) custom.disabled = true;
  renderProjectLogo();
  renderComposeOverlayLayer();
}

function addFlyerReference(path, url) {
  reelImageOverlays.push({ path, preview_url: url, x: 0.5, y: 0.5, width: 0.35, opacity: 1, animation: "fade", start_sec: 0, duration_sec: 3 });
  renderReelOptions();
}

function renderShotReview(items) {
  const previous = new Map(shotReviewItems.map((item) => [Number(item.index), item]));
  shotReviewItems = (items || []).map((item) => {
    const old = previous.get(Number(item.index));
    const merged = { ...old, keep: true, ...item };
    // Replace returns immediately, while its new JPEG is still rendering.
    // Keep the old frame visible until the new URL is actually available.
    if (!item.thumbnail && old?.thumbnail && item.thumbnail_status !== "failed") {
      merged.thumbnail = old.thumbnail;
      merged.thumbnail_status = "generating";
    }
    return merged;
  });
  const root = document.querySelector("#reviewGrid");
  if (!root) return;
  root.innerHTML = shotReviewItems.map((item) => {
    const alt = escapeHtml(item.landmark || item.source || `Shot ${item.index + 1}`);
    const thumb = item.thumbnail
      ? `<img class="review-thumb" src="${escapeHtml(item.thumbnail)}" alt="${alt}" />`
      : `<span class="review-thumb-placeholder ${item.thumbnail_status === "failed" ? "failed" : "pending"}">${item.thumbnail_status === "failed" ? "Render failed" : "Generating…"}</span>`;
    const error = item.thumbnail_status === "failed" && item.thumbnail_error
      ? `<em class="review-thumb-error">${escapeHtml(item.thumbnail_error)}</em>` : "";
    return `<article class="review-card ${item.keep ? "keep" : "reject"}" data-review-index="${item.index}">
    <button class="review-thumb-button" data-review-thumb="${item.index}">${thumb}</button>
    <label class="review-keep"><input type="checkbox" data-review-keep="${item.index}" ${item.keep ? "checked" : ""}/> Keep</label>
    <strong>#${item.index + 1} · ${escapeHtml(item.source)}</strong>
    <span>${Number(item.duration_sec).toFixed(1)}s${item.landmark ? ` · ${escapeHtml(item.landmark)} frame` : ""}</span>
    ${error}
    ${item.no_alternative ? '<em>No alternative coverage available</em>' : ""}
  </article>`;
  }).join("");
}

function refreshShotReviewAfterReplace(attempt = 0) {
  // Rendering time depends heavily on the source (especially 360 footage).
  // Poll until the worker reports ready or failed; there is no arbitrary
  // 10-second cutoff that can turn an in-flight render into a grey card.
  const delay = Math.min(3000, 500 + attempt * 250);
  setTimeout(() => {
    api("/wizard/review?render=0")
      .then((result) => {
        const items = result.items || [];
        renderShotReview(items);
        const pending = items.some((item) => ["missing", "rendering", "generating"].includes(item.thumbnail_status) && !item.thumbnail_error);
        if (pending) refreshShotReviewAfterReplace(attempt + 1);
      })
      .catch((error) => {
        // A transient HTTP failure is still a pending render. Keep the old
        // thumbnail in place and retry with the same adaptive backoff.
        logFrontendError(`review thumbnail poll failed: ${error.message}`, error.stack || "");
        refreshShotReviewAfterReplace(attempt + 1);
      });
  }, delay);
}

async function openShotReview() {
  const result = await api("/wizard/review");
  renderShotReview(result.items || []);
  // The server has rendered the JPEGs by this point, but the browser can
  // still be decoding them when the review transition is shown. Wait for the
  // actual <img> elements before revealing Frames.
  const images = Array.from(document.querySelectorAll("#reviewGrid img.review-thumb"));
  await Promise.all(images.map((image) => {
    if (image.complete) return Promise.resolve();
    return new Promise((resolve) => {
      image.addEventListener("load", resolve, { once: true });
      image.addEventListener("error", resolve, { once: true });
    });
  }));
  return result;
}

function renderPaperEdit(paper) {
  renderStoryboard(paper.storyboard);
  paperEditCuts = paper.cuts || [];
  const list = document.querySelector("#paperEditList");
  const summary = document.querySelector("#paperEditSummary");
  if (summary) summary.textContent = `${paperEditCuts.length} cortes · texto editable y criterio keep/drop/closing por corte.`;
  if (!list) return;
  list.innerHTML = paperEditCuts.map((cut) => `
    <div class="paper-edit-row">
      ${cut.thumbnail ? `<img class="paper-edit-thumb" src="${escapeHtml(cut.thumbnail)}" alt="Miniatura de ${escapeHtml(cut.source)}" />` : `<span class="paper-edit-thumb review-thumb-placeholder">—</span>`}
      <span class="paper-edit-order">${cut.order}</span>
      <span class="paper-edit-main"><strong>${escapeHtml(cut.source)} · ${escapeHtml(cut.section)}</strong><span>${Number(cut.in_sec).toFixed(1)}–${Number(cut.out_sec).toFixed(1)} s · ${Number(cut.duration_sec).toFixed(1)} s · ${escapeHtml(cut.language || "—")}</span><span class="paper-edit-original">${cut.text_original ? escapeHtml(cut.text_original) : "no dialogue"}</span>${cut.english_text ? `<span class="paper-edit-english">${escapeHtml(cut.english_text)}</span>` : ""}<em>score ${Number(cut.narrative_score ?? cut.interest_score ?? 0).toFixed(2)} · ${escapeHtml(cut.selection_reason || "")}</em>${cut.narrative_scores && Object.keys(cut.narrative_scores).length ? `<small>LLM: ${escapeHtml(JSON.stringify(cut.narrative_scores))}</small>` : ""}<input class="paper-edit-subtitle" data-paper-subtitle="${escapeHtml(cut.id)}" value="${escapeHtml(cut.subtitle_text || cut.english_text || "")}" aria-label="English subtitle for ${escapeHtml(cut.source)}" /></span><select data-paper-mark="${escapeHtml(cut.id)}" aria-label="Mark ${escapeHtml(cut.source)}"><option value="keep" ${cut.mark === "keep" ? "selected" : ""}>keep</option><option value="drop" ${cut.mark === "drop" ? "selected" : ""}>drop</option><option value="closing" ${cut.mark === "closing" ? "selected" : ""}>closing</option></select>
    </div>`).join("");
}

function renderStoryboard(payload) {
  const box = document.querySelector("#storyboardBox");
  const plan = document.querySelector("#storyboardPlan");
  const music = document.querySelector("#storyboardMusic");
  if (!box || !plan || !music) return;
  const storyboard = payload?.storyboard || {};
  if (!payload || !["ready", "ready_cached"].includes(payload.status) || !storyboard.title) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const title = document.querySelector("#storyboardTitle");
  const logline = document.querySelector("#storyboardLogline");
  if (title) title.textContent = storyboard.title;
  if (logline) logline.textContent = storyboard.logline || "";
  const labels = { opening: "Apertura", development: "Desarrollo", closing: "Cierre" };
  plan.innerHTML = ["opening", "development", "closing"].map((phase) => {
    const rows = (storyboard[phase] || []).map((scene) => `<div class="storyboard-scene"><strong>${escapeHtml(scene.sequence_id || "")} · ${escapeHtml(scene.role || "")}</strong><span>${escapeHtml(scene.reason || "")}</span><em>${Number(scene.suggested_duration_sec || 0).toFixed(1)} s sugeridos</em></div>`).join("");
    return rows ? `<section class="storyboard-phase"><h4>${labels[phase]}</h4>${rows}</section>` : "";
  }).join("");
  music.innerHTML = (storyboard.music_plan || []).map((item) => `<p><strong>${escapeHtml(item.phase || "")}</strong>: ${escapeHtml(item.policy || "")} <em>${escapeHtml(item.transition || "")}</em></p>`).join("");
}

async function openPaperEdit() {
  const paper = await api("/wizard/paper-edit");
  renderPaperEdit(paper);
  return paper;
}

function applyEditTypeMode() {
  const passthrough360 = selectedPlatform === "360";
  const cameraMix = document.querySelector("#cameraMix");
  const songPicker = document.querySelector("#songPicker");
  const reelOptions = document.querySelector("#reelOptions");
  const backstageOptions = document.querySelector("#backstageOptions");
  const trimBox = document.querySelector(".trim-box");
  if (cameraMix) cameraMix.hidden = passthrough360;
  if (songPicker && passthrough360) songPicker.hidden = true;
  if (reelOptions) {
    reelOptions.hidden = selectedPlatform !== "reel";
    if (selectedPlatform !== "reel") reelOptions.open = false;
    if (selectedPlatform === "reel") renderReelOptions();
  }
  if (backstageOptions) {
    backstageOptions.hidden = selectedPlatform !== "backstage";
    backstageOptions.open = selectedPlatform === "backstage";
  }
  if (trimBox) trimBox.hidden = selectedPlatform === "backstage";
}

function renderReelOptions() {
  const duration = Number(document.querySelector("#reelDuration")?.value || 30);
  const value = document.querySelector("#reelDurationValue");
  if (value) value.textContent = `${duration}s`;
  const ratioWrap = document.querySelector("#reelMixVerticalRatioWrap");
  if (ratioWrap) ratioWrap.hidden = document.querySelector("#reelAspect")?.value !== "mix_vertical_horizontal";
  const imageRoot = document.querySelector("#reelImageLines");
  const overlayRange = (label, icon, field, value, min, max, step) => `<label class="compact-control" title="${label}" aria-label="${label}"><span class="control-icon" aria-hidden="true">${icon}</span><input data-reel-image-field="${field}" type="range" min="${min}" max="${max}" step="${step}" value="${value}" aria-label="${label}" /><output>${Math.round(Number(value) * 100)}%</output></label>`;
  const imageColor = (label, field, value) => `<label class="compact-control" title="${label}" aria-label="${label}"><span class="control-icon" aria-hidden="true">●</span><input data-reel-image-field="${field}" type="color" value="${value || "#ffffff"}" aria-label="${label}" /></label>`;
  const imageRange = (label, icon, field, value, min, max, step, percent = false) => `<label class="compact-control" title="${label}" aria-label="${label}"><span class="control-icon" aria-hidden="true">${icon}</span><input data-reel-image-field="${field}" type="range" min="${min}" max="${max}" step="${step}" value="${value}" aria-label="${label}" /><output>${percent ? `${Math.round(Number(value) * 100)}%` : value}</output></label>`;
  if (imageRoot) imageRoot.innerHTML = reelImageOverlays.map((item, index) => `<div class="reel-text-line reel-image-properties" data-reel-image-index="${index}"><span>Flyer ${index + 1}</span>${overlayRange("Width", "↔", "width", item.width ?? .35, .05, 1, .01)}${overlayRange("Opacity", "◐", "opacity", item.opacity ?? 1, .05, 1, .05)}${imageColor("Tint color", "tint_color", item.tint_color || "#ffffff")}${imageRange("Tint strength", "T", "tint_opacity", item.tint_opacity ?? 0, 0, 1, .05, true)}${imageRange("Shadow distance", "↘", "shadow_distance", item.shadow_distance ?? 0, 0, 40, 1)}${imageRange("Shadow blur", "◌", "shadow_blur", item.shadow_blur ?? 0, 0, 40, 1)}${imageRange("Shadow opacity", "S", "shadow_opacity", item.shadow_opacity ?? 0, 0, 1, .05, true)}${imageColor("Shadow color", "shadow_color", item.shadow_color || "#000000")}${imageColor("Glow color", "glow_color", item.glow_color || "#ffffff")}${imageRange("Glow blur", "✦", "glow_blur", item.glow_blur ?? 0, 0, 40, 1)}${imageRange("Glow layers", "✧", "glow_layers", item.glow_layers ?? 0, 0, 8, 1)}<button class="overlay-icon-button" type="button" data-duplicate-reel-image="${index}" title="Duplicate flyer" aria-label="Duplicate flyer">⧉</button><button class="overlay-icon-button" type="button" data-remove-reel-image="${index}" title="Remove flyer" aria-label="Remove flyer">×</button></div>`).join("");
  const videoRoot = document.querySelector("#reelVideoLines");
  const videoRange = (label, icon, field, value, min, max, step) => `<label class="compact-control" title="${label}" aria-label="${label}"><span class="control-icon" aria-hidden="true">${icon}</span><input data-reel-video-field="${field}" type="range" min="${min}" max="${max}" step="${step}" value="${value}" aria-label="${label}" /><output>${Math.round(Number(value) * 100)}%</output></label>`;
  if (videoRoot) videoRoot.innerHTML = reelVideoOverlays.map((item, index) => `<div class="reel-text-line" data-reel-video-index="${index}"><span>Video ${index + 1}</span>${videoRange("Width", "↔", "width", item.width ?? .35, .05, 1, .01)}${videoRange("Opacity", "◐", "opacity", item.opacity ?? 1, .05, 1, .05)}<button class="overlay-icon-button" type="button" data-remove-reel-video="${index}" title="Remove video overlay" aria-label="Remove video overlay">×</button></div>`).join("");
  renderComposeTimeline();
  renderComposeOverlayLayer();
}

function renderReelTimeline() {
  const root = document.querySelector("#reelTimeline");
  if (!root) return;
  const duration = Number(document.querySelector("#reelDuration")?.value || 30);
  const items = [...reelTextOverlays.map((item, index) => ({ ...item, _index: index, _kind: "text" })), ...reelImageOverlays.map((item, index) => ({ ...item, _index: index, _kind: "image" }))];
  root.innerHTML = items.map((item) => `<div class="reel-timeline-block ${item._kind}" style="left:${Math.max(0, Number(item.start_sec) / duration * 100)}%;width:${Math.max(1, Number(item.duration_sec) / duration * 100)}%">${item._kind === "text" ? escapeHtml(item.text || "Text") : "Image"}</div>`).join("");
}

function drawReelPreview() {
  const canvas = document.querySelector("#reelPreview");
  if (!canvas) return;
  const horizontal = document.querySelector("#reelAspect")?.value === "16:9";
  canvas.width = horizontal ? 640 : 360;
  canvas.height = horizontal ? 360 : 640;
  const ctx = canvas.getContext("2d");
  const gradient = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
  gradient.addColorStop(0, "#182b38"); gradient.addColorStop(1, "#713f5a");
  ctx.fillStyle = gradient; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "rgba(255,255,255,.72)"; ctx.font = "16px sans-serif"; ctx.textAlign = "center";
  ctx.fillText("Reel preview", canvas.width / 2, canvas.height / 2);
  const safeX = canvas.width * 0.12, safeY = canvas.height * 0.10, safeW = canvas.width * 0.76, safeH = canvas.height * 0.72;
  ctx.strokeStyle = "rgba(255,255,255,.42)"; ctx.setLineDash([6, 5]); ctx.strokeRect(safeX, safeY, safeW, safeH); ctx.setLineDash([]);
  ctx.fillStyle = "rgba(255,255,255,.65)"; ctx.font = "11px sans-serif"; ctx.textAlign = "left"; ctx.fillText("Instagram safe zone", safeX + 6, safeY + 14);
  for (const item of reelTextOverlays) {
    if (reelPlayhead < Number(item.start_sec) || reelPlayhead > Number(item.start_sec) + Number(item.duration_sec)) continue;
    const [vertical, horizontalAlign] = item.position.split("-");
    ctx.fillStyle = item.color; ctx.globalAlpha = Number(item.opacity ?? 1); ctx.font = `${item.font_weight === "normal" ? "400" : "700"} ${Math.max(12, Number(item.size) * canvas.width / 1080)}px ${item.font === "arial" ? "Arial" : "Verdana"}`;
    ctx.textAlign = horizontalAlign === "left" ? "left" : horizontalAlign === "right" ? "right" : "center";
    const x = item.x != null ? Number(item.x) * canvas.width : horizontalAlign === "left" ? 18 : horizontalAlign === "right" ? canvas.width - 18 : canvas.width / 2;
    const y = item.y != null ? Number(item.y) * canvas.height : vertical === "top" ? 48 : vertical === "bottom" ? canvas.height - 48 : canvas.height / 2;
    if (Number(item.background_opacity || 0) > 0) { const metrics = ctx.measureText(item.text); ctx.fillStyle = item.background_color || "#000"; ctx.globalAlpha = Number(item.background_opacity) * Number(item.opacity ?? 1); ctx.fillRect(x - metrics.width / 2 - 8, y - Number(item.size) * canvas.width / 1080 - 8, metrics.width + 16, Number(item.size) * canvas.width / 1080 + 16); ctx.fillStyle = item.color; ctx.globalAlpha = Number(item.opacity ?? 1); }
    ctx.shadowColor = item.shadow_color || "rgba(0,0,0,.75)"; ctx.shadowBlur = Number(item.shadow_blur ?? 4); ctx.shadowOffsetX = Number(item.shadow_offset_x ?? 3); ctx.shadowOffsetY = Number(item.shadow_offset_y ?? 3);
    ctx.fillText(item.text, x, y);
    ctx.shadowBlur = 0; ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 0; ctx.globalAlpha = 1;
  }
  for (const item of reelImageOverlays) {
    if (reelPlayhead < Number(item.start_sec) || reelPlayhead > Number(item.start_sec) + Number(item.duration_sec)) continue;
    const previewPath = item.preview_url || item.path;
    let image = reelPreviewImages.get(previewPath);
    if (!image) { image = new Image(); image.onload = () => drawReelPreview(); image.src = previewPath; reelPreviewImages.set(previewPath, image); }
    if (!image.complete || !image.naturalWidth) continue;
    const iw = canvas.width * Number(item.width || .35); const ratio = image.naturalHeight / Math.max(1, image.naturalWidth); const ih = iw * ratio;
    ctx.globalAlpha = Number(item.opacity ?? 1); ctx.drawImage(image, Number(item.x ?? .5) * canvas.width - iw / 2, Number(item.y ?? .5) * canvas.height - ih / 2, iw, ih); ctx.globalAlpha = 1;
    if (reelDrag?.item === item) { ctx.strokeStyle = "#fff"; ctx.setLineDash([4, 3]); ctx.strokeRect(Number(item.x ?? .5) * canvas.width - iw / 2, Number(item.y ?? .5) * canvas.height - ih / 2, iw, ih); ctx.setLineDash([]); ctx.fillStyle = "#fff"; ctx.fillRect(Number(item.x ?? .5) * canvas.width + iw / 2 - 8, Number(item.y ?? .5) * canvas.height + ih / 2 - 8, 12, 12); }
  }
}

function reelOptionsFromForm() {
  return { duration: Number(document.querySelector("#reelDuration")?.value || 30), aspect: document.querySelector("#reelAspect")?.value || "9:16", mixVerticalRatio: document.querySelector("#reelMixVerticalRatio")?.value || "auto", texts: reelTextOverlays, images: reelImageOverlays, videos: reelVideoOverlays };
}

function backstageMessagesFromForm() {
  return [1, 2, 3, 4].map((index) => String(document.querySelector(`#backstageMessage${index}`)?.value || "").trim()).filter(Boolean);
}

function cameraRoleWeightsFromForm() {
  const values = {};
  document.querySelectorAll("[data-camera-role]").forEach((group) => {
    const text = String(group.querySelector('[data-field="weight"]')?.value || "").trim();
    const value = text ? parseLocaleNumber(text) : null;
    if (value != null) values[group.dataset.cameraRole] = Math.max(0, value);
  });
  return values;
}

function applyCameraRoleWeights(values = {}) {
  const normalized = normalizeCameraRoleWeights(values);
  document.querySelectorAll("[data-camera-role]").forEach((group) => {
    const input = group.querySelector('[data-field="weight"]');
    const value = normalized[group.dataset.cameraRole];
    if (input && value != null) input.value = formatCanonicalNumber(value);
  });
}

function normalizeCameraRoleWeights(raw = {}) {
  const defaults = { "360": 50, handheld: 30, fixed_rear: 20 };
  const result = { ...defaults };
  for (const key of Object.keys(defaults)) {
    const value = parseLocaleNumber(raw[key]);
    if (value != null) result[key] = Math.max(0, value);
  }
  return result;
}

function fixedRearMotionFromForm() {
  return document.querySelector("#fixedRearMotion")?.checked !== false;
}

function applyFixedRearMotion(enabled) {
  const input = document.querySelector("#fixedRearMotion");
  if (input) input.checked = enabled !== false;
}

function verticalFovFromHorizontal(horizontalFov, aspect) {
  const horizontal = clamp(Number(horizontalFov) || 100, 1, 179);
  return (2 * Math.atan(Math.tan((horizontal * Math.PI) / 360) / Math.max(0.1, aspect)) * 180) / Math.PI;
}

async function setupResult360Viewer(videoUrl) {
  const canvas = document.querySelector("#result360Canvas");
  const video = document.querySelector("#result360Video");
  teardownResult360Viewer();
  video.src = videoUrl;
  video.playsInline = true;
  const gl = canvas.getContext("webgl2");
  if (!gl) throw new Error("WebGL2 is not available in this webview");
  result360.gl = gl;
  const THREE = await import("/vendor/three.module.min.js");
  result360.three = THREE;
  result360.renderer = new THREE.WebGLRenderer({ canvas, context: gl, antialias: true });
  result360.scene = new THREE.Scene();
  result360.camera = new THREE.PerspectiveCamera(50, 16 / 9, 0.1, 1100);
  const geometry = new THREE.SphereGeometry(500, 96, 64);
  geometry.scale(-1, 1, 1);
  result360.texture = new THREE.VideoTexture(video);
  result360.texture.colorSpace = THREE.SRGBColorSpace;
  result360.sphere = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ map: result360.texture }));
  result360.scene.add(result360.sphere);
  result360.yaw = 0;
  result360.pitch = 0;
  result360.fov = 100;
  wireResult360Events(canvas);
  resizeResult360();
  document.querySelector("#result360Play").textContent = "Play";
  const render = () => {
    result360.animation = requestAnimationFrame(render);
    updateResult360Scrub();
    result360.renderer.render(result360.scene, result360.camera);
  };
  render();
  result360.ready = true;
}

function teardownResult360Viewer() {
  if (result360.animation) cancelAnimationFrame(result360.animation);
  result360.animation = null;
  result360.texture?.dispose?.();
  result360.renderer?.dispose?.();
  const video = document.querySelector("#result360Video");
  if (video) video.pause();
  result360.ready = false;
}

function showResultPlaybackWarning(message) {
  const el = document.querySelector("#resultPlaybackWarning");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
}

function hideResultPlaybackWarning() {
  const el = document.querySelector("#resultPlaybackWarning");
  if (el) el.hidden = true;
}

// Some codec/profile mismatches (e.g. a video tagged in a way this webview's
// decoder rejects) don't fire a proper `error` event -- the container and
// audio track are perfectly valid, so the element loads and plays sound
// normally, it just never produces a video frame. That reads to a user as
// "black screen with audio". Since no error event catches this, watch the
// decoded frame size directly: once playback is underway, a real video
// track reports a non-zero videoWidth/videoHeight within a couple of
// seconds. If it never does, the file's picture genuinely can't be
// decoded here -- show a fallback message rather than leave a silent black
// rectangle with no explanation.
function watchForUndecodableVideo(video) {
  if (!video) return;
  const exportedFileIsFine = "This export finished normally -- its picture just can't be decoded by this preview. " +
    "Open the exported file directly (e.g. in QuickTime or VLC) to view it.";
  const onError = () => showResultPlaybackWarning(exportedFileIsFine);
  video.addEventListener("error", onError, { once: true });
  const checkFrameSize = () => {
    if (video.videoWidth > 0 && video.videoHeight > 0) return;
    if (video.error) return; // the "error" listener above already handled it
    showResultPlaybackWarning(exportedFileIsFine);
  };
  video.addEventListener(
    "playing",
    () => {
      setTimeout(checkFrameSize, 2000);
    },
    { once: true }
  );
}

function wireResult360Events(canvas) {
  if (canvas.dataset.wired) return;
  canvas.dataset.wired = "1";
  canvas.addEventListener("pointerdown", (event) => {
    result360.dragging = true;
    result360.dragX = event.clientX;
    result360.dragY = event.clientY;
    canvas.classList.add("dragging");
    canvas.setPointerCapture?.(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!result360.dragging) return;
    const dx = event.clientX - result360.dragX;
    const dy = event.clientY - result360.dragY;
    result360.dragX = event.clientX;
    result360.dragY = event.clientY;
    const sensitivity = dragSensitivityScale(result360.fov);
    result360.yaw = normalizeYaw(result360.yaw - dx * YAW_DEG_PER_PX * sensitivity) ?? 0;
    result360.pitch = clamp(result360.pitch + dy * PITCH_DEG_PER_PX * sensitivity, -85, 85);
    updateResult360Camera();
  });
  const stopDrag = (event) => {
    result360.dragging = false;
    canvas.classList.remove("dragging");
    if (event?.pointerId != null) canvas.releasePointerCapture?.(event.pointerId);
  };
  canvas.addEventListener("pointerup", stopDrag);
  canvas.addEventListener("pointercancel", stopDrag);
  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const delta = event.deltaY > 0 ? 3 : -3;
      result360.fov = clamp((result360.fov || 100) + delta, MIN_SHOT_FOV, MAX_SHOT_FOV);
      updateResult360Camera();
    },
    { passive: false }
  );
  window.addEventListener("resize", resizeResult360);
}

function resizeResult360() {
  if (!result360.renderer || !result360.camera) return;
  const canvas = document.querySelector("#result360Canvas");
  const width = Math.max(320, canvas.clientWidth || 960);
  const height = Math.max(180, canvas.clientHeight || Math.round((width * 9) / 16));
  result360.renderer.setSize(width, height, false);
  result360.camera.aspect = width / height;
  updateResult360Camera();
}

function updateResult360Camera() {
  if (!result360.camera || !result360.three) return;
  const THREE = result360.three;
  const aspect = Math.max(0.1, result360.camera.aspect || 16 / 9);
  result360.camera.fov = verticalFovFromHorizontal(result360.fov, aspect);
  result360.camera.updateProjectionMatrix();
  const yaw = THREE.MathUtils.degToRad(signedYawDelta(result360.yaw, 0));
  const pitch = THREE.MathUtils.degToRad(clamp(result360.pitch, -85, 85));
  const target = new THREE.Vector3(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), -Math.cos(yaw) * Math.cos(pitch));
  result360.camera.lookAt(target);
  document.querySelector("#result360Hud").textContent = `Yaw ${formatCanonicalNumber(result360.yaw)}° · Pitch ${formatCanonicalNumber(result360.pitch)}°`;
}

function toggleResult360Play() {
  const video = document.querySelector("#result360Video");
  if (!video) return;
  if (video.paused) {
    video.play().catch(() => {});
    document.querySelector("#result360Play").textContent = "Pause";
  } else {
    video.pause();
    document.querySelector("#result360Play").textContent = "Play";
  }
}

function seekResult360(time) {
  const video = document.querySelector("#result360Video");
  if (!video || !Number.isFinite(video.duration)) return;
  video.currentTime = clamp(Number(time) || 0, 0, video.duration);
}

function updateResult360Scrub() {
  const video = document.querySelector("#result360Video");
  const scrub = document.querySelector("#result360Scrub");
  if (!video || !scrub || !Number.isFinite(video.duration)) return;
  scrub.max = String(Math.max(0.01, video.duration));
  if (document.activeElement !== scrub) scrub.value = String(video.currentTime || 0);
  document.querySelector("#result360Time").textContent = `${secondsToTime(video.currentTime || 0)} / ${secondsToTime(video.duration || 0)}`;
  if (video.ended) document.querySelector("#result360Play").textContent = "Play";
}

function setupTrimControls(masterPath) {
  const master = detected.master.find((item) => item.path === masterPath) || {};
  const duration = Number(master.duration || 0);
  const preview = document.querySelector("#masterPreview");
  if (preview && preview.dataset.path !== masterPath) {
    preview.dataset.path = masterPath || "";
    preview.src = `/api/v1/wizard/master-preview?path=${encodeURIComponent(masterPath)}&t=${Date.now()}`;
    preview.onerror = () => showToast("Could not load the master audio preview", true);
  }
  if (trimDefaultsAppliedFor !== masterPath) {
    const saved = savedAudioTrim[masterPath] || {};
    const start = Number(saved.start_sec);
    const end = Number(saved.end_sec);
    document.querySelector("#trimStart").value = Number.isFinite(start) ? secondsToTime(start) : "00:00";
    document.querySelector("#trimEnd").value = Number.isFinite(end) ? secondsToTime(end) : duration ? secondsToTime(duration) : "00:00";
    trimDefaultsAppliedFor = masterPath || "";
  }
}

function renderSongOptions(songs) {
  currentSongs = songs;
  const picker = document.querySelector("#songPicker");
  const root = document.querySelector("#songOptions");
  selectedSong = songs.length ? 0 : null;
  if (songs.length <= 1) {
    picker.hidden = true;
    root.innerHTML = "";
    return;
  }
  picker.hidden = false;
  const allOption = selectedPlatform === "youtube" || selectedPlatform === "360" ? `<button class="song-option selected" data-song="all">${escapeHtml(S.allSongs)}</button>` : "";
  selectedSong = selectedPlatform === "youtube" || selectedPlatform === "360" ? "all" : 0;
  root.innerHTML =
    allOption +
    songs
      .map((song, index) => {
        const duration = song.end_sec == null ? "" : ` · ${Math.round(song.end_sec - song.start_sec)}s`;
        return `<button class="song-option ${selectedSong === index ? "selected" : ""}" data-song="${index}">${escapeHtml(song.title)}${duration}</button>`;
      })
      .join("");
}

async function waitForPreparedProject() {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const projectQuery = activeProjectId ? `?project_id=${encodeURIComponent(activeProjectId)}` : "";
    const status = await api(`/wizard/status${projectQuery}`);
    const responseProjectId = projectIdFromStatus(status);
    if (activeProjectId && responseProjectId && !sameProjectId(activeProjectId, responseProjectId)) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      continue;
    }
    if (!activeProjectId && responseProjectId) activeProjectId = responseProjectId;
    if (status.status === "waiting_choice") return;
    if (status.status === "done") return;
    if (status.status === "failed") throw new Error(status.error || S.prepareFailed);
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(S.prepareTimeout);
}

async function startWizard(options = {}) {
  const waitForPrepare = options.waitForPrepare === true;
  const inputs = selectedInputs();
  lastPipelineStage = null;
  hideStageTransition();
  setStep(3);
  if (waitForPrepare && selectedPlatform !== "360" && selectedPlatform !== "reel" && selectedPlatform !== "backstage") {
    prepareHandoffInProgress = true;
    const prepared = await api("/wizard/prepare", {
      method: "POST",
      body: JSON.stringify({
        name: document.querySelector("#videoName").value || todayName(),
        platform: selectedPlatform,
        master: inputs.master,
        songs: inputs.songs,
        videos: inputs.videos,
      }),
    });
    activeProjectId = projectIdFromStatus(prepared) || activeProjectId;
    ensureStatusPolling();
    await waitForPreparedProject();
    prepareHandoffInProgress = false;
  }
  const started = await api("/wizard/start", {
    method: "POST",
    body: JSON.stringify({
      name: document.querySelector("#videoName").value || todayName(),
      platform: selectedPlatform,
      song_index: selectedSong,
      trim_start_sec: timeToSeconds(document.querySelector("#trimStart").value),
      trim_end_sec: timeToSeconds(document.querySelector("#trimEnd").value),
      // 360 shot angles remain project configuration, not a per-export UI step.
      spherical_landmarks: appConfig?.spherical_landmarks || {},
      camera_role_weights: cameraRoleWeightsFromForm(),
      fixed_rear_motion: fixedRearMotionFromForm(),
      spherical_motion: false,
      spherical_mode: "automatic",
      spherical_sweep: false,
      sweep_speed_deg_per_sec: appConfig?.sweep_speed_deg_per_sec || 60,
      reel_duration_sec: reelOptionsFromForm().duration,
      backstage_duration_sec: Number(document.querySelector("#backstageDuration")?.value || 180),
      reel_aspect: reelOptionsFromForm().aspect,
      reel_mix_vertical_ratio: reelOptionsFromForm().mixVerticalRatio,
      reel_text_overlays: reelOptionsFromForm().texts,
      reel_image_overlays: reelOptionsFromForm().images,
      backstage_messages: backstageMessagesFromForm(),
      master: inputs.master,
      songs: inputs.songs,
      videos: inputs.videos,
      variation_seed: currentVariationSeed,
    }),
  });
  activeProjectId = projectIdFromStatus(started) || activeProjectId;
  ensureStatusPolling();
  await pollStatus(statusPollGeneration);
}

async function cancelWizard() {
  if (!confirm("Stop this export? Progress so far will be lost.")) return;
  const button = document.querySelector("#cancelWizard");
  if (button) {
    button.disabled = true;
    button.textContent = "Cancelling…";
  }
  try {
    await api("/wizard/cancel", { method: "POST" });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Cancel";
    }
  }
}

function timeToSeconds(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const parts = text.split(":").map((part) => Number(part));
  if (parts.some((part) => !Number.isFinite(part))) return null;
  if (parts.length === 1) return Math.max(0, parts[0]);
  const seconds = parts.pop();
  const minutes = parts.pop() || 0;
  const hours = parts.pop() || 0;
  return Math.max(0, hours * 3600 + minutes * 60 + seconds);
}

function secondsToTime(seconds) {
  const whole = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function ensureStatusPolling() {
  if (!pollTimer) {
    const generation = statusPollGeneration;
    pollTimer = setInterval(() => {
      pollStatus(generation).catch((error) => {
        logFrontendError(`pollStatus failed: ${error.message}`, error.stack || "");
        showToast(`${S.statusUpdateFailed}: ${error.message}`, true);
      });
    }, 1000);
  }
}

async function pollStatus(generation = statusPollGeneration) {
  const projectQuery = activeProjectId ? `?project_id=${encodeURIComponent(activeProjectId)}` : "";
  const status = await api(`/wizard/status${projectQuery}`);
  if (generation !== statusPollGeneration) return;
  const responseProjectId = projectIdFromStatus(status);
  if (activeProjectId && responseProjectId && !sameProjectId(activeProjectId, responseProjectId)) return;
  if (!activeProjectId && responseProjectId) activeProjectId = responseProjectId;
  renderWizardStatus(status);
  const details = document.querySelector("#progressDetails");
  if (details?.open && Date.now() - lastProgressReportAt > 2500) {
    refreshProgressReport().catch((error) => logFrontendError(`progress report failed: ${error.message}`, error.stack || ""));
  }
}

// Playful, rotating variants for the main progress bar's headline message,
// keyed by the exact factual string the backend sends (core/messages.py).
// The detail line right underneath stays factual as-is -- this only
// touches the big headline text, in the spirit of Claude Code's own
// varied "Pondering...", "Noodling..." style working messages. Each pool's
// first entry is the original plain phrasing, so a stage that's barely
// begun still shows something sensible before rotation kicks in.
const PLAYFUL_PROGRESS_MESSAGES = {
  "Listening to your videos...": [
    "Listening to your videos...",
    "Earwigging the footage",
    "Sound-checking every angle",
    "Auditioning the takes",
    "Tuning into the clips",
    "Listening for the downbeat",
    "Scouting the setlist",
    "Soundchecking the room",
  ],
  "Syncing with the audio...": [
    "Syncing with the audio...",
    "Pacing the bars",
    "Counting in the cameras",
    "Locking to the groove",
    "Tuning the timeline",
    "Metronoming the multicam",
    "Harmonising the angles",
    "Getting everyone on the one",
    "Beeboping the beats",
  ],
  "Cutting the song...": [
    "Cutting the song...",
    "Beeboping the frames",
    "Chasing the chorus",
    "Marking the drops",
    "Phrasing the verses",
    "Mapping the middle eight",
    "Syncopating the segments",
    "Riffing on the arrangement",
    "Crescendoing the cuts",
  ],
  "Building the edit...": [
    "Building the edit...",
    "Rocking the cuts out",
    "Grooving the angles",
    "Swinging the shots",
    "Funkying the transitions",
    "Riffing on the edit",
    "Improvising the b-roll",
    "Choreographing the cameras",
    "Serenading the segments",
  ],
  "Exporting the video...": [
    "Exporting the video...",
    "Mixing the magic",
    "Jamming the pixels",
    "Enchanting the film",
    "Mastering the final cut",
    "Bouncing down the reel",
    "Polishing the encore",
    "Pressing the record",
    "Motivating the colours",
    "Reverbing the render",
  ],
};

const PROGRESS_MESSAGE_ROTATE_MS = 2800;
let progressMessageRotation = { key: null, order: [], index: 0, at: 0 };

function playfulProgressMessage(rawMessage) {
  const pool = PLAYFUL_PROGRESS_MESSAGES[rawMessage];
  if (!pool || pool.length <= 1) {
    progressMessageRotation = { key: null, order: [], index: 0, at: 0 };
    return rawMessage;
  }
  const now = Date.now();
  if (progressMessageRotation.key !== rawMessage) {
    // Entering this stage fresh: keep the plain phrasing first, then
    // shuffle the rest so a long-running stage doesn't always cycle
    // through the playful variants in the same order.
    const rest = pool.slice(1);
    for (let i = rest.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [rest[i], rest[j]] = [rest[j], rest[i]];
    }
    progressMessageRotation = { key: rawMessage, order: [pool[0], ...rest], index: 0, at: now };
  } else if (now - progressMessageRotation.at >= PROGRESS_MESSAGE_ROTATE_MS) {
    progressMessageRotation.index = (progressMessageRotation.index + 1) % progressMessageRotation.order.length;
    progressMessageRotation.at = now;
  }
  return progressMessageRotation.order[progressMessageRotation.index];
}

function renderWizardStatus(status) {
  latestStatus = status;
  for (const warning of status.input_warnings || []) {
    if (!inputWarningsShown.has(warning)) {
      inputWarningsShown.add(warning);
      showToast(warning, true);
    }
  }
  const reportedProgress = Math.max(0, Math.min(100, Number(status.progress || 0)));
  const progress = Math.max(progressFloor, reportedProgress);
  const progressBox = document.querySelector("#progressBox");
  const reviewBox = document.querySelector("#reviewBox");
  const platform = selectedPlatform || status.result?.platform || "";
  const stage = status.stage || "";
  if (status.status === "waiting_choice") {
    // The strip is a live-progress affordance. A terminal sync response must
    // clear it even though this branch intentionally avoids repainting the
    // progress card while the edit-type screen is being restored.
    renderStatusStrip(status, progress);
    if (prepareHandoffInProgress) {
      // The mode was already chosen before Prepare/Sync. Keep the user in the
      // progress view while the prepared project is handed directly to Cut.
      setStep(3);
      if (lastPipelineStage !== "sync-ready") {
        if (selectedPlatform === "youtube") showYouTubeSyncToCut();
        else if (selectedPlatform === "reel") showReelSyncToCut();
        else if (selectedPlatform === "360") show360SyncToCut();
        lastPipelineStage = "sync-ready";
      }
    } else {
      if (progressBox) progressBox.hidden = true;
      stopStatusPolling();
      resetProgressTiming();
      document.querySelector("#progressBar").style.width = "0%";
      document.querySelector("#progressPercent").textContent = "0%";
      document.querySelector("#progressMessage").textContent = status.message || "Ready to edit";
      document.querySelector("#progressDetail").textContent = status.detail || "Choose an edit type";
      document.querySelector("#elapsedTime").textContent = `${S.elapsed}: 0 s`;
      document.querySelector("#etaTime").textContent = `${S.eta}: estimating…`;
      setStep(2);
    }
    return;
  }
  if (status.status === "running") {
    setStep(3);
    if (stage && stage !== lastPipelineStage) stopAllPreviewAudio();
    if (stage === "cut" && lastPipelineStage !== "cut") {
      if (platform === "youtube") showYouTubeSyncToCut();
      else if (platform === "reel") showReelSyncToCut();
      else if (platform === "360") show360SyncToCut();
    } else if (stage === "export" && platform === "360" && lastPipelineStage === "edit") {
      show360EditToExport();
    }
    lastPipelineStage = stage || lastPipelineStage;
  }
  if (status.status === "waiting_review") {
    if (progressBox) progressBox.hidden = true;
    if (reviewBox) reviewBox.hidden = true;
    document.querySelector("#progressTitle").textContent = "Review shots";
    if (lastPipelineStage !== "review") {
      // Keep the transition and the Frames page hidden until both the API
      // payload and the browser image decoders have completed. This avoids
      // announcing Frames while the user is still staring at empty cards.
      lastPipelineStage = "review-loading";
      openShotReview()
        .then(() => {
          showToast("Review shots are ready — choose your takes now");
          showReviewReadyForPlatform(platform);
          lastPipelineStage = "review";
        })
        .catch((error) => {
          lastPipelineStage = "review";
          showToast(error.message, true);
        });
    }
    stopStatusPolling();
    return;
  }
  if (status.status === "waiting_paper_edit") {
    if (progressBox) progressBox.hidden = true;
    if (reviewBox) reviewBox.hidden = true;
    const paperBox = document.querySelector("#paperEditBox");
    if (paperBox) paperBox.hidden = false;
    if (lastPipelineStage !== "paper_edit") {
      lastPipelineStage = "paper_edit-loading";
      openPaperEdit().then(() => { lastPipelineStage = "paper_edit"; }).catch((error) => showToast(error.message, true));
    }
    stopStatusPolling();
    setStep(4);
    return;
  }
  if (status.status === "running" || status.status === "failed" || status.status === "done") {
    if (progressBox) progressBox.hidden = false;
    if (reviewBox) reviewBox.hidden = true;
    document.querySelector("#paperEditBox")?.setAttribute("hidden", "");
  }
  progressFloor = progress;
  updateTiming(status, progress);
  renderStatusStrip(status, progress);
  document.querySelector("#progressBar").style.width = `${progress}%`;
  document.querySelector("#progressPercent").textContent = `${Math.round(progress)}%`;
  document.querySelector("#progressMessage").textContent = playfulProgressMessage(status.message) || S.working;
  document.querySelector("#progressDetail").textContent = status.detail || currentSubtask(status) || S.nextStep;
  document.querySelector("#elapsedTime").textContent = `${S.elapsed}: ${formatElapsed(elapsedSeconds())}`;
  document.querySelector("#etaTime").textContent = `${S.eta}: ${formatEta(etaSeconds(progress, status))}`;
  updateStageChecks(progress, status);
  if (status.status === "running") {
    document.querySelector("#progressTitle").textContent = "Creating your video";
  }
  if (status.status === "failed") {
    stopStatusPolling();
    document.querySelector("#errorText").textContent = status.error || S.failedTitle;
    document.querySelector("#resultTitle").textContent = S.failedTitle;
    document.querySelector("#errorBox").hidden = false;
    document.querySelector("#resultBox").hidden = true;
    refreshProgressReport().catch((error) => logFrontendError(`progress report failed: ${error.message}`, error.stack || ""));
    setStep(6);
  }
  if (status.status === "done") {
    stopStatusPolling();
    latestResult = status.result;
    document.querySelector("#progressTitle").textContent = S.doneTitle;
    document.querySelector("#resultTitle").textContent = S.doneTitle;
    document.querySelector("#resultFilename").textContent = latestResult.filename;
    document.querySelector("#resultSummary").textContent = resultSummary(latestResult);
    renderClipFates(latestResult);
    renderSphericalShots(latestResult);
    hideResultPlaybackWarning();
    const mediaUrl = `${latestResult.media_url}?t=${Date.now()}`;
    if (latestResult.platform === "360") {
      document.querySelector("#resultVideo").hidden = true;
      document.querySelector("#result360Player").hidden = false;
      document.querySelector("#result360Controls").hidden = false;
      setupResult360Viewer(mediaUrl).catch((error) => {
        logFrontendError(`360 result viewer failed: ${error.message}`, error.stack || "");
        // Fall back to the plain player rather than leaving the result blank.
        document.querySelector("#resultVideo").hidden = false;
        document.querySelector("#result360Player").hidden = true;
        document.querySelector("#result360Controls").hidden = true;
        document.querySelector("#resultVideo").src = mediaUrl;
        watchForUndecodableVideo(document.querySelector("#resultVideo"));
      });
      watchForUndecodableVideo(document.querySelector("#result360Video"));
    } else {
      teardownResult360Viewer();
      document.querySelector("#resultVideo").hidden = false;
      document.querySelector("#result360Player").hidden = true;
      document.querySelector("#result360Controls").hidden = true;
      document.querySelector("#resultVideo").src = mediaUrl;
      watchForUndecodableVideo(document.querySelector("#resultVideo"));
    }
    document.querySelector("#errorBox").hidden = true;
    document.querySelector("#resultBox").hidden = false;
    if (status.stage === "compose") {
      setStep(6);
      return;
    }
    setStep(5);
    document.querySelector("#resultBox").hidden = true;
    openCaptions().catch((error) => showToast(error.message, true));
  }
  if (status.status === "cancelled") {
    stopStatusPolling();
    document.querySelector("#errorText").textContent = "Export cancelled.";
    document.querySelector("#resultTitle").textContent = "Cancelled";
    document.querySelector("#errorBox").hidden = false;
    document.querySelector("#resultBox").hidden = true;
    setStep(6);
  }
}

function renderStatusStrip(status, progress) {
  const strip = document.querySelector("#statusStrip");
  if (status.status !== "running") {
    strip.hidden = true;
    return;
  }
  strip.hidden = false;
  const detail = currentSubtask(status) || status.message || S.working;
  document.querySelector("#statusStripText").textContent = `${detail} · ${Math.round(progress)}% · ${formatEta(etaSeconds(progress, status))}`;
}

function currentSubtask(status) {
  return String(status.detail || "").replace(/^(.+?) — /, "$1 · ");
}

function updateTiming(status, progress) {
  if (status.status !== "running") return;
  const now = Date.now();
  const previous = progressSamples[progressSamples.length - 1];
  if (!progressStartedAt) {
    progressStartedAt = now;
    progressSamples = [];
    etaSmoothedSeconds = null;
  }
  const last = progressSamples[progressSamples.length - 1];
  if (!last || progress !== last.progress) {
    progressSamples.push({ time: now, progress });
    progressSamples = progressSamples.slice(-12);
  }
}

function elapsedSeconds() {
  return progressStartedAt ? Math.max(0, (Date.now() - progressStartedAt) / 1000) : 0;
}

// Predicted whole-run seconds from the backend, derived from throughput this
// machine actually measured on previous exports (core/throughput.py). It is
// available from the first poll after the cut stage — long before the in-run
// rate estimate below has anything trustworthy to say.
function backendEtaSeconds(status, progress) {
  const total = Number(status?.estimated_total_seconds || 0);
  if (!total || !Number.isFinite(total)) return null;
  const elapsed = elapsedSeconds();
  const byProgress = progress > 0 ? total * (1 - progress / 100) : total;
  // Never claim less time than the run has already overshot by.
  return Math.max(byProgress, total - elapsed, 0);
}

function etaSeconds(progress, status) {
  const elapsed = elapsedSeconds();
  if (progress <= 0 || progress >= 100 || progressSamples.length < 2) {
    etaSmoothedSeconds = null;
    return backendEtaSeconds(status, progress);
  }
  const first = progressSamples[0];
  const last = progressSamples[progressSamples.length - 1];
  // Rolling throughput observed so far *this run* — not a fixed assumption —
  // over the last ~12 progress samples (see updateTiming).
  const rate = (last.progress - first.progress) / ((last.time - first.time) / 1000);
  if (rate <= 0) return etaSmoothedSeconds ?? backendEtaSeconds(status, progress);
  const raw = (100 - progress) / rate;
  if (elapsed < 30) {
    // Too early for the in-run rate to mean anything: the first stages fly by
    // and then rendering crawls, which is exactly how the old estimate ended up
    // promising "1-3 minutes" for a much longer job. Prefer the measured
    // machine history, and blend the two only once both are meaningful.
    const backend = backendEtaSeconds(status, progress);
    if (backend != null) return backend;
    return null;
  }
  if (etaSmoothedSeconds == null) {
    etaSmoothedSeconds = raw;
  } else {
    // A single slow/fast sample (e.g. hitting an expensive 360 motion
    // segment) shouldn't make the displayed estimate lurch; cap how much it
    // can jump upward in one tick and blend the rest in smoothly.
    const maxUp = etaSmoothedSeconds * 1.25 + 10;
    etaSmoothedSeconds = etaSmoothedSeconds * 0.7 + Math.min(raw, maxUp) * 0.3;
  }
  return etaSmoothedSeconds;
}

function formatElapsed(seconds) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s`;
  return `${Math.round(seconds / 60)} min`;
}

function formatEta(seconds) {
  // Honest by construction: with nothing measured to go on we say we are still
  // estimating rather than showing an optimistic placeholder.
  if (seconds == null || !Number.isFinite(seconds)) return "estimating…";
  if (seconds < 90) return `~${Math.max(5, Math.round(seconds / 5) * 5)} s remaining`;
  return `~${Math.max(1, Math.round(seconds / 60))} min remaining`;
}

function updateStageChecks(progress, status) {
  const done = new Set();
  if (progress >= 22) done.add("ingest");
  if (progress >= 48 || status.status === "waiting_choice") done.add("sync");
  if (progress >= 58) done.add("cut");
  if (status.status === "done") done.add("export");
  document.querySelectorAll(".stage-checks [data-stage]").forEach((node) => {
    node.classList.toggle("done", done.has(node.dataset.stage));
  });
}

function resultSummary(result) {
  const cutCount = Number(result?.cut_count || 0);
  const cameraUsage = result?.camera_usage || {};
  const cameraCount = Object.keys(cameraUsage).length;
  const parts = [];
  if (cutCount > 0 || cameraCount > 0) parts.push(`${cutCount} cuts · ${cameraCount} cameras`);
  const warnings = result?.warnings || [];
  if (warnings.length) parts.push(warnings.join(" · "));
  const excluded = result?.excluded_clips || [];
  if (excluded.length) {
    parts.push(
      excluded
        .map((item) => `${item.filename || "clip"}: ${item.reason || "excluded"}`)
        .join(" · ")
    );
  }
  return parts.join(" · ");
}

function renderClipFates(result) {
  const root = document.querySelector("#clipFates");
  const fates = result?.clip_fates || [];
  if (!fates.length) {
    root.innerHTML = "";
    return;
  }
  root.innerHTML = `
    <h2>Camera status</h2>
    ${fates
      .map((item) => {
        const confidence = item.confidence == null ? "" : ` · confidence ${Number(item.confidence).toFixed(1)}`;
        const used = item.status === "used" ? ` · ${Number(item.used_percent || 0).toFixed(1)}% of timeline` : "";
        const rescue =
          item.status === "excluded" && item.clip_id
            ? `<button class="rescue-button" data-rescue="${escapeHtml(item.clip_id)}" data-offset="${Number(item.offset_sec || 0)}">Rescue</button>`
            : "";
        return `<div class="clip-fate ${escapeHtml(item.status || "")}">
          <div>
            <strong>${escapeHtml(item.filename || "clip")}</strong>
            <span>${escapeHtml(fateLabel(item.status))}${used}${confidence}</span>
            <small>${escapeHtml(item.reason || "")}</small>
          </div>
          ${rescue}
        </div>`;
      })
      .join("")}`;
}

function renderSphericalShots(result) {
  const usage = result?.spherical_shot_usage || {};
  const entries = Object.entries(usage);
  const summary = entries.map(([label, count]) => `${label} x${count}`).join(", ");
  const recording = result?.spherical_recording_usage || {};
  const recordedCount = Number(recording.recorded_segments || 0);
  const landmarkCount = Number(recording.landmark_segments || 0);
  const takeSummary = Object.entries(recording.takes || {})
    .map(([name, count]) => `${count} from "${name}"`)
    .join(", ");
  let recordingSummary = "";
  if (recording.recorded_takes_ignored) {
    recordingSummary = `Automatic mode - ${Number(recording.recorded_takes_available || 0)} recorded take(s) saved but not used. Switch to Directed to use them.`;
  } else if (recordedCount || landmarkCount) {
    recordingSummary = `360 source: ${recordedCount} segments from recorded take${takeSummary ? ` (${takeSummary})` : ""}, ${landmarkCount} from landmark shots.`;
  } else if (recording.mode === "automatic") {
    recordingSummary = "Automatic mode - landmark shots used when 360 is selected.";
  }
  const existing = document.querySelector("#sphericalShotUsage");
  if (!entries.length) {
    if (existing) existing.remove();
    return;
  }
  const root = document.querySelector("#clipFates");
  const html = `<div id="sphericalShotUsage" class="clip-fate used"><div><strong>360 shots</strong><span>${escapeHtml(summary)}</span><small>${escapeHtml(recordingSummary)}</small></div></div>`;
  if (existing) existing.outerHTML = html;
  else root.insertAdjacentHTML("beforebegin", html);
}

function fateLabel(status) {
  if (status === "used") return S.fateUsed || "Used";
  if (status === "excluded") return S.fateExcluded || "Excluded";
  if (status === "not_covering") return S.fateNotCovering || "Not covering this song";
  return status || "";
}

async function openRescue(clipId, offset) {
  rescueClipId = clipId;
  const fate = (latestResult?.clip_fates || []).find((item) => item.clip_id === clipId) || {};
  const panel = document.querySelector("#rescuePanel");
  document.querySelector("#rescueTitle").textContent = `${S.rescueCamera || "Rescue camera"}: ${fate.filename || clipId}`;
  document.querySelector("#rescueMeta").textContent = `${fate.reason || ""}${fate.confidence == null ? "" : ` · confidence ${Number(fate.confidence).toFixed(1)}`}`;
  document.querySelector("#rescueOffset").value = Number(offset || fate.offset_sec || 0).toFixed(3);
  document.querySelector("#rescueOffsetRanges").value = "";
  const preview = await api(`/stages/sync/preview/${encodeURIComponent(clipId)}`);
  document.querySelector("#rescuePreview").src = `${preview.media_url}?t=${Date.now()}`;
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function confirmRescue() {
  if (!rescueClipId) return;
  const rangesText = document.querySelector("#rescueOffsetRanges").value.trim();
  let ranges = null;
  if (rangesText) {
    try {
      ranges = JSON.parse(rangesText);
      if (!Array.isArray(ranges)) throw new Error("Ranges must be a JSON array");
    } catch (error) {
      showToast(`${S.invalidOffset || "Invalid offset"}: ${error.message}`, true);
      return;
    }
  }
  const offset = Number(document.querySelector("#rescueOffset").value);
  if (!ranges && !Number.isFinite(offset)) {
    showToast(S.invalidOffset || "Enter a valid offset", true);
    return;
  }
  document.querySelector("#rescuePanel").hidden = true;
  document.querySelector("#resultBox").hidden = true;
  setStep(3);
  const body = ranges ? { clip_id: rescueClipId, offset_ranges: ranges } : { clip_id: rescueClipId, offset_sec: offset };
  await api("/wizard/rescue", { method: "POST", body: JSON.stringify(body) });
  ensureStatusPolling();
  await pollStatus(statusPollGeneration);
}

async function revealNative(path, label) {
  if (!path) throw new Error(`${label || "Native action"}: no file path is available yet`);
  await window.NativeBridge.reveal(path, label);
}

async function openLogs() {
  const path = latestResult?.logs_path || latestStatus?.logs_path;
  await revealNative(path, S.logs);
}

async function copyReport() {
  const response = await fetch("/api/v1/wizard/report");
  const text = await response.text();
  await navigator.clipboard.writeText(text);
  showToast(S.reportCopied);
}

async function refreshProgressReport() {
  lastProgressReportAt = Date.now();
  const response = await fetch("/api/v1/wizard/report");
  const text = await response.text();
  const report = document.querySelector("#progressReport");
  if (!report) return;
  const wasNearBottom = report.scrollHeight - report.scrollTop - report.clientHeight <= 24;
  report.textContent = text || "No details yet.";
  const details = document.querySelector("#progressDetails");
  // Keep the live tail visible, but never yank the user away from an older
  // line they deliberately scrolled up to inspect.
  if (details?.open && wasNearBottom) report.scrollTop = report.scrollHeight;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

document.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (event.target.closest?.("#dropZone")) document.querySelector("#dropZone").classList.add("dragging");
});

document.addEventListener("dragleave", (event) => {
  if (event.target.closest?.("#dropZone")) document.querySelector("#dropZone").classList.remove("dragging");
});

document.addEventListener("drop", (event) => {
  if (!event.target.closest?.("#dropZone")) return;
  handleDrop(event).catch((error) => showToast(error.message, true));
});

document.addEventListener("click", (event) => {
  const rawTarget = event.target;
  const target = rawTarget instanceof HTMLElement ? rawTarget.closest("button, [data-remove-kind], [data-open-project], [data-delete-project], [data-rescue], [data-rescan-folder], [data-remove-folder]") || rawTarget : rawTarget;
  if (!(target instanceof HTMLElement)) return;
  const reviewThumb = rawTarget instanceof HTMLElement ? rawTarget.closest("[data-review-thumb]") : null;
  if (reviewThumb) {
    const item = shotReviewItems.find((entry) => Number(entry.index) === Number(reviewThumb.dataset.reviewThumb));
    if (item?.thumbnail) { document.querySelector("#reviewLarge").src = item.thumbnail; document.querySelector("#reviewLargeModal").hidden = false; }
  }
  if (target.id === "closeReviewLarge" || target.id === "reviewLargeModal") document.querySelector("#reviewLargeModal").hidden = true;
  if (target.id === "replaceRejected") {
    const rejected = shotReviewItems.filter((item) => !item.keep).map((item) => Number(item.index));
    if (!rejected.length) { showToast("Select at least one shot to replace", true); return; }
    target.disabled = true;
    api("/wizard/review/replace", { method: "POST", body: JSON.stringify({ rejected }) })
      .then((result) => {
        renderShotReview(result.items || []);
        const unavailable = result.replacement_diagnostics?.filter((item) => item.status === "unavailable") || [];
        if (unavailable.length) {
          const indexes = unavailable.map((item) => `#${Number(item.index) + 1}`).join(", ");
          showToast(`No alternative coverage for ${indexes}. The shot was kept and marked unavailable.`);
        }
        if ((result.replaced || []).length) refreshShotReviewAfterReplace();
      })
      .catch((error) => showToast(error.message, true))
      .finally(() => { target.disabled = false; });
  }
  if (target.id === "renderReviewed") {
    if (shotReviewItems.some((item) => !item.keep)) { showToast("Replace or re-approve rejected shots before rendering", true); return; }
    api("/wizard/review/render", { method: "POST" }).then((started) => { activeProjectId = projectIdFromStatus(started) || activeProjectId; document.querySelector("#reviewBox").hidden = true; document.querySelector("#progressBox").hidden = false; setStep(3); ensureStatusPolling(); return pollStatus(statusPollGeneration); }).catch((error) => showToast(error.message, true));
  }
  if (target.id === "approvePaperEdit") {
    const rejected = Array.from(document.querySelectorAll("[data-paper-reject]:checked"))
      .map((input) => input.dataset.paperReject)
      .filter(Boolean);
    target.disabled = true;
    api("/wizard/paper-edit/approve", { method: "POST", body: JSON.stringify({ rejected }) })
      .then((started) => {
        activeProjectId = projectIdFromStatus(started) || activeProjectId;
        document.querySelector("#paperEditBox").hidden = true;
        document.querySelector("#progressBox").hidden = false;
        setStep(3);
        ensureStatusPolling();
        return pollStatus(statusPollGeneration);
      })
      .catch((error) => showToast(error.message, true))
      .finally(() => { target.disabled = false; });
  }
  if (target.id === "reuseReelOverlays") {
    api("/wizard/overlays").then((saved) => {
      reelImageOverlays = saved.images || [];
      reelVideoOverlays = saved.videos || [];
      const reusedText = (saved.texts || []).map((item) => ({
        lines: String(item.text || "").split(/\r?\n/),
        start: Number(item.start_sec || 0),
        end: Number(item.start_sec || 0) + Math.max(0.1, Number(item.duration_sec || 3)),
        style_override: { color: item.color || "#ffffff", size: Number(item.size || 54), vertical: item.y != null ? Number(item.y) * 100 : 50, animation_in: item.animation || "fade", animation_out: item.animation || "fade" },
      })).filter((cue) => cue.lines.join("").trim());
      captionCues = [...captionCues, ...reusedText];
      syncCaptionTextArea(); renderCaptionBlocks(); renderReelOptions();
    }).catch((error) => showToast(error.message, true));
  }
  if (target.id === "addReelText") {
    reelTextOverlays.push({ text: "", color: "#ffffff", size: 54, position: "middle-center", x: 0.5, y: 0.5, opacity: 1, font: "bundled", font_weight: "bold", outline_color: "#000000", outline_width: 2, shadow_color: "#000000", shadow_offset_x: 3, shadow_offset_y: 3, shadow_blur: 4, background_color: "#000000", background_opacity: 0, background_radius: 8, animation: "fade", start_sec: 0, duration_sec: 3 });
    renderReelOptions();
  }
  if (target.id === "captionDeleteAll") {
    captionCues = [];
    captionMarkIndex = 0;
    captionPendingStart = null;
    captionActiveIndex = null;
    const textarea = document.querySelector("#captionText");
    if (textarea) textarea.value = "";
    renderCaptionBlocks();
    return;
  }
  if (target.dataset.captionDelete != null) {
    const index = Number(target.dataset.captionDelete);
    const blocks = captionBlocksFromText();
    if (Number.isInteger(index) && index >= 0 && index < captionCues.length) {
      captionCues.splice(index, 1);
      blocks.splice(index, 1);
      const textarea = document.querySelector("#captionText");
      if (textarea) textarea.value = blocks.join("\n\n");
      captionMarkIndex = Math.min(captionMarkIndex, captionCues.length);
      captionPendingStart = null;
      if (captionActiveIndex === index) captionActiveIndex = null;
      else if (captionActiveIndex != null && captionActiveIndex > index) captionActiveIndex -= 1;
      renderCaptionBlocks();
    }
    return;
  }
  if (target.id === "composeAddCaption") { addCaptionFromPlayback(); return; }
  const captionPreview = target.closest?.("[data-caption-preview-index]");
  if (captionPreview instanceof HTMLElement) {
    const index = Number(captionPreview.dataset.captionPreviewIndex);
    const field = document.querySelector(`[data-caption-text="${index}"]`);
    if (Number.isInteger(index) && field instanceof HTMLElement) {
      captionActiveIndex = index;
      renderCaptionBlocks();
      document.querySelector(`[data-caption-text="${index}"]`)?.focus();
    }
    return;
  }
  if (target.id === "composeEndCaption") {
    const video = document.querySelector("#composeVideo");
    if (video) closeActiveCaption(Number(video.currentTime || 0));
    return;
  }
  if (target.dataset.captionEdit != null) {
    const index = Number(target.dataset.captionEdit);
    const blocks = captionBlocksFromText();
    const textarea = document.querySelector("#captionText");
    if (textarea && blocks[index] != null) {
      const offset = blocks.slice(0, index).reduce((total, block) => total + block.length + 2, 0);
      textarea.focus();
      textarea.setSelectionRange(offset, offset + blocks[index].length);
    }
    return;
  }
  // Caption rows are also clickable seek targets. Do not let that parent
  // handler rebuild the row when a form control inside it is clicked: native
  // color pickers in particular must keep the original input alive while the
  // browser opens their picker.
  if (target.closest?.(".caption-block") && target.closest("input, select, textarea")) return;
  const timelineScroll = target.closest?.("[data-compose-timeline]");
  if (timelineScroll instanceof HTMLElement) {
    if (timelineSuppressClick) { timelineSuppressClick = false; return; }
    const overlayBlock = target.closest?.(".compose-timeline-block.overlay");
    if (overlayBlock instanceof HTMLElement) {
      const kind = overlayBlock.dataset.overlayKind;
      const index = Number(overlayBlock.dataset.overlayIndex);
      const item = kind === "image" ? reelImageOverlays[index] : reelVideoOverlays[index];
      const video = document.querySelector("#composeVideo");
      if (item && video) {
        selectedComposeOverlay = { kind, index };
        selectedComposeLogo = false;
        captionActiveIndex = null;
        video.pause();
        video.currentTime = Number(item.start_sec || 0);
        renderReelOptions();
        renderComposeOverlayLayer();
      }
      return;
    }
    const captionBlock = target.closest?.(".compose-timeline-block.caption");
    if (captionBlock instanceof HTMLElement) {
      const index = Number(captionBlock.dataset.captionSeek);
      const cue = captionCues[index];
      const video = document.querySelector("#composeVideo");
      if (cue && video) {
        captionActiveIndex = index;
        selectedComposeOverlay = null;
        selectedComposeLogo = false;
        video.pause();
        video.currentTime = Number(cue.start) || 0;
        renderCaptionBlocks();
        renderComposeOverlayLayer();
      }
      return;
    }
    seekFromComposeTimeline(event, timelineScroll);
    return;
  }
  const captionRow = target.closest?.("[data-caption-seek]");
  if (captionRow instanceof HTMLElement && captionRow.dataset.captionSeek != null) {
    const captionIndex = Number(captionRow.dataset.captionSeek);
    const cue = captionCues[captionIndex];
    const video = document.querySelector("#composeVideo");
    if (cue && video) {
      captionActiveIndex = captionIndex;
      selectedComposeOverlay = null;
      video.currentTime = Number(cue.start) || 0;
      video.pause();
      renderCaptionBlocks();
      renderComposeOverlayLayer();
    }
    return;
  }
  if (target.dataset.removeReelText != null) {
    reelTextOverlays.splice(Number(target.dataset.removeReelText), 1);
    renderReelOptions();
  }
  if (target.dataset.removeReelImage != null) {
    reelImageOverlays.splice(Number(target.dataset.removeReelImage), 1);
    renderReelOptions();
  }
  if (target.dataset.removeReelVideo != null) {
    reelVideoOverlays.splice(Number(target.dataset.removeReelVideo), 1);
    renderReelOptions();
  }
  if (target.id === "showAllClips") restoreSetAsideVideos();
  if (target.id === "scanInbox") {
    loadInbox()
      .then(() => api("/inbox/analysis/start", { method: "POST" }))
      .then((status) => { inboxAnalysis = status; renderInboxAnalysisStatus(); scheduleInboxAnalysisRefresh(); })
      .catch((error) => showToast(error.message, true));
  }
  if (target.id === "addMasterVideos") addVideosForSelectedMaster();
  if (target.id === "addSourceFolder") {
    const input = document.querySelector("#sourceFolderPath");
    const folder = input?.value.trim() || "";
    if (!folder.startsWith("/")) { showToast("Source folder must be an absolute path", true); return; }
    saveSourceFolders([...sourceFolders.map((item) => typeof item === "string" ? item : item.path), folder])
      .then(() => { input.value = ""; showToast("Source folder added"); })
      .catch((error) => showToast(error.message, true));
  }
  if (target.id === "masterAudioFilter") {
    const extensions = target.value === "mp3" ? [".mp3"] : [".wav", ".mp3", ".flac", ".aiff", ".aif"];
    api("/settings/master-audio-filter", { method: "POST", body: JSON.stringify({ extensions }) })
      .then((result) => {
        masterAudioExtensions = result.master_audio_extensions || extensions;
        clearDetected();
        return loadInbox();
      })
      .then(() => api("/inbox/analysis/start", { method: "POST" }))
      .then((status) => { inboxAnalysis = status; renderInboxAnalysisStatus(); scheduleInboxAnalysisRefresh(); })
      .catch((error) => showToast(error.message, true));
  }
  if (target.dataset.rescanFolder) {
    api("/inbox/analysis/start", { method: "POST", body: JSON.stringify({ folder: target.dataset.rescanFolder }) })
      .then((status) => { inboxAnalysis = status; renderInboxAnalysisStatus(); scheduleInboxAnalysisRefresh(); })
      .catch((error) => showToast(error.message, true));
  }
  if (target.dataset.removeFolder) {
    const remaining = sourceFolders
      .map((item) => typeof item === "string" ? item : item.path)
      .filter((path) => path !== target.dataset.removeFolder);
    saveSourceFolders(remaining)
      .catch((error) => showToast(error.message, true));
  }
  if (target.id === "confirmFiles") prepareStep2().catch((error) => showToast(error.message, true));
  if (target.id === "newProject") newProject().catch((error) => showToast(error.message, true));
  if (target.id === "refreshProjects") loadProjects().catch((error) => showToast(error.message, true));
  const stepNav = target.closest?.("[data-step-nav]");
  if (stepNav instanceof HTMLElement) {
    const requestedStep = Number(stepNav.dataset.stepNav);
    setStep(requestedStep);
    // Re-entering Review after wizard navigation must restore the persisted
    // cards.  Keeping the old in-memory array made Replace operate on stale
    // frames after a back/forward navigation.
    if (requestedStep === 4 && latestStatus?.status === "waiting_review") {
      document.querySelector("#progressBox")?.setAttribute("hidden", "");
      document.querySelector("#reviewBox")?.removeAttribute("hidden");
      openShotReview().catch((error) => showToast(error.message, true));
    }
  }
  const removeButton = target.closest?.("[data-remove-kind]");
  if (removeButton instanceof HTMLElement) {
    removeDetectedItem(removeButton.dataset.removeKind, removeButton.dataset.removePath);
  }
  const openButton = target.closest?.("[data-open-project]");
  if (openButton instanceof HTMLElement) {
    openProject(openButton.dataset.openProject).catch((error) => showToast(error.message, true));
  }
  const deleteButton = target.closest?.("[data-delete-project]");
  if (deleteButton instanceof HTMLElement) {
    deleteProject(deleteButton.dataset.deleteProject, deleteButton.dataset.name, deleteButton.dataset.hasExport === "1").catch((error) =>
      showToast(error.message, true)
    );
  }
  if (target.classList.contains("platform-card")) {
    choosePlatformInUi(target.dataset.platform);
    if (currentSongs.length > 1) renderSongOptions(currentSongs);
  }
  const flyerPreview = target.closest?.("[data-flyer-preview]");
  if (flyerPreview instanceof HTMLElement) {
    const modal = document.querySelector("#flyerPreviewModal");
    const image = document.querySelector("#flyerPreviewImage");
    if (modal && image) { image.src = flyerPreview.dataset.flyerPreview || ""; modal.hidden = false; }
    return;
  }
  const flyerDelete = target.closest?.("[data-delete-flyer]");
  if (flyerDelete instanceof HTMLElement) {
    deleteFlyerFromLibrary(flyerDelete.dataset.deleteFlyer || "").catch((error) => showToast(error.message, true));
    return;
  }
  const flyer = target.closest?.("[data-add-flyer]");
  if (flyer instanceof HTMLElement) {
    addFlyerReference(flyer.dataset.addFlyer || "", flyer.dataset.flyerUrl || "");
    return;
  }
  const duplicateFlyer = target.closest?.("[data-duplicate-reel-image]");
  if (duplicateFlyer instanceof HTMLElement) {
    const index = Number(duplicateFlyer.dataset.duplicateReelImage);
    if (reelImageOverlays[index]) {
      reelImageOverlays.splice(index + 1, 0, { ...reelImageOverlays[index] });
      renderReelOptions();
    }
    return;
  }
  if (target.id === "closeFlyerPreview" || target.id === "flyerPreviewModal") {
    document.querySelector("#flyerPreviewModal").hidden = true;
    return;
  }
  if (target.classList.contains("song-option")) {
    selectedSong = target.dataset.song === "all" ? "all" : Number(target.dataset.song);
    document.querySelectorAll(".song-option").forEach((button) => button.classList.toggle("selected", button === target));
  }
  if (target.id === "startWizard") startWizard().catch((error) => showToast(error.message, true));
  if (target.id === "setTrimStart" || target.id === "setTrimEnd") {
    const preview = document.querySelector("#masterPreview");
    document.querySelector(target.id === "setTrimStart" ? "#trimStart" : "#trimEnd").value = secondsToTime(preview.currentTime || 0);
  }
  if (target.id === "startAgain" || target.id === "retryWizard" || target.id === "retryWizardSuccess") {
    document.querySelector("#errorBox").hidden = true;
    document.querySelector("#resultBox").hidden = true;
    document.querySelector("#progressTitle").textContent = "Creating your video";
    setStep(3);
    api("/wizard/reset")
      .catch(() => {})
      .then(() => startWizard({ waitForPrepare: false }))
      .catch((error) => showToast(error.message, true));
  }
  if (target.id === "again") {
    currentVariationSeed = `${Date.now()}-${Math.random()}`;
    document.querySelector("#errorBox").hidden = true;
    document.querySelector("#resultBox").hidden = true;
    document.querySelector("#progressTitle").textContent = "Creating your video";
    startWizard({ waitForPrepare: false }).catch((error) => showToast(error.message, true));
  }
  if (target.id === "openLogsSuccess" || target.id === "openLogsError") {
    openLogs().catch((error) => showToast(error.message, true));
  }
  if (target.id === "copyProgressReport" || target.id === "copyProgressReportResult") {
    copyReport().catch((error) => showToast(error.message, true));
  }
  if (target.id === "cancelWizard") cancelWizard();
  if (target.id === "showFinder") {
    if (!latestResult?.path) {
      showToast("The exported file path is not available yet", true);
      return;
    }
    revealNative(latestResult?.path, S.reveal).catch((error) => showToast(error.message, true));
  }
  if (target.id === "openCaptions") openCaptions().catch((error) => showToast(error.message, true));
  if (target.id === "composePlayPause") {
    const video = document.querySelector("#composeVideo");
    if (video) { if (video.paused) video.play().catch(() => {}); else video.pause(); }
  }
  if (target.id === "composeContinue") saveComposition().then(() => {
    document.querySelector("#resultBox").hidden = true;
    document.querySelector("#errorBox").hidden = true;
    progressFloor = 0;
    progressStartedAt = Date.now();
    progressSamples = [];
    document.querySelector("#progressTitle").textContent = "Rendering your final composition";
    setStep(3);
    ensureStatusPolling();
  }).catch((error) => showToast(error.message, true));
  if (target.id === "composeDuplicateOverlay") {
    if (reelImageOverlays.length) reelImageOverlays.push({ ...reelImageOverlays[reelImageOverlays.length - 1], x: Math.min(.95, Number(reelImageOverlays[reelImageOverlays.length - 1].x ?? .5) + .04) });
    else if (reelVideoOverlays.length) reelVideoOverlays.push({ ...reelVideoOverlays[reelVideoOverlays.length - 1], x: Math.min(.95, Number(reelVideoOverlays[reelVideoOverlays.length - 1].x ?? .5) + .04) });
    renderReelOptions(); renderComposeOverlayLayer();
  }
  if (target.id === "composeRemoveLogo") removeProjectLogo().catch((error) => showToast(error.message, true));
  if (target.id === "captionExportSrt") exportCaptionSrt();
  if (target.id === "burnCaptions") burnCaptionTrack().catch((error) => showToast(error.message, true));
  if (target.id === "result360Play") toggleResult360Play();
  if (target.id === "statusStrip") setStep(3);
  const rescueButton = target.closest?.("[data-rescue]");
  if (rescueButton instanceof HTMLElement) {
    openRescue(rescueButton.dataset.rescue, rescueButton.dataset.offset).catch((error) => showToast(error.message, true));
  }
  if (target.closest?.("#confirmRescue")) {
    confirmRescue().catch((error) => showToast(error.message, true));
  }
});

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.id === "personalLogoInput") {
    savePersonalLogo(target.files?.[0]).catch((error) => showToast(error.message, true));
  }
  if (target instanceof HTMLInputElement && target.dataset.paperSubtitle) {
    api("/wizard/paper-edit/text", { method: "POST", body: JSON.stringify({ id: target.dataset.paperSubtitle, text: target.value }) })
      .then((paper) => { renderPaperEdit(paper); })
      .catch((error) => showToast(error.message, true));
  }
  if (target instanceof HTMLSelectElement && target.dataset.paperMark) {
    api("/wizard/paper-edit/mark", { method: "POST", body: JSON.stringify({ id: target.dataset.paperMark, mark: target.value }) })
      .then((paper) => { renderPaperEdit(paper); })
      .catch((error) => showToast(error.message, true));
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    const modal = document.querySelector("#reviewLargeModal");
    if (modal && !modal.hidden) modal.hidden = true;
  }
  if (event.key === "Enter" && event.target instanceof HTMLElement && event.target.dataset.captionText != null) {
    event.preventDefault();
    document.querySelector("#composeVideo")?.play().catch(() => {});
    document.querySelector("#composeAddCaption")?.focus();
    return;
  }
  const commandKey = event.metaKey || event.ctrlKey;
  const isTextEditingTarget = (target) => {
    if (!(target instanceof HTMLElement)) return false;
    return target instanceof HTMLInputElement
      || target instanceof HTMLTextAreaElement
      || target instanceof HTMLSelectElement
      || target.isContentEditable
      || Boolean(target.closest("[contenteditable=\"true\"]"));
  };
  const editingField = isTextEditingTarget(event.target) || isTextEditingTarget(document.activeElement);
  if (commandKey && event.key.toLowerCase() === "c" && captionActiveIndex != null && captionCues[captionActiveIndex]) {
    copiedCaption = { ...captionCues[captionActiveIndex], lines: [...captionCues[captionActiveIndex].lines], style_override: { ...(captionCues[captionActiveIndex].style_override || {}) } };
    event.preventDefault();
    return;
  }
  if (commandKey && event.key.toLowerCase() === "v" && copiedCaption) {
    const now = Number(document.querySelector("#composeVideo")?.currentTime || 0);
    const duration = Math.max(0.1, Number(copiedCaption.end) - Number(copiedCaption.start));
    captionCues.push({ ...copiedCaption, start: now, end: now + duration, lines: [...copiedCaption.lines], style_override: { ...(copiedCaption.style_override || {}) } });
    captionActiveIndex = captionCues.length - 1;
    syncCaptionTextArea(); renderCaptionBlocks(); renderComposeOverlayLayer();
    event.preventDefault();
    return;
  }
  if (commandKey && event.key.toLowerCase() === "c" && selectedComposeOverlay && !editingField) {
    const source = selectedComposeOverlay.kind === "image" ? reelImageOverlays[selectedComposeOverlay.index] : reelVideoOverlays[selectedComposeOverlay.index];
    if (source) { copiedComposeOverlay = { kind: selectedComposeOverlay.kind, item: { ...source } }; event.preventDefault(); }
    return;
  }
  if (commandKey && event.key.toLowerCase() === "v" && copiedComposeOverlay && !editingField) {
    const item = { ...copiedComposeOverlay.item, start_sec: Number(document.querySelector("#composeVideo")?.currentTime || 0) };
    if (copiedComposeOverlay.kind === "image") { reelImageOverlays.push(item); selectedComposeOverlay = { kind: "image", index: reelImageOverlays.length - 1 }; }
    else { reelVideoOverlays.push(item); selectedComposeOverlay = { kind: "video", index: reelVideoOverlays.length - 1 }; }
    renderReelOptions();
    event.preventDefault();
    return;
  }
  if ((event.key === "a" || event.key === "A") && currentStep === 5 && !editingField) {
    event.preventDefault(); addCaptionFromPlayback();
  }
  if (event.code === "Space" && currentStep === 5 && !editingField) {
    const video = document.querySelector("#composeVideo");
    if (video && !video.paused && !video.ended) {
      event.preventDefault(); addCaptionFromPlayback();
    }
  }
});

document.addEventListener("change", (event) => {
  const input = event.target;
  if (input instanceof HTMLSelectElement && (input.id === "reelAspect" || input.id === "reelMixVerticalRatio")) {
    renderReelOptions();
    if (input.id === "reelAspect") drawReelPreview();
    return;
  }
  if (!(input instanceof HTMLInputElement) || input.id !== "captionImport" || !input.files?.[0]) return;
  const source = input.files[0].name.toLowerCase().endsWith(".lrc") ? "lrc" : "srt";
  input.files[0].text().then((text) => api("/captions/parse", { method: "POST", body: JSON.stringify({ source, text }) })).then((parsed) => {
    captionCues = parsed.cues || []; captionMarkIndex = captionCues.length; captionPendingStart = null; captionActiveIndex = null;
    document.querySelector("#captionText").value = captionCues.map((cue) => cue.lines.join("\n")).join("\n\n"); renderCaptionBlocks();
  }).catch((error) => showToast(error.message, true));
});

document.addEventListener("input", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLElement)) return;
  const rangeOutput = input.closest("label")?.querySelector("output");
  if (rangeOutput && input.type === "range") {
    const percent = ["opacity", "shadowOpacity", "glowIntensity"].some((part) => Object.keys(input.dataset).some((key) => key.toLowerCase().includes(part.toLowerCase())));
    rangeOutput.textContent = percent ? `${Math.round(Number(input.value) * 100)}%` : input.value;
  }
  if (input.id === "captionStyle") { input.dataset.userChoice = "1"; renderComposeOverlayLayer(); return; }
  if (input.id === "reelDuration" || input.id === "reelAspect" || input.id === "reelMixVerticalRatio") { renderReelOptions(); return; }
  if (input.id === "composeTimelineZoom") { renderComposeTimeline(); return; }
  if (input.id === "backstageDuration") { const value = document.querySelector("#backstageDurationValue"); if (value) value.textContent = `${input.value}s`; return; }
  if (input.id === "reelPlayhead") { reelPlayhead = Number(input.value); document.querySelector("#reelPlayheadValue").textContent = `${reelPlayhead.toFixed(1)}s`; drawReelPreview(); return; }
  if (input.dataset.captionText != null) { const cue = captionCues[Number(input.dataset.captionText)]; if (cue) { cue.lines = input.value.split(/\r?\n/); syncCaptionTextArea(); renderComposeOverlayLayer(); } return; }
  if (input.dataset.captionStart != null || input.dataset.captionEnd != null || input.dataset.captionDuration != null) { const index = Number(input.dataset.captionStart ?? input.dataset.captionEnd ?? input.dataset.captionDuration); const cue = captionCues[index]; if (cue) { if (input.dataset.captionStart != null) cue.start = Math.max(0, Number(input.value) || 0); else if (input.dataset.captionEnd != null) cue.end = Math.max(Number(cue.start) + 0.05, Number(input.value) || 0); else cue.end = Number(cue.start) + Math.max(0.05, Number(input.value) || 0.05); renderComposeTimeline(); renderComposeOverlayLayer(); } return; }
  const captionStyleFields = {
    captionColor: "color", captionSize: "size", captionVertical: "vertical",
    captionOutlineColor: "outline_color", captionOutlineWidth: "outline_width",
    captionShadowDistance: "shadow_distance", captionShadowOpacity: "shadow_opacity",
    captionGlowColor: "glow_color", captionGlowBlur: "glow_blur",
    captionGlowLayers: "glow_layers", captionGlowIntensity: "glow_intensity",
    captionAnimationIn: "animation_in", captionAnimationOut: "animation_out",
  };
  const captionStyleKey = Object.keys(captionStyleFields).find((name) => input.dataset[name] != null);
  if (captionStyleKey) {
    const index = Number(input.dataset[captionStyleKey]);
    const cue = captionCues[index];
    if (cue) {
      cue.style_override ||= {};
      const key = captionStyleFields[captionStyleKey];
      if (input.value === "") delete cue.style_override[key];
      else cue.style_override[key] = ["size", "vertical", "outline_width", "shadow_distance", "shadow_opacity", "glow_blur", "glow_layers", "glow_intensity"].includes(key) ? Number(input.value) : input.value;
      renderComposeOverlayLayer();
    }
    return;
  }
  if (input.id === "captionText") { const blocks = captionBlocksFromText(); captionCues = blocks.map((text, index) => ({ lines: text.split(/\r?\n/), start: captionCues[index]?.start ?? index * 4, end: captionCues[index]?.end ?? index * 4 + 4 })); captionMarkIndex = Math.min(captionMarkIndex, captionCues.length); renderCaptionBlocks(); return; }
  const row = input.closest?.("[data-reel-text-index]");
  if (!row || !input.dataset.reelField) return;
  const index = Number(row.dataset.reelTextIndex);
  const item = reelTextOverlays[index];
  if (!item) return;
  const field = input.dataset.reelField;
  item[field] = ["size", "start_sec", "duration_sec", "opacity", "outline_width", "shadow_blur", "background_opacity", "background_radius", "shadow_offset_x", "shadow_offset_y"].includes(field) ? Number(input.value) : input.value;
  renderReelTimeline();
  drawReelPreview(); renderComposeOverlayLayer();
});

document.addEventListener("click", (event) => {
  const target = event.target;
  if (target?.id === "composeFrameBack" || target?.id === "composeFrameForward") {
    const video = document.querySelector("#composeVideo"); if (!video) return;
    video.pause(); video.currentTime = Math.max(0, Number(video.currentTime || 0) + (target.id === "composeFrameForward" ? 1 / 30 : -1 / 30)); renderComposeOverlayLayer();
  }
  if (target?.id === "composeScrub") return;
});
document.addEventListener("input", (event) => {
  if (event.target?.id === "composeScrub") { const video = document.querySelector("#composeVideo"); if (video) { video.currentTime = Number(event.target.value); renderComposeOverlayLayer(); } }
});

document.addEventListener("input", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLElement) || !input.dataset.reelVideoField) return;
  const output = input.closest("label")?.querySelector("output");
  if (output) output.textContent = ["opacity", "width"].includes(input.dataset.reelVideoField) ? `${Math.round(Number(input.value) * 100)}%` : input.value;
  const row = input.closest?.("[data-reel-video-index]"); if (!row) return;
  const item = reelVideoOverlays[Number(row.dataset.reelVideoIndex)]; if (!item) return;
  const field = input.dataset.reelVideoField;
  item[field] = ["width", "opacity", "start_sec", "duration_sec"].includes(field) ? Number(input.value) : input.value;
  renderComposeTimeline(); renderComposeOverlayLayer();
});

document.addEventListener("input", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLElement) || !input.dataset.reelImageField) return;
  const output = input.closest("label")?.querySelector("output");
  if (output) output.textContent = ["opacity", "width"].includes(input.dataset.reelImageField) ? `${Math.round(Number(input.value) * 100)}%` : input.value;
  const row = input.closest?.("[data-reel-image-index]"); if (!row) return;
  const item = reelImageOverlays[Number(row.dataset.reelImageIndex)]; if (!item) return;
  item[input.dataset.reelImageField] = ["width", "opacity", "start_sec", "duration_sec", "tint_opacity", "shadow_distance", "shadow_blur", "shadow_opacity", "glow_blur", "glow_layers"].includes(input.dataset.reelImageField) ? Number(input.value) : input.value;
  renderReelTimeline(); drawReelPreview(); renderComposeOverlayLayer();
});

document.addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLElement)) return;
  if (input.name === "captionLogoChoice") { renderProjectLogo(); renderComposeOverlayLayer(); return; }
  if (input.id === "composeLogoInput" && input.files?.[0]) {
    uploadProjectLogo(input.files[0]).catch((error) => showToast(error.message, true));
    return;
  }
  if (input.dataset.captionAnimationIn != null || input.dataset.captionAnimationOut != null) {
    const index = Number(input.dataset.captionAnimationIn ?? input.dataset.captionAnimationOut);
    const cue = captionCues[index];
    if (cue) {
      cue.style_override ||= {};
      cue.style_override[input.dataset.captionAnimationIn != null ? "animation_in" : "animation_out"] = input.value;
      renderComposeOverlayLayer();
    }
    return;
  }
  if (input.id === "reelAspect") drawReelPreview();
  if (input.id === "addReelImage" && input.files?.[0]) {
    const form = new FormData(); form.append("file", input.files[0]);
    api("/wizard/reel-overlay", { method: "POST", body: form, headers: {} }).then((result) => {
      reelImageOverlays.push({ path: result.path, preview_url: result.url || result.path, x: 0.5, y: 0.5, width: 0.35, opacity: 1, animation: "fade", start_sec: 0, duration_sec: 3 });
      renderReelOptions();
    }).catch((error) => showToast(error.message, true));
  }
  if ((input.id === "addReelVideo" || input.id === "composeAddReelVideo") && input.files?.[0]) {
    const form = new FormData(); form.append("file", input.files[0]);
    api("/wizard/reel-video-overlay", { method: "POST", body: form, headers: {} }).then((result) => {
      reelVideoOverlays.push({ path: result.path, preview_url: result.url || result.path, x: 0.5, y: 0.5, width: 0.35, opacity: 1, animation: "fade", start_sec: Number(document.querySelector("#composeVideo")?.currentTime || 0), duration_sec: 3 });
      renderReelOptions(); renderComposeTimeline(); renderComposeOverlayLayer();
    }).catch((error) => showToast(error.message, true));
  }
  if (input.dataset.reviewKeep != null) {
    const item = shotReviewItems.find((entry) => Number(entry.index) === Number(input.dataset.reviewKeep));
    if (item) { item.keep = input.checked; document.querySelector(`[data-review-index="${item.index}"]`)?.classList.toggle("reject", !item.keep); }
  }
});

// Direct manipulation in the live, assembled preview. The preview is the
// spatial source of truth for x/y/width; the lower timeline remains the only
// temporal source of truth.
document.addEventListener("pointerdown", (event) => {
  const layer = event.target.closest?.("#composeOverlayLayer");
  const stage = event.target.closest?.("#composeVideoStage");
  if (!(stage instanceof HTMLElement)) return;
  if (!(layer instanceof HTMLElement)) {
    selectedComposeOverlay = null;
    selectedComposeLogo = false;
    captionActiveIndex = null;
    renderReelOptions();
    renderComposeOverlayLayer();
    return;
  }
  const overlay = event.target.closest?.("[data-compose-preview-kind]");
  const video = document.querySelector("#composeVideo");
  if (!(video instanceof HTMLVideoElement)) return;
  video.pause();
  if (!(overlay instanceof HTMLElement)) {
    selectedComposeOverlay = null;
    selectedComposeLogo = false;
    captionActiveIndex = null;
    renderReelOptions();
    renderComposeOverlayLayer();
    return;
  }
  const kind = overlay.dataset.composePreviewKind;
  const index = Number(overlay.dataset.composePreviewIndex);
  const item = kind === "logo" ? (projectLogo.overlay ||= { x: .5, y: .08, width: .22 }) : kind === "image" ? reelImageOverlays[index] : reelVideoOverlays[index];
  if (!item) return;
  selectedComposeLogo = kind === "logo";
  selectedComposeOverlay = kind === "logo" ? null : { kind, index };
  captionActiveIndex = null;
  const rect = layer.getBoundingClientRect();
  const handle = event.target.closest?.("[data-compose-resize]");
  composePreviewDrag = { kind, index, item, mode: handle?.dataset.composeResize || "move", rect, startX: event.clientX, startY: event.clientY, x: Number(item.x ?? .5), y: Number(item.y ?? .5), width: Number(item.width ?? .35) };
  layer.setPointerCapture?.(event.pointerId);
  renderReelOptions();
  renderComposeOverlayLayer();
  event.preventDefault();
});

document.addEventListener("pointermove", (event) => {
  if (!composePreviewDrag) return;
  const drag = composePreviewDrag;
  const dx = (event.clientX - drag.startX) / Math.max(1, drag.rect.width);
  const dy = (event.clientY - drag.startY) / Math.max(1, drag.rect.height);
  if (drag.mode === "move") {
    drag.item.x = Math.max(0.03, Math.min(0.97, drag.x + dx));
    drag.item.y = Math.max(0.03, Math.min(0.97, drag.y + dy));
  } else {
    const sign = drag.mode.includes("w") ? -1 : 1;
    drag.item.width = Math.max(0.05, Math.min(0.9, drag.width + sign * dx * 2));
  }
  renderReelOptions();
  renderComposeOverlayLayer();
});

document.addEventListener("pointerup", () => {
  if (composePreviewDrag) {
    composePreviewDrag = null;
    renderReelOptions();
    renderComposeOverlayLayer();
  }
});

document.addEventListener("pointerdown", (event) => {
  const canvas = document.querySelector("#reelPreview");
  if (!(canvas && event.target === canvas)) return;
  const rect = canvas.getBoundingClientRect();
  const x = (event.clientX - rect.left) / rect.width, y = (event.clientY - rect.top) / rect.height;
  const visible = [...reelTextOverlays.map((item) => ({item, _kind: "text"})), ...reelImageOverlays.map((item) => ({item, _kind: "image"}))].filter(({item}) => reelPlayhead >= Number(item.start_sec) && reelPlayhead <= Number(item.start_sec) + Number(item.duration_sec));
  if (!visible.length) return;
  let best = visible[visible.length - 1];
  let bestDistance = Infinity;
  for (const candidate of visible) { const item = candidate.item; const distance = Math.hypot((Number(item.x ?? 0.5) - x), (Number(item.y ?? 0.5) - y)); if (distance < bestDistance) { best = candidate; bestDistance = distance; } }
  const resizing = best._kind === "image" && Math.abs(x - (Number(best.item.x ?? .5) + Number(best.item.width ?? .35) / 2)) < 0.08 && Math.abs(y - Number(best.item.y ?? .5)) < Number(best.item.width ?? .35) * 0.7;
  reelDrag = { item: best.item, kind: best._kind, resizing, pointerId: event.pointerId };
  canvas.setPointerCapture(event.pointerId);
});
document.addEventListener("pointermove", (event) => {
  if (!reelDrag) return;
  const canvas = document.querySelector("#reelPreview"), rect = canvas.getBoundingClientRect();
  const x = (event.clientX - rect.left) / rect.width, y = (event.clientY - rect.top) / rect.height;
  if (reelDrag.kind === "image" && reelDrag.resizing) reelDrag.item.width = Math.max(0.05, Math.min(0.9, Math.abs(x - Number(reelDrag.item.x ?? .5)) * 2));
  else { reelDrag.item.x = Math.max(0.02, Math.min(0.98, x)); reelDrag.item.y = Math.max(0.02, Math.min(0.98, y)); }
  drawReelPreview(); renderComposeOverlayLayer();
});
document.addEventListener("pointerup", () => { if (reelDrag) { reelDrag = null; renderReelTimeline(); renderComposeOverlayLayer(); } });

document.addEventListener("pointerdown", (event) => {
  const block = event.target.closest?.(".compose-timeline-block.overlay");
  if (!(block instanceof HTMLElement)) return;
  const kind = block.dataset.overlayKind;
  const index = Number(block.dataset.overlayIndex);
  const item = kind === "image" ? reelImageOverlays[index] : reelVideoOverlays[index];
  if (!item) return;
  selectedComposeOverlay = { kind, index };
  captionActiveIndex = null;
  block.focus();
  const handle = event.target.closest?.("[data-timeline-resize]");
  timelineDrag = { kind, index, mode: handle?.dataset.timelineResize || "move", startX: event.clientX, startStart: Number(item.start_sec || 0), startDuration: Number(item.duration_sec || 0.1), moved: false };
  event.preventDefault();
  renderComposeTimeline();
});

document.addEventListener("pointermove", (event) => {
  if (!timelineDrag) return;
  const item = timelineDrag.kind === "image" ? reelImageOverlays[timelineDrag.index] : reelVideoOverlays[timelineDrag.index];
  const scroll = document.querySelector("[data-compose-timeline] .compose-lane");
  if (!item || !scroll) return;
  const delta = (event.clientX - timelineDrag.startX) / Math.max(1, scroll.scrollWidth) * composeTimelineDuration();
  if (Math.abs(delta) > 1) timelineDrag.moved = true;
  if (timelineDrag.mode === "left") {
    item.start_sec = Math.max(0, Math.min(timelineDrag.startStart + timelineDrag.startDuration - 0.1, timelineDrag.startStart + delta));
    item.duration_sec = Math.max(0.1, timelineDrag.startDuration - (item.start_sec - timelineDrag.startStart));
  } else if (timelineDrag.mode === "right") {
    item.duration_sec = Math.max(0.1, timelineDrag.startDuration + delta);
  } else {
    item.start_sec = Math.max(0, timelineDrag.startStart + delta);
  }
  renderComposeTimeline();
  drawReelPreview();
  renderComposeOverlayLayer();
});

document.addEventListener("pointerup", () => {
  if (!timelineDrag) return;
  timelineSuppressClick = timelineDrag.moved;
  timelineDrag = null;
  renderReelTimeline();
  renderComposeTimeline();
});

document.addEventListener("toggle", (event) => {
  const target = event.target;
  if (target instanceof HTMLDetailsElement && target.id === "progressDetails" && target.open) {
    refreshProgressReport().then(() => {
      const report = document.querySelector("#progressReport");
      if (report) report.scrollTop = report.scrollHeight;
    }).catch((error) => showToast(error.message, true));
  }
}, true);

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement && target.id === "inboxMasterSelect") {
    selectedMasterPath = target.value || null;
    trimDefaultsAppliedFor = "";
    setupTrimControls(selectedMasterPath);
    detected.videos = [...detected.videos, ...analysisSetAsideVideos, ...setAsideVideos];
    analysisSetAsideVideos = [];
    setAsideVideos = [];
    sessionFilterDisabled = false;
    renderInboxAnalysisStatus();
    renderChips();
  }
  if (target instanceof HTMLSelectElement && target.id === "masterSelect") {
    selectedMasterPath = target.value;
    trimDefaultsAppliedFor = "";
    setupTrimControls(selectedMasterPath);
    // Put previously set-aside clips back in the pool so the newly chosen
    // song's own session decides which clips are relevant, rather than
    // inheriting the previous song's filtering.
    detected.videos = [...detected.videos, ...setAsideVideos];
    setAsideVideos = [];
    renderInboxAnalysisStatus();
    renderChips();
  }
});

document.querySelector("#result360Scrub").addEventListener("input", (event) => {
  seekResult360(Number(event.target.value) || 0);
});

document.querySelector("#videoName").value = todayName();

async function boot() {
  injectIcons();
  auditBackdropRuntime();
  await loadAppConfig();
  restoreSelectedPlatform();
  const status = await api("/wizard/status");
  activeProjectId = projectIdFromStatus(status);
  if (["running", "waiting_choice", "waiting_paper_edit", "done", "failed"].includes(status.status)) {
    await resumeInputsFromProject().catch(() => {});
  }
  await loadProjects().catch(() => {});
  renderWizardStatus(status);
  if (status.status === "running") {
    setStep(3);
    ensureStatusPolling();
  } else if (status.status === "waiting_choice") {
    setStep(2);
  } else if (status.status === "waiting_paper_edit") {
    setStep(4);
    document.querySelector("#paperEditBox")?.removeAttribute("hidden");
    openPaperEdit().catch((error) => showToast(error.message, true));
  } else if (status.status === "done") {
    setStep(5);
    openCaptions().catch((error) => showToast(error.message, true));
  } else if (status.status === "failed") {
    setStep(6);
  }
}

boot().catch((error) => showToast(error.message, true));
