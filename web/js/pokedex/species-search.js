import { siteText, t } from "../ui.js";
import { itemName, metric, growthRate, eggGroups, genderRatio, recordIds } from "./species-data.js";
import { ITEM_MARK, formEvidence, formLabel } from "./evolutions.js";


/** Label species forms only when multiple forms exist. */
export function displayName(record, records, text, index = null) {
  const label = formLabel(record, records, text, index);
  if (label && record.form_name_full) return label;
  const mega = megaLetter(record, records, text, index);
  if (mega !== null) { // Use the game's own Mega naming pattern.
    return `${t("Mega {species}", { species: text(record.name) })}${mega ? ` ${mega}` : ""}`;
  }
  return `${text(record.name)}${label ? ` ${label}` : ""}`;
}


/** Index species families and incoming form evidence. */
export function speciesFamilyIndex(records) {
  const dexSlots = new Map();
  const megaTargets = new Set();
  const incoming = new Map();
  for (const record of records) {
    for (const edge of record.evolution ?? []) {
      if (edge.kind === "mega") megaTargets.add(edge.target);
      if (!incoming.has(edge.target)) incoming.set(edge.target, []);
      incoming.get(edge.target).push({ source: record.slot, edge });
    }
    if (!(record.dex > 0)) continue;
    if (!dexSlots.has(record.dex)) dexSlots.set(record.dex, []);
    dexSlots.get(record.dex).push(record.slot);
  }
  const dexBases = new Map([...dexSlots].map(([dex, slots]) => [dex, Math.min(...slots)]));
  const bases = new Map();
  const families = new Map();
  for (const record of records) {
    const base = record.base_species ?? (record.dex > 0 ? dexBases.get(record.dex) : null) ?? record.slot;
    bases.set(record.slot, base);
    if (!families.has(base)) families.set(base, []);
    families.get(base).push(record);
  }
  for (const family of families.values()) family.sort((a, b) => a.slot - b.slot);
  return { bases, families, megaTargets, dexSlots, dexBases, incoming };
}


/** Find the slot carrying a forme family's base row. */
export function baseSlot(record, records, index = null) {
  if (index) return index.bases.get(record.slot) ?? record.slot;
  if (record.base_species != null) return record.base_species;
  const slots = records.filter((r) => r.dex > 0 && r.dex === record.dex).map((r) => r.slot);
  return slots.length ? Math.min(...slots) : record.slot;
}


/** List a base species and every forme in slot order. */
export function formesOf(record, records, index = null) {
  const base = baseSlot(record, records, index);
  if (index) return index.families.get(base) ?? [record];
  return records.filter((r) => baseSlot(r, records) === base).sort((a, b) => a.slot - b.slot);
}


/** Keep Mega evolution targets out of the Forms section. */
export function formSectionRecords(record, records, index = null) {
  index ??= speciesFamilyIndex(records);
  return formesOf(record, records, index).filter((forme) => !index.megaTargets.has(forme.slot));
}


/** Distinguish Mega stone suffixes, plain Mega and other forms. */
export function megaLetter(record, records, text = () => "", index = null) {
  const slots = index ? index.dexSlots.get(record.dex) : records.filter((r) => r.dex === record.dex);
  if (!(record.dex > 0) || (slots?.length ?? 0) < 2) return null;
  for (const { source, edge } of formEvidence(record, records, index)) {
    if (source < record.slot && edge.method === 254 && edge.parameter > 0 && (!edge.kind || edge.kind === "mega")) {
      const letter = text(`items:${edge.parameter}`).match(/\s([XY])$/);
      return letter ? letter[1] : "";
    }
  }
  return null;
}


/** Format a padded entry number. */
export const dexNumber = (dex) => (dex > 0 ? `#${String(dex).padStart(3, "0")}` : "");


/** Number species and forms by internal ID; placeholders stay unnumbered. */
export const speciesNumber = (record) => record.dex > 0 ? record.slot : record.dex;


/** Prefix names with numbers, leaving unnumbered placeholders bare. */
export function numberedName(record, records, text, index = null) {
  const name = displayName(record, records, text, index);
  const number = dexNumber(speciesNumber(record));
  return number ? `${number} ${name}` : name;
}


export function abilityIds(record, role) {
  if (record?.ability_details) return record.ability_details.filter((a) => a[role]).map((a) => a.id);
  return role === "regular" ? record?.abilities ?? [] : [];
}




/** Compare ability slots while preserving G-Max battle abilities. */
export function abilityChanges(source, target, text = () => "", method = null) {
  if (method === 253) return [];
  const changes = [];
  const regular = abilityIds(target, "regular");
  const first = regular[0] ?? null, second = regular[1] ?? null;
  const ability = (record, id) => {
    if (!id) return null;
    const name = record?.ability_details?.find((row) => row.id === id)?.name ?? `abilities:${id}`;
    return { id: name, text: text(name) };
  };
  for (const [role, label] of [["regular", "Ability"], ["hidden", "Hidden ability"]]) {
    const from = abilityIds(source, role), to = abilityIds(target, role);
    for (let i = 0; i < Math.max(from.length, to.length); i++) {
      const fromId = from[i] ?? null;
      let standIn = !!fromId && !to[i] && !!first;
      const toId = standIn ? first : to[i] ?? null;
      const alternate = role === "hidden" && standIn && second && second !== first ? ability(target, second) : null;
      if (alternate) standIn = false;
      const fromAbility = ability(source, fromId), toAbility = ability(target, toId);
      if (fromId === toId && fromAbility?.id === toAbility?.id && !alternate) continue;
      changes.push({
        role: role === "regular" ? `${label} ${i + 1}` : label, hidden: role === "hidden", standIn,
        from: fromAbility, to: toAbility, alternate,
      });
    }
  }
  return changes;
}


/** Name changed abilities and label the hidden role. */
export function abilityChangeText(change) {
  return `${change.from?.text || t("none")}${change.hidden ? ` (${t("hidden")})` : ""} ➝ ${change.to?.text || t("none")}`
    + (change.alternate ? ` ${t("or")} ${change.alternate.text}` : "");
}


/**
 * Match identifiers, text and species filters.
 * All selected types and any selected move must match.
 */
export function speciesMatches(record, criteria) {
  if (!criteria) return true;
  const types = record.types ?? [];
  if ((criteria.types ?? []).some((type) => !types.includes(type))) return false;
  if (criteria.ability != null) {
    const ids = record.ability_details ? record.ability_details.map((a) => a.id) : record.abilities ?? [];
    if (!ids.includes(criteria.ability)) return false;
  }
  if (criteria.moves && !(record.learnset ?? []).some((row) => criteria.moves.has(row.move))) return false;
  if (criteria.movesOfType && !(record.learnset ?? []).some((row) => criteria.movesOfType.has(row.move))) return false;
  return true;
}


/** Rank name prefixes, substrings and other-field matches. */
export function matchRank(name, query) {
  const q = (query ?? "").trim().toLowerCase();
  if (!q) return 0;
  // Both translated and source names contribute to ranking.
  const names = (Array.isArray(name) ? name : [name]).flat().map((n) => (n ?? "").toLowerCase());
  return names.some((n) => n.startsWith(q)) ? 0 : names.some((n) => n.includes(q)) ? 1 : 2;
}


/** Collect canonical names and optional search aliases. */
export function speciesSearchNames(record, text, source, aliases = () => [], records = [], index = null) {
  const sourceName = (id) => [source(id)].flat()[0] ?? "";
  const formKey = record.form_name?.startsWith("page:") ? record.form_name.slice(5) : "";
  const sourceForm = formKey && (record.form_name_full ? formKey : `${sourceName(record.name)} ${formKey}`);
  return [text(record.name), source(record.name), ...aliases(record.name),
    ...(sourceForm ? [sourceForm] : []),
    ...(records.length ? [displayName(record, records, text, index), displayName(record, records, sourceName, index)] : [])].flat();
}


/** Collect every forme name contributing to a base row. */
export function speciesGroupSearchNames(record, records, text, source, aliases = () => [], index = null) {
  return speciesRowForms(record, records, index).flatMap((forme) => speciesSearchNames(forme, text, source, aliases, records, index));
}


/** Keep independently listed forms outside their base row. */
export function speciesRowForms(record, records, index = null) {
  return record.form_browsing?.separate ? [record]
    : formesOf(record, records, index).filter((forme) => !forme.form_browsing?.separate);
}


/** Apply explicit public policy without changing raw records. */
export const recordBrowsable = (record, browsing = {}) => browsing?.[record.slot ?? record.id] !== "unreleased";

export const speciesBrowsable = recordBrowsable;


/** Keep declared placeholders outside public lists. */
export function publicRecords(data, kind, records = data[kind] ?? []) {
  const browsing = data[`${kind}_browsing`];
  return browsing ? records.filter((record) => recordBrowsable(record, browsing)) : records;
}


/** Cache normalized record and family search fields across refilters. */
export function createSpeciesSearch(records, text = () => "", source = () => "", aliases = () => [], grouped = false, index = null, items = [], browsing = {}) {
  const familyIndex = index ?? (grouped ? speciesFamilyIndex(records) : null);
  const recordNames = new Map();
  const groupNames = new Map();
  const normalizedNames = new Map();
  const haystacks = new Map();
  const representatives = [];
  records.forEach((record, i) => {
    if (!speciesBrowsable(record, browsing)) return;
    if (grouped && !record.form_browsing?.separate && baseSlot(record, records, familyIndex) !== record.slot) return;
    representatives.push({ record, i, forms: grouped ? speciesRowForms(record, records, familyIndex) : [record] });
  });
  const namesOf = (record) => {
    if (!recordNames.has(record.slot)) {
      recordNames.set(record.slot, speciesSearchNames(record, text, source, aliases, records, familyIndex));
    }
    return recordNames.get(record.slot);
  };
  const names = (record) => {
    if (!grouped) return namesOf(record);
    if (!groupNames.has(record.slot)) {
      groupNames.set(record.slot, speciesRowForms(record, records, familyIndex).filter((forme) => speciesBrowsable(forme, browsing)).flatMap(namesOf));
    }
    return groupNames.get(record.slot);
  };
  const normalized = (record) => {
    if (!normalizedNames.has(record.slot)) normalizedNames.set(record.slot, names(record).map((name) => String(name ?? "").toLowerCase()));
    return normalizedNames.get(record.slot);
  };
  const haystack = (record) => {
    if (!haystacks.has(record.slot)) {
      const ids = recordIds(record, items);
      haystacks.set(record.slot, [String(record.dex ?? ""), String(record.slot), ...ids, ...aliases(record.name),
        ...ids.map((id) => text(id)), ...ids.flatMap((id) => source(id)), ...namesOf(record)]
        .map((value) => String(value ?? "").toLowerCase()));
    }
    return haystacks.get(record.slot);
  };
  const rank = (record, query) => {
    const q = (query ?? "").trim().toLowerCase();
    if (!q) return 0;
    if (speciesNumber(record) > 0 && q === String(speciesNumber(record))) return -1;
    const values = normalized(record);
    return values.some((name) => name.startsWith(q)) ? 0 : values.some((name) => name.includes(q)) ? 1 : 2;
  };
  const filter = (query, criteria = null) => {
    const q = (query ?? "").trim().toLowerCase();
    if (typeof criteria === "string") criteria = criteria === "" ? null : { types: [Number(criteria)] };
    const accepts = typeof criteria === "function" ? criteria : (record) => speciesMatches(record, criteria);
    const matches = (record) => speciesBrowsable(record, browsing) && accepts(record) && (!q || haystack(record).some((value) => value.includes(q)));
    const out = [];
    for (const { record, i, forms } of representatives) if (forms.some(matches)) out.push(i);
    return out;
  };
  return { filter, names, rank };
}


export function filterSpecies(records, query, text = () => "", criteria = null, source = () => "", aliases = () => [], grouped = false, items = [], browsing = {}) {
  return createSpeciesSearch(records, text, source, aliases, grouped, null, items, browsing).filter(query, criteria);
}


/** Build a list row with number and resolved name. */
export function rowLabel(record, text) {
  const number = speciesNumber(record);
  return { dex: number == null ? "" : String(number), name: text(record.name) };
}


/**
 * Resolve detail fields through the selected language catalog.
 * Missing identifiers or translations produce empty strings.
 */
export function detailOf(record, text, abilityRecords = []) {
  const entries = (record.entry ?? []).map((id) => ({ id, text: text(id) }));
  const abilityById = new Map(abilityRecords.map((ability) => [ability.id, ability]));
  const abilities = (record.ability_details ?? (record.abilities ?? [])
    .map((id) => ({ id, regular: true, hidden: false })))
    .filter((ability, index, all) => all.findIndex((row) => row.id === ability.id) === index);
  return {
    name: text(record.name),
    dex: record.dex == null ? "" : String(record.dex),
    height: metric(record.height),
    weight: metric(record.weight),
    category: text(record.category),
    types: [...new Set(record.types ?? [])].map((t) => ({ id: `types:${t}`, text: text(`types:${t}`) })),
    abilities: abilities.map((ability) => {
      const id = ability.name ?? `abilities:${ability.id}`;
      const descriptionId = Object.hasOwn(ability, "description") ? ability.description
        : abilityById.get(ability.id)?.description ?? null;
      return {
        id, text: text(id), descriptionId,
        description: descriptionId ? text(descriptionId) : "",
        regular: ability.regular ?? true, hidden: ability.hidden ?? false,
      };
    }),
    stats: record.base_stats ?? null,
    catchRate: record.catch_rate == null ? "" : t("{n} / 255", { n: record.catch_rate }), // Catch rates are engine parameters, not probabilities.
    baseExp: record.base_exp == null ? "" : String(record.base_exp),
    growth: growthRate(record.growth),
    eggGroups: eggGroups(record.egg_groups),
    gender: genderRatio(record.gender_ratio),
    entries,
  };
}


/** Split acquisition descriptions around linked game entities. */
export function sourceParts(source, text = () => "", items = [], records = []) {
  const speciesText = (slot) => {
    const record = records.find((row) => row.slot === slot);
    return record ? displayName(record, records, text) : text(`species:${slot}`) || t("Species {n}", { n: slot });
  };
  const place = source.map_section == null ? "" : t("in {map}", {
    map: text(`mapsec:${source.map_section}`) || t("map section {n}", { n: source.map_section }),
  });
  const speaker = source.kind === "trade" && source.speaker ? text(source.speaker) : "";
  let label;
  if (source.kind === "egg") label = t("Egg from an NPC");
  else if (source.kind === "gift") label = t("Gift at level {level}", { level: source.level });
  else if (source.kind === "trade") {
    label = speaker ? siteText("{name}: trade for {species}", {
      name: speaker, species: `${ITEM_MARK}species:${source.wanted}${ITEM_MARK}`,
    }) : t("Trade for {species}", {
      species: `${ITEM_MARK}species:${source.wanted}${ITEM_MARK}`,
    });
  } else if (source.kind === "starter") label = t("Starter");
  else label = source.kind;
  const trainer = source.kind === "trade" && source.original_trainer ? text(source.original_trainer) : "";
  const originalTrainer = trainer && trainer !== speaker ? siteText("Original Trainer: {name}", { name: trainer }) : "";
  const item = source.item > 0 ? t("with {item}", { item: `${ITEM_MARK}items:${source.item}${ITEM_MARK}` }) : "";
  const availability = source.availability === "visiting_trader"
    ? siteText("Visiting trader; the offer depends on schedule and progress.")
    : source.availability === "alternate_trade"
      ? siteText("Another location for the same trade; availability depends on schedule and progress.") : "";
  return [label, originalTrainer ? `(${originalTrainer})` : "", place, item, availability ? `· ${availability}` : ""].filter(Boolean).join(" ").split(ITEM_MARK).filter(Boolean).map((part) => (
    part.startsWith("items:")
      ? { item: Number(part.slice(6)), text: itemName(Number(part.slice(6)), text, items) }
      : part.startsWith("species:")
        ? { species: Number(part.slice(8)), text: speciesText(Number(part.slice(8))) }
        : { text: part }));
}
