// Hide the card while forwarding completes.
(() => {
  const script = document.currentScript;
  const target = script && script.dataset.destination;
  if (!target) return;
  const destination = new URL(target, document.baseURI);
  const language = new URL(document.baseURI).searchParams.get("lang");
  if (language && /^[a-z]{2,3}(?:-[A-Za-z0-9]+)?$/.test(language)) {
    const parts = destination.hash.split("/");
    parts[1] = language;
    destination.hash = parts.join("/");
  }
  let theme = "";
  try {
    theme = localStorage.getItem("gen3tl-theme") || "";
  } catch (e) {
    // the system theme applies
  }
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-forwarding", "");
  window.setTimeout(() => document.documentElement.removeAttribute("data-forwarding"), 4000);
  if (destination.origin === new URL(document.baseURI).origin) {
    history.replaceState(history.state, "", destination.href);
    window.location.reload();
  } else {
    window.location.replace(destination.href);
  }
})();
