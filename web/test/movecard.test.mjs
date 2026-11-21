import assert from "node:assert/strict";

import { test } from "node:test";

import { setLabelSource } from "../js/ui.js";

import { PokedexView } from "../js/pokedex/pokedex.js";

import { moveCardContent } from "../js/movecard.js";

import { moveFields, moveMatches, PokedexDataView } from "../js/pokedexdataview/pokedexdataview.js";

import { FILTER_SETS } from "../js/filters.js";

import { moveCells, moveOf } from "../js/families.js";

import { STANDALONE_LABELS } from "./fixture.mjs";

const fake = () => ({
  children: [], className: "", textContent: "", title: "", attrs: {}, listeners: {}, open: false,
  addEventListener(ev, fn) { this.listeners[ev] = fn; },
  setAttribute(k, v) { this.attrs[k] = v; },
  append(...kids) { this.children.push(...kids.flat()); },
  replaceChildren(...kids) { this.children = [...kids.flat()]; },
  showModal() { this.open = true; }, close() { this.open = false; },
});

const texts = { "moves:7": "Fire Punch", "types:10": "Fire", "abilities:93": "Iron Fist", s1: "Hits with a fiery fist.\\pMay burn." };

const record = { id: 7, type: 10, damage_category: 0, power: 75, pp: 15, accuracy: 100, priority: 0, effect: 4, effect_key: "burn_hit",
  secondary_chance: 10, target: 0, target_key: "selected", flag_keys: ["makes_contact"], list_keys: ["punching"], description: "s1" };

test("flinch cards and filters consume declarations independently of raw effects", (context) => {
  globalThis.document = { createElement: () => fake() };
  context.after(() => { setLabelSource(null); delete globalThis.document; });
  const chance = FILTER_SETS.moves.find((field) => field.key === "flinchChance");
  for (const flinch_chance of [0, 1, 11, 21, 31, 100]) {
    for (const effect of [0, 31, 158, 500]) {
      const move = { ...record, effect, secondary_chance: 0, flinch_chance };
      const field = moveFields(move).find(([label]) => label === "Flinch chance");
      const popup = JSON.stringify(moveCardContent(move));
      assert.equal(!!field, flinch_chance > 0);
      assert.equal(popup.includes("Flinch chance"), flinch_chance > 0);
      assert.equal(moveMatches(move, { flinch: true }), flinch_chance > 0);
      assert.equal(chance.predicate(move, { op: ">=", a: 1 }), flinch_chance > 0);
      if (flinch_chance) {
        assert.equal(field[1], `${flinch_chance}%`);
        assert.ok(popup.includes(`${flinch_chance}%`));
      }
    }
  }
});

test("shared effect ids resolve consistently in popups, cards, lists and filters", (context) => {
  globalThis.document = { createElement: () => fake() };
  context.after(() => { setLabelSource(null); delete globalThis.document; });
  const field = FILTER_SETS.moves.find((entry) => entry.key === "effect");
  const moves = [
    { ...record, id: 1, effect: 51, effect_key: "defense_up_2", secondary_chance: 0 },
    { ...record, id: 2, effect: 51, effect_key: "defense_up_3", secondary_chance: 0 },
    { ...record, id: 3, effect: 53, effect_key: "special_attack_up_3", secondary_chance: 0 },
  ];
  for (const { code } of STANDALONE_LABELS.languages) {
    setLabelSource((key) => ({ text: `${key} ${code}`, game: false }));
    const options = field.options({ moves });
    assert.equal(options.length, 3);
    for (const move of moves) {
      const label = `page:effect:${move.effect_key} ${code}`;
      assert.ok(JSON.stringify(moveCardContent(move)).includes(label));
      assert.equal(moveFields(move).find(([key]) => key === `Effect ${code}`)[1], label);
      assert.equal(options.find((option) => option.value === move.effect_key).label, label);
      assert.deepEqual(moves.filter((row) => field.predicate(row, move.effect_key)).map((row) => row.id), [move.id]);
      assert.deepEqual(moves.filter((row) => moveMatches(row, { effect: move.effect_key })).map((row) => row.id), [move.id]);
      const view = { mode: "moves", filtered: [move], view: { text: () => "Demo" }, open() {} };
      assert.ok(JSON.stringify(PokedexDataView.prototype.row.call(view, 0)).includes(label));
    }
  }
  assert.equal(field.normalize("51"), "defense_up_2");
  assert.equal(field.predicate(moves[0], "51"), true);
  assert.equal(field.predicate(moves[1], "51"), false);
});

test("declared power controls cards, lists and every numeric filter", (context) => {
  globalThis.document = { createElement: () => fake() };
  context.after(() => { setLabelSource(null); delete globalThis.document; });
  const predicate = FILTER_SETS.moves.find((field) => field.key === "power").predicate;
  const cases = [
    { ...record, power: 100, power_kind: "fixed", power_formula: "page:power:modifier" },
    { ...record, power: 1, power_kind: "computed", power_formula: "page:power:computed" },
    { ...record, power: 150, power_kind: "computed", power_formula: "page:power:computed" },
    { ...record, power: 10, power_kind: "fixed", power_base: 160, power_formula: "page:power:constant" },
    { ...record, power: 1, power_kind: "fixed", power_formula: "page:power:modifier" },
    { ...record, power: 0 },
    record,
  ];
  for (const { code } of STANDALONE_LABELS.languages) {
    setLabelSource((key) => ({ text: `${key} ${code}`, game: false }));
    for (const move of cases) {
      const expected = move.power_kind === "computed" ? "—" : String((move.power_base ?? move.power) || "—");
      const popup = moveCardContent(move);
      assert.equal(popup.children[1].children[0].children[0].children[0], expected);
      const field = moveFields(move)[2][1];
      assert.equal(move.power_formula ? field.children[0] : field, expected);
      if (move.power_formula) {
        assert.ok(JSON.stringify(field).includes(`${move.power_formula} ${code}`));
        assert.ok(JSON.stringify(popup).includes(`${move.power_formula} ${code}`));
      }
      const view = { data: { moves: { [move.id]: move } }, view: { text: () => "" }, categoryIcon: () => null };
      const row = PokedexDataView.prototype.learnRow.call(view, { move: move.id }, "");
      assert.equal(row.children[4].children[0], expected);
      assert.equal(moveCells(moveOf([move]))[0], move.power_kind === "computed" ? `varies ${code}` : expected);
      assert.equal(predicate(move, { kind: "formula" }), move.power_kind === "computed");
      assert.equal(predicate(move, { kind: "fixed" }), move.power_kind !== "computed");
      for (const op of [">=", "<=", "=", "between"]) {
        assert.equal(predicate(move, { op, a: move.power_base ?? move.power, b: 200 }), move.power_kind !== "computed");
      }
      assert.equal(moveMatches(move, { minPower: 0 }), move.power_kind !== "computed");
    }
  }
});

function abilityView(context, entry = async () => null) {
  const element = (tag) => Object.assign(fake(), { tag, disabled: false,
    focus() { document.activeElement = this; }, contains(node) { return this.children.includes(node); } });
  globalThis.document = { body: element("body"), createElement: element };
  context.after(() => { setLabelSource(null); delete globalThis.document; });
  const species = { slot: 8, ability_details: [{ id: 3, name: "abilities:7", description: "s0000010" }] };
  const data = { abilities: [{ id: 3, name: "abilities:3", description: "s0000001" }] };
  const view = Object.create(PokedexView.prototype);
  Object.assign(view, { host: { data: () => data, entry }, generation: 0, texts: new Map(),
    record: () => species, catalogId: (id) => id,
    text(id) { const row = this.texts.get(id); return row?.translation || row?.source_text || ""; } });
  return { view, species };
}

test("party abilities use species text and share modal dismissal and focus behavior", async (context) => {
  const { view, species } = abilityView(context);
  const original = structuredClone(species);
  for (const localized of [false, true]) {
    setLabelSource((key) => localized && key === "Ability" ? { text: "Localized ability", game: false } : null);
    view.texts.set("abilities:7", { source_text: "Species name", translation: localized ? "Localized species name" : "" });
    view.texts.set("s0000010", { source_text: "Species description.\\nSecond line.", translation: localized ? "Localized description." : "" });
    for (const activation of ["click", "keyboard", "touch"]) {
      const link = view.abilityLink(8, 3);
      assert.equal(link.tag, "button");
      assert.equal(link.attrs.type, "button");
      assert.equal(link.attrs["aria-haspopup"], "dialog");
      assert.deepEqual(Object.keys(link.listeners), ["click"]);
      link.focus();
      await link.listeners.click({ detail: activation === "keyboard" ? 0 : 1, pointerType: activation === "touch" ? "touch" : "mouse" });
      const dialog = document.body.children[0];
      assert.equal(document.body.children.length, 1);
      assert.equal(dialog.open, true);
      assert.equal(dialog.attrs["aria-label"], localized ? "Localized ability" : "Ability");
      const content = JSON.stringify(dialog.children[1]);
      assert.match(content, /Species name|Localized species name/);
      assert.match(content, /Species description\. Second line\.|Localized description\./);
      assert.doesNotMatch(content, /abilities:3|s0000001/);
      if (activation === "click") dialog.children[0].listeners.click();
      else if (activation === "keyboard") dialog.listeners.cancel({ preventDefault() {} });
      else dialog.listeners.click({ target: dialog });
      assert.equal(dialog.open, false);
      assert.equal(document.activeElement, link);
    }
  }
  assert.deepEqual(species, original);
});

test("late ability text cannot replace another card or a newer language", async (context) => {
  const pending = new Map();
  const { view } = abilityView(context, (id) => new Promise((resolve) => pending.set(id, resolve)));
  view.texts.set("abilities:7", { source_text: "Name" });
  const opening = view.openAbility(8, 3);
  view.moveDialog.show({ tag: "another card" });
  pending.get("s0000010")({ source_text: "Old description" });
  await opening;
  assert.equal(view.moveDialog.node.children[1].tag, "another card");
  view.texts.delete("s0000010");
  const stale = view.openAbility(8, 3);
  view.generation += 1;
  pending.get("s0000010")({ source_text: "Stale description" });
  await stale;
  assert.equal(view.texts.has("s0000010"), false);
  view.moveDialog.close();
  const current = view.openAbility(8, 3);
  pending.get("s0000010")({ source_text: "Current description" });
  await current;
  assert.match(JSON.stringify(view.moveDialog.node.children[1]), /Current description/);
});

test("profile mechanics share localized stat names and appear once in the move popup", async () => {
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  try {
    const { moveCardContent } = await import("../js/movecard.js");
    const { moveFacts, moveFields, moveMechanics } = await import("../js/pokedexdataview/pokedexdataview.js");
    const labels = {
      "page:mechanics:demo:summary": "Current user type",
      "page:mechanics:demo:condition": "Condition met",
      "page:mechanics:demo:effect": "{attack}, {defense}, {speed}; {hp}",
      "page:mechanics:demo:note": "Verified note",
      "page:mechanics:demo:other": "Other condition",
      "page:mechanics:demo:other_effect": "Other effect",
      Attack: "Атака", Defense: "Защита", Speed: "Скорость", HP: "ОЖ",
    };
    setLabelSource((key) => labels[key] ? { text: labels[key], game: false } : null);
    const mechanics = { summary: "page:mechanics:demo:summary",
      rows: [{ condition: "page:mechanics:demo:condition", effect: "page:mechanics:demo:effect",
        notes: ["page:mechanics:demo:note"] },
      { condition: "page:mechanics:demo:other", effect: "page:mechanics:demo:other_effect" }] };
    const conditional = { ...record, mechanics };
    const node = moveCardContent(conditional, { text: (id) => texts[id] ?? "" });
    const card = JSON.stringify(node);
    assert.equal(moveFields(conditional).find(([key]) => key === "Target")[1], "Current user type");
    assert.ok(JSON.stringify(node.children.find(child => child.className === "kv dex-move-card-kv").children[1]).includes("Current user type"));
    assert.equal(moveFacts(conditional).find(([key]) => key === "Target")[1], 0);
    assert.equal(conditional.target, 0);
    assert.equal(card.split("Current user type").length - 1, 2);
    for (const text of ["Condition met", "Атака, Защита, Скорость; ОЖ", "Verified note"]) {
      assert.equal(card.split(text).length - 1, 1, text);
    }
    assert.ok(!card.includes("page:mechanics:"));
    assert.ok(!card.includes("{attack}"));
    assert.deepEqual(moveMechanics(record), []);
    const plain = JSON.stringify(moveCardContent(record));
    assert.ok(!plain.includes("dex-move-mechanics"));
    assert.ok(plain.includes("Selected target"));
    assert.equal(moveFields(record).find(([key]) => key === "Target")[1], "Selected target");
    assert.ok(!moveFacts(record).some(([key]) => key === "Target"));
    const rows = moveMechanics({ mechanics })[0].children[1].children[1].children;
    assert.ok(JSON.stringify(rows[0]).includes("Verified note"));
    assert.ok(!JSON.stringify(rows[1]).includes("Verified note"));
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

test("environment tables show one localized column with move links, type badges and place names", async () => {
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  try {
    const { moveCardContent } = await import("../js/movecard.js");
    const { moveFacts, moveFields, moveMechanics } = await import("../js/pokedexdataview/pokedexdataview.js");
    const labels = {
      "page:mechanics:demo:summary": "Depends on the place",
      "page:mechanics:demo:condition": "Terrain active", "page:mechanics:demo:effect": "Terrain row",
      "page:mechanics:demo:grass": "Tall grass", "page:mechanics:demo:named": "Water in {place}",
      "page:mechanics:demo:anywhere": "Anywhere", "page:mechanics:demo:in": "Tall grass in:",
      "page:mechanics:demo:sleep": "Puts the target to sleep", "page:mechanics:demo:lower": "Lowers {sp_attack}",
      "Sp. Attack": "Сп. атака", Environment: "Обстановка", Where: "Где", "Added effect": "Доп. эффект",
      "(one map)": "(одна карта)", "Also set by event scripts in:": "Также сценариями здесь:",
      "page:mechanics:demo:scene": "Tall grass in winter areas or where a script sets a snowfield:",
      "page:mechanics:demo:champion": "Trainer battles in the Champion’s room:",
      "page:mechanics:demo:persistence": "The script value persists across maps until a script rewrites it.",
    };
    setLabelSource((key) => labels[key] ? { text: labels[key], game: false } : null);
    const names = { ...texts, "mapsec:9": "Lake", "mapsec:4": "Grotto", "mapsec:7": "Peak", "mapsec:5": "Gym Town",
      "mapsec:8": "Champion’s room", "moves:85": "Thunderbolt", "types:12": "Grass" };
    const rows = [
      { environment: "page:mechanics:demo:grass", where: "page:mechanics:demo:in", sections: [9], maps: [{ map: "1.9", section: 4 }],
        also_script_maps: [{ map: "3.1", section: 7 }, { map: "3.2", section: 7 }] },
      { environment: "page:mechanics:demo:named", where: "page:mechanics:demo:champion", place: 9,
        map_code_maps: [{ map: "1.79", section: 8 }] },
      { environment: "page:mechanics:demo:grass", where: "page:mechanics:demo:scene", sections: [5],
        scene_maps: [{ map: "2.0", section: 5 }], script_maps: [{ map: "2.1", section: 5 }, { map: "4.4", section: 9 }] },
    ];
    const mechanics = (column, values) => ({ summary: "page:mechanics:demo:summary",
      rows: [{ condition: "page:mechanics:demo:condition", effect: "page:mechanics:demo:effect", notes: [] }],
      environments: { column, rows: rows.map((row, index) => ({ ...row, value: values[index] })),
        script_persistence: "page:mechanics:demo:persistence" } });
    const secret = { ...record, mechanics: mechanics("effect", ["page:mechanics:demo:sleep", "page:mechanics:demo:lower",
      "page:mechanics:demo:sleep"]) };
    const card = JSON.stringify(moveCardContent(secret, { text: (id) => names[id] ?? "",
      location: (id) => ({ link: names[`mapsec:${id}`] }) }));
    for (const text of ["Обстановка", "Где", "Доп. эффект", "Tall grass in:", "Water in Lake", "Puts the target to sleep",
      "Lowers Сп. атака", "(одна карта)", "Также сценариями здесь:", '"title":"1.9"', '{"link":"Grotto"}', '{"link":"Lake"}',
      "Tall grass in winter areas or where a script sets a snowfield:", "Trainer battles in the Champion’s room:",
      "The script value persists across maps until a script rewrites it.", '"title":"1.79"', '{"link":"Champion’s room"}',
      '"title":"3.1, 3.2"', '{"link":"Peak"}', '"title":"2.0, 2.1"', '{"link":"Gym Town"}', '"title":"4.4"']) {
      assert.ok(card.includes(text), text);
    }
    assert.equal(card.split('{"link":"Peak"}').length, 2);
    assert.equal(card.split('{"link":"Gym Town"}').length, 2);
    assert.ok(!card.includes("page:mechanics:"));
    assert.equal(moveFields(secret).find(([key]) => key === "Target")[1], "Selected target");
    assert.ok(!moveFacts(secret).some(([key]) => key === "Target"));
    const nature = JSON.stringify(moveMechanics({ mechanics: mechanics("move", [85, 85, 85]) },
      { text: (id) => names[id] ?? "", move: (id) => ({ move: id }) }));
    assert.ok(nature.includes('{"move":85}') && !nature.includes("Thunderbolt"));
    const plain = JSON.stringify(moveMechanics({ mechanics: mechanics("move", [85, 85, 85]) }, { text: (id) => names[id] ?? "" }));
    assert.ok(plain.includes("Thunderbolt") && plain.includes("Lake"));
    const camouflage = JSON.stringify(moveMechanics({ mechanics: mechanics("type", [12, 12, 12]) }, { text: (id) => names[id] ?? "" }));
    assert.ok(camouflage.includes("Grass") && camouflage.includes("dex-move-environments"));
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

test("the move card and filter share localized effect names with English fallback", async () => {
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  try {
    const { moveCardContent } = await import("../js/movecard.js");
    const { effectOptions } = await import("../js/pokedexdataview/pokedexdataview.js");
    const { effectName } = await import("../js/moveeffects.js");
    const { PokedexView } = await import("../js/pokedex/pokedex.js");
    const labels = { "page:effect:burn_hit": ["Burn hit", "Удар с ожогом"],
      "page:effect:freeze_hit": ["Frostbite hit", "Удар с обморожением"],
      "page:effect:eat_berry": ["Berry/Gem removal", "Удаление ягод и самоцветов"] };
    const parent = { lang: "ru", host: { data: () => ({ labels: {} }), pageLabels: () => labels } };
    setLabelSource((key) => PokedexView.prototype.label.call(parent, key));
    assert.ok(JSON.stringify(moveCardContent(record)).includes("Удар с ожогом · 10%"));
    assert.deepEqual(effectOptions([record]), [{ id: "burn_hit", name: "Удар с ожогом" }]);
    assert.equal(effectName(5), "Удар с обморожением");
    parent.lang = "en";
    assert.ok(JSON.stringify(moveCardContent(record)).includes("Burn hit · 10%"));
    assert.deepEqual(effectOptions([record]), [{ id: "burn_hit", name: "Burn hit" }]);
    assert.equal(effectName(5), "Frostbite hit");
    assert.deepEqual(effectOptions([{ effect: 219, effect_key: "eat_berry" }]),
      [{ id: "eat_berry", name: "Berry/Gem removal" }]);
    assert.ok(JSON.stringify(moveCardContent({ ...record, effect: 219, effect_key: "eat_berry" }))
      .includes("Berry/Gem removal · 10%"));
    assert.equal(effectName(300), "Effect 300");
    setLabelSource(null);
    assert.equal(effectName(4), "Burn hit");
    assert.equal(effectName(5), "Freeze hit");
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

test("formula power is a dash and an expression stays a sentence in the popup", async () => {
  globalThis.document = { createElement: (tag) => ({ ...fake(), tag }), createTextNode: (t) => ({ text: t }) };
  setLabelSource((key) => key === "page:power:return" ? { text: "Power by friendship", game: false } : null);
  try {
    const { moveCardContent } = await import("../js/movecard.js");
    const move = { ...record, id: 216, power: 1, power_kind: "computed", power_formula: "page:power:return" };
    const card = moveCardContent(move);
    assert.equal(card.children[1].children[0].children[0].children[0], "—");
    const power = card.children[2].children[1].children[0];
    assert.equal(power.tag, "p");
    assert.deepEqual(power.children, ["Power by friendship"]);
    assert.ok(!JSON.stringify(card).includes('"1"'));
    assert.ok(!JSON.stringify(card).includes('"table"'));
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

test("the popup translates each condition and pairs it with tier power", async () => {
  globalThis.document = { createElement: (tag) => ({ ...fake(), tag }), createTextNode: (t) => ({ text: t }) };
  const labels = { "page:power:grass_knot": "Target weight", "page:power:cond:light": "Under 10 kg",
    "page:power:cond:heavy": "At least 10 kg", Condition: "Condition", Power: "Power" };
  setLabelSource((key) => labels[key] ? { text: labels[key], game: false } : null);
  try {
    const { moveCardContent } = await import("../js/movecard.js");
    const move = { ...record, power: 1, power_kind: "computed", power_formula: "page:power:grass_knot",
      power_tiers: [["page:power:cond:light", 20], ["page:power:cond:heavy", 40]] };
    const card = moveCardContent(move);
    assert.equal(card.children[1].children[0].children[0].children[0], "—");
    const formula = card.children[2].children[1].children[0];
    assert.deepEqual(formula.children[0].children, ["Target weight"]);
    const table = formula.children[1];
    assert.equal(table.tag, "table");
    assert.deepEqual(table.children[0].children[0].children.map((cell) => cell.children[0]), ["Condition", "Power"]);
    assert.deepEqual(table.children[1].children.map((row) => row.children.map((cell) => cell.children[0])),
      [["Under 10 kg", "20"], ["At least 10 kg", "40"]]);
    assert.ok(!JSON.stringify(card).includes('"1"'));
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

test("item power tables share localized links and type badges across move surfaces", async () => {
  globalThis.document = { createElement: (tag) => ({ ...fake(), tag }), createTextNode: (t) => ({ text: t }) };
  try {
    const { moveCardContent } = await import("../js/movecard.js");
    const { PokedexDataView } = await import("../js/pokedexdataview/pokedexdataview.js");
    const items = [{ item: 5, type: 0, power: 80 }, { item: 7, type: 23, power: 100 }];
    const move = { ...record, description: null, flag_keys: [], list_keys: [], power: 1,
      power_kind: "computed", power_formula: "page:power:demo", power_items: items };
    for (const suffix of ["", " localized"]) {
      const labels = Object.fromEntries(["Item", "Type", "Power", "page:power:demo"].map((key) => [key, key + suffix]));
      setLabelSource((key) => labels[key] ? { text: labels[key], game: false } : null);
      const opened = [];
      const view = { data: { types: [{ id: 0, colour: "#aaaabb" }, { id: 23, colour: "#dd99aa" }] },
        view: { text: (id) => id ? id + suffix : "", openItem: (id) => opened.push(id) },
        categoryIcon: () => fake(), learnerTable: () => fake() };
      view.itemLink = (id) => PokedexDataView.prototype.itemLink.call(view, id);
      const text = (id) => view.view.text(id);
      const popup = moveCardContent(move, { text, types: view.data.types, link: view.itemLink });
      const popupPower = popup.children[2].children[1].children[0];
      const page = PokedexDataView.prototype.moveDetail.call(view, move);
      const pagePower = page.children[0].children[0].children[0].children[5].children[0].children[1];
      assert.equal(JSON.stringify(pagePower), JSON.stringify(popupPower));
      for (const formula of [pagePower, popupPower]) {
        const table = formula.children[1];
        assert.equal(table.tag, "table");
        assert.deepEqual(table.children[0].children[0].children.map((cell) => cell.children[0]),
          [labels.Item, labels.Type, labels.Power]);
        const rows = table.children[1].children;
        assert.equal(rows.length, items.length);
        rows.forEach((row, index) => {
          const [item, type, power] = row.children.map((cell) => cell.children[0]);
          assert.equal(item.tag, "button");
          assert.equal(item.title, `items:${items[index].item}`);
          assert.ok(item.children.includes(text(item.title)));
          item.listeners.click();
          assert.equal(opened.at(-1), items[index].item);
          assert.equal(type.className, "chip ptype");
          assert.equal(type.title, `types:${items[index].type}`);
          assert.deepEqual(type.children, [text(type.title)]);
          assert.ok(type.attrs.style.includes(view.data.types[index].colour));
          assert.equal(power, String(items[index].power));
        });
      }
    }
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

test("the card shows the name, type, category, three figures, target, effect, text, flags and the page link", async () => {
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const { moveCardContent } = await import("../js/movecard.js");
  let opened = null;
  const card = moveCardContent(record, { text: (id) => texts[id] ?? "", types: [{ id: 10, colour: "#f75231" }],
    readers: { punching: { readers: { abilities: [93], items: [] } } }, open: (id) => { opened = id; } });
  const flat = JSON.stringify(card);
  for (const expected of ["Fire Punch", "Fire", "Physical", "75", "15", "100", "Power", "PP", "Accuracy",
    "Selected target", "Burn hit · 10%", "Hits with a fiery fist. May burn.", "Makes contact", "Punching", "Iron Fist", "Open in Pokédex"]) {
    assert.ok(flat.includes(expected), expected);
  }
  const foot = card.children.at(-1);
  foot.children[0].listeners.click();
  assert.equal(opened, 7);
  delete globalThis.document;
});

test("the dialog hosts one card, opens as modal and closes on the button or a click outside", async () => {
  const body = fake();
  globalThis.document = { body, createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const { MoveDialog } = await import("../js/movecard.js");
  const dialog = new MoveDialog(globalThis.document);
  assert.equal(dialog.available, true);
  dialog.show({ tag: "card" });
  const node = body.children[0];
  assert.equal(node.open, true);
  assert.equal(node.children[1].tag, "card");
  node.children[0].listeners.click();
  assert.equal(node.open, false);
  dialog.show({ tag: "second" });
  assert.equal(body.children.length, 1); // reused
  assert.equal(node.children[1].tag, "second");
  node.listeners.click({ target: node });
  assert.equal(node.open, false);
  assert.equal(new MoveDialog({ createElement: () => fake() }).available, false); // no body: the tests' document
  delete globalThis.document;
});

test("the item card shows the icon, name, fields, taught move, text, evolutions and the page link", async () => {
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const { itemCardContent } = await import("../js/movecard.js");
  let opened = null;
  const item = { id: 93, name: "items:93", pocket: 1, price: 2100, sell_price: 1050, description: "s2", tm_index: null };
  const card = itemCardContent(item, { text: (id) => ({ "items:93": "Sun Stone", s2: "Evolves certain Pokémon." })[id] ?? "",
    icon: { tag: "icon" }, pokedollarIcon: () => ({ tag: "span", className: "dex-pokedollar-icon", role: "img", ariaLabel: "Pokédollar" }),
    evolutions: [{ tag: "evolutions" }], open: (id) => { opened = id; } });
  const flat = JSON.stringify(card);
  for (const expected of ["Sun Stone", "icon", "Pocket", "Items", "dex-pokedollar-icon", "Pokédollar", "2100",
    "Base buy price", "Base sell price", "1050", "Evolves certain Pokémon.", "evolutions", "Open in Pokédex"]) {
    assert.ok(flat.includes(expected), expected);
  }
  assert.ok(!flat.includes("¥"));
  assert.ok(!flat.includes("Teaches"));
  assert.ok(!flat.includes("Items (1)"));
  card.children.at(-1).children[0].listeners.click();
  assert.equal(opened, 93);
  delete globalThis.document;
});

test("item dialogs omit absent sale prices and retain a declared zero payout", async () => {
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  try {
    const { itemCardContent } = await import("../js/movecard.js");
    for (const sell_price of [undefined, null]) {
      const card = itemCardContent({ id: 1, pocket: 4, price: 3000, sell_price });
      assert.ok(!JSON.stringify(card).includes("Base sell price"));
    }
    const card = itemCardContent({ id: 1, pocket: 3, price: 1, sell_price: 0 });
    assert.equal(card.children[1].children[4].children[0], "Base sell price (Pokédollar)");
    assert.equal(card.children[1].children[5].children[0], "0");
  } finally {
    delete globalThis.document;
  }
});

test("the item dialog resolves held-effect types through its text source", async () => {
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  try {
    const { itemCardContent } = await import("../js/movecard.js");
    const item = { id: 512, pocket: 1, price: 0, held_effect: 72, hold_effect_param: 23,
      held_effect_key: "Type plate: {type}", held_effect_param_name: "types:23" };
    const card = itemCardContent(item, { text: (id) => id === "types:23" ? "Fairy" : "" });
    assert.ok(JSON.stringify(card).includes("Type plate: Fairy"));
    assert.ok(!JSON.stringify(card).includes("types:23"));
  } finally {
    delete globalThis.document;
  }
});
