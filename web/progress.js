/* One measured progress contract for imports, processing and request feedback. */
window.MeasuredProgress = (() => {
  function set(bar, raw, label = "Processing") {
    const value = raw == null || raw === "" ? null : Number(raw);
    const known = value != null && Number.isFinite(value);
    const percent = known ? Math.max(0, Math.min(100, value)) : null;
    bar.classList.toggle("pending", !known);
    bar.setAttribute("aria-label", label);
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    if (known) {
      bar.setAttribute("aria-valuenow", String(percent));
      bar.removeAttribute("aria-valuetext");
    } else {
      bar.removeAttribute("aria-valuenow");
      bar.setAttribute("aria-valuetext", "Working; percentage unavailable");
    }
    bar.querySelector(".task-progress-fill").style.width = known ? `${percent}%` : "22%";
    return percent;
  }
  function create(raw, label) {
    const bar = document.createElement("div");
    bar.className = "task-progress-track";
    bar.setAttribute("role", "progressbar");
    const fill = document.createElement("div");
    fill.className = "task-progress-fill";
    bar.append(fill);
    set(bar, raw, label);
    return bar;
  }
  return { create, set };
})();
