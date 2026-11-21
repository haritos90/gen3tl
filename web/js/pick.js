// Keep explicit tool requests on their destination.

import { setToolContext, startPage } from "./site-shell.js";
import { el } from "./dom.js";
import { actionsOf, gamesModel, LANGUAGE_STATES, languageName, loadSources, pokedexLanguages, progressTitle,
  STAGE_DETAILS, SURFACES } from "./site-model.js";
import { chosenGame, gameHash, loadRegistry, rememberGame, siteRoute } from "./registry.js";
import { installSiteFonts } from "./site-fonts.js";
import { chosenLanguage, errorText, N_, onSiteLanguage, t } from "./ui.js";

export { actionsOf, pokedexLanguages, SURFACES } from "./site-model.js";

const $ = (id) => document.getElementById(id);
const dexLanguages = new Map();

const LANGUAGE_COLUMNS = [N_("Language"), N_("Status"), N_("Translated"), N_("Approved")];

/** One table row per language: name, status, translated and approved shares. */
export function languageRows(game) {
  return game.languages.map((l) => {
    const state = LANGUAGE_STATES[l.state];
    return { name: l.name, tone: state.tone, status: t(state.label), translated: l.entries ? `${l.translated}%` : "—",
      percent: l.entries ? `${l.percent}%` : "—", title: l.entries ? progressTitle(l.progress) : "" };
  });
}

// Explicit roles survive the grid layout of narrow screens.
const languageRow = (props, name, status, ...numbers) => el("tr", { role: "row", ...props },
  el("th", { scope: "row", role: "rowheader" }, name), el("td", { role: "cell" }, status),
  ...numbers.map((number) => el("td", { role: "cell", class: "num", ...number })));

function languageTable(rows) {
  return el("table", { class: "langs", role: "table" },
    el("thead", { role: "rowgroup" }, el("tr", { role: "row" }, ...LANGUAGE_COLUMNS.map((text) => el("th", {
      scope: "col", role: "columnheader", textContent: t(text) })))),
    el("tbody", { role: "rowgroup" }, ...rows));
}

function card(game, next, selected) {
  const stage = STAGE_DETAILS[game.stage];
  const keys = actionsOf(game, next);
  const codes = next === "pokedex" && keys.includes("pokedex") ? pokedexLanguages(game) : [];
  const code = dexLanguages.get(game.id) ?? (codes.includes(chosenLanguage()) ? chosenLanguage() : codes[0] ?? game.sourceLanguage);
  dexLanguages.set(game.id, code);
  const acts = keys.map((k, i) => el("a", {
    class: `btn${k === next ? " primary" : ""}${i || !next ? " sm" : ""}`, href: SURFACES[k].url(game, code),
    textContent: t(SURFACES[k].label), on: { click: () => rememberGame(game.id) },
  }));
  const language = codes.length ? el("select", { class: "select mini", value: code },
    ...codes.map((value) => el("option", { value, selected: value === code, textContent: languageName(value) }))) : null;
  language?.addEventListener("change", () => {
    dexLanguages.set(game.id, language.value);
    acts[keys.indexOf("pokedex")].href = SURFACES.pokedex.url(game, language.value);
  });
  const base = [game.engineLabel, game.sha1 ? `${game.sha1.slice(0, 8)}…${game.sha1.slice(-6)}` : ""].filter(Boolean);
  return el("article", { class: `g${selected ? " is-selected" : ""}${game.stage === "translation" ? "" : " muted-row"}`,
    ...(selected ? { "aria-current": "true" } : {}) },
    el("div", {}, el("h2", { class: "game-title", title: t(stage.description), textContent: game.name }),
      el("p", { class: "base" }, base.length ? base.join(" · ") : ""),
      game.error ? el("p", { class: "small muted", textContent: t(game.error) }) : null),
    languageTable(languageRows(game).map((row) => languageRow({ class: row.tone }, row.name,
      el("span", { class: `chip ${row.tone}`, textContent: row.status }),
      ...[row.translated, row.percent].map((textContent) => ({ ...(row.title ? { title: row.title } : {}), textContent }))))),
    el("div", { class: "sel" }, language ? el("label", { class: "field" },
      el("span", { textContent: t("Pokédex language") }), language) : null,
    !next ? el("a", { class: "btn primary", href: `./${gameHash(game.id)}`, textContent: t("Choose this game"),
      on: { click: () => rememberGame(game.id) } }) : null,
    next && !keys.includes(next) ? el("p", { class: "small muted",
      textContent: t("{surface} is not available for {game}.", { surface: t(SURFACES[next].title), game: game.name }) }) : null,
    keys.length ? el("div", { class: "acts" }, ...acts)
      : el("span", { class: "small muted", textContent: t("No surface yet") }),
    game.stage !== "translation" && !game.released
      ? el("span", { class: "small muted", textContent: t("no patch yet") }) : null));
}

function placeholderCard(game, next) {
  const surfaces = next ? [SURFACES[next], ...Object.values(SURFACES).filter((surface) => surface !== SURFACES[next])]
    : Object.values(SURFACES);
  const actions = game.languages.length ? surfaces.map((surface, index) => el("span", {
    class: `btn${index ? " sm" : " primary"} skeleton`, textContent: t(surface.label),
  })) : [el("span", { class: "small muted", textContent: t("No surface yet") })];
  return el("article", { class: `g${game.stage === "translation" ? "" : " muted-row"}`, "aria-busy": "true" },
    el("div", {}, el("h2", { class: "game-title", textContent: game.name }), el("p", { class: "base", textContent: "\u00a0" })),
    languageTable(game.languages.map(() => languageRow({}, el("span", { class: "skeleton", textContent: "\u00a0" }),
      el("span", { class: "chip plain skeleton", textContent: "\u00a0" }), { textContent: "\u00a0" }, { textContent: "\u00a0" }))),
    el("div", { class: "sel" },
      next === "pokedex" && game.languages.length ? el("label", { class: "field" },
        el("span", { textContent: "\u00a0" }),
        el("select", { class: "select mini" }, el("option", { textContent: languageName(game.languages[0]) }))) : null,
      el("div", { class: "acts" }, ...actions),
      game.stage !== "translation" ? el("span", { class: "small muted", textContent: "\u00a0" }) : null));
}

async function main() {
  const requested = new URLSearchParams(location.search).get("next");
  const next = Object.hasOwn(SURFACES, requested) ? requested : null;
  const surface = next ? SURFACES[next] : null;
  let shown = null; // drawn again after a language switch
  const titles = () => {
    if (!surface) return;
    $("eyebrow").textContent = t("Choose your game · {surface}", { surface: t(surface.title) });
    document.title = $("eyebrow").textContent;
  };
  onSiteLanguage(() => {
    titles();
    shown?.();
  });
  await startPage({ reveal: () => {} });
  titles();
  try {
    const registry = await loadRegistry(new URL("data/registry.json", document.baseURI).href);
    $("games-placeholders").replaceChildren(...registry.games.map((game) => placeholderCard(game, next)));
    await installSiteFonts(document);
    const fontText = document.body.textContent;
    await Promise.all([document.fonts?.load('400 15px "IBM Plex Sans"', fontText),
      document.fonts?.load('600 30px "Bricolage Grotesque"', fontText)]);
    await document.fonts?.ready;
    document.siteStartup?.reveal();
    const sources = await loadSources(registry);
    const games = gamesModel(registry, sources);
    if (games.length === 1 && !registry.skipped.length && !sources.get(games[0].data)?.unavailable) {
      const game = games[0];
      rememberGame(game.id);
      const codes = pokedexLanguages(game);
      const code = [siteRoute(location.hash, registry).language, chosenLanguage()].find((code) => codes.includes(code));
      const target = surface?.allowed(game) ? surface.url(game, code)
        : `./${next ? `?unavailable=${next}` : ""}${gameHash(game.id)}`;
      location.replace(target);
      return;
    }
    const { game: selected, route } = chosenGame(registry, location.hash);
    setToolContext(selected?.id, route.language);
    shown = () => {
      $("games-error").hidden = !registry.skipped.length;
      $("games-error").textContent = registry.skipped.length ? t("A game could not be loaded. Reload to try again.") : "";
      $("games").replaceChildren(...games.map((g) => card(g, surface ? next : null, g.id === selected?.id)));
    };
    shown();
  } catch (e) {
    console.error(e);
    shown = () => {
      $("games-error").textContent = t("The game list could not be loaded: {error}", { error: errorText(e) });
    };
    shown();
    $("games-error").hidden = false;
  } finally {
    document.siteStartup?.reveal();
    $("games-loading").hidden = true;
    $("games-placeholders").hidden = true;
  }
}

if (typeof document !== "undefined" && document.getElementById("games")) main();
