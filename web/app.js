const detected = { master: [], songs: [], videos: [], ignored: [] };
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

const icons = {
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 12h14"></path><path d="m13 6 6 6-6 6"></path></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"></path></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m8 5 11 7-11 7Z"></path></svg>',
  wand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m15 4 5 5"></path><path d="M14 5 3 16l5 5L19 10Z"></path><path d="M4 4h.01M9 2h.01M2 9h.01M20 15h.01M15 22h.01"></path></svg>',
  doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"></path><path d="M14 2v6h6"></path><path d="M8 13h8M8 17h8"></path></svg>',
  clipboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M8 4h8l1 2h3v14H4V6h3Z"></path><path d="M9 4a3 3 0 0 1 6 0"></path></svg>',
  retry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M20 12a8 8 0 1 1-2.34-5.66"></path><path d="M20 4v6h-6"></path></svg>',
  youtube: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="6" width="18" height="12" rx="4"></rect><path d="m10 9 5 3-5 3Z"></path></svg>',
  instagram: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="4" y="4" width="16" height="16" rx="5"></rect><circle cx="12" cy="12" r="3"></circle><path d="M17 7h.01"></path></svg>',
  tiktok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M14 4v10.5a3.5 3.5 0 1 1-3-3.46"></path><path d="M14 4c1 3 2.7 4.7 5 5"></path></svg>',
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
  if (!response.ok) throw new Error(data.error?.message || "Error");
  return data;
}

async function apiForm(path, formData) {
  const response = await fetch(`/api/v1${path}`, { method: "POST", body: formData });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || "Error");
  return data;
}

async function loadAppConfig() {
  appConfig = await api("/app/config");
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
  document.querySelectorAll(".step").forEach((step, index) => step.classList.toggle("active", index === number - 1));
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
    note: kind === "master" ? "audio master registrado" : kind === "songs" ? "songs.json registrado" : "vídeo preparado",
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
      detected.ignored.push({ ...recordToDetectedItem(record, "ignored"), note: record.not_a_video_reason || "ignorado" });
    } else {
      detected.videos.push(recordToDetectedItem(record, "videos"));
    }
  }
  chooseDefaultMaster();
  renderChips();
  document.querySelector("#videoName").value = project.name || document.querySelector("#videoName").value || todayName();
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

function filename(path) {
  return String(path || "").split(/[\\/]/).pop();
}

function renderChips() {
  const root = document.querySelector("#chips");
  const items = [...detected.videos, ...detected.master, ...detected.songs, ...detected.ignored];
  root.innerHTML = items
    .map(
      (item) => `
        <span class="chip ${item.kind === "ignored" ? "muted" : ""}" title="${escapeHtml(item.path)}">
          ${iconFor(item)} ${escapeHtml(item.filename || filename(item.path))}
          ${item.projection === "equirect" || item.probe?.projection === "equirect" ? "<small>360°</small>" : ""}
          ${item.source === "inbox" ? "<small>del Inbox</small>" : ""}
          ${item.kind === "ignored" ? `<small>${escapeHtml(item.note || "ignorado")}</small>` : ""}
        </span>`
    )
    .join("");
  if (detected.master.length > 1) {
    const selected = selectedMasterPath || detected.master[0].path;
    root.insertAdjacentHTML(
      "afterbegin",
      `<label class="master-select-chip">🎵 Audio master
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
  const note = document.querySelector("#softRule");
  const button = document.querySelector("#confirmFiles");
  button.disabled = !(hasVideo && hasMaster);
  if (!hasVideo) note.textContent = "Falta al menos un vídeo";
  else if (!hasMaster) note.textContent = "Falta el audio master";
  else if (!hasSongs) note.textContent = "Sin songs.json haré un solo vídeo continuo";
  else note.textContent = "Todo listo";
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
    showToast("No encontré archivos compatibles", true);
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
  if (inputs.songs) {
    const result = await api("/wizard/songs", { method: "POST", body: JSON.stringify({ songs: inputs.songs }) });
    renderSongOptions(result.songs || []);
  } else {
    renderSongOptions([]);
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
  const allOption = selectedPlatform === "youtube" ? `<button class="song-option selected" data-song="all">Todas</button>` : "";
  selectedSong = selectedPlatform === "youtube" ? "all" : 0;
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
    if (status.status === "failed") throw new Error(status.error || "No pude preparar los archivos");
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("La preparación tardó demasiado");
}

async function startWizard() {
  const inputs = selectedInputs();
  setStep(3);
  await waitForPreparedProject();
  await api("/wizard/start", {
    method: "POST",
    body: JSON.stringify({
      name: document.querySelector("#videoName").value || todayName(),
      platform: selectedPlatform,
      song_index: selectedSong,
      master: inputs.master,
      songs: inputs.songs,
      videos: inputs.videos,
    }),
  });
  ensureStatusPolling();
  await pollStatus();
}

function ensureStatusPolling() {
  if (!pollTimer) pollTimer = setInterval(pollStatus, 1000);
}

async function pollStatus() {
  const status = await api("/wizard/status");
  renderWizardStatus(status);
}

function renderWizardStatus(status) {
  latestStatus = status;
  const progress = Number(status.progress || 0);
  updateTiming(status, progress);
  renderStatusStrip(status, progress);
  document.querySelector("#progressBar").style.width = `${progress}%`;
  document.querySelector("#progressPercent").textContent = `${Math.round(progress)}%`;
  document.querySelector("#progressMessage").textContent = status.message || "Trabajando...";
  document.querySelector("#progressDetail").textContent = status.detail || currentSubtask(status) || "Calculando el siguiente paso...";
  document.querySelector("#elapsedTime").textContent = `Tiempo: ${formatElapsed(elapsedSeconds())}`;
  document.querySelector("#etaTime").textContent = `ETA: ${formatEta(etaSeconds(progress))}`;
  updateStageChecks(progress, status);
  if (status.status === "failed") {
    clearInterval(pollTimer);
    pollTimer = null;
    document.querySelector("#errorText").textContent = status.error || "No pude terminar";
    document.querySelector("#technicalDetails").textContent = status.technical_details || "";
    document.querySelector("#errorBox").hidden = false;
  }
  if (status.status === "done") {
    clearInterval(pollTimer);
    pollTimer = null;
    latestResult = status.result;
    document.querySelector("#progressTitle").textContent = "Tu vídeo está listo";
    document.querySelector("#resultFilename").textContent = latestResult.filename;
    document.querySelector("#resultVideo").src = `${latestResult.media_url}?t=${Date.now()}`;
    document.querySelector("#resultBox").hidden = false;
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
  const detail = currentSubtask(status) || status.message || "Trabajando";
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
  if (seconds == null || !Number.isFinite(seconds)) return "calculando...";
  return `~${Math.max(1, Math.round(seconds / 60))} min restantes`;
}

function updateStageChecks(progress, status) {
  const done = new Set();
  if (progress >= 22) done.add("ingest");
  if (progress >= 48 || status.status === "waiting_choice") done.add("sync");
  if (progress >= 64) done.add("cut");
  if (status.status === "done") done.add("export");
  document.querySelectorAll(".stage-checks [data-stage]").forEach((node) => {
    node.classList.toggle("done", done.has(node.dataset.stage));
  });
}

async function revealNative(path, label) {
  await window.NativeBridge.reveal(path, label);
}

async function openLogs() {
  const path = latestResult?.logs_path || latestStatus?.logs_path;
  await revealNative(path, "Abrir logs");
}

async function copyReport() {
  const response = await fetch("/api/v1/wizard/report");
  const text = await response.text();
  await navigator.clipboard.writeText(text);
  showToast("Informe copiado");
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
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.id === "confirmFiles") prepareStep2().catch((error) => showToast(error.message, true));
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
  if (target.id === "retryWizard") startWizard().catch((error) => showToast(error.message, true));
  if (target.id === "again") {
    document.querySelector("#errorBox").hidden = true;
    document.querySelector("#resultBox").hidden = true;
    document.querySelector("#progressTitle").textContent = "Creando tu vídeo";
    setStep(1);
  }
  if (target.id === "openLogsSuccess" || target.id === "openLogsError") {
    openLogs().catch((error) => showToast(error.message, true));
  }
  if (target.id === "copyReport") {
    copyReport().catch((error) => showToast(error.message, true));
  }
  if (target.id === "showFinder") {
    revealNative(latestResult?.path, "Mostrar en Finder").catch((error) => showToast(error.message, true));
  }
  if (target.id === "statusStrip") setStep(3);
});

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement && target.id === "masterSelect") {
    selectedMasterPath = target.value;
    renderChips();
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
  renderWizardStatus(status);
  if (status.status === "running") {
    setStep(3);
    ensureStatusPolling();
  } else if (status.status === "waiting_choice") {
    setStep(2);
  } else if (status.status === "done" || status.status === "failed") {
    setStep(3);
  }
}

boot().catch((error) => showToast(error.message, true));
