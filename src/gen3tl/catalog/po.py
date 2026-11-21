"""Minimal gettext PO reader and writer for the translation catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SOURCE_PREFIX = "source: "
SOURCE_FIELD = "X-Source"   # header field of the catalog's form
KEY_ONLY = "key-only"   # msgid holds the key; English comes from the ROM
# extracted comments of catalog.build_entries, in order;
# flag: quotes the game's text; one group shares a place
COMMENTS = ((("reach: never shown (",), False), (("category: ",), False),
            (("inline field: ", "fixed field: ", "pointer table "), False), (("species: ",), False),
            (("group ",), False), (("in place only: ",), False), (("save-link: ",), False),
            (("trainer gender: ", "trainer class id: "), False), (("used: ", "trainer: "), True),
            (("used in ",), False), (("probable map ",), True), (("speaker label: ",), True),
            (("inserted as ",), True), (("{",), True), (("original lines: ",), False),
            (("prev ",), True), (("next ",), True))
CATALOG_LABEL = Path("translation/po")   # the name every layout shares
_names: dict[Path, str] = {}   # catalog files read, by path
_CACHE_FILES = 512
_cache: dict[Path, tuple] = {}


def file_label(path: Path, root: Path) -> str:
    """A catalog file under `root` as outputs name it: its path below translation/po."""
    return (CATALOG_LABEL / Path(path).relative_to(root)).as_posix()


def remember_name(path: Path, root: Path) -> None:
    """Record the output name of a catalog file read under `root`."""
    _names[Path(path)] = file_label(path, root)


def catalog_name(path: Path) -> str:
    """The output name of a catalog file read, else its path."""
    return _names.get(Path(path)) or Path(path).as_posix()


def translation_state(text: str, state: str) -> str:
    """Validate the state against its stored text."""
    if (not isinstance(text, str) or state not in ("original", "empty", "text")
            or (state == "text") != bool(text)):
        raise ValueError("invalid translation state")
    return state


@dataclass
class PoEntry:
    msgctxt: str
    msgid: str
    msgstr: str = ""
    extracted: list[str] = field(default_factory=list)   # "#." lines
    translator: list[str] = field(default_factory=list)  # "# " lines
    references: list[str] = field(default_factory=list)  # "#:" lines
    flags: list[str] = field(default_factory=list)       # "#," items
    previous_msgid: str | None = None                    # "#| msgid"

    owner: str | None = None                            # runtime ownership, never serialized
    keyed: bool = False   # read from a key-only file; never serialized

    @property
    def state(self) -> str:
        return "text" if self.msgstr else "empty" if "empty" in self.flags else "original"

    @property
    def approved(self) -> bool:
        """Text or an intended blank without the fuzzy flag."""
        return self.state != "original" and not self.fuzzy

    def set_translation(self, text: str, state: str, *, keep_approved: bool = False) -> bool:
        """Store changed text or state as translated; `keep_approved` leaves an approved entry approved."""
        translation_state(text, state)
        if (self.msgstr, self.state) == (text, state):
            return False
        approved = self.approved
        self.msgstr = text
        self.flags = [flag for flag in self.flags if flag != "empty"]
        if state == "empty":
            self.flags.append("empty")
        if not (keep_approved and approved):
            self.set_fuzzy(True)
        return True

    @property
    def source_note(self) -> str:
        """Wording sources, preserving their line order."""
        return "\n".join(c[len(SOURCE_PREFIX):] for c in self.translator if c.startswith(SOURCE_PREFIX))

    @source_note.setter
    def source_note(self, note: str) -> None:
        """Replace source metadata while retaining unrelated translator comments."""
        from .entrynotes import MARKER
        if MARKER in self.translator:
            raise ValueError("edit structured source notes in notes/entries.toml")
        if not isinstance(note, str):
            raise ValueError("source note must be a string")
        if any(c in note for c in "\r\v\f\x00\x1c\x1d\x1e\x85\u2028\u2029"):
            raise ValueError("source notes use LF line breaks")
        self.translator = [c for c in self.translator if not c.startswith(SOURCE_PREFIX)]
        if note:
            self.translator.extend(SOURCE_PREFIX + line for line in note.split("\n"))

    @property
    def fuzzy(self) -> bool:
        return "fuzzy" in self.flags

    def set_fuzzy(self, on: bool) -> None:
        if on and "fuzzy" not in self.flags:
            self.flags.append("fuzzy")
        if not on and "fuzzy" in self.flags:
            self.flags.remove("fuzzy")


def comment_kind(comment: str) -> tuple[int, bool]:
    """The place of an extracted comment among COMMENTS and whether it quotes the game's text."""
    for place, (prefixes, quotes) in enumerate(COMMENTS):
        if comment.startswith(prefixes):
            return place, quotes
    return len(COMMENTS), True


def key_only(e: PoEntry) -> PoEntry:
    """The entry in the key-only form."""
    return PoEntry(e.msgctxt, e.msgctxt, e.msgstr, [c for c in e.extracted if not comment_kind(c)[1]],
                   list(e.translator), list(e.references), list(e.flags), None, e.owner, True)


def with_source(e: PoEntry, source: PoEntry) -> None:
    """Join a keyed entry with its English source entry: the English and its quoting comments."""
    own = [c for c in e.extracted if not comment_kind(c)[1]]
    quoted = [c for c in source.extracted if comment_kind(c)[1]]
    e.msgid, e.keyed = source.msgid, False
    e.extracted = sorted(own + quoted, key=lambda c: comment_kind(c)[0])


def escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


def unescape(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(n, "\\" + n))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _fmt(keyword: str, value: str, width: int = 76) -> list[str]:
    esc = escape(value)
    if len(esc) + len(keyword) + 3 <= width:
        return [f'{keyword} "{esc}"']
    # split on escaped line-break markers and spaces for readability
    parts = []
    cur = ""
    for tok in _split_tokens(esc):
        if len(cur) + len(tok) > width - 2 and cur:
            parts.append(cur)
            cur = ""
        cur += tok
    if cur:
        parts.append(cur)
    return [f'{keyword} ""'] + [f'"{p}"' for p in parts]


def _split_tokens(esc: str) -> list[str]:
    toks = []
    cur = ""
    i = 0
    while i < len(esc):
        cur += esc[i]
        # break after a space or escaped break (\\n \\l \\p)
        if esc[i] == " " or (esc[i] in "nlp" and cur.endswith("\\\\" + esc[i])):
            toks.append(cur)
            cur = ""
        i += 1
    if cur:
        toks.append(cur)
    return toks


def _entry_lines(e: PoEntry, obsolete: bool = False) -> list[str]:
    lines = []
    for c in e.translator:
        lines.append(f"# {c}" if c else "#")
    for c in e.extracted:
        lines.append(f"#. {c}")
    for r in e.references:
        lines.append(f"#: {r}")
    if e.flags:
        lines.append("#, " + ", ".join(e.flags))
    if e.previous_msgid is not None:
        lines += [("#~| " if obsolete else "#| ") + x for x in _fmt("msgid", e.previous_msgid)]
    body = _fmt("msgctxt", e.msgctxt) + _fmt("msgid", e.msgid)
    # an owned entry's text is resolved, not stored
    body += _fmt("msgstr", "" if e.owner else e.msgstr)
    lines += [("#~ " if obsolete else "") + x for x in body]
    lines.append("")
    return lines


def _notes(path: Path, entries: list[PoEntry], header: dict[str, str], notes_path=None):
    from . import entrynotes, keys
    from ..lang.language import current
    if notes_path is not None:
        entrynotes.remember(path, notes_path)
    file = notes_path if notes_path is not None else entrynotes.for_file(path)
    if file is None or not Path(file).exists():
        return entries
    return entrynotes.project(entries, file, header.get("Language") or current().code,
                              keys.for_profile_name(header.get("Project-Id-Version")))


def render(path: Path, entries: list[PoEntry], header: dict[str, str],
           obsolete: list[PoEntry] | None = None, *, notes_path: Path | None = None) -> str:
    """Serialize without writing; omitted obsolete entries come from the destination."""
    if obsolete is None:
        obsolete = read_obsolete(path) if Path(path).is_file() else []
    entries = _notes(path, entries, header, notes_path)
    obsolete = _notes(path, obsolete, header, notes_path)
    if header.get(SOURCE_FIELD) == KEY_ONLY:
        entries, obsolete = [key_only(e) for e in entries], [key_only(e) for e in obsolete]
    lines = ['msgid ""', 'msgstr ""']
    for k, v in header.items():
        lines.append(f'"{k}: {escape(v)}\\n"')
    lines.append("")
    for e in entries:
        lines += _entry_lines(e)
    for e in obsolete:
        lines += _entry_lines(e, obsolete=True)
    return "\n".join(lines)


def write(path: Path, entries: list[PoEntry], header: dict[str, str],
          obsolete: list[PoEntry] | None = None, *, notes_path: Path | None = None) -> None:
    """`obsolete`: the obsolete section; None keeps the one `path` holds."""
    text = render(path, entries, header, obsolete, notes_path=notes_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _cache.pop(Path(path).resolve(), None)


def read(path: Path, *, notes_path: Path | None = None) -> tuple[dict[str, str], list[PoEntry]]:
    """The header and the active entries."""
    header, entries, _ = parse(path, notes_path=notes_path)
    return header, entries


def template_reasons(path: Path) -> dict[str, str]:
    """Read keyed, untranslated labels with mandatory extracted reasons."""
    reasons = {}
    for entry in read(path)[1]:
        key = entry.msgid
        if not key.strip() or entry.msgctxt != key or entry.msgstr:
            raise ValueError(f"{path}: {key!r} needs matching keys and an empty translation")
        if key in reasons:
            raise ValueError(f"{path}: duplicate label {key!r}")
        reason = "\n".join(entry.extracted)
        if not reason.strip():
            raise ValueError(f"{path}: {key!r} has no reason")
        reasons[key] = reason
    return reasons


def read_obsolete(path: Path) -> list[PoEntry]:
    """The entries of the obsolete section."""
    if "#~" not in Path(path).read_text(encoding="utf-8"):
        return []
    return parse(path)[2]


def parse_text(text: str) -> tuple[dict[str, str], list[PoEntry], list[PoEntry]]:
    """Parse the text of one file without projecting its structured notes."""
    header: dict[str, str] = {}
    entries: list[PoEntry] = []
    obsolete: list[PoEntry] = []
    cur: dict = {}
    key = None

    def flush():
        nonlocal cur, key
        if "msgid" in cur:
            if not cur.get("obsolete") and cur.get("msgctxt") is None and cur["msgid"] == "":
                for line in cur.get("msgstr", "").split("\n"):
                    if ": " in line:
                        k, v = line.split(": ", 1)
                        header[k] = v
            else:
                (obsolete if cur.get("obsolete") else entries).append(PoEntry(
                    msgctxt=cur.get("msgctxt", ""), msgid=cur["msgid"], msgstr=cur.get("msgstr", ""),
                    extracted=cur.get("extracted", []), translator=cur.get("translator", []),
                    references=cur.get("references", []), flags=cur.get("flags", []),
                    previous_msgid=cur.get("previous_msgid")))
        cur = {}
        key = None

    def take(line: str) -> None:
        nonlocal key
        if line.startswith("#|"):
            body = line[2:].strip()
            if body.startswith("msgid "):
                cur["previous_msgid"] = unescape(body[6:].strip()[1:-1])
                key = "previous_msgid"
            elif body.startswith('"') and key == "previous_msgid":
                cur["previous_msgid"] += unescape(body[1:-1])
            return
        if line.startswith("#."):
            if "msgid" in cur:
                flush()
            cur.setdefault("extracted", []).append(line[3:] if len(line) > 2 else "")
            return
        if line.startswith("#:"):
            if "msgid" in cur:
                flush()
            cur.setdefault("references", []).append(line[3:])
            return
        if line.startswith("#,"):
            if "msgid" in cur:
                flush()
            cur.setdefault("flags", []).extend(x.strip() for x in line[2:].split(",") if x.strip())
            return
        if line.startswith("#"):
            if "msgid" in cur:
                flush()
            cur.setdefault("translator", []).append(line[2:] if len(line) > 1 else "")
            return
        for kw in ("msgctxt", "msgid", "msgstr"):
            if line.startswith(kw + " "):
                if kw in ("msgctxt",) and "msgid" in cur:
                    flush()
                key = kw
                cur[kw] = unescape(line[len(kw) + 1:].strip()[1:-1])
                break
        else:
            if line.startswith('"') and key:
                cur[key] = cur.get(key, "") + unescape(line.strip()[1:-1])

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            flush()
            continue
        if not line.startswith("#~"):
            take(line)
            continue
        # an obsolete line reads like the active one
        body = line[2:].lstrip(" ")
        if not body:
            continue
        take("#" + body if body.startswith("|") else body)
        cur["obsolete"] = True
    flush()
    if header.get(SOURCE_FIELD) == KEY_ONLY:
        for e in entries + obsolete:
            e.keyed = True
    return header, entries, obsolete


def _copy_entries(entries: list[PoEntry]) -> list[PoEntry]:
    return [PoEntry(e.msgctxt, e.msgid, e.msgstr, list(e.extracted), list(e.translator), list(e.references),
                    list(e.flags), e.previous_msgid, e.owner, e.keyed) for e in entries]


def _cached_file(path: Path):
    key = Path(path).resolve()
    stat = key.stat()
    stamp = stat.st_size, stat.st_mtime_ns
    cached = _cache.get(key)
    if cached is None or cached[0] != stamp:
        parsed = parse_text(key.read_text(encoding="utf-8"))
        if len(_cache) >= _CACHE_FILES:
            _cache.pop(next(iter(_cache)))
        _cache[key] = stamp, parsed
    else:
        parsed = cached[1]
    header, entries, obsolete = parsed
    return dict(header), _copy_entries(entries), _copy_entries(obsolete)


def parse(path: Path, *, notes_path: Path | None = None) -> tuple[dict[str, str], list[PoEntry], list[PoEntry]]:
    """The header, the active entries and the obsolete entries."""
    header, entries, obsolete = _cached_file(path)
    return header, _notes(path, entries, header, notes_path), _notes(path, obsolete, header, notes_path)
