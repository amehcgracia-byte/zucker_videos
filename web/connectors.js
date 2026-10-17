// Connectors: the accounts the app works with. Switch one when its allowance runs out.
// Claude, ChatGPT (Codex) and GitHub sign in in a Terminal window you type into; the app
// never sees a password. Hugging Face keeps the token you paste here.
(() => {
  let dialog = null;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  async function call(path, options = {}) {
    const response = await fetch(`/api/v1${path}`, { headers: { "Content-Type": "application/json" }, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error?.message || "Error");
    return data;
  }
  function row(item) {
    const state = !item.installed ? `<span class="connector-state off">Not installed</span>` : item.signed_in
      ? `<span class="connector-state on">Signed in${item.account ? ` as ${esc(item.account)}` : ""}${item.plan ? ` · ${esc(item.plan)}` : ""}</span>`
      : `<span class="connector-state off">Not signed in</span>`;
    const hf = item.id === "huggingface";
    const actions = !item.installed ? "" : hf
      ? `<div class="connector-hf" ${item.signed_in ? "hidden" : ""}><input type="password" autocomplete="off" placeholder="hf_… (paste your token)" data-hf-token aria-label="Hugging Face token">
          <button type="button" data-hf-save>Sign in</button> <a href="https://huggingface.co/settings/tokens/new?tokenType=read" target="_blank" rel="noopener">Create a token</a></div>
         ${item.signed_in ? `<button type="button" data-hf-switch>Use another account</button> <button type="button" data-hf-out>Sign out</button>` : ""}`
      : `<button type="button" data-sign-in="${item.id}">${item.signed_in ? "Switch account…" : "Sign in…"}</button>`;
    return `<section class="connector"><header><strong>${esc(item.name)}</strong>${state}</header><p>${esc(item.used_for)}</p>${item.detail && !item.signed_in ? `<small>${esc(item.detail)}</small>` : ""}<div class="connector-actions">${actions}</div></section>`;
  }
  async function show() {
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "connectorsDialog";
      document.body.append(dialog);
      dialog.addEventListener("click", (event) => act(event).catch((error) => status(error.message)));
    }
    dialog.innerHTML = `<header><h2>Connectors</h2><button type="button" data-close aria-label="Close connectors">×</button></header>
      <p class="connector-intro">The accounts this app works with. When one runs out of allowance, switch to another and carry on.</p>
      <div data-list><p>Checking your accounts…</p></div><p role="status" data-status></p>
      <div class="connector-actions"><button type="button" data-refresh>Check again</button></div>`;
    if (!dialog.open) dialog.showModal();
    try { dialog.querySelector("[data-list]").innerHTML = (await call("/connectors")).connectors.map(row).join(""); }
    catch (error) { dialog.querySelector("[data-list]").innerHTML = `<p>${esc(error.message)}</p>`; }
  }
  const status = (text) => { const line = dialog?.querySelector("[data-status]"); if (line) line.textContent = text; };
  async function act(event) {
    const target = event.target instanceof Element ? event.target.closest("button") : null;
    if (!target) return;
    if (target.hasAttribute("data-close")) { dialog.close(); return; }
    if (target.hasAttribute("data-refresh")) { await show(); return; }
    if (target.dataset.signIn) {
      const switching = /Switch/.test(target.textContent);
      await call(`/connectors/${target.dataset.signIn}/sign-in`, { method: "POST", body: JSON.stringify({ switch: switching }) });
      status("A Terminal window opened: sign in there (a browser page may open), then press Check again.");
      return;
    }
    if (target.hasAttribute("data-hf-switch")) { dialog.querySelector(".connector-hf").hidden = false; dialog.querySelector("[data-hf-token]").focus(); return; }
    if (target.hasAttribute("data-hf-out")) { await call("/connectors/huggingface/sign-out", { method: "POST", body: "{}" }); await show(); return; }
    if (target.hasAttribute("data-hf-save")) {
      const input = dialog.querySelector("[data-hf-token]");
      target.disabled = true;
      try { const result = await call("/connectors/huggingface/token", { method: "POST", body: JSON.stringify({ token: input.value }) }); input.value = ""; await show(); status(`Hugging Face: signed in as ${result.account}.`); }
      finally { target.disabled = false; }
    }
  }
  document.addEventListener("click", (event) => { if (event.target instanceof Element && event.target.closest("#connectorsOpen")) show(); });
  window.Connectors = { show };
})();
