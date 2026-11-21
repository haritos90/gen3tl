(() => {
  const base = document.createElement("base");
  base.href = new URL(".", location.href).href;
  document.head.prepend(base);
  const text = globalThis.pokedexStartupCatalog ?? {};
  const { matchLanguage, preferredLanguage } = globalThis.gen3tlLanguages;
  const codes = ["en", ...Object.keys(text)];
  const siteLanguage = (code) => matchLanguage(code, codes, "en");
  const parts = location.hash.replace(/^#/, "").split("/").filter(Boolean);
  const routed = [parts[1], parts[0]].map((code) => matchLanguage(code, codes)).find(Boolean) ?? null;
  let remembered = null;
  try { remembered = localStorage.getItem("gen3tl-site-lang"); } catch { /* The routed language still works. */ }
  const query = new URLSearchParams(location.search).get("lang");
  let language = routed || matchLanguage(query, codes) || preferredLanguage(codes, remembered, globalThis.navigator?.languages);
  const startup = { completed: 0, active: 0, detail: "site", selected: true, failed: false, complete: false, error: null };
  const byId = (id) => document.getElementById(id);
  const N_ = (key) => key;
  const translate = (key, vars = null) => (text[language]?.[key] || key)
    .replace(/\{(\w+)\}/g, (hole, name) => (vars && name in vars ? String(vars[name]) : hole));
  const stages = [N_("Game and language setup"), N_("Loading catalog text"), N_("Loading Pokédex data"),
    N_("Opening the selected entry"), N_("Preparing the interface")];
  const details = {
    site: N_("Loading interface language…"), game: N_("Loading game details…"), languages: N_("Loading the game's language options…"),
    catalog: N_("Names, descriptions and search text"), data: N_("Species, moves, abilities, items and encounters"),
    entry: N_("Preparing the list and selected entry…"), list: N_("Preparing the species list…"),
    interface: N_("Arranging navigation and entry cards…"), refinements: N_("Finishing compact cards and controls…"),
  };
  const labels = () => ({
    stages: stages.map(translate), listStage: translate(N_("Preparing the entry list")),
    details: Object.fromEntries(Object.entries(details).map(([name, key]) => [name, translate(key)])),
    count: (current, total, completed) => translate(N_("Stage {current} of {total} · {completed} completed"), { current, total, completed }),
    progress: translate(N_("Pokédex loading progress")),
    progressValue: (current, total, completed, stage) => translate(
      N_("Stage {current} of {total}: {stage}; {completed} of {total} completed"), { current, total, completed, stage }),
    failure: translate(N_("Cannot open the Pokédex")), retry: translate(N_("Open the Pokédex")), close: translate(N_("Close")),
  });
  const stageName = () => startup.active === 3 && !startup.selected ? labels().listStage : labels().stages[startup.active];
  function put(id, value) {
    const node = byId(id);
    if (node && node.textContent !== value) node.textContent = value;
  }
  function render() {
    if (startup.failed || startup.complete) return;
    const app = byId("app");
    if (app) {
      app.inert = true;
      app.setAttribute("aria-busy", "true");
    }
    document.documentElement.lang = language;
    const total = labels().stages.length;
    const current = Math.min(startup.completed + 1, total);
    const stage = stageName();
    put("c-startup-message", stage);
    put("c-startup-detail", labels().details[startup.detail]);
    put("c-startup-count", labels().count(current, total, startup.completed));
    put("c-startup-retry", labels().retry);
    put("c-startup-close", labels().close);
    const bar = byId("c-startup-progress");
    if (bar) {
      bar.setAttribute("aria-label", labels().progress);
      bar.setAttribute("aria-valuemax", String(total));
      bar.setAttribute("aria-valuenow", String(startup.completed));
      bar.setAttribute("aria-valuetext", labels().progressValue(current, total, startup.completed, stage));
    }
    const fill = byId("c-startup-fill");
    if (fill) fill.style.width = `${startup.completed / total * 100}%`;
  }
  function update(stage, detail) {
    if (startup.failed || startup.complete || startup.active !== stage || startup.completed !== stage) return;
    startup.detail = detail;
    render();
  }
  function activate(stage, detail) {
    if (startup.failed || startup.complete || stage !== startup.completed + 1 || stage >= labels().stages.length) return;
    startup.completed = stage;
    startup.active = stage;
    startup.detail = detail;
    render();
  }
  function unblock() {
    const app = byId("app");
    if (!app) return;
    app.inert = false;
    app.removeAttribute("aria-busy");
  }
  function dismiss() {
    if (startup.failed || startup.complete || startup.completed !== 4) return;
    if (!globalThis.pokedexPage?.ready || !globalThis.pokedexExplorer?.ready || !globalThis.pokedexExplorer?.refined) return;
    startup.completed = labels().stages.length;
    startup.complete = true;
    byId("c-startup")?.remove();
    unblock();
  }
  function showFailure() {
    const overlay = byId("c-startup");
    if (!overlay) return;
    document.documentElement.lang = language;
    overlay.setAttribute("role", "alert");
    overlay.setAttribute("aria-live", "assertive");
    overlay.setAttribute("aria-busy", "false");
    const message = byId("c-startup-message");
    const error = startup.error;
    if (message) {
      message.textContent = `${labels().failure}: ${error?.message ?? error}`;
      if (startup.completed > 0) import("./ui.js").then(({ showText }) => {
        if (message.isConnected) showText(message, "Cannot open the Pokédex: {error}", { error: error?.message ?? error });
      }).catch(() => {});
    }
    const detail = byId("c-startup-detail");
    if (detail) detail.hidden = true;
    const count = byId("c-startup-count");
    if (count) count.hidden = true;
    const bar = byId("c-startup-progress");
    if (bar) bar.hidden = true;
    put("c-startup-retry", labels().retry);
    put("c-startup-close", labels().close);
    const actions = byId("c-startup-actions");
    if (actions) actions.hidden = false;
    if (document.readyState !== "loading") byId("c-startup-retry")?.focus({ preventScroll: true });
  }
  function fail(error) {
    if (startup.failed || startup.complete) return;
    startup.failed = true;
    startup.error = error ?? new Error("Unknown startup error");
    showFailure();
  }
  function setLanguage(code) {
    language = siteLanguage(code);
    if (startup.failed) showFailure();
    else render();
  }
  window.addEventListener("pokedex-setup-site", () => update(0, "game"));
  window.addEventListener("pokedex-setup-game", () => update(0, "languages"));
  window.addEventListener("pokedex-setup-complete", () => activate(1, "catalog"));
  window.addEventListener("pokedex-catalog-start", () => activate(1, "catalog"));
  window.addEventListener("pokedex-catalog-ready", () => activate(2, "data"));
  window.addEventListener("pokedex-data-ready", () => activate(3, startup.selected ? "entry" : "list"));
  window.addEventListener("pokedex-entry-start", (event) => {
    if (startup.completed === 2 && startup.active === 2) startup.selected = event.detail?.selected !== false;
    else if (startup.completed === 3 && startup.active === 3) {
      startup.selected = event.detail?.selected !== false;
      update(3, startup.selected ? "entry" : "list");
    }
  });
  window.addEventListener("pokedex-ready", () => {
    if (globalThis.pokedexPage?.ready) activate(4, "interface");
  });
  window.addEventListener("pokedex-explorer-ready", () => {
    if (globalThis.pokedexExplorer?.ready) update(4, "refinements");
  });
  window.addEventListener("pokedex-explorer-refined", dismiss);
  window.addEventListener("pokedex-startup-error", (event) => fail(event.detail));
  window.addEventListener("unhandledrejection", (event) => fail(event.reason));
  window.addEventListener("error", (event) => {
    const target = event.target;
    if (target?.tagName === "SCRIPT" || (target?.tagName === "LINK" && target.rel === "stylesheet")) {
      fail(new Error(`${target.src || target.href}: resource failed to load`));
    } else if (event.error) fail(event.error);
  }, true);
  const observer = new MutationObserver(() => {
    render();
    if (byId("app")) observer.disconnect();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", () => {
    observer.disconnect();
    render();
    byId("c-startup-retry")?.addEventListener("click", () => location.reload());
    byId("c-startup-close")?.addEventListener("click", () => {
      const message = byId("c-startup-message")?.textContent ?? `${labels().failure}: ${startup.error?.message ?? startup.error}`;
      byId("c-startup")?.remove();
      unblock();
      const banner = byId("banner");
      if (banner && !banner.textContent.trim()) banner.textContent = message;
      if (banner) banner.hidden = !banner.textContent.trim();
      const target = banner && !banner.hidden ? banner : byId(byId("c-list-bar")?.hidden === false ? "c-list-search" : "x-search");
      if (target === banner) banner.tabIndex = -1;
      target?.focus({ preventScroll: true });
    });
    byId("c-startup")?.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && startup.failed) byId("c-startup-close")?.click();
    });
    if (startup.failed) showFailure();
  }, { once: true });
  globalThis.pokedexStartup = {
    dismiss, setLanguage,
    snapshot: () => ({ completed: startup.completed, active: startup.active, detail: startup.detail,
      selected: startup.selected, failed: startup.failed, complete: startup.complete, language }),
  };
  render();
})();
