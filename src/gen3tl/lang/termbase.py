"""Termbase: the glossary with inflected forms, the term index and term renames."""

from __future__ import annotations

from . import language

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

COLUMNS = ["id", "category", "source_text", "translation", "label", "gender", "declension", "forms",
           "sources", "variants", "confidence", "address", "notes", "status", "note_ids"]
CASES = ["nomn", "gent", "datv", "accs", "ablt", "loct"]
NUMBERS = ["sing", "plur"]
WORD = re.compile(r"[^\W_]+(?:[’'\-][^\W_]+)*")


@dataclass
class Term:
    """One glossary row (glossary.tsv of the language folder)."""
    id: str
    category: str
    source_text: str
    translation: str
    label: str = ""
    gender: str = ""
    declension: str = "auto"
    forms: dict[str, str] = field(default_factory=dict)
    sources: str = ""
    variants: str = ""
    confidence: str = ""
    address: str = ""
    notes: str = ""
    status: str = "proposed"
    note_ids: tuple[str, ...] = ()
    note_records: tuple[dict, ...] = field(default=(), repr=False)
    related_note_records: tuple[dict, ...] = field(default=(), repr=False)
    research_findings: dict = field(default_factory=dict, repr=False, compare=False)
    notes_cell: str | None = field(default=None, repr=False)

    @property
    def editable_notes(self) -> str | None:
        return self.notes_cell if self.note_ids else self.notes

    def refresh_notes(self) -> None:
        self.notes = "\n".join(text for text in (self.notes_cell,
            _notes_view(self.note_records, self.related_note_records, self.research_findings)) if text)

    def all_forms(self) -> dict[str, str]:
        f = dict(self.forms)
        f.setdefault("nomn.sing", self.translation)
        if self.label:
            f.setdefault("label", self.label)
        return f


def load(path: Path) -> list[Term]:
    terms = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            forms = {}
            for item in (row.get("forms") or "").split("|"):
                if "=" in item:
                    k, v = item.split("=", 1)
                    forms[k.strip()] = v.strip()
            note_ids = tuple(row["note_ids"].split("|")) if row.get("note_ids") else ()
            terms.append(Term(**{c: row.get(c, "") or "" for c in COLUMNS if c not in ("forms", "note_ids")},
                              forms=forms, note_ids=note_ids))
    selected = _glossary_notes(path, terms)
    for term, records, (related, findings) in zip(terms, selected, _related_notes(path, selected)):
        if term.note_ids:
            if term.notes and not all_references(records):
                raise ValueError(f"{path}: {term.id}: both legacy and structured notes")
            term.notes_cell = term.notes if all_references(records) else None
            term.note_records = records
            term.related_note_records = related
            term.research_findings = findings
            term.refresh_notes()
    return terms


def save(path: Path, terms: list[Term], *, write_notes: bool = False) -> None:
    from ..core import notes as structured

    document = None
    if write_notes:
        document = {"schema": structured.SCHEMA,
                    "note": [note for term in terms for note in term.note_records]}
        structured.validate(document)
        notes_path = path.parent / "notes" / "terms.toml"
        if notes_path.exists():
            previous = structured.read_notes(notes_path)
            proposed = {note["id"]: note for note in document["note"]}
            missing = {note["id"] for note in previous["note"]} - proposed.keys()
            if missing:
                raise ValueError(f"{notes_path}: unreferenced notes: {', '.join(sorted(missing))}")
            from . import research
            for note in previous["note"]:
                if not research.is_term_note(note) and proposed[note["id"]] != note:
                    raise ValueError(f"{notes_path}: {note['id']}: structured notes changed; reload the glossary")
    selected = _glossary_notes(path, terms, document)
    for term, records, (related, findings) in zip(terms, selected, _related_notes(path, selected)):
        if term.note_records and term.note_records != records:
            raise ValueError(f"{path}: {term.id}: structured notes changed; reload the glossary")
        if term.related_note_records != related or term.research_findings != findings:
            raise ValueError(f"{path}: {term.id}: research notes changed; reload the glossary")
        if term.notes_cell is not None and not all_references(records):
            raise ValueError(f"{path}: {term.id}: both legacy and structured notes")
        expected = "\n".join(text for text in (term.notes_cell, _notes_view(records, related, findings)) if text)
        if term.note_ids and term.notes != (expected if term.note_records else ""):
            raise ValueError(f"{path}: {term.id}: edit structured notes in notes/terms.toml")
    if write_notes and document["note"]:
        structured.write_notes(path.parent / "notes" / "terms.toml", document)
    columns = COLUMNS if any(term.note_ids for term in terms) else COLUMNS[:-1]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(columns)
        for t in terms:
            values = {c: getattr(t, c) for c in columns}
            values["forms"] = "|".join(f"{k}={v}" for k, v in t.forms.items())
            values["note_ids"] = "|".join(t.note_ids)
            values["notes"] = t.editable_notes or ""
            row = [values[c] for c in columns]
            w.writerow(row)


def _glossary_notes(path: Path, terms: list[Term], document: dict | None = None) -> list[tuple[dict, ...]]:
    from ..core import notes as structured

    notes_path = path.parent / "notes" / "terms.toml"
    if document is None:
        document = structured.read_notes(notes_path) if notes_path.exists() else {"note": []}
    by_id = {note["id"]: note for note in document["note"]}
    targets = {}
    for term in terms:
        targets[term.id] = targets.get(term.id, 0) + 1
    for note in by_id.values():
        where = f"{notes_path}: {note['id']}"
        if note["subject"]["kind"] != "term":
            raise ValueError(f"{where}: expected a term subject")
        count = targets.get(note["subject"]["id"], 0)
        if count != 1:
            raise ValueError(f"{where}: {'unknown' if count == 0 else 'ambiguous'} term {note['subject']['id']}")
        if note["scope"] != {"kind": "language", "language": language.current().code}:
            raise ValueError(f"{where}: expected language scope {language.current().code}")
    selected, referenced = [], set()
    for term in terms:
        where = f"{path}: {term.id}"
        if (not isinstance(term.note_ids, (tuple, list))
                or any(not isinstance(nid, str) or not nid or nid != nid.strip()
                       or any(char in nid for char in "|\t\r\n") for nid in term.note_ids)):
            raise ValueError(f"{where}: invalid note_ids")
        if len(set(term.note_ids)) != len(term.note_ids):
            raise ValueError(f"{where}: duplicate note_ids")
        records = []
        for nid in term.note_ids:
            if nid not in by_id:
                raise ValueError(f"{where}: unknown note {nid}")
            note = by_id[nid]
            if note["subject"]["id"] != term.id:
                raise ValueError(f"{where}: note {nid} targets a different term")
            records.append(note)
            referenced.add(nid)
        selected.append(tuple(records))
    unused = by_id.keys() - referenced
    if unused:
        raise ValueError(f"{notes_path}: unreferenced notes: {', '.join(sorted(unused))}")
    return selected


def _related_notes(path: Path, selected: list[tuple[dict, ...]]) -> list[tuple[tuple[dict, ...], dict]]:
    from . import research

    rows = None
    result = []
    local_ids = {note["id"] for records in selected for note in records}
    for records in selected:
        related = {}
        findings = {}
        for note in records:
            if not research.is_term_note(note):
                continue
            if rows is None:
                rows = research.index(path.parent / "research")
                if local_ids.intersection(nid for row in rows.values() for nid in row.note_ids):
                    raise ValueError(f"{path}: duplicate glossary and research note identities")
            targets = note.get("related_subjects", [])
            if len(targets) != 1 or targets[0]["kind"] != "research":
                raise ValueError(f"{path}: {note['id']}: expected one research subject")
            row = rows.get(targets[0]["id"])
            if row is None:
                raise ValueError(f"{path}: {note['id']}: unknown research record {targets[0]['id']}")
            if tuple(note.get("related_notes", [])) != row.note_ids:
                raise ValueError(f"{path}: {note['id']}: research references changed; regenerate the glossary notes")
            related.update((record["id"], record) for record in row.note_records)
            findings[row.record_id] = row
        result.append((tuple(related.values()), findings))
    return result


def all_references(records: tuple[dict, ...] | list[dict]) -> bool:
    from .research import is_reference
    return bool(records) and all(is_reference(record) for record in records)


def attach_notes(term: Term, records: list[dict], related: tuple[dict, ...] = (), findings: dict | None = None) -> None:
    """Update generated references while retaining independent term notes."""
    from uuid import NAMESPACE_URL, uuid5

    kept = list(term.note_records)
    cell = term.editable_notes
    combined = {record["id"]: record for record in (*kept, *records)}
    term.notes_cell = (cell or "") if all_references(list(combined.values())) else None
    if cell and term.notes_cell is None:
        kept.append({
            "id": f"term-legacy:{uuid5(NAMESPACE_URL, term.id)}",
            "subject": {"kind": "term", "id": term.id},
            "scope": {"kind": "language", "language": language.current().code},
            "facts": [cell], "status": "unreviewed",
        })
    by_id = {record["id"]: record for record in kept}
    by_id.update((record["id"], record) for record in records)
    linked = {record["id"]: record for record in term.related_note_records}
    linked.update((record["id"], record) for record in related)
    references = dict.fromkeys(nid for record in by_id.values() for nid in record.get("related_notes", []))
    term.note_records = tuple(by_id.values())
    term.note_ids = tuple(by_id)
    term.related_note_records = tuple(linked[nid] for nid in references if nid in linked)
    term.research_findings.update(findings or {})
    term.refresh_notes()


def note_facts(record: dict, findings: dict) -> list[str]:
    from . import research
    if research.is_term_note(record):
        subject = record["related_subjects"][0]["id"]
        if subject in findings:
            return research.term_facts(record, findings[subject])
    return record.get("facts", [])


def _notes_view(records: tuple[dict, ...], related: tuple[dict, ...] = (), findings: dict | None = None) -> str:
    from ..core import notes as structured

    def details(value):
        if isinstance(value, dict):
            return "; ".join(f"{key.replace('_', ' ')}: {details(item)}" for key, item in value.items())
        if isinstance(value, list):
            return "\n".join(details(item) for item in value)
        return str(value)

    blocks = []
    active_links = {nid for record in records if record["status"] in ("unreviewed", "accepted")
                    for nid in record.get("related_notes", [])}
    for record in (*records, *(note for note in related if note["id"] in active_links)):
        if record["status"] not in ("unreviewed", "accepted"):
            continue
        prose = [item for key in structured.PROSE_FIELDS
                 for item in (note_facts(record, findings or {}) if key == "facts" else record.get(key, []))]
        metadata = {key: record[key] for key in ("status", "sources", "evidence", "related_notes",
                                                 "related_subjects", *structured.ATTRIBUTION_FIELDS) if key in record}
        blocks.append("\n".join([*prose, details(metadata)]))
    return "\n".join(blocks)


# morphology comes from the language pack

def morph():
    return language.current().analyzer()


def generate_forms(t: Term) -> dict[str, str]:
    return language.current().generate_forms(t)


def _match_case(src: str, word: str, lower: bool = False) -> str:
    """Copy capitalisation from src to word, per hyphen-separated part."""
    sp, wp = src.split("-"), word.split("-")
    if len(sp) != len(wp):
        sp, wp = [src], [word]
    out = []
    for a, b in zip(sp, wp):
        if a.isupper() and len(a) > 1:
            out.append(b.upper())
        elif a[:1].isupper():
            out.append(b[:1].upper() + b[1:])
        elif lower and a[:1].islower():
            out.append(b[:1].lower() + b[1:])
        else:
            out.append(b)
    return "-".join(out)


# categories written as common nouns in running text:
# - trainer classes, roles (section 6); weather, dynamax, items
# - a lowercase occurrence keeps the replacement lowercase
PROSE_LOWERCASE = {"trainer-class", "term"}



def _norm(s: str) -> str:
    return language.current().normalize(s.lower())


def strip_codes(text: str) -> str:
    return re.sub(r"\{[^}]*\}|\\[nlp]", " ", text)


# a word start on raw text:
# - no letter or digit before; an escape may precede
# - escapes: lower case only, not an escape's own letter
RAW_START = r"(?<!\\)(?:(?<![^\W_])|(?<=(?-i:\\[nlp])))"


@lru_cache(maxsize=1 << 16)
def form_pattern(form: str) -> re.Pattern:
    """The pattern of a normalised form as a whole word, compiled once."""
    return re.compile(r"(?<![^\W_])" + re.escape(form) + r"(?![^\W_])")


def find_forms(text: str, t: Term) -> list[tuple[str, str, int]]:
    """(form key, matched text, position) for every occurrence of a term form."""
    plain = strip_codes(text)
    low = _norm(plain)
    hits = []
    taken: list[tuple[int, int]] = []
    forms = sorted(t.all_forms().items(), key=lambda kv: -len(kv[1]))
    for key, form in forms:
        if not form:
            continue
        pat = form_pattern(_norm(form))
        for m in pat.finditer(low):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((key, plain[m.start():m.end()], m.start()))
    return sorted(hits, key=lambda h: h[2])


@lru_cache(maxsize=1 << 14)
def regular_english_forms(name: str) -> tuple[str, ...]:
    """The literal name and its regular final-word plural."""
    match = re.search(r"[A-Za-z]+$", name)
    if match is None:
        return (name,)
    word = match.group()
    suffix = "s"
    stem = name
    if re.search(r"[^aeiou]y$", word, re.I):
        stem, suffix = name[:-1], "ies"
    elif re.search(r"(?:s|x|z|ch|sh)$", word, re.I):
        suffix = "es"
    return name, stem + (suffix.upper() if word.isupper() else suffix)


@lru_cache(maxsize=1 << 14)
def _english_source_pattern(name: str) -> re.Pattern:
    forms = "|".join(re.escape(form) for form in regular_english_forms(name))
    return re.compile(r"(?<![A-Za-z])(?:" + forms + r")(?![A-Za-z])", re.I)


def english_present(msgid: str, t: Term) -> bool:
    if not t.source_text:
        return False
    return _english_source_pattern(t.source_text).search(strip_codes(msgid)) is not None


# a term's uses among its forms' occurrences

def _fold(s: str) -> str:
    return language.current().normalize(s)


def spelled(match: str, form: str, start: bool) -> bool:
    """Whether `match` is written as `form`, or as a lower-case form capitalised at a sentence start."""
    a, b = _fold(match), _fold(form)
    return a == b or (start and b[:1].islower() and a == b[:1].upper() + b[1:])


OPENING = "“«\"‘'([-—– "
CLOSING = "”»\"’')"


def sentence_start(before: str) -> bool:
    """Whether a word after `before` starts a sentence; `before` is plain text with ¶ for a new box."""
    s = before.rstrip(OPENING + "\n\t")
    s = s.rstrip(CLOSING)
    return not s or s[-1] in ".!?…¶"


def marked_plain(text: str) -> str:
    """strip_codes with ¶ for \\p: the same positions, new boxes kept."""
    return strip_codes(text.replace("\\p", "¶"))


def _english_text(s: str) -> str:
    """English for names: line breaks as spaces, é as e."""
    return re.sub(r"\\[nlp]", " ", s).replace("é", "e").replace("É", "E")


def _english_alternative(e: str) -> str:
    """The pattern of one English name with a plural or verb ending."""
    words = _english_text(e).split()
    body = r"\s+".join(re.escape(w) for w in words)
    last = words[-1] if words else ""
    if not last[-1:].isalpha():
        return body
    if re.search(r"[^aeiou]y$", last, re.I):
        return body[:-1] + r"(?:ies|ied|y(?:s|ing|ed)?)"
    if last[-1:].lower() == "e":
        return body + r"(?:s|d)?|" + body[:-1] + "ing"
    return body + r"(?:s|es|ing|ed|" + re.escape(last[-1]) + r"(?:ing|ed))?"


@lru_cache(maxsize=1 << 14)
def english_pattern(names: tuple[str, ...]) -> re.Pattern | None:
    """The English names of a term as one pattern (Senses.names), None without one."""
    alts = sorted({_english_alternative(e) for e in names if e.strip()}, key=len, reverse=True)
    if not alts:
        return None
    return re.compile(r"(?<![A-Za-z])(?:" + "|".join(alts) + r")(?![A-Za-z])", re.I)


USE, FORM_ONLY, OTHER = "use", "form only", "other row"
UNKNOWN = "unknown"   # a sense the English decides, read without it


class Senses:
    """The glossary's spellings and English names, to tell a term's uses from same-spelled words."""

    def __init__(self, terms: list[Term]):
        # normalised form -> term id -> forms
        self.spellings: dict[str, dict[str, set[str]]] = {}
        self.source_text: dict[str, list[str]] = {}   # id without ~N -> English
        self.category: dict[str, str] = {}
        self._enclosing: dict[tuple, list[re.Pattern]] = {}
        for t in terms:
            self.add(t)

    def add(self, t: Term) -> None:
        self._enclosing.clear()
        self.category[t.id] = t.category
        for f in t.all_forms().values():
            if f:
                self.spellings.setdefault(_norm(f), {}).setdefault(t.id, set()).add(f)
        if t.source_text and t.source_text not in self.source_text.setdefault(t.id.split("~")[0], []):
            self.source_text[t.id.split("~")[0]].append(t.source_text)

    def enclosing_patterns(self, t: Term) -> list[re.Pattern]:
        """Protect forms belonging to longer glossary terms."""
        forms = tuple(sorted({_norm(f) for f in t.all_forms().values() if f}))
        key = (t.id, forms)
        if key not in self._enclosing:
            patterns = []
            for surface, rows in self.spellings.items():
                if not any(tid != t.id for tid in rows):
                    continue
                if not any(len(f) < len(surface) and form_pattern(f).search(surface) for f in forms):
                    continue
                pattern = language.current().pattern(surface).replace(r"\ ", r"(?: |(?-i:\\[nlp]))")
                patterns.append(re.compile(RAW_START + "(?=(" + pattern + r")(?![^\W_]))", re.I))
            self._enclosing[key] = patterns
        return self._enclosing[key]

    def names(self, msgid: str, t: Term) -> bool:
        """Whether the English names t (Senses)."""
        alts = tuple(self.source_text.get(t.id.split("~")[0], [])) or ((t.source_text,) if t.source_text else ())
        pat = english_pattern(alts)
        return pat is not None and pat.search(_english_text(msgid)) is not None

    def kind(self, t: Term, match: str, start: bool, named) -> str:
        """USE, FORM_ONLY or OTHER of an occurrence `match` of a form of t."""
        rows = self.spellings.get(_norm(match), {})
        forms = rows.get(t.id) or {f for f in t.all_forms().values() if f and _norm(f) == _norm(match)}
        own = [f for f in forms if spelled(match, f, start)]
        others = [i for i, fs in rows.items() if i != t.id and any(spelled(match, f, start) for f in fs)]
        if others and not own:
            return OTHER
        if match[:1].islower() and forms and all(f[:1].isupper() for f in forms):
            return FORM_ONLY
        if named() if callable(named) else named:
            return USE
        item = self.category.get(t.id, t.category) == "item"
        if own and not others and any(f[:1].islower() or (item and not start) for f in own):
            return USE
        return FORM_ONLY

    def told_uses(self, text: str, t: Term, told: bool | None) -> list[tuple[str, str, int, str]]:
        """uses, with whether the English names t given in its place; None leaves an English-decided occurrence UNKNOWN."""
        plain = marked_plain(text)
        out = []
        for key, match, pos in find_forms(text, t):
            start = sentence_start(plain[:pos])
            kinds = {self.kind(t, match, start, named) for named in ((True, False) if told is None else (told,))}
            out.append((key, match, pos, kinds.pop() if len(kinds) == 1 else UNKNOWN))
        return out

    def uses(self, text: str, t: Term, msgid: str) -> list[tuple[str, str, int, str]]:
        """find_forms of t in `text` with their kind: (key, match, position, USE, FORM_ONLY or OTHER)."""
        plain = marked_plain(text)
        named: list[bool] = []

        def name() -> bool:   # the English read once, where needed
            if not named:
                named.append(self.names(msgid, t))
            return named[0]
        out = []
        for key, match, pos in find_forms(text, t):
            out.append((key, match, pos, self.kind(t, match, sentence_start(plain[:pos]), name)))
        return out


# a run of word characters at a match boundary
RUN = re.compile(r"[^\W_]+")


def build_index(terms: list[Term], catalog, told=None) -> list[dict]:
    """Distinct glossary term occurrences, in first-match order."""
    from ..catalog.po import catalog_name
    # a form matches only at whole-run boundaries:
    # - a match starts and ends on a boundary
    # - strings are tried only against forms starting with their runs
    by_run: dict[str, set[int]] = {}
    always: set[int] = set()   # a form without letters or digits can match anywhere
    for i, t in enumerate(terms):
        for form in t.all_forms().values():
            if not form:
                continue
            m = RUN.search(_norm(form))
            if m:
                by_run.setdefault(m.group(0), set()).add(i)
            else:
                always.add(i)
    senses = Senses(terms)
    rows = []
    seen = set()
    for sid, (path, e) in sorted(catalog.items()):
        if not e.msgstr:
            continue
        cand = set(always)
        for r in set(RUN.findall(_norm(strip_codes(e.msgstr)))):
            cand |= by_run.get(r, set())
        for t in (terms[i] for i in sorted(cand)):
            if e.keyed:   # without the English: what the index it replaces tells
                named = told(sid, t.id) if told is not None else None
                found = senses.told_uses(e.msgstr, t, named)
            else:
                found = senses.uses(e.msgstr, t, e.msgid)
                named = senses.names(e.msgid, t) if found else None
            for key, text, pos, kind in found:
                row = {"term": t.id, "string": sid, "form": key, "text": text,
                       "source_names_term": UNKNOWN if named is None else "yes" if named else "no",
                       "use": "yes" if kind == USE else kind, "file": catalog_name(path)}
                identity = tuple(row.values())
                if identity not in seen:
                    seen.add(identity)
                    rows.append(row)
    return rows


def save_index(path: Path, rows: list[dict]) -> None:
    cols = ["term", "string", "form", "text", "source_names_term", "use", "file"]
    from ..catalog import keys
    key = keys.for_profile(None).key   # the index names entries by key
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows({**r, "string": key(r["string"])} for r in rows)


def index_names(path: Path) -> dict[tuple[str, str], bool]:
    """(string key, term id) -> whether the English names the term, of a term index (build_index)."""
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return {(r["string"], r["term"]): r["source_names_term"] == "yes"
                for r in csv.DictReader(fh, delimiter="\t")
                if r.get("string") and r.get("term") and r.get("source_names_term") in ("yes", "no")}


def told_by(path: Path, key):
    """Entry id, term id -> whether the English names the term, as the term index at `path` records it."""
    facts = index_names(path) if Path(path).is_file() else {}
    return lambda sid, term: facts.get((key(sid), term))


def missing_terms(terms: list[Term], msgid: str, msgstr: str) -> list[Term]:
    """Glossary terms whose English occurs in the source but no translated form in the translation."""
    return [t for t in terms if t.source_text and english_present(msgid, t) and not find_forms(msgstr, t)]


def rename_in_text(text: str, old: Term, new_forms: dict[str, str], english: str | None = None,
                   senses: Senses | None = None, include: bool = False,
                   named: bool | None = None) -> tuple[str, int, list[str]]:
    """Replace every form of `old` in `text` with the same form of the new term."""
    count = 0
    ambiguous: list[str] = []
    out = text
    old_forms = old.all_forms()
    sensed = english is not None or named is not None   # only the uses
    if sensed and senses is None:
        senses = Senses([old])
    enclosing = senses.enclosing_patterns(old) if senses is not None else []
    told: list[bool] = [] if named is None else [named]

    def name() -> bool:   # the English read once, where needed
        if not told:
            told.append(senses.names(english, old))
        return told[0]
    by_surface: dict[str, list[str]] = {}
    for key, form in old_forms.items():
        if form:
            by_surface.setdefault(_norm(form), []).append(key)
    order = {f"{c}.{n}": i for i, (n, c) in enumerate((n, c) for n in NUMBERS for c in CASES)}
    for surface, keys in sorted(by_surface.items(), key=lambda kv: -len(kv[0])):
        keys = sorted((k for k in keys if k in new_forms), key=lambda k: order.get(k, 99))
        if not keys:
            continue
        choice = new_forms[keys[0]]
        alts = {new_forms[k] for k in keys}
        # a space of the form also matches a break code
        pattern = language.current().pattern(surface).replace(r"\ ", r"( |(?-i:\\[nlp]))")
        pat = re.compile(RAW_START + pattern
                         + r"(?![^\W_])", re.I)
        protected = [m.span(1) for outer in enclosing for m in outer.finditer(out)]

        def sub(m):
            nonlocal count
            if any(a <= m.start() and m.end() <= b and (a < m.start() or m.end() < b)
                   for a, b in protected):
                return m.group(0)
            plain = strip_codes(m.group(0))
            if sensed:
                start = sentence_start(marked_plain(m.string[:m.start()]))
                kind = senses.kind(old, plain, start, name)
                if kind != USE and not (kind == FORM_ONLY and include):
                    return m.group(0)
            words = _match_case(plain, choice, old.category in PROSE_LOWERCASE).split(" ")
            separators = [" "] * (len(words) - 1)
            for i, separator in enumerate(m.groups()):
                if separator != " ":
                    if i >= len(separators):
                        raise ValueError(f"cannot preserve {separator} in {m.group(0)!r} -> {choice!r}: "
                                         "replacement has fewer word boundaries")
                    separators[i] = separator
            count += 1
            if len(alts) > 1:
                ambiguous.append(f"{m.group(0)} -> {' / '.join(sorted(alts))}")
            # break codes stay between the words
            return words[0] + "".join(sep + word for sep, word in zip(separators, words[1:]))
        out = pat.sub(sub, out)
    return out, count, ambiguous


class TermMatcher:
    """Find which glossary terms occur in English text (one combined case-sensitive pattern)."""

    # categories that only fill a name table:
    # their English ("Egg", "Mouse") is ordinary running text
    TABLE_ONLY = {"dex-category"}

    def __init__(self, terms: list[Term]):
        self.by_id = {term.id: term for term in terms}
        self.by_english: dict[str, list[Term]] = {}
        for t in terms:
            if t.source_text and len(t.source_text) > 2 and t.category not in self.TABLE_ONLY:
                for name in regular_english_forms(t.source_text):
                    self.by_english.setdefault(name, []).append(t)
        names = sorted(self.by_english, key=len, reverse=True)
        self.pattern = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z])") \
            if names else None

    def in_source(self, msgid: str) -> list[Term]:
        if not self.pattern:
            return []
        found = []
        for m in self.pattern.finditer(strip_codes(msgid)):
            found.extend(self.by_english[m.group(1)])
        return found

    def missing(self, msgid: str, msgstr: str) -> list[Term]:
        # homonyms: one present form satisfies the English
        found = self.in_source(msgid)
        present = {t.source_text for t in found if find_forms(msgstr, t)}
        return [t for t in found if t.source_text not in present]

    def only(self, ids) -> "TermMatcher":
        """Restrict checks to declared source identities."""
        return TermMatcher([self.by_id[tid] for tid in ids])
