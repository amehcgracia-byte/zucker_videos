/* Show Studio's work kept in the Zucker project.

   Served by Zucker (window.ZUCKER_STUDIO.project), the editor loads its recipe, its own
   backgrounds and masks from the open project and saves there; a save that started from an
   older revision is refused rather than overwriting another window. Standalone, nothing
   changes, and "Save to project" copies this browser's work into a project once: every file
   is checked against its SHA-256 after the copy, and nothing in this browser is deleted. */
(function () {
  'use strict';
  const inProject = !!(window.ZUCKER_STUDIO && window.ZUCKER_STUDIO.project);
  // The part of the source being edited (ShowSource's "Beginning" or the earlier workshop): each keeps its own edit.
  const section = new URLSearchParams(location.search).get('section') === 'opening' ? 'opening' : 'workshop';
  const scoped = (path) => `${path}?section=${section}`;
  const local = (name) => (window.ShowSource ? window.ShowSource.path(name) : name);
  const SOURCES = { 'original.mp4': 'original', 'mix.m4a': 'baseAudio', 'voice.m4a': 'voice', 'cutout-atlas.webp': 'cutoutAtlas' };
  const ANALYSIS = { 'finger-track.json': 'finger-track', 'face-track.json': 'face-track', 'automatic-hands.json': 'automatic-hands',
    'captions.json': 'captions', 'cutout-sample.json': 'cutout-sample', 'recipe.json': 'proposal' };
  let loaded = null, revision = 0, pending = null, timer = null;
  const listeners = [];

  function notify(kind, detail) { for (const fn of listeners) try { fn(kind, detail); } catch (_) { /* a listener's own problem */ } }

  async function call(url, options) {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body?.error?.message || `${response.status} ${response.statusText}`);
      error.code = body?.error?.code; error.status = response.status;
      throw error;
    }
    return body;
  }
  const put = (url, payload) => call(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });

  async function sha256(blob) {
    const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  // A file into the project library, checked byte for byte by its SHA-256.
  async function importFile(blob, name, origin, base = '') {
    const form = new FormData();
    form.append('file', blob, name);
    form.append('origin', origin);
    const record = await call(`${base}/api/v1/library/import`, { method: 'POST', body: form });
    if (record.sha256 !== await sha256(blob)) throw new Error(`${name}: the project copy does not match the original`);
    return record;
  }

  // Settings of an own background or mask, without the picture held in this browser.
  function settings(record) {
    const out = {};
    for (const [key, value] of Object.entries(record)) {
      if (key === 'file' || key === 'blob' || key === 'remote' || value instanceof Blob) continue;
      out[key] = value;
    }
    return out;
  }
  function material(kind, record, asset) {
    const out = { ...settings(record), kind, ...(asset ? { asset } : {}) };
    if (kind === 'background') out.media = record.kind === 'video' ? 'video' : 'image';
    if (!asset && typeof record.file === 'string' && !record.blob) out.builtin = record.file;   // an edited sample
    return out;
  }
  // Back to what the libraries expect: the picture now comes from the project.
  function asRecord(item) {
    const record = { ...item, remote: !!item.asset, file: item.asset ? `/api/v1/library/assets/${item.asset}` : item.builtin };
    if (item.kind === 'background') { if (item.media === 'video') record.kind = 'video'; else delete record.kind; }
    return record;
  }

  async function load() {
    if (!inProject) return null;
    loaded = await call(scoped('/api/v1/show-studio/state'));
    revision = loaded.revision;
    notify('loaded', loaded);
    return loaded;
  }
  const ready = inProject ? load().catch((error) => { notify('error', error.message); return null; }) : Promise.resolve(null);

  async function flush() {
    if (!pending) return;
    const recipe = pending;
    pending = null;
    try {
      revision = (await put(scoped('/api/v1/show-studio/composition'), { recipe, revision })).revision;
      notify('saved', revision);
    } catch (error) {
      notify(error.code === 'stale_revision' ? 'stale' : 'error', error.message);
    }
  }
  function saveRecipe(recipe) {
    if (!inProject) return;
    pending = JSON.parse(JSON.stringify(recipe));
    clearTimeout(timer);
    timer = setTimeout(flush, 500);
  }

  async function materials(kind) {
    await ready;
    return (loaded?.materials || []).filter((item) => item.kind === kind).map(asRecord);
  }
  // A new or edited own background or mask: its picture into the library once, its settings into the project.
  async function keepMaterial(kind, record) {
    await ready;
    if (!loaded) throw new Error('The project is not available');
    const known = loaded.materials.find((item) => item.id === record.id);
    let asset = known?.asset || null;
    if (record.blob) asset = (await importFile(record.blob, record.blob.name || `${record.id}.png`, `Show Studio · ${record.origin || 'your picture'}`)).id;
    const entry = material(kind, record, asset);
    const list = [...loaded.materials.filter((item) => item.id !== entry.id), entry];
    const saved = await put('/api/v1/show-studio/materials', { materials: list });
    loaded.materials = saved.materials;
    return asRecord(entry);
  }

  // ---- One-off copy of this browser's work into a project open in Zucker ---------------

  function idbAll(name, store) {
    return new Promise((resolve) => {
      const request = indexedDB.open(name, 1);
      request.onupgradeneeded = () => request.result.createObjectStore(store, { keyPath: 'id' });
      request.onerror = () => resolve([]);
      request.onsuccess = () => {
        const db = request.result;
        const all = db.transaction(store).objectStore(store).getAll();
        all.onerror = () => { db.close(); resolve([]); };
        all.onsuccess = () => { db.close(); resolve(all.result || []); };
      };
    });
  }

  async function migrate(base, { recipe, step = () => {} }) {
    base = String(base || '').replace(/\/+$/, '');
    const target = await call(scoped(`${base}/api/v1/show-studio/state`));
    const report = { project: target.project, section, files: 0, materials: 0, analysis: 0, skipped: [] };
    const sources = {};
    for (const [name, key] of Object.entries(SOURCES)) {
      step(`Copying ${local(name)}…`);
      const response = await fetch(local(name));
      if (!response.ok) { report.skipped.push(local(name)); continue; }
      sources[key] = (await importFile(await response.blob(), name, `Show pilot excerpt · ${section}`, base)).id;
      report.files += 1;
    }
    // The scene workshop's thumbnails (frames of this video).
    const thumbs = [...new Set([...document.querySelectorAll('img[src*="scene-thumbs/"]')].map((img) => img.getAttribute('src')))];
    for (const path of thumbs) {
      const id = path.slice(path.indexOf('scene-thumbs/') + 'scene-thumbs/'.length).replace(/\.jpg$/, '');
      if (!/^[a-z0-9-]{1,30}$/.test(id)) continue;
      const response = await fetch(path);
      if (!response.ok) { report.skipped.push(path); continue; }
      step(`Copying ${path}…`);
      sources[`thumb-${id}`] = (await importFile(await response.blob(), `${id}.jpg`, 'Show pilot scene thumbnail', base)).id;
      report.files += 1;
    }
    if (Object.keys(sources).length) await put(scoped(`${base}/api/v1/show-studio/sources`), { sources });
    for (const [name, key] of Object.entries(ANALYSIS)) {
      const response = await fetch(local(name));
      if (!response.ok) { report.skipped.push(local(name)); continue; }
      step(`Copying ${local(name)}…`);
      await put(scoped(`${base}/api/v1/show-studio/analysis/${key}`), await response.json());
      report.analysis += 1;
    }
    const list = [...(target.materials || [])];
    const own = [...(await idbAll('show-personal-backgrounds', 'images')).map((record) => ['background', record]),
      ...(await idbAll('show-personal-masks', 'masks')).map((record) => ['mask', record])];
    for (const [kind, record] of own) {
      step(`Copying your ${kind} “${record.title || record.id}”…`);
      const asset = record.blob ? (await importFile(record.blob, record.blob.name || `${record.id}.png`, `Show Studio · ${record.origin || 'your picture'}`, base)).id : null;
      const entry = material(kind, record, asset);
      list.splice(0, list.length, ...list.filter((item) => item.id !== entry.id), entry);
      report.materials += 1;
    }
    if (own.length) await put(`${base}/api/v1/show-studio/materials`, { materials: list });
    step('Saving your edit…');
    report.revision = (await put(scoped(`${base}/api/v1/show-studio/composition`), { recipe, revision: target.revision })).revision;
    return report;
  }

  // ---- What the person sees: where the work is kept --------------------------------------

  function mountControls() {
    const header = document.querySelector('header');
    if (!header) return;
    const line = document.createElement('small');
    line.id = 'studioStorage';
    line.setAttribute('role', 'status');
    header.append(line);
    if (inProject) {
      const back = document.createElement('a');
      back.href = '/';
      back.textContent = '← Zucker';
      back.className = 'studioBack';
      header.prepend(back);
      line.textContent = 'Opening the project…';
      const show = (kind, detail) => {
        if (kind === 'loaded') line.textContent = `Saved in project “${detail.project?.name}”` + (detail.revision ? ` · revision ${detail.revision}` : ' · not edited yet') + ` · ${location.origin}`;
        if (kind === 'saved') line.textContent = `Saved in project “${loaded?.project?.name}” · revision ${detail}`;
        if (kind === 'stale') line.textContent = 'Changed in another window: reload this page before editing further.';
        if (kind === 'error') line.textContent = `Not saved: ${detail}`;
        line.dataset.state = kind;
      };
      onChange(show);
      if (loaded) show('loaded', { ...loaded, revision });     // the project opened before this line existed
      return;
    }
    line.textContent = 'Kept in this browser only.';
    const button = document.createElement('button');
    button.id = 'saveToProject';
    button.type = 'button';
    button.textContent = section === 'opening' ? 'Save this section to project…' : 'Save to project…';
    header.append(button);
    button.onclick = async () => {
      let address = '';
      try { address = localStorage.getItem('zucker-studio-address') || ''; } catch (_) { /* private window */ }
      address = prompt('Address of Zucker Editor with the project open (shown under the title of its Show Studio):', address || 'http://127.0.0.1:5179');
      if (!address) return;
      try { localStorage.setItem('zucker-studio-address', address); } catch (_) { /* private window */ }
      button.disabled = true;
      try {
        const target = await call(scoped(`${address.replace(/\/+$/, '')}/api/v1/show-studio/state`));
        const replaces = target.revision ? `\n\nIt replaces the edit already saved there (revision ${target.revision}).` : '';
        if (!confirm(`Copy this edit, its video and sound, its tracking and your own backgrounds and masks into the project “${target.project?.name}”?\n${target.project?.folder}${replaces}\n\nEvery file is checked after the copy. Nothing is deleted from this browser.`)) return;
        // eslint-disable-next-line no-undef
        const current = typeof recipe !== 'undefined' ? recipe : null;
        if (!current) throw new Error('The edit is not loaded yet');
        const report = await migrate(address, { recipe: current, step: (text) => { line.textContent = text; } });
        line.textContent = `Saved in “${report.project?.name}”: ${report.files} files, ${report.analysis} tracking files, ${report.materials} backgrounds and masks · revision ${report.revision}.`
          + (report.skipped.length ? ` Not found here: ${report.skipped.join(', ')}.` : '');
      } catch (error) {
        line.textContent = `Not saved to the project: ${error.message}`;
      } finally {
        button.disabled = false;
      }
    };
  }
  function onChange(fn) { listeners.push(fn); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountControls); else mountControls();

  window.ShowStudioStore = { inProject, ready, load, saveRecipe, flush, materials, keepMaterial, migrate, sha256,
    onChange };
})();
