(function () {
  const bridgeState = {
    desktop: null,
    ready: Boolean(window.pywebview?.api),
  };

  function configure(config) {
    bridgeState.desktop = Boolean(config?.desktop);
    bridgeState.ready = Boolean(window.pywebview?.api);
    updateDesktopOnlyAvailability();
  }

  function api() {
    return window.pywebview?.api || null;
  }

  function waitForApi(timeoutMs = 3000) {
    const existing = api();
    if (existing) {
      bridgeState.ready = true;
      updateDesktopOnlyAvailability();
      return Promise.resolve(existing);
    }
    if (bridgeState.desktop === false) {
      return Promise.reject(new Error("Solo disponible en la app de escritorio"));
    }
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (bridge) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        document.removeEventListener("pywebviewready", onReady);
        bridgeState.ready = true;
        updateDesktopOnlyAvailability();
        resolve(bridge);
      };
      const fail = () => {
        if (settled) return;
        settled = true;
        document.removeEventListener("pywebviewready", onReady);
        bridgeState.ready = false;
        updateDesktopOnlyAvailability();
        reject(new Error("El puente nativo no respondió"));
      };
      const onReady = () => {
        const bridge = api();
        if (bridge) finish(bridge);
      };
      const timer = setTimeout(fail, timeoutMs);
      document.addEventListener("pywebviewready", onReady, { once: true });
      const bridge = api();
      if (bridge) finish(bridge);
    });
  }

  async function call(methodName, args = [], label = "Acción nativa") {
    const bridge = await waitForApi();
    const method = bridge[methodName];
    if (typeof method !== "function") {
      throw new Error(`${label}: el puente nativo no expone ${methodName}`);
    }
    try {
      return await method.apply(bridge, args);
    } catch (error) {
      throw new Error(`${label}: ${error?.message || error}`);
    }
  }

  async function reveal(path, label = "Abrir en Finder") {
    if (!path) return false;
    return call("reveal_in_finder", [path], label);
  }

  function updateDesktopOnlyAvailability() {
    const desktop = bridgeState.desktop;
    const ready = Boolean(api());
    bridgeState.ready = ready;
    document.querySelectorAll(".desktop-only").forEach((button) => {
      button.disabled = desktop === false;
      if (desktop === false) {
        button.title = "Disponible en la app de escritorio";
      } else if (ready) {
        button.title = "";
      } else {
        button.title = "Esperando el puente nativo";
      }
    });
  }

  document.addEventListener("pywebviewready", () => {
    bridgeState.ready = Boolean(api());
    updateDesktopOnlyAvailability();
  });

  window.NativeBridge = {
    configure,
    waitForApi,
    call,
    reveal,
    updateDesktopOnlyAvailability,
    isDesktop: () => bridgeState.desktop === true,
    isReady: () => Boolean(api()),
  };
})();
