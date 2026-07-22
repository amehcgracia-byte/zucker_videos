const detected = { master: [], songs: [], videos: [], ignored: [] };
const S = window.UI_STRINGS || {};
let selectedPlatform = null;
let selectedSong = null;
let selectedMasterPath = null;
let currentSongs = [];
let latestResult = null;
let latestStatus = null;
let pollTimer = null;
let appConfig = { dev: true, desktop: false };
let progressStartedAt = null;
let progressSamples = [];
let rescueClipId = null;
let currentStep = 1;
let lastProgressReportAt = 0;
let trimDefaultsAppliedFor = "";
let lastSphericalSetup = {};
let cameraRoleWeights = { "360": 50, handheld: 30, fixed_rear: 20 };
let fixedRearMotion = true;
let sphericalMode = "automatic";
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
  sphericalMode = appConfig.spherical_mode === "directed" ? "directed" : "automatic";
  applySphericalMode(sphericalMode);
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
  if (project.settings?.edit?.spherical_mode) {
    applySphericalMode(project.settings.edit.spherical_mode);
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
  if (!inputs.master || !inputs.videos.length) return;
  api("/wizard/prepare", {
    method: "POST",
    body: JSON.stringify({
      name: document.querySelector("#videoName").value || todayName(),
      master: inputs.master,
      songs: inputs.songs,
      videos: inputs.videos,
    }),
  }).catch((error) => showToast(error.message, true));
  setStep(2);
  ensureStatusPolling();
  setupTrimControls(inputs.master);
  renderSphericalSetup();
  if (inputs.songs) {
    const result = await api("/wizard/songs", { method: "POST", body: JSON.stringify({ songs: inputs.songs }) });
    renderSongOptions(result.songs || []);
  } else {
    renderSongOptions([]);
  }
}

function renderSphericalSetup() {
  const panel = document.querySelector("#sphericalSetup");
  if (!panel) return;
  panel.hidden = !hasSphericalInput();
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
    const count = (result.takes || []).length;
    status.textContent =
      sphericalModeFromForm() === "directed"
        ? count
          ? `${count} recorded take${count === 1 ? "" : "s"} available. Directed mode will use covered recording ranges.`
          : "No recorded take yet. Open Director to record one before exporting."
        : count
          ? `${count} recorded take${count === 1 ? "" : "s"} saved, but Automatic mode will use shot angles.`
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
  if (zoom && fov != null) zoom.value = String(clamp(fov, 65, 150));
  if (frequency) frequency.value = weightToFrequency(weight);
  updateRawSummary(group);
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
  const [THREE, media] = await Promise.all([import("/vendor/three.module.min.js"), api("/wizard/director-media")]);
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
  directorStatus("WebGL active. Drag the view while the song plays, then record a take.");
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
      reject(new Error("Could not load Director media"));
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
    director.yaw = normalizeYaw(director.yaw - dx * 0.16) ?? 0;
    director.pitch = clamp(director.pitch + dy * 0.12, -85, 85);
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
  const duration = Number(video.duration || director.media?.duration_sec || 0);
  const next = clamp(time, 0, duration || 0);
  video.currentTime = next;
  audio.currentTime = masterTimeForDirectorVideo(next);
  updateDirectorScrub();
}

function syncDirectorAudio(force = false) {
  const video = document.querySelector("#directorVideo");
  const audio = document.querySelector("#directorAudio");
  if (!video || !audio || !director.media || Number.isNaN(video.currentTime)) return;
  const target = masterTimeForDirectorVideo(video.currentTime);
  if (force || Math.abs((audio.currentTime || 0) - target) > 0.08) {
    audio.currentTime = Math.max(0, Math.min(Number(audio.duration || target), target));
  }
  if (audio.paused !== video.paused) {
    if (video.paused) audio.pause();
    else audio.play().catch(() => {});
  }
}

function masterTimeForDirectorVideo(videoTime) {
  return Math.max(0, Number(director.media?.offset_sec || 0) + Number(videoTime || 0));
}

function updateDirectorScrub() {
  const video = document.querySelector("#directorVideo");
  const scrub = document.querySelector("#directorScrub");
  if (!video || !scrub) return;
  const duration = Number(video.duration || director.media?.duration_sec || 0);
  scrub.max = String(Math.max(0.01, duration));
  if (document.activeElement !== scrub) scrub.value = String(video.currentTime || 0);
  document.querySelector("#directorTime").textContent = secondsToTime(masterTimeForDirectorVideo(video.currentTime || 0));
}

async function toggleDirectorRecording() {
  if (director.recording) {
    await stopDirectorRecording(true);
    return;
  }
  director.samples = [];
  director.recording = true;
  document.querySelector("#directorRecord").textContent = "Stop recording";
  directorStatus("Recording camera moves...");
  sampleDirectorCamera();
  director.recordTimer = setInterval(sampleDirectorCamera, 1000 / 15);
}

function sampleDirectorCamera() {
  const video = document.querySelector("#directorVideo");
  if (!director.recording || !video || video.paused) return;
  const t = masterTimeForDirectorVideo(video.currentTime || 0);
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
  const nameInput = document.querySelector("#directorTakeName");
  const fallback = `Take ${new Date().toISOString().slice(0, 19).replace("T", " ").replaceAll(":", ".")}`;
  const result = await api("/wizard/camera-moves", {
    method: "POST",
    body: JSON.stringify({
      name: nameInput.value || fallback,
      source_path: director.media?.source_path || "",
      samples: director.samples,
    }),
  });
  nameInput.value = "";
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
    preview.src = `/api/v1/wizard/master-preview?t=${Date.now()}`;
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
  if (waitForPrepare) await waitForPreparedProject();
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
      spherical_mode: sphericalModeFromForm(),
      master: inputs.master,
      songs: inputs.songs,
      videos: inputs.videos,
    }),
  });
  ensureStatusPolling();
  await pollStatus();
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

function renderWizardStatus(status) {
  latestStatus = status;
  const progress = Number(status.progress || 0);
  updateTiming(status, progress);
  renderStatusStrip(status, progress);
  document.querySelector("#progressBar").style.width = `${progress}%`;
  document.querySelector("#progressPercent").textContent = `${Math.round(progress)}%`;
  document.querySelector("#progressMessage").textContent = status.message || S.working;
  document.querySelector("#progressDetail").textContent = status.detail || currentSubtask(status) || S.nextStep;
  document.querySelector("#elapsedTime").textContent = `${S.elapsed}: ${formatElapsed(elapsedSeconds())}`;
  document.querySelector("#etaTime").textContent = `${S.eta}: ${formatEta(etaSeconds(progress))}`;
  updateStageChecks(progress, status);
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
    document.querySelector("#resultVideo").src = `${latestResult.media_url}?t=${Date.now()}`;
    document.querySelector("#errorBox").hidden = true;
    document.querySelector("#resultBox").hidden = false;
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
  document.querySelector("#statusStripText").textContent = `${detail} · ${Math.round(progress)}% · ${formatEta(etaSeconds(progress))}`;
}

function currentSubtask(status) {
  return String(status.detail || "").replace(/^(.+?) — /, "$1 · ");
}

function updateTiming(status, progress) {
  if (status.status !== "running") return;
  const now = Date.now();
  const previous = progressSamples[progressSamples.length - 1];
  if (!progressStartedAt || progress < (previous?.progress || 0)) {
    progressStartedAt = now;
    progressSamples = [];
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

function etaSeconds(progress) {
  const elapsed = elapsedSeconds();
  if (elapsed < 30 || progress <= 0 || progress >= 100 || progressSamples.length < 2) return null;
  const first = progressSamples[0];
  const last = progressSamples[progressSamples.length - 1];
  const rate = (last.progress - first.progress) / ((last.time - first.time) / 1000);
  if (rate <= 0) return null;
  return (100 - progress) / rate;
}

function formatElapsed(seconds) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s`;
  return `${Math.round(seconds / 60)} min`;
}

function formatEta(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return S.calculating;
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
  const recordingSummary =
    recordedCount || landmarkCount ? `360 source: ${recordedCount} segments from recorded take, ${landmarkCount} from landmark shots.` : "";
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
  if (target.id === "showFinder") {
    if (!latestResult?.path) {
      showToast("The exported file path is not available yet", true);
      return;
    }
    revealNative(latestResult?.path, S.reveal).catch((error) => showToast(error.message, true));
  }
  if (target.id === "statusStrip") setStep(3);
  const rescueButton = target.closest?.("[data-rescue]");
  if (rescueButton instanceof HTMLElement) {
    openRescue(rescueButton.dataset.rescue, rescueButton.dataset.offset).catch((error) => showToast(error.message, true));
  }
  if (target.closest?.("#confirmRescue")) {
    confirmRescue().catch((error) => showToast(error.message, true));
  }
});

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

document.querySelector("#videoName").value = todayName();

async function boot() {
  injectIcons();
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
