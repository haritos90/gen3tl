// Source graphs load every candidate before measurement.

import { N_, t } from "./ui.js";
import { limitText } from "./checktext.js";

const STATES = { computed: N_("computed"), recorded: N_("recorded; sources undeclared"),
  override: N_("override"), unavailable: N_("measurement unavailable"),
  fallback: N_("recorded; sources unavailable") };
const LABELS = [N_("Battle name prefix"), N_("Nickname of up to ten letters"),
  N_("Player name of up to seven letters"), N_("Species name"), N_("Move name"), N_("Ability name"),
  N_("Item name"), N_("Trainer class"), N_("Nickname or species name"), N_("Battle name with prefix"),
  N_("Trainer name"), N_("Battle side prefix"), N_("Battle side label"), N_("Typed name"), N_("Number"), N_("Inserted text"), N_("Source text"), N_("No prefix")];

/** Include candidates that are currently below the maximum. */
export function sourceIds(window, graph) {
  return [...new Set(Object.values(window?.slot_details ?? {}).flatMap((detail) =>
    [...(graph?.dependencies?.[detail.source] ?? []), ...(detail.dependencies ?? [])]))];
}

/** Chunks retain flags and original-text state. */
export async function loadSlotSources(entry, graph, loadEntry, edits) {
  const entries = await Promise.all(sourceIds(entry.window, graph).map(async (id) => {
    const value = await loadEntry(id);
    if (!value) throw new Error(`Substitution source unavailable: ${id}`);
    return [id, value];
  }));
  const sources = Object.fromEntries(entries);
  const drafts = Object.fromEntries(entries.flatMap(([id]) => {
    const draft = edits?.get(id);
    return draft ? [[id, { translation: draft.translation, state: draft.state }]] : [];
  }));
  return { sources, drafts };
}

/** Render Python results without duplicating width calculations. */
export function slotRows(window, sources = {}, drafts = {}) {
  return Object.entries(window?.slot_details ?? {}).map(([token, detail], index) => {
    const present = (part) => {
      if (part.literal) return { width: part.width, label: t("Original literal"), note: t("original text used") };
      if (part.id) {
        const entry = sources[part.id], draft = drafts[part.id];
        const text = part.original ? entry?.source_text : draft?.translation ?? entry?.translation;
        return { id: part.id, width: part.width,
          label: text ? `«${text.replaceAll(" ", "·")}»` : t("Open source text"),
          note: part.original ? t("original text used") : "" };
      }
      return { width: part.width, label: part.count != null
        ? t("Up to {n} characters using «{glyph}»", { n: part.count, glyph: part.glyph })
        : limitText(LABELS.includes(part.label) ? part.label : "Source text"), note: "" };
    };
    const parts = (detail.contributors ?? []).map(present);
    const alternatives = [];
    for (const part of (detail.winners ?? []).flat().map(present)) {
      if (![...parts, ...alternatives].some((p) => p.id === part.id && p.label === part.label)) alternatives.push(part);
    }
    return { token, width: detail.width, computed: detail.computed,
      label: detail.label ? limitText(detail.label) : t("Inserted value {n}", { n: index + 1 }),
      state: t(STATES[detail.state === "recorded" && detail.unavailable ? "fallback" : detail.state] ?? STATES.unavailable),
      reason: detail.reason ?? "", parts, alternatives };
  });
}
