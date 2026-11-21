"""Editor submissions: a GitHub issue that holds an editor export."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import tempfile
import tomllib
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..core.terminal import line
from .exportenvelope import validate
from .sitecatalog import SITE

EXPORT_HEADING = "Editor export"
FILE_HEADING = "Export file"
PICTURES_HEADING = "Pictures"
NO_RESPONSE = "_No response_"
LIMIT = 5 * 1024 * 1024
PICTURE_LIMIT = 64 * 1024   # an area form is mostly index 0
MARKER = "<!-- gen3tl-submission -->"
ATTACHMENT = re.compile(r"https://github\.com/(?:user-attachments/files|[A-Za-z0-9-]+/[A-Za-z0-9._-]+/files)"
                        r"/\d+/[^\s()<>\[\]\"']+")
IMAGE = re.compile(r"https://github\.com/(?:user-attachments/assets|[A-Za-z0-9-]+/[A-Za-z0-9._-]+/assets/\d+)"
                   r"/[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
FENCE = re.compile(r"(`{3,}|~{3,})[^`]*")
TABLE = re.compile(r"\[\[?[A-Za-z0-9_.\-\"' ]+\]\]?\s*(#.*)?")   # a TOML table header
BLOCK = re.compile(r"^(`{3,})[^\n`]*\n(.*?)\n\1[ \t]*$", re.S | re.M)


class Invalid(ValueError):
    """A submission refused, with the reason for its author."""


@dataclass
class Submission:
    profile: str
    language: str
    sha256: str
    files: list[Path] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: int = 0
    notes: int = 0
    remarks: str = ""   # the contributor's notes for the reviewers
    terms: list[str] = field(default_factory=list)   # one line per applied term edit
    pictures: list[str] = field(default_factory=list)   # one line per applied picture
    policy: Path | None = None
    renamed: int = 0   # lines the term edits rename
    glossary: Path | None = None
    index: Path | None = None
    unknown_index: list[tuple[str, str]] = field(default_factory=list)   # entry id, term id of each unknown row written
    engine_rules: list[str] = field(default_factory=list)
    rule_reports: list[dict] = field(default_factory=list)
    note_changes: list[dict] = field(default_factory=list)
    note_files: list[Path] = field(default_factory=list)
    note_subjects: dict = field(default_factory=dict)
    submitter: str = ""   # the issue author, for new notes
    submitted: str = ""


def sections(body: str) -> dict[str, str]:
    """Issue form answers by their heading."""
    out, current = {}, None
    for line in body.replace("\r\n", "\n").split("\n"):
        heading = re.fullmatch(r"### +(.+?)\s*", line)
        if heading:
            current = heading.group(1)
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in out.items()}


def unfenced(text: str) -> str:
    """The content of fenced blocks, outermost first."""
    while True:
        lines = text.strip().split("\n")
        opening = FENCE.fullmatch(lines[0].strip())
        if len(lines) < 2 or not opening or lines[-1].strip() != opening.group(1):
            return text.strip()
        text = "\n".join(lines[1:-1])


def locate(body: str) -> tuple[str | None, str | None]:
    """The inline export and the attachment URL of an issue body."""
    body = body.replace("\r\n", "\n")
    parts = sections(body)
    if EXPORT_HEADING in parts or FILE_HEADING in parts:
        inline = unfenced(parts.get(EXPORT_HEADING, ""))
        attached = parts.get(FILE_HEADING, "")
    else:
        block = BLOCK.search(body)
        inline = block.group(2).strip() if block else ""
        attached = body
    link = ATTACHMENT.search(attached)
    return (inline if inline and inline != NO_RESPONSE else None), (link.group(0) if link else None)


def picture_links(body: str) -> list[str]:
    """The image attachments of the Pictures field, else of a body without form headings."""
    body = body.replace("\r\n", "\n")
    parts = sections(body)
    if PICTURES_HEADING in parts:
        body = parts[PICTURES_HEADING]
    elif EXPORT_HEADING in parts or FILE_HEADING in parts:
        return []
    return list(dict.fromkeys(IMAGE.findall(body)))


def _size(limit: int) -> str:
    return f"{limit // (1024 * 1024)} MiB" if limit >= 1024 * 1024 else f"{limit // 1024} KiB"


def download(url: str, limit: int = LIMIT, what: str = "the attached export file") -> bytes:
    """An attached file, at most `limit` bytes."""
    request = urllib.request.Request(url, headers={"User-Agent": "gen3tl-submission"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(limit + 1)
    except OSError as exc:
        raise Invalid(f"{what} cannot be downloaded ({exc})") from exc
    if len(data) > limit:
        raise Invalid(f"{what} exceeds {_size(limit)}")
    return data


def document(body: str, fetch=None) -> bytes:
    """The export bytes an issue body holds or links."""
    inline, url = locate(body)
    if inline and url:
        raise Invalid("the issue holds an inline export and an attached file; keep one of them")
    if inline:
        return inline.encode("utf-8")
    if url:
        return (fetch or download)(url)
    raise Invalid(f"no editor export found; fill the {EXPORT_HEADING} field or attach the export file")


def parse(raw: bytes) -> dict:
    """The export object, duplicate fields refused."""
    from .editorimport import _object
    try:
        doc = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object)
    except (UnicodeDecodeError, ValueError) as exc:
        raise Invalid(f"the export is not valid JSON ({exc})") from exc
    except RecursionError:
        raise Invalid("the export nests too deeply") from None
    try:
        return validate(doc)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc


def _control(text: str) -> bool:
    return any(ord(c) < 32 and c not in "\n\t" for c in text)


def remarks(doc: dict) -> str:
    """The document's notes for the reviewers, "" without."""
    notes = doc.get("notes")
    if notes is None:
        return ""
    if not isinstance(notes, str):
        raise Invalid("the export's notes must be a string")
    return notes.replace("\r\n", "\n").strip()


def fenced(text: str) -> str:
    """`text` in a fenced block: no mention, reference or closing keyword."""
    fence = "`" * max(3, 1 + max(map(len, re.findall(r"`+", text)), default=0))
    return f"{fence}text\n{text}\n{fence}"


def notes_section(result: Submission) -> str:
    """The notes for the pull request and the issue comment, "" without."""
    return f"Notes for the reviewers:\n\n{fenced(result.remarks)}\n" if result.remarks else ""


def _texts(name: str, value) -> list[str] | None:
    """The texts of a glossary field value, None where its type is wrong (forms: an object of texts)."""
    if name != "forms":
        return [value] if isinstance(value, str) else None
    if isinstance(value, dict) and all(isinstance(x, str) for x in (*value, *value.values())):
        return [*value, *value.values()]
    return None


def term_records(doc: dict) -> list[dict]:
    """The document's term edits with their shape checked."""
    records = doc.get("terms")
    if records is None:
        return []
    if not isinstance(records, list):
        raise Invalid("the export's terms must be a list")
    seen = set()
    for r in records:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            raise Invalid("each term edit needs a string id")
        tid = r["id"]
        if tid in seen:
            raise Invalid(f"duplicate term edit: {tid}")
        seen.add(tid)
        for part in ("base", "changes"):
            if not isinstance(r.get(part), dict):
                raise Invalid(f"{tid}: {part} must be an object")
            for name, value in r[part].items():
                texts = _texts(name, value)
                if texts is None:
                    raise Invalid(f"{tid}: {name} must be {'an object of texts' if name == 'forms' else 'a text'}")
                # the glossary file separates by tab, line, | and =
                bad = any(_control(x) or "\t" in x or "\n" in x for x in texts)
                if name == "forms":
                    bad = bad or any("|" in x for x in texts) or any("=" in k for k in value)
                if bad:
                    raise Invalid(f"{tid}: unsupported character in {name}")
        include = r.get("include")
        if include is not None and not (isinstance(include, list) and all(isinstance(x, str) for x in include)):
            raise Invalid(f"{tid}: include must be a list of entry ids")
    return records


def _code(text: str) -> str:
    """`text` as inline code."""
    tick = "`" * (1 + max(map(len, re.findall(r"`+", text)), default=0))
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{tick}{pad}{text}{pad}{tick}"


def index_named(project, current: dict[str, str], renames, aliases):
    """Entry id, term id -> whether the English names the term, from the project's term index (termbase.index_names)."""
    from ..lang import termbase as tb
    path = project.translation / "term-index.tsv"
    if not path.is_file():
        raise Invalid(f"term edits read the uses of a term with the English or the term index; "
                      f"{project.key} has neither {path.relative_to(project.root).as_posix()} nor its English")
    named = tb.index_names(path)
    stale = sorted({f"{r.old.id} in {aliases.key(sid)}" for r in renames for sid, text in current.items()
                    if text and (aliases.key(sid), r.old.id) not in named and tb.find_forms(text, r.old)})
    if stale:
        raise Invalid(f"the term index does not tell whether the English names {', '.join(stale[:5])}"
                      + (f" and {len(stale) - 5} more" if len(stale) > 5 else "")
                      + "; regenerate it with the English: gen3tl lang index")
    return lambda sid, term: named.get((aliases.key(sid), term), False)


def apply_terms(project, records: list[dict], texts: dict[str, str], english: dict[str, str] | None, aliases,
                result: "Submission", current: dict[str, str] | None = None) -> tuple[list, dict[str, dict]]:
    """The glossary after the term edits and the lines their renames change, by entry id ({after, note})."""
    from ..lang import termbase as tb
    from ..lang.renamecheck import apply_renames
    from ..lang.termedit import apply_changes, values
    if not project.glossary.is_file():
        raise Invalid(f"{project.key} has no glossary; its submissions carry no term edits")
    terms = tb.load(project.glossary)
    for r in records:
        for x in r.get("include") or []:
            if aliases.entry_id(x) not in texts:
                raise Invalid(f"{r['id']}: include {x} must be an entry of the selected catalog")
    # include names entries as the catalog keys them
    mapped = [{**r, "include": [aliases.entry_id(x) for x in r.get("include") or []]} for r in records]
    edited, renames, refused = apply_changes(terms, mapped)
    if refused:
        raise Invalid("; ".join(refused))
    named = index_named(project, current or {}, renames, aliases) if english is None else None
    changes = apply_renames({k: v for k, v in texts.items() if v}, renames, english, tb.Senses(terms), named)
    lines = {}
    for sid, c in changes.items():
        ambiguous = [a for s in c["steps"] for a in s["ambiguous"]]
        note = "check case: " + "; ".join(ambiguous) if ambiguous else ""
        lines[sid] = {"after": c["after"], "note": note}
    before, after = {t.id: t for t in terms}, {t.id: t for t in edited}
    for r in records:
        old, new = values(before[r["id"]]), values(after[r["id"]])
        fields = [f for f in new if new[f] != old[f]]
        if not fields:
            continue
        renamed = sum(any(s["term"] == r["id"] for s in c["steps"]) for c in changes.values())
        # long and structured fields by name
        shown = [f if f in ("forms", "notes", "sources") else f"{f} {_code(old[f])} → {_code(new[f])}" for f in fields]
        english = before[r["id"]].source_text
        result.terms.append(f"`{r['id']}`{f' ({english})' if english else ''}: {', '.join(shown)}; "
                            f"renamed lines: {renamed}")
    result.renamed = len(changes)
    return edited, lines


def picture_records(doc: dict) -> list[dict]:
    """The document's pictures with their shape checked: id, areas, tiles, and the PNG inline or attached."""
    records = doc.get("pictures")
    if records is None:
        return []
    if not isinstance(records, list):
        raise Invalid("the export's pictures must be a list")
    seen = set()
    for r in records:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            raise Invalid("each picture needs a string id")
        gid = r["id"]
        if gid in seen:
            raise Invalid(f"duplicate picture: {gid}")
        seen.add(gid)
        areas = r.get("areas")
        if not isinstance(areas, list) or not all(isinstance(a, list) and len(a) == 4
                                                  and all(type(v) is int for v in a) for a in areas):
            raise Invalid(f"{gid}: areas must be a list of [x, y, w, h] integers")
        tiles = r.get("tiles")
        if tiles is not None and (type(tiles) is not int or tiles < 1):
            raise Invalid(f"{gid}: tiles must be a positive integer or null")
        sha, png = r.get("sha256"), r.get("png")
        if sha is not None and not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
            raise Invalid(f"{gid}: sha256 must be 64 lowercase hexadecimal digits")
        if png is not None and not isinstance(png, str):
            raise Invalid(f"{gid}: png must be base64 text")
        if sha is None and png is None:
            raise Invalid(f"{gid}: the picture needs its sha256 and an attachment, or its png")
    return records


def picture_files(records: list[dict], links: list[str]) -> dict[str, bytes]:
    """The PNG of each picture by id: its png, else the attachment with its sha256."""
    attached = [r for r in records if r.get("png") is None]
    if len(links) > len(attached):
        raise Invalid(f"the {PICTURES_HEADING} field holds {len(links)} attachments for "
                      f"{len(attached)} attached pictures")
    files = {}
    for url in links:
        data = download(url, PICTURE_LIMIT, "an attached picture")
        files.setdefault(hashlib.sha256(data).hexdigest(), data)
    out = {}
    for r in records:
        gid = r["id"]
        if r.get("png") is None:
            if r["sha256"] not in files:
                raise Invalid(f"{gid}: no attachment of the {PICTURES_HEADING} field has the SHA-256 {r['sha256']}; "
                              f"attach {gid}.png there")
            out[gid] = files[r["sha256"]]
            continue
        try:
            data = base64.b64decode(r["png"], validate=True)
        except (binascii.Error, ValueError):
            raise Invalid(f"{gid}: png is not base64") from None
        if len(data) > PICTURE_LIMIT:
            raise Invalid(f"{gid}: the picture exceeds {_size(PICTURE_LIMIT)}")
        if r.get("sha256") not in (None, hashlib.sha256(data).hexdigest()):
            raise Invalid(f"{gid}: png differs from its sha256")
        out[gid] = data
    unused = set(files) - {hashlib.sha256(d).hexdigest() for d in out.values()}
    if unused:
        raise Invalid(f"{len(unused)} attachment(s) of the {PICTURES_HEADING} field match no picture of the export")
    return out


def policy_with(text: str, entry: dict) -> str:
    """The policy `text` with `entry` as the [[graphics.edit]] of its id; every other line kept."""
    lines = text.split("\n")
    block = ["[[graphics.edit]]", *(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in entry.items())]
    starts = [i for i, x in enumerate(lines) if TABLE.fullmatch(x.strip())] + [len(lines)]
    out = None
    for i, end in zip(starts, starts[1:]):
        if lines[i].strip() != "[[graphics.edit]]":
            continue
        # comments right above the next table are its own
        while end > i + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
            end -= 1
        try:
            gid = tomllib.loads("\n".join(lines[i + 1:end])).get("id")
        except tomllib.TOMLDecodeError:
            continue
        if gid == entry["id"]:
            out = "\n".join(lines[:i] + block + lines[end:])
            break
    if out is None:
        out = text.rstrip("\n") + "\n\n" + "\n".join(block) + "\n"
    # the parsed policy differs by this entry alone
    before, after = tomllib.loads(text), tomllib.loads(out)
    edits = before.get("graphics", {}).get("edit", [])
    at = next((n for n, e in enumerate(edits) if e.get("id") == entry["id"]), len(edits))
    want = {**before, "graphics": {**before.get("graphics", {}), "edit": [*edits[:at], entry, *edits[at + 1:]]}}
    if after != want:
        raise Invalid(f"the policy cannot take the picture {entry['id']} in place")
    return out


def apply_pictures(project, base, prof, records: list[dict], files: dict[str, bytes],
                   result: "Submission") -> tuple[str, list[tuple[Path, bytes]]]:
    """The policy text with a [[graphics.edit]] per changed picture and the area forms to write."""
    from ..build.graphics import GraphicsConfig, GraphicsError, append_problem, area_check, area_size
    from ..check.policy import PolicyError, effective
    from ..check.render import png_image
    if project.policy is None or not project.policy.is_file():
        raise Invalid(f"{project.key} has no policy file; its submissions carry no pictures")
    cfg = GraphicsConfig.from_profile(prof)
    current = {e["id"]: e for e in prof.get("graphics", {}).get("edit", [])}
    text = project.policy.read_text(encoding="utf-8")
    changed = []
    for r in records:
        gid, data = r["id"], files[r["id"]]
        try:
            g = cfg.graphic(gid)
        except GraphicsError as exc:
            raise Invalid(str(exc)) from None
        old = current.get(gid)
        if old is not None and "label" in old:
            raise Invalid(f"{gid}: the policy draws it from {old['label']}; a picture cannot replace it")
        tiles = g.tiles if r.get("tiles") is None else r["tiles"]
        if append_problem(g, tiles):
            raise Invalid(f"{gid}: {append_problem(g, tiles)}")
        kept = project.root / old["png"] if old is not None else None
        if old is not None and old.get("form", "sheet") == "areas" and old["areas"] == r["areas"] \
                and old.get("tiles", g.tiles) == tiles and kept.is_file() and kept.read_bytes() == data:
            continue
        path = project.translation / "graphics/areas" / f"{gid}.png"
        entry = {"id": gid, "png": path.relative_to(project.root).as_posix(), "form": "areas", "areas": r["areas"]}
        if tiles != g.tiles:
            entry["tiles"] = tiles
        text = policy_with(text, entry)
        changed.append((r, g, tiles, path, old))
    # the policy bounds the tile count before any decoding
    with tempfile.TemporaryDirectory() as tmp:
        check = Path(tmp) / project.policy.name
        check.write_text(text, encoding="utf-8")
        try:
            effective(base, project.language, check, project.root)
        except PolicyError as exc:
            raise Invalid(f"the policy refuses the pictures: {exc}") from None
    writes = []
    for r, g, tiles, path, old in changed:
        gid, data = r["id"], files[r["id"]]
        width, height = area_size(cfg, g, tiles)
        if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise Invalid(f"{gid}: the picture is not a PNG file")
        # header first, so other sheet sizes are not decoded
        if (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")) != (width, height):
            raise Invalid(f"{gid}: the picture is not {width}x{height}, the size of its area form")
        try:
            img = png_image(data, f"{gid}.png")
        except Exception as exc:   # truncated chunks or rows
            raise Invalid(f"{gid}: unreadable PNG ({exc})") from None
        findings = area_check(cfg, gid, img, r["areas"], tiles)
        if findings:
            more = f" ({len(findings) - 1} more findings)" if len(findings) > 1 else ""
            raise Invalid(f"{gid}: {findings[0]}{more}")
        writes.append((path, data))
        state = "replaces the project's edit" if old is not None else "new edit"
        count = len(r["areas"])
        grown = f", {tiles} tiles ({tiles - g.tiles} appended)" if tiles != g.tiles else ""
        result.pictures.append(f"`{gid}`{f' ({g.text})' if g.text else ''}: {state}, "
                               f"{count} {'area' if count == 1 else 'areas'}{grown}")
    return text, writes


def apply(path: Path, registry, check: bool = False, links: list[str] = (), *,
          author: str | None = None, created: str | None = None) -> Submission:
    """Validate the export at `path` with the picture attachments `links`; write it unless `check`."""
    from ..catalog.catalog import load_catalog
    from .editorimport import read_export
    from ..build.keyboard import reject_owned
    from ..catalog.po import read, write
    from ..lang import termbase as tb
    from . import notechanges
    doc = parse(path.read_bytes())
    notechanges.records(doc)
    profile, language = doc.get("profile"), doc.get("language")
    notes = remarks(doc)
    for key in ("glyphs", "keyboard"):
        if doc.get(key, []) not in ([], None):
            raise Invalid(f"the export holds {key}; glyph and keyboard edits belong to the toolkit repository "
                          "(haritos90/gen3tl), whose submission workflow imports the JSON file")
    if profile == SITE:
        for key in ("pictures", "terms", "engine_rules", "note_changes"):
            if doc.get(key, []) not in ([], None):
                raise Invalid(f"the export holds {key}; a site submission carries text entries only")
        result = apply_site(path, doc, registry, check)
        result.remarks = notes
        return result
    terms, pictures = term_records(doc), picture_records(doc)
    project = next((p for p in registry.projects if (p.profile, p.language) == (profile, language)), None)
    if project is None:
        raise Invalid(f"{profile}/{language} is not a translation project of this repository")
    base = project.load_profile()
    problem = base.command_problem("web submission")
    if problem:
        raise Invalid(problem)
    prof = project.effective(base)
    project.validate(prof)
    files = picture_files(pictures, list(links))
    with project.activate(prof):
        catalog = load_catalog(project.catalog, english=False)   # the English where build/catalog.pot holds it
        rule_context = ()
        if doc.get("engine_rules"):
            from .policyrules import MeasurementsRequired, patches
            try:
                patches(prof, doc["engine_rules"])
            except MeasurementsRequired:
                from . import editorsnapshot
                try:
                    snapshot = editorsnapshot.load(project.snapshot)
                except (OSError, ValueError) as exc:
                    raise Invalid("custom unit validation needs a current editor snapshot") from exc
                editorsnapshot.validate(snapshot, prof, language, catalog, project.catalog)
                measure, cm, _ = editorsnapshot.measures(prof, language, snapshot["fonts"])
                main = snapshot["fonts"][prof["font_style"]["style"]]["widths"]
                rule_context = cm, {byte for byte in cm.decode_table if byte < len(main)}, measure
        data = read_export(path, prof.name, language, prof.sha1, catalog,
                           carried=("terms", "pictures", "engine_rules", "note_changes"), graphics_profile=prof,
                           rule_profile=prof, rule_context=rule_context)
        records = data["document"]["entries"]   # named by key (read_export)
        from ..catalog import keys
        aliases = keys.load(prof.sha1)
        reject_owned([aliases.entry_id(r["id"]) for r in records], prof)
        result = Submission(profile, language, data["sha256"], remarks=notes)
        result.note_changes = notechanges.validate(doc, project, prof, catalog, author=author, created=created)
        if result.note_changes:
            result.submitter, result.submitted = author, created
            result.note_subjects = notechanges.subject_texts(project, prof, catalog, result.note_changes)
        texts = {eid: entry.msgstr for eid, (_, entry) in catalog.items()}   # by entry id, edits applied
        for record in records:
            sid, text = record["id"], record["translation"]
            if _control(text):
                raise Invalid(f"{sid}: unsupported control character in the translation")
            entry = catalog[aliases.entry_id(sid)][1]
            if record.get("note", entry.source_note) != entry.source_note:
                result.notes += 1
            if (text, record["state"]) == (entry.msgstr, entry.state):
                result.unchanged += 1
                continue
            texts[aliases.entry_id(sid)] = text
            result.changed.append(sid)
        lines, glossary = {}, None
        if terms:
            # without English sources, the term index tells the uses
            english = None if any(entry.keyed for _, entry in catalog.values()) else {
                eid: entry.msgid for eid, (_, entry) in catalog.items()}
            current = {eid: entry.msgstr for eid, (_, entry) in catalog.items()}
            glossary, lines = apply_terms(project, terms, texts, english, aliases, result, current)
            reject_owned(lines, prof)
        policy, writes = apply_pictures(project, base, prof, pictures, files, result) if pictures else (None, [])
        proposed_prof = prof
        if data["rule_patches"]:
            from ..check.policy import effective, patch_rules
            if project.policy is None:
                raise Invalid("engine rules require a project policy")
            policy = patch_rules(policy if policy is not None else project.policy.read_text(encoding="utf-8"),
                                 data["rule_patches"])
            with tempfile.TemporaryDirectory() as tmp:
                staged = Path(tmp) / "policy.toml"
                staged.write_text(policy, encoding="utf-8")
                proposed_prof = effective(base, language, staged, project.root)
            result.engine_rules = [patch["id"] for patch in data["rule_patches"]]
        application = notechanges.plan(result.note_changes, project, prof, catalog, glossary if terms else None)
        if not (result.changed or result.terms or result.pictures or result.engine_rules or result.note_changes):
            raise Invalid("every translation equals the current catalog; nothing to submit" if not terms and not pictures
                          else "every change equals the current catalog, glossary and policy; nothing to submit")
        states = {aliases.entry_id(r["id"]): r["state"] for r in records}
        targets = {}
        for eid in [aliases.entry_id(sid) for sid in result.changed] + list(lines):
            renamed = lines.get(eid)
            targets.setdefault(catalog[eid][0], {})[aliases.key(eid)] = (
                renamed["after"] if renamed else texts[eid],
                "text" if renamed else states[eid], renamed["note"] if renamed else None)
        if prof.language_rules:
            from ..check.prcheck import rule_changes
            proposed_catalog = deepcopy(catalog)
            for eid, (entry_path, entry) in proposed_catalog.items():
                changed = targets.get(entry_path, {}).get(aliases.key(eid))
                if changed:
                    entry.set_translation(changed[0], changed[1], keep_approved=True)
            result.rule_reports = rule_changes(prof, proposed_prof, catalog, proposed_catalog, context=rule_context)
            result.engine_rules = list(dict.fromkeys([*result.engine_rules,
                                                      *(report["id"] for report in result.rule_reports)]))
        result.note_files = [path.resolve().relative_to(registry.root) for path in application.files]
        if application.files and not check:   # first: a refused note file leaves no other write
            application.write(project, prof)
        for entry_path in sorted(targets):
            result.files.append(entry_path.resolve().relative_to(registry.root))
            if check:
                continue
            header, entries = read(entry_path)
            for entry in entries:
                text, state, note = targets[entry_path].get(aliases.key(entry.msgctxt), (None, None, None))
                if text is None:
                    continue
                entry.set_translation(text, state, keep_approved=True)
                if note:
                    entry.translator.append(note)
            write(entry_path, entries, header)
        if result.terms:
            result.glossary = project.glossary.resolve().relative_to(registry.root)
            result.files.append(result.glossary)
            if not check and application.terms is None:
                tb.save(project.glossary, glossary)
        if (targets or result.terms) and project.glossary.is_file():
            index = project.translation / "term-index.tsv"
            result.index = index.resolve().relative_to(registry.root)
            result.files.append(result.index)
            if not check:   # without the English, the facts of the index it replaces
                current = load_catalog(project.catalog, english=False)
                told = tb.told_by(index, aliases.key) if any(e.keyed for _, e in current.values()) else None
                rows = tb.build_index(tb.load(project.glossary), current, told)
                tb.save_index(index, rows)
                result.unknown_index = [(aliases.key(r["string"]), r["term"])
                                        for r in rows if r["source_names_term"] == tb.UNKNOWN]
        if result.pictures or data["rule_patches"]:
            result.policy = project.policy.resolve().relative_to(registry.root)
            result.files += [result.policy, *(p.resolve().relative_to(registry.root) for p, _ in writes)]
            if not check:
                for picture, data in writes:
                    picture.parent.mkdir(parents=True, exist_ok=True)
                    picture.write_bytes(data)
                project.policy.write_text(policy, encoding="utf-8")
        result.files += [path for path in result.note_files if path not in result.files]
    return result


def apply_site(path: Path, doc: dict, registry, check: bool = False) -> Submission:
    """Validate an export of the site project; write it into the site catalog unless `check`."""
    import hashlib
    from . import sitecatalog
    applied = sitecatalog.apply_export(doc, write=False)
    if not applied.changed:
        raise Invalid("every translation equals the current catalog; nothing to submit")
    if not applied.path.resolve().is_relative_to(registry.root):
        raise Invalid(f"the site catalog {applied.path.name} is not part of this repository")
    if not check:
        sitecatalog.apply_export(doc)
    return Submission(SITE, doc["language"], hashlib.sha256(path.read_bytes()).hexdigest(),
                      [applied.path.resolve().relative_to(registry.root)], applied.changed, applied.unchanged,
                      applied.notes)


def refusal(reason: str, inert: bool = False) -> str:
    """The refusal comment; an `inert` reason stays in a fenced block."""
    stated = f"the note changes are invalid:\n\n{fenced(reason)}\n" if inert else f"{reason}.\n"
    return (f"{MARKER}\nSubmission not applied: {stated}\n"
            "Edit the issue to correct it; the check reruns on every edit.\n" +
            'How a submission is made: CONTRIBUTING.md of this repository.\n')


def pull_request(number: int, result: Submission) -> str:
    if result.profile == SITE:
        head = [f"Site interface texts submitted with the editor in #{number}.", "",
                f"- Site catalog: `{result.files[0].as_posix()}`"]
        checks = "Every changed text keeps the holes of its key; the site catalog check renders the catalog."
    else:
        head = [f"Translations submitted with the editor in #{number}.", "",
                f"- Project: `{result.profile}/{result.language}`"]
        checks = "The pull request checks report the fit of every changed line."
    lines = head
    if result.changed or result.unchanged:
        lines += [f"- Changed entries: {len(result.changed)}; new translations are translated, "
                  "approved texts stay approved",
                  f"- Unchanged entries in the export: {result.unchanged}"]
    if result.terms:
        lines += [f"- Term edits: {len(result.terms)}, applied to `{result.glossary.as_posix()}`",
                  f"- Lines the term edits rename: {result.renamed}"]
    if result.pictures:
        lines.append(f"- Pictures: {len(result.pictures)}, `[[graphics.edit]]` entries of "
                     f"`{result.policy.as_posix()}` with their area forms")
    if result.engine_rules:
        lines.append(f"- Engine rules: {', '.join(_code(rule) for rule in result.engine_rules)}")
        lines.append("- Rule approval: combined catalog and policy behavior requires verification before merge.")
    if result.note_changes:
        lines.append(f"- Note changes: {len(result.note_changes)}, applied with status proposed; "
                     "review accepts them before merge")
    lines.append(f"- Export SHA-256: `{result.sha256}`")
    if result.notes:
        lines.append(f"- Source notes that differ from the catalog: {result.notes}, kept as in the catalog")
    if result.terms:
        lines += ["", "Term edits:", "", *(f"- {term}" for term in result.terms)]
    if result.pictures:
        lines += ["", "Pictures:", "", *(f"- {picture}" for picture in result.pictures)]
        checks += (" Each area form passed the pixel check against the graphics inventory without the ROM; "
                   "`build gfx-check` and the build check it against the base ROM.")
    if result.index:
        if result.unknown_index:
            lines += ["", "Unknown term index rows:", "",
                      *(f"- {_code(sid)} ({_code(term)})" for sid, term in result.unknown_index)]
        else:
            lines += ["", "Unknown term index rows: none."]
    if result.rule_reports:
        from ..check.prcheck import rules_report
        lines += ["", rules_report(result.rule_reports)]
    if result.note_changes:
        from .notechanges import provenance
        lines += ["", provenance(result.note_changes, submitter=result.submitter, submitted=result.submitted,
                                 sha256=result.sha256, files=[path.as_posix() for path in result.note_files],
                                 subjects=result.note_subjects)]
    lines += ["", checks, "", f"Resolves #{number}", ""]
    notes = notes_section(result)
    return "\n".join(lines) + (f"\n{notes}" if notes else "")


def run(event: Path, out: Path, registry, check: bool = False, fetch=None) -> int:
    """Answer a submission issue: files for the workflow in `out`."""
    event, out = Path(event).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    issue = json.loads(event.read_text(encoding="utf-8")).get("issue") or {}
    number = issue.get("number")
    if type(number) is not int or number < 1:
        raise ValueError(f"{event}: the event names no issue")
    login = (issue.get("user") or {}).get("login")
    if not isinstance(login, str) or not login:
        raise ValueError(f"{event}: the event names no issue author")
    (out / "issue").write_text(f"{number}\n", encoding="utf-8")
    body = issue.get("body") or ""
    try:
        body = body if isinstance(body, str) else ""
        raw = document(body, fetch)
        (out / "export.json").write_bytes(raw)
        result = apply(out / "export.json", registry, check, picture_links(body), author=login,
                       created=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    except ValueError as exc:
        from .notechanges import Refused
        inert = isinstance(exc, Refused)
        reason = str(exc) if inert else str(exc).rstrip(".")
        (out / "comment.md").write_text(refusal(reason, inert), encoding="utf-8")
        (out / "status").write_text("invalid\n", encoding="utf-8")
        line("issue", f"#{number}")
        line("status", "invalid")
        line("error", " ".join(reason.split()) if inert else reason)
        return 1
    count, terms, pictures = len(result.changed), len(result.terms), len(result.pictures)
    rules = len(result.engine_rules)
    proposals = len(result.note_changes)
    site = result.profile == SITE   # the site catalog is named by its file
    named = result.files[0].as_posix() if site else f"{result.profile}/{result.language}"
    parts = [f"{count} changed {'entry' if count == 1 else 'entries'}" if count or not (terms or pictures or rules or proposals) else "",
             f"{terms} term {'edit' if terms == 1 else 'edits'}" if terms else "",
             f"{pictures} {'picture' if pictures == 1 else 'pictures'}" if pictures else "",
             f"{rules} engine {'rule' if rules == 1 else 'rules'}" if rules else "",
             f"{proposals} note {'proposal' if proposals == 1 else 'proposals'}" if proposals else ""]
    parts = [part for part in parts if part]
    listed = " and ".join(parts) if len(parts) < 3 else ", ".join(parts[:-1]) + " and " + parts[-1]
    summary = f"{listed} of {'the site catalog ' if site else ''}{named}"
    scope, label = ("site", "Site catalog") if site else ("translation", "Project")
    body = [f"- {label}: {named}", f"- Entries: {count}"] + ([f"- Terms: {terms}"] if terms else []) \
        + ([f"- Pictures: {pictures}"] if pictures else []) + ([f"- Engine rules: {rules}"] if rules else []) \
        + ([f"- Note proposals: {proposals}"] if proposals else [])
    files = {"status": "valid", "profile": result.profile, "language": result.language, "summary": summary,
             "rules": str(rules), "note-proposals": str(proposals),
             "index": result.index.as_posix() if result.index else "",
             "title": f"Editor submission #{number}: {named}",
             "files": "\n".join(path.as_posix() for path in result.files),
             "commit.txt": f"fix({scope}): apply editor submission (#{number})\n\n" + "\n".join(body)}
    for name, text in files.items():
        (out / name).write_text(text + "\n", encoding="utf-8")
    (out / "pr.md").write_text(pull_request(number, result), encoding="utf-8")
    (out / "notes.md").write_text(notes_section(result), encoding="utf-8")
    action = "checked" if check else "applied"
    line("issue", f"#{number}")
    line("status", action)
    line("changed", f"{count} entries")
    if terms:
        line("terms", f"{terms} terms")
        line("renamed", f"{result.renamed} lines")
    if pictures:
        line("pictures", f"{pictures} pictures")
    if rules:
        line("rules", rules)
    if proposals:
        line("note proposals", proposals)
    line("project", f"{result.profile}/{result.language}")
    for path in result.files:
        line("file", path.as_posix())
    return 0
