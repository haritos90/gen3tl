"""QA gate over the translated PO catalog: encoding, placeholders, layout fit, limits, typography, windows."""

from __future__ import annotations

from ..core.lists import append_unique
from ..core.notes import has_prose
from ..lang import language

import re
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..catalog.catalog import load_catalog, never_shown_tag
from ..catalog.keys import ID_TOKEN
from ..catalog.po import catalog_name
from ..core.charmap import Charmap, CharmapError
from ..core.fields import long_name_fields
# entrycheck is shared with the browser bundle; names re-exported
from .entrycheck import (BRACE, SPACING, Source, addable_tokens, droppable_tokens,  # noqa: F401
                         instant_print_problems, placeholders_differ, token_counts, typography_problems, window_problems)
from .layout import (Limit, Measure, arrow_overflows, check, dropped_breaks, entry_measure, fits, layout, line_width,
                     lines_of, paragraphs, short_line_ends, slot_note, slot_rewraps, split_names, wrapped_dash_lines)
from ..build.release import expand
from ..core.rom import Rom
from ..discover.scan import INPLACE_ONLY_KINDS, table_base_lengths
from .provenance import code as coded, formatted


DIALOGUE_REFS = {"script:loadpointer", "script:message", "script:trainerbattle", "script:autoscroll",
                 "script:vmessage", "script:chain"}


@dataclass
class Finding:
    id: str
    file: str
    severity: str   # error | warning
    message: str


@dataclass
class Gate:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0
    # never-shown entry id -> its evidence
    never_shown: dict[str, str] = field(default_factory=dict)
    never_checked: set[str] = field(default_factory=set)
    source_notes: dict[str, str] = field(default_factory=dict)
    # typography reasons skipped without the English, by name
    skipped_typography: list[str] = field(default_factory=list)

    def add(self, sid, f, sev, msg):
        self.findings.append(Finding(sid, f, sev, msg))

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")


class Windows:
    """Name-table windows measured in game, per font style (profile [[table]] windows)."""

    def __init__(self, prof, inv: dict, measures: dict[str, tuple[Measure, Charmap]] | None = None):
        from ..build.spritefont import from_profile as sprite_fonts
        self.main = prof.get("font_style", {}).get("style")
        self.by_table = {t["name"]: list(t.get("windows", [])) for t in prof.get("table", [])}
        for font in sprite_fonts(prof):
            for w in font.windows:
                if w.table not in self.by_table:
                    raise ValueError(f"[[sprite_font]] {font.name}: no [[table]] {w.table}")
                self.by_table[w.table].append({"sprite": font.name, "width": w.width, "where": w.where,
                                               "suffix": w.suffix})
        tables = {t["name"]: t for t in prof.get("table", [])}
        for name, table in tables.items():
            for window in self.by_table[name]:
                for key in ("indices", "exclude_indices"):
                    values = window.get(key, [])
                    if (not isinstance(values, list) or any(type(i) is not int or not 0 <= i < table["count"]
                                                           for i in values)):
                        raise ValueError(f"[[table]] {name}: invalid window {key}")
                if "chars" in window and (type(window["chars"]) is not int or window["chars"] < 1):
                    raise ValueError(f"[[table]] {name}: invalid window chars")
                if "strict" in window and type(window["strict"]) is not bool:
                    raise ValueError(f"[[table]] {name}: invalid window strict")
        self.table_of, self.by_id = {}, {}
        for e in inv["entries"]:
            records = set()
            if e.get("kind") == "fixed":
                records.add((e["table"], e["index"]))
            for ref in e.get("refs", []):
                name = ref["kind"].removeprefix("table:")
                if not ref["kind"].startswith("table:") or name not in tables:
                    continue
                table = tables[name]
                delta = ref["at"] - table["base"] - table.get("field_offset", 0)
                index, remainder = divmod(delta, table["stride"])
                if remainder or not 0 <= index < table["count"]:
                    raise ValueError(f"{e['id']}: invalid name-table reference {ref['at']:#x}")
                records.add((name, index))
            for name, index in sorted(records):
                self.table_of.setdefault(e["id"], name)
                for window in self.by_table.get(name, []):
                    if ("indices" in window and index not in window["indices"]
                            or index in window.get("exclude_indices", [])):
                        continue
                    self.by_id.setdefault(e["id"], []).append({**window, "table": name})
        self.measures = measures or {}

    def rules(self, sid: str, style: str | None = None) -> list[dict]:
        return [w for w in self.by_id.get(sid, []) if style is None or w.get("style") == style]

    def width(self, sid: str, style: str) -> int | None:
        ws = [w.get("entries", {}).get(sid, w["width"]) for w in self.rules(sid)
              if "sprite" not in w and w.get("style") == style]
        return min(ws) if ws else None

    def other(self, sid: str) -> list[dict]:
        """The windows of the other styles that show `sid`, as {font, width, where}."""
        out = []
        for w in self.rules(sid):
            if "sprite" in w:
                out.append({"font": w["sprite"], "sprite": True, "width": w["width"], "where": w["where"],
                            **({"suffix": w["suffix"]} if w["suffix"] else {})})
            elif w["style"] != self.main:
                out.append({"font": w["style"], "width": w.get("entries", {}).get(sid, w["width"]),
                            "where": w["where"]})
        return out

    def problems(self, sid: str, text: str) -> list[str]:
        # shared with the editor check
        return window_problems(text, self.other(sid), self.measures)


def window_like(text: str) -> bool:
    """A paragraph with more than two lines and no scroll breaks (\\l)."""
    return any(len(lines_of(par)) > 2 and "\\l" not in par for par in paragraphs(text))


def english_width(text: str, m: Measure, cm: Charmap) -> int:
    """The widest line of an English text, without trailing spaces and ending zero-width codes."""
    return max(line_width(l, m, cm) for par in paragraphs(text) for l in lines_of(par))


def facts_rows(catalog, source: dict | None) -> dict[str, list]:
    """The snapshot rows the limits take for entries without their English, else {}."""
    if source is None or not any(e.keyed for _, e in catalog.values()):
        return {}
    return {sid: source[sid] for sid in catalog if sid in source}


def source_text(sid: str, e, rows: dict) -> str:
    """The English the limits read of an entry: its line, else the brace tokens of its row."""
    return "".join(rows[sid][1]) if e.keyed and sid in rows else e.msgid


def row_width(row: list, style: str | None = None, trimmed: bool = False) -> int:
    """The widest English line in its printer style, optionally without trailing spaces."""
    if len(row) > 5:
        widths = row[5]["widths"]
        if style in widths or "main" in widths:
            return widths.get(style, widths.get("main"))[int(trimmed)]
    return max(row[3], default=0)


def row_limit(row: list) -> Limit:
    """The limit of a snapshot row: the one the English as translation gets."""
    kind, width, lines, chars, size, keys, style = row[4]
    return Limit(kind, width, lines, chars=chars, bytes=size, keys=keys, style=style)


def source_of(sid: str, e, rows: dict) -> str | Source:
    """What a check reads of an entry's English: its line, else the facts of its row."""
    if not (e.keyed and sid in rows):
        return e.msgid
    from ..catalog.source import digest
    row = rows[sid]
    return Source(tuple(row[1]), tuple(row[2]), row[0] == digest(""))


def typography_without_english(rules: dict) -> tuple[dict[str, str], list[str]]:
    """The typography rules that run without the English; `forbidden_needs_source` marks the rest."""
    forbidden = rules.get("forbidden", {})
    needs = rules.get("forbidden_needs_source", {})
    independent = {pattern: reason for pattern, reason in forbidden.items() if not needs.get(pattern, True)}
    skipped = [reason for pattern, reason in forbidden.items() if needs.get(pattern, True)]
    return independent, skipped


@dataclass
class SlotWidth:
    """Width of one placeholder slot of an entry."""
    translated: int          # Maximum inserted text width, including profile bounds.
    source_width: int          # px: widest English of the same insertions, else profile width
    source: dict     # where the width comes from, coded
    known: str       # script | partly | profile | record | code
    details: dict | None = None


NUMBER_DIGITS = 5   # a script variable holds 0-65535


def with_reasons(source: dict, why: list[dict]) -> dict:
    """A slot source with what leaves it partly unknown."""
    return coded("slot_reasons", source=source, reasons=why) if why else source


def slot_widths(prof, inv: dict, ctx: dict, catalog, measure: Measure, cm: Charmap,
                overrides: dict[str, str] | None = None, rows: dict | None = None,
                computed: dict | None = None, fragments=None) -> dict[str, dict[str, SlotWidth]]:
    """Entry id -> placeholder name -> SlotWidth, from the context records' slot fills."""
    overrides = overrides or {}
    rows = rows or {}
    from .fillsources import Evaluator, native_graph, merge_results
    selected = {value for sid, row in (ctx or {}).get("strings", {}).items() if sid in catalog
                for name, info in row.get("slots", {}).items() if name in source_text(sid, catalog[sid][1], rows)
                for kind, value in info.get("fills", []) if kind == "native"}
    native = native_graph(prof, inv, catalog, cm, fragments=fragments, selected=selected)
    native_evaluator = Evaluator(native, native["values"], measure, cm, native["fragments"], overrides)
    native_widths = {}
    tables = prof.get("layout", {}).get("buffer_tables", {})
    tdefs = {t["name"]: t for t in prof.get("table", [])}
    by_id = {e["id"]: e for e in inv["entries"]}
    entry_at: dict[tuple[str, int], str] = {}
    for e in inv["entries"]:
        if e["kind"] == "fixed":
            entry_at[(e["table"], e["index"])] = e["id"]
        for r in e["refs"]:
            t = tdefs.get(r["kind"][6:]) if r["kind"].startswith("table:") else None
            if t is not None:   # a long-name pointer in the record
                entry_at.setdefault((t["name"], (r["at"] - t["base"] - t.get("field_offset", 0)) // t["stride"]),
                                    e["id"])

    def px(text: str, m: Measure = measure) -> int:
        try:
            return max(m.width(l, cm) for par in paragraphs(text) for l in lines_of(par))
        except CharmapError:
            return -1

    def english_px(sid: str, en: str) -> int:
        return row_width(rows[sid], prof.get("font_style", {}).get("style")) \
            if sid in catalog and catalog[sid][1].keyed and sid in rows else px(en)

    def word(sid: str) -> tuple[int, int, dict]:
        en = catalog[sid][1].msgid if sid in catalog else by_id[sid]["text"]
        en_px = english_px(sid, en)
        if sid not in overrides and sid in catalog and "empty" in catalog[sid][1].flags:
            return 0, en_px, coded("slot_empty", id=sid)
        translated = overrides.get(sid) or (catalog[sid][1].msgstr if sid in catalog else "")
        translated_px = px(translated) if translated else -1
        if translated_px < 0:
            return en_px, en_px, coded("slot_untranslated", id=sid)
        return translated_px, en_px, coded("slot_word", id=sid, text=repr(translated))

    widest: dict[str, tuple] = {}
    table_names = {t for t, _ in entry_at}

    def table(name: str) -> tuple[int, int, dict]:
        if name not in widest:
            ids = [sid for (t, _), sid in entry_at.items() if t == name]
            ws = [(word(sid), sid) for sid in ids]
            (translated, _, desc), _ = max(ws, key=lambda x: x[0][0])
            widest[name] = (translated, max(w[0][1] for w in ws), coded("slot_table", table=name, word=desc))
        return widest[name]

    def resolve(kind: str, value, fallback) -> tuple[int, int, dict] | None:
        if kind == "native":
            result = native_evaluator.result("native/" + value, fallback=fallback)
            native_widths[value] = result
            ids = [part["id"] for part in result["contributors"]]
            return result["width"], result["source_width"], coded("slot_native", table=value, ids=ids)
        if kind == "string":
            return word(value) if value in by_id else None
        if kind == "number":
            if value is None:
                digits = max(px(d) for d in "0123456789") * NUMBER_DIGITS
                return digits, digits, coded("slot_number_variable", digits=NUMBER_DIGITS)
            return px(str(value)), px(str(value)), coded("slot_number", value=value)
        name = tables.get(kind)
        if name not in table_names:
            return None
        if value is not None and (name, value) in entry_at:
            translated, en, desc = word(entry_at[(name, value)])
            return translated, en, coded("slot_record", kind=kind, word=desc)
        return table(name)

    out: dict[str, dict[str, SlotWidth]] = {}
    if computed is not None:
        for sid, (_, entry) in catalog.items():
            used = source_text(sid, entry, rows)
            selected = {name: value for name, value in computed.items() if "{" + name + "}" in used}
            if selected:
                out[sid] = selected
    if ctx is None:
        return out
    strings = ctx.get("strings", {})
    estimates = unverified_slots(prof, "placeholders")
    for sid, (p, e) in catalog.items():
        names = sorted(set(re.findall(r"\{(STR_VAR_\d)\}", source_text(sid, e, rows))))
        if not names:
            continue
        rec = strings.get(sid, {})
        refs = rec.get("slot_refs", {})
        for name in names:
            fallback = measure.ph.get(name, measure.default_ph)
            estimate = name in estimates
            info = rec.get("slots", {}).get("{%s}" % name)
            fills = [(f, resolve(*f, fallback)) for f in (info or {}).get("fills", [])]
            known = [r for _, r in fills if r is not None]
            why = []   # what leaves the slot partly unknown
            if not info or not info["fills"]:
                why.append(coded("slot_no_fill"))
            elif info["unfilled"]:
                why.append(coded("slot_unfilled", unfilled=info["unfilled"], uses=info["uses"]))
            odd = sorted({f[0] for f, r in fills if r is None})
            if odd:
                why.append(coded("slot_unnamed", kinds=odd))
            if refs.get("unwalked"):
                why.append(coded("slot_unwalked", references=refs["unwalked"]))
            if refs.get("code"):
                why.append(coded("slot_by_code"))
            if not known:
                profile = coded("slot_profile_estimate" if estimate else "slot_profile")
                out.setdefault(sid, {})[name] = SlotWidth(fallback, fallback, with_reasons(profile, why), "profile")
                continue
            details = [native_widths[value] for (kind, value), result in fills if kind == "native" and result]
            floor = max([fallback if why else 0] + [result[0] for (kind, _), result in fills
                                                    if kind != "native" and result])
            detail = merge_results(details, floor) if details else None
            translated, _, desc = max(known, key=lambda r: r[0])
            en = max(r[1] for r in known)
            if why and fallback > translated:
                above = coded("slot_profile_estimate_above" if estimate else "slot_profile_above", source=desc,
                              width=translated)
                out.setdefault(sid, {})[name] = SlotWidth(fallback, en, with_reasons(above, why), "partly", detail)
            else:
                out.setdefault(sid, {})[name] = SlotWidth(translated, en, with_reasons(desc, why),
                                                          "partly" if why else "script", detail)
    return out


def box_cap(boxes: dict[str, int], kind: str, uses: list[dict]) -> int | None:
    """The measured text width of the message box a dialogue or battle entry is shown in, or None."""
    bound = box_binding(boxes, kind, uses)
    return None if bound is None else bound[1]


def box_binding(boxes: dict[str, int], kind: str, uses: list[dict]) -> tuple[str, int] | None:
    """The message box of box_cap and its width: (name, px), None where none binds."""
    if kind == "battle":
        shown = {"battle"}
    elif kind == "dialogue":
        shown = {"dialogue"} | ({"sign"} if any(u.get("event") == "sign" or u.get("box") == "sign"
                                                for u in uses) else set())
    else:
        return None
    widths = sorted((boxes[b], b) for b in shown if b in boxes)
    return (widths[0][1], widths[0][0]) if widths else None


def record_list(rom: Rom, start: int, stride: int, field: int, end: int | None) -> list[int]:
    """Text pointer positions of a list of entries `stride` bytes apart from `start`."""
    out = []
    e = start
    while e + stride <= len(rom):
        if end is not None and rom.u16(e + end) == 0:
            break
        if end is None and rom.ptr_at(e + field) is None:
            break
        out.append(e + field)
        e += stride
    return out


def window_texts(lay: dict, rom: Rom | None = None) -> list[tuple[int, int, int | None]]:
    """(text pointer position, window width, window lines or None) for every pointer of the measured windows."""
    return [(at, width, lines) for at, width, lines, _ in window_rules(lay, rom)]


def table_window_positions(tw: dict) -> list[int]:
    """Text pointer positions of a [[layout.table_window]] rule."""
    idx = tw.get("indexes", range(tw["count"]))
    if any(not 0 <= i < tw["count"] for i in idx):
        raise ValueError(f"[[layout.table_window]] {tw.get('name')}: an index outside count {tw['count']}")
    return [tw["base"] + i * tw["stride"] + f for i in idx for f in tw.get("fields", [0])]


def window_rules(lay: dict, rom: Rom | None = None) -> list[tuple[int, int, int | None, dict]]:
    """window_texts with the rule of each text pointer: (position, width, lines, rule)."""
    out = []
    for tw in lay.get("table_window", []):
        out += [(at, int(tw["width"]), tw.get("lines"), tw) for at in table_window_positions(tw)]
    rules = lay.get("record_window", [])
    if rules and rom is None:
        raise ValueError("[[layout.record_window]] reads the ROM: pass rom")
    for rule in rules:
        for i in range(rule["count"]):
            rec = rom.ptr_at(rule["table"] + 4 * i)
            if rec is None:
                continue
            kind = rom.u8(rec + rule["type_at"]) if "type_at" in rule else None
            for t in rule["texts"]:
                if "types" in t and kind not in t["types"]:
                    continue
                pos = rec
                for o in t["path"][:-1]:
                    pos = rom.ptr_at(pos + o) if pos is not None else None
                if pos is None:
                    continue
                start = pos + t["path"][-1]
                ats = [start] if "stride" not in t else record_list(rom, start, t["stride"], t.get("field", 0),
                                                                    t.get("end"))
                width, lines = int(t.get("width", rule["width"])), t.get("lines", rule.get("lines"))
                out += [(at, width, lines, rule) for at in ats]
    return out


def window_chars(lay: dict) -> dict[int, int]:
    """Text pointer position -> character limit, for [[layout.table_window]] rules with `chars`."""
    out: dict[int, int] = {}
    for tw in lay.get("table_window", []):
        if "chars" not in tw:
            continue
        n = int(tw["chars"])
        for at in table_window_positions(tw):
            out[at] = min(n, out.get(at, n))
    return out


def window_boxes(lay: dict) -> dict[int, str]:
    """Text pointer position -> message box ("dialogue" or "battle"), for rules with `box`."""
    out: dict[int, str] = {}
    for tw in lay.get("table_window", []):
        if "box" not in tw:
            continue
        if tw["box"] not in ("dialogue", "battle"):
            raise ValueError(f"[[layout.table_window]] {tw.get('name')}: box {tw['box']!r} is not dialogue or battle")
        for at in table_window_positions(tw):
            out.setdefault(at, tw["box"])
    return out


def window_icon_spacings(lay: dict) -> dict[int, tuple[int, dict]]:
    """Text pointer position -> largest declared printer spacing and its rule."""
    out: dict[int, tuple[int, dict]] = {}
    for tw in lay.get("table_window", []):
        if "letter_spacing" not in tw:
            continue
        spacing = tw["letter_spacing"]
        if type(spacing) is not int or not 0 <= spacing <= 255:
            raise ValueError(f"[[layout.table_window]] {tw.get('name')}: letter_spacing must be an integer 0..255")
        for at in table_window_positions(tw):
            if at not in out or spacing > out[at][0]:
                out[at] = spacing, tw
    return out


def window_centres(lay: dict) -> dict[int, tuple[str, int]]:
    """Text pointer position -> (reference id, step), for [[layout.table_window]] rules with `centre`."""
    out: dict[int, tuple[str, int]] = {}
    for tw in lay.get("table_window", []):
        c = tw.get("centre")
        if c is None:
            continue
        for at in table_window_positions(tw):
            out.setdefault(at, (c["reference"], int(c["step"])))
    return out


def window_strips(lay: dict) -> dict[int, tuple[tuple[str, int], ...]]:
    """Text pointer position -> the prefixes the code strips, as (reference id, characters) pairs."""
    out: dict[int, tuple[tuple[str, int], ...]] = {}
    for tw in lay.get("table_window", []):
        s = tuple((x["reference"], int(x["chars"])) for x in tw.get("strip", []))
        for at in table_window_positions(tw):
            out[at] = s if out.get(at, s) == s else ()
    return out


def path_end(rom: Rom, pos: int | None, path: list[int]) -> int | None:
    """The position a path of byte offsets leads to from `pos`, or None where one is no ROM pointer."""
    for o in path[:-1]:
        pos = rom.ptr_at(pos + o) if pos is not None else None
    return None if pos is None else pos + path[-1]


def record_numbers(lay: dict, rom: Rom | None = None) -> dict[int, dict[str, int]]:
    """Text pointer position -> placeholder name -> the largest number the game prints there."""
    out: dict[int, dict[str, int]] = {}
    rules = lay.get("record_window", [])
    if rules and rom is None:
        raise ValueError("[[layout.record_window]] reads the ROM: pass rom")
    for rule in rules:
        for i in range(rule["count"]):
            rec = rom.ptr_at(rule["table"] + 4 * i)
            if rec is None:
                continue
            kind = rom.u8(rec + rule["type_at"]) if "type_at" in rule else None
            for t in rule["texts"]:
                num = t.get("number")
                if num is None or "stride" in t or ("types" in t and kind not in t["types"]):
                    continue
                at, val = path_end(rom, rec, t["path"]), path_end(rom, rec, num["path"])
                if at is None or val is None:
                    continue
                n = rom.u32(val) if num.get("bytes", 4) == 4 else rom.u16(val)
                for name in num["slots"]:
                    out.setdefault(at, {})[name] = max(n, out.get(at, {}).get(name, 0))
    return out


def number_slots(english: str, inv_e: dict, number_at: dict[int, dict[str, int]],
                 digit_px: int) -> dict[str, SlotWidth]:
    """The placeholders of an entry that a record number fills (record_numbers), with their widths."""
    largest: dict[str, int] = {}
    for r in inv_e["refs"]:
        for name, n in number_at.get(r["at"], {}).items():
            if "{%s}" % name in english:
                largest[name] = max(n, largest.get(name, 0))
    return {name: SlotWidth(len(str(n)) * digit_px, len(str(n)) * digit_px,
                            coded("slot_record_number", digits=len(str(n)), value=n), "record")
            for name, n in largest.items()}


def code_fills(lay: dict) -> tuple[dict[str, list[dict]], dict[int, list[dict]]]:
    """The [[layout.code_fill]] rules by entry id and by text pointer position."""
    by_id: dict[str, list[dict]] = {}
    by_at: dict[int, list[dict]] = {}
    for r in lay.get("code_fill", []):
        if not has_prose(r.get("evidence")):
            raise ValueError(f"[[layout.code_fill]] {r.get('ids') or r.get('base')} has no evidence")
        for name, s in r["slots"].items():
            declared = name in lay.get("code_slot_source", {}).get(r.get("name"), {})
            if len({"digits", "width", "strings"} & set(s)) != 1 and not declared:
                raise ValueError(f"[[layout.code_fill]] {name}: give digits, width or strings")
            if "strings" in s:
                ids = s["strings"]
                if (not isinstance(ids, list) or not ids
                        or any(not isinstance(sid, str) or not ID_TOKEN.fullmatch(sid) for sid in ids)
                        or len(ids) != len(set(ids))
                        or set(s) - {"strings", "originals", "font"} or not isinstance(s.get("font"), str)):
                    raise ValueError(f"[[layout.code_fill]] {name}: invalid source strings")
            elif "originals" in s or "font" in s:
                raise ValueError(f"[[layout.code_fill]] {name}: originals and font require strings")
            if "source_width" in s and "width" not in s and not declared:
                raise ValueError(f"[[layout.code_fill]] {name}: source_width goes with a width")
        for sid in r.get("ids", []):
            by_id.setdefault(sid, []).append(r)
        if "base" in r:
            for i in range(r["count"]):
                for f in r.get("fields", [0]):
                    by_at.setdefault(r["base"] + i * r["stride"] + f, []).append(r)
    return by_id, by_at


def source_fill_rules(prof, catalog, inv=None):
    """Select rules used by the supplied catalog subset."""
    for rule in prof.get("layout", {}).get("code_fill", []):
        if not any("strings" in slot or "digits" in slot for slot in rule["slots"].values()):
            continue
        if set(rule.get("ids", [])) & catalog.keys():
            yield rule
        elif "base" in rule:
            positions = {rule["base"] + i * rule["stride"] + field
                         for i in range(rule["count"]) for field in rule.get("fields", [0])}
            if inv is None or any((ref["at"] if isinstance(ref, dict) else ref[0]) in positions
                                  for entry in inv["entries"] if entry["id"] in catalog for ref in entry["refs"]):
                yield rule


def fill_originals(prof, catalog, rom=None, facts=None, inv=None) -> dict:
    """Validate explicit unextracted literals and their font measurements."""
    from ..build.font import FontTable

    code_fills(prof.get("layout", {}))
    declarations = {}
    fonts = {font.name: font for font in FontTable.from_profile(prof)} if prof.get("font") else {}
    for rule in source_fill_rules(prof, catalog, inv):
        for slot in rule["slots"].values():
            if "strings" not in slot:
                continue
            if slot["font"] not in fonts:
                raise ValueError("code fill names an unknown font")
            for sid in slot["strings"]:
                if sid not in catalog:
                    raise ValueError(f"{sid}: code-fill source is missing from catalog")
            if not isinstance(slot.get("originals", []), list):
                raise ValueError("code-fill originals must be a list")
            for source in slot.get("originals", []):
                if (not isinstance(source, dict) or set(source) != {"id", "length", "sha256"}
                        or not isinstance(source["id"], str) or not re.fullmatch(r"s[0-9A-F]{7}", source["id"])
                        or type(source["length"]) is not int or not 2 <= source["length"] <= 256
                        or not isinstance(source["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])):
                    raise ValueError("invalid code-fill original descriptor")
                sid = source["id"]
                if sid in catalog:
                    raise ValueError(f"{sid}: code-fill original now has a catalog entry")
                value = {**source, "font": slot["font"]}
                if sid in declarations and declarations[sid] != value:
                    raise ValueError(f"{sid}: conflicting code-fill original descriptors")
                declarations[sid] = value
    if facts is not None:
        if not isinstance(facts, dict) or set(facts) != set(declarations):
            raise ValueError("code-fill original facts differ from declarations")
        for sid, source in declarations.items():
            fact = facts[sid]
            if (not isinstance(fact, dict) or set(fact) != {"length", "sha256", "font", "width", "glyph_counts"}
                    or any(fact.get(key) != source[key] for key in ("length", "sha256", "font"))
                    or type(fact["width"]) is not int or fact["width"] < 0
                    or not isinstance(fact["glyph_counts"], list)):
                raise ValueError(f"{sid}: invalid code-fill original fact")
            counts = fact["glyph_counts"]
            if (any(not isinstance(row, list) or len(row) != 2 or any(type(x) is not int for x in row)
                    or not 0 <= row[0] < min(0xF7, fonts[source["font"]].count) or row[1] <= 0 for row in counts)
                    or [row[0] for row in counts] != sorted(set(row[0] for row in counts))
                    or sum(row[1] for row in counts) != source["length"] - 1):
                raise ValueError(f"{sid}: invalid code-fill glyph counts")
        return facts
    if declarations and rom is None:
        raise ValueError("code-fill originals require verified ROM facts")
    out = {}
    for sid, source in declarations.items():
        data = rom.read(int(sid[1:], 16), source["length"])
        if (len(data) != source["length"] or data[-1] != 255 or any(b >= min(0xF7, fonts[source["font"]].count) for b in data[:-1])
                or hashlib.sha256(data).hexdigest() != source["sha256"]):
            raise ValueError(f"{sid}: code-fill original bytes differ")
        font = fonts[source["font"]]
        out[sid] = {key: source[key] for key in ("length", "sha256", "font")}
        out[sid].update(width=sum(font.width(rom, b) for b in data[:-1]),
                        glyph_counts=[list(pair) for pair in sorted(Counter(data[:-1]).items())])
    return out


def source_fills(prof, catalog, measure, cm, originals, overrides=None, inv=None, rows=None,
                 evaluator=None, fragments=None) -> dict:
    """Measure declared catalog fills with the shared source evaluator."""
    from .fillsources import Evaluator, code_graph
    selected = {id(rule) for rule in source_fill_rules(prof, catalog, inv)}
    legacy = code_graph(prof, catalog, originals, measure, cm, rules=selected, fragments=fragments)
    measured = Evaluator(legacy, legacy["values"], measure, cm, legacy["fragments"], overrides)
    out = {}
    for index, rule in enumerate(prof.get("layout", {}).get("code_fill", [])):
        name = rule.get("name", f"reader-{index}")
        for token, source in legacy["code"].get(name, {}).items():
            slot = rule["slots"][token]
            recorded = slot.get("width", slot.get("source_width", measure.ph.get(token, measure.default_ph)))
            result = measured.result(source, fallback=recorded)
            sid = next((part["id"] for part in result["contributors"] if "id" in part), None)
            if sid:
                desc = coded("slot_widest_source", id=sid)
            elif "digits" in slot:
                desc = coded("slot_digits" if slot["digits"] > 1 else "slot_digit", digits=slot["digits"])
            else:
                desc = coded("slot_label", label=result["label"])
            out[id(slot)] = (result["width"], result["source_width"], desc, result)
    if evaluator is not None:
        lay = prof.get("layout", {})
        for rule in lay.get("code_fill", []):
            for token, name in lay.get("code_slot_source", {}).get(rule.get("name"), {}).items():
                if name not in evaluator.graph["sources"]:
                    continue
                slot = rule["slots"][token]
                result = evaluator.result(name, slot.get("width"),
                                          lay.get("code_slot_override", {}).get(rule["name"], {}).get(token),
                                          fallback=slot.get("source_width", measure.ph.get(token)))
                out[id(slot)] = (result["width"], result["source_width"], coded("slot_label", label=result["label"]),
                                 result)
    return out


def fill_slots(sid: str, english: str, inv_e: dict, fills: tuple[dict, dict], digit_px: int, sources=None,
               estimates: frozenset = frozenset()) -> dict[str, SlotWidth]:
    """The placeholders of an entry the code fills (code_fills), each as wide as its digits or width."""
    by_id, by_at = fills
    rules = by_id.get(sid, []) + [r for x in inv_e["refs"] for r in by_at.get(x["at"], [])]
    out: dict[str, SlotWidth] = {}
    for r in rules:
        for name, s in r["slots"].items():
            if "{%s}" % name not in english:
                continue
            details = None
            if id(s) in (sources or {}):
                px, en, desc, *extra = sources[id(s)]
                details = extra[0] if extra else None
            elif "digits" in s:
                n = int(s["digits"])
                px, desc = n * digit_px, coded("slot_digits" if n > 1 else "slot_digit", digits=n)
            else:
                px = int(s["width"])
                desc = coded("slot_width_estimate" if r.get("name") in estimates else "slot_width", width=s["width"])
            if id(s) not in (sources or {}):
                en = int(s.get("source_width", px))   # the width of the English fills
            if "source_width" in s:
                desc = coded("slot_english", fill=desc, source_width=s["source_width"])
            previous = out.get(name)
            description = (coded("slot_fill", fill=desc, where=r["where"]) if "where" in r
                           else coded("slot_code_fill", fill=desc))
            if previous is not None:
                from .fillsources import merge_results
                known = [item for item in (previous.details, details) if item]
                if known:
                    floor = max(previous.translated if previous.details is None else 0,
                                px if details is None else 0)
                    details = merge_results(known, floor)
                # Each language keeps its widest fill.
                if px <= previous.translated:
                    description = previous.source
                px, en = max(px, previous.translated), max(en, previous.source_width)
            out[name] = SlotWidth(px, en, description, "code", details)
    return out


def row_keyboards(prof, inv: dict) -> dict[str, int]:
    """Entry id -> the base keyboard of a naming keyboard row."""
    kb = prof.get("keyboard")
    tabs = {t["name"]: t for t in (kb or {}).get("table", [])}
    keys, rows = tabs.get("keys"), tabs.get("rows")
    out: dict[str, int] = {}
    if keys is None and rows is None:
        return out
    n = kb["base_pages"]
    if keys is not None:
        strides = {t["name"]: t["stride"] for t in prof.get("table", []) if t["base"] == keys["base"]}
        for e in inv["entries"]:
            if e["kind"] == "fixed" and e.get("table") in strides:
                k = e["index"] * strides[e["table"]] // keys["entry"]
                if k < n:
                    out[e["id"]] = k
    if rows is not None:
        for e in inv["entries"]:
            for r in e["refs"]:
                k = (r["at"] - rows["base"]) // rows["entry"]
                if r["at"] >= rows["base"] and k < n:
                    out[e["id"]] = k
    return out


def keyboard_rows(prof, inv: dict, rom: Rom | None = None) -> dict[str, int]:
    """Entry id -> key cells of a naming keyboard row (row_keyboards), from the [keyboard] table `columns`."""
    kb = prof.get("keyboard")
    tabs = {t["name"]: t for t in (kb or {}).get("table", [])}
    cols = tabs.get("columns")
    if cols is None or not ({"keys", "rows"} & set(tabs)):
        return {}
    if rom is None:
        raise ValueError("[keyboard] columns are read from the ROM: pass rom")
    columns = [rom.u8(cols["base"] + k * cols["entry"]) for k in range(kb["base_pages"])]
    return {sid: columns[k] for sid, k in row_keyboards(prof, inv).items()}


def pack_columns(prof, inv: dict) -> dict[str, int]:
    """Entry id -> the columns the language pack's keyboard declares for the row, the least where several do."""
    declared: dict[int, int] = {}
    for k in (prof.get("keyboard") or {}).get("keyboards", []):
        if "columns" in k and not k.get("base"):
            declared[k["like"]] = min(k["columns"], declared.get(k["like"], k["columns"]))
    return {sid: declared[k] for sid, k in row_keyboards(prof, inv).items() if k in declared}


def interior_heads(prof, inv: dict, cm: Charmap, rom: Rom | None = None) -> dict[str, str]:
    """Entry id -> the text its translation must begin with."""
    by_id = {e["id"]: e for e in inv["entries"]}
    out = {}
    for d in prof.get("strings", {}).get("interior", []):
        if not has_prose(d.get("evidence")):
            raise ValueError(f"[[strings.interior]] for {d.get('id')} has no evidence")
        e = by_id.get(d["id"])
        if e is None:
            continue
        if rom is not None and rom.u32(d["pointer"]) != 0x08000000 + e["offset"] + d["offset"]:
            raise ValueError(f"[[strings.interior]] {d['id']}: the word at {d['pointer']:#x} does not point "
                             f"{d['offset']} bytes into it")
        head = cm.encode(e["text"], terminate=False)[:d["offset"]]
        text = cm.decode(head + b"\xff")[0]
        if cm.encode(text, terminate=False) != head:
            raise ValueError(f"[[strings.interior]] {d['id']}: byte {d['offset']} is inside a code")
        out[d["id"]] = text
    return out


# the kinds of Limit.sources
MEASURED, PROFILE, SOURCE = "measured", "profile", "source"


def limit_source(kind: str, rule: dict, evidence: str = "", unverified: bool = False) -> dict:
    """A value of Limit.sources: kind MEASURED, PROFILE or SOURCE, the rule that sets it and its evidence."""
    source = {"kind": kind, "rule": rule, "evidence": evidence}
    if unverified:
        source["unverified"] = True
    return source


def unverified_slots(prof, section: str) -> frozenset:
    """Policy slot names of `section` marked as estimates."""
    return frozenset(prof.get("policy_unverified", {}).get(section, ()))


def window_source(rule: dict, dimension: str = "width", estimates: frozenset = frozenset()) -> dict:
    """The source of a value a [[layout.table_window]] or [[layout.record_window]] rule sets."""
    where = {"where": rule["where"]} if rule.get("where") else {}
    suffix = "_where" if where else ""
    if "texts" in rule:
        return limit_source(MEASURED, coded("record_window" + suffix, name=rule.get("name", hex(rule["table"])),
                                            **where), rule.get("evidence", ""))
    if dimension == "width" and rule.get("policy_width"):
        return limit_source(PROFILE, coded("policy_table_window" + suffix, name=rule["name"], **where),
                            rule.get("evidence", ""), rule["name"] in estimates)
    return limit_source(MEASURED, coded("table_window" + suffix, name=rule.get("name", hex(rule["base"])), **where),
                        rule.get("evidence", ""))


def binding(cands: list[tuple[int, dict]]) -> dict:
    """The source of the largest candidate value, the first of equal ones."""
    return max(cands, key=lambda c: c[0])[1]


def trainer_battle_only(entry: dict, uses: list[dict]) -> bool:
    """Require battle speech evidence for every reference."""
    if not uses or any(u.get("trainer_role") not in {"defeat", "victory"} for u in uses):
        return False
    proven = {u.get("at") for u in uses} - {None}
    refs = entry.get("refs", [])
    return bool(refs) and all(r["kind"] == "script:trainerbattle" and r["at"] in proven for r in refs)


def window_consumer(width: int, source: dict, arrow: int | None, rule: dict | None = None,
                    confirmed: bool = False) -> dict:
    """Keep a window's width and arrow together."""
    rule = rule or {}
    declared = "prompt_arrow_width" in rule
    return {"name": rule.get("where") or rule.get("name") or source["rule"], "width": width,
            "arrow": rule.get("prompt_arrow_width", arrow or 0),
            **({"newline_scroll_after": rule["newline_scroll_after"]} if "newline_scroll_after" in rule else {}),
            **({"instant_print": rule["instant_print"]} if "instant_print" in rule else {}),
            "arrow_source": "declared" if declared else "default",
            "arrow_confirmed": declared or confirmed, "source": source}


def compute_limits(prof, inv: dict, catalog, measure: Measure, cm: Charmap, ctx: dict | None = None,
                   overrides: dict[str, str] | None = None, english_slots: bool = False,
                   rom: Rom | None = None, facts: dict | None = None,
                   source: dict | None = None) -> dict[str, Limit]:
    """Limits per entry."""
    lay = prof.get("layout", {})
    from ..profile.schema import validate_prompt_arrows
    validate_prompt_arrows(lay)
    dlg_w = lay.get("dialogue_width", 208)
    bat_w = lay.get("battle_width", 208)
    boxes = {k: int(v) for k, v in lay.get("box_width", {}).items()}
    label_min = lay.get("label_min_width", 0)
    target = lay.get("target_width")
    arrow = lay.get("prompt_arrow_width")   # drawn after a line that waits
    icon_spacing = {k: int(v) for k, v in lay.get("icon_letter_spacing", {}).items()}   # per box kind
    windows = {k: int(v) for k, v in lay.get("window_width", {}).items()}
    window_lines = {k: int(v) for k, v in lay.get("window_lines", {}).items()}
    window_style = dict(lay.get("window_style", {}))   # font of the window
    # text pointer position -> width of its measured window
    table_window_at: dict[int, int] = {}
    table_lines_at: dict[int, int] = {}   # window lines per position
    rule_at: dict[int, dict] = {}   # binding width rule
    lines_rule_at: dict[int, dict] = {}
    # snapshot facts replace ROM reads
    resolved_windows = window_rules(lay, rom) if facts is None else facts["windows"]
    consumers_at = defaultdict(list)
    for at, w, n, rule in resolved_windows:
        consumers_at[at].append((w, rule))
        if w < table_window_at.get(at, w + 1):
            table_window_at[at], rule_at[at] = w, rule
        if n is not None and int(n) < table_lines_at.get(at, int(n) + 1):
            table_lines_at[at], lines_rule_at[at] = int(n), rule
    table_chars_at = window_chars(lay)
    table_box_at = window_boxes(lay)
    table_spacing_at = window_icon_spacings(lay)
    chars_rule_at: dict[int, dict] = {}   # binding chars rule
    box_rule_at: dict[int, dict] = {}
    for tw in lay.get("table_window", []):
        for at in table_window_positions(tw):
            if "chars" in tw and int(tw["chars"]) == table_chars_at.get(at):
                chars_rule_at.setdefault(at, tw)
            if "box" in tw:
                box_rule_at.setdefault(at, tw)
    window_box = dict(lay.get("window_box", {}))   # per id
    for k, v in window_box.items():
        if v not in ("dialogue", "battle"):
            raise ValueError(f"[layout.window_box] {k}: {v!r} is not dialogue or battle")
    table_centre_at = window_centres(lay)
    table_strip_at = window_strips(lay)

    rows = facts_rows(catalog, source)   # without the English, the snapshot's limits
    english = {sid: source_text(sid, e, rows) for sid, (p, e) in catalog.items()}

    def shown(sid: str) -> str | None:
        """The text a check of `sid` sees; None for an English the catalog does not hold."""
        e = catalog[sid][1] if sid in catalog else None
        if english_slots or e is None:
            return e.msgid if e is not None else ""
        return (overrides or {}).get(sid) or e.msgstr or (None if e.keyed else e.msgid)

    def chars(text: str) -> int:
        try:
            return len(cm.encode(text, terminate=False))
        except CharmapError:
            return 0

    def shown_chars(sid: str) -> int:
        text = shown(sid)
        return rows[sid][5]["chars"] if text is None else chars(text)
    number_at = record_numbers(lay, rom) if facts is None else facts["numbers"]
    fills = code_fills(lay)
    originals = fill_originals(prof, catalog, rom, None if facts is None else facts.get("code_originals", {}), inv)
    from .fillsources import Evaluator, graph
    names = {token.strip("{}") for text in english.values() for token in BRACE.findall(text)}
    selected = {source for token, source in lay.get("slot_source", {}).items() if token in names}
    for row in inv["entries"]:
        sid = row["id"]
        if sid not in english:
            continue
        rules = fills[0].get(sid, []) + [rule for ref in row["refs"] for rule in fills[1].get(ref["at"], [])]
        selected.update(source for rule in rules
                        for token, source in lay.get("code_slot_source", {}).get(rule.get("name"), {}).items()
                        if "{" + token + "}" in english[sid])
    source_graph = graph(prof, inv, catalog, selected=selected, alphabet=(facts or {}).get("fill_alphabet"))
    evaluator = Evaluator(source_graph, catalog, measure, cm,
                          (facts or {}).get("fill_fragments", {}), overrides)
    global_slots = {}
    for token, source_name in lay.get("slot_source", {}).items():
        if token not in names:
            continue
        result = evaluator.result(source_name, measure.ph.get(token),
                                  lay.get("slot_override", {}).get(token))
        global_slots[token] = SlotWidth(result["width"], result["source_width"],
                                       coded("slot_label", label=result["label"]), result["state"], result)
    fill_sources = source_fills(prof, catalog, measure, cm, originals, overrides, inv, rows, evaluator,
                                (facts or {}).get("fill_fragments"))
    key_rows = keyboard_rows(prof, inv, rom) if facts is None else facts["keys"]
    pack_keys = pack_columns(prof, inv)
    digit_px = max(measure.width(d, cm) for d in "0123456789")
    table_windows = Windows(prof, inv)
    sbreak = lay.get("sentence_break")
    estimates = {k: unverified_slots(prof, k) for k in ("window_width", "table_windows", "table_window", "code_fill")}
    by_id = {e["id"]: e for e in inv["entries"]}
    heads = interior_heads(prof, inv, cm, rom) if facts is None else facts["heads"]
    tables = {t["name"]: t for t in prof.get("table", [])}
    # declared table-base fields: id -> (table, length)
    tb_fields = {f"s{off:07X}": (tb, n) for tb in prof.get("strings", {}).get("table_base", [])
                 for off, n in table_base_lengths(tb).items()}
    # group calibration from the original text
    shape: dict[Path, dict] = defaultdict(lambda: {"width": 0, "lines": 1, "multi": False})
    for sid, (p, e) in catalog.items():
        s = shape[p]
        ls = [l for par in paragraphs(english[sid]) for l in lines_of(par)]
        s["width"] = max(s["width"], english_width(english[sid], measure, cm))
        s["lines"] = max(s["lines"], len(ls))
        s["multi"] = s["multi"] or len(ls) > 1
    # pointer-table runs: position -> run start, widest original per run
    from ..catalog.catalog import pointer_runs
    run_of = pointer_runs(inv)
    run_width: dict[int, int] = {}
    for sid, (p, e) in catalog.items():
        inv_e = by_id.get(sid)
        if inv_e is None:
            continue
        fw = {k: v.source_width for k, v in fill_slots(sid, english[sid], inv_e, fills, digit_px, fill_sources).items()}
        w = english_width(english[sid], measure.with_placeholders(fw), cm)   # code fills measured
        for r in inv_e["refs"]:
            k = run_of.get(r["at"])
            if k is not None:
                run_width[k] = max(run_width.get(k, 0), w)
    slots = slot_widths(prof, inv, ctx, catalog, measure, cm, overrides, rows, global_slots, (facts or {}).get("fill_fragments"))
    limits: dict[str, Limit] = {}
    for sid, (p, e) in catalog.items():
        inv_e = by_id.get(sid)
        if inv_e is None:
            continue
        en, row = english[sid], rows.get(sid)
        sw = {**slots.get(sid, {}), **number_slots(en, inv_e, number_at, digit_px),
              **fill_slots(sid, en, inv_e, fills, digit_px, fill_sources, estimates["code_fill"])}
        slot_context = lay.get("window_slot_context", {}).get(sid)
        if slot_context:
            for token, slot in list(sw.items()):
                detail = slot.details or {}
                source_name = detail.get("source")
                if source_name in source_graph["sources"]:
                    result = evaluator.result(source_name, detail.get("recorded"),
                                              lay.get("slot_override", {}).get(token), slot_context)
                    sw[token] = SlotWidth(result["width"], result["source_width"],
                                          coded("slot_label", label=result["label"]), result["state"], result)
        en_measure = measure.with_placeholders({k: v.source_width for k, v in sw.items()})
        # never stricter than the original shape of this string
        own = english_width(en, en_measure, cm)
        own_box = max(len(lines_of(par)) for par in paragraphs(en))
        own_total = sum(len(lines_of(par)) for par in paragraphs(en))
        if row is not None:   # the English box lines the snapshot measured
            own_box, own_total = max(row[2], default=1), sum(row[2])
        kinds = {r["kind"] for r in inv_e["refs"]}
        # the sources of the width and lines below
        en_w = limit_source(SOURCE, coded("english_widest"))
        en_file = limit_source(SOURCE, coded("english_file_widest", file=catalog_name(p)))
        en_one = limit_source(SOURCE, coded("english_one_line"))
        source_lines = limit_source(SOURCE, coded("english_line_count"))
        box_two = limit_source(MEASURED, coded("box_lines"))
        box_lines = limit_source(SOURCE, coded("english_box_lines"))
        prof_w = {k: limit_source(PROFILE, coded("layout_default", key=k)) for k in ("dialogue_width", "battle_width",
                                                                                     "label_min_width")}
        so: dict[str, dict] = {}
        if inv_e["kind"] == "fixed":
            t = tables[inv_e["table"]]
            extended = long_name_fields(t).get(inv_e["index"])
            capacity = extended["length"] if extended else t["field_length"]
            measured = table_windows.width(sid, table_windows.main)
            lim = Limit("field", max(own, shape[p]["width"] if measured is None else measured), 1,
                        chars=capacity - (1 if t.get("terminated", True) else 0))
            if measured is None:
                so["width"] = binding([(own, en_w), (shape[p]["width"], en_file)])
            else:
                tw = min(table_windows.rules(sid, table_windows.main),
                         key=lambda w: w.get("entries", {}).get(sid, w["width"]))
                kind = PROFILE if tw.get("policy_width") else MEASURED
                guess = kind == PROFILE and t["name"] in estimates["table_windows"]
                so["width"] = binding([(measured, limit_source(kind, coded("table_field_window", table=t["name"],
                                                                           where=tw["where"]),
                                                               tw.get("evidence", ""), guess)),
                                       (own, en_w)])
            so["lines"] = limit_source(MEASURED, coded("table_one_line", table=t["name"]))
            field_rule = ("table_field" + ("_byte" if t["field_length"] <= 1 else "")
                          + ("" if t.get("terminated", True) else "_unterminated"))
            so["chars"] = limit_source(MEASURED, coded(field_rule, table=t["name"], bytes=t["field_length"]),
                                       t.get("evidence", ""))
            if extended:
                so["chars"] = limit_source(MEASURED, coded("table_long_name", table=t["name"], bytes=capacity),
                                           extended["evidence"])
        # verified trainer speeches count as battle text
        elif inv_e["category"] == "battle-script" or trainer_battle_only(
                inv_e, (ctx or {}).get("strings", {}).get(sid, {}).get("uses", [])):
            lim = Limit("battle", max(bat_w, own), max(2, own_box))
            so["width"] = binding([(bat_w, prof_w["battle_width"]), (own, en_w)])
            so["lines"] = binding([(2, box_two), (own_box, box_lines)])
        elif kinds & DIALOGUE_REFS and window_like(en):
            # Unscrolled text over two lines is a fixed window:
            # full-screen text, sign, letter; keep own width and lines
            lim = Limit("multiline", own, own_total)
            so["width"], so["lines"] = en_w, source_lines
        elif kinds & DIALOGUE_REFS:
            lim = Limit("dialogue", max(dlg_w, own), max(2, own_box))
            so["width"] = binding([(dlg_w, prof_w["dialogue_width"]), (own, en_w)])
            so["lines"] = binding([(2, box_two), (own_box, box_lines)])
        elif inv_e["category"] == "script":
            # Buffered words and labels enter other text:
            # allow up to the STR_VAR width (label_min_width).
            if own_total > 1:
                lim = Limit("multiline", max(own, dlg_w), own_total)
                so["width"] = binding([(dlg_w, prof_w["dialogue_width"]), (own, en_w)])
                so["lines"] = source_lines
            else:
                lim = Limit("single", max(own, label_min), 1)
                so["width"] = binding([(label_min, prof_w["label_min_width"]), (own, en_w)])
                so["lines"] = en_one
        elif own_total == 1:
            # A single-line original stays single-line.
            # Its window is shared across the pointer-table run;
            # the widest original sets the limit.
            run_w = max((run_width.get(run_of.get(r["at"]), 0) for r in inv_e["refs"]), default=0)
            lim = Limit("single", max(own, run_w, label_min if inv_e["category"] == "script" else 0), 1)
            so["width"] = binding([(label_min if inv_e["category"] == "script" else 0, prof_w["label_min_width"]),
                                   (own, en_w),
                                   (run_w, limit_source(SOURCE, coded("english_run_widest")))])
            so["lines"] = en_one
        elif shape[p]["multi"]:
            lim = Limit("multiline", max(own, shape[p]["width"]), max(own_total, shape[p]["lines"]))
            so["width"] = binding([(own, en_w), (shape[p]["width"], en_file)])
            so["lines"] = binding([(own_total, source_lines), (shape[p]["lines"], limit_source(
                SOURCE, coded("english_file_lines", file=catalog_name(p))))])
        else:
            lim = Limit("single", max(own, shape[p]["width"], label_min), 1)
            so["width"] = binding([(label_min, prof_w["label_min_width"]), (own, en_w), (shape[p]["width"], en_file)])
            so["lines"] = en_one
        if row is not None:   # snapshot limit: later steps recompute it
            lim = row_limit(row)
            so = {k: limit_source(*v) for k, v in row[5]["sources"].items()} if len(row) > 5 else {}
        # English or least width never exceeds the message box:
        # estimated slot widths can overstate English;
        # a line passing such a limit prints past the box
        cap = box_cap(boxes, lim.kind, (ctx or {}).get("strings", {}).get(sid, {}).get("uses", []))
        if cap is not None and lim.width > cap:
            lim.width = cap
        if cap is not None and lim.width >= cap:   # the box binds
            name = box_binding(boxes, lim.kind, (ctx or {}).get("strings", {}).get(sid, {}).get("uses", []))[0]
            so["width"] = limit_source(MEASURED, coded("box_width", box=name))
        # Measured in game per table, whatever the string's group.
        # the narrowest window binds a string shared by tables
        table_w = [table_window_at[r["at"]] for r in inv_e["refs"] if r["at"] in table_window_at]
        if table_w:
            lim.width = min(table_w)
            so["width"] = window_source(rule_at[min((r["at"] for r in inv_e["refs"] if r["at"] in table_window_at),
                                                    key=table_window_at.get)],
                                        estimates=estimates["table_window"])
        # a text of a message box, two lines per box
        box_k = [table_box_at[r["at"]] for r in inv_e["refs"] if r["at"] in table_box_at]
        if sid in window_box:
            box_k.append(window_box[sid])
        if box_k:
            k = min(box_k, key=lambda b: boxes.get(b, dlg_w if b == "dialogue" else bat_w))
            lim.kind, lim.lines = k, max(2, own_box)
            if not table_w:
                lim.width = boxes.get(k, dlg_w if k == "dialogue" else bat_w)
            # the box and the rule that names it
            via = [coded("table_window", name=box_rule_at[r["at"]].get("name")) for r in inv_e["refs"]
                   if table_box_at.get(r["at"]) == k] + ([coded("window_box", entry=sid)]
                                                         if window_box.get(sid) == k else [])
            so["lines"] = binding([(2, limit_source(MEASURED, coded("box_lines_via", via=via[0]))),
                                   (own_box, box_lines)])
            if not table_w:
                so["width"] = (limit_source(MEASURED, coded("box_width_via", box=k, via=via[0])) if k in boxes
                               else prof_w["dialogue_width" if k == "dialogue" else "battle_width"])
        # centred by character count against a reference
        cen = [table_centre_at[r["at"]] for r in inv_e["refs"] if r["at"] in table_centre_at]
        if cen:
            ref, step = cen[0]
            shift = step * (shown_chars(ref) - shown_chars(sid))
            lim.width = max(0, lim.width - shift) if shift >= 0 else 0
            if "width" in so:
                so["width"] = {**so["width"], "rule": coded("centred", rule=so["width"]["rule"], reference=ref)}
        # a fixed window of so many lines
        table_l = [table_lines_at[r["at"]] for r in inv_e["refs"] if r["at"] in table_lines_at]
        if table_l:
            lim.kind, lim.lines = "multiline", min(table_l)
            so["lines"] = window_source(lines_rule_at[min((r["at"] for r in inv_e["refs"] if r["at"] in table_lines_at),
                                                          key=table_lines_at.get)], "lines")
        # characters the printing code copies
        table_c = [table_chars_at[r["at"]] for r in inv_e["refs"] if r["at"] in table_chars_at]
        if table_c:
            # the binding rule, unless the field binds
            if lim.chars is None or min(table_c) < lim.chars:
                so["chars"] = window_source(chars_rule_at[min((r["at"] for r in inv_e["refs"]
                                                               if r["at"] in table_chars_at), key=table_chars_at.get)], "chars")
            lim.chars = min(table_c + ([] if lim.chars is None else [lim.chars]))
        # prefixes every measured window strips
        strips = {table_strip_at.get(r["at"], ()) for r in inv_e["refs"] if r["at"] in table_window_at}
        if len(strips) == 1 and sid not in windows:
            lim.strip = [shown(ref) for ref, n in strips.pop()
                         if shown(ref) is not None and chars(shown(ref)) == n] or None
        if sid in windows:
            lim.width = windows[sid]  # measured in game
            so["width"] = limit_source(MEASURED, coded("window_width", entry=sid))
            if sid in prof.get("policy_slots", {}).get("window_width", []):
                so["width"] = limit_source(PROFILE, coded("policy_window_width", entry=sid),
                                           unverified=sid in estimates["window_width"])
        if sid in window_lines:
            lim.lines = window_lines[sid]  # lines of a fixed window, measured in game
            so["lines"] = limit_source(MEASURED, coded("window_lines", entry=sid))
        if sid in window_style:
            lim.style = window_style[sid]   # measured in that font
        consumer_windows = table_windows.rules(sid)
        strict = [w for w in consumer_windows if w.get("strict") and w["style"] == table_windows.main]
        if strict:
            bound = min(strict, key=lambda w: w.get("entries", {}).get(sid, w["width"]))
            width = bound.get("entries", {}).get(sid, bound["width"])
            lim.width = min([width] + table_w + ([windows[sid]] if sid in windows else []))
            lim.lines = 1
            if lim.width == width:
                so["width"] = limit_source(MEASURED, coded("table_field_window", table=bound["table"],
                                                           where=bound["where"]), bound.get("evidence", ""))
        capacities = [w for w in consumer_windows if "chars" in w]
        if capacities:
            bound = min(capacities, key=lambda w: w["chars"])
            if lim.chars is None or bound["chars"] < lim.chars:
                lim.chars = bound["chars"]
                so["chars"] = limit_source(MEASURED, coded("table_buffer", table=bound["table"], where=bound["where"]),
                                           bound.get("evidence", ""))
        if sid in key_rows:   # the key grid binds
            lim.keys = key_rows[sid]
            so["keys"] = limit_source(MEASURED, coded("keyboard_columns"))
            if pack_keys.get(sid, lim.keys) < lim.keys:
                lim.keys = pack_keys[sid]
                so["keys"] = limit_source(MEASURED, coded("pack_keyboard_columns"))
        if lim.kind in ("dialogue", "battle"):
            lim.target, lim.sentence_break = target, sbreak
        consumers = []
        for ref in inv_e["refs"]:
            for width, rule in consumers_at.get(ref["at"], []):
                source = window_source(rule, estimates=estimates["table_window"])
                if "centre" in rule:
                    centre = rule["centre"]
                    shift = centre["step"] * (shown_chars(centre["reference"]) - shown_chars(sid))
                    width = max(0, width - shift) if shift >= 0 else 0
                    source = {**source, "rule": coded("centred", rule=source["rule"], reference=centre["reference"])}
                consumer = window_consumer(width, source, arrow, rule, rule.get("box") in ("dialogue", "battle"))
                append_unique(consumers, consumer)
        if not consumers or sid in windows or strict or sid in lay.get("window_binding", {}):
            rule = dict(lay.get("window_binding", {}).get(sid, {}))
            if sid in lay.get("window_prompt_arrow_width", {}):
                rule["prompt_arrow_width"] = lay["window_prompt_arrow_width"][sid]
            if sid in lay.get("window_newline_scroll_after", {}):
                rule["newline_scroll_after"] = lay["window_newline_scroll_after"][sid]
            if sid in lay.get("window_instant_print", {}):
                rule["instant_print"] = lay["window_instant_print"][sid]
            source = so.get("width", en_w)
            if rule.get("name"):
                if rule.get("where"):
                    basis = coded("layout_window_where", name=rule["name"], where=rule["where"])
                else:
                    basis = rule["where"] = coded("layout_window", name=rule["name"])
                if "width" in rule:
                    source = limit_source(MEASURED, basis, rule.get("evidence", ""))
            consumers.append(window_consumer(lim.width, source, arrow, rule,
                                              lim.kind in ("dialogue", "battle")))
        bound = min(consumers, key=lambda c: (c["width"], c["source"] != so.get("width")))
        lim.width, so["width"] = bound["width"], bound["source"]
        lim.consumers = consumers if lim.keys is None else None
        thresholds = [c["newline_scroll_after"] for c in consumers if "newline_scroll_after" in c]
        lim.newline_scroll_after = min(thresholds) if thresholds and lim.keys is None else None
        waiting = [c for c in consumers if c["arrow"]] if lim.keys is None else []
        lim.arrow = min(waiting, key=lambda c: c["width"] - c["arrow"])["arrow"] if waiting else None
        lim.icon_spacing = icon_spacing.get(lim.kind) or None
        spacings = [table_spacing_at[r["at"]] for r in inv_e["refs"] if r["at"] in table_spacing_at]
        if spacings:
            lim.icon_spacing, spacing_rule = max(spacings, key=lambda s: s[0])
            so["icon_spacing"] = window_source(spacing_rule, "letter_spacing")
        if "in-place-only" in inv_e["flags"]:
            lim.bytes = inv_e["length"] - 1
            # the references that keep it in place
            fixed_refs = sorted({r["kind"] for r in inv_e["refs"]} & INPLACE_ONLY_KINDS)
            so["bytes"] = limit_source(MEASURED, coded("in_place", bytes=inv_e["length"],
                                                       references=", ".join(fixed_refs)))
            if sid in tb_fields:   # the declared field binds
                tb, n = tb_fields[sid]
                lim.bytes = n - 1
                so["bytes"] = limit_source(MEASURED, coded("table_base", id=tb["id"], bytes=n),
                                           tb.get("length_evidence", ""))
        if sid in heads:   # read from inside, in place
            lim.head, lim.bytes = heads[sid], inv_e["length"] - 1
            d = next(x for x in prof.get("strings", {}).get("interior", []) if x["id"] == sid)
            so["bytes"] = limit_source(MEASURED, coded("interior", pointer=f"0x{d['pointer']:X}", offset=d["offset"],
                                                       bytes=inv_e["length"]), d.get("evidence", ""))
        lim.slot_context = slot_context
        if sw:
            lim.placeholders = {k: v.source_width if english_slots else v.translated for k, v in sw.items()}
            lim.slot_sources = {k: v.source for k, v in sw.items()}
            lim.slot_details = {k: v.details for k, v in sw.items() if v.details} or None
        # English lines per box bound the short-word rule
        lim.source_lines = list(row[2]) if row is not None else [len(lines_of(par)) for par in paragraphs(e.msgid)]
        lim.sources = so if lim.lines is not None else {k: v for k, v in so.items() if k != "lines"}
        limits[sid] = lim
    # a word shown only through bufferstring in sentences:
    # it takes the narrowest window width, not its own English
    for sid, lim in limits.items():
        refs = by_id[sid]["refs"]
        if ctx is None or lim.kind != "single" or sid in windows or not refs or any(
                r["kind"] != "script:bufferstring" for r in refs):
            continue
        hosts = [t for t, _ in ctx.get("strings", {}).get(sid, {}).get("fills", []) if t in limits and t != sid]
        if hosts:
            lim.host = min(hosts, key=lambda t: limits[t].width)
            lim.width = limits[lim.host].width
            confirmed = any(c["arrow_source"] == "declared" and c["arrow_confirmed"]
                            for c in lim.consumers or [])
            lim.consumers = [window_consumer(lim.width, limits[lim.host].sources["width"], arrow,
                                             confirmed=confirmed)]
            # the host's width source
            h = (limits[lim.host].sources or {}).get("width") or limit_source(PROFILE, coded("host_default"))
            lim.sources = {**(lim.sources or {}), "width": {**h, "rule": coded("host", host=lim.host, rule=h["rule"])}}
    return limits


@language.profile_selected
def run(prof, inv: dict, po_root: Path, cm: Charmap, measure: Measure, glyph_slots: set[int],
        source_as_translation: bool = False, glossary: Path | None = None,
        windows: Windows | None = None, version: str | None = None, ctx: dict | None = None,
        rom: Rom | None = None, facts: dict | None = None, pokedex: dict | None = None,
        source: dict | None = None, catalog=None, limits=None) -> Gate:
    """Run the QA checks over a catalog and return the findings."""
    catalog = load_catalog(po_root, prof, english=source is None) if catalog is None else catalog
    rows = facts_rows(catalog, source)
    if rows and source_as_translation:
        raise ValueError("the English as translation needs the English source catalog; run gen3tl catalog extract")
    matcher = None
    if glossary is not None and glossary.exists() and not source_as_translation:
        from ..lang import termbase
        matcher = termbase.TermMatcher(termbase.load(glossary))
    limits = compute_limits(prof, inv, catalog, measure, cm, ctx, english_slots=source_as_translation, rom=rom,
                            facts=facts, source=source) if limits is None else limits
    source_windows = Windows(prof, inv) if source_as_translation else None
    rules = prof.get("qa", {})
    forbidden = rules.get("forbidden", {})
    optional = set(rules.get("optional_tokens", []))  # glyph tokens a translation may drop
    droppable = droppable_tokens(rules)   # per entry
    addable = addable_tokens(rules)   # per entry
    gate = Gate()
    facts_typography = {}
    if rows:
        facts_typography, skipped = typography_without_english(rules)
        gate.skipped_typography = sorted(set(skipped))
    if not source_as_translation:
        from ..discover.inventory import untranslated_slot_usage
        collisions = untranslated_slot_usage(inv, glyph_slots, Charmap(), catalog)
        for sid in sorted({sid for sid, _ in collisions}):
            path = str(catalog[sid][0]) if sid in catalog else ""
            gate.add(sid, path, "error", "source glyph collision requires a translation")
    translations: dict[str, set[str]] = defaultdict(set)
    from ..catalog.source import digest
    # without the English, the same English is the same digest
    same = {sid: (rows[sid][0] if sid in rows else digest(e.msgid)) if rows else e.msgid
            for sid, (p, e) in catalog.items()}
    # pack rows may repeat English on purpose
    pack_rows = {sid for sid, (p, e) in catalog.items() if e.owner == "keyboard"}
    for sid, (p, e) in catalog.items():
        target = e.msgid if source_as_translation else expand(e.msgstr, version)
        if e.source_note:
            gate.source_notes[sid] = e.source_note
        never = never_shown_tag(e.extracted)
        if never is not None:
            gate.never_shown[sid] = never
        if not target:
            continue
        f = str(p)
        gate.checked += 1
        if never is not None:
            gate.never_checked.add(sid)
        if sid not in pack_rows:
            translations[same[sid]].add(target)
        english = source_of(sid, e, rows)
        lim = limits.get(sid)
        if lim is None:
            gate.add(sid, f, "error", "no inventory entry for this id")
            continue
        try:
            cm.encode(target, terminate=False)
        except CharmapError as exc:
            gate.add(sid, f, "error", f"not encodable: {exc}")
            continue
        differ = placeholders_differ(english, target, optional | droppable.get(sid, set()), addable.get(sid, {}))
        if differ:
            gate.add(sid, f, "error", differ)
        boxes = len(english.boxes) > 1 if isinstance(english, Source) else e.msgid.count("\\p")
        if boxes and not target.count("\\p") and lim.kind in ("dialogue", "battle"):
            gate.add(sid, f, "warning", "paragraph breaks removed")
        laid = layout(target, lim, measure, cm)
        for prob in instant_print_problems(target, laid, lim):
            gate.add(sid, f, "error", prob)
        size_probs = check(laid, lim, measure, cm) + (windows.problems(sid, target) if windows else [])
        for prob in size_probs:
            # English wider than the measured window: the original overflows
            original_buffer = (prob.startswith("field overflow")
                               and source_windows is not None
                               and any(w.get("chars") == lim.chars for w in source_windows.rules(sid)))
            over = source_as_translation and (prob.startswith("line too wide") or original_buffer)
            gate.add(sid, f, "warning" if over else "error", prob)
        for prob in wrapped_dash_lines(target, laid):
            gate.add(sid, f, "error", prob)
        for prob in arrow_overflows(laid, lim, measure, cm):   # English lines may clip it
            gate.add(sid, f, "warning" if source_as_translation else "error", prob)
        for prob in slot_rewraps(target, lim, measure, cm):
            gate.add(sid, f, "warning", prob)
        dropped = [] if size_probs else dropped_breaks(target, lim, measure, cm)
        for prob in dropped:   # only where the re-wrap fits
            gate.add(sid, f, "warning", prob)
        for prob in short_line_ends(target) if lim.kind not in ("single", "field") and not dropped else []:
            gate.add(sid, f, "warning", prob)   # the written break stays
        for prob in [] if size_probs else split_names(laid, lim, measure):
            gate.add(sid, f, "warning", prob)   # the breaks the game shows
        if source_as_translation or e.owner == "keyboard":
            text_rules, text_english = {}, None
        elif isinstance(english, Source):
            text_rules, text_english = facts_typography, None   # only rules without the English
        else:
            text_rules, text_english = forbidden, e.msgid
        for why in typography_problems(text_english, target, text_rules, lim.keys is not None):
            gate.add(sid, f, "error", f"typography: {why}")
        if matcher is not None and not isinstance(english, Source):
            from ..lang.fill import entry_matcher
            for t in entry_matcher(e, matcher).missing(e.msgid, target):
                gate.add(sid, f, "warning", f"glossary: {t.source_text!r} expected as {t.translation!r} ({t.id})")
        plain = re.sub(r"\\[nlp]", " ", re.sub(BRACE, " ", target))
        mixed = language.current().data.get("mixed_scripts", {})
        if mixed.get("pattern") and re.search(mixed["pattern"], plain) and not source_as_translation:
            gate.add(sid, f, "error", mixed["message"])
    from . import languagerules, namepairs
    available_glyphs = set(Charmap().decode_table) | glyph_slots
    for problem in languagerules.catalog_problems(prof, catalog, cm,
                                                 source_as_translation=source_as_translation,
                                                 glyphs=available_glyphs):
        path = str(catalog[problem.entry][0]) if problem.entry in catalog else ""
        gate.add(problem.entry, path, problem.severity, f"engine language rule: {problem.message}")
    shown = {sid: (e.msgid if source_as_translation else expand(e.msgstr, version)) or e.msgid
             for sid, (p, e) in catalog.items() if e.msgstr or not e.keyed}
    for res in namepairs.results(prof, shown, windows.measures if windows else {}, pokedex):
        for pair in res.over():
            path = str(catalog[pair.species][0]) if pair.species in catalog else ""
            gate.add(pair.species, path, "warning" if source_as_translation else "error",
                     namepairs.message(res, pair))
    for src, ts in translations.items():
        if len(ts) > 1 and not source_as_translation:
            ids = [sid for sid in catalog if same[sid] == src and sid not in pack_rows][:6]
            shown_src = next((e.msgid for sid, (p, e) in catalog.items() if same[sid] == src and not e.keyed),
                             None) if rows else src
            gate.add(",".join(ids), "", "warning", f"same English, {len(ts)} different translations: "
                     + (f"{shown_src[:50]!r}" if shown_src is not None else f"digest {src[:12]}"))
    return gate


def report(gate: Gate) -> str:
    by_msg = Counter((f.severity, re.sub(r"\d+", "N", f.message.split(":")[0])) for f in gate.findings)
    lines = ["# QA gate report", "", f"Entries checked: {gate.checked}; errors: {gate.errors}; "
             f"warnings: {sum(1 for f in gate.findings if f.severity == 'warning')}; "
             f"never shown: {len(gate.never_shown)} tagged, {len(gate.never_checked)} of them checked", ""]
    if gate.skipped_typography:
        lines += ["## Skipped without the English", "",
                  "Typography rules that need the English:", ""]
        lines += [f"- {reason}" for reason in gate.skipped_typography]
        lines.append("")
    lines += ["| Severity | Kind | Count |", "|---|---|---|"]
    lines += [f"| {s} | {k} | {n} |" for (s, k), n in by_msg.most_common()]
    lines += never_shown_section(gate)
    lines += source_notes_section(gate)
    lines += ["", "## Findings", ""]
    for f in gate.findings[:2000]:
        mark = " [never shown]" if f.id in gate.never_shown else ""
        lines.append(f"- {f.severity} {f.id} ({f.file}){mark}: {f.message}")
    return "\n".join(lines) + "\n"


def source_notes_section(gate: Gate) -> list[str]:
    """List catalog entries by their stated wording sources."""
    if not gate.source_notes:
        return []
    groups: dict[str, set[str]] = defaultdict(set)
    for sid, note in gate.source_notes.items():
        for source in note.split("\n"):
            if source.strip():
                groups[source].add(sid)
    if not groups:
        return []
    lines = ["", "## Wording sources", "", "Catalog entries, including untranslated entries, grouped by source note.", "",
             "| Source | Entries | IDs |", "|---|---|---|"]
    for source, ids in sorted(groups.items()):
        lines.append(f"| {cell(source)} | {len(ids)} | {', '.join(sorted(ids))} |")
    return lines


def never_shown_section(gate: Gate) -> list[str]:
    """The report's never-shown entries per evidence."""
    if not gate.never_shown:
        return []
    by_ev: dict[str, list[str]] = defaultdict(list)
    for sid, ev in gate.never_shown.items():
        by_ev[ev].append(sid)
    lines = ["", "## Never shown", "",
             "Tag: reach: never shown (build/reach.json unreachable, [[reach.never_shown]] reviews).", "",
             "Checked like other entries; findings carry the marker [never shown].", "",
             "| Evidence | Entries | Checked | First |", "|---|---|---|---|"]
    for ev, ids in sorted(by_ev.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"| {cell(ev)} | {len(ids)} | {sum(1 for i in ids if i in gate.never_checked)} | "
                     f"{min(ids)} |")
    return lines


def cell(text: str) -> str:
    return text.replace("|", "\\|")


def inserted_width_report(prof, inv: dict, catalog, ctx: dict, measure: Measure, cm: Charmap,
                          version: str | None = None, rom: Rom | None = None, current=None) -> tuple[str, int]:
    """Markdown report of the placeholder widths per slot and of the entries with text that overflow with them."""
    old = compute_limits(prof, inv, catalog, measure, cm, rom=rom)
    new = compute_limits(prof, inv, catalog, measure, cm, ctx, rom=rom) if current is None else current
    slots = slot_widths(prof, inv, ctx, catalog, measure, cm)
    overflows, rewrapped, hosted = [], [], []
    for sid, (p, e) in catalog.items():
        if sid not in new or not (e.msgstr or "empty" in e.flags):
            continue
        target = expand(e.msgstr, version)
        lim, lim0 = new[sid], old[sid]
        if lim.host:
            hosted.append((sid, str(p), lim.host, lim0.width, lim.width, measure.width(target, cm)))
        if not lim.placeholders:
            continue
        m, m0 = entry_measure(measure, lim), entry_measure(measure, lim0)
        laid = layout(target, lim, measure, cm)
        for box, par in enumerate(paragraphs(laid), 1):
            for line in lines_of(par):
                w = line_width(line, m, cm)
                if not fits(line, lim.width, m, cm):
                    overflows.append((sid, str(p), str(box), line, slot_note(line, lim)[6:], f"{w} > {lim.width}"))
        before = check(layout(target, lim0, measure, cm), lim0, measure, cm)
        for prob in check(laid, lim, measure, cm):
            if not prob.startswith("line too wide") and prob not in before:
                overflows.append((sid, str(p), "-", prob, "", ""))
        if lim.kind not in ("dialogue", "battle"):
            continue
        en_pars = paragraphs(e.msgid)
        for box, par in enumerate(paragraphs(target), 1):
            ls = lines_of(par)
            if not all(fits(l, lim0.width, m0, cm) for l in ls):
                continue   # re-wrapped with the profile widths already
            for line in ls:
                w = line_width(line, m, cm)
                if not fits(line, lim.width, m, cm):
                    after = len(lines_of(layout(par, lim, measure, cm)))
                    en = len(lines_of(en_pars[box - 1])) if box <= len(en_pars) else 0
                    rewrapped.append((sid, str(p), box, line, slot_note(line, lim)[6:], f"{w} > {lim.width}",
                                      lim0.width, f"{after} / {en}"))
    known = Counter(v.known for sw in slots.values() for v in sw.values())
    entries = sorted({x[0] for x in overflows})
    lines = ["# Inserted word widths", "",
             "Scope: placeholder widths per entry and slot, translated and approved entries they affect.", "",
             f"Slots: {sum(known.values())} in {len(slots)} entries; width from the scripts: {known['script']}; "
             f"partly known (the widest known value, at least the profile width): {known['partly']}; "
             f"profile width (no value known): {known['profile']}.", "",
             f"Entries that overflow: {len(entries)} ({len(overflows)} lines); paragraphs the build "
             f"re-wraps: {len(rewrapped)}; inserted words limited by their host sentence: {len(hosted)}.", "",
             "## Entries that overflow", "",
             "Lines wider than the window after the layout, with the widest insertion of each slot of the line, "
             "and other problems the widths of the insertions add.", "",
             "| Entry | File | Box | Line | Widest insertion | px |", "|---|---|---|---|---|---|"]
    lines += [f"| {sid} | {f} | {box} | {cell(line)} | {cell(ins)} | {px} |"
              for sid, f, box, line, ins, px in overflows]
    lines += ["", "## Paragraphs the build re-wraps", "",
              "Fit with profile widths, overflow with insertion widths: the build re-wraps the paragraph.", "",
              "A line without insertion: the limit shrank to the English measured with real insertions.", "",
              "Lines column: after the re-wrap / of the English box.", "",
              "| Entry | File | Box | Line | Widest insertion | px | Limit before | Lines |",
              "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {sid} | {f} | {box} | {cell(line)} | {cell(ins)} | {px} | {w0} | {n} |"
              for sid, f, box, line, ins, px, w0, n in rewrapped]
    lines += ["", "## Inserted words limited by their host sentence", "",
              "Words shown only through bufferstring inside other sentences: the line width of the narrowest "
              "host window replaces the width taken from the word's English.", "",
              "| Entry | File | Host | Limit before | Limit | Translation px |", "|---|---|---|---|---|---|"]
    lines += [f"| {sid} | {f} | {host} | {w0} | {w1} | {px} |" for sid, f, host, w0, w1, px in hosted]
    lines += ["", "## Slot widths", "", "| Entry | Slot | Translation px | Source px | Source |", "|---|---|---|---|---|"]
    for sid in sorted(slots):
        for name, v in sorted(slots[sid].items()):
            lines.append(f"| {sid} | {{{name}}} | {v.translated} | {v.source_width} | {cell(formatted(v.source))} |")
    return "\n".join(lines) + "\n", len(entries)


def unmeasured_report(catalog, limits: dict[str, Limit]) -> tuple[str, int]:
    """Markdown report of the entries whose width limit is the English width, grouped by catalog file."""
    groups: dict[str, list] = defaultdict(list)
    never = 0
    for sid, (p, e) in catalog.items():
        lim = limits.get(sid)
        src = ((lim.sources or {}).get("width") if lim is not None else None) or {}
        if src.get("kind") != SOURCE or lim.keys is not None:   # keys bind, not the width
            continue
        if never_shown_tag(e.extracted) is not None:
            never += 1
            continue
        groups[catalog_name(p)].append((sid, lim, src, e))
    n = sum(len(v) for v in groups.values())
    order = sorted(groups, key=lambda g: (-len(groups[g]), g))
    out = ["# Entries measured against the English width", "",
           f"Entries: {n}; catalog files: {len(groups)}; width limit from the English text, no window "
           "measured in game (gen3tl check unmeasured); measure the window before shortening a word. "
           f"{never} more entries tagged never shown are left out.", "",
           "| catalog file | entries | translated |", "|---|---|---|"]
    for g in order:
        done = sum(1 for _, _, _, e in groups[g] if e.msgstr or "empty" in e.flags)
        out.append(f"| {g} | {len(groups[g])} | {done} |")
    for g in order:
        out += ["", f"## {g} ({len(groups[g])})", "", "| id | kind | width | lines | source | English |",
                "|---|---|---|---|---|---|"]
        for sid, lim, src, e in groups[g]:
            en = e.msgid if len(e.msgid) <= 60 else e.msgid[:57] + "..."
            out.append(f"| {sid} | {lim.kind} | {lim.width} | {lim.lines if lim.lines is not None else ''} | "
                       f"{cell(formatted(src['rule']))} | {cell(en)} |")
    return "\n".join(out) + "\n", n
