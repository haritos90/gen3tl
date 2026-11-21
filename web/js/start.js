// Verified ROMs provide the original text and glyphs.

import { el } from "./dom.js";
import { STAGE_DETAILS } from "./site-model.js";
import { formatNumber, t } from "./ui.js";

export { STAGE_NOTES } from "./site-model.js";

export function patchHint(game) {
  if (!game?.patch_bases?.length) return "";
  return t("A clean {bases} ROM and the {game} UPS patch can also be loaded.",
    { bases: game.patch_bases.map((base) => base.name).join(" / "), game: game.name });
}

/** Keep patch selection beside the recognized base ROM. */
export function patchPicker(base, game, choose, cancel, busy = false) {
  const input = el("input", { type: "file", accept: ".ups,application/octet-stream", hidden: true,
    on: { change: (e) => {
      const file = e.target.files[0];
      e.target.value = "";
      if (file) choose(file);
    } } });
  return el("div", { class: "note", role: "status" },
    el("p", { textContent: t("{base} recognized. Choose the UPS patch for {game}.",
      { base: base.name, game: game.name }) }),
    el("p", { class: "small mono", textContent: t("Expected SHA-1 {sha1}", { sha1: game.sha1 }) }),
    el("p", { class: "small", textContent: t("The ROM and patch are read in this browser and stay local.") }),
    input,
    el("button", { type: "button", class: "btn sm", disabled: busy,
      textContent: busy ? t("Checking…") : t("Choose UPS patch"), on: { click: () => input.click() } }),
    " ", el("button", { type: "button", class: "btn sm", textContent: t("Cancel"), on: { click: cancel } }));
}

/** The game options: translation stage first, then by name. */
export function gameOptions(games, selected) {
  const order = Object.fromEntries(Object.keys(STAGE_DETAILS).map((key, index) => [key, index]));
  return [...games].sort((a, b) => order[a.stage] - order[b.stage] || a.name.localeCompare(b.name)).map((g) => {
    const open = g.languages.filter((l) => l.editable).length;
    const meta = [g.engineLabel, g.entries ? t("{n} texts", { n: formatNumber(g.entries) }) : "",
      g.stage === "catalog" ? t("drafting only") : "",
      !open ? t("no language catalog yet") : ""].filter(Boolean).join(" · ");
    const { tone, label } = STAGE_DETAILS[g.stage] ?? STAGE_DETAILS.legacy;
    return { id: g.id, title: g.name, meta, chip: [tone, t(label)], selected: g.id === selected,
      muted: !open };
  });
}

/** Only the chosen language can supply a workspace. */
export const workspaceLanguage = (game, code) => game?.languages.find((l) => l.editable && l.code === code) ?? null;

/** The line beside the open button. */
export function startSummary(game, lang, rom) {
  if (!game) return t("Choose a game.");
  if (!lang) return t("{game} · choose a language", { game: game.name });
  if (game.stage === "interface") return `${game.name} · ${lang.name}`;
  const which = !rom ? t("without a ROM") : rom.release ? t("with a patched ROM") : t("with your ROM");
  return `${game.name} · ${lang.name} · ${which}`;
}
