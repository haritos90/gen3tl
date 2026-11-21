"""Translation coverage across the source, the catalog and a built ROM."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..core.notes import has_prose
from ..core.charmap import CharmapError
from ..core.fields import long_name_fields

NEVER = "reach: never shown"
WORD = re.compile(r"[A-Za-zÀ-ÿ]{2,}")
CODE = re.compile(r"\{[^}]*\}")
KEPT_KINDS = {"unverified", "aligned"}   # unverified pointers: the build never rewrites them


def untranslated_cause(entry) -> tuple[str, str]:
    """(cause, evidence) of a catalog entry left without a translation."""
    never = next((c for c in entry.extracted if c.startswith(NEVER)), None)
    if never:
        return "never shown", never[len("reach: "):]
    if "empty" in (entry.flags or []):
        return "empty on purpose", "flag empty: the translation is the empty string"
    if not entry.msgid:
        return "empty source", "the English is empty"
    if not WORD.search(CODE.sub(" ", entry.msgid)):
        return "no words", "no word of two letters outside the control codes"
    return "open: no cause", ""


def same_text(a: str, b: str) -> bool:
    """Equal up to the line breaks the layout adds."""
    def norm(t: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"\\[nl]", " ", t)).strip()
    return norm(a) == norm(b)


@dataclass
class Report:
    untranslated: Counter = field(default_factory=Counter)
    untranslated_rows: list = field(default_factory=list)   # (cause, id, file, english, evidence)
    built: Counter = field(default_factory=Counter)
    built_rows: list = field(default_factory=list)          # (status, id, ref kind, at, shown, expected)
    unexported: list = field(default_factory=list)          # (id, offset, text, refs, open, overlapped id)
    families: dict = field(default_factory=dict)            # name -> Counter

    @property
    def open(self) -> int:
        return (sum(n for k, n in self.untranslated.items() if k.startswith("open")) + sum(n for k, n in self.built.items() if k.startswith("open"))
                + sum(1 for row in self.unexported if row[4]))


def _targets(e: dict, rom, tables: dict) -> list[tuple[str, int, bytes]]:
    """(ref kind, reference position, terminated bytes) the built ROM shows the entry from."""
    if e["kind"] == "fixed" or any(isinstance(r, dict) and r["kind"] == "table-base" for r in e["refs"]):
        at = e["offset"]
        table = tables.get(e.get("table"))
        if table and e.get("index") in long_name_fields(table) and (p := rom.ptr_at(at)) is not None:
            return [("long name", at, bytes(rom.data[p:p + 4000]))]
        if table and table.get("terminated", True) is False:   # an unterminated field
            raw = bytes(rom.data[at:at + table["field_length"]]).rstrip(bytes([table.get("pad", 0xFF)]))
            return [("in place", at, raw + b"\xff")]
        return [("in place", at, bytes(rom.data[at:at + 4000]))]
    out = []
    for r in e["refs"]:
        if isinstance(r, dict) and r["kind"] != "table-base" and (p := rom.ptr_at(r["at"])) is not None:
            out.append((r["kind"], r["at"], bytes(rom.data[p:p + 4000])))
    return out


def _pc_loads(rom, at: int) -> bool:
    """A Thumb or ARM PC-relative load reads the word at `at`."""
    d = rom.data
    for a in range(max(0, at - 1024), at, 2):
        h = d[a] | d[a + 1] << 8
        if h & 0xF800 == 0x4800 and ((a + 4) & ~3) + (h & 0xFF) * 4 == at:
            return True
    for a in range(max(0, at - 4104) & ~3, at, 4):
        w = int.from_bytes(d[a:a + 4], "little")
        if w & 0x0F7F0000 == 0x051F0000 and a + 8 + (w & 0xFFF if w & 0x800000 else -(w & 0xFFF)) == at:
            return True
    return False


def unread_rules(prof: dict) -> dict[int, str]:
    """[[coverage.unread_reference]]: reference positions the game never reads, with evidence."""
    out = {}
    for rule in prof.get("coverage", {}).get("unread_reference", []):
        if (not isinstance(rule, dict) or set(rule) != {"at", "evidence"} or type(rule["at"]) is not int
                or not has_prose(rule["evidence"]) or rule["at"] in out):
            raise ValueError("invalid coverage.unread_reference declaration")
        out[rule["at"]] = rule["evidence"]
    return out


def not_read(base, at: int, commands: list | None, unread: dict) -> str | None:
    """Why the word at `at` is no pointer the game reads, or None."""
    if at in unread:
        return unread[at]
    if commands is None or base is None:
        return None
    i = bisect_right(commands, (at + 3, 1 << 32)) - 1
    if any(a < at + 4 and at < b for a, b in commands[max(0, i - 3):i + 1]):
        return None
    if at % 4:
        return "unaligned, outside every walked script command"
    if _pc_loads(base, at) or any(base.ptr_at(at + k) is not None for k in (-8, -4, 4, 8)):
        return None
    return "aligned, outside walked scripts, no PC-relative load, no pointer beside it"


STOP = set("a an the you your i me my we our it is are was be to of in on for and or not this that "
           "with can will have here there what".split())


def _sentence(text: str) -> bool:
    """Reads as English: three words, one of them a common function word."""
    words = [w.lower() for w in WORD.findall(CODE.sub(" ", text.replace("\\n", " ").replace("\\p", " ")))]
    return len(words) >= 3 and bool(STOP & set(words))


def build(inv: dict, catalog: dict, rom, cm, prof: dict, ctx: dict, expand=lambda t: t,
          base=None, commands: list | None = None) -> Report:
    """The coverage report data; `expand` fills release tokens of a translation."""
    rep = Report()
    unread = unread_rules(prof)
    tables = {t["name"]: t for t in prof.get("table", [])}
    keyboard = {v for v in prof.get("keyboard", {}).get("row_bindings", {}).values() if isinstance(v, str)}
    for sid, (path, e) in sorted(catalog.items()):
        if e.msgstr:
            continue   # translated or approved
        cause, evidence = untranslated_cause(e)
        rep.untranslated[cause] += 1
        rep.untranslated_rows.append((cause, sid, str(path), e.msgid, evidence))
    spans = sorted((x["offset"], x["offset"] + x["length"], x["id"]) for x in inv["entries"] if x["id"] in catalog)
    starts = [a for a, _, _ in spans]
    for e in inv["entries"]:
        sid = e["id"]
        if sid not in catalog:
            refs = sorted({r["kind"] for r in e["refs"] if isinstance(r, dict)})
            lo, hi = e["offset"], e["offset"] + e["length"]
            i = bisect_right(starts, hi) - 1
            inside = next((x for a, b, x in spans[max(0, i - 3):i + 1] if a < hi and lo < b), None)
            why = [not_read(base, r["at"], commands, unread) for r in e["refs"] if isinstance(r, dict)]
            read = not why or None in why
            rep.unexported.append((sid, e["offset"], e["text"], refs,
                                   _sentence(e["text"]) and inside is None and read, inside))
            continue
        ce = catalog[sid][1]
        empty = ce.msgstr == "" and "empty" in (ce.flags or [])
        translated = bool(ce.msgstr)   # approved or awaiting approval, built alike
        want = "" if empty else expand(ce.msgstr) if translated else ce.msgid
        never = untranslated_cause(ce)[0] == "never shown"
        mode = e.get("mode") if e.get("mode") in ("field", "battle") else "field"
        for kind, at, raw in _targets(e, rom, tables):
            try:
                shown, _ = cm.decode(raw, 0, mode=mode)
            except (CharmapError, IndexError):
                shown = None
            if shown is not None and same_text(shown, want):
                status = "translation" if translated else "English, as the catalog says"
            elif sid in keyboard:
                status = "keyboard row written by the keyboard pack"
            elif kind in KEPT_KINDS and shown is not None and same_text(shown, ce.msgid):
                why = not_read(base, at, commands, unread)
                status = ("kept reference to the English, entry never shown" if never
                          else f"kept reference to the English, not read: {why}" if why
                          else "open: kept reference to the English")
            elif shown is not None and same_text(shown, ce.msgid) and translated:
                status = "open: the English is shown"
            else:
                status = "open: other text"
            rep.built[status] += 1
            if status not in ("translation", "English, as the catalog says"):
                rep.built_rows.append((status, sid, kind, at, shown or "", want))
    sign_ids = {sid for sid, s in ctx.get("strings", {}).items() if any(u["event"] == "sign" for u in s.get("uses", []))}
    names = {e["id"] for e in inv["entries"] if e.get("table") == "trainer_names"}
    for name, ids in (("signs", sign_ids), ("trainer names", names)):
        c = Counter()
        for sid in ids:
            if sid not in catalog:
                c["not in the catalog"] += 1
                continue
            ce = catalog[sid][1]
            c["translated" if ce.msgstr else untranslated_cause(ce)[0]] += 1
        bad = Counter(row[0] for row in rep.built_rows if row[1] in ids and row[0].startswith("open"))
        c.update({f"built {k}": n for k, n in bad.items()})
        rep.families[name] = c
    return rep


def _cell(text: str, n: int = 70) -> str:
    t = text.replace("|", "\\|").replace("\n", " ")
    return t if len(t) <= n else t[:n - 1] + "…"


def markdown(rep: Report, built_name: str) -> str:
    lines = ["# Translation coverage", "",
             f"Built ROM: {built_name}. Open items: {rep.open}.", "",
             "## Untranslated catalog entries", "",
             "| Cause | Entries |", "|---|---|"]
    lines += [f"| {k} | {n} |" for k, n in rep.untranslated.most_common()]
    shown = [r for r in rep.untranslated_rows if r[0] != "never shown"]
    lines += ["", "Entries the game may show (never-shown entries carry their reach evidence in the catalog):", "",
              "| Cause | Id | File | English | Evidence |", "|---|---|---|---|---|"]
    lines += [f"| {c} | {sid} | {p} | {_cell(en, 50)} | {_cell(ev, 60)} |" for c, sid, p, en, ev in shown]
    never = Counter(r[4][:90] for r in rep.untranslated_rows if r[0] == "never shown")
    lines += ["", "Never-shown entries by evidence:", "", "| Evidence | Entries |", "|---|---|"]
    lines += [f"| {_cell(ev, 90)} | {n} |" for ev, n in never.most_common()]
    lines += ["", "## Built output", "",
              "Each inventory entry decoded from the built ROM through every reference the build "
              "rewrites or keeps, compared with the catalog up to added line breaks.", "",
              "| Shown | References |", "|---|---|"]
    lines += [f"| {k} | {n} |" for k, n in rep.built.most_common()]
    groups = defaultdict(list)
    for row in rep.built_rows:
        groups[row[0]].append(row)
    for status, rows in sorted(groups.items()):
        lines += ["", f"### {status} ({len(rows)})", "", "| Id | Reference | At | Shown | Expected |",
                  "|---|---|---|---|---|"]
        lines += [f"| {sid} | {kind} | {at:#x} | {_cell(s, 45)} | {_cell(w, 45)} |"
                  for _, sid, kind, at, s, w in rows]
    sentences = [r for r in rep.unexported if r[4]]
    within = [r for r in rep.unexported if r[5]]
    lines += ["", "## Strings outside the catalog", "",
              f"Inventory strings with unverified references only are not exported: "
              f"{len(rep.unexported)}; {len(within)} overlap a catalog string, which shows the text; "
              f"{len(sentences)} others read as English sentences (open).", "",
              "| Id | Offset | References | Text |", "|---|---|---|---|"]
    lines += [f"| {sid} | {off:#x} | {', '.join(refs)} | {_cell(t, 60)} |" for sid, off, t, refs, _, _ in sentences]
    lines += ["", "## Signs and trainer names", "", "| Family | Counts |", "|---|---|"]
    lines += [f"| {name} | {', '.join(f'{k}: {n}' for k, n in sorted(c.items()))} |" for name, c in rep.families.items()]
    return "\n".join(lines) + "\n"
