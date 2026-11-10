// Storytelling setup: the narration (an audio of the story) and the pictures and clips it is
// told over. Kept in the project (settings.wizard.storytelling).
(() => {
  let story = { narration: null, clips: [] }, saving = null;
  const box = () => document.querySelector("#showStory");
  const fileName = path => String(path || "").split(/[\\/]/).pop();
  const status = text => { const el = document.querySelector("#showStoryStatus"); if (el) el.textContent = text; };
  async function pick(label) {
    if (window.NativeBridge?.call && window.pywebview?.api) { try { return await window.NativeBridge.call("pick_videos", [], label); } catch { return []; } }
    return (prompt(`${label}: full paths, one per line`) || "").split("\n").map(line => line.trim()).filter(Boolean);
  }
  function render() {
    const el = box(); if (!el) return;
    el.querySelector("[data-story-narration-name]").textContent = story.narration ? `🎙 ${fileName(story.narration)}` : "No narration yet: an audio (or a video) of the story told.";
    const clips = story.clips || [];
    el.querySelector("[data-story-clips]").textContent = clips.length ? `${clips.length} pictures and clips: ${clips.slice(0, 4).map(fileName).join(", ")}${clips.length > 4 ? "…" : ""}` : "None yet.";
  }
  async function load() {
    try { story = await api("/show/story"); } catch (error) { status(error.message); }
    render();
  }
  function save() {
    clearTimeout(saving);
    saving = setTimeout(async () => {
      try { story = await api("/show/story", { method: "PUT", body: JSON.stringify(story) }); status("Saved."); render(); }
      catch (error) { status(error.message); }
    }, 200);
  }
  function attach() {
    const el = box(); if (!el || el.dataset.ready) return;
    el.dataset.ready = "1";
    el.querySelector("[data-story-narration]").onclick = async () => {
      const [path] = await pick("The narration (audio or video)");
      if (path) { story.narration = path; story.clips = (story.clips || []).filter(clip => clip !== path); render(); save(); }
    };
    el.querySelector("[data-story-add]").onclick = async () => {
      const paths = await pick("Pictures and clips");
      story.clips = [...new Set([...(story.clips || []), ...paths])];
      render(); save();
    };
  }
  function sync() {
    const el = box(); if (!el) return;
    const on = document.querySelector("#showStyle")?.value === "storytelling";
    el.hidden = !on;
    if (on) { document.querySelector(".show-assistant")?.setAttribute("hidden", ""); attach(); load(); }
    else if (document.querySelector("#showStyle")?.value !== "podcast") document.querySelector(".show-assistant")?.removeAttribute("hidden");
  }
  document.addEventListener("change", event => { if (event.target.id === "showStyle") sync(); });
  document.addEventListener("DOMContentLoaded", sync);
  window.ShowStory = { sync, ready: () => Boolean(story.narration) && (story.clips || []).length > 0 };
})();
