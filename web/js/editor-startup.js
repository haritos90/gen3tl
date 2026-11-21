// Reserve routed workspaces before registry data arrives.
(() => {
  const parts = location.hash.replace(/^#/, "").split("/").filter(Boolean);
  if (parts.length >= 2 && parts[0] !== "start") document.documentElement.setAttribute("data-editor-route", "");
})();
