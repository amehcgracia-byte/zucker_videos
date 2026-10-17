// A right-click menu like the system's: items with shortcuts, ticks and separators, and
// submenus that open BESIDE their item (to the right, or to the left near the screen edge),
// never inside it. Hover or → opens a submenu, ← closes it, ↑↓ move, Enter chooses, Esc closes.
// One menu at a time; it lives in the given host (the scene editor while it is open).
(() => {
  let host = null;
  const menus = () => [...document.querySelectorAll(".zmenu")];

  function close() {
    menus().forEach((menu) => menu.remove());
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", keys, true);
    window.removeEventListener("blur", close);
  }
  function outside(event) { if (!(event.target instanceof Element && event.target.closest(".zmenu"))) close(); }

  function build(items, level) {
    const menu = document.createElement("div");
    menu.className = "zmenu";
    menu.setAttribute("role", "menu");
    menu.dataset.level = level;
    for (const item of items) {
      if (!item) continue;
      if (item.separator) { const line = document.createElement("div"); line.className = "zmenu-separator"; menu.append(line); continue; }
      if (item.heading) { const head = document.createElement("div"); head.className = "zmenu-heading"; head.textContent = item.heading; menu.append(head); continue; }
      const row = document.createElement("button");
      row.type = "button";
      row.className = "zmenu-item";
      row.setAttribute("role", "menuitem");
      row.disabled = !!item.disabled;
      if (item.submenu) row.setAttribute("aria-haspopup", "menu");
      row.innerHTML = '<span class="zmenu-check"></span><span class="zmenu-label"></span><span class="zmenu-hint"></span>';
      row.querySelector(".zmenu-check").textContent = item.checked ? "✓" : "";
      row.querySelector(".zmenu-label").textContent = item.label;
      row.querySelector(".zmenu-hint").textContent = item.submenu ? "▸" : item.hint || "";
      if (item.title) row.title = item.title;
      row.addEventListener("pointerenter", () => { row.focus({ preventScroll: true }); openSub(menu, row, item, level); });
      row.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (item.disabled) return;
        if (item.submenu) { openSub(menu, row, item, level, true); return; }
        close();
        try { await item.action?.(); } catch (error) { window.showToast?.(error.message, true); }
      });
      row._item = item;
      menu.append(row);
    }
    (host || document.body).append(menu);
    return menu;
  }

  function openSub(menu, row, item, level, focus = false) {
    menus().filter((other) => Number(other.dataset.level) > level).forEach((other) => other.remove());
    menu.querySelectorAll(".zmenu-item.open").forEach((other) => other.classList.remove("open"));
    if (!item.submenu || item.disabled) return;
    row.classList.add("open");
    const sub = build(typeof item.submenu === "function" ? item.submenu() : item.submenu, level + 1);
    const box = row.getBoundingClientRect();
    place(sub, box.right - 3, box.top - 5, box.left + 3);
    if (focus) sub.querySelector(".zmenu-item:not(:disabled)")?.focus({ preventScroll: true });
  }

  function place(menu, x, y, leftEdge = null) {
    menu.style.left = "0px"; menu.style.top = "0px";
    const width = menu.offsetWidth, height = menu.offsetHeight;
    let left = x;
    if (left + width > innerWidth - 4) left = (leftEdge ?? x) - width;      // no room on the right: it opens on the left
    menu.style.left = `${Math.max(4, Math.min(left, innerWidth - width - 4))}px`;
    menu.style.top = `${Math.max(4, Math.min(y, innerHeight - height - 4))}px`;
  }

  function keys(event) {
    const row = document.activeElement?.closest?.(".zmenu-item");
    const menu = row?.closest(".zmenu") || menus().at(-1);
    if (!menu) return;
    const rows = [...menu.querySelectorAll(".zmenu-item:not(:disabled)")];
    const at = rows.indexOf(row);
    const level = Number(menu.dataset.level);
    if (event.key === "Escape") { event.preventDefault(); if (level > 0) { menu.remove(); document.querySelector(`.zmenu[data-level="${level - 1}"] .zmenu-item.open`)?.focus(); } else close(); }
    else if (event.key === "ArrowDown") { event.preventDefault(); rows[(at + 1) % rows.length]?.focus(); }
    else if (event.key === "ArrowUp") { event.preventDefault(); rows[(at - 1 + rows.length) % rows.length]?.focus(); }
    else if (event.key === "ArrowRight" && row?._item?.submenu) { event.preventDefault(); openSub(menu, row, row._item, level, true); }
    else if (event.key === "ArrowLeft" && level > 0) { event.preventDefault(); menu.remove(); document.querySelector(`.zmenu[data-level="${level - 1}"] .zmenu-item.open`)?.focus(); }
    else if (event.key === "Enter" && row) { event.preventDefault(); row.click(); }
    else return;
    event.stopPropagation();
  }

  function open(x, y, items, options = {}) {
    close();
    host = options.host || document.body;
    const menu = build(items, 0);
    place(menu, x, y);
    menu.querySelector(".zmenu-item:not(:disabled)")?.focus({ preventScroll: true });
    setTimeout(() => {
      document.addEventListener("pointerdown", outside, true);
      document.addEventListener("keydown", keys, true);
      window.addEventListener("blur", close);
    });
  }

  window.ShowMenu = { open, close };
})();
