import { el } from "../dom.js";
import { MoveDialog } from "../movecard.js";
import { siteText, t } from "../ui.js";
import { PICKUP_KINDS } from "./items.js";
import { trainerName, trainerGroups } from "./trainers.js";
import { locationText, pickupLabel, variantLabel, dataSectionGroups, encounterTables, encounterRate, levels, fieldList, mapPosition, itemSpotCrop } from "./locations.js";

export const withLocationCards = (Base) => class extends Base {



  /** Render area artwork and verified event markers. */
  areaImage(area, markers = []) {
    const record = (this.data.maps ?? []).find((row) => row.runtime_map === area.id);
    if (!record) return null;
    const image = el("img", {
      class: "dex-map", src: this.host.spriteSrc(record.image), loading: "lazy",
      alt: this.locationMapName({ map: area.map, runtime_map: area.id, map_section: area.map_section }),
      width: record.width * 16, height: record.height * 16, title: `${record.width} × ${record.height} blocks`,
    });
    const local = markers.filter((row) => row.runtime_map === area.id && mapPosition(record, row));
    if (!local.length) return image;
    const nodes = local.map((row) => {
      const item = row.item == null ? null : (this.data.items ?? []).find((entry) => entry.id === row.item);
      const name = row.choices ? row.choices.map((entry) => this.view.text(`items:${entry.item}`)).join(" / ")
        : item ? this.view.text(item.name) : siteText("Zygarde Cell");
      const description = row.description ? siteText(row.description) : "";
      const label = [name, item ? pickupLabel(row.kind) : "", description].filter(Boolean).join(" · ");
      const attrs = {
        class: `dex-map-marker ${item ? `dex-map-item dex-map-${row.kind === "exchange" ? "gift" : row.kind}` : "dex-map-point"}`, title: label, "aria-label": label,
      };
      const marker = item ? el("button", { ...attrs, type: "button", on: { click: () => this.view.openItem?.(item.id) } },
        item.icon ? el("img", { src: this.host.spriteSrc(item.icon), alt: "" }) : el("span", {}, "◆"))
        : el("span", { ...attrs, role: "img" }, "◆");
      Object.assign(marker.style, { left: `${(row.x + 0.5) * 100 / record.width}%`,
        top: `${(row.y + 0.5) * 100 / record.height}%` });
      return marker;
    });
    return el("div", { class: "dex-map-wrap" }, image, ...nodes);
  }


  locationDetails(group) {
    return (this.data.location_details ?? []).filter((row) => row.map_section === group.section);
  }


  locationMapName(source) {
    const runtime = this.sourceRuntime(source);
    const record = (this.data.location_maps ?? []).find((row) => runtime && row.runtime_map === runtime
      || row.map === source.map && row.runtime_map == null);
    if (record?.name) return t(record.name);
    const section = record?.parent_section ?? record?.map_section ?? source.map_section;
    const location = section == null ? t("Area") : this.view.text(`mapsec:${section}`) || t("map section {n}", { n: section });
    if (record?.kind === "interior") return t("{location} · Interior", { location });
    if (record?.kind === "underground") return t("{location} · Underground", { location });
    if (record?.kind === "area") return t("{location} · Area", { location });
    return location;
  }


  locationArea(group, runtime) {
    const index = group.areas.findIndex((area) => area.id === runtime);
    if (index < 0) return null;
    const area = group.areas[index];
    if (!area.encounters.length) {
      return this.locationMapName({ map: area.map, runtime_map: area.id, map_section: group.section });
    }
    return t("Area {n}", { n: index + 1 });
  }


  sourceRuntime(source) {
    if (source.runtime_map) return source.runtime_map;
    if (/^\d+\.\d+$/.test(source.map)) return source.map;
    const match = /^h([0-9A-Fa-f]{7})$/.exec(source.map ?? "");
    if (!match) return null;
    const header = Number.parseInt(match[1], 16);
    return (this.data.maps ?? []).find((row) => Number.parseInt(row.map_header, 16) === header)?.runtime_map ?? null;
  }


  locationItems(group) {
    const rows = [];
    for (const item of this.data.items ?? []) {
      for (const source of item.sources ?? []) {
        if (!PICKUP_KINDS.includes(source.kind)) continue;
        if (source.choices && source.choices[0].item !== item.id) continue;
        const related = source.related_sections ?? [];
        if (source.map_section !== group.section && !related.includes(group.section)) continue;
        rows.push({ item, source, runtime_map: this.sourceRuntime(source), external: source.map_section !== group.section });
      }
    }
    return rows.sort((a, b) => Number(a.external) - Number(b.external)
      || (a.runtime_map ?? "").localeCompare(b.runtime_map ?? "", undefined, { numeric: true }) || a.item.id - b.item.id);
  }


  /** Link map sections to location pages; otherwise show their names. */
  sectionLink(section) {
    if (this.sectionPagesOf !== this.data) {
      this.sectionPagesOf = this.data;
      this.sectionPages = new Set(dataSectionGroups(this.data)
        .map((group) => group.section));
    }
    const name = this.view.text(`mapsec:${section}`) || t("map section {n}", { n: section });
    return this.sectionPages.has(section) ? this.locationLink(section, null, name) : name;
  }


  locationLink(section, runtime, label) {
    return el("button", { type: "button", class: "dex-link", title: label,
      on: { click: () => this.view.openLocation(section == null ? runtime : String(section)) } }, label);
  }


  locationTable(title, headings, rows) {
    if (!rows.length) return null;
    const headingText = (heading) => heading === "Area" ? t("Area {n}", { n: "" }).trim()
      : ["Item", "Location"].includes(heading) ? t(heading) : siteText(heading);
    const kind = headings[0] === "Area" ? "points" : headings[0] === "From" ? "exits" : "items";
    return el("section", { class: `block dex-location-section dex-location-${kind}` }, el("h2", {}, siteText(title)),
      el("div", { class: "dex-location-table" }, el("table", { class: "t-forms dex-enc dex-location-data" },
        el("thead", {}, el("tr", {}, ...headings.map((heading) => heading === "Map"
          ? el("th", { scope: "col", class: "dex-item-spot", "aria-label": siteText("Map") })
          : el("th", { scope: "col" }, headingText(heading))))),
        el("tbody", {}, ...rows))));
  }


  locationItemSpot(group, { source, runtime_map }) {
    if (!group.areas.some((area) => area.id === runtime_map)) return null;
    const map = (this.data.maps ?? []).find((row) => row.runtime_map === runtime_map);
    if (!map?.image || !(map.width > 0 && map.height > 0)) return null;
    const position = (source.positions ?? [source]).find((row) => mapPosition(map, row));
    return position ? el("span", { class: "dex-item-spot-image", role: "img", "aria-label": siteText("Map"),
      "data-map": runtime_map, "data-x": position.x, "data-y": position.y }) : null;
  }


  /** Observe live nodes after table reconciliation. */
  observeItemSpots(section) {
    this.itemSpotObserver?.disconnect();
    this.itemSpotObserver = null;
    if (!section) return;
    const spots = section.querySelectorAll(".dex-item-spot-image");
    if (!spots.length) return;
    const draw = () => {
      for (const node of spots) {
        const map = (this.data.maps ?? []).find((row) => row.runtime_map === node.dataset.map);
        if (!map) continue;
        const crop = itemSpotCrop(map, { x: Number(node.dataset.x), y: Number(node.dataset.y) });
        Object.assign(node.style, {
          backgroundImage: `url(${JSON.stringify(this.host.spriteSrc(map.image))})`,
          backgroundPosition: `${-crop.left}px ${-crop.top}px`,
          backgroundSize: `${map.width * 16}px ${map.height * 16}px`,
          width: `${crop.width}px`, height: `${crop.height}px`,
        });
        node.style.setProperty("--spot-x", `${crop.markerX}px`);
        node.style.setProperty("--spot-y", `${crop.markerY}px`);
      }
    };
    if (typeof globalThis.IntersectionObserver !== "function") { draw(); return; }
    const observer = new IntersectionObserver((entries) => {
      if (this.itemSpotObserver !== observer || !entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      this.itemSpotObserver = null;
      draw();
    });
    this.itemSpotObserver = observer;
    observer.observe(section);
  }


  locationPoints(group, details) {
    const rows = details.flatMap((record) => record.points.map((point) => {
      const description = locationText(point.description);
      return el("tr", {},
        el("td", {}, this.locationArea(group, record.runtime_map) ?? this.locationMapName(record)),
        el("td", {}, siteText("Zygarde Cell")),
        el("td", {}, description));
    }));
    const trainers = trainerGroups(this.data.trainers ?? [], (id) => this.source(id))
      .filter((trainer) => trainer.battles.some((battle) => (battle.map_section ?? []).includes(group.section)));
    for (const trainer of trainers) {
      const areas = new Set();
      for (const battle of trainer.battles) {
        for (let index = 0; index < (battle.map ?? []).length; index += 1) {
          if (battle.map_section?.[index] === group.section) {
            areas.add(this.locationArea(group, battle.map[index]) ?? this.locationMapName({
              map: battle.map[index], runtime_map: battle.map[index], map_section: group.section,
            }));
          }
        }
      }
      const name = trainerName(trainer, (id) => this.view.text(id), (id) => this.source(id));
      rows.push(el("tr", {},
        el("td", {}, [...areas].join(", ")),
        el("td", {}, el("button", { type: "button", class: "dex-link", title: `trainer:${trainer.id}`,
          on: { click: () => this.view.goTo({ kind: "trainers", id: trainer.id }) } }, name)),
        el("td", {}, this.view.text(trainer.class))));
    }
    return rows;
  }


  locationExits(group, details) {
    return details.flatMap((record) => record.exits.map((exit) => {
      const from = this.locationArea(group, record.runtime_map) ?? this.locationMapName(record);
      const internalArea = exit.to_section === group.section ? this.locationArea(group, exit.to_runtime_map) : null;
      const baseName = this.view.text(`mapsec:${exit.to_section}`) || t("map section {n}", { n: exit.to_section });
      const name = internalArea ? `${baseName} · ${internalArea}` : baseName;
      return el("tr", {}, el("td", {}, from),
        el("td", {}, this.locationLink(exit.to_section, exit.to_runtime_map, name)),
        el("td", {}, siteText(exit.direction)));
    }));
  }


  encounterHelp(rate) {
    this.view.moveDialog ??= new MoveDialog();
    this.view.moveDialog.show(el("div", { class: "dex-move-card-body" },
      el("div", { class: "dex-move-card-head" }, el("h3", {}, rate.label)),
      el("p", { class: "dex-move-card-text" }, rate.note)), rate.label);
  }


  /** Render map sections, encounters and every available location record. */
  locationDetail(group) {
    const details = this.locationDetails(group);
    const items = this.locationItems(group);
    const markers = [...details.flatMap((record) => record.points.map((point) => ({ ...point, runtime_map: record.runtime_map }))),
      ...items.flatMap((row) => (row.source.positions ?? [row.source]).map((position) =>
        ({ ...row.source, ...position, runtime_map: row.runtime_map, item: row.item.id })))];
    const areas = group.areas.map((area, index) => el("section", { class: "block dex-area" },
      el("div", { class: "dex-battle-head" }, el("b", {}, this.locationArea(group, area.id)),
        el("span", {}, area.map_error ?? "")),
      this.areaImage(area, markers),
      el("div", { class: "dex-enc-grid" }, ...encounterTables(area.encounters).map((table) => {
        const rate = encounterRate(table, this.data.encounter_rates);
        return el("table", { class: "t-forms dex-enc" },
          el("caption", {}, el("div", { class: "dex-enc-title" }, `${table.method} · ${variantLabel(table)}`),
            rate ? el("div", { class: "dex-enc-rate" }, el("button", {
              type: "button", class: "dex-link", "aria-haspopup": "dialog",
              on: { click: () => this.encounterHelp(rate) },
            }, rate.label, el("span", { "aria-hidden": "true" }, " ⓘ"))) : null),
          el("tbody", {}, ...table.rows.map((row) => this.encounterRow(row))));
      }))));
    const pointRows = this.locationPoints(group, details);
    const exitRows = this.locationExits(group, details);
    const spots = items.map((row) => this.locationItemSpot(group, row));
    const hasSpots = spots.some(Boolean);
    const itemRows = items.map(({ item, source, runtime_map }, index) => {
      const area = this.locationArea(group, runtime_map);
      const location = area || this.locationMapName(source);
      const description = source.description ? el("span", {}, siteText(source.description)) : null;
      const choices = source.choices ? this.itemChoices(source.choices) : null;
      const rewards = choices?.length && source.choices.length > 1
        ? [el("span", { class: "dex-item-choices" }, ...choices)] : choices;
      return el("tr", {},
        el("td", {}, ...(rewards ?? [this.itemLink(item.id),
          source.quantity > 1 ? ` ${t("×{n}", { n: source.quantity })}` : null])),
        el("td", {}, this.locationLink(source.map_section, runtime_map, location)),
        el("td", {}, el("span", {}, pickupLabel(source.kind)),
          source.choices ? el("span", {}, ` · ${t("Possible rewards")}`) : null,
          item.taught_move != null ? el("span", {}, " · ", this.view.moveLink(item.taught_move)) : null,
          description ? el("span", {}, " · ", description) : null),
        hasSpots ? el("td", { class: "dex-item-spot" }, spots[index]) : null);
    });
    const facts = [...group.areas.flatMap((area, index) => [
      [t("Area {n}", { n: index + 1 }), area.id], [siteText("Context map"), area.map ?? ""],
      [t("Map header"), area.map_header ?? ""], [t("Map section"), group.section ?? ""],
      ...encounterTables(area.encounters).map((table) => [siteText("Encounter parameter"),
        `${t("Area {n}", { n: index + 1 })} · ${table.method} · ${variantLabel(table)} · ${table.rate}`]),
    ]), ...(this.data.location_maps ?? []).filter((row) => row.map_section === group.section
      && !group.areas.some((area) => area.id === row.runtime_map))
      .map((row) => [this.locationMapName(row), row.runtime_map ?? row.map])];
    return el("div", { class: "dex-data-detail" }, ...areas,
      this.locationTable("Points of Interest", ["Area", "Point of interest", "Details"], pointRows),
      this.locationTable("Exits", ["From", "To", "Direction"], exitRows),
      this.locationTable("Item Locations", ["Item", "Location", "Details", ...(hasSpots ? ["Map"] : [])], itemRows),
      el("details", { class: "block dex-record" }, el("summary", {}, t("Technical record")), fieldList(facts)));
  }


  /** Render merged species encounters with levels and chances. */
  encounterRow(row) {
    const species = this.view.record(row.species);
    const name = (species && this.view.speciesName?.(species)) || this.view.text(species?.name) || species?.name || `species:${row.species}`;
    return el("tr", {},
      el("td", { class: "dex-enc-sprite" }, species?.sprite?.front ? el("img", {
        src: this.host.spriteSrc(species.sprite.front), alt: "", loading: "lazy",
      }) : null),
      el("td", {}, el("button", {
        type: "button", class: "dex-link", title: species?.name ?? "", disabled: !species,
        on: { click: () => species && this.view.openSpecies(species.slot) },
      }, name)),
      el("td", { class: "num" }, levels(row)),
      el("td", { class: "num" }, `${row.chance}%`));
  }
};
