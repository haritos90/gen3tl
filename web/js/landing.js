// Render the landing page from shared game data.

import { el } from "./dom.js";
import { chosenGame, pickerURL, recallGame, REPOSITORY, siteRoute } from "./registry.js";
import { actionsOf, gamesModel, hrefs, LANGUAGE_STATES, languageMeta, languageName, progressTitle, STAGE_DETAILS, SURFACES } from "./site-model.js";
import { N_, formatNumber, t, textParts } from "./ui.js";

export { compareVersions, gameModel, hrefs, languageMeta, languageModel, languageName, loadSources, measured,
  percentOf, progressTitle, translatedOf } from "./site-model.js";

/** Preserve registry order across all stages. */
export function landingModel(registry, sources, hash = "", storage) {
  const games = gamesModel(registry, sources);
  const current = chosenGame({ ...registry, games }, hash, storage).game;
  for (const game of games) game.selected = game.id === current?.id;
  const toolkit = [...sources.values()].find((s) => s.manifest?.toolkit)?.manifest.toolkit ?? null;
  return { games, toolkit, current, site: registry.site };
}

/** Require a choice before central landing content. */
export function landingRedirect(registry, hash = "", storage) {
  const route = siteRoute(hash, registry);
  const ids = [route.game, recallGame(storage)];
  return registry.games.length > 1 && !registry.games.some((game) => ids.includes(game.id)) ? pickerURL("home") : null;
}

/** Group hashes into readable eight-character sections. */
export const grouped = (hash) => hash.match(/.{1,8}/g).join(" ");

/** The approved share drawn over the translated share. */
export function shareBar(translated = 0, approved = 0) {
  const fills = [el("i", { class: "more" }), el("i")];
  fills[0].style.width = `${translated}%`;
  fills[1].style.width = `${approved}%`;
  return el("div", { class: "bar" }, ...fills);
}

function languageRow(game, lang) {
  const { tone, label } = LANGUAGE_STATES[lang.state];
  const links = hrefs(game, lang);
  const acts = el("div", { class: "acts" },
    SURFACES.patch.allowed(game, lang) ? el("a", { class: "btn sm primary", href: links.patch, textContent: t("Patch") }) : null,
    SURFACES.pokedex.allowed(game, lang)
      ? el("a", { class: "btn sm", href: links.pokedex, textContent: t("Pokédex") }) : null,
    SURFACES.editor.allowed(game, lang) ? el("a", { class: "btn sm", href: links.editor, textContent: t("Edit") }) : null,
    SURFACES.save.allowed(game, lang) ? el("a", { class: "btn sm", href: links.save, textContent: t("Convert a save") }) : null);
  return el("div", { class: "lang" },
    el("div", {}, el("span", { class: "name", textContent: lang.name }), el("span", { class: `chip ${tone}`, textContent: t(label) }),
      el("div", { class: "meta", textContent: languageMeta(lang) })),
    acts,
    lang.editable ? el("div", { class: "progress", title: progressTitle(lang.progress) },
      shareBar(lang.translated, lang.percent), el("span", { class: "pct", textContent: `${lang.translated}%` })) : null);
}

export function languageRequestURL(game, site = null) {
  if (Object.hasOwn(game.links ?? {}, "new_language")) return game.links.new_language;
  if (site && Object.hasOwn(site, "new_language_url")) return site.new_language_url;
  const repository = game.repository ?? site?.repository;
  if (!REPOSITORY.test(repository ?? "")) return null;
  const url = new URL(`https://github.com/${repository}/issues/new`);
  url.searchParams.set("title", t("New translation language for {game}", { game: game.name }));
  url.searchParams.set("body", t("Language:\n\nHow I would like to contribute:\n"));
  return url.href;
}

function gameCard(game, site) {
  const { tone, label, description } = STAGE_DETAILS[game.stage];
  const actions = actionsOf(game).map((key, index) => el("a", {
    class: `btn sm${index === 0 ? " primary" : ""}`, href: SURFACES[key].url(game), textContent: t(SURFACES[key].label),
  }));
  const facts = el("dl", { class: "kv" });
  const fact = (k, v) => v && facts.append(el("dt", { textContent: k }), el("dd", {}, v));
  fact(t("Original ROM"), game.sha1 ? el("span", { class: "hash", textContent: `SHA-1 ${grouped(game.sha1)}` }) : null);
  fact(t("Catalog"), game.entries ? t("{n} texts", { n: formatNumber(game.entries) })
    : game.sourceEntries ? t("{n} draft source entries", { n: formatNumber(game.sourceEntries) }) : null);
  const compatibility = ["changed_bytes", "saves", "link_play"].map((key) => game.compatibility[key]).filter(Boolean);
  fact(t("Compatibility"), compatibility.length ? [...new Set(compatibility)].map((key) => t(key)).join(" ") : null);
  for (const [key, label] of [["project", t("Project")], ["content_license", t("Content licence")]]) {
    fact(label, game.links[key] ? el("a", { href: game.links[key], rel: "noreferrer", textContent: label }) : null);
  }
  const rows = game.languages.map((l) => languageRow(game, l));
  const issueURL = languageRequestURL(game, site);
  const issue = issueURL
    ? el("a", { href: issueURL, textContent: t("request a language"), rel: "noreferrer" })
    : el("span", { "aria-disabled": "true", title: t("Issue tracker not available yet"), textContent: t("request a language") });
  return el("article", { class: "game card", ...(game.selected ? { "aria-current": "true" } : {}) },
    el("div", { class: "game-summary" },
      game.engineLabel ? el("p", { class: "eyebrow", textContent: game.engineLabel }) : null,
      el("h3", { class: "title", textContent: game.name }),
      el("span", { class: `chip ${tone}`, textContent: t(label) }),
      el("p", { class: "small muted", textContent: t(description) }),
      facts,
      el("div", { class: "acts" }, ...(actions.length ? actions
        : [el("span", { class: "small muted", textContent: t("No surface yet") })])),
      game.error ? el("p", { class: "small muted", textContent: t("Data unavailable: {error}", { error: t(game.error) }) }) : null),
    el("div", { class: "langs" }, ...(rows.length ? rows
      : [el("p", { class: "small muted", textContent: t("No languages available yet.") })])),
    el("p", { class: "game-language-request small muted" },
      ...textParts(t("Missing your language? You can {issue}."), { issue })));
}

function placeholderLanguage(code) {
  return el("div", { class: "lang" },
    el("div", {}, el("span", { class: "name", textContent: languageName(code) }),
      el("span", { class: "chip plain skeleton", textContent: "\u00a0" }),
      el("div", { class: "meta", textContent: "\u00a0" })),
    el("div", { class: "acts" }, el("span", { class: "btn sm skeleton", textContent: "\u00a0" }),
      el("span", { class: "btn sm skeleton", textContent: "\u00a0" })),
    el("div", { class: "progress" }, shareBar(), el("span", { class: "pct", textContent: "\u00a0" })));
}

function placeholderCard(game) {
  const facts = el("dl", { class: "kv" });
  for (let index = 0; index < 3; index += 1) {
    facts.append(el("dt", { textContent: "\u00a0" }), el("dd", { textContent: "\u00a0" }));
  }
  const actions = Array.from({ length: 4 }, () => el("span", { class: "btn sm skeleton", textContent: "\u00a0" }));
  return el("article", { class: "game card", "aria-busy": "true" },
    el("div", { class: "game-summary" },
      game.engine ? el("p", { class: "eyebrow", textContent: game.engine }) : null,
      el("h3", { class: "title", textContent: game.name }),
      el("span", { class: "chip plain skeleton", textContent: "\u00a0" }),
      el("p", { class: "small muted", textContent: STAGE_DETAILS[game.stage].description }),
      facts, el("div", { class: "acts" }, ...actions)),
    el("div", { class: "langs" }, ...game.languages.map(placeholderLanguage)),
    el("p", { class: "game-language-request small muted", textContent: "\u00a0" }));
}

/** Render registry-known cards before their manifests arrive. */
export function renderLoading(registry, $) {
  $("games-placeholders").replaceChildren(...registry.games.map(placeholderCard));
}

/** Render copy declared by the registry before slower data. */
export function renderCopy(current, $) {
  const copy = current?.landing ?? {};
  const show = (id, key) => {
    const node = $(id);
    node.textContent = key ? t(key) : "";
    node.hidden = !key;
  };
  show("landing-intro", copy.intro ?? N_("Translation patches for Game Boy Advance ROM hacks. Runs locally in your browser."));
  show("landing-editor", copy.editor ?? N_("Translate in the browser. Start a new language or improve one."));
  for (const section of ["changes", "review"]) {
    const visible = Boolean(copy[section] && copy[`${section}_title`]);
    $(`how-${section}`).hidden = !visible;
    show(`how-${section}-title`, visible ? copy[`${section}_title`] : null);
    show(`how-${section}-text`, visible ? copy[section] : null);
  }
}

/** Collapse an occupied placeholder without a jump. */
export function collapse(node) {
  if (!node.getBoundingClientRect) {
    node.hidden = true;
    return;
  }
  const height = node.getBoundingClientRect().height;
  if (!height) {
    node.hidden = true;
    return;
  }
  node.style.height = `${height}px`;
  node.style.overflow = "hidden";
  node.getBoundingClientRect();
  node.classList.add("is-empty");
  let timer;
  const finish = () => {
    clearTimeout(timer);
    node.hidden = true;
    node.classList.remove("is-empty");
    node.style.height = "";
    node.style.overflow = "";
  };
  node.addEventListener("transitionend", finish, { once: true });
  timer = setTimeout(finish, 800);
}

/** Render the landing page from its model. */
export function render(model, $, unavailable = null) {
  const { games, current, site } = model;
  $("games-list").replaceChildren(...games.map((game) => gameCard(game, site)));
  $("games-list").hidden = !games.length;
  $("games-empty").hidden = Boolean(games.length);
  const keys = current ? actionsOf(current) : [];
  if (!keys.length) collapse($("doors"));
  $("doors").replaceChildren(...Object.entries(SURFACES).map(([key, surface]) => {
    const door = $(`door-${key}`);
    door.hidden = !keys.includes(key);
    door.className = `door${key === keys[0] ? " primary" : ""}`;
    if (door.hidden) door.removeAttribute("href");
    else door.setAttribute("href", surface.url(current));
    return door;
  }));
  const missing = current && Object.hasOwn(SURFACES, unavailable) && !keys.includes(unavailable);
  $("surface-unavailable").hidden = !missing;
  $("surface-unavailable").textContent = missing
    ? t("{surface} is not available for {game}.", { surface: t(SURFACES[unavailable].title), game: current.name }) : "";
  renderCopy(current, $);
}
