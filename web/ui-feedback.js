/* Shared dismissible messages and measured foreground request feedback. */
window.UiFeedback = (() => {
  let toastTimer;
  let confirmation = null;
  const pending = new Map();
  const timings = [];
  let sequence = 0;
  let dismissedThrough = 0;
  const tray = document.createElement("aside");
  tray.className = "request-feedback";
  tray.hidden = true;
  tray.setAttribute("role", "status");
  document.body.append(tray);
  function closeButton(label, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "feedback-close";
    button.textContent = "×";
    button.setAttribute("aria-label", label);
    button.addEventListener("click", action);
    return button;
  }
  function dismissMessages() {
    document.querySelector("#toast").hidden = true;
    clearTimeout(toastTimer);
  }
  function message(text, error = false) {
    clearTimeout(toastTimer);
    const toast = document.querySelector("#toast");
    toast.replaceChildren();
    const content = document.createElement("span");
    content.textContent = text;
    toast.append(closeButton("Close message", dismissMessages), content);
    toast.classList.toggle("error", error);
    toast.setAttribute("role", error ? "alert" : "status");
    toast.hidden = false;
    // Errors remain available to read until explicitly dismissed.
    if (!error) toastTimer = setTimeout(dismissMessages, 6000);
  }
  function renderPending() {
    const active = [...pending.values()].filter(item => item.id > dismissedThrough && performance.now() - item.started > 800);
    tray.hidden = active.length === 0;
    if (!active.length) return;
    tray.replaceChildren(closeButton("Hide loading details", () => { dismissedThrough = sequence; tray.hidden = true; }));
    for (const item of active) {
      const row = document.createElement("div");
      row.textContent = `${item.label} · ${((performance.now() - item.started) / 1000).toFixed(1)} s`;
      row.append(window.MeasuredProgress.create(null, item.label));
      tray.append(row);
    }
  }
  setInterval(renderPending, 250);
  function requestLabel(route) {
    const labels = [
      [/spherical-source-frame|spherical-preview/, "Loading 360 camera preview"],
      [/spherical/, "Saving 360 camera setup"],
      [/projects\/open/, "Opening project"], [/projects\/delete/, "Removing project"],
      [/projects/, "Loading projects"], [/app\/config/, "Loading application settings"],
      [/report/, "Loading processing report"], [/review/, "Loading shot review"],
      [/paper-edit/, "Loading dialogue edit"], [/captions/, "Preparing captions"],
      [/overlays|flyer|logo/, "Preparing images and overlays"],
      [/sync/, "Checking camera synchronization"], [/inbox/, "Analyzing input files"],
      [/classify|inputs|register/, "Preparing selected files"], [/songs|audio|trim/, "Preparing audio"],
      [/cache/, "Checking cached media"], [/composition|compose/, "Preparing composition"],
      [/settings/, "Saving settings"], [/result|export/, "Loading exported video"],
      [/project/, "Loading project"], [/start|prepare|run/, "Starting processing"],
    ];
    return labels.find(([pattern]) => pattern.test(route))?.[1] || "Loading application data";
  }
  const readsInFlight = new Map();
  async function request(url, options) {
    const route = new URL(url, location.origin).pathname;
    const shared = (options?.method || "GET").toUpperCase() === "GET" && route.startsWith("/api/") && !/\/(status|frontend-log)$/.test(route);
    if (!shared) return requestOnce(url, options);
    const key = JSON.stringify([url, options?.headers || {}]);
    if (!readsInFlight.has(key)) {
      const pending = requestOnce(url, options);
      readsInFlight.set(key, pending);
      pending.finally(() => { if (readsInFlight.get(key) === pending) readsInFlight.delete(key); }).catch(() => {});
    }
    return (await readsInFlight.get(key)).clone();
  }
  async function requestOnce(url, options) {
    const route = new URL(url, location.origin).pathname;
    const background = /\/(status|frontend-log)$/.test(route);
    const id = ++sequence;
    const started = performance.now();
    const label = requestLabel(route);
    if (!background) pending.set(id, { id, started, label });
    let status = 0;
    try {
      const response = await fetch(url, options);
      status = response.status;
      return response;
    } finally {
      const duration_ms = Math.round(performance.now() - started);
      pending.delete(id);
      renderPending();
      timings.push({ route, method: options?.method || "GET", duration_ms, status });
      if (timings.length > 200) timings.shift();
      if (duration_ms > 2000 && !background) {
        fetch("/api/v1/wizard/frontend-log", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: `LOAD ${route}: ${duration_ms} ms; HTTP ${status}` }) }).catch(() => {});
      }
    }
  }
  function confirm(text, options = {}) {
    // A dismissed confirmation never performs the proposed action.
    if (confirmation) return Promise.resolve(null);
    return new Promise(resolve => {
      const previousFocus = document.activeElement;
      const backdrop = document.createElement("div");
      backdrop.className = "feedback-backdrop";
      const box = document.createElement("section");
      box.className = "feedback-dialog";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-labelledby", "feedback-confirm-text");
      const finish = accepted => {
        backdrop.remove(); confirmation = null; previousFocus?.focus(); resolve(accepted);
      };
      confirmation = { finish, box };
      const paragraph = document.createElement("p");
      paragraph.id = "feedback-confirm-text";
      paragraph.textContent = text;
      const cancel = document.createElement("button");
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", () => finish(null));
      const accept = document.createElement("button");
      accept.textContent = "Continue";
      accept.addEventListener("click", () => finish(true));
      box.append(closeButton("Close confirmation", () => finish(null)), paragraph, cancel);
      if (options.alternateLabel) {
        const alternate = document.createElement("button");
        alternate.textContent = options.alternateLabel;
        alternate.addEventListener("click", () => finish(false));
        box.append(alternate);
      }
      box.append(accept);
      backdrop.append(box);
      backdrop.addEventListener("click", event => { if (event.target === backdrop) finish(null); });
      document.body.append(backdrop);
      cancel.focus();
    });
  }
  document.addEventListener("pointerdown", event => {
    const toast = document.querySelector("#toast");
    if (!toast.hidden && !toast.contains(event.target)) dismissMessages();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") { dismissMessages(); confirmation?.finish(null); dismissedThrough = sequence; tray.hidden = true; }
    if (event.key === "Tab" && confirmation) {
      const buttons = [...confirmation.box.querySelectorAll("button")];
      const index = buttons.indexOf(document.activeElement);
      const next = (index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length;
      buttons[next].focus(); event.preventDefault();
    }
  });
  return { request, message, confirm, timings };
})();
