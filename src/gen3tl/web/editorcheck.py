"""The editor's layout check, run in the browser with Pyodide."""

from __future__ import annotations

from ..lang import language

import hashlib
import json
from dataclasses import fields
from pathlib import Path

from ..core.charmap import Charmap, CharmapError
from ..check.entrycheck import (TEXT_CHECKS, Source, addable_tokens, droppable_tokens, entry_problems, pair_width,
                                 window_widths)
from ..build.font import decode_glyph, glyph_size, shadow_end
from ..check.layout import (Limit, Measure, SpriteMeasure, entry_measure, key_count, line_width,
                            lines_of, paragraphs, shadow_overflow, waiting_windows)
from ..check.provenance import code as coded, encoding_problem, is_warning
from ..build.release import expand
from ..lang.termbase import Term, TermMatcher

# the modules the page imports, this one included;
# font and rom decode the ROM glyph cells;
# paths under the package, then the package files
BUNDLE = ("core/terminal.py", "lang/language.py", "__init__.py", "core/charmap.py", "check/layout.py", "check/fillsources.py", "build/release.py", "core/ups.py",
          "lang/termbase.py", "core/rom.py", "core/lists.py", "core/font.py", "build/font.py", "check/entrycheck.py", "web/editorcheck.py",
          "profile/profile.py", "check/render.py", "core/graphics.py", "core/notes.py", "build/graphics.py", "web/editorgfx.py", "build/graphiclabel.py",
          "lang/renamecheck.py", "lang/termedit.py", "lang/research.py", "lang/cite.py", "project/__init__.py", "project/manifest.py",
          "project/scope.py", "project/registry.py", "check/provenance.py", "check/policy.py", "build/textprefix.py",
          "build/codereplace.py", "catalog/po.py", "catalog/source.py", "catalog/entrynotes.py", "catalog/keys.py",
          "catalog/guidance.py", "discover/maps.py", "build/keyboard.py", "verify/savefile.py",
          "profile/schema.py", "profile/tsv.py", "profile/languagerules.py", "check/languagerules.py",
          "web/editorrules.py", "web/policyrules.py", "families/knowledge.py",
          "build/__init__.py", "catalog/__init__.py", "check/__init__.py", "core/__init__.py", "lang/__init__.py",
          "profile/__init__.py", "families/__init__.py", "verify/__init__.py", "web/__init__.py", "discover/__init__.py")
RULES = ("optional_tokens", "forbidden", "droppable", "addable")   # the [qa] keys check batch reads
STAMP = (1980, 1, 1, 0, 0, 0)   # zip entry time: reproducible bundle
LIMIT_FIELDS = {f.name for f in fields(Limit)}


def limit_of(w: dict) -> Limit:
    """The layout.Limit of an entry's window."""
    return Limit(**{k: v for k, v in w.items() if k in LIMIT_FIELDS})


def latin_widths(fonts: dict, rom: bytes, sha1: str | None = None) -> dict[str, bytes]:
    """The width table of each font style read from the base ROM image in the editor data; `sha1` pins the image."""
    if sha1 is not None and hashlib.sha1(rom).hexdigest() != sha1.lower():
        raise ValueError(f"ROM SHA-1 {hashlib.sha1(rom).hexdigest()} does not match the editor data ({sha1})")
    out = {}
    for style, f in fonts["styles"].items():
        at = f["rom"]["widths"]
        out[style] = bytes(rom[at:at + max(f["rom"]["count"], 256)])
    return out


def latin_cells(fonts: dict, rom: bytes) -> dict[str, bytes]:
    """The glyph table of each font style read from the base ROM image at its offset in the editor data."""
    out = {}
    for style, f in fonts["styles"].items():
        at = f["rom"]["glyphs"]
        out[style] = bytes(rom[at:at + f["rom"]["count"] * glyph_size(f["format"])])
    return out


def shadow_table(style: dict, latin, cells: bytes | None = None) -> list[bool]:
    """Per byte value whether its glyph's last column holds no ink (font.shadow_end), from the pack or the ROM cell."""
    pack = {int(b, 16) for b in style["pack"]}
    shadow = {int(b, 16) for b in style.get("shadow", [])}
    copy = {int(b, 16): int(s, 16) for b, s in style.get("copy", {}).items()}
    n = glyph_size(style["format"])
    out = []
    for b in range(max(256, style.get("rom", {}).get("count", 256), len(latin))):
        src = copy.get(b, b)
        if b in pack:
            out.append(b in shadow)
        elif cells is None or (src + 1) * n > len(cells):
            out.append(False)
        else:
            out.append(shadow_end(decode_glyph(cells[src * n:(src + 1) * n], style["format"]), latin[src]))
    return out


def glyph_table(style: dict, latin) -> list[int]:
    """Width per byte value: the pack's where it draws the byte, the ROM's otherwise."""
    pack = {int(b, 16): px for b, px in style["pack"].items()}
    copy = {int(b, 16): int(s, 16) for b, s in style.get("copy", {}).items()}
    count = max(256, style.get("rom", {}).get("count", 256), len(latin))
    return [pack[b] if b in pack else latin[copy.get(b, b)] for b in range(count)]


def term_of(tid: str, t: dict) -> Term:
    """A termbase Term from an editor data glossary term."""
    return Term(tid, t["category"], t["source_text"], t["translation"], label=t["label"], gender=t["gender"],
                forms=dict(t["forms"]), address=t["address"], notes=t.get("notes", ""), status=t["status"],
                note_ids=tuple(t.get("note_ids") or ()))


def source(entry: dict):
    """The English an entry is checked against: its text, else what the data holds of it (entrycheck.Source)."""
    english = entry.get("source_text")
    if isinstance(english, (str, Source)):   # the text, or the facts a check passed in
        return english
    return Source(tuple(entry.get("tokens", ())), tuple((entry.get("window") or {}).get("source_lines") or (1,)),
                  entry.get("status") == "nothing")


def encode_problem(text: str, cm: Charmap) -> dict | None:
    """The problem of layout.check for text the charmap cannot encode."""
    try:
        cm.encode(text, terminate=False)
    except CharmapError as e:
        return encoding_problem(e)
    return None


def _obj(x):
    return json.loads(x) if isinstance(x, str) else x


def export_rules(rules: dict) -> dict:
    """Validate evidence before exporting rules as ordinary strings."""
    droppable_tokens(rules)
    addable_tokens(rules)
    out = dict(rules)
    for kind in ("droppable", "addable"):
        if kind in out:
            out[kind] = [{**row, "evidence": str(row["evidence"])} for row in out[kind]]
    return out


class Checker:
    """check batch of single entries, from the editor data."""

    def __init__(self, doc, latin: dict, rules=None, version: str | None = None, cells: dict | None = None,
                 shadows: dict | None = None):
        doc, rules = _obj(doc), _obj(rules) or {}
        self.language = language.load(doc["language"]) if doc.get("language") else language.current()
        styles = doc["fonts"]["styles"]
        tables = {s: glyph_table(f, latin[s]) for s, f in styles.items()}
        ends = (shadows if shadows is not None else
                {s: shadow_table(f, latin[s], (cells or {}).get(s)) for s, f in styles.items()})
        ph = doc["placeholders"]
        main = tables[doc["fonts"]["main"]]
        self.cm = Charmap({c: int(b, 16) for c, b in doc["charmap"].items()})
        self.engine_rules = {row["id"]: row for row in doc.get("engine_rules", {}).get("rules", [])}
        self.rule_glyphs = {byte for byte in self.cm.decode_table if byte < len(main)}
        self.measure = Measure(main.__getitem__, dict(ph["widths"]), ph["default"],
                               styles={s: t.__getitem__ for s, t in tables.items()},
                               shadow_end=ends[doc["fonts"]["main"]].__getitem__,
                               shadow_styles={s: e.__getitem__ for s, e in ends.items()},
                               fonts=doc["fonts"].get("codes"))
        self.measures = {s: (Measure(t.__getitem__, {}, shadow_end=ends[s].__getitem__), self.cm)
                         for s, t in tables.items()}
        self.measures.update({name: (SpriteMeasure.from_data(d), self.cm)
                              for name, d in doc["fonts"].get("sprites", {}).items()})
        terms = [term_of(k, t) for k, t in doc.get("glossary", {}).items()]
        self.terms = terms   # the rename preview's glossary
        with language.use(self.language):
            self.matcher = TermMatcher(terms) if terms else None
        self.optional = set(rules.get("optional_tokens", []))
        self.droppable = droppable_tokens(rules, validated=True)
        self.addable = addable_tokens(rules, validated=True)
        self.forbidden = rules.get("forbidden", {})
        self.version = version
        from ..check.fillsources import Evaluator
        self.fill_graph = doc.get("fill_sources")
        self.fill_evaluator = (Evaluator(self.fill_graph, self.fill_graph["values"], self.measure,
                                        self.cm, self.fill_graph["fragments"]) if self.fill_graph else None)

    def substitution_window(self, entry, sources=None, drafts=None):
        """Recompute all declared slots with the current catalog state."""
        from ..check.fillsources import Evaluator, merge_results
        original = entry.get("window")
        if original is None:
            return None
        window = dict(original)
        details = dict(window.get("slot_details") or {})
        widths = dict(window.get("placeholders") or {})
        evaluator = self.fill_evaluator
        if evaluator and (sources is not None or drafts is not None):
            values = {**self.fill_graph["values"], **(_obj(sources) or {})}
            states = {sid: {"text": state.get("translation", state.get("text", "")),
                            "status": state.get("state", state.get("status", "text"))}
                      for sid, state in (_obj(drafts) or {}).items()}
            evaluator = Evaluator(self.fill_graph, values, self.measure, self.cm,
                                  self.fill_graph["fragments"], states)
        for token in entry.get("tokens", []):
            name = token.strip("{}")
            if not name.startswith("F8 ") and (name in self.measure.ph or name in widths):
                details.setdefault(name, {"width": widths.get(name, self.measure.ph.get(name)),
                                           "state": "recorded", "contributors": [], "dependencies": []})
        def resolved(detail):
            if detail.get("choices"):
                return merge_results([resolved(choice) for choice in detail["choices"]], detail.get("floor", 0))
            source = detail.get("source")
            if not source or evaluator is None:
                return detail
            override = ({"source": source, "width": detail["width"], "reason": detail["reason"]}
                        if detail.get("previous_state", detail["state"]) == "override" else None)
            return evaluator.result(source, detail.get("recorded"), override, window.get("slot_context"))

        for name, detail in list(details.items()):
            result = resolved(detail)
            details[name] = result
            widths[name] = result["width"]
        window["slot_details"], window["placeholders"] = details, widths or None
        return window

    @language.selected
    def check(self, entry, text: str, matcher=None, sources=None, drafts=None) -> dict:
        """The check of `text` as the translation of `entry`; `matcher` replaces the data's glossary matcher."""
        entry = _obj(entry)
        t = expand(text, self.version)
        unavailable = None
        try:
            w = self.substitution_window(entry, sources, drafts)
        except (ValueError, CharmapError) as exc:
            original = entry.get("window")
            w = ({**original, "slot_details": {name: {**detail, "state": "unavailable",
                      "previous_state": detail.get("previous_state", detail["state"])}
                  for name, detail in (original.get("slot_details") or {}).items()}} if original else None)
            unavailable = coded("substitution_unavailable", error=str(exc))
        if w is None:
            laid, probs, m, keys = t, [coded("no_inventory_entry")], self.measure, None
        else:
            lim = limit_of(w)
            m, keys = entry_measure(self.measure, lim), lim.keys
            try:
                scoped_matcher = self.matcher if matcher is None else matcher
                if scoped_matcher is not None and "terms" in entry:
                    scoped_matcher = scoped_matcher.only(entry["terms"])
                laid, probs = entry_problems(source(entry), t, lim, self.measure, self.cm,
                                             self.optional | self.droppable.get(entry["id"], set()),
                                             self.forbidden, w.get("other", []), self.measures,
                                             scoped_matcher,
                                             self.addable.get(entry["id"]), w.get("pair"))
            except CharmapError as e:   # check batch stops here
                laid, probs = t, [encode_problem(t, self.cm) or encoding_problem(e)]
        if unavailable:
            probs.append(unavailable)
        problems = [{"message": p, "severity": "warning" if is_warning(p) else "error"} for p in probs]

        def line(l: str, consumer: dict | None = None) -> dict:
            try:
                px = line_width(l, m, self.cm)
            except CharmapError:
                px = None
            if keys is not None:
                return {"text": l, "width": px, "keys": key_count(l)}
            room = consumer["width"] - consumer["arrow"] if consumer else None
            shadow = px is not None and w is not None and shadow_overflow(l, w["width"], m, self.cm)
            shadow = shadow and (room is None or px <= room)
            if room is not None:
                return {"text": l, "width": px, "shadow": shadow, "room": room,
                        **({"consumer": consumer["name"]} if consumer.get("name") else {})}
            return {"text": l, "width": px, "shadow": shadow}
        pars = paragraphs(laid)
        waiting = waiting_windows(laid, limit_of(w)) if w else [[None] * len(lines_of(p)) for p in pars]
        return {"id": entry["id"], "text": t, "laid": laid, "problems": problems,
                **({"window": w} if w and self.fill_graph else {}),
                "errors": sum(p["severity"] == "error" for p in problems),
                "warnings": sum(p["severity"] == "warning" for p in problems),
                "lines": [[line(l, consumer) for l, consumer in zip(lines_of(par), row)]
                          for par, row in zip(pars, waiting)],
                **({} if isinstance(entry.get("source_text"), str) else {"unavailable": list(TEXT_CHECKS)}),
                **self.extents(w, t)}

    def extents(self, w: dict | None, text: str) -> dict:
        """The text in the entry's other windows and its pair, where it has them."""
        if w is None or not self.cm.can_encode(text):
            return {}
        out = {}
        others = window_widths(text, w.get("other", []), self.measures)
        if others:
            out["others"] = others
        pair = w.get("pair")
        n = pair_width(text, pair, self.measures) if pair else None
        if n is not None:
            out["pair"] = {"value": n, "width": pair["width"], "where": pair["where"], "partner": pair["partner"]}
        return out

    def check_json(self, entry, text: str, sources=None, drafts=None) -> str:
        return json.dumps(self.check(entry, text, sources=sources, drafts=drafts), ensure_ascii=False)

    def rule_preview_json(self, identity, entries, drafts, request) -> str:
        """Use the shared validator for grouped catalog previews."""
        from .editorrules import catalog_preview
        rule = self.engine_rules[identity]
        if rule["kind"] == "policy":
            from .policyrules import preview
            result = preview(rule, _obj(request), self.cm, self.rule_glyphs, self.measure)
        else:
            result = catalog_preview(rule, _obj(entries), _obj(drafts), _obj(request),
                                     self.cm, self.rule_glyphs)
        return json.dumps(result, ensure_ascii=False)


def bundle_current(path: Path) -> bool:
    """Whether the bundle contains this toolkit's versioned initializer."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as bundle:
            return bundle.read("gen3tl/__init__.py") == (Path(__file__).parents[1] / "__init__.py").read_bytes()
    except (FileNotFoundError, zipfile.BadZipFile, KeyError):
        return False


def write_bundle(out: Path, rules: dict, profile=None) -> list[str]:
    """Write the browser bundle (the BUNDLE modules and rules.json) as a zip with fixed entry times; returns its names."""
    import zipfile
    rules = export_rules(rules)
    src = Path(__file__).parents[1]
    files = [(f"gen3tl/{n}", (src / n).read_bytes()) for n in BUNDLE]
    pack_root = language.ROOT / "assets/lang"
    for path in sorted(pack_root.glob("*/language.json")):
        selected = language.load(path.parent.name)
        paths = [path] + [selected.root / selected.data[key] for key in ("provider", "agreement")
                          if selected.data.get(key, "manual") != "manual"]
        if "stand_ins" in selected.data:
            paths.append(selected.root / selected.data["stand_ins"])
        files.extend((p.relative_to(language.ROOT).as_posix(), p.read_bytes()) for p in paths)
    # the profiles' save layouts, for the save page
    from ..profile.profile import Profile, profile_paths
    layouts = {}
    profiles = [Profile.load(path) for path in profile_paths()]
    if profile is not None:
        profiles.append(profile)
    for prof in profiles:
        if prof.get("save"):
            layouts[prof.name] = prof["save"]
    files.append(("save_layouts.json", json.dumps(layouts, ensure_ascii=False, sort_keys=True).encode("utf-8")))
    files.append(("rules.json", json.dumps(rules, ensure_ascii=False, indent=1).encode("utf-8")))
    with zipfile.ZipFile(out, "w") as z:
        for name, data in files:
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return [name for name, _ in files]
