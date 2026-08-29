(() => {
  try {
    const requested = new URLSearchParams(window.location.search).get("theme");
    const requestedTheme = requested === "dark" || requested === "light" ? requested : "";
    let saved = "";
    try {
      saved = requestedTheme || localStorage.getItem("options-theme") || "";
      if (requestedTheme) localStorage.setItem("options-theme", requestedTheme);
    } catch {
      saved = requestedTheme;
    }
    const systemDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = saved || (systemDark ? "dark" : "light");
  } catch {
    document.documentElement.dataset.theme = "light";
  }
})();
