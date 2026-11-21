import { el, updateChildren, setText } from "../dom.js";
import { t, languageName, formatNumber, chosenLanguage } from "../ui.js";
import { setToolContext } from "../site-shell.js";
import { workspaceLanguage } from "../start.js";
import { $, state, VIEW_NAMES, trail, isSite, DATA, recall } from "./state.js";





export function showStart(on) {
  $("start").hidden = !on;
  $("app").hidden = on;
  if (on) document.documentElement.removeAttribute("data-editor-route");
  else document.documentElement.setAttribute("data-editor-route", "");
  document.body.classList.toggle("in-start", on);
  $("start-error").hidden = true;
  setText($("start-error"), "");
  if (on) setText(document.querySelector("title"), t("Open the editor"));
}




export function option(o, choose) {
  const children = [el("span", { class: "radio" }),
    el("div", {}, el("div", { class: "t", textContent: o.title }), el("div", { class: "m", textContent: o.meta }))];
  if (o.percent !== undefined && o.percent !== null) {
    const fill = el("i");
    fill.style.width = `${o.percent}%`;
    children.push(el("div", { class: "progress" }, el("div", { class: "bar" }, fill),
      el("span", { class: "pct", textContent: `${o.percent}%` })));
  } else if (o.chip) children.push(el("span", { class: `chip ${o.chip[0]}`, textContent: o.chip[1] }));
  return el("button", {
    type: "button", class: `opt${o.selected ? " is-selected" : ""}${o.muted ? " is-muted" : ""}`, role: "radio",
    "aria-checked": String(o.selected), disabled: Boolean(o.disabled), on: { click: choose },
  }, ...children);
}




/** Offer the way back while the entry came from elsewhere. */
export function showBack() {
  const from = state.lang ? trail.from : null;
  const b = $("go-back");
  b.hidden = !from;
  if (!from) return;
  setText(b, t(VIEW_NAMES[from]));
  b.title = t("Back to {view}", { view: t(VIEW_NAMES[from]) });
}




/** Translate the workspace message when no catalog exists. */
export function showEmpty() {
  const box = $("project-empty");
  if (!state.empty) return;
  const { key, vars, language, links } = state.empty;
  setText(box, t(key, { ...vars, ...(language ? { language: languageName(language) } : {}) }));
  if (links) sourceLinks(links, box);
}




/** Keep the selected project in the browser title. */
export function showTitle() {
  if ($("start").hidden) setToolContext(state.profile?.id, state.titleLanguage);
  if (!$("start").hidden) setText(document.querySelector("title"), t("Open the editor"));
  else if (state.titleLanguage) {
    setText(document.querySelector("title"), `${languageName(state.titleLanguage)} · ${isSite() ? t("Site interface") : state.profile.name} · ${t("Translation editor")}`);
  }
}




/** Offer source downloads for profiles without language catalogs. */
export function sourceLinks(profile, box) {
  const source = profile.source_catalog;
  if (!source) return;
  box.append(el("p", {
    textContent: t("{n} draft source entries are available for review. These candidates may include false positives and missing text. Translation builds remain unavailable.",
      { n: formatNumber(source.entries) }),
  }), el("a", { href: new URL(source.pot, DATA()).href, textContent: t("Download source catalog (.pot)"), download: "" }),
  document.createTextNode(" · "),
  el("a", { href: new URL(source.manifest, DATA()).href, textContent: t("View catalog identity and limitations"),
    target: "_blank" }));
}




export function editableLanguages(game) {
  return game?.languages.filter((lang) => lang.editable) ?? [];
}




export function preferredCatalog(game, preferred = null) {
  const candidates = [preferred, game ? recall(`lang:${game.id}`) : null, chosenLanguage()];
  return candidates.find((code) => workspaceLanguage(game, code)) ?? editableLanguages(game)[0]?.code ?? null;
}




export function fillCatalogSelect(id, game, code) {
  const select = $(id);
  const languages = editableLanguages(game);
  updateChildren(select, ...languages.map((lang) => el("option", { value: lang.code, textContent: lang.name })));
  select.value = workspaceLanguage(game, code)?.code ?? "";
  select.disabled = !languages.length;
}




export function showSubmit() {
  $("submit").disabled = false;
}
