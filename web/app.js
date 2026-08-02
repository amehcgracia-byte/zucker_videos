const detected = { master: [], songs: [], videos: [], ignored: [] };
const S = window.UI_STRINGS || {};
let selectedPlatform = null;
let selectedSong = null;
let selectedMasterPath = null;
// Inbox clips set aside because they belong to a different session than the
// chosen song. Kept (not discarded) so "Show all clips" can restore them.
let setAsideVideos = [];
let sessionFilterDisabled = false;
let currentSongs = [];
let latestResult = null;
let latestStatus = null;
let pollTimer = null;
let appConfig = { dev: true, desktop: false };
let progressStartedAt = null;
let progressSamples = [];
let progressFloor = 0;
let etaSmoothedSeconds = null;
let rescueClipId = null;
let currentStep = 1;
let lastProgressReportAt = 0;
let trimDefaultsAppliedFor = "";
let lastSphericalSetup = {};
let cameraRoleWeights = { "360": 50, handheld: 30, fixed_rear: 20 };
let fixedRearMotion = true;
let reelTextOverlays = [];
let reelImageOverlays = [];
let reelPlayhead = 0;
let reelDrag = null;
const reelPreviewImages = new Map();
let sphericalMode = "automatic";
const MAX_RECORDED_YAW_RATE_DEG_PER_SEC = 40;
let savedAudioTrim = {};
let previewTimers = new Map();
let previewVersions = new Map();
let previewControllers = new Map();
let director = {
  ready: false,
  loading: false,
  media: null,
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
  recording: false,
  recordTimer: null,
  samples: [],
  smoothedSamples: [],
  previewingTake: false,
  animation: null,
};

// Result screen's read-only 360 viewer -- same sphere-mapping approach as
// the Director (drag to look around), but no recording/take machinery: the
// exported file already has its audio embedded and needs no separate sync.
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
  lastSphericalSetup = normalizeSphericalSetup(appConfig.spherical_landmarks || {});
  cameraRoleWeights = normalizeCameraRoleWeights(appConfig.camera_role_weights || cameraRoleWeights);
  applyCameraRoleWeights(cameraRoleWeights);
  fixedRearMotion = appConfig.fixed_rear_motion !== false;
  applyFixedRearMotion(fixedRearMotion);
  applySphericalMotion(appConfig.spherical_motion === true);
  sphericalMode = appConfig.spherical_mode === "directed" ? "directed" : "automatic";
  applySphericalMode(sphericalMode);
  applySphericalSweep(appConfig.spherical_sweep !== false, appConfig.sweep_speed_deg_per_sec || 60);
  savedAudioTrim = appConfig.audio_trim_by_master || {};
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
  currentStep = Math.max(1, Math.min(4, Number(number) || 1));
  document.querySelectorAll(".step").forEach((step, index) => step.classList.toggle("active", index === currentStep - 1));
  document.querySelectorAll("[data-step-nav]").forEach((button) => button.classList.toggle("active", Number(button.dataset.stepNav) === currentStep));
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
  for (const key of ["master", "songs", "videos", "ignored"]) {
    const existing = new Set(detected[key].map((item) => item.path));
    for (const item of result[key] || []) {
      if (existing.has(item.path)) continue;
      detected[key].push({ ...item, source });
    }
  }
  chooseDefaultMaster();
  renderChips();
}

function clearDetected() {
  for (const key of ["master", "songs", "videos", "ignored"]) detected[key] = [];
  selectedMasterPath = null;
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
  const inputs = project.inputs || {};
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
  if (project.settings?.spherical_landmarks) {
    lastSphericalSetup = normalizeSphericalSetup(project.settings.spherical_landmarks);
  }
  if (project.settings?.edit?.camera_role_weights) {
    cameraRoleWeights = normalizeCameraRoleWeights(project.settings.edit.camera_role_weights);
    applyCameraRoleWeights(cameraRoleWeights);
  }
  if (project.settings?.edit && "fixed_rear_motion" in project.settings.edit) {
    fixedRearMotion = project.settings.edit.fixed_rear_motion !== false;
    applyFixedRearMotion(fixedRearMotion);
  }
  if (project.settings?.edit && "spherical_motion" in project.settings.edit) {
    applySphericalMotion(project.settings.edit.spherical_motion === true);
  }
  if (project.settings?.edit?.spherical_mode) {
    applySphericalMode(project.settings.edit.spherical_mode);
  }
  if (project.settings?.edit) {
    applySphericalSweep(project.settings.edit.spherical_sweep !== false, project.settings.edit.sweep_speed_deg_per_sec || 60);
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
  applySessionFilter();
  const items = [...detected.videos, ...detected.master, ...detected.songs, ...detected.ignored];
  root.innerHTML = items
    .map(
      (item) => `
        <span class="chip ${item.kind === "ignored" ? "muted" : ""} ${isHelpfulWarning(item) ? "warning" : ""} ${isRaw360(item) ? "info" : ""}" title="${escapeHtml(
        item.path
      )}">
          ${iconFor(item)} ${escapeHtml(item.filename || filename(item.path))}
          ${isSphericalVideo(item) ? "<small>360°</small>" : ""}
          ${item.source === "inbox" ? "<small>from Inbox</small>" : ""}
          ${isRaw360(item) ? `<small>${escapeHtml(item.info || "360 stitched automatically")}</small>` : ""}
          ${item.kind === "ignored" ? `<small>${escapeHtml(item.note || S.ignored)}</small>` : ""}
          <button class="chip-remove" data-remove-kind="${escapeHtml(item.kind)}" data-remove-path="${escapeHtml(item.path)}" aria-label="Remove ${escapeHtml(
        item.filename || filename(item.path)
      )}">×</button>
        </span>`
    )
    .join("");
  if (detected.master.length > 1) {
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
  button.disabled = !(hasVideo && hasMaster);
  if (!hasVideo) note.textContent = S.missingVideo;
  else if (!hasMaster) note.textContent = S.missingMaster;
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
  const status = await api("/wizard/projects/open", { method: "POST", body: JSON.stringify({ path }) });
  await resumeInputsFromProject().catch(() => {});
  renderWizardStatus(status);
  if (status.status === "running") {
    setStep(3);
    ensureStatusPolling();
  } else if (status.status === "done" || status.status === "failed") {
    setStep(4);
  } else if (status.status === "waiting_choice") {
    setStep(2);
  } else {
    setStep(1);
  }
}

async function newProject() {
  if (latestStatus?.status === "running" && !confirm("A job is still running for the current project. Start a new project view anyway?")) return;
  await api("/wizard/projects/new", { method: "POST", body: JSON.stringify({}) });
  clearInterval(pollTimer);
  pollTimer = null;
  latestStatus = null;
  latestResult = null;
  progressFloor = 0;
  progressStartedAt = null;
  progressSamples = [];
  selectedPlatform = null;
  selectedSong = null;
  currentSongs = [];
  clearDetected();
  document.querySelector("#videoName").value = todayName();
  document.querySelector("#errorBox").hidden = true;
  document.querySelector("#resultBox").hidden = true;
  document.querySelector("#progressTitle").textContent = "Creating your video";
  document.querySelector("#startWizard").disabled = true;
  document.querySelectorAll(".platform-card").forEach((card) => card.classList.remove("selected"));
  await loadInbox().catch(() => {});
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
  if (!inputs.master || !inputs.videos.length) return;
  setStep(2);
  setupTrimControls(inputs.master);
  applyEditTypeMode();
  if (inputs.songs) {
    const result = await api("/wizard/songs", { method: "POST", body: JSON.stringify({ songs: inputs.songs }) });
    renderSongOptions(result.songs || []);
  } else {
    renderSongOptions([]);
  }
}

function applyEditTypeMode() {
  const passthrough360 = selectedPlatform === "360";
  const cameraMix = document.querySelector("#cameraMix");
  const sphericalSetup = document.querySelector("#sphericalSetup");
  const songPicker = document.querySelector("#songPicker");
  const reelOptions = document.querySelector("#reelOptions");
  if (cameraMix) cameraMix.hidden = passthrough360;
  if (sphericalSetup) {
    sphericalSetup.hidden = passthrough360;
    if (passthrough360) sphericalSetup.open = false;
  }
  if (songPicker && passthrough360) songPicker.hidden = true;
  if (reelOptions) {
    reelOptions.hidden = selectedPlatform !== "reel";
    if (selectedPlatform !== "reel") reelOptions.open = false;
    if (selectedPlatform === "reel") renderReelOptions();
  }
}

function renderReelOptions() {
  const root = document.querySelector("#reelTextLines");
  if (!root) return;
  const duration = Number(document.querySelector("#reelDuration")?.value || 30);
  const value = document.querySelector("#reelDurationValue");
  if (value) value.textContent = `${duration}s`;
  const playhead = document.querySelector("#reelPlayhead");
  if (playhead) { playhead.max = String(duration); playhead.value = String(Math.min(duration, reelPlayhead)); reelPlayhead = Number(playhead.value); }
  root.innerHTML = reelTextOverlays.map((item, index) => `<div class="reel-text-line" data-reel-text-index="${index}">
    <input data-reel-field="text" placeholder="Text" value="${escapeHtml(item.text)}" />
    <input data-reel-field="color" type="color" value="${item.color}" title="Colour" />
    <label>Size <input data-reel-field="size" type="number" min="18" max="160" value="${item.size}" /></label>
    <label>Font <select data-reel-field="font"><option value="bundled" ${item.font === "bundled" ? "selected" : ""}>Verdana Bold</option><option value="arial" ${item.font === "arial" ? "selected" : ""}>Arial</option></select></label>
    <label>Weight <select data-reel-field="font_weight"><option value="normal" ${item.font_weight === "normal" ? "selected" : ""}>Normal</option><option value="bold" ${item.font_weight !== "normal" ? "selected" : ""}>Bold</option></select></label>
    <label>Opacity <input data-reel-field="opacity" type="range" min="0.05" max="1" step="0.05" value="${item.opacity ?? 1}" /></label>
    <label>Outline <input data-reel-field="outline_color" type="color" value="${item.outline_color || "#000000"}" /> <input data-reel-field="outline_width" type="number" min="0" max="12" value="${item.outline_width ?? 2}" /></label>
    <label>Shadow <input data-reel-field="shadow_color" type="color" value="${item.shadow_color || "#000000"}" /> <input data-reel-field="shadow_blur" type="number" min="0" max="30" value="${item.shadow_blur ?? 4}" /></label>
    <label>Box <input data-reel-field="background_color" type="color" value="${item.background_color || "#000000"}" /> <input data-reel-field="background_opacity" type="number" min="0" max="1" step="0.05" value="${item.background_opacity ?? 0}" /></label>
    <label>Style <select data-reel-field="animation"><option value="none" ${item.animation === "none" ? "selected" : ""}>None</option><option value="fade" ${item.animation !== "none" ? "selected" : ""}>Fade</option><option value="slide" ${item.animation === "slide" ? "selected" : ""}>Slide</option><option value="scale" ${item.animation === "scale" ? "selected" : ""}>Scale</option></select></label>
    <label>Start <input data-reel-field="start_sec" type="number" min="0" max="60" step="0.1" value="${item.start_sec}" /></label>
    <label>Duration <input data-reel-field="duration_sec" type="number" min="0.1" max="60" step="0.1" value="${item.duration_sec}" /></label>
    <button type="button" data-remove-reel-text="${index}">Remove</button>
  </div>`).join("");
  const imageRoot = document.querySelector("#reelImageLines");
  if (imageRoot) imageRoot.innerHTML = reelImageOverlays.map((item, index) => `<div class="reel-text-line" data-reel-image-index="${index}"><span>Flyer ${index + 1}</span><label>Width <input data-reel-image-field="width" type="number" min="0.05" max="1" step="0.01" value="${item.width ?? .35}" /></label><label>Opacity <input data-reel-image-field="opacity" type="number" min="0.05" max="1" step="0.05" value="${item.opacity ?? 1}" /></label><label>Start <input data-reel-image-field="start_sec" type="number" min="0" max="60" step="0.1" value="${item.start_sec ?? 0}" /></label><label>Duration <input data-reel-image-field="duration_sec" type="number" min="0.1" max="60" step="0.1" value="${item.duration_sec ?? 3}" /></label><button type="button" data-remove-reel-image="${index}">Remove</button></div>`).join("");
  renderReelTimeline();
  drawReelPreview();
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
  return { duration: Number(document.querySelector("#reelDuration")?.value || 30), aspect: document.querySelector("#reelAspect")?.value || "9:16", texts: reelTextOverlays, images: reelImageOverlays };
}

function renderSphericalSetup() {
  const panel = document.querySelector("#sphericalSetup");
  if (!panel) return;
  panel.hidden = !hasSphericalInput();
  if (!panel.dataset.initialized) {
    panel.open = false;
    panel.dataset.initialized = "1";
  }
  if (!panel.hidden) {
    applySphericalSetup(lastSphericalSetup);
    refreshDirectorEntryStatus().catch(() => {});
  }
}

function sphericalModeFromForm() {
  return document.querySelector('input[name="sphericalMode"]:checked')?.value === "directed" ? "directed" : "automatic";
}

function applySphericalMode(mode) {
  sphericalMode = mode === "directed" ? "directed" : "automatic";
  const input = document.querySelector(`input[name="sphericalMode"][value="${sphericalMode}"]`);
  if (input) input.checked = true;
  document.querySelector("#directorEntry")?.classList.toggle("directed", sphericalMode === "directed");
  refreshDirectorEntryStatus().catch(() => {});
}

async function refreshDirectorEntryStatus() {
  const status = document.querySelector("#directorEntryStatus");
  if (!status || !hasSphericalInput()) return;
  try {
    const result = await api("/wizard/camera-moves");
    const takes = result.takes || [];
    const count = takes.length;
    const brief = takes
      .slice(0, 2)
      .map((take) => `${take.name || "Take"} ${secondsToTime(take.start_master_sec || 0)}-${secondsToTime(take.end_master_sec || 0)}`)
      .join("; ");
    status.textContent =
      sphericalModeFromForm() === "directed"
        ? count
          ? `Directed mode active. ${count} take${count === 1 ? "" : "s"} available: ${brief}${count > 2 ? "..." : ""}`
          : "No recorded take yet. Open Director to record one before exporting."
        : count
          ? `Automatic mode active. ${count} recorded take${count === 1 ? "" : "s"} saved (${brief}) but not used.`
          : "Play the synced song, drag the 360 view, and record a camera move take.";
  } catch (_error) {
    status.textContent = "Open Director to record live 360 camera moves.";
  }
}

function applySphericalSetup(values = {}) {
  const normalized = normalizeSphericalSetup(values);
  document.querySelectorAll("fieldset[data-spherical-landmark]").forEach((group) => {
    const key = group.dataset.sphericalLandmark;
    const data = normalized[key] || {};
    group.querySelectorAll("[data-field]").forEach((input) => {
      const value = data[input.dataset.field];
      if (value != null && input.value === "") input.value = formatCanonicalNumber(value);
    });
    syncFriendlyFromAdvanced(group);
    queueSphericalPreview(group, "final");
  });
  updateSphericalWarnings();
}

function sphericalLandmarksFromForm() {
  const values = {};
  document.querySelectorAll("fieldset[data-spherical-landmark]").forEach((group) => {
    const yawInput = group.querySelector('[data-field="yaw"]');
    const yawText = String(yawInput?.value || "").trim();
    if (!yawText) return;
    const yaw = normalizeYaw(yawText);
    if (yaw == null) return;
    const data = { yaw };
    for (const field of ["pitch", "fov", "weight"]) {
      const text = String(group.querySelector(`[data-field="${field}"]`)?.value || "").trim();
      if (!text) continue;
      const value = parseLocaleNumber(text);
      if (value != null) data[field] = value;
    }
    values[group.dataset.sphericalLandmark] = data;
  });
  return values;
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

function sphericalMotionFromForm() {
  // Checked means subtle hold motion; unchecked means a perfectly locked hold.
  return document.querySelector("#sphericalMotion")?.checked === true;
}

function applySphericalMotion(enabled) {
  const input = document.querySelector("#sphericalMotion");
  if (input) input.checked = enabled === true;
}

function sphericalSweepFromForm() {
  return document.querySelector("#sphericalSweep")?.checked !== false;
}

function sweepSpeedFromForm() {
  return Math.max(30, Math.min(120, Number(document.querySelector("#sweepSpeed")?.value) || 60));
}

function applySphericalSweep(enabled, speed) {
  const toggle = document.querySelector("#sphericalSweep");
  const input = document.querySelector("#sweepSpeed");
  const output = document.querySelector("#sweepSpeedValue");
  if (toggle) toggle.checked = enabled !== false;
  if (input) input.value = String(Math.max(30, Math.min(120, Number(speed) || 60)));
  if (output) output.value = `${sweepSpeedFromForm()}°/s`;
}

function normalizeSphericalSetup(raw = {}) {
  const legacy = {
    full_stage: "full_stage_yaw",
    singer: "singer_yaw",
    drummer: "drummer_yaw",
    left: "left_yaw",
    right: "right_yaw",
    audience: "audience_yaw",
    audience_stage_wide: "audience_stage_wide_yaw",
    planet: "planet_yaw",
  };
  const defaults = {
    full_stage: 120,
    audience_stage_wide: 125,
    planet: 150,
  };
  const result = {};
  for (const [key, legacyKey] of Object.entries(legacy)) {
    const source = raw[key] && typeof raw[key] === "object" ? raw[key] : raw[legacyKey] != null ? { yaw: raw[legacyKey] } : null;
    if (!source || source.yaw == null) continue;
    const yaw = normalizeYaw(source.yaw);
    if (yaw == null) continue;
    const pitch = parseLocaleNumber(source.pitch);
    const fov = parseLocaleNumber(source.fov);
    const weight = parseLocaleNumber(source.weight);
    result[key] = {
      yaw,
      pitch: pitch != null ? pitch : 0,
      fov: fov != null ? fov : defaults[key] || 95,
      weight: weight != null ? weight : 1,
    };
  }
  return result;
}

function updateSphericalWarnings() {
  const warning = document.querySelector("#sphericalWarnings");
  if (!warning) return;
  const values = sphericalLandmarksFromForm();
  const enabled = Object.entries(values)
    .filter(([, data]) => (data.weight == null ? 1 : data.weight) > 0 && data.yaw != null)
    .map(([key, data]) => ({ key, yaw: data.yaw }));
  const messages = [];
  for (let i = 0; i < enabled.length; i += 1) {
    for (let j = i + 1; j < enabled.length; j += 1) {
      const delta = Math.abs(((enabled[j].yaw - enabled[i].yaw + 540) % 360) - 180);
      if (delta <= 10) {
        messages.push(`${LANDMARK_LABELS[enabled[i].key] || enabled[i].key} and ${LANDMARK_LABELS[enabled[j].key] || enabled[j].key} point at nearly the same angle.`);
      }
    }
  }
  warning.textContent = messages.join(" ");
  warning.hidden = messages.length === 0;
}

function syncFriendlyFromAdvanced(group) {
  const yaw = normalizeYaw(group.querySelector('[data-field="yaw"]')?.value);
  const pitch = parseLocaleNumber(group.querySelector('[data-field="pitch"]')?.value);
  const fov = parseLocaleNumber(group.querySelector('[data-field="fov"]')?.value);
  const weight = parseLocaleNumber(group.querySelector('[data-field="weight"]')?.value);
  const direction = group.querySelector('[data-friendly="direction"]');
  const height = group.querySelector('[data-friendly="height"]');
  const zoom = group.querySelector('[data-friendly="zoom"]');
  const frequency = group.querySelector('[data-friendly="frequency"]');
  const center = group.dataset.sphericalLandmark === "full_stage" ? 0 : stageCenterYaw();
  if (direction && yaw != null) direction.value = String(Math.round(signedYawDelta(yaw, center)));
  if (height && pitch != null) height.value = String(clamp(pitch, -45, 20));
  if (zoom && fov != null) zoom.value = String(clamp(fov, 65, MAX_SHOT_FOV));
  if (frequency) frequency.value = weightToFrequency(weight);
  updateRawSummary(group);
}

// Drag-to-look positioning for each 360 landmark, mirroring the Director/Result
// viewers: drag the shot image to aim, wheel to zoom. The numeric fields stay
// the source of truth and update live (kept visible, secondary), so the same
// interaction model works everywhere. Reuses the exact drag sensitivity of the
// live viewers (YAW_DEG_PER_PX / PITCH_DEG_PER_PX / dragSensitivityScale).
function wireLandmarkDragToLook() {
  const panel = document.querySelector("#sphericalSetup");
  if (!panel || panel.dataset.dragWired) return;
  panel.dataset.dragWired = "1";
  let active = null;

  const landmarkFov = (group) => parseLocaleNumber(group.querySelector('[data-field="fov"]')?.value) || 100;

  panel.addEventListener("pointerdown", (event) => {
    const image = event.target.closest?.(".shot-preview");
    const group = image?.closest("fieldset[data-spherical-landmark]");
    if (!image || !group) return;
    active = { group, image, x: event.clientX, y: event.clientY };
    image.classList.add("dragging");
    image.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });

  panel.addEventListener("pointermove", (event) => {
    if (!active) return;
    const dx = event.clientX - active.x;
    const dy = event.clientY - active.y;
    active.x = event.clientX;
    active.y = event.clientY;
    const group = active.group;
    const sensitivity = dragSensitivityScale(landmarkFov(group));
    const yawInput = group.querySelector('[data-field="yaw"]');
    const pitchInput = group.querySelector('[data-field="pitch"]');
    const yaw = normalizeYaw((parseLocaleNumber(yawInput?.value) || 0) - dx * YAW_DEG_PER_PX * sensitivity) ?? 0;
    const pitch = clamp((parseLocaleNumber(pitchInput?.value) || 0) + dy * PITCH_DEG_PER_PX * sensitivity, -85, 85);
    if (yawInput) yawInput.value = formatCanonicalNumber(yaw);
    if (pitchInput) pitchInput.value = formatCanonicalNumber(pitch);
    syncFriendlyFromAdvanced(group);
    queueSphericalPreview(group, "drag");
  });

  const stop = (event) => {
    if (!active) return;
    active.image.classList.remove("dragging");
    active.image.releasePointerCapture?.(event.pointerId);
    updateSphericalWarnings();
    queueSphericalPreview(active.group, "final"); // sharpen once the drag ends
    active = null;
  };
  panel.addEventListener("pointerup", stop);
  panel.addEventListener("pointercancel", stop);

  panel.addEventListener(
    "wheel",
    (event) => {
      const image = event.target.closest?.(".shot-preview");
      const group = image?.closest("fieldset[data-spherical-landmark]");
      if (!image || !group) return;
      event.preventDefault();
      const fovInput = group.querySelector('[data-field="fov"]');
      const delta = event.deltaY > 0 ? 4 : -4;
      const fov = clamp((parseLocaleNumber(fovInput?.value) || 100) + delta, MIN_SHOT_FOV, MAX_SHOT_FOV);
      if (fovInput) fovInput.value = formatCanonicalNumber(fov);
      syncFriendlyFromAdvanced(group);
      queueSphericalPreview(group, "drag");
    },
    { passive: false }
  );
}

function syncAdvancedFromFriendly(group, previewQuality = "drag") {
  const direction = parseLocaleNumber(group.querySelector('[data-friendly="direction"]')?.value);
  const height = parseLocaleNumber(group.querySelector('[data-friendly="height"]')?.value);
  const zoom = parseLocaleNumber(group.querySelector('[data-friendly="zoom"]')?.value);
  const frequency = parseLocaleNumber(group.querySelector('[data-friendly="frequency"]')?.value);
  const yawInput = group.querySelector('[data-field="yaw"]');
  const pitchInput = group.querySelector('[data-field="pitch"]');
  const fovInput = group.querySelector('[data-field="fov"]');
  const weightInput = group.querySelector('[data-field="weight"]');
  const center = group.dataset.sphericalLandmark === "full_stage" ? 0 : stageCenterYaw();
  if (yawInput && direction != null) yawInput.value = formatCanonicalNumber(normalizeYaw(center + direction));
  if (pitchInput && height != null) pitchInput.value = formatCanonicalNumber(height);
  if (fovInput && zoom != null) fovInput.value = formatCanonicalNumber(zoom);
  if (weightInput && frequency != null) weightInput.value = formatCanonicalNumber(frequency);
  updateRawSummary(group);
  updateSphericalWarnings();
  queueSphericalPreview(group, previewQuality);
}

function updateRawSummary(group) {
  const summary = group.querySelector(".raw-summary");
  if (!summary) return;
  const yaw = formatCanonicalNumber(group.querySelector('[data-field="yaw"]')?.value);
  const pitch = formatCanonicalNumber(group.querySelector('[data-field="pitch"]')?.value);
  const fov = formatCanonicalNumber(group.querySelector('[data-field="fov"]')?.value);
  const weight = parseLocaleNumber(group.querySelector('[data-field="weight"]')?.value);
  const frequency = weightToFrequency(weight);
  const label = { 0: "Never", 1: "Rarely", 5: "Sometimes", 15: "Often", 40: "A lot" }[frequency] || "Sometimes";
  summary.textContent = yaw ? `Exact: ${yaw}° direction, ${pitch || "0"}° height, zoom ${fov || "95"} · ${label}` : "";
}

function queueSphericalPreview(group, quality = "final") {
  const image = group.querySelector("[data-preview]");
  const source = selectedSphericalSourcePath();
  const yaw = normalizeYaw(group.querySelector('[data-field="yaw"]')?.value);
  const pitch = parseLocaleNumber(group.querySelector('[data-field="pitch"]')?.value);
  const fov = parseLocaleNumber(group.querySelector('[data-field="fov"]')?.value);
  if (!image || !source || yaw == null) return;
  const key = group.dataset.sphericalLandmark;
  clearTimeout(previewTimers.get(key));
  previewControllers.get(key)?.abort();
  const version = (previewVersions.get(key) || 0) + 1;
  previewVersions.set(key, version);
  previewTimers.set(
    key,
    setTimeout(() => {
      if (previewVersions.get(key) !== version) return;
      const params = new URLSearchParams({
        source,
        yaw: formatCanonicalNumber(yaw),
        pitch: formatCanonicalNumber(pitch ?? 0),
        fov: formatCanonicalNumber(fov ?? 95),
        shot_type: key,
        quality,
      });
      const nextSrc = `/api/v1/wizard/spherical-preview?${params.toString()}`;
      if (image.dataset.pendingSrc === nextSrc || image.src.endsWith(nextSrc)) return;
      image.dataset.pendingSrc = nextSrc;
      const controller = new AbortController();
      previewControllers.set(key, controller);
      fetch(nextSrc, { signal: controller.signal })
        .then((response) => {
          if (!response.ok) throw new Error("Preview failed");
          return response.blob();
        })
        .then((blob) => {
          if (previewVersions.get(key) === version) {
            if (image.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
            const objectUrl = URL.createObjectURL(blob);
            image.dataset.objectUrl = objectUrl;
            image.src = objectUrl;
            image.dataset.pendingSrc = "";
          }
        })
        .catch((error) => {
          if (error.name !== "AbortError" && previewVersions.get(key) === version) image.dataset.pendingSrc = "";
        })
        .finally(() => {
          if (previewControllers.get(key) === controller) previewControllers.delete(key);
        });
    }, quality === "drag" ? 90 : 220)
  );
}

function directorStatus(message, isError = false) {
  const node = document.querySelector("#directorStatus");
  if (!node) return;
  node.textContent = message;
  node.style.color = isError ? "var(--bad)" : "";
}

function setDirectorProgress(percent, detail = "", visible = true) {
  const root = document.querySelector("#directorProgress");
  const bar = document.querySelector("#directorProgressBar");
  const text = document.querySelector("#directorProgressText");
  if (!root || !bar || !text) return;
  root.hidden = !visible;
  const value = clamp(Number(percent) || 0, 0, 100);
  bar.style.width = `${value}%`;
  text.textContent = detail || `${Math.round(value)}%`;
}

async function openDirector() {
  if (!hasSphericalInput()) {
    showToast("Add a 360 clip before opening Director", true);
    return;
  }
  document.querySelector("#directorScreen").hidden = false;
  directorStatus("Loading 360 Director...");
  try {
    await setupDirector();
    await loadDirectorTakes();
  } catch (error) {
    director.loading = false;
    setDirectorProgress(0, "", false);
    directorStatus(error.message, true);
    showToast(error.message, true);
  }
}

function closeDirector() {
  stopDirectorRecording(false);
  pauseDirector();
  document.querySelector("#directorScreen").hidden = true;
}

async function setupDirector() {
  if (director.loading) return;
  director.loading = true;
  const canvas = document.querySelector("#directorCanvas");
  const video = document.querySelector("#directorVideo");
  const audio = document.querySelector("#directorAudio");
  const gl = canvas.getContext("webgl2");
  if (!gl) throw new Error("WebGL2 is not available in this webview");
  director.gl = gl;
  const [THREE, media] = await Promise.all([import("/vendor/three.module.min.js"), loadDirectorMedia()]);
  director.media = media;
  director.three = THREE;
  video.src = `${media.video_url}?t=${Date.now()}`;
  audio.src = `${media.master_url}?t=${Date.now()}`;
  video.muted = true;
  video.playsInline = true;
  await Promise.all([waitForMedia(video), waitForMedia(audio).catch(() => {})]);
  initDirectorScene(THREE, canvas, video);
  wireDirectorEvents();
  resizeDirector();
  updateDirectorCamera();
  director.ready = true;
  director.loading = false;
  seekDirector(0);
  setDirectorProgress(100, "", false);
  directorStatus("WebGL active. Drag the view while the song plays, then record a take.");
}

async function loadDirectorMedia() {
  let media = await api("/wizard/director-media");
  if (media.proxy_ready) return media;
  setDirectorProgress(media.progress || 0, media.detail || media.message || "Preparing preview...", true);
  directorStatus(media.message || "Preparing a lightweight 360 preview — this happens once per clip.");
  const started = Date.now();
  while (!media.proxy_ready) {
    if (Date.now() - started > 30 * 60 * 1000) throw new Error("Preparing the 360 preview timed out. Open logs for details.");
    await new Promise((resolve) => setTimeout(resolve, 700));
    media = await api(`/wizard/director-media/status?job_id=${encodeURIComponent(media.job_id)}`);
    if (media.status === "failed") throw new Error(media.error || "Could not prepare the 360 preview");
    setDirectorProgress(media.progress || 0, media.detail || media.message || "Preparing preview...", true);
  }
  return media;
}

function waitForMedia(media) {
  if (Number.isFinite(media.duration) && media.duration > 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const done = () => {
      cleanup();
      resolve();
    };
    const fail = () => {
      cleanup();
      const details = media.error?.message || media.networkState || "unknown media error";
      logFrontendError(`Director media failed to load: ${details}`);
      reject(new Error("Could not load Director media. Open logs for details."));
    };
    const cleanup = () => {
      media.removeEventListener("loadedmetadata", done);
      media.removeEventListener("error", fail);
    };
    media.addEventListener("loadedmetadata", done, { once: true });
    media.addEventListener("error", fail, { once: true });
  });
}

function initDirectorScene(THREE, canvas, video) {
  if (director.renderer) {
    director.texture?.dispose?.();
    director.renderer.dispose?.();
  }
  director.renderer = new THREE.WebGLRenderer({ canvas, context: director.gl, antialias: true });
  director.scene = new THREE.Scene();
  director.camera = new THREE.PerspectiveCamera(50, 16 / 9, 0.1, 1100);
  const geometry = new THREE.SphereGeometry(500, 96, 64);
  geometry.scale(-1, 1, 1);
  director.texture = new THREE.VideoTexture(video);
  director.texture.colorSpace = THREE.SRGBColorSpace;
  director.sphere = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ map: director.texture }));
  director.scene.add(director.sphere);
  if (director.animation) cancelAnimationFrame(director.animation);
  const render = () => {
    director.animation = requestAnimationFrame(render);
    syncDirectorAudio();
    updateDirectorScrub();
    applyDirectorPreviewCurve();
    director.renderer.render(director.scene, director.camera);
  };
  render();
}

function wireDirectorEvents() {
  const canvas = document.querySelector("#directorCanvas");
  if (canvas.dataset.wired) return;
  canvas.dataset.wired = "1";
  canvas.addEventListener("pointerdown", (event) => {
    director.dragging = true;
    director.dragX = event.clientX;
    director.dragY = event.clientY;
    canvas.classList.add("dragging");
    canvas.setPointerCapture?.(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!director.dragging) return;
    const dx = event.clientX - director.dragX;
    const dy = event.clientY - director.dragY;
    director.dragX = event.clientX;
    director.dragY = event.clientY;
    const sensitivity = dragSensitivityScale(director.fov);
    director.yaw = normalizeYaw(director.yaw - dx * YAW_DEG_PER_PX * sensitivity) ?? 0;
    director.pitch = clamp(director.pitch + dy * PITCH_DEG_PER_PX * sensitivity, -85, 85);
    updateDirectorCamera();
  });
  const stopDrag = (event) => {
    director.dragging = false;
    canvas.classList.remove("dragging");
    if (event?.pointerId != null) canvas.releasePointerCapture?.(event.pointerId);
  };
  canvas.addEventListener("pointerup", stopDrag);
  canvas.addEventListener("pointercancel", stopDrag);
  window.addEventListener("resize", resizeDirector);
  document.querySelector("#directorFov").addEventListener("input", (event) => {
    director.fov = Number(event.target.value) || 100;
    updateDirectorCamera();
  });
  document.querySelector("#directorScrub").addEventListener("input", (event) => {
    seekDirector(Number(event.target.value) || 0);
  });
  // D2: mouse wheel controls FOV (zoom) directly on the sphere canvas.
  // Scrolling up (negative deltaY) zooms in (smaller FOV); down zooms out.
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const delta = event.deltaY > 0 ? 3 : -3;
    director.fov = clamp((director.fov || 100) + delta, MIN_SHOT_FOV, MAX_SHOT_FOV);
    const fovSlider = document.querySelector("#directorFov");
    if (fovSlider) fovSlider.value = String(director.fov);
    updateDirectorCamera();
  }, { passive: false });
}

function resizeDirector() {
  if (!director.renderer || !director.camera) return;
  const canvas = document.querySelector("#directorCanvas");
  const width = Math.max(320, canvas.clientWidth || 960);
  const height = Math.max(180, canvas.clientHeight || Math.round(width * 9 / 16));
  director.renderer.setSize(width, height, false);
  director.camera.aspect = width / height;
  updateDirectorCamera();
}

function updateDirectorCamera() {
  if (!director.camera || !director.three) return;
  const THREE = director.three;
  const aspect = Math.max(0.1, director.camera.aspect || 16 / 9);
  director.camera.fov = verticalFovFromHorizontal(director.fov, aspect);
  director.camera.updateProjectionMatrix();
  const yaw = THREE.MathUtils.degToRad(signedYawDelta(director.yaw, 0));
  const pitch = THREE.MathUtils.degToRad(clamp(director.pitch, -85, 85));
  const target = new THREE.Vector3(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), -Math.cos(yaw) * Math.cos(pitch));
  director.camera.lookAt(target);
  document.querySelector("#directorHud").textContent = `Yaw ${formatCanonicalNumber(director.yaw)}° · Pitch ${formatCanonicalNumber(director.pitch)}° · Shot width ${formatCanonicalNumber(director.fov)}°`;
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

async function toggleDirectorPlay() {
  const video = document.querySelector("#directorVideo");
  if (video.paused) await playDirector();
  else pauseDirector();
}

async function playDirector() {
  const video = document.querySelector("#directorVideo");
  const audio = document.querySelector("#directorAudio");
  syncDirectorAudio(true);
  await Promise.all([video.play(), audio.play().catch(() => {})]);
  document.querySelector("#directorPlay").textContent = "Pause";
  injectIcons();
}

function pauseDirector() {
  document.querySelector("#directorVideo")?.pause();
  document.querySelector("#directorAudio")?.pause();
  const button = document.querySelector("#directorPlay");
  if (button) {
    button.textContent = "Play";
    button.dataset.icon = "play";
    button.innerHTML = "Play";
    injectIcons();
  }
}

function seekDirector(time) {
  const video = document.querySelector("#directorVideo");
  const audio = document.querySelector("#directorAudio");
  const duration = Number(director.media?.duration_sec || 0);
  const next = clamp(time, 0, duration || 0);
  const masterTime = localDirectorTimeToMaster(next);
  video.currentTime = videoTimeForMaster(masterTime);
  audio.currentTime = masterTime;
  updateDirectorScrub();
}

// Audio is the timing master: it's a plain linear MP3 track that plays back
// reliably on its own, whereas the video drives a WebGL VideoTexture and is
// far more prone to visible stalls when seeked. So we let audio.currentTime
// run natively and correct the VIDEO element to follow it — with a generous
// tolerance and a throttled correction interval, so normal decode jitter
// never triggers a seek, let alone a seek-every-frame loop (that was the
// original stutter bug, just on the other element).
let _videoSyncLastAt = 0;
const VIDEO_SYNC_INTERVAL_MS = 1000; // soft-correct at most once a second during playback
const VIDEO_SOFT_DRIFT_THRESHOLD = 0.2; // seconds; ignore drift below this entirely
const VIDEO_HARD_DRIFT_THRESHOLD = 0.6; // seconds; resync immediately regardless of interval

function syncDirectorAudio(force = false) {
  const video = document.querySelector("#directorVideo");
  const audio = document.querySelector("#directorAudio");
  if (!video || !audio || !director.media || Number.isNaN(audio.currentTime)) return;
  const masterTime = Number(audio.currentTime || 0);
  const end = Number(director.media?.trim_end_sec || masterTime);
  if (masterTime >= end && !audio.paused) {
    pauseDirector();
    return;
  }
  const targetVideoTime = videoTimeForMaster(masterTime);
  const now = performance.now();
  const drift = Math.abs((video.currentTime || 0) - targetVideoTime);
  if (force || drift > VIDEO_HARD_DRIFT_THRESHOLD || (drift > VIDEO_SOFT_DRIFT_THRESHOLD && now - _videoSyncLastAt > VIDEO_SYNC_INTERVAL_MS)) {
    video.currentTime = Math.max(0, Math.min(Number(video.duration || targetVideoTime), targetVideoTime));
    _videoSyncLastAt = now;
  }
  if (video.paused !== audio.paused) {
    if (audio.paused) video.pause();
    else video.play().catch(() => {});
  }
}

function masterTimeForDirectorVideo(videoTime) {
  return Math.max(0, Number(director.media?.offset_sec || 0) + Number(videoTime || 0));
}

// Audio is the timing master (see syncDirectorAudio) — prefer its
// currentTime directly wherever "what master-timeline position are we at
// right now" is needed (scrub display, recording samples, take preview),
// since the video element is only ever a generously-tolerant follower and
// can legitimately lag/lead by a few hundred ms.
function masterTimeNow() {
  const audio = document.querySelector("#directorAudio");
  if (audio && director.media && !Number.isNaN(audio.currentTime)) {
    return Math.max(0, Number(audio.currentTime || 0));
  }
  const video = document.querySelector("#directorVideo");
  return masterTimeForDirectorVideo(video?.currentTime || 0);
}

function localDirectorTimeToMaster(localTime) {
  return Number(director.media?.trim_start_sec || 0) + Number(localTime || 0);
}

function videoTimeForMaster(masterTime) {
  const video = document.querySelector("#directorVideo");
  const duration = Number(video?.duration || director.media?.proxy_duration_sec || 0);
  return clamp(Number(masterTime || 0) - Number(director.media?.offset_sec || 0), 0, duration || 0);
}

function updateDirectorScrub() {
  const video = document.querySelector("#directorVideo");
  const scrub = document.querySelector("#directorScrub");
  if (!video || !scrub) return;
  const duration = Number(director.media?.duration_sec || video.duration || 0);
  const local = clamp(masterTimeNow() - Number(director.media?.trim_start_sec || 0), 0, duration || 0);
  scrub.max = String(Math.max(0.01, duration));
  if (document.activeElement !== scrub) scrub.value = String(local);
  document.querySelector("#directorTime").textContent = `${secondsToTime(local)} / ${secondsToTime(duration)}`;
}

async function toggleDirectorRecording() {
  if (director.recording) {
    await stopDirectorRecording(true);
    return;
  }
  director.samples = [];
  director.smoothedSamples = [];
  director.previewingTake = false;
  document.querySelector("#directorPreviewTake").hidden = true;
  document.querySelector("#directorSaveTake").hidden = true;
  director.recording = true;
  document.querySelector("#directorRecord").textContent = "Stop recording";
  directorStatus("Recording camera moves...");
  sampleDirectorCamera();
  director.recordTimer = setInterval(sampleDirectorCamera, 1000 / 15);
}

function sampleDirectorCamera() {
  const video = document.querySelector("#directorVideo");
  if (!director.recording || !video || video.paused) return;
  const t = masterTimeNow();
  const trimEnd = Number(director.media?.trim_end_sec || Infinity);
  if (t > trimEnd) return;
  const last = director.samples[director.samples.length - 1];
  if (last && t <= last.t) return;
  director.samples.push({
    t,
    video_time: video.currentTime || 0,
    yaw: director.yaw,
    pitch: director.pitch,
    fov: director.fov,
  });
}

async function stopDirectorRecording(save) {
  if (!director.recording) return;
  clearInterval(director.recordTimer);
  director.recordTimer = null;
  director.recording = false;
  const button = document.querySelector("#directorRecord");
  if (button) button.textContent = "Record camera moves";
  if (!save) return;
  if (director.samples.length < 2) {
    directorStatus("Recording was too short.", true);
    return;
  }
  directorStatus("Smoothing camera moves...");
  await new Promise((resolve) => setTimeout(resolve, 30));
  const strength = document.querySelector("#directorSmoothing")?.value || "medium";
  director.smoothedSamples = smoothDirectorSamples(director.samples, strength);
  director.previewingTake = false;
  document.querySelector("#directorPreviewTake").hidden = false;
  document.querySelector("#directorSaveTake").hidden = false;
  directorStatus(`Smoothed ${director.samples.length} samples (${strength}). Preview it, then save the take.`);
}

function smoothingRadius(strength) {
  return { light: 3, medium: 7, strong: 12 }[String(strength || "medium")] || 7;
}

function smoothDirectorSamples(samples, strength) {
  const radius = smoothingRadius(strength);
  if (!Array.isArray(samples) || samples.length <= 2) return limitDirectorYawRate(samples || []);
  const yaws = unwrapYawSeries(samples.map((sample) => Number(sample.yaw || 0)));
  const smoothed = samples.map((sample, index) => {
    const start = Math.max(0, index - radius);
    const end = Math.min(samples.length, index + radius + 1);
    const weighted = (values) => {
      let total = 0;
      let sum = 0;
      for (let i = start; i < end; i += 1) {
        const weight = 1 - Math.abs(i - index) / (radius + 1);
        total += weight;
        sum += Number(values[i] || 0) * weight;
      }
      return total ? sum / total : Number(values[index] || 0);
    };
    return {
      ...sample,
      yaw: normalizeYaw(weighted(yaws)),
      pitch: clamp(weighted(samples.map((item) => Number(item.pitch || 0))), -89, 89),
      fov: clamp(weighted(samples.map((item) => Number(item.fov || 100))), 1, 179),
    };
  });
  return limitDirectorYawRate(smoothed);
}

function limitDirectorYawRate(samples, maxRate = MAX_RECORDED_YAW_RATE_DEG_PER_SEC) {
  if (!Array.isArray(samples) || samples.length <= 1) return [...(samples || [])];
  const unwrapped = unwrapYawSeries(samples.map((sample) => Number(sample.yaw || 0)));
  const output = [{ ...samples[0], yaw: normalizeYaw(unwrapped[0]) }];
  let previousYaw = unwrapped[0];
  for (let index = 1; index < samples.length; index += 1) {
    const previous = samples[index - 1];
    const current = samples[index];
    const dt = Math.max(0, Number(current.t || 0) - Number(previous.t || 0));
    let delta = unwrapped[index] - previousYaw;
    const maxDelta = Math.max(0, Number(maxRate) || 0) * dt;
    if (Math.abs(delta) > maxDelta) delta = Math.sign(delta) * maxDelta;
    previousYaw += delta;
    output.push({ ...current, yaw: normalizeYaw(previousYaw) });
  }
  return output;
}

function unwrapYawSeries(yaws) {
  if (!yaws.length) return [];
  const output = [Number(yaws[0] || 0)];
  for (let index = 1; index < yaws.length; index += 1) {
    let value = Number(yaws[index] || 0);
    const previous = output[index - 1];
    while (value - previous > 180) value -= 360;
    while (value - previous < -180) value += 360;
    output.push(value);
  }
  return output;
}

function previewDirectorTake() {
  if (!director.smoothedSamples.length) return;
  director.previewingTake = true;
  seekDirector(Math.max(0, director.smoothedSamples[0].t - Number(director.media?.trim_start_sec || 0)));
  playDirector().catch((error) => showToast(error.message, true));
  directorStatus("Previewing smoothed camera move.");
}

function applyDirectorPreviewCurve() {
  if (!director.previewingTake || !director.smoothedSamples.length || director.recording) return;
  const masterTime = masterTimeNow();
  const sample = interpolateDirectorSample(director.smoothedSamples, masterTime);
  if (!sample) return;
  director.yaw = sample.yaw;
  director.pitch = sample.pitch;
  director.fov = sample.fov;
  document.querySelector("#directorFov").value = String(sample.fov);
  updateDirectorCamera();
}

function interpolateDirectorSample(samples, t) {
  if (!samples.length) return null;
  if (t <= samples[0].t) return samples[0];
  if (t >= samples[samples.length - 1].t) return samples[samples.length - 1];
  for (let index = 0; index < samples.length - 1; index += 1) {
    const left = samples[index];
    const right = samples[index + 1];
    if (left.t <= t && t <= right.t) {
      const amount = (t - left.t) / Math.max(0.000001, right.t - left.t);
      const yawStart = unwrapYawSeries([left.yaw, right.yaw]);
      return {
        t,
        yaw: normalizeYaw(yawStart[0] + (yawStart[1] - yawStart[0]) * amount),
        pitch: left.pitch + (right.pitch - left.pitch) * amount,
        fov: left.fov + (right.fov - left.fov) * amount,
      };
    }
  }
  return samples[samples.length - 1];
}

async function saveDirectorTake() {
  if (director.samples.length < 2) {
    directorStatus("Record a take before saving.", true);
    return;
  }
  const nameInput = document.querySelector("#directorTakeName");
  const fallback = `Take ${new Date().toISOString().slice(0, 19).replace("T", " ").replaceAll(":", ".")}`;
  const result = await api("/wizard/camera-moves", {
    method: "POST",
    body: JSON.stringify({
      name: nameInput.value || fallback,
      source_path: director.media?.source_path || "",
      samples: director.samples,
      smoothing: document.querySelector("#directorSmoothing")?.value || "medium",
    }),
  });
  nameInput.value = "";
  document.querySelector("#directorPreviewTake").hidden = true;
  document.querySelector("#directorSaveTake").hidden = true;
  director.previewingTake = false;
  renderDirectorTakes(result.takes || []);
  refreshDirectorEntryStatus().catch(() => {});
  directorStatus(`Saved ${result.take?.name || "take"} with ${director.samples.length} samples.`);
}

async function loadDirectorTakes() {
  const result = await api("/wizard/camera-moves");
  renderDirectorTakes(result.takes || []);
}

function renderDirectorTakes(takes) {
  const root = document.querySelector("#directorTakes");
  if (!root) return;
  if (!takes.length) {
    root.innerHTML = "<small>No recorded takes yet.</small>";
    return;
  }
  root.innerHTML = takes
    .map((take) => {
      const start = secondsToTime(take.start_master_sec || 0);
      const end = secondsToTime(take.end_master_sec || 0);
      return `<div class="director-take">
        <div><strong>${escapeHtml(take.name)}</strong><small>${escapeHtml(start)}-${escapeHtml(end)} · ${Number(take.sample_count || 0)} samples</small></div>
        <button class="icon-button small" data-icon="trash" data-delete-take="${escapeHtml(take.name)}" type="button">Delete</button>
      </div>`;
    })
    .join("");
  injectIcons();
}

async function deleteDirectorTake(name) {
  const response = await fetch(`/api/v1/wizard/camera-moves/${encodeURIComponent(name)}`, { method: "DELETE" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || "Could not delete take");
  renderDirectorTakes(data.takes || []);
  refreshDirectorEntryStatus().catch(() => {});
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
    const status = await api("/wizard/status");
    if (status.status === "waiting_choice") return;
    if (status.status === "done") return;
    if (status.status === "failed") throw new Error(status.error || S.prepareFailed);
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(S.prepareTimeout);
}

async function startWizard(options = {}) {
  const waitForPrepare = options.waitForPrepare !== false;
  const inputs = selectedInputs();
  setStep(3);
  if (waitForPrepare && selectedPlatform !== "360") {
    await api("/wizard/prepare", {
      method: "POST",
      body: JSON.stringify({
        name: document.querySelector("#videoName").value || todayName(),
        master: inputs.master,
        songs: inputs.songs,
        videos: inputs.videos,
      }),
    });
    ensureStatusPolling();
    await waitForPreparedProject();
  }
  await api("/wizard/start", {
    method: "POST",
    body: JSON.stringify({
      name: document.querySelector("#videoName").value || todayName(),
      platform: selectedPlatform,
      song_index: selectedSong,
      trim_start_sec: timeToSeconds(document.querySelector("#trimStart").value),
      trim_end_sec: timeToSeconds(document.querySelector("#trimEnd").value),
      spherical_landmarks: sphericalLandmarksFromForm(),
      camera_role_weights: cameraRoleWeightsFromForm(),
      fixed_rear_motion: fixedRearMotionFromForm(),
      spherical_motion: sphericalMotionFromForm(),
      spherical_mode: sphericalModeFromForm(),
      spherical_sweep: sphericalSweepFromForm(),
      sweep_speed_deg_per_sec: sweepSpeedFromForm(),
      reel_duration_sec: reelOptionsFromForm().duration,
      reel_aspect: reelOptionsFromForm().aspect,
      reel_text_overlays: reelOptionsFromForm().texts,
      reel_image_overlays: reelOptionsFromForm().images,
      master: inputs.master,
      songs: inputs.songs,
      videos: inputs.videos,
    }),
  });
  ensureStatusPolling();
  await pollStatus();
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
    pollTimer = setInterval(() => {
      pollStatus().catch((error) => {
        logFrontendError(`pollStatus failed: ${error.message}`, error.stack || "");
        showToast(`${S.statusUpdateFailed}: ${error.message}`, true);
      });
    }, 1000);
  }
}

async function pollStatus() {
  const status = await api("/wizard/status");
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
  const reportedProgress = Math.max(0, Math.min(100, Number(status.progress || 0)));
  const progress = Math.max(progressFloor, reportedProgress);
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
    clearInterval(pollTimer);
    pollTimer = null;
    document.querySelector("#errorText").textContent = status.error || S.failedTitle;
    document.querySelector("#resultTitle").textContent = S.failedTitle;
    document.querySelector("#errorBox").hidden = false;
    document.querySelector("#resultBox").hidden = true;
    refreshProgressReport().catch((error) => logFrontendError(`progress report failed: ${error.message}`, error.stack || ""));
    setStep(4);
  }
  if (status.status === "done") {
    clearInterval(pollTimer);
    pollTimer = null;
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
    setStep(4);
  }
  if (status.status === "cancelled") {
    clearInterval(pollTimer);
    pollTimer = null;
    document.querySelector("#errorText").textContent = "Export cancelled.";
    document.querySelector("#resultTitle").textContent = "Cancelled";
    document.querySelector("#errorBox").hidden = false;
    document.querySelector("#resultBox").hidden = true;
    setStep(4);
  }
  if (status.status === "waiting_choice") {
    clearInterval(pollTimer);
    pollTimer = null;
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
  const preview = await api(`/stages/sync/preview/${encodeURIComponent(clipId)}`);
  document.querySelector("#rescuePreview").src = `${preview.media_url}?t=${Date.now()}`;
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function confirmRescue() {
  if (!rescueClipId) return;
  const offset = Number(document.querySelector("#rescueOffset").value);
  if (!Number.isFinite(offset)) {
    showToast(S.invalidOffset || "Enter a valid offset", true);
    return;
  }
  document.querySelector("#rescuePanel").hidden = true;
  document.querySelector("#resultBox").hidden = true;
  const sphericalPanel = document.querySelector("#sphericalSetup");
  if (sphericalPanel) {
    sphericalPanel.open = false;
    sphericalPanel.dataset.initialized = "";
  }
  setStep(3);
  await api("/wizard/rescue", { method: "POST", body: JSON.stringify({ clip_id: rescueClipId, offset_sec: offset }) });
  ensureStatusPolling();
  await pollStatus();
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
  document.querySelector("#progressReport").textContent = text || "No details yet.";
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
  const target = rawTarget instanceof HTMLElement ? rawTarget.closest("button, [data-remove-kind], [data-open-project], [data-delete-project], [data-rescue]") || rawTarget : rawTarget;
  if (!(target instanceof HTMLElement)) return;
  if (target.id === "addReelText") {
    reelTextOverlays.push({ text: "", color: "#ffffff", size: 54, position: "middle-center", x: 0.5, y: 0.5, opacity: 1, font: "bundled", font_weight: "bold", outline_color: "#000000", outline_width: 2, shadow_color: "#000000", shadow_offset_x: 3, shadow_offset_y: 3, shadow_blur: 4, background_color: "#000000", background_opacity: 0, background_radius: 8, animation: "fade", start_sec: 0, duration_sec: 3 });
    renderReelOptions();
  }
  if (target.dataset.removeReelText != null) {
    reelTextOverlays.splice(Number(target.dataset.removeReelText), 1);
    renderReelOptions();
  }
  if (target.dataset.removeReelImage != null) {
    reelImageOverlays.splice(Number(target.dataset.removeReelImage), 1);
    renderReelOptions();
  }
  if (target.id === "showAllClips") restoreSetAsideVideos();
  if (target.id === "confirmFiles") prepareStep2().catch((error) => showToast(error.message, true));
  if (target.id === "newProject") newProject().catch((error) => showToast(error.message, true));
  if (target.id === "refreshProjects") loadProjects().catch((error) => showToast(error.message, true));
  const stepNav = target.closest?.("[data-step-nav]");
  if (stepNav instanceof HTMLElement) {
    setStep(Number(stepNav.dataset.stepNav));
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
    selectedPlatform = target.dataset.platform;
    document.querySelectorAll(".platform-card").forEach((card) => card.classList.toggle("selected", card === target));
    document.querySelector("#startWizard").disabled = false;
    applyEditTypeMode();
    if (currentSongs.length > 1) renderSongOptions(currentSongs);
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
  if (target.id === "reuseSphericalSetup") {
    document.querySelectorAll("#sphericalSetup [data-field]").forEach((input) => (input.value = ""));
    applySphericalSetup(lastSphericalSetup);
  }
  if (target.id === "openDirector") openDirector().catch((error) => showToast(error.message, true));
  if (target.id === "closeDirector") closeDirector();
  if (target.id === "directorPlay") toggleDirectorPlay().catch((error) => showToast(error.message, true));
  if (target.id === "directorRecord") toggleDirectorRecording().catch((error) => showToast(error.message, true));
  if (target.id === "directorPreviewTake") previewDirectorTake();
  if (target.id === "directorSaveTake") saveDirectorTake().catch((error) => showToast(error.message, true));
  if (target.dataset.deleteTake) {
    deleteDirectorTake(target.dataset.deleteTake).catch((error) => showToast(error.message, true));
  }
  if (target.id === "retryWizard" || target.id === "retryWizardSuccess") {
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
    document.querySelector("#errorBox").hidden = true;
    document.querySelector("#resultBox").hidden = true;
    document.querySelector("#progressTitle").textContent = "Creating your video";
    setStep(1);
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
  if (target.id === "fullscreenResult") {
    const element = latestResult?.platform === "360" ? document.querySelector("#result360Player") : document.querySelector("#resultVideo");
    element?.requestFullscreen?.().catch((error) => showToast(error.message, true));
  }
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

document.addEventListener("input", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLElement)) return;
  if (input.id === "reelDuration" || input.id === "reelAspect") { renderReelOptions(); return; }
  if (input.id === "reelPlayhead") { reelPlayhead = Number(input.value); document.querySelector("#reelPlayheadValue").textContent = `${reelPlayhead.toFixed(1)}s`; drawReelPreview(); return; }
  const row = input.closest?.("[data-reel-text-index]");
  if (!row || !input.dataset.reelField) return;
  const index = Number(row.dataset.reelTextIndex);
  const item = reelTextOverlays[index];
  if (!item) return;
  const field = input.dataset.reelField;
  item[field] = ["size", "start_sec", "duration_sec", "opacity", "outline_width", "shadow_blur", "background_opacity", "background_radius", "shadow_offset_x", "shadow_offset_y"].includes(field) ? Number(input.value) : input.value;
  renderReelTimeline();
  drawReelPreview();
});

document.addEventListener("input", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLElement) || !input.dataset.reelImageField) return;
  const row = input.closest?.("[data-reel-image-index]"); if (!row) return;
  const item = reelImageOverlays[Number(row.dataset.reelImageIndex)]; if (!item) return;
  item[input.dataset.reelImageField] = ["width", "opacity", "start_sec", "duration_sec"].includes(input.dataset.reelImageField) ? Number(input.value) : input.value;
  renderReelTimeline(); drawReelPreview();
});

document.addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLElement)) return;
  if (input.id === "reelAspect") drawReelPreview();
  if (input.id === "addReelImage" && input.files?.[0]) {
    const form = new FormData(); form.append("file", input.files[0]);
    api("/wizard/reel-overlay", { method: "POST", body: form, headers: {} }).then((result) => {
      reelImageOverlays.push({ path: result.path, preview_url: result.url || result.path, x: 0.5, y: 0.5, width: 0.35, opacity: 1, animation: "fade", start_sec: 0, duration_sec: 3 });
      renderReelOptions();
    }).catch((error) => showToast(error.message, true));
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
  drawReelPreview();
});
document.addEventListener("pointerup", () => { if (reelDrag) { reelDrag = null; renderReelTimeline(); } });

document.addEventListener("toggle", (event) => {
  const target = event.target;
  if (target instanceof HTMLDetailsElement && target.id === "progressDetails" && target.open) {
    refreshProgressReport().catch((error) => showToast(error.message, true));
  }
}, true);

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement && target.id === "masterSelect") {
    selectedMasterPath = target.value;
    trimDefaultsAppliedFor = "";
    setupTrimControls(selectedMasterPath);
    // Put previously set-aside clips back in the pool so the newly chosen
    // song's own session decides which clips are relevant, rather than
    // inheriting the previous song's filtering.
    detected.videos = [...detected.videos, ...setAsideVideos];
    setAsideVideos = [];
    renderChips();
  }
  if (target instanceof HTMLInputElement && target.name === "sphericalMode") {
    applySphericalMode(target.value);
  }
  const sphericalGroup = target.closest?.("fieldset[data-spherical-landmark]");
  if (sphericalGroup instanceof HTMLElement && target instanceof HTMLInputElement) {
    if (target.dataset.friendly) syncAdvancedFromFriendly(sphericalGroup, "final");
    else {
      syncFriendlyFromAdvanced(sphericalGroup);
      updateSphericalWarnings();
      queueSphericalPreview(sphericalGroup, "final");
    }
  }
  if (sphericalGroup instanceof HTMLElement && target instanceof HTMLSelectElement && target.dataset.friendly) {
    syncAdvancedFromFriendly(sphericalGroup, "final");
  }
});

document.addEventListener("input", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.id === "sweepSpeed") {
    const output = document.querySelector("#sweepSpeedValue");
    if (output) output.value = `${sweepSpeedFromForm()}°/s`;
  }
  const sphericalGroup = target.closest?.("fieldset[data-spherical-landmark]");
  if (!(sphericalGroup instanceof HTMLElement)) return;
  if (target instanceof HTMLInputElement && target.dataset.friendly) {
    syncAdvancedFromFriendly(sphericalGroup, "drag");
  } else if (target instanceof HTMLInputElement) {
    syncFriendlyFromAdvanced(sphericalGroup);
    updateSphericalWarnings();
    queueSphericalPreview(sphericalGroup, "drag");
  }
});

document.querySelector("#result360Scrub").addEventListener("input", (event) => {
  seekResult360(Number(event.target.value) || 0);
});

document.querySelector("#videoName").value = todayName();

async function boot() {
  injectIcons();
  auditBackdropRuntime();
  wireLandmarkDragToLook();
  await loadAppConfig();
  const status = await api("/wizard/status");
  if (["running", "waiting_choice", "done", "failed"].includes(status.status)) {
    await resumeInputsFromProject().catch(() => {});
  } else {
    await loadInbox();
  }
  await loadProjects().catch(() => {});
  renderWizardStatus(status);
  if (status.status === "running") {
    setStep(3);
    ensureStatusPolling();
  } else if (status.status === "waiting_choice") {
    setStep(2);
  } else if (status.status === "done" || status.status === "failed") {
    setStep(4);
  }
}

boot().catch((error) => showToast(error.message, true));
