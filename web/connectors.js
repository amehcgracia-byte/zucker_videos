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
      <div data-list><p>Checking your accounts…</p></div><div data-api-settings>Loading API settings…</div><p role="status" data-status></p>
      <div class="connector-actions"><button type="button" data-refresh>Check again</button></div>`;
    if (!dialog.open) dialog.showModal();
    apiSettings().catch(error=>status(error.message));
    try { dialog.querySelector("[data-list]").innerHTML = (await call("/connectors")).connectors.map(row).join(""); }
    catch (error) { dialog.querySelector("[data-list]").innerHTML = `<p>${esc(error.message)}</p>`; }
  }
  async function apiSettings() {
    const data=await call('/connectors/api'), host=dialog.querySelector('[data-api-settings]');
    if(!host)return;
    host.innerHTML='<h3>API models · optional</h3><p>API usage is billed by the provider, separately from signing in above. Keys stay in the system credential vault. No model request is made when you save a key.</p>'+data.providers.map(p=>`<section data-api-provider="${esc(p.id)}"><strong>${esc(p.name)}</strong> <span>${p.active?esc(p.masked)+' · configured (not verified)':'No key'}</span><input type="password" autocomplete="off" data-api-key aria-label="${esc(p.name)} API key" placeholder="Add or replace API key"><button data-api-save="${p.id}">Save key</button><button data-api-delete="${p.id}">Delete saved key</button>${p.environment?'<small>An environment key is also configured; deleting the saved key does not remove it.</small>':''}</section>`).join('')+
      `<h3>Narrative model</h3><label>Provider <select data-model-provider>${data.providers.filter(p=>p.default_model).map(p=>`<option value="${p.id}" data-model="${esc(p.default_model)}" ${p.id===data.provider?'selected':''} ${p.active?'':'disabled'}>${esc(p.name)}${p.active?'':' · Add your key in Connectors'}</option>`).join('')}</select></label><label>Model ID <input data-model-id value="${esc(data.model)}"></label><button data-model-save>Use this narrative model</button><h3>External control · this computer only</h3><p>A local tool can read status and request a render of the open project. This does not connect an online chat automatically.</p><button data-remote-copy>Copy local control token</button><p><small>Hugging Face tokens allow supported hosted tasks; they do not turn the existing local face swap into a hosted service.</small></p>`;
    host.querySelector('[data-model-provider]').onchange=e=>{host.querySelector('[data-model-id]').value=e.target.selectedOptions[0].dataset.model;};
  }
  const status = (text) => { const line = dialog?.querySelector("[data-status]"); if (line) line.textContent = text; };
  async function act(event) {
    const target = event.target instanceof Element ? event.target.closest("button") : null;
    if (!target) return;
    if(target.dataset.apiSave||target.dataset.apiDelete){
      const provider=target.dataset.apiSave||target.dataset.apiDelete, row=target.closest('[data-api-provider]');
      const value=target.dataset.apiDelete?'':row.querySelector('[data-api-key]').value;
      await call('/connectors/api/'+provider,{method:'PUT',body:JSON.stringify({key:value})});
      row.querySelector('[data-api-key]').value='';await apiSettings();status('Connection settings saved.');return;
    }
    if(target.hasAttribute('data-model-save')){
      await call('/connectors/model',{method:'PUT',body:JSON.stringify({provider:dialog.querySelector('[data-model-provider]').value,model:dialog.querySelector('[data-model-id]').value})});
      status('Narrative model saved for future analysis. Existing edits are kept.');return;
    }
    if(target.hasAttribute('data-remote-copy')){
      const result=await call('/connectors/remote-token',{method:'POST',body:'{}'});
      try{await navigator.clipboard.writeText(result.token);status('Local control token copied. Give it only to a tool you trust on this computer.');}
      catch{status('Clipboard is unavailable. The token remains in the secure credential store.');}return;
    }
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
