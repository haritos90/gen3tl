// Render Pokédex cards using published game catalogs.

import { setToolContext, startPage } from "./site-shell.js";
import { Chunks, dirOf, getJson, loadAliases, loadIndex, projectProfiles, verifyProjectIndex } from "./data.js";
import { isDraft } from "./draft.js";
import { DATA_MODES } from "./pokedexdataview/pokedexdataview.js";
import { datasetPath, MODE_FILTERS, PokedexView, RECENT_MODES, speciesSort, spritePath } from "./pokedex/pokedex.js";
import { pokedexRouteURL } from "./pokedexshareurl.js";
import { FILTER_SETS, migrateFilters } from "./filters.js";
import { chosenGame, gameHash, loadRegistry, pickerURL, pokedexTail, rememberGame, siteRoute } from "./registry.js";
import { chosenLanguage, fillPage, languageName, plainText, prepareSiteLanguage, rememberSiteLanguage,
  showText, siteText, useSiteLanguage } from "./ui.js";

/** Resolve a route to its requested card. */
export function pageWant(tail) {
  if (!tail.length) return null;
  if (tail[0] === "species") return { kind: "species", id: tail[1] };
  if (tail[0] === "types") return { kind: "types", id: tail[1] };
  if (DATA_MODES.includes(tail[0])) return { kind: tail[0], id: tail[1] };
  return { kind: "species", id: tail[0] };
}

/** Prefer the configured source over legacy catalog order. */
export function pageLanguage(profile, wanted) {
  const languages = profile?.languages ?? [];
  return languages.find((l) => l.code === wanted)
    ?? profile?.source_texts ?? languages[0] ?? null;
}

/** Look up source and translated page text. */
export function textEntry(doc, id) {
  const pair = doc?.texts?.[id];
  return pair ? { source_text: pair[0], translation: pair[1] } : null;
}

/** Index catalog keys and their known ROM aliases. */
export function catalogRows(index, aliases = new Map()) {
  const rows = new Map(index.list.map((row, i) => [row.id, i]));
  for (const [id, key] of aliases) {
    if (!rows.has(id) && rows.has(key)) rows.set(id, rows.get(key));
  }
  return rows;
}

/** Identify draft text catalogs. */
export const textsDraft = (doc) => !!doc?.draft;

/** Reject text files belonging to another project. */
export function verifyProjectTexts(doc, profile, language) {
  if (doc?.schema !== 1 || doc.profile !== profile || doc.language !== language) {
    throw new Error(`Pokédex texts do not match ${profile}/${language}`);
  }
}

export const ENGLISH = "en";

const RECENT = 50; // entries kept, the strip shows what fits

/** Shared addresses override local move criteria. */
export function readMoveFilters(href) {
  try {
    const raw = new URL(href).searchParams.get("move-filters");
    if (raw === null) return null;
    const saved = migrateFilters("moves", JSON.parse(raw));
    for (const entry of FILTER_SETS.moves) {
      const value = saved[entry.key];
      if (value == null) continue;
      const valid = entry.kind === "chips" ? Array.isArray(value) && value.every((key) => typeof key === "string")
        : entry.kind === "number" ? typeof value === "object" && !Array.isArray(value)
          && (value.a == null || value.a !== "" && Number.isFinite(Number(value.a))
            && entry.operators.includes(value.op)
            && (value.op !== "between" || value.b != null && value.b !== "" && Number.isFinite(Number(value.b))))
          : typeof value === "string";
      if (!valid) delete saved[entry.key];
    }
    return saved;
  } catch { return null; }
}

/** Keep move criteria attached to their routes. */
export function moveFiltersURL(href, filters = null) {
  const url = new URL(href);
  if (filters === null) url.searchParams.delete("move-filters");
  else url.searchParams.set("move-filters", JSON.stringify(migrateFilters("moves", filters)));
  return url.href;
}

export class DexMemory {
  constructor(game, language, storage = () => globalThis.localStorage) {
    this.key = `gen3tl-dex-last:${game}:${language}`;
    this.storage = storage;
    this.available = false;
    let saved = null;
    try {
      const target = this.storage();
      const raw = target?.getItem(this.key);
      this.available = !!target;
      try { saved = JSON.parse(raw); } catch { /* Damaged history records must not prevent navigation. */ }
    } catch { /* Navigation still works when storage is refused. */ }
    const modes = Object.keys(MODE_FILTERS);
    const valid = (mode, id) => mode === "locations" ? typeof id === "string" && id.length > 0
      : Number.isSafeInteger(id) && id >= (mode === "species" ? 1 : 0);
    this.state = {
      mode: modes.includes(saved?.mode) ? saved.mode : "species",
      modes: Object.fromEntries(modes.map((mode) => {
        const previous = saved?.modes?.[mode] ?? { last: saved?.last?.[mode], filters: saved?.filters,
          ...(mode === "species" ? { recent: saved?.recent, favourites: saved?.favourites } : {}) };
        const ids = (value) => [...new Set(Array.isArray(value) ? value.filter((id) => valid(mode, id)) : [])];
        const favourites = ids(previous.favourites);
        const stored = migrateFilters(mode, previous.filters);
        return [mode, {
          last: valid(mode, previous.last) ? previous.last : null,
          filters: stored,
          ...(mode === "species" ? { sort: speciesSort(previous.sort) } : {}),
          filtersOpen: previous.filtersOpen !== false,
          recent: RECENT_MODES.includes(mode) ? ids(previous.recent).filter((id) => !favourites.includes(id)).slice(0, RECENT) : [],
          favourites,
        }];
      })),
    };
  }

  save() {
    if (!this.available) return;
    try { this.storage().setItem(this.key, JSON.stringify(this.state)); }
    catch { this.available = false; }
  }

  want(route) {
    if (route?.id != null) return route;
    const kind = route?.kind ?? this.state.mode;
    return { kind, id: this.state.modes[kind].last };
  }

  open(kind, id) {
    if (id != null && kind !== "locations") id = Number(id);
    this.state.mode = kind;
    const mode = this.state.modes[kind];
    if (id != null) {
      mode.last = id;
      if (RECENT_MODES.includes(kind) && !mode.favourites.includes(id)) mode.recent = [id, ...mode.recent.filter((value) => value !== id)].slice(0, RECENT);
    }
    this.save();
  }

  pin(kind, id) {
    const mode = this.state.modes[kind];
    const pinned = mode.favourites.includes(id);
    mode.favourites = pinned ? mode.favourites.filter((value) => value !== id) : [...mode.favourites, id];
    mode.recent = mode.recent.filter((value) => value !== id);
    this.save();
  }
}

/**
 * Resolve route and masthead language choices.
 * Missing catalogs fall back to English source strings.
 */
export function pageChoice(profile, wanted, chosen = ENGLISH) {
  const languages = profile?.languages ?? [];
  const fallback = pageLanguage(profile, null);
  if (!fallback) return null;
  if (wanted === ENGLISH) return { code: ENGLISH, catalog: fallback, missing: null };
  const routed = wanted ? languages.find((l) => l.code === wanted) : null;
  if (routed) return { code: routed.code, catalog: routed, missing: null };
  const open = chosen && chosen !== ENGLISH ? languages.find((l) => l.code === chosen) : null;
  if (open) return { code: open.code, catalog: open, missing: null };
  return { code: ENGLISH, catalog: fallback, missing: chosen && chosen !== ENGLISH ? chosen : null };
}

/** Find searchable catalogs with published text files. */
export function searchCatalogs(profile) {
  return (profile?.languages ?? []).filter((l) => l.texts);
}

/** Cache text requests while allowing failed requests to retry. */
export function textsCache(fetchDoc) {
  const files = new Map();
  return (lang) => {
    if (!files.has(lang.texts)) {
      const doc = Promise.resolve().then(() => fetchDoc(lang));
      files.set(lang.texts, doc);
      doc.catch(() => files.delete(lang.texts));
    }
    return files.get(lang.texts);
  };
}

/** Load search names, skipping catalogs that fail. */
export async function searchTexts(profile, file) {
  const catalogs = await Promise.all(searchCatalogs(profile).map((lang) => file(lang).then(
    (doc) => ({ code: lang.code, texts: doc?.texts ?? {}, aliases: doc?.search_aliases ?? {} }),
    (e) => {
      console.error(e);
      return null;
    })));
  return catalogs.filter(Boolean);
}

/** Build the hash for a Pokédex card. */
export function pageHash(game, language, id) {
  return gameHash(game, language, ...pokedexTail(id == null ? [] : String(id).split("/")));
}

if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const appBase = document.baseURI;
  const state = { registry: null, game: null, base: null, profile: null, lang: null, index: null, byId: new Map(),
    chunks: null, pokedex: null, generation: 0, code: null, missing: null, texts: null, textsPath: null,
    depth: 0, navigating: false, opening: false, leaveScroll: null, following: false, memory: null, requestedEntry: null,
    routing: false, missedRoute: null };
  const startupEvent = (name, detail) => window.dispatchEvent?.(detail === undefined
    ? new Event(name) : new CustomEvent(name, { detail }));
  // Shown and searched text share cached requests.
  const textsFile = textsCache(async (lang) => {
    const doc = await getJson(new URL(lang.texts, state.base).href);
    verifyProjectTexts(doc, state.profile.id, lang.code);
    return doc;
  });

  const banner = (text) => {
    $("banner").textContent = text;
    $("banner").hidden = !text;
  };

  const pokedex = new PokedexView({
    profile: () => state.profile,
    data: () => state.pokedex,
    load: async () => {
      if (!state.profile?.pokedex) return;
      const generation = state.generation;
      const data = await getJson(new URL(state.profile.pokedex, state.base).href);
      if (generation === state.generation) {
        state.pokedex = data;
        startupEvent("pokedex-data-ready");
      }
    },
    entry: async (id) => {
      if (state.texts) return textEntry(state.texts, id);
      const i = state.byId.get(id);
      if (i === undefined) return null;
      return state.chunks.entry(state.index.list[i]);
    },
    spriteSrc: (path) => new URL(spritePath(state.profile.pokedex, path), state.base).href,
    datasetFile: (file) => new URL(datasetPath(state.profile.pokedex, file), state.base).href,
    pageLabels: () => state.texts?.page ?? null,
    memory: () => state.memory,
    filtersChanged: (mode) => {
      if (mode !== "moves" || state.navigating || state.opening) return;
      const wanted = pageWant(siteRoute(location.hash, state.registry).tail);
      if (wanted?.kind === mode) history.replaceState(history.state, "", routeURL());
    },
    searchAliases: (id) => state.texts?.search_aliases?.[id] ?? [],
    searchTexts: () => searchTexts(state.profile, textsFile),
    draft: () => (state.texts ? textsDraft(state.texts) : isDraft(state.index)),
    beforeOpen: () => {
      if (!state.navigating) state.leaveScroll = scrollState();
    },
    setHash: (id) => {
      if (!(state.game && state.code)) return;
      const hash = pageHash(state.game.id, chosenLanguage(), id);
      const url = routeURL(hash);
      if (url === location.href) return;
      if (state.navigating || hash === location.hash) {
        history.replaceState(history.state, "", url);
      } else {
        // Save scroll position before card content changes.
        history.replaceState(state.leaveScroll ?? scrollState(), "", routeURL());
        state.leaveScroll = null;
        state.depth = (history.state?.depth ?? 0) + 1;
        history.pushState({ depth: state.depth }, "", url);
        backButton();
      }
    },
  }, $);

  function routeURL(hash = location.hash) {
    const route = siteRoute(hash, state.registry);
    const wanted = pageWant(route.tail);
    const languages = new Set(["en", ...(state.profile?.languages ?? []).map((row) => row.code)]);
    const slot = wanted?.kind === "species" ? Number(wanted.id) : null;
    const record = Number.isSafeInteger(slot) && state.pokedex ? pokedex.record(slot) : null;
    const species = route.game === state.game?.id && state.game.id === state.profile?.id
      && languages.has(route.language) && record?.slot === slot
      ? { game: route.game, language: route.language, slot, pages: state.profile.share_pages, data: state.game.data } : null;
    const url = pokedexRouteURL(appBase, location.href, hash, species);
    return moveFiltersURL(url, wanted?.kind === "moves" ? state.memory?.state.modes.moves.filters ?? {} : null);
  }

  /** Follow the page's own navigation history. */
  function backButton() {
    $("x-history-back").disabled = state.depth === 0;
  }

  const PANES = ["x-detail", "x-list"];

  /** Remember window and pane positions in history. */
  function scrollState() {
    return { ...history.state, depth: state.depth, scroll: { window: window.scrollY, panes: Object.fromEntries(PANES.map((id) => [id, $(id).scrollTop])) } };
  }

  function restoreScroll(saved) {
    if (!saved) return;
    requestAnimationFrame(() => {
      for (const [id, top] of Object.entries(saved.panes ?? {})) $(id).scrollTop = top;
      window.scrollTo(0, saved.window ?? 0);
    });
  }

  /** Update scroll positions without adding history entries. */
  function keepScroll() {
    let pending = false;
    const save = () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        try { history.replaceState(scrollState(), "", routeURL()); } catch { /* Detached history must not interrupt scrolling. */ }
      });
    };
    for (const id of PANES) $(id).addEventListener("scroll", save, { passive: true });
    window.addEventListener("scroll", save, { passive: true });
    // Remember position before input can replace content.
    const leave = () => { state.leaveScroll = scrollState(); };
    document.addEventListener("pointerdown", leave, true);
    document.addEventListener("keydown", leave, true);
  }

  function drawer(open) {
    if (open) $("app").dataset.drawer = "list";
    else delete $("app").dataset.drawer;
    $("show-list").setAttribute("aria-expanded", String(open));
    if (open) requestAnimationFrame(() => pokedex.renderList());
  }

  function wire() {
    $("x-prev").addEventListener("click", () => pokedex.step(-1));
    $("x-next").addEventListener("click", () => pokedex.step(1));
    $("x-list").addEventListener("scroll", () => requestAnimationFrame(() => pokedex.renderList()));
    $("x-list").addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        pokedex.step(e.key === "ArrowDown" ? 1 : -1);
      }
    });
    window.addEventListener("resize", () => pokedex.renderList());
    $("show-list").addEventListener("click", () => drawer($("app").dataset.drawer !== "list"));
    $("x-search").addEventListener("input", () => {
      if (window.innerWidth <= 760 && $("app").dataset.drawer !== "list") drawer(true);
    });
    for (const button of document.querySelectorAll("[data-drawer-close]")) {
      button.addEventListener("click", () => drawer(false));
    }
    $("x-list").addEventListener("click", (e) => {
      if (e.target.closest(".row") && window.innerWidth <= 760) drawer(false);
    });
    $("x-favourites").addEventListener("click", (event) => {
      if (event.target.closest(".dex-saved-open") && window.innerWidth <= 760) drawer(false);
    });
    $("x-history-back").addEventListener("click", () => history.back());
    keepScroll();
    let popstateURL = null;
    const followRoute = () => {
      // Start-up opens the address it reads; later changes wait.
      if (!state.routing) {
        state.missedRoute = location.href;
        return;
      }
      const route = siteRoute(location.hash, state.registry);
      if (route.game !== state.game?.id) {
        history.replaceState(history.state, "", pokedexRouteURL(appBase, location.href, location.hash));
        location.reload();
      } else if (route.language) {
        state.depth = history.state?.depth ?? 0;
        backButton();
        state.navigating = true;
        const saved = history.state?.scroll;
        if (pageWant(route.tail)?.kind === "moves" && state.memory) {
          state.memory.state.modes.moves.filters = readMoveFilters(location.href) ?? {};
          if (pokedex.mode === "moves") pokedex.restoreFilters("moves");
        }
        const opening = route.language !== chosenLanguage() ? openRoute(route.language, route.tail)
          : pokedex.goTo(pageWant(route.tail));
        return Promise.resolve(opening).then(() => restoreScroll(saved)).catch(fail)
          .finally(() => { state.navigating = false; });
      }
    };
    window.addEventListener("popstate", () => {
      const href = location.href;
      popstateURL = href;
      setTimeout(() => { if (popstateURL === href) popstateURL = null; }, 0);
      followRoute();
    });
    // A traversal can emit both events.
    window.addEventListener("hashchange", () => {
      if (location.href !== popstateURL) followRoute();
    });
    return followRoute;
  }

  /** Keep the current card across language switches. */
  function currentTail() {
    const route = siteRoute(location.hash, state.registry);
    return route.game === state.game?.id ? route.tail : [];
  }

  function fail(e) {
    console.error(e);
    banner(siteText("Cannot open the Pokédex: {error}", { error: e?.message ?? e }));
    $("status").textContent = "";
  }


  /** Label the data language and missing catalogs. */
  function showLanguage() {
    if (!state.code) return;
    showText($("data-lang"), "Game text: {language}", { language: languageName(state.code) });
    $("data-lang").hidden = false;
    $("data-note").hidden = !state.missing;
    if (state.missing) {
      showText($("data-note"), "No {language} catalog for this game: the English game text is shown.",
        { language: languageName(state.missing) });
    }
  }

  /** Open translated catalog text or English sources. */
  async function openLanguage(choice, tail = []) {
    const { code, catalog: lang, missing } = choice;
    const english = code === ENGLISH;
    state.generation += 1;
    state.opening = true;
    state.code = code;
    state.missing = missing;
    // View labels follow data language independently of site language.
    $("pokedex").lang = english ? ENGLISH : code;
    const modes = $("x-history-back").parentElement;
    if (modes) modes.lang = $("pokedex").lang;
    $("x-search").lang = $("pokedex").lang;
    fillPage();
    showLanguage();
    $("status").textContent = siteText("Loading {language}…", { language: languageName(code) });
    showTitle();
    setToolContext(state.game.id, chosenLanguage());
    const generation = state.generation;
    startupEvent("pokedex-catalog-start");
    try {
      if (lang.texts) {
        if (state.textsPath !== lang.texts || !state.texts) {
          const doc = await textsFile(lang);
          if (generation !== state.generation) return;
          state.texts = doc;
          state.textsPath = lang.texts;
        }
      } else if (state.lang?.index !== lang.index || !state.index) { // Older datasets expose text through the index.
        state.texts = null;
        state.textsPath = null;
        const url = new URL(lang.index, state.base).href;
        const index = await loadIndex(url);
        if (generation !== state.generation) return;
        verifyProjectIndex(index, state.profile.id, lang.code);
        const aliases = await loadAliases(new URL("../keys.json", url).href);
        if (generation !== state.generation) return;
        state.index = index;
        state.byId = catalogRows(index, aliases);
        state.chunks = new Chunks(new URL(dirOf(lang.index), state.base).href, index);
      }
      startupEvent("pokedex-catalog-ready");
      state.lang = lang;
      state.memory = new DexMemory(state.game.id, code);
      const filters = readMoveFilters(location.href);
      if (pageWant(tail)?.kind === "moves" && filters !== null) state.memory.state.modes.moves.filters = filters;
      state.navigating = true;
      const wanted = pageWant(tail);
      state.requestedEntry = wanted;
      pokedex.reset(state.memory.want(wanted));
      pokedex.setLang(english ? ENGLISH : "translation");
      history.replaceState({ ...history.state, depth: state.depth }, "", routeURL(gameHash(state.game.id, chosenLanguage(), ...tail)));
      try {
        startupEvent("pokedex-entry-start", { selected: wanted?.id != null });
        await pokedex.show();
        if (generation === state.generation && !pokedex.loaded) throw new Error($("x-count").textContent);
      } finally {
        if (generation === state.generation) state.navigating = false;
      }
      if (generation === state.generation) $("status").textContent = "";
    } catch (error) {
      if (generation === state.generation) throw error;
    } finally {
      if (generation === state.generation) state.opening = false;
    }
  }

  /** Open the routed language and synchronize the masthead. */
  async function openRoute(language, tail) {
    if (language && language !== chosenLanguage()) {
      state.following = true;
      try {
        rememberSiteLanguage(language);
        await useSiteLanguage(language);
      } finally {
        state.following = false;
      }
    }
    globalThis.pokedexStartup?.setLanguage(chosenLanguage());
    await openLanguage(pageChoice(state.profile, null, chosenLanguage()), tail);
  }

  /** Translate the tab title into the site language. */
  function showTitle() {
    if (!state.profile || !state.code) return;
    showText(document.querySelector("title"), "Pokédex · {game} · {language}",
      { game: state.profile.name, language: languageName(state.code) });
  }

  /** Keep the visible catalog until replacement texts are ready. */
  prepareSiteLanguage(async (chosen) => {
    if (!state.profile || !state.code || state.following) return;
    const choice = pageChoice(state.profile, null, chosen);
    const lang = choice.catalog;
    const generation = state.generation;
    let doc = null, index = null, chunks = null, byId = null;
    try {
      if (lang.texts) doc = await textsFile(lang);
      else {
        const url = new URL(lang.index, state.base).href;
        index = await loadIndex(url);
        verifyProjectIndex(index, state.profile.id, lang.code);
        byId = catalogRows(index, await loadAliases(new URL("../keys.json", url).href));
        chunks = new Chunks(new URL(dirOf(lang.index), state.base).href, index);
      }
      const ids = new Set([...pokedex.texts.keys(), ...Object.keys(doc?.texts ?? {})]);
      const texts = new Map(await Promise.all([...ids].map(async (id) => {
        const key = pokedex.catalogId(id);
        const row = byId?.get(key);
        const entry = doc ? textEntry(doc, key) : row == null ? null : await chunks.entry(index.list[row]);
        return [id, { source_text: entry?.source_text ?? "", translation: entry?.translation ?? "" }];
      })));
      return () => {
        if (generation !== state.generation) return;
        state.generation += 1;
        state.code = choice.code;
        state.missing = choice.missing;
        state.lang = lang;
        state.texts = doc;
        state.textsPath = lang.texts ?? null;
        if (index) Object.assign(state, { index, chunks, byId });
        const memory = new DexMemory(state.game.id, choice.code);
        if (state.memory) memory.state = structuredClone(state.memory.state);
        state.memory = memory;
        memory.save();
        if (state.opening || !pokedex.loaded || !state.pokedex) {
          openLanguage(choice, currentTail()).catch(fail);
          return;
        }
        state.navigating = false;
        $("status").textContent = "";
        pokedex.texts = texts;
        for (const id of ["pokedex", "x-search"]) $(id).lang = choice.code;
        $("x-history-back").parentElement.lang = choice.code;
        pokedex.setLang(choice.code === ENGLISH ? ENGLISH : "translation");
        history.replaceState(history.state, "", routeURL(gameHash(state.game.id, chosen, ...currentTail())));
        setToolContext(state.game.id, chosen);
        showLanguage();
        showTitle();
      };
    } catch (error) {
      fail(error);
      throw error;
    }
  });

  async function main() {
    let storage = null;
    try { storage = globalThis.localStorage; } catch { /* Navigation still works when storage is refused. */ }
    const shown = await startPage({ storage });
    globalThis.pokedexStartup?.setLanguage(shown);
    startupEvent("pokedex-setup-site");
    const followRoute = wire();
    state.registry = await loadRegistry(new URL("data/registry.json", appBase).href);
    state.missedRoute = null;
    const { game, route } = chosenGame(state.registry, location.hash, storage);
    if (!game) {
      location.replace(pickerURL("pokedex"));
      return;
    }
    try {
      state.game = game;
      state.base = game.data;
      rememberGame(game.id, storage);
      setToolContext(game.id, route.language);
      startupEvent("pokedex-setup-game");
      const manifest = await getJson(new URL("languages.json", game.data).href);
      state.profile = projectProfiles(manifest, { readOnly: true }).find((p) => p.id === game.id) ?? null;
      if (!state.profile?.pokedex) throw new Error(siteText("{game} has no Pokédex dataset yet.", { game: game.name }));
      if (!state.profile.languages.length) throw new Error(siteText("{game} has no language catalog yet.", { game: game.name }));
      startupEvent("pokedex-setup-complete");
      searchTexts(state.profile, textsFile);
      await openRoute(route.language, route.language ? route.tail : []);
    } finally {
      // A failed start still follows later addresses.
      state.routing = true;
      if (state.missedRoute) {
        // The opened card replaced the address meanwhile.
        history.replaceState(history.state, "", state.missedRoute);
        await followRoute();
      }
    }
  }

  globalThis.pokedexPage = { view: pokedex, state };
  main().then(() => {
    globalThis.pokedexPage.ready = true;
    window.dispatchEvent?.(new Event("pokedex-ready"));
  }).catch((error) => {
    fail(error);
    window.dispatchEvent?.(new CustomEvent("pokedex-startup-error", { detail: error }));
  });
}
