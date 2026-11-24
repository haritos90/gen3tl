"""Build language documents for the web editor."""

from __future__ import annotations

from ..lang import language

import re

from ..catalog import keys
from ..catalog.batch import approved_by_english
from ..catalog.catalog import never_shown_evidence, nothing_to_translate
from ..catalog.po import catalog_name, comment_kind
from ..catalog.source import digest as text_digest
from ..core.charmap import PLACEHOLDER, CharmapError
from ..discover.maps import gender_source, identity_context
# re-exported from the browser check
from .editorcheck import LIMIT_FIELDS, RULES, export_rules, limit_of  # noqa: F401
from .editorgfx import graphics_data
from .editorkeyboard import keyboard_data
from .editorrules import SOURCE_TEXT_FIELDS, rules_data
from ..check.layout import Limit, entry_measure
from ..check.provenance import code as coded

SCHEMA = "9.0.0"
NEVER = "never shown"   # the reach class progress leaves out
BLANK = text_digest("")   # an empty English
BRACE = re.compile(r"\{[^}]*\}")
# PO extracted comments (catalog.build_entries)
USED = re.compile(r"used: (?P<map>.*?), (?P<event>[a-z]+)(?P<rest>.*) \(script [^)]*\)$")
USED_IN = re.compile(r"used in (\d+) places$")
INFERRED = "probable map (inferred from neighbours): "
LABEL = re.compile(r"speaker label: (?P<name>.*) \((?P<gender>[^()]*)\)$")
TRAINER = re.compile(r"trainer: (?P<name>.*) \((?P<cls>.*), (?P<gender>[^,()]*)\)$")
WORD = re.compile(r"(\{STR_VAR_\d\}) may be (\S+?): ")
HOST = re.compile(r"inserted as (\{[^}]+\}) into (\S+?): ")
# batch.record_speakers notes
RECORD = re.compile(r"speaker: (?P<gender>[a-z]+(?: \(\d+\))?)(?:, trainer class (?P<cls>.*?))? "
                    r"\(record (?P<record>\d+) of (?P<table>[^)]+)\)")
# category comment: prefers the name's own table row
CATEGORY = re.compile(r"^category: ([^;]+);")


from contextlib import contextmanager
from contextvars import ContextVar

TOOLKIT_VERSION_OVERRIDE = ContextVar("toolkit_version_override", default=None)


@contextmanager
def pinned_toolkit_version(version: str):
    """Use stable version metadata for deterministic generation."""
    token = TOOLKIT_VERSION_OVERRIDE.set(version)
    try:
        yield
    finally:
        TOOLKIT_VERSION_OVERRIDE.reset(token)


def toolkit_version(repo=None) -> str:
    """gen3tl's version with the commit of `repo` as SemVer build metadata, .dirty with tracked changes."""
    import subprocess
    from pathlib import Path
    from .. import INSTALLED, __version__
    if (pinned := TOOLKIT_VERSION_OVERRIDE.get()) is not None:
        return pinned
    if repo is None and INSTALLED:   # a checkout around the environment is not the toolkit's
        return __version__
    git = ["git", "-C", str(repo or Path(__file__).resolve().parent), "--no-optional-locks"]
    try:
        head = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        if head.returncode != 0 or not head.stdout.strip():
            return __version__
        changed = subprocess.run(git + ["status", "--porcelain", "--untracked-files=no"],
                                 capture_output=True, text=True)
    except OSError:
        return __version__
    dirty = ".dirty" if changed.returncode == 0 and changed.stdout.strip() else ""
    return f"{__version__}+{head.stdout.strip()}{dirty}"


def toolkit_fingerprint(package=None) -> str:
    """A hash of the toolkit's sources: the key of generated data follows the code it ran."""
    import hashlib
    from pathlib import Path
    root = Path(package or Path(__file__).resolve().parents[1])
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def source_data(prof, catalog: dict, inv: dict, snapshot: dict | None = None) -> dict:
    """The rows that read every entry's English from the user's ROM in the browser (catalog.source.read), by key."""
    from ..catalog import source
    from .editorsnapshot import digests
    expected = digests(snapshot) if snapshot is not None else {
        sid: source.digest(e.msgid) for sid, (_, e) in catalog.items() if not e.keyed}
    key = keys.for_profile(prof).key
    by_id = {e["id"]: e for e in inv["entries"]}
    rows = {key(sid): source.row(by_id[sid]) + [expected[sid]]
            for sid in catalog if sid in by_id and sid in expected}
    return {"schema": 1, "profile": prof.name, "rom_sha1": prof.sha1, "rows": rows}


def status(e, blank: bool | None = None) -> str:
    """approved | fuzzy | untranslated | nothing, from the PO flags; `blank`: whether the English is empty."""
    if nothing_to_translate(e) if blank is None else blank and not e.msgstr and not e.fuzzy:
        return "nothing"
    if not (e.msgstr or "empty" in e.flags):
        return "untranslated"
    return "fuzzy" if e.fuzzy else "approved"


def add(items: list, value) -> None:
    if value not in items:
        items.append(value)


def context(comments: list[str], catalog: dict, aliases: keys.Aliases = keys.EMPTY,
            english: str | None = None) -> dict:
    """Context of an entry from its PO extracted comments; `aliases` names the entries of the comments by key."""
    out = {"comments": comments, "maps": [], "events": [], "boxes": [], "uses": 0, "speakers": [],
           "inserted": [], "words": [], "hosts": [], "identity": identity_context(english)}
    used = 0
    for c in comments:
        if m := USED.match(c):
            used += 1
            if m["map"] != "?":
                add(out["maps"], m["map"])
            add(out["events"], m["event"])
            if b := re.search(r", box (\w+)", m["rest"]):
                add(out["boxes"], b[1])
            if g := re.search(r", speaker colour (\w+)", m["rest"]):
                add(out["speakers"], {"gender": g[1], "source": "text colour"})
            if sprite := re.search(r", sprite (\d+)", m["rest"]):
                add(out["identity"]["speaker"]["sources"], gender_source("sprite", None, sprite[1]))
        elif m := USED_IN.match(c):
            out["uses"] = int(m[1])
        elif c.startswith(INFERRED):
            add(out["maps"], c[len(INFERRED):])
        elif m := LABEL.match(c):
            add(out["speakers"], {"name": m["name"], "gender": m["gender"], "source": "label"})
        elif m := TRAINER.match(c):
            add(out["speakers"], {"name": m["name"], "class": m["cls"], "gender": m["gender"],
                                  "source": "trainer"})
        elif m := RECORD.match(c):
            sp = {"gender": m["gender"], "source": "record"}
            if m["cls"]:
                sp["class"] = m["cls"]
            add(out["speakers"], sp)
            cls = re.match(r"\d+", m["cls"] or "")
            value = m["record"] + (f" / {cls[0]}" if cls else "")
            add(out["identity"]["speaker"]["sources"],
                gender_source("record", m["gender"], value, m["table"]))
        elif m := WORD.match(c):
            w = catalog.get(aliases.entry_id(m[2]))
            add(out["words"], {"slot": m[1], "id": aliases.key(m[2]), "source_text": w[1].msgid if w else "",
                               "translation": w[1].msgstr if w else ""})
        elif m := HOST.match(c):
            add(out["hosts"], {"slot": m[1], "id": aliases.key(m[2])})
    out["uses"] = max(out["uses"], used)
    for speaker in out["speakers"]:
        sources = out["identity"]["speaker"]["sources"]
        if not any(s["source"] == speaker["source"] and s["gender"] == speaker["gender"] for s in sources):
            add(sources, gender_source(speaker["source"], speaker["gender"]))
    return out


def merge_identity(ctx: dict, record: dict) -> None:
    """Prefer measured provenance; retain additional catalog evidence."""
    measured = record.get("identity")
    if measured is None:
        return
    identity = {"schema": measured["schema"]}
    for role in ("speaker", "addressee"):
        sources = list(measured[role]["sources"])
        for source in ctx["identity"][role]["sources"]:
            if not any(s["source"] == source["source"] and
                       (s["gender"] == source["gender"] or source["gender"] == "unknown")
                       and (not source["value"] or s["value"] == source["value"]) for s in sources):
                add(sources, source)
        identity[role] = {**measured[role], "sources": sources}
    ctx["identity"] = identity


def name_keys(catalog: dict, key) -> dict[str, str]:
    """Raw name -> the key of its catalog entry, table and data-table rows before script lines with the same text."""
    names: dict[str, tuple[int, str]] = {}
    for sid, (_, e) in catalog.items():
        if not e.msgid or e.keyed:
            continue
        category = ""
        for c in e.extracted:
            if m := CATEGORY.match(c):
                category = m[1]
                break
        score = 2 if category == "data-table" or category.startswith("table:") else 1
        if score >= names.get(e.msgid, (0, ""))[0]:
            names[e.msgid] = (score, key(sid))
    return {text: k for text, (_, k) in names.items()}


def text_free(ctx: dict, names: dict[str, str] | None = None) -> dict:
    """An entry's context without the game's text: quoting comments dropped, names as keys, inserted words without English."""
    names = names or {}
    speakers = []
    for sp in ctx["speakers"]:
        out = {}
        if "name" in sp and sp["name"] in names:
            out["name"] = names[sp["name"]]
        out.update({k: v for k, v in sp.items() if k not in ("name", "class")})
        add(speakers, out)
    return {**{k: v for k, v in ctx.items() if k != "maps"},
            "maps": [names[m] for m in ctx["maps"] if m in names],
            "comments": [c for c in ctx["comments"] if not comment_kind(c)[1]], "speakers": speakers,
            "words": [{k: v for k, v in w.items() if k != "source_text"} for w in ctx["words"]]}


def context_links(records: dict, catalog: dict, aliases: keys.Aliases) -> dict:
    """Rebuild word and host links from context fills."""
    out = {}
    for sid, rec in sorted(records.get("strings", {}).items()):
        entry = catalog.get(sid)
        for host, slot in rec.get("fills", []):
            add(out.setdefault(sid, {"hosts": [], "words": []})["hosts"],
                {"slot": slot, "id": aliases.key(host)})
            add(out.setdefault(host, {"hosts": [], "words": []})["words"],
                {"slot": slot, "id": aliases.key(sid), "translation": entry[1].msgstr if entry else ""})
    return out


def partner_text(pair: dict | None, translated) -> dict | None:
    """A name pair whose partner and species keep their text only where `translated` says so."""
    if not pair:
        return pair
    partner = {**pair["partner"], "species": dict(pair["partner"]["species"])}
    for part in (partner, partner["species"]):
        if not translated(part["id"]):
            part.pop("text", None)
    return {**pair, "partner": partner}


def inserted(english: str, lim: Limit | None, measure, cm, estimates=frozenset()) -> list[dict]:
    """Each placeholder of the English at the width the check measures it with, and the coded source of that width."""
    m = entry_measure(measure, lim) if lim is not None else measure
    own = (lim.placeholders or {}) if lim is not None else {}
    out = []
    for tok in dict.fromkeys(BRACE.findall(english)):
        try:
            if cm.encode(tok, terminate=False)[:1] != bytes([PLACEHOLDER]):
                continue
        except CharmapError:
            continue
        name = tok[1:-1].split()[0]
        if name in own:
            src = (lim.slot_sources or {}).get(name, coded("slot_context"))
        elif tok[1:-1] in measure.ph or name in measure.ph:
            estimate = (tok[1:-1] if tok[1:-1] in measure.ph else name) in estimates
            src = coded("slot_profile_estimate" if estimate else "slot_profile")
        else:
            src = coded("slot_default")
        out.append({"token": tok, "width": m.token_width(tok, cm), "source": src})
    return out


def profile_label_inputs(prof) -> dict[str, Path]:
    """Include every registered display language, including absent catalogs."""
    from ..project import REGISTRY, Registry, registry_root

    root = registry_root()
    if not (root / REGISTRY).is_file():
        return {}
    return {project.language: project.profile_labels for project in Registry.load(root).projects
            if project.profile == getattr(prof, "name", None)}


def where_texts(prof, aliases: keys.Aliases) -> dict:
    """Keep profile descriptions in the chosen site language."""
    from ..catalog.po import read
    from ..profile.schema import element_ids, key_text

    catalogs = {}
    for code, path in profile_label_inputs(prof).items():
        if path.is_file():
            _, entries = read(path)
            catalogs[code] = {entry.msgctxt: entry for entry in entries if entry.msgstr}
    out = {}

    def collect(value, path):
        if isinstance(value, list):
            for identity, child in zip(element_ids(value), value):
                collect(child, f"{path}[{identity}]")
        elif isinstance(value, dict):
            where = value.get("where")
            if isinstance(where, str):
                texts = {code: entry.msgstr for code, entries in catalogs.items()
                         if (entry := entries.get(f"{path}.where")) is not None and entry.msgid == where}
                if texts:
                    out.setdefault(where, {}).update(texts)
                    out.setdefault(aliases.rekey_text(where), {}).update(
                        {code: aliases.rekey_text(text) for code, text in texts.items()})
            for key, child in value.items():
                collect(child, f"{path}.{key_text(key)}")

    for section in ("layout", "table", "sprite_font"):
        collect(prof.get(section, {}), section)
    return out


def window(sid: str, lim: Limit | None, main: str, windows=None, pair: dict | None = None,
           translations=None) -> dict | None:
    """The check's limit as a dict, its font, the windows of other styles, the pair."""
    if lim is None:
        return None
    w = {k: v for k, v in vars(lim).items() if v is not None or k in ("kind", "width", "lines")}
    if w.get("strip") and translations is not None:   # an untranslated prefix is the game's text
        w["strip"] = [x for x in w["strip"] if x in translations]
        if not w["strip"]:
            del w["strip"]
    w["font"] = lim.style or main
    other = windows.other(sid) if windows is not None else []
    if other:
        w["other"] = other
    if pair:
        w["pair"] = pair
    return w


def reach_data(sid: str, comments: list[str], study: dict | None) -> dict:
    """The reach class of an entry, its source and evidence."""
    evidence = never_shown_evidence(sid, comments, study)
    if evidence is not None:
        parts = [x.split(":")[0] for x in evidence.split("; ") if x.startswith(("reach study:", "review:"))]
        return {"class": NEVER, "source": ", ".join(dict.fromkeys(parts)) or "review", "evidence": evidence}
    r = ((study or {}).get("strings") or {}).get(sid)
    if r is None:
        return {"class": "reachable", "source": "not studied", "evidence": ""}
    root = r["evidence"][-1] if r.get("evidence") else {}
    how = " ".join(x for x in (root.get("kind"), root.get("at")) if x)
    study = {"class": r["class"], **({"root": how} if how else {})}
    return {"class": r["class"], "source": "reach study",
            "evidence": coded("reach_study_root" if how else "reach_study", **study)}


def keyed_rules(rules: dict, aliases: keys.Aliases) -> dict:
    """[qa] rules whose droppable and addable selections name entries by key."""
    out = export_rules(rules)
    for kind in ("droppable", "addable"):
        if isinstance(out.get(kind), list):
            out[kind] = [{**r, "ids": [aliases.key(i) for i in r["ids"]]}
                         if isinstance(r, dict) and isinstance(r.get("ids"), list) else r for r in out[kind]]
    return out


def substitution_data(prof, inv, catalog, cm, key, facts=None):
    """Export source graphs and source-free fallback measurements."""
    from ..check.fillsources import graph, fragment, code_graph, native_graph
    if not (prof.get("layout", {}).get("fill_source") or prof.get("context", {}).get("native_fill") or any("strings" in slot or "digits" in slot
            for row in prof.get("layout", {}).get("code_fill", []) for slot in row["slots"].values())):
        return None
    facts = facts or {}
    resolved = graph(prof, inv, catalog, key=key, alphabet=facts.get("fill_alphabet"))
    wanted = set().union(*map(set, resolved["dependencies"].values()))
    fragments, values = {}, {}
    for sid, (_, entry) in catalog.items():
        name = key(sid)
        if name not in wanted:
            continue
        fact = facts.get("fill_fragments", {}).get(sid)
        if fact is None and not entry.keyed:
            fact = fragment(entry.msgid, cm)
        if fact is not None:
            fragments[name] = fact
        values[name] = {"translation": entry.msgstr, "flags": list(entry.flags)}
    legacy = code_graph(prof, catalog, facts.get("code_originals", {}), None, cm, key=key,
                        fragments=facts.get("fill_fragments"))
    native = native_graph(prof, inv, catalog, cm, key=key, fragments=facts.get("fill_fragments"))
    return {**resolved, "sources": {**native["sources"], **legacy["sources"], **resolved["sources"]},
            "dependencies": {**native["dependencies"], **legacy["dependencies"], **resolved["dependencies"]},
            "fragments": {**native["fragments"], **legacy["fragments"], **fragments},
            "values": {**native["values"], **legacy["values"], **values}}


def rekeyed(value, aliases: keys.Aliases):
    """An explanation whose texts name every aliased inventory id by its key."""
    if isinstance(value, str):
        return aliases.rekey_text(value)
    if isinstance(value, list):
        return [rekeyed(item, aliases) for item in value]
    if isinstance(value, dict):
        return {key: item if key == "code" else rekeyed(item, aliases) for key, item in value.items()}
    return value


def keyed_window(w: dict | None, aliases: keys.Aliases) -> dict | None:
    """A window whose slot sources name their words by key."""
    if w and w.get("slot_sources"):
        w["slot_sources"] = {k: rekeyed(v, aliases) for k, v in w["slot_sources"].items()}
    if w and w.get("slot_details"):
        from copy import deepcopy
        w["slot_details"] = deepcopy(w["slot_details"])
        def rekey(detail):
            for choice in detail.get("choices", []):
                rekey(choice)
            detail["dependencies"] = [aliases.key(sid) for sid in detail.get("dependencies", [])]
            for part in detail.get("contributors", []) + [p for row in detail.get("winners", []) for p in row]:
                if "id" in part:
                    part["id"] = aliases.key(part["id"])
        for detail in w["slot_details"].values():
            rekey(detail)
    return w


def term_data(t) -> dict:
    return {"category": t.category, "source_text": t.source_text, "translation": t.translation, "label": t.label,
            "gender": t.gender, "address": t.address, "status": t.status,
            **({"note_ids": [note["id"] for note in t.note_records]} if t.note_records else {}),
            **({"notes": t.editable_notes} if t.editable_notes is not None else {}),
            "forms": dict(t.forms)}


def note_inputs(folder) -> list:
    """Track absent containers too, including later rule-format changes."""
    from pathlib import Path
    return [Path(folder) / name for name in ("notes", "research", "rules.toml", "rules.tsv")]


def editor_inputs(project, prof, lang: str) -> list:
    """List the cache inputs beside the catalog and profile."""
    from ..profile.profile import TOOLKIT_ROOT
    from ..data.pokedextexts import dataset_dir
    from .snapshotstore import paths as snapshot_paths

    return [*snapshot_paths(project.snapshot), *note_inputs(project.translation), project.glossary,
            project.release, project.translation / "graphics", TOOLKIT_ROOT / "assets" / "lang" / lang,
            dataset_dir(prof.name), project.translation / "pokedex-labels.po", keys.alias_path(prof.sha1),
            project.translation / "pokedex-aliases.toml", project.build / "catalog.pot",
            *[path for path in (project.notes, project.graphics, project.graphics_views,
                                getattr(project, "pokedex_labels", None)) if path is not None],
            *profile_label_inputs(prof).values()]


def research_data(folder) -> dict:
    """Name research subjects without duplicating their note content."""
    from ..lang import research

    subjects = {}
    for path in sorted(folder.glob("*.tsv")):
        for row in research.load(path).rows:
            if not row.record_id:
                continue
            if row.record_id in subjects:
                raise ValueError(f"{path}: duplicate record_id {row.record_id}")
            name = next((row.values[key] for key in ("source_text", "term", "subject", "name")
                         if row.values.get(key)), "")
            subjects[row.record_id] = {"table": path.name, "name": name}
    return subjects


def attach_notes(doc: dict, prof, catalog: dict, terms=(), *, folder=None, wording=None,
                 digests=None) -> None:
    """Expose complete records and retain only unmigrated display fields."""
    from pathlib import Path
    from ..build.graphics import graphic_notes
    from ..catalog import entrynotes, guidance
    from ..check.rules import list_path, load_list, wording_rules
    from ..core import notes as structured
    from ..profile import schema

    code = doc["language"]
    aliases = keys.for_profile(prof)
    release = getattr(prof, "release_path", None)
    folder = Path(folder) if folder is not None else Path(release).parent if release is not None else None
    records = [row for row in guidance.profile_rows(prof) if "subject" in row]
    shared_entries = {aliases.entry_id(row["subject"]["id"]) for row in records}
    source_entries = set()
    candidates = {entrynotes.for_file(file) for file, _ in catalog.values()}
    paths = {path.resolve() for path in candidates if path is not None and path.is_file()}
    if not paths and folder is not None and (folder / "notes" / "entries.toml").is_file():
        paths.add((folder / "notes" / "entries.toml").resolve())
    for path in sorted(paths):
        for sid, rows in entrynotes.load(path, code, aliases).items():
            source_entries.add(sid)
            records.extend(rows)
    for term in terms or ():
        records.extend(term.note_records)
    if prof.path.exists():
        sources = schema.read(prof.path) if prof.path.is_dir() else schema.flat(prof.path)
        for fact in schema.overlay_facts(sources):
            records.extend(fact.note_records)
        if folder is not None and (folder / "notes" / "profile.toml").is_file():
            for rows in schema.language_notes(prof.path, folder / "notes" / "profile.toml", code).values():
                records.extend(rows)
    if folder is not None:
        for path in sorted((folder / "research").glob("*.notes.toml")):
            rows = structured.read_notes(path)["note"]
            for row in rows:
                if (row["subject"]["kind"] != "research"
                        or row["scope"] != {"kind": "language", "language": code}):
                    raise ValueError(f"{path}: expected research notes for {code}")
            records.extend(rows)
        if wording is None:
            wording = load_list(list_path(folder), code=code)
    rule_links = {}
    for rule in wording or ():
        rows = rule.get("notes", [])
        records.extend(rows)
        if rows:
            selectors = {key: value for key, value in rule.items() if key in ("ids", "source_text")}
            selected = wording_rules([{**selectors, "rule": "note", "source": "note"}], catalog)
            for sid in selected:
                rule_links.setdefault(aliases.key(sid), []).extend(row["id"] for row in rows)
    graphics = doc.get("graphics")
    section = prof.get("graphics", {})
    if graphics is not None and section.get("inventory"):
        by_graphic = graphic_notes(Path(prof.project_root) / section["inventory"],
                                   notes_path=folder / "notes" / "graphics.toml" if folder else None,
                                   language=code)
        for row in graphics["inventory"]:
            rows = by_graphic.get(row["id"], [])
            records.extend(rows)
            if rows:
                row["note_ids"] = [note["id"] for note in rows]
                row.pop("reason", None)
    collection = {"schema": structured.SCHEMA, "note": records}
    structured.validate(collection)
    entry_links = {}
    for note in records:
        if note["subject"]["kind"] != "entry":
            continue
        sid = aliases.entry_id(note["subject"]["id"])
        if sid not in catalog:
            continue
        entry = catalog[sid][1]
        expected = (digests or {}).get(sid) if entry.keyed else text_digest(entry.msgid)
        if expected is not None and note["binding"]["source_sha256"] != expected:
            raise ValueError(f"{note['id']}: source note differs from catalog English")
        entry_links.setdefault(aliases.key(sid), []).append(note["id"])
    for entry in doc["entries"]:
        sid = aliases.entry_id(entry["id"])
        source = catalog[sid][1]
        digest = (digests or {}).get(sid) if source.keyed else text_digest(source.msgid)
        if digest is not None:
            entry["source_sha256"] = digest
        ids = entry_links.get(entry["id"], []) + rule_links.get(entry["id"], [])
        if ids:
            entry["note_ids"] = ids
        if sid in source_entries:
            entry.pop("note", None)
        if sid in shared_entries:
            entry.pop("translator_notes", None)
    doc["notes"] = collection
    summaries = {}
    from ..lang.termbase import note_facts
    from ..lang.research import is_term_note
    for term in terms or ():
        for note in term.note_records:
            if is_term_note(note):
                facts = note_facts(note, term.research_findings)
                if facts != note.get("facts", []):
                    summaries[note["id"]] = facts
    if summaries:
        collection["summaries"] = summaries
    doc["research"] = research_data(folder / "research") if folder is not None else {}


WORD_ID = re.compile(r"s[0-9A-F]{7}|[a-z_]+:\d+|" + keys.KEY.pattern)
LETTER = re.compile(r"[^\W_]")


def slot_word(source: dict) -> str | None:
    """The id of the one word a slot source names, else None."""
    if source["code"] == "slot_reasons":
        source = source["source"]
    if source["code"] == "slot_table" or (source["code"] == "slot_record" and re.fullmatch(r"[a-z]+", source["kind"])):
        source = source["word"]
    named = source["code"] in ("slot_untranslated", "slot_empty", "slot_word")
    return source["id"] if named and WORD_ID.fullmatch(source["id"]) else None


def terms_data(terms, doc: dict, catalog: dict, source: dict | None = None) -> dict:
    """terms.json of the split form: every glossary term with the entries that use it, and the slots the words fill."""
    from ..lang.termbase import _norm, build_index
    key = keys.for_catalog(catalog).key
    eng: dict[str, list[str]] = {}
    lines: list[dict] = []   # the words each line's slot may receive, in order
    groups: dict[tuple, int] = {}

    def fill(line: str, slot: str, words: list[str]) -> None:
        if len(words) > 1:
            lines.append({"line": line, "slot": slot, "group": groups.setdefault(tuple(words), len(groups))})
        elif lines and "words" in lines[-1] and (lines[-1]["line"], lines[-1]["slot"]) == (line, slot):
            lines[-1]["words"] += words
        elif words:
            lines.append({"line": line, "slot": slot, "words": words})
    for e in doc["entries"]:
        for t in e.get("terms", []):
            eng.setdefault(t, []).append(e["id"])
        for w in e["context"]["words"]:
            fill(e["id"], w["slot"].strip("{}"), [w["id"]])
        for name, src in ((e["window"] or {}).get("slot_sources") or {}).items():
            if word := slot_word(src):
                fill(e["id"], name, [word])
        for name, detail in ((e["window"] or {}).get("slot_details") or {}).items():
            fill(e["id"], name, list(detail.get("dependencies", [])))
    tr: dict[str, list[str]] = {}
    fo: dict[str, list[str]] = {}   # form-only occurrences
    other: dict[str, list[str]] = {}   # another row's spelling
    sh: dict[str, list[str]] = {}   # the label shown cut
    cut = {t.id for t in terms if t.label and _norm(t.label) != _norm(t.translation)}
    names = {sid: set(row[5]["names"]) for sid, row in (source or {}).items()}
    for r in build_index(terms, catalog, told=lambda sid, tid: tid in names[sid] if sid in names else None):
        if LETTER.search(r["text"]):
            ids = {"yes": tr, "form only": fo}.get(r["use"], other).setdefault(r["term"], [])
            if key(r["string"]) not in ids[-1:]:
                ids.append(key(r["string"]))
            if r["use"] == "yes" and r["form"] == "label" and r["term"] in cut:
                short = sh.setdefault(r["term"], [])
                if key(r["string"]) not in short[-1:]:
                    short.append(key(r["string"]))
    for tid, ids in other.items():   # the other row's sense, not this term's
        own = set(tr.get(tid, [])) | set(fo.get(tid, []))
        drop = set(ids) - own
        if tid in eng and drop:
            eng[tid] = [x for x in eng[tid] if x not in drop]
    rows = [{"id": t.id, **term_data(t), "declension": t.declension, "sources": t.sources,
             "variants": t.variants, "confidence": t.confidence, "in_source": eng.get(t.id, []),
             "in_translation": tr.get(t.id, []), "form_only": fo.get(t.id, []),
             "shortened": sh.get(t.id, [])} for t in terms]
    return {"schema": SCHEMA, "terms": rows, "slots": {"groups": [list(words) for words in groups], "lines": lines}}


def fonts_data(prof, packs: dict) -> dict:
    """Font styles: pack widths, and where the user's ROM holds the rest."""
    from ..build.font import FontTable, font_codes, shadow_end
    main = prof.get("font_style", {}).get("style")
    styles: dict[str, dict] = {}
    for f in FontTable.from_profile(prof):
        if f.style in styles:
            continue   # Shared bytes need only one preview.
        pack = packs.get(f.style)
        slots = pack.glyph_slots() if pack is not None else {}
        styles[f.style] = {"format": f.fmt, "pack": {f"{b:02X}": g.width for b, g in sorted(slots.items())},
                           "rom": {"font": f.name, "glyphs": f.glyphs, "widths": f.widths, "count": f.count}}
        # letters that copy a ROM glyph
        copies = pack.copy_slots() if pack is not None else {}
        styles[f.style]["copy"] = {f"{b:02X}": f"{s:02X}" for b, s in sorted(copies.items())}
        # pack glyphs ending in shadow
        styles[f.style]["shadow"] = [f"{b:02X}" for b, g in sorted(slots.items()) if shadow_end(g.pixels, g.width)]
    from .editorfonts import sprite_measures
    code = next(iter(packs.values())).code if packs else None
    sprites = {name: m.data() for name, m in sprite_measures(prof, code).items()} if code else {}
    return {"main": main, "styles": styles, "codes": font_codes(prof), **({"sprites": sprites} if sprites else {})}


def glyph_art(prof, packs: dict) -> dict:
    """Pack glyph art per font style for the page's preview: cell geometry and each drawn byte's cell rows."""
    from ..lang.langpack import ART_CHARS, style_geometry
    out = {}
    for style, pack in packs.items():
        top, rows = style_geometry(prof, style)
        art = {f"{b:02X}": ["".join(ART_CHARS[g.pixels[y][x]] for x in range(g.width))
                            for y in range(top, top + rows)]
               for b, g in sorted(pack.glyph_slots().items())}
        out[style] = {"top": top, "rows": rows, "art": art}
    return out


def codec_data() -> dict:
    """The base character table and token names the page draws with."""
    from ..core.charmap import BASE_CHARS, CTRL_CODES, NAMED_BYTES, Charmap
    from ..check.layout import COLOR_LIKE
    return {"chars": {c: f"{b:02X}" for b, c in sorted(BASE_CHARS.items())},
            "named": {n: f"{b:02X}" for b, n in sorted(NAMED_BYTES.items())},
            "controls": {n: [f"{c:02X}", k] for c, (n, k) in sorted(CTRL_CODES.items())},
            "placeholders": {n: f"{b:02X}" for n, b in Charmap().placeholder_by_name.items()},
            "zero": COLOR_LIKE.pattern}


def split(doc: dict, version: str | None, glyphs: dict, problems: dict | None = None,
          terms: dict | None = None) -> tuple[dict, list[dict]]:
    """Split editor documents into indexes and entry chunks."""
    head = {k: v for k, v in doc.items() if k != "entries"}
    files: dict[str, int] = {}
    chunks: list[dict] = []
    rows = []
    short: dict[str, list[str]] = {}   # entry -> terms shown cut
    for t in (terms or {}).get("terms", []):
        for sid in t.get("shortened", []):
            short.setdefault(sid, []).append(t["id"])
    for e in doc["entries"]:
        if e["file"] not in files:
            files[e["file"]] = len(chunks)
            chunks.append({"schema": SCHEMA, "file": e["file"], "entries": []})
        n = files[e["file"]]
        chunks[n]["entries"].append(e)
        rows.append({"id": e["id"], "chunk": n, "status": e["status"],
                     **({"owner": e["owner"]} if e.get("owner") else {}),
                     "translation": e["translation"], "kind": (e["window"] or {}).get("kind"),
                     "reach": e["reach"]["class"],
                     "maps": e.get("context", {}).get("maps", []),
                     **({"shortened": short[e["id"]]} if e["id"] in short else {}),
                     "problems": (problems or {}).get(e["id"], [])})
    index = {**head, "version": version, "glyphs": glyphs, "codec": codec_data(),
             "chunks": [{"file": c["file"], "path": f"entries/{n}.json", "entries": len(c["entries"])}
                        for n, c in enumerate(chunks)],
             "list": rows}
    index["terms"] = {"path": "terms.json", "count": len(terms["terms"])} if terms else None
    return index, chunks


def data_problems(doc: dict, checker, english: dict[str, str] | None = None) -> dict[str, list[dict]]:
    """The check's problems of every translated entry (editorcheck.Checker); `english` overrides the English checked against."""
    english = english or {}
    return {e["id"]: checker.check({**e, "source_text": english[e["id"]]} if e["id"] in english else e,
                                   e["translation"])["problems"]
            for e in doc["entries"] if e["status"] not in ("untranslated", "nothing")}




def _check_source_fields(value, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            column = (key == "source_text" and isinstance(child, str)
                      and re.fullmatch(r"index\.notes\.note\[\d+\]\.citation\.columns", path))
            if key in SOURCE_TEXT_FIELDS and not column:
                raise ValueError(f"{child_path}: forbidden editor source-text field")
            _check_source_fields(child, child_path)
    elif isinstance(value, list):
        for n, child in enumerate(value):
            _check_source_fields(child, f"{path}[{n}]")


def write_split(out, index: dict, chunks: list[dict], terms: dict | None = None,
                key_only: bool = True, source: dict | None = None) -> None:
    """Write the split form into the directory `out` and its game parts (editorstore); `key_only` rejects source-text fields."""
    if key_only:
        _check_source_fields({k: v for k, v in index.items() if k not in ("glossary", "keyboard")}, "index")
        for chunk, description in zip(chunks, index["chunks"]):
            _check_source_fields(chunk, description["path"])
    from pathlib import Path
    from . import editorstore
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if index.get("font_sheets"):
        from .editorfonts import write_art
        write_art(out, index["language"], index["font_sheets"])
    editorstore.write(out, index, chunks, terms, source)


def write_keys(directory, prof):
    """Write directory/keys.json, the profile's alias table (inventory id -> key), or None without one."""
    import json
    from pathlib import Path
    aliases = keys.for_profile(prof)
    if not aliases:
        return None
    doc = {"schema": 1, "profile": prof.name, "rom_sha1": prof.sha1, "ids": {i: k for k, _, i in aliases.rows}}
    path = Path(directory) / "keys.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


def _check_output(root, target) -> None:
    """Refuse linked output paths and paths outside root."""
    from pathlib import Path
    root, target = Path(root), Path(target)
    base = root.resolve()

    def refuse(path):
        raise ValueError(f"{path}: symbolic link or outside the data directory")

    try:
        relative = target.absolute().relative_to(root.absolute())
    except ValueError:
        refuse(target)
    current = root.absolute()
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            refuse(current)
    if not target.resolve().is_relative_to(base):
        refuse(target)
    if target.is_dir():
        for path in target.rglob("*"):
            if path.is_symlink() or not path.resolve().is_relative_to(base):
                refuse(path)


def text_projects(registry) -> dict:
    """Select source texts from configured defaults, independently of exports."""
    from ..project import MANIFEST, Manifest
    projects = {}
    for project in registry.projects:
        language = (Manifest.read(project.root / MANIFEST).default_language if project.game
                    else registry.default_language)
        if project.profile not in projects or project.language == language:
            projects[project.profile] = project
    return projects


def write_languages(root, bundle: str = "gen3tl-check.zip", toolkit: str | None = None) -> dict:
    """Write root/languages.json from the split languages under `root`; `toolkit` defaults to toolkit_version."""
    import json
    from pathlib import Path
    root = Path(root)
    _check_output(root, root)
    from . import sitecatalog
    sitecatalog.write(root)   # the site's own catalog, an editor project too
    from ..profile.profile import Profile, profile_paths
    from ..project import REGISTRY, Registry, registry_root
    registry = registry_root()
    registered = Registry.load(registry) if (registry / REGISTRY).is_file() else None
    games = {p.profile for p in registered.projects} if registered is not None else None
    from . import editorstore
    editorstore.prune(root)
    langs = []
    seen = set()
    for p in sorted(root.rglob("index.json")):
        x = editorstore.identity(p)
        if games is not None and x["profile"] not in games and x["profile"] != sitecatalog.SITE:
            continue
        key = (x["profile"], x["language"])
        if key in seen:
            raise ValueError(f"duplicate editor project: {key[0]}/{key[1]}")
        seen.add(key)
        lang = {"code": x["language"], "index": p.relative_to(root).as_posix(), "profile": x["profile"],
                "entries": x["entries"], "progress": x["progress"]}
        texts = p.with_name("pokedex-texts.json")   # the Pokedex page's texts
        if texts.exists():
            lang["texts"] = texts.relative_to(root).as_posix()
        langs.append(lang)
    profiles = {}
    loaded = registered.profiles() if registered is not None else {
        prof.name: prof for prof in map(Profile.load, profile_paths())}
    for prof in loaded.values():
        profiles[prof.name] = {"id": prof.name, "name": prof["rom"]["name"],
                               "support": prof.get("support", {}).get("stage", "legacy"),
                               "engine": prof.get("engine", {}).get("family", "unspecified"),
                               "sha1": prof.sha1}
        for key in ("source_language", "source_locale"):
            if prof["rom"].get(key):
                profiles[prof.name][key] = prof["rom"][key]
        if "label" in prof.get("engine", {}):
            profiles[prof.name]["engine_label"] = prof["engine"]["label"]
        for key in ("links", "compatibility", "landing"):
            if key in prof.data:
                profiles[prof.name][key] = prof[key]
        if prof["rom"].get("patch_bases"):
            profiles[prof.name].update(size=prof["rom"]["size"], patch_bases=[
                {"name": base["name"], "sha1": base["sha1"]} for base in prof["rom"]["patch_bases"]])
    if any(lang["profile"] == sitecatalog.SITE for lang in langs):
        profiles[sitecatalog.SITE] = sitecatalog.profile_entry()
    for lang in langs:
        profiles.setdefault(lang["profile"], {"id": lang["profile"], "name": lang["profile"],
                                              "support": "legacy", "engine": "unspecified"})
    # the tracked Pokedex datasets ship with the editor data
    from ..data.pokedextexts import dataset_dir
    for name in sorted(profiles):
        source = dataset_dir(name) / "pokedex.json"
        if not source.exists():
            continue
        dest = root / "pokedex" / name / "pokedex.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        if not dest.exists() or dest.read_bytes() != data:
            dest.write_bytes(data)
        profiles[name]["pokedex"] = f"pokedex/{name}/pokedex.json"
        for companion in ("learnsets.json", "trainer-ivs.json"):
            auxiliary = source.with_name(companion)
            if auxiliary.exists():
                data = auxiliary.read_bytes()
                if not (dest.parent / companion).exists() or (dest.parent / companion).read_bytes() != data:
                    (dest.parent / companion).write_bytes(data)
        sprite_dir = source.with_name("sprites")
        if sprite_dir.exists():
            # copy only changed sprites: starts stay fast
            sync_tree(sprite_dir, root / "pokedex" / name / "sprites")
    for name, project in text_projects(registered).items() if registered else ():
        if name in profiles and "pokedex" in profiles[name]:
            texts = next((lang["texts"] for lang in langs if lang["profile"] == name
                          and lang["code"] == project.language and "texts" in lang),
                         f"{project.key}/pokedex-texts.json")
            profiles[name]["source_texts"] = {
                "code": project.language, "texts": texts}
    write_releases(root, registered)   # Keep releases within the selected registry.
    for path in sorted(root.glob("sources/*/catalog.json")):
        import hashlib
        source = json.loads(path.read_text(encoding="utf-8"))
        profile = profiles.get(source["profile"])
        if profile and profile["support"] in ("discovery", "catalog"):
            prof = loaded[source["profile"]]
            if source["rom_sha1"] != prof.sha1 or source["status"] != "unverified-candidates":
                raise ValueError(f"{path}: source identity or status differs")
            if hashlib.sha256(path.with_name("source.pot").read_bytes()).hexdigest() != source["source_sha256"]:
                raise ValueError(f"{path}: source checksum differs")
            profile["source_catalog"] = {
                "entries": source["entries"], "manifest": path.relative_to(root).as_posix(),
                "pot": path.with_name("source.pot").relative_to(root).as_posix(),
            }
    doc = {"schema": SCHEMA, "toolkit": toolkit or toolkit_version(), "bundle": bundle,
           "languages": langs, "profiles": list(profiles.values())}
    if registered and registered.site is not None:
        doc["site"] = registered.site
    site_registry = registry_of(doc)
    (root / "languages.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    # the site registry beside it
    (root / "registry.json").write_text(json.dumps(site_registry, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    return doc


REGISTRY_SCHEMA = 1
STAGES = ("translation", "catalog", "discovery", "legacy")


def write_releases(root, registry=None) -> int:
    """Copy each project's metadata and verified stored patches into the site data."""
    from pathlib import Path
    from ..project import MANIFEST, REGISTRY, Manifest, Registry, game_roots, registry_root
    from .patcher import copy_releases
    scope = registry_root()
    if registry is None and (scope / REGISTRY).is_file():
        registry = Registry.load(scope)
    roots = ([project.root for project in registry.projects if project.game] + [registry.root]
             if registry is not None else game_roots())
    return sum(copy_releases(project / "releases", Path(root) / "releases", Manifest.read(project / MANIFEST).profile)
               for project in dict.fromkeys(roots) if (project / MANIFEST).is_file())


def sync_tree(source, destination) -> int:
    """Copy the visible files of `source` that `destination` lacks or holds with another size."""
    import shutil
    from pathlib import Path
    source, destination = Path(source), Path(destination)
    copied = 0
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if not path.is_file() or any(part.startswith(".") for part in relative.parts):
            continue
        target = destination / relative
        if target.is_file() and target.stat().st_size == path.stat().st_size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied += 1
    return copied


def progress_of(rows: list[dict]) -> dict[str, int]:
    """Entry counts by status, in a fixed order; never-shown entries count under "never shown"."""
    from collections import Counter
    n = Counter(NEVER if r.get("reach") == NEVER else r["status"] for r in rows)
    return {k: n[k] for k in sorted(n)}


def registry_of(doc: dict, data: str = "./", only: list[str] | None = None,
                repository: str | None = None) -> dict:
    """The site registry of a languages manifest."""
    games = []
    for p in doc["profiles"]:
        if only is not None and p["id"] not in only or p.get("support") == "interface":   # the site's own catalog
            continue
        codes = sorted({x["code"] for x in doc["languages"] if x["profile"] == p["id"]})
        stage = p["support"] if p["support"] in STAGES else "legacy"
        games.append({"id": p["id"], "name": p["name"], "stage": stage, "engine": p.get("engine", "unspecified"),
                      "languages": codes, "data": data})
        for key in ("source_language", "engine_label", "links", "compatibility", "landing"):
            if key in p:
                games[-1][key] = p[key]
        if repository is not None:
            games[-1]["repository"] = repository
    missing = sorted(set(only or ()) - {g["id"] for g in games})
    if missing:
        raise ValueError(f"no profile {', '.join(missing)} in the languages manifest")
    games.sort(key=lambda g: (not g["languages"], STAGES.index(g["stage"]), g["name"].lower()))
    registry = {"schema": REGISTRY_SCHEMA, "games": games}
    if "site" in doc:
        registry["site"] = doc["site"]
    if games:
        registry["default"] = games[0]["id"]
    return validate_registry(registry)


REGISTRY_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*")
LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/(?!\.\.?$)[A-Za-z0-9._-]+")


def _registry_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _registry_url(value) -> bool:
    from urllib.parse import urlsplit
    if not _registry_text(value) or re.search(r"[\s\\\x00-\x1f\x7f]", value) or value.startswith("//"):
        return False
    try:
        url = urlsplit(value)
        if url.scheme:
            return url.scheme in ("http", "https") and bool(url.hostname) and url.port != 0
        return not url.netloc and ":" not in url.path.split("/")[0]
    except ValueError:
        return False


def _registry_fields(value, fields: dict, where: str) -> None:
    if not isinstance(value, dict) or set(value) - fields.keys():
        raise ValueError(f"registry: invalid {where}")
    for key, item in value.items():
        if not fields[key](item):
            raise ValueError(f"registry: invalid {where}.{key}")


def _registry_site(site) -> None:
    _registry_fields(site, {
        "name": _registry_text, "brand": _registry_url, "home": _registry_url,
        "repository": lambda v: v is None or isinstance(v, str) and REPOSITORY.fullmatch(v),
        "new_language_url": lambda v: v is None or _registry_url(v),
        "licenses": lambda v: isinstance(v, list),
    }, "site")
    for license in site.get("licenses", []):
        _registry_fields(license, {"label_key": _registry_text, "url": _registry_url}, "site.licenses")
        if set(license) != {"label_key", "url"}:
            raise ValueError("registry: invalid site.licenses")


def validate_registry(doc: dict) -> dict:
    """A registry the site reads (web/js/registry.js), else ValueError."""
    if not isinstance(doc, dict) or doc.get("schema") != REGISTRY_SCHEMA or not isinstance(doc.get("games"), list):
        raise ValueError("registry: unknown schema")
    if set(doc) - {"schema", "games", "default", "site", "include"}:
        raise ValueError(f"registry: unknown keys {sorted(set(doc) - {'schema', 'games', 'default', 'site', 'include'})}")
    if "include" in doc and (not isinstance(doc["include"], list) or not all(map(_registry_url, doc["include"]))):
        raise ValueError("registry: invalid include")
    if "site" in doc:
        _registry_site(doc["site"])
    seen = set()
    for g in doc["games"]:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str) or not REGISTRY_ID.fullmatch(g["id"]):
            raise ValueError("registry: invalid game id")
        if g["id"] in seen:
            raise ValueError(f"registry: duplicate game {g['id']}")
        seen.add(g["id"])
        if set(g) - {"id", "name", "stage", "engine", "languages", "data", "repository", "source_language",
                     "engine_label", "links", "compatibility", "landing"}:
            raise ValueError(f"registry: {g['id']} has unknown keys")
        if "engine_label" in g and not _registry_text(g["engine_label"]):
            raise ValueError(f"registry: {g['id']} has an invalid engine label")
        if "links" in g:
            _registry_fields(g["links"], dict.fromkeys(("project", "new_language", "content_license"),
                                                     lambda v: v is None or _registry_url(v)), "links")
        if "compatibility" in g:
            _registry_fields(g["compatibility"], dict.fromkeys(("changed_bytes", "saves", "link_play"),
                                                             _registry_text), "compatibility")
        if "landing" in g:
            _registry_fields(g["landing"], dict.fromkeys(("intro", "editor", "changes_title", "changes",
                                                       "review_title", "review"), _registry_text), "landing")
        if "repository" in g and not (isinstance(g["repository"], str) and REPOSITORY.fullmatch(g["repository"])):
            raise ValueError(f"registry: {g['id']} has an invalid repository")
        if "source_language" in g and not (isinstance(g["source_language"], str)
                                           and LANGUAGE_TAG.fullmatch(g["source_language"])):
            raise ValueError(f"registry: {g['id']} has an invalid source language")
        if not isinstance(g.get("name"), str) or not g["name"].strip():
            raise ValueError(f"registry: {g['id']} has no name")
        if g.get("stage") not in STAGES:
            raise ValueError(f"registry: {g['id']} has an unknown stage")
        codes = g.get("languages")
        if not isinstance(codes, list) or not all(isinstance(c, str) and REGISTRY_ID.fullmatch(c) for c in codes):
            raise ValueError(f"registry: {g['id']} has invalid languages")
        if not isinstance(g.get("data"), str) or not isinstance(g.get("engine", ""), str):
            raise ValueError(f"registry: {g['id']} has no data URL")
    if "default" in doc and doc["default"] not in seen:
        raise ValueError("registry: the default game is not listed")
    return doc


def map_order(prof, catalog: dict, inv: dict | None, key, source: dict, entries: list[dict]) -> dict[str, int]:
    """Keep map positions independent of translated names."""
    maps = prof.get("maps", {})
    base = maps.get("mapsec_names")
    if base is None:
        return {}
    count = maps.get("mapsec_count", 255)
    positions = {}
    for row in (inv or {}).get("entries", []):
        for ref in row.get("refs", []):
            offset = ref["at"] - base
            if 0 <= offset < count * 4 and offset % 4 == 0:
                digest = row.get("source_sha256") or text_digest(row["text"])
                positions[digest] = min(positions.get(digest, count), offset // 4)
    names = {}
    for sid, (_, entry) in catalog.items():
        digest = source[sid][0] if entry.keyed else text_digest(entry.msgid)
        if digest in positions:
            names[key(sid)] = positions[digest]
    files = {}
    for entry in entries:
        ctx = entry["context"]
        # Complete single-map contexts identify their catalog group.
        if len(ctx["maps"]) == 1 and ctx["uses"] <= 4:
            position = names.get(ctx["maps"][0])
            if position is not None:
                files.setdefault(entry["file"], set()).add(position)
    return {file: next(iter(positions)) for file, positions in files.items() if len(positions) == 1}


@language.code_selected
def build(lang: str, prof, catalog: dict, limits: dict, measure, cm, packs: dict, terms=None,
          notes: dict[str, list[str]] | None = None, windows=None, toolkit: str | None = None,
          reach: dict | None = None, rules: dict[str, list[dict]] | None = None,
          wording: dict[str, list[dict]] | None = None, inv: dict | None = None,
          pokedex: dict | None = None, cursor: list[list[int]] | None = None, source: dict | None = None,
          contexts: dict | None = None, note_root=None, wording_rows=None, fill_facts=None) -> dict:
    """The editor document of a language."""
    from ..build.keyboard import resolve_catalog
    from .editorfonts import font_sheets
    resolve_catalog(catalog, prof)
    from ..check.rules import context_rules
    from ..catalog.guidance import entry_notes
    from ..lang.fill import entry_matcher
    from ..lang.termbase import TermMatcher
    facts = source or {}
    if any(e.keyed and sid not in facts for sid, (_, e) in catalog.items()):
        raise ValueError("entries without their English need the snapshot's source rows")

    def english(sid: str, e) -> str:   # the English, else its digest; empty for none
        return e.msgid if not e.keyed else "" if facts[sid][0] == BLANK else facts[sid][0]
    guidance = entry_notes(prof, catalog, {sid: row[0] for sid, row in facts.items()})
    aliases = keys.for_profile(prof)   # the document names entries by key
    from ..catalog import entrynotes
    entrynotes.refresh(catalog, lang, aliases, {sid: row[0] for sid, row in facts.items()})
    key = aliases.key
    links = context_links(contexts, catalog, aliases) if contexts is not None else None
    names = name_keys(catalog, key)
    matcher = TermMatcher(terms) if terms else None
    by_id = {t.id: t for t in terms or []}
    approved = approved_by_english(catalog, english)
    main = prof.get("font_style", {}).get("style")
    used_terms: dict[str, dict] = {}
    entries = []
    estimates = frozenset(prof.get("policy_unverified", {}).get("placeholders", ()))
    from ..check import namepairs
    shown = {sid: e.msgstr or e.msgid for sid, (p, e) in catalog.items() if e.msgstr or not e.keyed}
    pairs = namepairs.results(prof, shown, windows.measures if windows is not None else {}, pokedex)
    translations = {e.msgstr for _, e in catalog.values() if e.msgstr}

    def translated(k: str) -> bool:
        found = catalog.get(aliases.entry_id(k))
        return bool(found and found[1].msgstr)
    for sid, (p, e) in catalog.items():
        lim = limits.get(sid)
        tokens = list(facts[sid][1]) if e.keyed else BRACE.findall(e.msgid)
        ctx = text_free(context(list(e.extracted) + (notes or {}).get(sid, []), catalog, aliases,
                                None if e.keyed else e.msgid), names)
        if e.keyed:
            original = facts[sid][5]["context"]
            speakers = [{k: v for k, v in sp.items() if v is not None} for sp in original["speakers"]]
            for speaker in ctx["speakers"]:
                add(speakers, speaker)
            merge_identity(ctx, original)
            ctx.update({**{k: v for k, v in original.items() if k != "identity"}, "speakers": speakers})
        if contexts is not None:
            merge_identity(ctx, contexts.get("strings", {}).get(sid, {}))
        if links is not None:
            ctx.update(links.get(sid, {"words": [], "hosts": []}))
        ctx["inserted"] = [{**x, "source": rekeyed(x["source"], aliases)}
                           for x in inserted("".join(tokens), lim, measure, cm, estimates)]
        scoped_matcher = entry_matcher(e, matcher) if not e.keyed else None
        ids = list(dict.fromkeys(t.id for t in scoped_matcher.in_source(e.msgid))) if scoped_matcher else []
        if e.keyed:
            ids = [tid for tid in facts[sid][5]["terms"] if tid in by_id]
        for t in ids:
            used_terms.setdefault(t, term_data(by_id[t]))
        same = [{"translation": tr, "ids": [key(i) for i in src if i != sid]}
                for tr, src in approved.get(english(sid, e), {}).items()]
        where = reach_data(sid, e.extracted, reach)
        first, last = context_rules(ctx, where)
        pair = next((x for x in (namepairs.partner(r, sid, key) for r in pairs) if x), None)
        listed = first + (rules or {}).get(sid, []) + last + (wording or {}).get(sid, [])
        entries.append({
            "id": key(sid), "file": catalog_name(p), "tokens": tokens, "translation": e.msgstr,
            "flags": list(e.flags), "status": status(e, english(sid, e) == "" if e.keyed else None),
            "context": ctx, **({"owner": e.owner} if e.owner else {}),
            "window": keyed_window(window(sid, lim, main, windows, partner_text(pair, translated), translations),
                                   aliases), "terms": ids,
            "approved": [x for x in same if x["ids"]], "note": e.source_note, "reach": where,
            "translator_notes": guidance.get(sid, []),
            "rules": [{**r, "text": aliases.rekey_text(r["text"])} for r in listed],
        })
    doc = {
        "schema": SCHEMA, "toolkit": toolkit or toolkit_version(),   # version and commit
        "language": lang, "profile": prof.name, "rom": {"sha1": prof.sha1},
        "map_order": map_order(prof, catalog, inv, key, facts, entries),
        "where_texts": where_texts(prof, aliases),
        **({"unverified": {k: [key(x) for x in v] if k == "window_width" else v
                           for k, v in prof["policy_unverified"].items()}} if prof.get("policy_unverified") else {}),
        "language_rules": {"fold": language.load(lang).data.get("fold", {}),
                           "stand_ins": language.load(lang).stand_ins()},
        "engine_rules": rules_data(prof, entries, key=key, inv=inv),
        "term_columns": {key: key
                         for key in ("translation", "label", "gender", "declension", "forms", "notes", "sources")},
        # the check's rules and the typography reasons in site languages
        "check_rules": keyed_rules({k: prof.get("qa", {})[k] for k in (*RULES, "reasons") if k in prof.get("qa", {})},
                                   aliases),
        "fonts": fonts_data(prof, packs),
        "font_sheets": font_sheets(prof, lang),
        "charmap": {c: f"{b:02X}" for c, b in packs[main].chars.items()
                    if packs[main].sources[c] != "base"} if main in packs else {},
        "placeholders": {"default": measure.default_ph, "widths": dict(measure.ph)},
        "fill_sources": substitution_data(prof, inv, catalog, cm, key, fill_facts),
        # glossary file order: the order of the check's warnings
        "glossary": {t.id: used_terms[t.id] for t in terms or [] if t.id in used_terms},
        "entries": entries,
        "graphics": graphics_data(prof.get("graphics"), prof.project_root, getattr(prof, "release_path", None),
                                  catalog, key),
        "keyboard": keyboard_data(prof, catalog, inv, limits, measure, cm, packs.get(main), key, cursor)
        if inv is not None else None,
    }
    attach_notes(doc, prof, catalog, terms, folder=note_root, wording=wording_rows,
                 digests={sid: row[0] for sid, row in facts.items()})
    return doc
