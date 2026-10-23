// Podcast setup: who speaks into which microphone, and what each camera shows. A normal camera
// shows the people it was pointed at; a 360 camera in the middle shows everyone, and each
// face found in it is told who it is. Saved in the project (artifacts/show_podcast.json).
(() => {
  let setup = { people: [], cameras: [] }, saving = null, loaded = false;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const box = () => document.querySelector("#showPodcast");
  const fileName = path => String(path || "").split(/[\\/]/).pop();
  const nameFrom = path => fileName(path).replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ").trim();
  const status = text => { const el = document.querySelector("#showPodcastStatus"); if (el) el.textContent = text; };

  async function pick(label) {
    // The desktop app picks files by path (nothing is copied); in a browser, paths are typed.
    if (window.NativeBridge?.call && window.pywebview?.api) {
      try { return await window.NativeBridge.call("pick_videos", [], label); } catch { return []; }
    }
    const typed = prompt(`${label}: full paths, one per line`);
    return (typed || "").split("\n").map(line => line.trim()).filter(Boolean);
  }

  async function load() {
    try { const data = await api("/show/podcast"); setup = { people: data.people || [], cameras: data.cameras || [] }; loaded = true; }
    catch (error) { status(error.message); }
    if (!setup.cameras.length) {
      // The project's videos are the cameras, to begin with.
      const videos = (typeof detected !== "undefined" ? detected.videos.map(item => item.path) : []).filter(Boolean);
      setup.cameras = videos.map(path => ({ path, kind: /\.insv$|360/i.test(path) ? "360" : "flat", people: [], seats: [] }));
    }
    render();
  }

  function save() {
    clearTimeout(saving);
    saving = setTimeout(async () => {
      try {
        const data = await api("/show/podcast", { method: "PUT", body: JSON.stringify(setup) });
        setup = { ...setup, people: data.people, cameras: data.cameras };
        status(`${setup.people.length} people, ${setup.cameras.length} cameras. Saved.`);
      } catch (error) { status(error.message); }
    }, 300);
  }

  function render() {
    const el = box(); if (!el) return;
    const people = setup.people, names = people.map((p, i) => p.name || `Person ${i + 1}`);
    el.querySelector("[data-podcast-people]").innerHTML = people.length ? people.map((person, i) => `
      <div class="podcast-row"><span class="podcast-dot" style="--hue:${(i * 57) % 360}"></span>
        <input data-person-name="${i}" value="${esc(person.name)}" placeholder="Name" aria-label="Name of the person on ${esc(fileName(person.mic))}">
        <small title="${esc(person.mic)}">🎙 ${esc(fileName(person.mic))}</small>
        <button type="button" class="icon-button small" data-person-remove="${i}" aria-label="Remove ${esc(person.name)}">Remove</button></div>`).join("")
      : '<p class="show-hint">One microphone track per person (from the recorder: one file each).</p>';
    el.querySelector("[data-podcast-cameras]").innerHTML = setup.cameras.length ? setup.cameras.map((camera, c) => `
      <div class="podcast-camera" data-camera="${c}">
        <div class="podcast-row"><strong title="${esc(camera.path)}">🎥 ${esc(fileName(camera.path))}</strong>
          <select data-camera-kind="${c}" aria-label="Kind of camera"><option value="flat" ${camera.kind !== "360" ? "selected" : ""}>Normal camera</option><option value="360" ${camera.kind === "360" ? "selected" : ""}>360 camera</option></select>
          <button type="button" class="icon-button small" data-camera-remove="${c}">Remove</button></div>
        ${camera.kind === "360" ? `
          <div class="podcast-row"><small>Who sits where is found by itself.</small><button type="button" class="icon-button small" data-find-seats="${c}">Look now and set it by hand…</button></div>
          <div class="podcast-seats">${(camera.seats || []).map((seat, s) => `<figure>
            <img src="/api/v1/show/podcast/view?path=${encodeURIComponent(camera.path)}&yaw=${seat.yaw}&pitch=${seat.pitch}" alt="Seat at ${Math.round(seat.yaw)}°" loading="lazy">
            <figcaption><select data-seat="${c}:${s}" aria-label="Who sits at ${Math.round(seat.yaw)}°"><option value="">Found by itself</option>${names.map((name, p) => `<option value="${p}" ${seat.person === p ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></figcaption></figure>`).join("")}</div>`
        : `<div class="podcast-row podcast-shows"><small>Shows (found by itself if none is ticked):</small>${names.map((name, p) => `<label><input type="checkbox" data-shows="${c}:${p}" ${(camera.people || []).includes(p) ? "checked" : ""}> ${esc(name)}</label>`).join("") || '<small>add the microphones first</small>'}</div>`}
      </div>`).join("") : '<p class="show-hint">Every camera of the recording: normal ones (usually two people each) and the 360 in the middle.</p>';
  }

  function attach() {
    const el = box(); if (!el || el.dataset.ready) return;
    el.dataset.ready = "1";
    el.querySelector("[data-add-mics]").onclick = async () => {
      const paths = await pick("Microphone tracks (one per person)");
      for (const path of paths) setup.people.push({ name: nameFrom(path), mic: path });
      render(); save();
    };
    el.querySelector("[data-add-cameras]").onclick = async () => {
      const paths = await pick("Camera videos");
      for (const path of paths) setup.cameras.push({ path, kind: /\.insv$|360/i.test(path) ? "360" : "flat", people: [], seats: [] });
      render(); save();
    };
    el.addEventListener("input", event => {
      const name = event.target.closest("[data-person-name]");
      if (name) { setup.people[Number(name.dataset.personName)].name = name.value; save(); }
    });
    el.addEventListener("change", event => {
      const t = event.target;
      if (t.matches("[data-person-name]")) { render(); return; }
      if (t.matches("[data-camera-kind]")) { setup.cameras[Number(t.dataset.cameraKind)].kind = t.value; render(); save(); }
      if (t.matches("[data-shows]")) {
        const [c, p] = t.dataset.shows.split(":").map(Number), camera = setup.cameras[c];
        camera.people = t.checked ? [...new Set([...(camera.people || []), p])] : (camera.people || []).filter(x => x !== p);
        save();
      }
      if (t.matches("[data-seat]")) {
        const [c, s] = t.dataset.seat.split(":").map(Number);
        setup.cameras[c].seats[s].person = t.value === "" ? null : Number(t.value);
        save();
      }
    });
    el.addEventListener("click", async event => {
      const t = event.target;
      if (t.matches("[data-person-remove]")) {
        const gone = Number(t.dataset.personRemove);
        setup.people.splice(gone, 1);
        // Who each camera shows is kept for everyone else.
        for (const camera of setup.cameras) {
          camera.people = (camera.people || []).filter(p => p !== gone).map(p => p > gone ? p - 1 : p);
          for (const seat of camera.seats || []) seat.person = seat.person === gone ? null : seat.person > gone ? seat.person - 1 : seat.person;
        }
        render(); save();
      }
      if (t.matches("[data-camera-remove]")) { setup.cameras.splice(Number(t.dataset.cameraRemove), 1); render(); save(); }
      if (t.matches("[data-find-seats]")) {
        const camera = setup.cameras[Number(t.dataset.findSeats)];
        t.disabled = true; status("Looking for the people around the 360 camera… (a minute)");
        try {
          await api("/show/podcast", { method: "PUT", body: JSON.stringify(setup) });
          const { seats } = await api("/show/podcast/seats", { method: "POST", body: JSON.stringify({ path: camera.path }) });
          camera.seats = seats.map(seat => ({ ...seat, person: null }));
          status(seats.length ? `${seats.length} places found. Leave them as "found by itself" or say who sits at each one.` : "No faces found: is it the 360 camera?");
          render(); save();
        } catch (error) { status(error.message); }
        t.disabled = false;
      }
    });
  }

  function sync() {
    const kind = document.querySelector("#showStyle")?.value;
    const el = box(); if (!el) return;
    const podcast = kind === "podcast";
    el.hidden = !podcast;
    // A podcast is automatic: no idea, effects or review; only whether it has subtitles.
    document.querySelectorAll(".show-assistant, .show-not-podcast").forEach(node => node.toggleAttribute("hidden", podcast));
    document.querySelectorAll(".show-only-podcast").forEach(node => node.toggleAttribute("hidden", !podcast));
    if (podcast) { attach(); if (!loaded) load(); }
  }
  document.addEventListener("change", event => { if (event.target.id === "showStyle") sync(); });
  document.addEventListener("DOMContentLoaded", sync);
  window.ShowPodcast = { sync, ready: () => setup.people.length > 0 && setup.cameras.length > 0 };
})();
