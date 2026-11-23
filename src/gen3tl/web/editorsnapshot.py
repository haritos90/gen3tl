"""ROM-free editor measurements; translations remain live inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..check import qa
from ..catalog.batch import record_speakers
from .editordata import context, fonts_data, merge_identity, name_keys, text_free
from .editorcheck import Checker
from .snapshotstore import load, paths, write
from .editorkeyboard import cursor_columns
from ..build.font import FontTable, check_copies
from ..lang.langpack import load_styles
from ..check.layout import Limit, Measure, lines_of, paragraphs
from ..check.entrycheck import BRACE
from ..catalog import keys
from ..catalog.catalog import line_widths
from ..catalog.source import digest as text_digest
from ..core.charmap import Charmap
from ..core.notes import BoundProse

SCHEMA = "7.0.0"
CONFIG_KEYS = ("font", "font_style", "font_styles", "layout", "table", "keyboard",
               "context", "strings", "script", "maps", "reach", "language_rules",
               "code_replacement", "selected_code_replacements", "text_prefixes",
               "text_controls", "easy_chat_alphabet", "sprite_font")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def source_digest(catalog: dict, root: Path, rows: dict) -> str:
    """The catalog's files and English: each entry's path and the digest its source row holds."""
    return digest([[sid, path.resolve().relative_to(root.resolve()).as_posix(), rows[sid][0]]
                   for sid, (path, entry) in sorted(catalog.items())])


def source_rows(catalog: dict, limits: dict, glyph_width, measurements=None, terms=None,
                contexts=None) -> dict[str, list]:
    """Inventory id -> the source row of its English (see the module)."""
    import re
    from ..lang.fill import entry_matcher
    from ..lang.termbase import Senses, TermMatcher, _english_text, english_pattern
    matcher = TermMatcher(terms) if terms else None
    senses = Senses(terms or [])
    names = []
    by_prefix: dict[str, set[int]] = {}
    always: set[int] = set()
    for i, t in enumerate(terms or []):
        alternatives = tuple(senses.source_text.get(t.id.split("~")[0], [])) or ((t.source_text,) if t.source_text else ())
        names.append((t.id, english_pattern(alternatives)))
        for name in alternatives:
            if not name.strip():
                continue
            # Inflection can replace the final letter.
            prefix = re.match(r"[A-Za-z]+", _english_text(name).strip()[:-1])
            if prefix:
                by_prefix.setdefault(prefix[0].lower(), set()).add(i)
            else:
                always.add(i)
    word_runs = re.compile(r"[A-Za-z]+", re.I)
    # Unicode case equivalents share ASCII boundaries.
    case_equivalents = str.maketrans("İıſK", "iisk")
    measurements = measurements or {"main": (Measure(glyph_width, {}), Charmap())}
    context_names = name_keys(catalog, keys.for_catalog(catalog).key)
    out = {}
    for sid, (_, e) in sorted(catalog.items()):
        lim = limits[sid]
        ctx = text_free(context(e.extracted, catalog, english=e.msgid), context_names)
        merge_identity(ctx, (contexts or {}).get("strings", {}).get(sid, {}))
        english = _english_text(e.msgid)
        candidates = set(always)
        for word in set(word_runs.findall(english)):
            word = word.translate(case_equivalents).lower()
            for end in range(1, len(word) + 1):
                candidates.update(by_prefix.get(word[:end], ()))
        named = []
        for i in sorted(candidates):
            tid, pattern = names[i]
            if pattern is not None and pattern.search(english):
                named.append(tid)
        out[sid] = [text_digest(e.msgid), BRACE.findall(e.msgid),
                    [len(lines_of(par)) for par in paragraphs(e.msgid)], line_widths(e.msgid, Charmap(), glyph_width),
                    [lim.kind, lim.width, lim.lines, lim.chars, lim.bytes, lim.keys, lim.style],
                    {"chars": len(Charmap().encode(e.msgid, terminate=False)),
                     "sources": {k: [v["kind"], v["rule"], v["evidence"], v.get("unverified", False)]
                                 for k, v in (lim.sources or {}).items()},
                     "widths": {style: [max(m.width(line, cm) for par in paragraphs(e.msgid)
                                            for line in lines_of(par)), qa.english_width(e.msgid, m, cm)]
                                for style, (m, cm) in measurements.items() if isinstance(m, Measure)},
                     "terms": list(dict.fromkeys(t.id for t in entry_matcher(e, matcher).in_source(e.msgid)))
                     if matcher else [],
                     "names": named,
                     "context": {**{k: ctx[k] for k in ("maps", "events", "boxes", "uses", "identity")},
                                 "speakers": [{"name": sp.get("name"), **sp} for sp in ctx["speakers"]]}}]
    return out


def digests(doc: dict) -> dict[str, str]:
    """Inventory id -> the digest of its English, of a snapshot document."""
    return {sid: row[0] for sid, row in doc.get("source", {}).items()}


def project_digests() -> dict[str, str]:
    """digests of the running project's snapshot; {} without one."""
    from ..project import active
    project = active()
    if project is None or not project.snapshot.is_file():
        return {}
    return digests(load(project.snapshot))


def configuration(prof) -> str:
    # Display translations do not affect measurements.
    def measured(value):
        if isinstance(value, dict):
            return {k: measured(v) for k, v in value.items() if not k.startswith("where_")}
        if isinstance(value, list):
            return [measured(v) for v in value]
        return value

    facts = {key: prof.get(key) for key in CONFIG_KEYS}
    if prof.get("selected_text_prefixes"):
        facts["selected_text_prefixes"] = prof.get("selected_text_prefixes")
    return digest(measured(facts))


def _shape(value, spec, where="snapshot") -> None:
    """Reject untyped or unexpected snapshot fields."""
    if isinstance(value, BoundProse):
        value = str(value)
    if isinstance(spec, type):
        good = type(value) is spec
    elif spec is None:
        good = value is None
    elif isinstance(spec, dict):
        good = isinstance(value, dict) and value.keys() == spec.keys()
        if good:
            for key, child in spec.items():
                _shape(value[key], child, f"{where}.{key}")
    elif spec[0] == "map":
        good = isinstance(value, dict) and all(isinstance(key, str) for key in value)
        if good:
            for key, child in value.items():
                _shape(child, spec[1], f"{where}.{key}")
    elif spec[0] == "list":
        good = isinstance(value, list)
        if good:
            for child in value:
                _shape(child, spec[1], where)
    elif spec[0] == "row":
        good = isinstance(value, list) and len(value) == len(spec) - 1
        if good:
            for child, child_spec in zip(value, spec[1:]):
                _shape(child, child_spec, where)
    else:
        good = any(type(value) is item if isinstance(item, type) else value is item for item in spec)
    if not good:
        raise ValueError(f"{where}: invalid measurement snapshot value")


INVENTORY = {"id": str, "offset": int, "length": int, "kind": str, "category": str,
             "mode": str, "table": (str, None), "index": (int, None), "flags": ("list", str),
             "refs": ("list", ("row", int, str)), "source_sha256": str}
CONTEXT = {"uses": ("list", {"event": (str, None), "box": (str, None),
                            "at": (int, None), "trainer_role": (str, None)}),
           "slots": ("map", {"fills": ("list", ("row", str, (str, int, None))),
                              "uses": int, "unfilled": int}),
           "slot_refs": {"unwalked": int, "code": int},
           "fills": ("list", ("row", str, str))}
LIMIT = ("row", str, int, (int, None), (int, None), (int, None), (int, None), (str, None))
PARTICIPANT = {"in_source": str, "basis": str,
               "sources": ("list", {"source": str, "gender": str, "value": str, "evidence": str})}
IDENTITY = {"schema": str, "speaker": PARTICIPANT, "addressee": PARTICIPANT}
SOURCE = ("row", str, ("list", str), ("list", int), ("list", int), LIMIT,
          {"chars": int, "sources": ("map", ("row", str, dict, str, bool)),
           "widths": ("map", ("row", int, int)), "terms": ("list", str), "names": ("list", str),
           "context": {"maps": ("list", str), "events": ("list", str), "boxes": ("list", str), "uses": int,
                       "speakers": ("list", {"name": (str, None), "gender": str, "source": str}),
                       "identity": IDENTITY}})
SHAPE = {"schema": str, "profile": str, "language": str, "rom_sha1": str,
         "source_sha256": str, "configuration_sha256": str, "facts_sha256": str, "source": ("map", SOURCE),
         "fonts": ("map", {"widths": ("list", int), "shadows": ("list", bool)}),
         "inventory": ("list", INVENTORY), "context": ("map", CONTEXT),
         "reach": ("map", {"class": str, "group": str,
                            "evidence": ("list", {"kind": str, "at": str})}),
         "notes": ("map", ("list", str)),
         "records": {"windows": ("list", ("row", int, int, (int, None), str, int)),
                     "numbers": ("map", ("map", int)), "keys": ("map", int), "heads": ("map", str),
                     "cursor": ("list", ("list", int)),
                     "fill_alphabet": str,
                     "fill_fragments": ("map", {"glyphs": ("list", ("row", int, int)),
                                                 "trailing": int, "end": (int, None)}),
                     "code_originals": ("map", {"length": int, "sha256": str, "font": str, "width": int,
                                                  "glyph_counts": ("list", ("row", int, int))})}}


def extract(prof, language: str, rom, inv: dict, ctx: dict, reach: dict,
            catalog: dict, po_root: Path, terms=None) -> dict:
    """Extract semantic facts, never pixel or palette data."""
    if rom.sha1() != prof.sha1:
        raise ValueError("measurement ROM differs from the selected profile")
    for name, document, command in (("inventory", inv, "discover inventory"),
                                    ("context", ctx, "discover context"), ("reach", reach, "discover reach")):
        if (document.get("profile"), document.get("rom_sha1")) != (prof.name, prof.sha1):
            raise ValueError(f"{name}: missing or mismatched identity; regenerate gen3tl {command}")
    fonts = FontTable.from_profile(prof)
    problems = check_copies(rom, fonts)
    if problems:
        raise ValueError("font copies differ: " + "; ".join(problems))
    metrics = {}
    for font in fonts:
        metrics.setdefault(font.style, {"widths": [font.width(rom, b) for b in range(font.count)],
                                        "shadows": [font.shadow_end(rom, b) for b in range(font.count)]})
    inventory = []
    for entry in inv["entries"]:
        sid = entry["id"]
        if sid in catalog and entry["text"] != catalog[sid][1].msgid:
            raise ValueError(f"{sid}: catalog source differs from inventory")
        row = {key: entry[key] for key in INVENTORY if key not in ("refs", "source_sha256")}
        row["refs"] = [[r["at"], r["kind"]] for r in entry["refs"]]
        row["source_sha256"] = text_digest(entry["text"])
        inventory.append(row)
    context = {}
    for sid, rec in ctx["strings"].items():
        uses = [{key: u.get(key) for key in ("event", "box", "at", "trainer_role")}
                for u in rec.get("uses", [])]
        slots, refs, fills = rec.get("slots", {}), rec.get("slot_refs", {}), rec.get("fills", [])
        if slots or refs or fills or any(u["event"] == "sign" or u["box"] == "sign"
                                        or u["trainer_role"] for u in uses):
            context[sid] = {"uses": uses, "slots": slots, "fills": fills,
                            "slot_refs": {key: refs.get(key, 0) for key in ("unwalked", "code")}}
    study = {sid: {"class": rec["class"], "group": rec.get("group", "script"),
                   "evidence": [{key: r.get(key, "") for key in ("kind", "at")}
                                for r in rec.get("evidence", [])[-1:]]}
             for sid, rec in reach["strings"].items()}
    layout = prof.get("layout", {})
    windows = []
    for at, width, lines, rule in qa.window_rules(layout, rom):
        kind = "record_window" if "texts" in rule else "table_window"
        windows.append([at, width, lines, kind, layout[kind].index(rule)])
    packs = load_styles(language, prof)
    cm = packs[prof["font_style"]["style"]].charmap()
    from ..check.fillsources import graph, fragment, code_graph, native_graph
    sources = graph(prof, inv, catalog)
    originals = qa.fill_originals(prof, catalog, rom, inv=inv)
    legacy = code_graph(prof, catalog, originals, None, cm)
    native = native_graph(prof, inv, catalog, cm)
    dependencies = {**sources["dependencies"], **legacy["dependencies"], **native["dependencies"]}
    fragments = {sid: fragment(catalog[sid][1].msgid, cm)
                 for sid in sorted(set().union(*map(set, dependencies.values())))}
    doc = {"schema": SCHEMA, "profile": prof.name, "language": language, "rom_sha1": prof.sha1,
           "configuration_sha256": configuration(prof),
           "fonts": metrics, "inventory": inventory, "context": context, "reach": study,
           "notes": record_speakers(prof.get("context", {}).get("record_speakers", []), rom, inv),
           "records": {"windows": windows,
                       "numbers": {str(at): values for at, values in qa.record_numbers(layout, rom).items()},
                       "keys": qa.keyboard_rows(prof, inv, rom), "heads": qa.interior_heads(prof, inv, cm, rom),
                       "cursor": cursor_columns(prof, rom) or [],
                       "code_originals": originals,
                       "fill_fragments": fragments, "fill_alphabet": sources["alphabet"]}}
    # the limits with the English as translation
    offline, offline_ctx, _, facts = inputs(doc, catalog, prof)
    measure, offline_cm, measurements = measures(prof, language, metrics)
    limits = qa.compute_limits(prof, offline, catalog, measure, offline_cm, offline_ctx, english_slots=True,
                               facts=facts)
    font = fonts[0]
    doc["source"] = source_rows(catalog, limits, lambda b: font.width(rom, b), measurements, terms, ctx)
    doc["source_sha256"] = source_digest(catalog, po_root, doc["source"])
    doc["facts_sha256"] = digest(doc)
    validate(doc, prof, language, catalog, po_root)
    return doc


def validate(doc: dict, prof, language: str, catalog: dict, po_root: Path) -> None:
    """The snapshot of the profile, the language and the catalog; an entry whose English differs from its digest is refused."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ValueError("editor snapshot schema differs; regenerate gen3tl web snapshot")
    _shape(doc, SHAPE)
    if getattr(prof, "language", language) != language:
        raise ValueError("editor snapshot language differs from the effective profile")
    rows = doc["source"]
    if not set(catalog) <= set(rows):
        raise ValueError("editor snapshot source_sha256 differs: catalog entries without source facts; "
                         "regenerate gen3tl web snapshot")
    changed = [sid for sid, (_, e) in sorted(catalog.items())
               if not e.keyed and text_digest(e.msgid) != rows[sid][0]]
    if changed:
        key = keys.for_profile(prof).key
        raise ValueError(f"editor snapshot source_sha256 differs: the English of {len(changed)} entries differs from "
                         f"the snapshot digests: {', '.join(key(sid) for sid in changed[:10])}")
    expected = {"schema": SCHEMA, "profile": prof.name, "language": language, "rom_sha1": prof.sha1,
                "source_sha256": source_digest(catalog, po_root, rows), "configuration_sha256": configuration(prof)}
    for key, value in expected.items():
        if doc[key] != value:
            raise ValueError(f"editor snapshot {key} differs; regenerate gen3tl web snapshot")
    if doc["facts_sha256"] != digest({key: value for key, value in doc.items() if key != "facts_sha256"}):
        raise ValueError("editor snapshot facts checksum differs; regenerate gen3tl web snapshot")
    fonts = {f.style: f for f in FontTable.from_profile(prof)}
    if fonts.keys() != doc["fonts"].keys():
        raise ValueError("editor snapshot font styles differ")
    for style, metrics in doc["fonts"].items():
        if (len(metrics["widths"]) != fonts[style].count or len(metrics["shadows"]) != fonts[style].count
                or any(not 0 <= width <= 255 for width in metrics["widths"])):
            raise ValueError(f"editor snapshot {style}: invalid glyph measurements")
    qa.code_fills(prof.get("layout", {}))
    qa.fill_originals(prof, catalog, facts=doc["records"]["code_originals"], inv={"entries": doc["inventory"]})
    names = {font.name: font for font in FontTable.from_profile(prof)}
    for sid, fact in doc["records"]["code_originals"].items():
        widths = doc["fonts"][names[fact["font"]].style]["widths"]
        if fact["width"] != sum(widths[code] * count for code, count in fact["glyph_counts"]):
            raise ValueError(f"{sid}: original width differs from font measurements")
    ids = [row["id"] for row in doc["inventory"]]
    if len(ids) != len(set(ids)) or not set(catalog) <= set(ids):
        raise ValueError("editor snapshot inventory has duplicate or missing catalog entries")
    for row in doc["inventory"]:
        value = row["source_sha256"]
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("editor snapshot inventory has an invalid English digest")
        if row["id"] in rows and value != rows[row["id"]][0]:
            raise ValueError("editor snapshot inventory digest differs from source")
    for _, _, _, kind, index in doc["records"]["windows"]:
        if kind not in ("table_window", "record_window") or not 0 <= index < len(prof.get("layout", {}).get(kind, [])):
            raise ValueError("editor snapshot has an invalid window rule reference")


def inputs(doc: dict, catalog: dict, prof) -> tuple[dict, dict, dict, dict]:
    """The inventory, context, reach study and records of a snapshot for the checks."""
    def text(row):
        if row["id"] not in catalog:
            return ""
        entry = catalog[row["id"]][1]
        return "" if entry.keyed else entry.msgid
    inv = {"entries": [{**row, "text": text(row), "refs": [{"at": at, "kind": kind} for at, kind in row["refs"]]}
                       for row in doc["inventory"]]}
    records = doc["records"]
    facts = {**records, "numbers": {int(at): values for at, values in records["numbers"].items()},
             "windows": [(at, width, lines, prof["layout"][kind][index])
                         for at, width, lines, kind, index in records["windows"]]}
    return inv, {"strings": doc["context"]}, {"strings": doc["reach"]}, facts


def limits(doc: dict, catalog: dict, prof) -> dict[str, Limit]:
    """Recalculate translation limits from the snapshot's English measurements."""
    inv, ctx, _, facts = inputs(doc, catalog, prof)
    measure, cm, _ = measures(prof, doc["language"], doc["fonts"])
    return qa.compute_limits(prof, inv, catalog, measure, cm, ctx, facts=facts, source=doc["source"])


def checker(doc: dict, metrics: dict, rules=None, version: str | None = None) -> Checker:
    """Combine original measurements with current language glyphs."""
    shadows = {}
    for name, style in doc["fonts"]["styles"].items():
        base = metrics[name]["shadows"]
        pack = {int(code, 16) for code in style["pack"]}
        drawn = {int(code, 16) for code in style["shadow"]}
        copies = {int(code, 16): int(source, 16) for code, source in style["copy"].items()}
        shadows[name] = [code in drawn if code in pack else base[copies.get(code, code)]
                         for code in range(len(base))]
    return Checker(doc, {name: values["widths"] for name, values in metrics.items()},
                   rules, version, shadows=shadows)


def measures(prof, language: str, metrics: dict):
    packs = load_styles(language, prof)
    main = prof["font_style"]["style"]
    measure = Measure(lambda _: 0, prof.get("layout", {}).get("placeholders", {}))
    chars = {c: f"{b:02X}" for c, b in packs[main].chars.items() if packs[main].sources[c] != "base"}
    doc = {"fonts": fonts_data(prof, packs), "charmap": chars,
           "placeholders": {"default": measure.default_ph, "widths": measure.ph}}
    result = checker(doc, metrics)
    measures = {style: (measure, packs[style].charmap() if style in packs else result.cm)
                for style, (measure, _) in result.measures.items()}
    return result.measure, result.cm, measures
