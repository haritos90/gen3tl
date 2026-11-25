import { el, keepPosition, setText, updateChildren } from "./dom.js";
import { translationState } from "./draft.js";
import { rulePreview } from "./preview.js";
import { gameText } from "./site-model.js";
import { N_, errorText, formatNumber, t } from "./ui.js";

const STATES = { original: N_("Inherited original"), empty: N_("Off"), text: N_("Custom") };
const badge = (key) => el("span", { class: "badge", textContent: t(key) });
const hex = (value) => `0x${value.toString(16).toUpperCase()}`;

export const engineRulesOf = (index) => index?.engine_rules?.rules ?? [];

export class EngineRulesView {
  constructor(host, $) {
    this.host = host;
    this.$ = $;
    this.cur = null;
    this.filtered = [];
    this.requests = new Map();
    this.serial = 0;
    $("er-search").addEventListener("input", () => this.applyFilters());
    $("er-prev").addEventListener("click", () => this.step(-1));
    $("er-next").addEventListener("click", () => this.step(1));
    $("er-fills").addEventListener("toggle", () => this.renderList());
  }

  rule(id) {
    return engineRulesOf(this.host.index()).find((rule) => rule.id === id);
  }

  /** A rule's title or description in the game's own texts. */
  text(key) {
    return gameText(this.host.game?.(), key);
  }

  reset(id = null) {
    this.requests.clear();
    this.serial++;
    this.cur = id;
    this.$("er-search").value = "";
    this.$("er-fills").open = false;
    this.$("er-body").replaceChildren();
    this.applyFilters();
  }

  applyFilters() {
    const query = this.$("er-search").value.trim().toLowerCase();
    this.filtered = engineRulesOf(this.host.index()).filter((rule) => {
      const sources = rule.sources.map((source) => {
        const row = this.host.row(source.entry);
        return [source.entry, row?.source_text, row?.translation, this.host.edits()?.get(source.entry)?.translation].join(" ");
      });
      return [rule.id, this.text(rule.title), this.text(rule.description), ...sources].join(" ").toLowerCase().includes(query);
    });
    if (query && this.filtered.some((rule) => rule.collapsed)) this.$("er-fills").open = true;
    this.show(this.filtered.some((rule) => rule.id === this.cur) ? this.cur : null);
  }

  show(id = this.cur) {
    const rule = id === null ? this.filtered.find((row) => !row.collapsed) : this.rule(id);
    this.cur = rule?.id ?? null;
    if (rule?.collapsed) this.$("er-fills").open = true;
    this.host.reference(this.cur);
    this.renderList();
    this.renderDetail();
    this.host.setHash(this.cur);
  }

  step(delta) {
    const visible = this.filtered.filter((rule) => !rule.collapsed || this.$("er-fills").open);
    const at = visible.findIndex((rule) => rule.id === this.cur);
    const next = visible[Math.max(0, Math.min(visible.length - 1, at + delta))];
    if (next) this.show(next.id);
  }

  renderList() {
    const { $ } = this;
    setText($("er-count"), t("{n} rules", { n: formatNumber(this.filtered.length) }));
    const rows = (collapsed) => this.filtered.filter((rule) => Boolean(rule.collapsed) === collapsed).map((rule) =>
      el("button", { type: "button", class: "er-row", "data-key": rule.id,
        "aria-pressed": String(rule.id === this.cur), on: { click: () => this.show(rule.id) } },
      el("span", { textContent: this.text(rule.title) }), el("span", { class: "eid muted", textContent: rule.id })));
    updateChildren($("er-list"), ...rows(false));
    updateChildren($("er-fill-list"), ...rows(true));
    $("er-fills").hidden = !this.filtered.some((rule) => rule.collapsed);
    const visible = this.filtered.filter((rule) => !rule.collapsed || $("er-fills").open);
    const at = visible.findIndex((rule) => rule.id === this.cur);
    $("er-prev").disabled = at <= 0;
    $("er-next").disabled = at < 0 || at >= visible.length - 1;
  }

  source(rule, id) {
    const row = this.host.row(id);
    const source = rule.sources.find((item) => item.entry === id);
    const available = Boolean(row);
    const draft = this.host.edits()?.get(id);
    const value = draft ?? row;
    const state = draft ? translationState(draft) : source?.state ?? (row ? translationState(row) : null);
    const content = el("div", { class: "er-source", "data-key": id },
      el("button", { type: "button", class: "btn sm", textContent: id, disabled: !available,
        on: { click: () => this.host.openEntry(id) } }));
    if (!available) content.append(badge(N_("Text unavailable")));
    if (state) content.append(badge(STATES[state]));
    if (draft) content.append(badge(N_("Draft")));
    else if ((row?.status ?? source?.status) === "fuzzy") content.append(badge(N_("Translated")));
    const reach = row?.reach ?? source?.reach;
    if ((reach?.class ?? reach) === "never shown") content.append(badge(N_("Never shown")));
    content.append(el("p", { class: "er-text" }, el("strong", { textContent: t("Original") + ": " }),
      typeof row?.source_text === "string" ? row.source_text : t("Original text unavailable without a ROM")));
    const text = state === "original" ? row?.source_text : state === "empty" ? "" : value?.translation;
    content.append(el("p", { class: "er-text" }, el("strong", { textContent: t(draft ? "Draft" : "Current") + ": " }),
      state === "empty" ? t("Off") : text ?? t("Text unavailable")));
    return content;
  }

  textGroup(rule, title, keys) {
    if (!keys.length) return null;
    return el("section", { class: "block" }, el("h2", { textContent: t(title) }),
      [...new Set(keys)].map((id) => this.source(rule, id)));
  }

  evidence(value) {
    return el("details", { class: "er-evidence" }, el("summary", { textContent: t("Profile evidence") }),
      el("pre", { textContent: typeof value === "string" ? value : JSON.stringify(value, null, 2) }));
  }

  operands(rule) {
    if (!rule.operands.length) return null;
    return el("section", { class: "block" }, el("h2", { textContent: t("Affected operands") }),
      rule.operands.map((operand) => el("section", { class: "er-operand", "data-key": operand.id },
        el("h3", { class: "eid", textContent: operand.id }),
        operand.bindings.map((binding) => el("div", { class: "er-binding" },
          el("p", { class: "small" }, binding.routine ? `${binding.routine} · ` : "", hex(binding.offset), " · ",
            t("Byte budget: {n}", { n: binding.byte_budget }), " · ",
            binding.terminated ? t("Includes terminator") : t("Immediate operand")),
          this.source(rule, binding.entry))))));
  }

  declarations(rule) {
    return rule.references.flatMap((reference) => reference.declarations.map((declaration) =>
      el("details", { class: "block er-declaration", "data-key": declaration.id },
        el("summary", {}, el("span", { class: "eid", textContent: declaration.id }), " ", badge(N_("Read-only")),
          declaration.unverified ? badge(N_("Unverified limit")) : null),
        this.textGroup(rule, N_("Affected texts"), declaration.hosts),
        this.textGroup(rule, N_("Source texts"), declaration.sources),
        this.evidence(declaration.facts))));
  }

  controls(rule) {
    if (rule.kind === "policy") return this.policyControls(rule);
    if (rule.kind !== "catalog") return null;
    const request = this.requests.get(rule.id) ?? { operands: {}, quantity: 2, example: rule.examples[0] ?? null };
    this.requests.set(rule.id, request);
    const choices = { original: N_("Original"), off: N_("Off"), custom: N_("Custom") };
    const output = el("div", { class: "er-preview", "aria-live": "polite" });
    const apply = el("button", { type: "button", class: "btn primary", disabled: true, textContent: t("Apply draft") });
    const fields = new Map();
    let report = null, entries = [];
    const refresh = async () => {
      const serial = ++this.serial;
      apply.disabled = true;
      output.replaceChildren(el("p", { class: "muted", textContent: t("Check: loading") }));
      if (!this.host.check?.()?.ready) {
        output.replaceChildren(el("p", { class: "muted", textContent: t("Check: unavailable") }));
        return;
      }
      try {
        const loaded = (await Promise.all(rule.sources.map(async (source) => {
          const row = this.host.row(source.entry);
          if (!row) return null;
          return this.host.loadEntry ? this.host.loadEntry(source.entry) : { ...row, state: source.state };
        }))).filter(Boolean);
        if (serial !== this.serial) return;
        entries = loaded;
        const drafts = entries.flatMap((row) => {
          const draft = this.host.edits().get(row.id);
          return draft ? [{ ...draft, id: row.id }] : [];
        });
        const checked = await this.host.check().rulePreview(rule.id, entries, drafts, structuredClone(request));
        if (serial !== this.serial) return;
        report = checked;
        output.replaceChildren(rulePreview(report, rule));
        if (!report.exact) output.prepend(el("p", { class: "muted", textContent: t("Check: approximate") }));
        apply.disabled = !report.valid || !report.changes.length;
      } catch (error) {
        if (serial === this.serial) output.replaceChildren(el("p", { class: "err", textContent: errorText(error) }));
      }
    };
    apply.addEventListener("click", () => {
      if (apply.disabled || !report?.valid) return;
      this.host.edits().setMany(report.changes.map((change) => {
        const entry = entries.find((row) => row.id === change.id);
        return { entry, translation: change.translation, state: change.state,
          note: this.host.edits().get(entry.id)?.note ?? entry.note, problems: [], exact: report.exact };
      }));
      request.operands = {};
      this.host.changed?.();
      this.renderDetail();
    });
    const form = el("section", { class: "block er-controls", "data-key": "controls" },
      el("h2", { textContent: t("Proposed") }),
      el("div", { class: "toolbar" }, ["original", "off"].filter((choice) =>
        rule.operands.every((operand) => operand.choices?.includes(choice))).map((choice) =>
        el("button", { type: "button", class: "btn sm", textContent: t(choices[choice]), on: { click: () => {
          for (const operand of rule.operands) {
            request.operands[operand.id] = { choice };
            const field = fields.get(operand.id);
            field.select.value = choice;
            field.input.hidden = true;
          }
          refresh();
        } } }))));
    for (const operand of rule.operands) {
      const selected = request.operands[operand.id];
      const input = el("input", { type: "text", class: "input", value: selected?.text ?? "",
        hidden: selected?.choice !== "custom", "aria-label": t("Custom value: {operand}", { operand: operand.id }),
        on: { input: (event) => {
          request.operands[operand.id] = { choice: "custom", text: event.target.value };
          refresh();
        } } });
      const select = el("select", { class: "select", "aria-label": operand.id,
        on: { change: (event) => {
          const choice = event.target.value;
          if (choice) request.operands[operand.id] = { choice, ...(choice === "custom" ? { text: input.value } : {}) };
          else delete request.operands[operand.id];
          input.hidden = choice !== "custom";
          refresh();
        } } }, el("option", { value: "", textContent: t("Keep draft value") }),
      (operand.choices ?? []).map((choice) => el("option", { value: choice, textContent: t(choices[choice]) })));
      select.value = selected?.choice ?? "";
      fields.set(operand.id, { select, input });
      form.append(el("label", { class: "er-control" }, el("span", { class: "eid", textContent: operand.id }), select, input));
    }
    const name = el("input", { type: "text", class: "input", value: request.name ?? "",
      on: { input: (event) => { request.name = event.target.value; refresh(); } } });
    const examples = el("select", { class: "select", "aria-label": t("Example texts"), on: { change: (event) => {
      request.example = event.target.value || null;
      delete request.name;
      name.value = "";
      refresh();
    } } }, el("option", { value: "", textContent: t("Custom") }), rule.examples.map((id) =>
      el("option", { value: id, textContent: id })));
    examples.value = request.example ?? "";
    form.append(el("div", { class: "er-samples" },
      el("label", {}, el("span", { textContent: t("Quantity") }), el("input", { type: "number", min: 0, step: 1,
        class: "input", value: request.quantity, on: { input: (event) => {
          request.quantity = event.target.value === "" ? null : Number(event.target.value); refresh();
        } } })),
      el("label", {}, el("span", { textContent: t("Example name") }), name), examples),
    el("div", { class: "toolbar" }, apply, el("button", { type: "button", class: "btn", textContent: t("Discard draft"),
      on: { click: () => {
        this.host.edits().removeMany(rule.operands.flatMap((operand) => operand.bindings.map((binding) => binding.entry)));
        request.operands = {};
        this.host.changed?.();
        this.renderDetail();
      } } })), output);
    this.refresh = refresh;
    refresh();
    return form;
  }

  policyControls(rule) {
    const policy = rule.policies.length === 1 ? rule.policies[0] : null;
    if (!["code_replacement", "text_prefix"].includes(policy?.kind)) return null;
    const units = policy.kind === "code_replacement";
    const drafts = this.host.ruleEdits?.();
    const draft = drafts?.get(rule.id);
    const request = this.requests.get(rule.id) ?? { value: structuredClone(draft?.value ?? policy.current) };
    this.requests.set(rule.id, request);
    const fields = { height_unit: N_("Height unit"), weight_unit: N_("Weight unit") };
    const original = units ? N_("Use original units") : N_("Show number marker");
    const enabled = units ? N_("Use metric units") : N_("Off");
    const output = el("div", { class: "er-preview", "aria-live": "polite" });
    const apply = el("button", { type: "button", class: "btn primary", disabled: true, textContent: t("Apply draft") });
    const custom = new Map();
    let report = null;
    const refresh = async () => {
      const serial = ++this.serial;
      apply.disabled = true;
      for (const [field, input] of custom) {
        input.disabled = !request.value.enabled || rule.limit_status === "unverified" || !policy.field_limits?.[field];
      }
      if (!this.host.check?.()?.ready) {
        output.replaceChildren(el("p", { class: "muted", textContent: t("Check: unavailable") }));
        return;
      }
      output.replaceChildren(el("p", { class: "muted", textContent: t("Check: loading") }));
      try {
        const checked = await this.host.check().rulePreview(rule.id, [], [],
          structuredClone({ ...request, ...(draft ? { draft } : {}) }));
        if (serial !== this.serial) return;
        report = checked;
        output.replaceChildren(el("div", { class: "er-comparison" }, ["original", "current", "proposed"].map((mode) => {
          const value = report[mode];
          return el("section", { class: "er-state", "data-key": mode },
            el("h3", { textContent: t({ original: N_("Original"), current: N_("Current"), proposed: N_("Proposed") }[mode]) }),
            el("p", { textContent: t(value.enabled ? enabled : original) }),
            units && value.enabled ? (policy.fields ?? []).map((field) => el("p", { class: "er-text" },
              `${t(fields[field] ?? field)}: `, value.fields[field] ?? t("Use catalog text"))) : null);
        })), ...(report.fields ?? []).filter((field) => field.used !== null).map((field) =>
          el("p", { class: "small", textContent: `${t(fields[field.field] ?? field.field)}: ` +
            t("Encoded bytes: {used}/{limit}", { used: field.used, limit: field.limit }) })),
        ...(report.problems ?? []).map((problem) => el("p", { class: "err", textContent: problem })));
        if (!report.exact) output.prepend(el("p", { class: "muted", textContent: t("Check: approximate") }));
        apply.disabled = !drafts || !report.valid || (!report.changes.length && !draft);
      } catch (error) {
        if (serial === this.serial) output.replaceChildren(el("p", { class: "err", textContent: errorText(error) }));
      }
    };
    const select = el("select", { class: "select", "aria-label": this.text(rule.title), on: { change: (event) => {
      request.value.enabled = event.target.value === "enabled";
      if (units) request.value.fields = request.value.enabled
        ? Object.fromEntries([...custom].filter(([, input]) => input.value).map(([field, input]) => [field, input.value])) : {};
      refresh();
    } } },
    rule.choices.includes("original") ? el("option", { value: "original", textContent: t(original) }) : null,
    rule.choices.includes(units ? "enabled" : "off") ? el("option", { value: "enabled", textContent: t(enabled) }) : null);
    select.value = request.value.enabled ? "enabled" : "original";
    const form = el("section", { class: "block er-controls" }, el("h2", { textContent: t("Proposed") }),
      draft ? badge(N_("Draft")) : null, select);
    if (units) for (const field of policy.fields ?? []) {
      const input = el("input", { type: "text", class: "input",
        value: request.value.fields[field] ?? policy.current.fields[field] ?? "",
        placeholder: policy.capability.fallbacks?.[field] ? t("Use catalog text") : "",
        "aria-label": t(fields[field] ?? field), on: { input: (event) => {
          if (event.target.value) request.value.fields[field] = event.target.value;
          else delete request.value.fields[field];
          refresh();
        } } });
      custom.set(field, input);
      form.append(el("label", { class: "er-control" }, el("span", { textContent: t(fields[field] ?? field) }), input));
    }
    apply.addEventListener("click", () => {
      if (apply.disabled || !report?.valid) return;
      if (report.changes.length) drafts.set(report.changes[0]);
      else drafts.remove(rule.id);
      this.requests.delete(rule.id);
      this.host.changed?.();
      this.renderDetail();
    });
    form.append(el("div", { class: "toolbar" }, apply,
      el("button", { type: "button", class: "btn", textContent: t("Discard draft"), on: { click: () => {
        drafts?.remove(rule.id);
        this.requests.delete(rule.id);
        this.host.changed?.();
        this.renderDetail();
      } } })), output);
    this.refresh = refresh;
    refresh();
    return form;
  }

  renderDetail() {
    this.serial++;
    this.refresh = null;
    const { $ } = this, rule = this.rule(this.cur);
    const hasRules = engineRulesOf(this.host.index()).length > 0;
    $("er-empty").hidden = Boolean(rule);
    setText($("er-empty"), !hasRules ? t("No rules declared for this game")
      : !this.filtered.length ? t("No matching rules") : t("Select a rule"));
    $("er-detail").hidden = !rule;
    setText($("er-title"), rule ? this.text(rule.title) : "");
    setText($("er-id"), rule?.id ?? "");
    if (!rule) {
      $("er-body").replaceChildren();
      return;
    }
    const related = [];
    if (rule.references.some((ref) => ref.section === "sprite_font")) related.push([N_("Fonts"), "fonts"]);
    if (rule.references.some((ref) => ref.section === "keyboard") && this.host.index()?.keyboard) {
      related.push([N_("Keyboards"), "keyboards"]);
    }
    $("er-body").replaceChildren(...[
      el("p", { textContent: this.text(rule.description) }),
      el("div", { class: "toolbar" }, rule.read_only ? badge(N_("Read-only")) : null,
        rule.limit_status === "unverified" ? badge(N_("Unverified limit")) : null,
        related.map(([title, view]) => el("button", { type: "button", class: "btn sm", textContent: t(title),
          on: { click: () => this.host.openView(view) } }))),
      this.controls(rule),
      this.operands(rule),
      this.textGroup(rule, N_("Affected texts"), rule.related),
      this.textGroup(rule, N_("Example texts"), rule.examples),
      this.declarations(rule),
      this.evidence({ evidence: rule.evidence, routines: rule.routines, branches: rule.branches, policies: rule.policies }),
    ].flat().filter(Boolean));
  }

  relabel() {
    keepPosition(() => { this.renderList(); this.renderDetail(); });
  }
}
