const stages = ["ingest", "sync", "cut", "edit", "export"];
let project = null;
let syncMap = null;
let appConfig = { dev: true, desktop: false, inbox_path: "~/ZuckerVideos/Inbox" };
let detectedInputs = { inbox_path: null, master: [], songs: [], videos: [], ignored: [] };
let songSuggestions = [];
let dropStatusTimer = null;
const uploadableDropExtensions = new Set([".mp4", ".mov", ".mts", ".m4v", ".wav", ".mp3", ".flac", ".aiff", ".aif", ".json"]);
const maxBrowserUploadBytes = 512 * 1024 * 1024;
const pickerMethods = {
  chooseMaster: ["pick_master", "master"],
  chooseSongs: ["pick_songs", "songs"],
  addVideos: ["pick_videos", "videos"],
  addVideoFolder: ["pick_video_folder", "folder"],
};
const stageRequirementHints = {
  ingest: "needs at least one video",
  sync: "needs ingest done + master",
  cut: "needs sync done + songs.json from Zucker Mixer",
  edit: "needs cut done + songs.json",
  export: "needs edit done + songs.json",
};

async function api(path, options = {}) {
  const response = await window.UiFeedback.request(`/api/v1${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || "Request failed");
  return data;
}

async function apiForm(path, formData) {
  const response = await window.UiFeedback.request(`/api/v1${path}`, { method: "POST", body: formData });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || "Request failed");
  return data;
}

function showToast(message, isError = false) {
  window.UiFeedback.message(message, isError);
}

function showError(error) {
  showToast(error.message, true);
}

function setDropStatus(message, options = {}) {
  const root = document.querySelector("#dropProgress");
  if (dropStatusTimer) {
    clearTimeout(dropStatusTimer);
    dropStatusTimer = null;
  }
  if (!message) {
    root.hidden = true;
    root.innerHTML = "";
    return;
  }
  const percent = options.percent;
  root.hidden = false;
  root.innerHTML = `
    <strong>${escapeHtml(message)}</strong>
    ${options.detail ? `<span>${escapeHtml(options.detail)}</span>` : ""}

  `;
  root.append(window.MeasuredProgress.create(percent, message));
  if (options.autoHide) {
    dropStatusTimer = setTimeout(() => setDropStatus(null), options.autoHide);
  }
}

function setPickerAvailability() {
  const desktopMode = Boolean(appConfig.desktop);
  document.querySelectorAll(".desktop-only").forEach((button) => {
    button.disabled = !desktopMode;
    if (!desktopMode) {
      button.title = "Native pickers are available in the bundled desktop app. Use drag and drop or the Inbox in browser dev mode.";
    } else {
      button.title = "";
    }
  });
  window.NativeBridge?.updateDesktopOnlyAvailability();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function filename(path) {
  return String(path || "").split(/[\\/]/).pop();
}

function secondaryPath(path) {
  return String(path || "").replace(/^\/Users\/[^/]+/, "~");
}

function formatTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = String(Math.floor(total / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const secs = String(total % 60).padStart(2, "0");
  return `${hours}:${minutes}:${secs}`;
}

function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes) || 0;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function renderCacheStatus(status) {
  const root = document.querySelector("#cacheSummary");
  if (!root) return;
  const counts = status.counts || {};
  root.textContent = `${formatBytes(status.size_bytes || status.after_bytes || 0)} · ${counts.normalized || 0} prepared videos · ${counts.envelopes || 0} envelopes · ${status.path || ""}`;
}

async function refreshCacheStatus() {
  renderCacheStatus(await api("/cache/status"));
}

async function freeCache() {
  const result = await api("/cache/free", { method: "POST", body: "{}" });
  renderCacheStatus({ ...result, size_bytes: result.after_bytes, counts: (await api("/cache/status")).counts });
  showToast(`Freed cache: ${formatBytes(result.deleted_bytes || 0)} across ${result.deleted_files || 0} files`);
}

function parseOffset(value) {
  const trimmed = value.trim();
  if (trimmed.includes(":")) {
    const parts = trimmed.split(":").map(Number);
    if (parts.some(Number.isNaN)) throw new Error("Invalid time value");
    while (parts.length < 3) parts.unshift(0);
    return parts[0] * 3600 + parts[1] * 60 + parts[2];
  }
  const seconds = Number(trimmed);
  if (Number.isNaN(seconds)) throw new Error("Invalid offset value");
  return seconds;
}

function setActiveTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === name));
}

async function loadAppConfig() {
  appConfig = await api("/app/config");
  window.NativeBridge?.configure(appConfig);
  document.querySelector("#inboxPath").textContent = appConfig.inbox_path;
  setPickerAvailability();
  document.querySelector("#dropMode").textContent = appConfig.desktop
    ? "Desktop drops use local paths. Folder drops recurse for video files."
    : "Browser dev drops upload smaller files. Put large videos in the Inbox or use the desktop app.";
}

function renderProject() {
  document.querySelector("#projectSummary").textContent = project
    ? `${project.name} · ${project.modified_at}`
    : "No project open";
  document.querySelector("#copyIntoProject").checked = Boolean(project?.settings?.inputs?.copy_into_project);
  renderRegisteredInputs();
}

function renderRegisteredInputs() {
  const root = document.querySelector("#registeredInputs");
  const inputs = project?.inputs || {};
  const groups = [
    ["Master", "needed for sync", inputs.master ? [inputs.master] : []],
    ["Songs", "needed for cut", inputs.songs ? [inputs.songs] : []],
    ["Videos", "needed for ingest", inputs.videos || []],
  ];
  root.innerHTML = groups
    .map(([label, hint, records]) => {
      if (!records.length) return `<div class="registered-group"><h3>${label}<span>${hint}</span></h3><p>None registered</p></div>`;
      return `<div class="registered-group"><h3>${label}<span>${hint}</span></h3>${records
        .map(
          (record) => `
            <div class="file-row ${record.missing ? "missing" : ""}" title="${escapeHtml(record.path)}">
              <strong>${escapeHtml(filename(record.path))}</strong>
              <span>${escapeHtml(secondaryPath(record.path))}</span>
              ${record.missing ? '<span class="badge danger">missing file</span>' : ""}
            </div>`
        )
        .join("")}</div>`;
    })
    .join("");
}

async function refreshProject() {
  try {
    project = await api("/project");
  } catch {
    project = null;
  }
  renderProject();
}

async function refreshPipelineState() {
  await refreshProject();
  await refreshStatus();
}

let inboxScan = null;
function scanInbox() {
  if (!inboxScan) inboxScan = api("/inbox").then(result => {
    detectedInputs = result;
    renderDetectedInputs();
  }).finally(() => { inboxScan = null; });
  return inboxScan;
}

function mergeDetected(result) {
  for (const key of ["master", "songs", "videos", "ignored"]) {
    const existing = new Set(detectedInputs[key].map((item) => item.path));
    for (const item of result[key] || []) {
      if (!existing.has(item.path)) detectedInputs[key].push(item);
    }
  }
  renderDetectedInputs();
}

function renderDetectedInputs() {
  const root = document.querySelector("#detectedInputs");
  root.innerHTML = "";
  for (const [key, title] of [
    ["master", "Master candidates"],
    ["songs", "Songs JSON"],
    ["videos", "Video clips"],
    ["ignored", "Ignored"],
  ]) {
    const section = document.createElement("section");
    section.className = "detected-group";
    const items = detectedInputs[key] || [];
    section.innerHTML = `<h3>${title} <span>${items.length}</span></h3>`;
    if (!items.length) {
      section.innerHTML += `<p>No files found</p>`;
    } else {
      section.innerHTML += items.map((item, index) => renderDetectedItem(key, item, index)).join("");
    }
    root.appendChild(section);
  }
}

async function refreshSongSuggestions() {
  if (!project?.inputs?.master || project?.inputs?.songs) {
    songSuggestions = [];
    renderSongSuggestions();
    return;
  }
  const result = await api("/inputs/suggestions/songs");
  songSuggestions = result.songs || [];
  renderSongSuggestions();
}

function renderSongSuggestions() {
  const root = document.querySelector("#songSuggestions");
  if (!songSuggestions.length || project?.inputs?.songs) {
    root.hidden = true;
    root.innerHTML = "";
    return;
  }
  root.hidden = false;
  root.innerHTML = `
    <strong>Found songs.json candidates</strong>
    ${songSuggestions
      .map(
        (item, index) => `
          <div class="suggestion-row" title="${escapeHtml(item.path)}">
            <span>${escapeHtml(item.filename)}<small>${escapeHtml(item.note)} · ${escapeHtml(secondaryPath(item.path))}</small></span>
            <button data-use-song-suggestion="${index}">Use this</button>
          </div>`
      )
      .join("")}
  `;
}

function renderDetectedItem(group, item, index) {
  const inputType = group === "master" ? "radio" : "checkbox";
  const disabled = group === "ignored" ? "disabled" : "";
  const checked = group === "master" && detectedInputs.master.length > 1 ? "" : item.checked === false ? "" : "checked";
  return `
    <label class="detected-row" title="${escapeHtml(item.path)}">
      <input ${inputType} name="${group === "master" ? "master-choice" : `detected-${group}-${index}`}" data-detected-group="${group}" data-detected-index="${index}" ${checked} ${disabled}>
      <span>
        <strong>${escapeHtml(item.filename || filename(item.path))}</strong>
        <small>${escapeHtml(item.note || "")} · ${escapeHtml(secondaryPath(item.path))}</small>
      </span>
    </label>`;
}

function selectedDetected() {
  const selected = { master: null, songs: null, videos: [] };
  const masterInputs = [...document.querySelectorAll('[data-detected-group="master"]')];
  const selectedMaster = masterInputs.find((input) => input.checked);
  if (selectedMaster) selected.master = detectedInputs.master[Number(selectedMaster.dataset.detectedIndex)].path;
  if (detectedInputs.master.length > 1 && !selected.master) {
    throw new Error("Pick one master candidate before registering");
  }
  const selectedSongs = [...document.querySelectorAll('[data-detected-group="songs"]')].find((input) => input.checked);
  if (selectedSongs) selected.songs = detectedInputs.songs[Number(selectedSongs.dataset.detectedIndex)].path;
  for (const input of document.querySelectorAll('[data-detected-group="videos"]')) {
    if (input.checked) selected.videos.push(detectedInputs.videos[Number(input.dataset.detectedIndex)].path);
  }
  return selected;
}

async function registerDetected() {
  const selected = selectedDetected();
  project = await api("/inbox/register", { method: "POST", body: JSON.stringify(selected) });
  renderProject();
  setActiveTab("pipeline");
  await refreshPipelineState();
  if (selected.master && !selected.songs) await refreshSongSuggestions();
  const bits = [];
  if (selected.videos.length) bits.push(`${selected.videos.length} videos`);
  if (selected.master) bits.push("master");
  if (selected.songs) bits.push("songs");
  showToast(`${bits.join(", ")} registered - ready to run`);
}

async function registerPicked(paths, kind) {
  if (!paths?.length) return;
  if (kind === "master") {
    project = await api("/inputs/master", { method: "POST", body: JSON.stringify({ master: paths[0] }) });
  } else if (kind === "songs") {
    project = await api("/inputs/master", { method: "POST", body: JSON.stringify({ songs: paths[0] }) });
  } else if (kind === "videos") {
    project = await api("/inputs/videos", { method: "POST", body: JSON.stringify({ paths, append: true }) });
  } else if (kind === "folder") {
    const result = await api("/inputs/classify-paths", { method: "POST", body: JSON.stringify({ paths }) });
    mergeDetected(result);
    return;
  }
  renderProject();
  setActiveTab("pipeline");
  await refreshPipelineState();
  if (kind === "master") await refreshSongSuggestions();
  showToast(`${paths.length} item${paths.length === 1 ? "" : "s"} registered - ready to run`);
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
  const reader = entry.createReader();
  const entries = await readAllDirectoryEntries(reader);
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

function uploadFiles(files) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    const request = new XMLHttpRequest();
    request.open("POST", "/api/v1/inputs/upload");
    request.timeout = 10 * 60 * 1000;
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        setDropStatus("Uploading dropped files", { detail: `${files.length} file${files.length === 1 ? "" : "s"}` });
        return;
      }
      const percent = Math.round((event.loaded / event.total) * 100);
      setDropStatus("Uploading dropped files", {
        detail: `${formatBytes(event.loaded)} of ${formatBytes(event.total)}`,
        percent,
      });
    };
    request.onload = () => {
      let payload = {};
      try {
        payload = JSON.parse(request.responseText || "{}");
      } catch {
        payload = {};
      }
      if (request.status >= 200 && request.status < 300) {
        resolve(payload);
        return;
      }
      reject(new Error(payload.error?.message || `Upload failed with HTTP ${request.status}`));
    };
    request.onerror = () => reject(new Error("Upload failed because the network request failed"));
    request.ontimeout = () => reject(new Error("Upload timed out. Put large videos in the Inbox or use the desktop app."));
    request.send(form);
  });
}

async function callPicker(buttonId) {
  if (!appConfig.desktop) {
    throw new Error("Native pickers are only available in the bundled desktop app. Use drag and drop or the Inbox in browser dev mode.");
  }
  const [methodName, kind] = pickerMethods[buttonId] || [];
  if (!methodName || !kind) throw new Error(`Unknown picker: ${buttonId}`);
  let paths;
  paths = await window.NativeBridge.call(methodName, [], "Native picker");
  if (!Array.isArray(paths)) {
    throw new Error(`Native picker returned an invalid result for ${methodName}`);
  }
  await registerPicked(paths, kind);
}

async function handleDrop(event) {
  event.preventDefault();
  document.querySelector("#dropZone").classList.remove("dragging");
  setDropStatus("Reading dropped items");
  const paths = [...event.dataTransfer.files]
    .map((file) => file.path || file.webkitRelativePath)
    .filter(Boolean);
  if (paths.length) {
    const result = await api("/inputs/classify-paths", { method: "POST", body: JSON.stringify({ paths }) });
    mergeDetected(result);
    const count = (result.master?.length || 0) + (result.songs?.length || 0) + (result.videos?.length || 0);
    setDropStatus(`Classified ${count} dropped item${count === 1 ? "" : "s"}`, { autoHide: 3000 });
    return;
  }
  if (!project) throw new Error("Open or create a project before browser uploads");
  const files = await browserDropFiles(event.dataTransfer);
  if (!files.length) {
    setDropStatus("No supported files found in the drop", { autoHide: 4000 });
    showToast("No supported video, audio, or JSON files found in that drop", true);
    return;
  }
  const tooLarge = files.filter((file) => file.size > maxBrowserUploadBytes);
  if (tooLarge.length) {
    const names = tooLarge.slice(0, 3).map((file) => `${file.name} (${formatBytes(file.size)})`).join(", ");
    throw new Error(`Browser uploads are limited to ${formatBytes(maxBrowserUploadBytes)} per file. Use the Inbox or desktop folder picker for: ${names}`);
  }
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  setDropStatus("Preparing upload", { detail: `${files.length} file${files.length === 1 ? "" : "s"} · ${formatBytes(totalBytes)}` });
  mergeDetected(await uploadFiles(files));
  setDropStatus(`Uploaded and classified ${files.length} file${files.length === 1 ? "" : "s"}`, { percent: 100, autoHide: 3500 });
  showToast(`${files.length} dropped file${files.length === 1 ? "" : "s"} copied into project`);
}

async function refreshSyncMap() {
  try {
    syncMap = await api("/artifacts/sync");
  } catch {
    syncMap = null;
  }
  renderSyncReview();
}

function renderSyncReview() {
  renderTimeline();
  const rows = document.querySelector("#syncTable");
  rows.innerHTML = "";
  const clips = syncMap?.clips || {};
  for (const [clipId, clip] of Object.entries(clips)) {
    const tr = document.createElement("tr");
    const badges = [];
    if (clip.low_confidence) badges.push("low confidence");
    if (clip.no_audio) badges.push("no audio");
    if (clip.manual_override) badges.push("manual");
    if (clip.error) badges.push("error");
    tr.innerHTML = `
      <td><img class="thumb" src="/api/v1/stages/sync/thumbnail/${clipId}" alt="" loading="lazy"></td>
      <td title="${escapeHtml(clip.path)}">${escapeHtml(clip.filename || filename(clip.path))}<small>${escapeHtml(secondaryPath(clip.path))}</small></td>
      <td><strong>${formatTime(clip.offset_sec)}</strong>${clip.manual_override ? `<small>detected ${formatTime(clip.detected_offset_sec)}</small>` : ""}</td>
      <td><span class="confidence ${confidenceClass(clip)}">${Number(clip.confidence || 0).toFixed(2)}</span></td>
      <td>${badges.map((badge) => `<span class="badge">${escapeHtml(badge)}</span>`).join(" ")}</td>
      <td>
        <div class="sync-controls">
          <button data-preview="${clipId}">Preview</button>
          <input data-offset-input="${clipId}" value="${Number(clip.offset_sec || 0).toFixed(3)}" aria-label="Offset">
          <button data-override="${clipId}">Adjust</button>
          <button data-clear-override="${clipId}">Clear</button>
        </div>
      </td>`;
    rows.appendChild(tr);
  }
}

function confidenceClass(clip) {
  if (clip.error || clip.no_audio) return "bad";
  if (clip.low_confidence) return "warn";
  return "ok";
}

function renderTimeline() {
  const timeline = document.querySelector("#syncTimeline");
  timeline.innerHTML = "";
  if (!syncMap) {
    timeline.textContent = "No sync map yet";
    return;
  }
  const duration = Number(syncMap.master_duration_sec || 0);
  const ruler = document.createElement("div");
  ruler.className = "timeline-ruler";
  const track = document.createElement("div");
  track.className = "timeline-track";
  timeline.append(ruler, track);
  for (const song of syncMap.songs || []) {
    const start = percent(song.start_sec, duration);
    const end = song.end_sec == null ? start : percent(song.end_sec, duration);
    const marker = document.createElement("div");
    marker.className = "song-marker";
    marker.style.left = `${start}%`;
    marker.style.width = `${Math.max(0.4, end - start)}%`;
    marker.title = `${song.title} ${formatTime(song.start_sec)}`;
    ruler.appendChild(marker);
  }
  for (const [clipId, clip] of Object.entries(syncMap.clips || {})) {
    const block = document.createElement("button");
    block.className = `clip-block ${confidenceClass(clip)}`;
    block.style.left = `${percent(clip.offset_sec, duration)}%`;
    block.style.width = `${Math.max(0.6, percent(clip.duration_sec, duration))}%`;
    block.title = `${clip.filename || clipId} @ ${formatTime(clip.offset_sec)}`;
    block.dataset.preview = clipId;
    track.appendChild(block);
  }
}

function percent(value, duration) {
  if (!duration) return 0;
  return Math.max(0, Math.min(100, (Number(value || 0) / duration) * 100));
}

async function refreshStatus() {
  const data = await api("/stages/status");
  const list = document.querySelector("#stageList");
  list.innerHTML = "";
  for (const name of stages) {
    const state = data.stages[name] || { status: "pending" };
    const readiness = data.readiness?.[name] || { ready: false, reasons: [] };
    const reasonText = (readiness.reasons || []).join("; ");
    const readyText = readiness.ready ? `ready - ${stageRequirementHints[name]}` : `${reasonText} · ${stageRequirementHints[name]}`;
    const row = document.createElement("div");
    row.className = "stage-row";
    row.innerHTML = `<strong>${name}</strong><span class="chip ${state.status}">${state.status}</span><span>${escapeHtml(state.error || readyText)}</span><button data-run="${name}" ${readiness.ready ? "" : "disabled"}>Run</button>`;
    list.appendChild(row);
  }
  const progress = document.querySelector("#progress");
  const current = data.current;
  progress.hidden = !current;
  if (current) {
    document.querySelector("#progressBar").style.width = `${current.percent}%`;
    document.querySelector("#progressText").textContent = `${current.stage}: ${current.percent}% · ${current.message}`;
  }
  if (!data.busy && data.stages?.sync?.status === "done") await refreshSyncMap();
}

document.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (event.target.closest?.("#dropZone")) document.querySelector("#dropZone").classList.add("dragging");
});

document.addEventListener("dragleave", (event) => {
  if (event.target.closest?.("#dropZone")) document.querySelector("#dropZone").classList.remove("dragging");
});

document.addEventListener("drop", async (event) => {
  event.preventDefault();
  if (!event.target.closest?.("#dropZone")) return;
  try {
    await handleDrop(event);
  } catch (error) {
    setDropStatus(`Drop failed: ${error.message}`, { autoHide: 9000 });
    showError(error);
  }
});

document.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  try {
    if (target.matches(".tab")) {
      setActiveTab(target.dataset.tab);
      if (target.dataset.tab === "pipeline") await refreshPipelineState();
      if (target.dataset.tab === "sync") await refreshSyncMap();
      if (target.dataset.tab === "inputs") await scanInbox();
    }
    if (target.id === "createProject") {
      project = await api("/project", {
        method: "POST",
        body: JSON.stringify({
          name: document.querySelector("#projectName").value,
          folder: document.querySelector("#projectFolder").value,
        }),
      });
      renderProject();
      await scanInbox();
      await refreshSongSuggestions();
    }
    if (target.id === "openProject") {
      project = await api("/project/open", { method: "POST", body: JSON.stringify({ folder: document.querySelector("#openFolder").value }) });
      renderProject();
      await scanInbox();
      await refreshSongSuggestions();
      await refreshSyncMap();
    }
    if (target.id === "copyIntoProject") {
      project = await api("/settings/inputs", { method: "POST", body: JSON.stringify({ copy_into_project: target.checked }) });
      renderProject();
    }
    if (target.id === "revealInbox") {
      await window.NativeBridge.reveal(appConfig.inbox_path, "Reveal in Finder");
    }
    if (target.id === "rescanInbox") await scanInbox();
    if (target.id === "refreshCache") await refreshCacheStatus();
    if (target.id === "freeCache") await freeCache();
    if (target.id === "useDetected") await registerDetected();
    if (pickerMethods[target.id]) await callPicker(target.id);
    if (target.dataset.useSongSuggestion) {
      const item = songSuggestions[Number(target.dataset.useSongSuggestion)];
      if (!item) throw new Error("Song suggestion is no longer available");
      await registerPicked([item.path], "songs");
      songSuggestions = [];
      renderSongSuggestions();
    }
    if (target.dataset.run) {
      await api(`/stages/${target.dataset.run}/run`, { method: "POST", body: "{}" });
      await refreshStatus();
    }
    if (target.dataset.preview) {
      const result = await api(`/stages/sync/preview/${target.dataset.preview}`);
      const video = document.querySelector("#syncPreview");
      video.src = result.media_url;
      video.hidden = false;
      await video.play().catch(() => {});
    }
    if (target.dataset.override) {
      const input = document.querySelector(`[data-offset-input="${target.dataset.override}"]`);
      await api("/stages/sync/override", {
        method: "POST",
        body: JSON.stringify({ clip_id: target.dataset.override, offset_sec: parseOffset(input.value) }),
      });
      await refreshSyncMap();
      await refreshStatus();
    }
    if (target.dataset.clearOverride) {
      await api("/stages/sync/override/clear", { method: "POST", body: JSON.stringify({ clip_id: target.dataset.clearOverride }) });
      await refreshSyncMap();
      await refreshStatus();
    }
  } catch (error) {
    showError(error);
  }
});

async function boot() {
  await Promise.all([loadAppConfig(), refreshProject()]);
  await Promise.all([scanInbox(), refreshSongSuggestions(), refreshStatus(), refreshSyncMap(), refreshCacheStatus()]);
}

setInterval(async () => {
  if (document.querySelector("#inputs").classList.contains("active")) {
    await scanInbox().catch(() => {});
  }
}, 4000);

let statusRefresh = false;
setInterval(async () => {
  if (statusRefresh) return;
  statusRefresh = true;
  try { await refreshStatus(); } catch (error) { showError(error); }
  finally { statusRefresh = false; }
}, 1000);
boot().catch(showError);
