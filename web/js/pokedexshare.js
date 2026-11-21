import { el } from "./dom.js";
import { t } from "./ui.js";
import { speciesShareURL } from "./pokedexshareurl.js";

const $ = (id) => document.getElementById(id);

function icon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const attributes = {
    viewBox: "0 0 24 24", width: "21", height: "21", fill: "none", stroke: "currentColor",
    "stroke-width": "1.7", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false",
  };
  for (const [key, value] of Object.entries(attributes)) svg.setAttribute(key, value);
  svg.innerHTML = '<path d="M12 16V3m0 0L7 8m5-5 5 5"/><path d="M5 13v7h14v-7"/>';
  return svg;
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return; } catch { /* Use the selection fallback. */ }
  }
  const field = el("textarea", { style: "position:fixed;inset:0 auto auto -9999px", readonly: "true" });
  field.value = text;
  document.body.append(field);
  field.select();
  const copied = document.execCommand("copy");
  field.remove();
  if (!copied) throw new Error("Clipboard unavailable");
}

function initialize() {
  const current = globalThis.pokedexExplorer;
  if (!current?.refined || !current.view?.loaded || !current.state?.profile || $("c-share")) return;
  const { view, state } = current;
  const status = el("span", { class: "c-share-status", role: "status", "aria-live": "polite", "aria-atomic": "true" });
  const button = el("button", { id: "c-share", type: "button" }, icon());
  $("x-pin").after(button, status);
  let reset = null;
  function sync() {
    button.replaceChildren(icon());
    button.title = navigator.share ? t("Share") : t("Copy preview link");
    button.setAttribute("aria-label", button.title);
  }
  async function share() {
    const record = view.record(view.cur);
    const languages = new Set(["en", ...(state.profile.languages ?? []).map((row) => row.code)]);
    if (view.mode !== "species" || !record || state.game?.id !== state.profile.id || !languages.has(state.code)) return;
    clearTimeout(reset);
    status.textContent = "";
    button.disabled = true;
    try {
      const url = speciesShareURL(state.game.data, state.profile.id, state.code, record.slot, state.profile.share_pages);
      if (navigator.share) {
        try { await navigator.share({ title: view.speciesName(record), url }); return; }
        catch (error) { if (error?.name === "AbortError") return; }
      }
      await copyText(url);
      status.textContent = t("Preview link copied");
    } catch {
      status.textContent = t("Could not copy the preview link");
    } finally {
      button.disabled = false;
      reset = setTimeout(() => { status.textContent = ""; }, 4000);
    }
  }
  button.addEventListener("click", share);
  const nativeSpecies = view.renderSpecies.bind(view);
  view.renderSpecies = (...args) => {
    const result = nativeSpecies(...args);
    sync();
    return result;
  };
  const nativeLanguage = view.setLang.bind(view);
  view.setLang = (...args) => {
    const result = nativeLanguage(...args);
    sync();
    return result;
  };
  sync();
  const url = new URL(location.href);
  if (url.searchParams.get("share") === "1") {
    url.searchParams.delete("share");
    history.replaceState(history.state, "", url.href);
    if (view.mode === "species" && view.record(view.cur) && history.state?.cScreen !== "list") {
      current.open({ kind: "species", id: view.cur });
    }
  }
}

window.addEventListener("pokedex-explorer-refined", initialize);
initialize();
