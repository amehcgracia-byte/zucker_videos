(function () {
  var STORAGE_KEY = "zucker-theme";

  function preferredTheme() {
    try {
      var saved = localStorage.getItem(STORAGE_KEY);
      if (saved === "light" || saved === "dark") return saved;
    } catch (e) {
      // localStorage unavailable (e.g. private browsing) — fall through to system preference.
    }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var toggle = document.getElementById("themeToggle");
    if (toggle) toggle.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  }

  var currentTheme = preferredTheme();
  applyTheme(currentTheme);

  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("themeToggle");
    if (!toggle) return;
    applyTheme(currentTheme);
    toggle.addEventListener("click", function () {
      currentTheme = currentTheme === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(STORAGE_KEY, currentTheme);
      } catch (e) {
        // Ignore — theme just won't persist across reloads.
      }
      applyTheme(currentTheme);
    });
  });
})();
