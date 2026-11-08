// "Your idea for this video", asked before anything is planned: written, pasted, imported
// from a document or dictated. The director follows it first and adds its own ideas around
// it ("With my idea"); "Fully automatic" plans without it. Dictation is transcribed on
// this Mac (local Whisper); nothing is uploaded.
(() => {
  const $ = (selector) => document.querySelector(selector);
  let recorder = null, chunks = [], source = "typed";
  const status = (text) => { const line = $("#showIdeaStatus"); if (line) line.textContent = text; };
  const mode = () => document.querySelector('[name="showIdeaMode"]:checked')?.value || "together";
  function sync() {
    const together = mode() === "together";
    $("#showIdeaBody").hidden = !together;
  }
  function append(text) {
    const box = $("#showIdeaText");
    box.value = box.value.trim() ? `${box.value.trim()}\n\n${text}` : text;
    box.dispatchEvent(new Event("input"));
  }
  async function form(path, field, file, name) {
    const data = new FormData();
    data.append(field, file, name);
    const response = await fetch(`/api/v1${path}`, { method: "POST", body: data });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error?.message || "Error");
    return result;
  }
  async function dictate(button) {
    if (recorder) { recorder.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { status("The microphone is not available here: write or import your idea."); return; }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const type = ["audio/mp4", "audio/webm", "audio/ogg"].find((kind) => MediaRecorder.isTypeSupported(kind)) || "";
    recorder = new MediaRecorder(stream, type ? { mimeType: type } : {});
    chunks = [];
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      const kind = recorder.mimeType || type || "audio/webm";
      recorder = null;
      button.textContent = "🎙 Dictate";
      status("Writing down what you said (on this Mac)…");
      try {
        const result = await form("/show/idea/dictate", "audio", new Blob(chunks, { type: kind }), `idea.${kind.includes("mp4") ? "m4a" : kind.includes("ogg") ? "ogg" : "webm"}`);
        append(result.text); source = "dictated";
        status("Done. Read it over and change anything before you start.");
      } catch (error) { status(error.message); }
    };
    recorder.start();
    button.textContent = "■ Stop dictating";
    status("● Recording: tell us your idea, then press Stop.");
  }
  async function importDocument(input) {
    const file = input.files[0];
    if (!file) return;
    try { append((await form("/show/idea/document", "file", file, file.name)).text); source = "document"; status(`${file.name} imported. Read it over before you start.`); }
    catch (error) { status(error.message); }
    input.value = "";
  }
  async function load() {
    try {
      const brief = await (await fetch("/api/v1/show/idea")).json();
      if (brief.idea && !$("#showIdeaText").value) $("#showIdeaText").value = brief.idea;
      if (brief.mode === "automatic" && brief.updated) document.querySelector('[name="showIdeaMode"][value="automatic"]').checked = true;
      sync();
    } catch { /* no project yet */ }
  }
  document.addEventListener("change", (event) => {
    if (event.target.matches?.('[name="showIdeaMode"]')) sync();
    if (event.target.id === "showIdeaFile") importDocument(event.target);
  });
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("#showIdeaDictate") : null;
    if (button) dictate(button).catch((error) => status(error.message));
  });
  // Filled in from the project each time the Show options appear.
  const options = $("#showOptions");
  if (options) new MutationObserver(() => { if (!options.hidden) load(); }).observe(options, { attributes: true, attributeFilter: ["hidden"] });
  window.ShowIdea = {
    ready: () => mode() === "automatic" || $("#showIdeaText").value.trim().length >= 3,
    ask() {
      const box = $("#showIdea");
      box.classList.add("show-idea-asking");
      box.scrollIntoView({ block: "center", behavior: "smooth" });
      $("#showIdeaText").focus();
      status("Tell us your idea first (write, paste, import or dictate it), or choose “Fully automatic”.");
    },
    payload: () => ({ show_idea_mode: mode(), show_idea: mode() === "together" ? $("#showIdeaText").value : "", show_idea_source: source }),
  };
})();
