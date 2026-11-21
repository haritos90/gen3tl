import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

test("keyed language changes refresh grouped names and retain registry criteria", () => {
  const script = `
    import assert from "node:assert/strict";
    import { fakeFetch, fakeStorage, storedLanguage } from ${JSON.stringify(new URL("./fixture.mjs", import.meta.url).href)};
    import { useSiteLanguage } from ${JSON.stringify(new URL("../js/ui.js", import.meta.url).href)};
    const fake = () => ({ children: [], value: "", textContent: "", dataset: {}, style: {}, hidden: false,
      scrollTop: 0, clientHeight: 400, listeners: {}, classList: { toggle() {} },
      get options() { return this.children; }, addEventListener(name, fn) {
        const previous = this.listeners[name]; this.listeners[name] = event => { previous?.(event); fn(event); };
      },
      setAttribute(key, value) { this[key] = value; }, append(...kids) { this.children.push(...kids.flat()); },
      replaceChildren(...kids) { this.children = kids.flat(); } });
    const elements = new Map();
    const $ = id => { if (!elements.has(id)) elements.set(id, fake()); return elements.get(id); };
    const title = { ...fake(), dataset: { ui: "Pokédex · gen3tl" } };
    const document = { ...fake(), baseURI: "https://fixture/", getElementById: $,
      querySelector: selector => selector === "title" ? title : null, querySelectorAll: () => [],
      createElement: fake, createTextNode: text => ({ text }) };
    const location = new URL("https://fixture/pokedex#game/ru/species/1056");
    const history = { state: null, pushState(state, title, url) { this.replaceState(state, title, url); }, replaceState(state, title, url) {
      this.state = state; location.href = new URL(url, location.href).href;
    } };
    const storage = fakeStorage();
    const base = "https://fixture/data/";
    const dataset = { species: [
      { slot: 990, dex: 773, base_species: 990, name: "species:990", types: [0], base_stats: { hp: 95 } },
      { slot: 1056, dex: 773, base_species: 990, name: "species:1056", form_name: "types:10", types: [10], base_stats: { hp: 95 } },
    ], types: [{ id: 0 }, { id: 10 }], moves: [
      { id: 1, name: "moves:1", type: 0, power: 40, accuracy: 100 },
      { id: 2, name: "moves:2", type: 10, power: 90, accuracy: 100 },
    ] };
    const files = {
      [base + "registry.json"]: { schema: 1, games: [
        { id: "game", name: "Game", stage: "translation", languages: ["ru", "fr"], data: "./" }] },
      [base + "languages.json"]: { profiles: [{ id: "game", name: "Game", pokedex: "dex.json" }],
        languages: ["ru", "fr"].map(code => ({ profile: "game", code, index: "game/" + code + "/index.json" })) },
      [base + "site/catalogs.json"]: { schema: 1, catalogs: [{ code: "ru" }, { code: "fr" }] },
      [base + "dex.json"]: dataset,
      [base + "game/keys.json"]: { ids: { "species:990": "table:species/990", "species:1056": "table:species/1056",
        "types:0": "table:types/0", "types:10": "table:types/10" } },
    };
    for (const [code, name, fire] of [["ru", "Силвалли", "Огонь"], ["fr", "Silvallié", "Feu"]]) {
      const entries = [990, 1056].map(id => ({ id: "table:species/" + id, source_text: "Silvally", translation: name }));
      entries.push({ id: "table:types/0", source_text: "Normal", translation: "Normal" },
        { id: "table:types/10", source_text: "Fire", translation: fire });
      files[base + "site/" + code + ".json"] = { schema: 1, language: code, strings: {} };
      Object.assign(files, storedLanguage(base + "game/" + code + "/index.json", { profile: "game", language: code,
        chunks: [{ file: "source.po", path: "entries/0.json", entries: entries.length }] }, entries));
    }
    const fetcher = fakeFetch(files);
    Object.assign(globalThis, { document, location, history, fetch: fetcher, localStorage: storage,
      window: { ...fake(), scrollY: 0, innerWidth: 1400, scrollTo() {} }, requestAnimationFrame: fn => fn() });
    $("x-history-back").parentElement = fake();
    const settle = async () => { for (let i = 0; i < 50; i++) await new Promise(resolve => setImmediate(resolve)); };
    const find = (node, id) => node.id === id ? node : (node.children ?? []).map(n => find(n, id)).find(Boolean);
    const text = node => typeof node === "string" ? node : node.textContent || node.text || (node.children ?? []).map(text).join("");
    await import(${JSON.stringify(new URL("../js/dexpage.js?grouped-keyed-language", import.meta.url).href)});
    await settle();
    assert.equal($("banner").textContent, "");
    assert.equal($("x-name").textContent, "#1056 Силвалли Огонь");
    const type = find($("x-filters"), "x-filter-species-type");
    type.value = "10"; type.listeners.change();
    const hp = find($("x-filters"), "x-filter-species-stat-a");
    hp.value = "95"; hp.listeners.input();
    assert.equal(find($("x-filters"), "x-filter-count-species").textContent, "1 / 1");
    for (const [code, name, fire] of [["fr", "Silvallié", "Feu"], ["ru", "Силвалли", "Огонь"]]) {
      await useSiteLanguage(code); await settle();
      assert.equal($("x-name").textContent, "#1056 " + name + " " + fire);
      assert.equal(location.hash, "#game/" + code + "/species/1056");
      assert.equal(location.pathname, "/share/game/" + code + "/species/1056/");
      assert.equal(find($("x-filters"), "x-filter-species-type").value, "10");
      assert.equal(find($("x-filters"), "x-filter-species-stat-a").value, "95");
      assert.equal(find($("x-filters"), "x-filter-count-species").textContent, "1 / 1");
      assert.ok(text(find($("x-filters"), "x-filter-species-type")).includes(fire));
      const saved = JSON.parse(storage.getItem("gen3tl-dex-last:game:" + code));
      assert.equal(saved.modes.species.filters.type, "10");
      $("x-search").value = name + " " + fire; $("x-search").listeners.input();
      assert.equal(find($("x-filters"), "x-filter-count-species").textContent, "1 / 1");
      $("x-search").value = "unknown species";
      $("x-search").listeners.input();
      assert.equal(find($("x-filters"), "x-filter-count-species").textContent, "0 / 1");
      $("x-search").value = ""; $("x-search").listeners.input();
    }
    $("x-mode-moves").listeners.click(); await settle();
    const moveType = find($("x-filters"), "x-filter-moves-type");
    moveType.value = "10"; moveType.listeners.change();
    const power = find($("x-filters"), "x-filter-moves-power-a");
    power.value = "80"; power.listeners.input();
    assert.equal(find($("x-filters"), "x-filter-count-moves").textContent, "1 / 2");
    await useSiteLanguage("fr"); await settle();
    assert.equal(find($("x-filters"), "x-filter-count-moves").textContent, "1 / 2");
    assert.equal(find($("x-filters"), "x-filter-moves-type").value, "10");
    assert.equal(find($("x-filters"), "x-filter-moves-power-a").value, "80");
    assert.ok(text(find($("x-filters"), "x-filter-moves-type")).includes("Feu"));
    assert.equal($("banner").textContent, "");
    assert.ok(fetcher.calls.includes(base + "game/keys.json"));
  `;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], { encoding: "utf8", timeout: 10000 });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
});
