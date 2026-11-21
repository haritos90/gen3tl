// Site theme, applied before the first paint.
// - the stored choice overrides the system theme
// - [data-toggle-theme] buttons switch and store it
(function () {
  var root = document.documentElement;
  // Scripted layout, known before the first paint.
  root.setAttribute("data-scripted", "");
  var KEY = "gen3tl-theme";
  function stored() {
    try {
      return localStorage.getItem(KEY) || "";
    } catch (e) {
      return "";
    }
  }
  function apply(theme) {
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }
  apply(stored());
  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-toggle-theme]");
    if (!button) return;
    var current = root.getAttribute("data-theme");
    var dark = current === "dark" || (!current && matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark";
    apply(next);
    document.dispatchEvent(new Event("gen3tl-theme-change"));
    try {
      localStorage.setItem(KEY, next);
    } catch (e) {
      // this page only
    }
  });
})();

// Hold page text until its language is ready.
(() => {
  const root = document.documentElement;
  const valid = (code) => typeof code === "string" && /^[a-z]{2,3}(?:-[A-Za-z0-9]+)?$/.test(code);
  let stored = null;
  try {
    stored = localStorage.getItem("gen3tl-site-lang") || localStorage.getItem("gen3tl-dex-lang");
  } catch { /* Browser preferences remain available without storage. */ }
  const browser = Array.from(globalThis.navigator?.languages ?? [], (tag) => String(tag).toLowerCase().split("-")[0]);
  const language = valid(stored) ? stored : browser.find(valid) || "en";
  const configured = Number(document.querySelector('meta[name="site-startup-deadline"]')?.content);
  const wait = Number.isFinite(configured) && configured > 0 ? configured : 1500;
  let timer;
  const startup = document.siteStartup = {
    language, deadline: Date.now() + wait, expired: false,
    reveal() {
      clearTimeout(timer);
      root.removeAttribute("data-ui-pending");
    },
  };
  if (language !== "en" || configured > 0) {
    if (language !== "en") {
      const url = new URL(`data/site/${language}.json`, document.baseURI).href;
      startup.catalog = {
        url,
        response: fetch(url, { credentials: "omit", cache: "no-cache" }).catch(() => null),
      };
    }
    root.setAttribute("data-ui-pending", "");
    timer = setTimeout(() => {
      startup.expired = true;
      startup.reveal();
    }, wait);
  }
})();
