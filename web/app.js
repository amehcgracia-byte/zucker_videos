const detected = { master: [], songs: [], videos: [], ignored: [] };
const uploadableDropExtensions = new Set([".mp4", ".mov", ".mts", ".m4v", ".wav", ".mp3", ".flac", ".aiff", ".aif", ".json"]);
let selectedPlatform = null;
let selectedSong = null;
let currentSongs = [];
let latestResult = null;
let latestStatus = null;
let pollTimer = null;

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

function mergeDetected(result, source = "") {
  for (const key of ["master", "songs", "videos", "ignored"]) {
    const existing = new Set(detected[key].map((item) => item.path));
    for (const item of result[key] || []) {
      if (existing.has(item.path)) continue;
      detected[key].push({ ...item, source });
    }
  }
  renderChips();
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
          ${item.source === "inbox" ? "<small>del Inbox</small>" : ""}
        </span>`
    )
    .join("");
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
    master: detected.master[0]?.path || "",
    songs: detected.songs[0]?.path || "",
    videos: detected.videos.map((item) => item.path),
  };
}

async function loadInbox() {
  const result = await api("/inbox");
  mergeDetected(result, "inbox");
}

function supportedDropFile(file) {
  const name = file.name || "";
  const suffix = name.includes(".") ? `.${name.split(".").pop().toLowerCase()}` : "";
  return uploadableDropExtensions.has(suffix);
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
    if (status.status === "waiting_choice" || status.status === "idle") return;
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
  pollTimer = setInterval(pollStatus, 1000);
  await pollStatus();
}

async function pollStatus() {
  const status = await api("/wizard/status");
  latestStatus = status;
  document.querySelector("#progressBar").style.width = `${status.progress || 0}%`;
  document.querySelector("#progressMessage").textContent = status.message || "Trabajando...";
  document.querySelector("#progressDetail").textContent = status.detail || "";
  if (status.status === "failed") {
    clearInterval(pollTimer);
    document.querySelector("#errorText").textContent = status.error || "No pude terminar";
    document.querySelector("#technicalDetails").textContent = status.technical_details || "";
    document.querySelector("#errorBox").hidden = false;
  }
  if (status.status === "done") {
    clearInterval(pollTimer);
    latestResult = status.result;
    document.querySelector("#progressTitle").textContent = "Tu vídeo está listo";
    document.querySelector("#resultFilename").textContent = latestResult.filename;
    document.querySelector("#resultVideo").src = `${latestResult.media_url}?t=${Date.now()}`;
    document.querySelector("#resultBox").hidden = false;
  }
}

async function openLogs() {
  const path = latestResult?.logs_path || latestStatus?.logs_path;
  if (window.pywebview?.api && path) {
    await window.pywebview.api.reveal_in_finder(path);
    return;
  }
  showToast("Abrir logs está disponible en la app de escritorio", true);
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
    if (window.pywebview?.api && latestResult?.path) window.pywebview.api.reveal_in_finder(latestResult.path);
    else showToast("Disponible en la app de escritorio");
  }
});

document.querySelector("#videoName").value = todayName();
loadInbox().catch((error) => showToast(error.message, true));
