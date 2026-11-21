import { el } from "../dom.js";
import { itemLink, typeChip } from "../pokedex/evolutions.js";
import { STAT_LABELS, STAT_ORDER } from "../pokedex/species-list.js";
import { gameLabel, t } from "../ui.js";
import { defaultMoves, settingsLabel, cardEncounters, variantConditions, difficultyFields, trainerIvMember, trainerEvRows, evAlternatives, abilityFact, bandLevel, trainerWriteRows, writeRange, factorWrites, trainerIvSummary, INVERSE_BATTLE_MAPS, INVERSE_BATTLE_SIGN, evYield, trainerName, levelRange } from "./trainers.js";
import { levels, fieldList } from "./locations.js";

export const withTrainerCards = (Base) => class extends Base {


  /** Render the stored portrait when available. */
  trainerSprite(battle, large = false) {
    return battle?.sprite ? el("img", {
      class: large ? "dex-img dex-trainer-portrait" : "dex-trainer-icon",
      src: this.host.spriteSrc(battle.sprite),
      alt: large ? trainerName(battle, (id) => this.view.text(id), (id) => this.source(id)) : "",
      width: large ? 128 : 32, height: large ? 128 : 32,
    }) : null;
  }


  /** Render trainer rules and each battle's party. */
  trainerDetail(group) {
    const difficulty = this.data.trainer_difficulty;
    const mode = difficulty ? String(difficulty.order.some((value) => String(value) === this.difficulty)
      ? this.difficulty : difficulty.default) : null;
    const rule = difficultyFields(mode, difficulty);
    const { shown: encounters, unused } = cardEncounters(group, this.data, mode);
    const shown = [...new Set(encounters.flatMap((encounter) => encounter.variants.map((row) => row.battle)))];
    const speciesName = (slot) => {
      const record = this.view.record(slot);
      return (record && this.view.speciesName?.(record)) || this.view.text(record?.name) || `species:${slot}`;
    };
    const variant = (row) => {
      const battle = row.battle;
      const label = settingsLabel(row.settings, difficulty);
      return el("div", { class: "dex-variant" },
        el("div", { class: "dex-battle-head dex-variant-head" },
          ...(label ? [el("span", { class: "dex-setting" }, label)] : []),
          ...variantConditions(row, difficulty, speciesName).map((text) => el("span", { class: "dex-setting" }, text)),
          el("span", {}, t("trainer {n}", { n: battle.id })),
          el("span", {}, levelRange(battle.party) ? t("Lv {n}", { n: levelRange(battle.party) }) : ""),
          el("span", {}, t("{n} Pokémon", { n: battle.party.length }))),
        el("div", { class: "dex-party" }, ...battle.party.map((mon) => this.partyMember(mon, rule, battle, mode))),
        ...(row.participants?.length ? [fieldList(row.participants.map((participant) => [
          participant.role === "partner" ? t("Battle partner")
            : participant.role === "second_opponent" ? t("Second opponent") : t("First opponent"),
          el("span", {}, el("button", { type: "button", class: "dex-link dex-participant-link",
            on: { click: () => this.view.goTo({ kind: "trainers", id: participant.record }) } },
          trainerName(participant.battle, (id) => this.view.text(id), (id) => this.source(id))),
          ...variantConditions({ ...participant, role: null }, difficulty, speciesName)
            .map((condition) => ` · ${condition}`)),
        ]))] : []));
    };
    const battles = encounters.map((encounter, i) => el("section", { class: "block dex-battle dex-encounter" },
      el("div", { class: "dex-battle-head" },
        el("b", {}, encounters.length > 1 ? t("Battle {n}", { n: i + 1 }) : t("Battle"))),
      ...encounter.variants.map(variant)));
    if (unused.length) {
      battles.push(el("section", { class: "block dex-battle dex-unused" },
        el("div", { class: "dex-battle-head" }, el("b", {}, t("Not found in any battle"))),
        ...unused.map((battle) => variant({ battle, settings: null }))));
    }
    const facts = group.battles.flatMap((battle) => [
      [t("Trainer record {n}", { n: battle.id }), el("span", {},
        trainerName(battle, (id) => this.view.text(id), (id) => this.source(id)), " ",
        el("span", { class: "dex-sub" }, `${t("Catalog key")}: ${battle.name}`))], [t("Class id"), battle.class_id],
      [t("AI flags"), battle.ai_flags], [t("Party flags"), battle.party_flags],
      [t("Stored party size"), battle.party_size], [t("Party offset"), battle.party_offset ?? ""],
      [t("Record offset"), battle.offset ?? ""],
      [t("Stored species"), battle.party.map((mon) => `${mon.index}:${mon.species}`).join(" · ")],
      [t("Stored levels"), battle.party.map((mon) => `${mon.index}:${mon.level}`).join(" · ")],
      [t("Stored held items"), battle.party.map((mon) => `${mon.index}:${mon.item ?? "—"}`).join(" · ")],
      [t("Stored moves"), battle.party.map((mon) => `${mon.index}:${mon.moves?.join(",") ?? "—"}`).join(" · ")],
      [t("Stored IV/spread"), battle.party.map((mon) => `${mon.index}:${mon.iv}`).join(" · ")],
      ...this.technicalConditions(battle),
    ]);
    const rules = el("div", { class: "dex-rules" },
        difficulty ? el("label", { class: "dex-difficulty" }, t("Difficulty"),
          el("select", {
            on: { change: (event) => { this.difficulty = event.target.value; this.renderDetail(group.id); } },
          }, ...difficulty.order.map((value) =>
            el("option", { value: String(value), selected: String(value) === mode }, t(difficulty.labels[value]))))) : null,
        fieldList([[t("Class"), this.view.text(group.class)], [t("Location"), this.trainerPlaces(group)],
          [t("EV training"), this.trainingPlaces(group)]]));
    const portrait = this.trainerSprite(shown[0] ?? group.battles[0], true);
    return el("div", { class: "dex-data-detail" },
      portrait ? el("div", { class: "dex-trainer-head" }, portrait, rules) : rules,
      ...battles,
      el("details", { class: "block dex-record" }, el("summary", {}, t("Technical record")), fieldList(facts)));
  }


  /** Name every mapped battle location, else null. */
  trainerPlaces(group, main = false) {
    const names = group.battles.flatMap((battle) => (battle.map ?? []).slice(0, main ? 1 : undefined).map((map, index) => {
      const id = battle.map_section?.[index];
      const section = id == null ? map : this.view.text(`mapsec:${id}`) || t("Map section {n}", { n: id });
      return INVERSE_BATTLE_MAPS.has(map) ? gameLabel(this.view.text(INVERSE_BATTLE_SIGN)) || section : section;
    }));
    return [...new Set(names)].join(", ") || null;
  }


  /** Name the map sections of a group's EV-training battles. */
  trainingPlaces(group) {
    const sections = [...new Set(group.battles.filter((battle) => battle.ev_training).flatMap((battle) => battle.map_section ?? []))];
    if (!sections.length) return null;
    return sections.map((n) => this.view.text(`mapsec:${n}`) || t("Map section {n}", { n })).join(", ");
  }


  /** Render party members with stats, items and move tiles. */
  partyMember(mon, rule, battle, mode) {
    const species = this.view.record(mon.species);
    const text = (id) => this.view.text(id);
    const name = (species && this.view.speciesName?.(species)) || text(species?.name) || species?.name || `species:${mon.species}`;
    const stored = Array.isArray(mon.moves);
    const generated = trainerIvMember(this.data, battle.id, mon.index);
    const writes = trainerWriteRows(generated, mode, this.data.trainer_ivs?.species_rules);
    const listed = stored ? mon.moves : generated?.listed?.moves ?? defaultMoves(species, mon.level);
    const factored = mode == null ? { head: {}, rows: writes }
      : factorWrites(writes, { item: mon.item ?? 0, moves: generated?.listed?.moves ?? mon.moves },
        writeRange(generated, mode, this.data.trainer_ivs?.species_rules));
    const write = factored.head.moves ? factored.head : null;
    const item = factored.head.item ?? mon.item;
    const moves = write ? write.moves.filter(Boolean) : listed;
    if (!stored && moves === null && this.view.learnsetsPending?.()) { // Default movesets require the species' loaded learnset.
      this.view.ensureLearnsets().then((ok) => ok && this.cur != null && this.renderDetail(this.cur));
    }
    const memberKey = `${battle.base ?? battle.id}/${mon.index}`;
    return el("div", { class: "dex-mon", "data-key": memberKey },
      species?.sprite?.front ? el("img", {
        src: this.host.spriteSrc(species.sprite.front), alt: "", loading: "lazy",
      }) : el("span", { class: "dex-mon-blank" }),
      el("div", { class: "dex-mon-head" },
        el("div", { class: "dex-mon-title" },
          el("button", {
            type: "button", class: "dex-link dex-mon-name", title: species?.name ?? "",
            disabled: !species, on: { click: () => species && this.view.openSpecies(species.slot) },
          }, name),
          el("span", { class: "dex-mon-level" }, t("Lv {n}", { n: mon.level }))),
        el("div", { class: "dex-types" }, ...[...new Set(species?.types ?? [])].map((type) =>
          typeChip(type, text(`types:${type}`), this.data.types ?? []))),
        el("div", { class: "dex-mon-facts" },
          el("span", { class: "dex-mon-fact" }, el("span", { class: "muted" }, `${t("Item")}: `), item ? this.itemLink(item) : "—"),
          this.partyStats(species, generated, mode, memberKey, battle.kind === "partners" ? 0 : 1),
          el("span", { class: "dex-mon-fact" }, el("span", { class: "muted" }, `${t("EV earned")}: `), evYield(species?.ev_yield) || "—"),
          this.partyAbility(mon, battle, mode))),
      el("div", { class: "dex-mon-moves-grid" },
        ...(moves ?? []).map((id) => this.moveTile(id)),
        ...(!stored && !write ? [this.partyDefaultMoves(generated, moves, mode)] : [])),
      this.partyWrites(generated, mode, factored.rows),
      this.partySpecies(generated, mode, mon));
  }


  /** Show only member-specific move and item changes. */
  partyWrites(source, mode, writes = trainerWriteRows(source, mode, this.data.trainer_ivs?.species_rules)) {
    const rows = writes.filter((row) => this.evCondition(row.when, source) !== null);
    if (!rows.length) return null;
    return el("div", { class: "dex-party-species" }, ...rows.map((row) => {
      const condition = [settingsLabel(row.difficulties.map((day) =>
        this.data.trainer_ivs.difficulties[day]), this.data.trainer_difficulty),
      // A write at every level states no level condition.
      row.level > 1 || row.last != null ? bandLevel(row, {}) : null,
      this.evCondition(row.when, source)].filter(Boolean).join(", ");
      return el("details", { class: "dex-built" }, el("summary", {}, condition),
        el("div", { class: "dex-built-body" },
          "item" in row ? el("span", { class: "dex-mon-fact" }, el("span", { class: "muted" }, `${t("Item")}: `),
            row.item ? this.itemLink(row.item) : "—") : null,
          row.moves ? el("div", { class: "dex-mon-moves-grid" }, ...row.moves.filter(Boolean).map((id) => this.moveTile(id))) : null));
    }));
  }


  /** Expand only moves differing from the listed level. */
  partyDefaultMoves(generated, moves, mode) {
    const ranges = mode == null ? Object.values(generated?.level_ranges ?? {})
      : [generated?.level_ranges?.[mode]].filter(Boolean);
    const minimum = Math.min(...ranges.map((range) => range.min));
    const maximum = Math.max(...ranges.map((range) => range.max));
    const bands = (generated?.move_bands ?? []).map((band) => ({ ...band,
      level: Math.max(band.level, minimum), last: Math.min(band.last ?? 100, maximum),
    })).filter((band) => band.level <= band.last &&
      (band.moves.length !== moves?.length || band.moves.some((id, index) => id !== moves[index])));
    if (!bands.length) return el("span", { class: "dex-sub dex-mon-default" }, t("Default moves"));
    return el("details", { class: "dex-built dex-mon-default" }, el("summary", {}, t("Default moves")),
      el("div", { class: "dex-built-body" }, ...bands.map((band) => el("div", { class: "dex-built-band" },
        el("span", { class: "dex-sub" }, bandLevel(band, {})),
        el("div", { class: "dex-mon-moves-grid" }, ...band.moves.map((id) => this.moveTile(id)))))));
  }


  /** One move with its type and category. */
  moveTile(id) {
    const text = (key) => this.view.text(key);
    const move = (this.data.moves ?? [])[id];
    return el("div", { class: "dex-move-tile" },
      this.view.moveLink ? this.view.moveLink(id) : el("span", {}, text(`moves:${id}`) || `moves:${id}`),
      el("div", { class: "dex-move-tile-meta" },
        move ? typeChip(move.type, text(`types:${move.type}`), this.data.types ?? []) : null,
        move ? this.categoryIcon(move.damage_category) : null));
  }


  /** Keep every possible ability individually accessible. */
  partyAbility(mon, battle, mode) {
    const side = battle.kind === "partners" ? 0 : 1;
    return abilityFact(this.view, mon.species, mode == null ? null : mon.abilities?.[`${Number(mode)}/${side}`]);
  }


  /** Equal EVs must not hide species substitutions.
   * Built species expand to their details. */
  partySpecies(generated, mode = null, mon = null) {
    const changes = (generated?.species_changes ?? [])
      .filter((row) => mode == null || row.difficulties?.includes(Number(mode)));
    const rows = [...(generated?.species_variants ?? []).map((row) => [row.species, this.speciesCondition(row), null]),
      ...changes.map((row) => [row.species, this.speciesRule(row), row])];
    if (!rows.length) return null;
    const groups = new Map();
    for (const [slot, condition, change] of rows) {
      // Equal details share one disclosure.
      const details = change?.abilities && mode != null ? JSON.stringify({ ...change, species: null,
        stats: this.view.record(slot)?.base_stats ?? null }) : "";
      const key = `${condition}\u0000${details}`;
      groups.set(key, { condition, change: details ? change : null, slots: [...groups.get(key)?.slots ?? [], slot] });
    }
    return el("div", { class: "dex-mon-fact dex-party-species" },
      el("span", { class: "muted" }, `${t("Possible species")}: `),
      ...[...groups.values()].map(({ condition, change, slots }) => {
        const links = slots.flatMap((slot, index) => {
          const record = this.view.record(slot);
          const link = el("button", { type: "button", class: "dex-link", disabled: !record,
            on: { click: () => record && this.view.openSpecies(record.slot) } }, this.speciesText(slot));
          return index ? [" / ", link] : [link];
        });
        if (!change) return el("div", {}, ...links, `: ${condition}`);
        return el("details", { class: "dex-built" }, el("summary", {}, ...links, `: ${condition}`),
          this.builtSpecies(change, mode, mon, generated));
      }));
  }


  /** Built species: held item, ability, stats and moves.
   * Group by built level where these change. */
  builtSpecies(change, mode, mon, generated) {
    const day = String(Number(mode));
    const species = this.view.record(change.species);
    const writes = trainerWriteRows(change, mode, this.data.trainer_ivs?.species_rules);
    const factored = factorWrites(writes, { item: change.item ?? mon?.item ?? 0, moves: change.moves },
      writeRange(change, mode, this.data.trainer_ivs?.species_rules));
    const item = factored.head.item ?? change.item ?? mon?.item;
    const stats = (source) => source.ivs || source.evs
      ? this.statsBlock(species, source.ivs?.[day], source.evs?.[day], generated) : null;
    const moves = (source) => source.moves
      ? el("div", { class: "dex-mon-moves-grid" }, ...source.moves.filter((id) => id).map((id) => this.moveTile(id))) : null;
    return el("div", { class: "dex-built-body" },
      el("span", { class: "dex-mon-fact" }, el("span", { class: "muted" }, `${t("Item")}: `), item ? this.itemLink(item) : "—"),
      abilityFact(this.view, change.species, change.abilities?.[day] ?? null),
      stats(change), moves(factored.head.moves ? { moves: factored.head.moves } : change),
      this.partyWrites(change, mode, factored.rows),
      ...(change.bands ?? []).map((band) => el("div", { class: "dex-built-band" },
        el("span", { class: "dex-sub" }, bandLevel(band, change)), stats(band), moves(band))),
      ...(mon && !Array.isArray(mon.moves) ? [el("span", { class: "dex-sub" }, t("Default moves"))] : []));
  }


  /** The displayed name of a species slot. */
  speciesText(slot) {
    const record = this.view.record(slot);
    return (record && this.view.speciesName?.(record)) || this.view.text(record?.name) || `species:${slot}`;
  }


  /** Name the build condition of a declared species rule. */
  speciesRule(change) {
    const rule = (this.data.trainer_ivs?.species_rules ?? []).find((row) => row.id === change?.rule);
    if (rule?.status !== "known") return t("Unknown species condition");
    if (rule.kind === "scaled_evolution") return this.scaledCondition(rule, change);
    const opponent = rule.opponent == null ? null : (this.data.trainers ?? []).find((row) => row.id === rule.opponent);
    return t(rule.label, { level: change.level, trainer: opponent
      ? trainerName(opponent, (id) => this.view.text(id), (id) => this.source(id)) : rule.opponent });
  }


  /** Level-scaling evolution: badges, level range, battle and source species. */
  scaledCondition(rule, change) {
    const labels = rule.labels ?? {};
    const range = change.last != null;
    const key = change.badges == null ? (range ? labels.range : rule.label)
      : change.badges >= rule.hall_badges ? (range ? labels.hall_range : labels.hall)
        : (range ? labels.badges_range : labels.badges);
    let condition = t(key, { level: change.level, last: change.last, badges: change.badges });
    const battle = (rule.battles ?? []).find((row) => row.id === change.battle);
    if (battle) condition = t(battle.label, { condition, party_level: battle.party_level });
    if (change.from != null) condition = t(labels.from, { condition, species: this.speciesText(change.from) });
    return condition;
  }


  /** Conditions come from the member's declared selector. */
  speciesCondition(outcome) {
    const condition = (this.data.trainer_ivs?.species_conditions ?? []).find((row) => row.id === outcome?.condition);
    return condition?.status === "known" ? t(condition.label) : t("Unknown species condition");
  }


  /** Keep species and generated stats together. */
  partyStats(species, generated, mode, memberKey, side) {
    const vector = mode == null ? null : generated?.contexts?.[`${Number(mode)}/${side}`];
    const summary = trainerIvSummary(vector);
    const note = summary === null ? t("IVs unavailable") : null;
    const evRows = trainerEvRows(generated, mode) ?? [{ evs: undefined, when: null }];
    return el("details", { class: "dex-mon-fact dex-party-stats", open: this.expandedPartyMembers.has(memberKey),
      on: { toggle: (event) => {
        if (event.currentTarget.open) this.expandedPartyMembers.add(memberKey);
        else this.expandedPartyMembers.delete(memberKey);
      } },
    }, el("summary", {}, `${t("IV")}: ${summary ?? "?"}`),
    this.statsTable(species, vector, evRows),
    note ? el("p", { class: "dex-sub" }, note) : null,
    ...this.evNotes(evRows, generated));
  }


  /** Base stats, one EV column per alternative, then IVs. */
  statsTable(species, vector, evRows) {
    const summary = trainerIvSummary(vector);
    const indices = { hp: 0, attack: 1, defense: 2, speed: 3, sp_attack: 4, sp_defense: 5 };
    const stats = species?.base_stats;
    const total = STAT_ORDER.every((key) => Number.isFinite(stats?.[key]))
      ? STAT_ORDER.reduce((sum, key) => sum + stats[key], 0) : "—";
    const evHead = (index) => evRows.length > 1 ? t("EV {n}", { n: index + 1 }) : t("EV");
    const evCell = (row, index) => Array.isArray(row.evs) ? String(row.evs[index]) : "—";
    const evTotal = (row) => Array.isArray(row.evs) ? String(row.evs.reduce((sum, value) => sum + value, 0)) : "—";
    return el("table", { class: evRows.length > 1 ? "dex-party-stats-table dex-party-stats-wide" : "dex-party-stats-table" },
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, t("Base stats")),
        el("th", { scope: "col" }, t("Base")),
        ...evRows.map((row, index) => el("th", { scope: "col" }, evHead(index))),
        el("th", { scope: "col" }, t("IV")))),
      el("tbody", {}, ...STAT_ORDER.map((key) => el("tr", {},
        el("th", { scope: "row" }, t(STAT_LABELS[key])), el("td", {}, String(stats?.[key] ?? "—")),
        ...evRows.map((row) => el("td", {}, evCell(row, indices[key]))),
        el("td", {}, summary === null ? "—" : String(vector[indices[key]]))))),
      el("tfoot", {}, el("tr", {}, el("th", { scope: "row" }, t("Total")), el("td", {}, String(total)),
        ...evRows.map((row) => el("td", {}, evTotal(row))), el("td", {}, "—"))));
  }


  /** A removed member, or the condition of each EV alternative. */
  evNotes(evRows, generated) {
    if (evRows.length === 1) {
      return evRows[0].evs === null ? [el("p", { class: "dex-sub" }, t("Not in party on this difficulty"))] : [];
    }
    return evRows.flatMap((row, index) => {
      const condition = this.evCondition(row.when, generated);
      return condition ? [el("p", { class: "dex-sub" }, t("EV {n}: {condition}", { n: index + 1, condition }))] : [];
    });
  }


  /** The stats table of a built species with its notes. */
  statsBlock(species, vector, evs, generated) {
    const evRows = evAlternatives(evs) ?? [{ evs: undefined, when: null }];
    return el("div", { class: "dex-party-stats" }, this.statsTable(species, vector, evRows),
      ...this.evNotes(evRows, generated));
  }


  /** Name the progress, variant and place of one EV alternative. */
  evCondition(when, generated) {
    const doc = this.data.trainer_ivs ?? {};
    const labels = new Map((doc.ev_conditions ?? []).map((row) => [row.flag, row]));
    const place = (name) => {
      const id = name.startsWith("section:") ? Number(name.slice(8)) : doc.ev_places?.[name];
      return id == null ? name : this.view.text(`mapsec:${id}`) || t("Map section {n}", { n: id });
    };
    const parts = Object.entries(when ?? {}).map(([key, values]) => {
      if (key === "place") {
        return values.includes("other") ? t("Battle elsewhere")
          : t("Battle in {places}", { places: [...new Set(values.map(place))].join(", ") });
      }
      if (key === "species") {
        return values.map((slot) => {
          const record = this.view.record(slot);
          const name = (record && this.view.speciesName?.(record)) || this.view.text(record?.name) || `species:${slot}`;
          const outcome = generated?.species_variants?.find((row) => row.species === slot);
          return t("{species}: {condition}", { species: name, condition: this.speciesCondition(outcome) });
        }).join(" / ");
      }
      const label = labels.get(key);
      const text = values.length === 1 && [0, 1].includes(values[0]) ? label?.[values[0] ? "set" : "clear"] : null;
      return text && !/\{flag\}|0x[\da-f]+/i.test(text) ? t(text) : null;
    });
    return parts.includes(null) ? null : parts.join(", ");
  }


  /** Preserve unresolved predicates with their complete outcomes. */
  technicalConditions(battle) {
    const rows = [];
    const walk = (value, path, member) => {
      if (!value || typeof value !== "object") return;
      if (value.when && this.evCondition(value.when, member) === null) {
        rows.push({ slot: member.slot, path, ...value });
        return;
      }
      for (const [key, child] of Object.entries(value)) walk(child, path ? `${path}.${key}` : key, member);
    };
    for (const member of this.data.trainer_ivs?.members ?? []) {
      if (member.trainer === battle.id) walk(member, "", member);
    }
    return rows.length ? [[t("Unresolved conditions"), el("pre", {}, JSON.stringify(rows, null, 2))]] : [];
  }


  /** Link to an item card with its icon. */
  itemLink(id) {
    return itemLink(id, (i) => this.view.text(i), this.data.items ?? [], (p) => this.host.spriteSrc(p),
      (i) => this.view.openItem?.(i));
  }
};
