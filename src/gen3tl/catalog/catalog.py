"""Translation catalog: export the inventory to PO files and load them back as translation memory."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ..core.notes import has_prose
from ..core.charmap import Charmap
from . import keys as keys_mod
from .keys import copy_source
from .po import KEY_ONLY, SOURCE_FIELD, PoEntry, read, read_obsolete, remember_name, render, write
from .source import digest


# tag of text never shown
NEVER_SHOWN = "reach: never shown"
STUDY_GROUPS = ("script", "system")


def observed_entries(rules: list[dict]) -> dict[str, dict]:
    """Validate explicit runtime observations."""
    out = {}
    for rule in rules:
        ids, evidence, at = rule.get("ids"), rule.get("evidence"), rule.get("at")
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(sid, str) or not sid for sid in ids)
                or not has_prose(evidence, single_line=True)
                or type(at) is not int or at < 0):
            raise ValueError("[[reach.observed]] needs ids, an address and one-line evidence")
        for sid in ids:
            if sid in out:
                raise ValueError(f"duplicate [[reach.observed]] entry {sid}")
            out[sid] = {"kind": "observed", "at": at, "detail": evidence}
    return out


def never_shown(reach: dict | None, rules: list[dict],
                observed: list[dict] | None = None) -> dict[str, str]:
    """Entry id -> evidence of the strings the game never shows."""
    seen = observed_entries(observed or [])
    out: dict[str, list[str]] = {}
    for sid, r in ((reach or {}).get("strings") or {}).items():
        if r.get("class") == "unreachable":
            g = r.get("group", "script")
            out.setdefault(sid, []).append(f"reach study: unreachable {g} text"
                                           + ("" if g in STUDY_GROUPS else ", build/reach.json"))
    for rule in rules:
        if not rule.get("ids") or not has_prose(rule.get("evidence"), single_line=True):
            raise ValueError(f"[[reach.never_shown]] for {rule.get('ids')} needs ids and a one-line evidence")
        ev = " ".join(rule["evidence"].splitlines())
        for sid in rule["ids"]:
            if sid in seen:
                raise ValueError(f"{sid} is both observed and never shown")
            out.setdefault(sid, []).append(f"review: {ev}")
    for sid in seen:
        out.pop(sid, None)
    return {sid: "; ".join(evs) for sid, evs in out.items()}


def never_shown_tag(extracted: list[str]) -> str | None:
    """The evidence of an entry's never-shown tag, None without one."""
    for c in extracted:
        if c.startswith(NEVER_SHOWN + " (") and c.endswith(")"):
            return c[len(NEVER_SHOWN) + 2:-1]
    return None


def never_shown_evidence(sid: str, extracted: list[str], study: dict | None) -> str | None:
    """Why the game never shows an entry, None when it may show it."""
    tag = never_shown_tag(extracted)
    if tag is not None:
        return tag
    r = ((study or {}).get("strings") or {}).get(sid)
    if r is not None and r.get("class") == "unreachable":
        return never_shown({"strings": {sid: r}}, [])[sid]
    return None


def nothing_to_translate(e: PoEntry) -> bool:
    """No English, no translation, not fuzzy: an unused slot."""
    return not e.msgid and not e.msgstr and not e.fuzzy


def slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s.replace("é", "e")).strip("-").lower()
    return s or "unnamed"


def line_widths(text: str, cm: Charmap, glyph_width) -> list[int]:
    """Pixel width of each line of the original text, as the limits measure it."""
    from ..check.render import iter_text_bytes
    space = cm.encode(" ", terminate=False)[0]
    widths, cur = [0], 0
    for kind, v in iter_text_bytes(cm.encode(text)):
        if kind == "char":
            cur += glyph_width(v)
            if v != space:   # spaces count once text follows
                widths[-1] = cur
        elif kind == "placeholder":   # inserted text follows
            widths[-1] = cur
        elif kind == "break":
            widths.append(0)
            cur = 0
    return widths


def group_of(e: dict, c: dict, table_runs: dict[int, int], run_sizes: dict[int, int] | None = None) -> str:
    run_sizes = run_sizes or {}
    if e["kind"] == "fixed":
        return f"tables/{e['table']}"
    if e.get("table"):  # a pointer table the profile declares
        return f"system/{slug(e['table'])}"
    cat = e["category"]
    if cat == "script":
        m = None
        if c and c["uses"]:
            m = sorted({u["map_name"] for u in c["uses"] if u["map_name"]})[0] if any(u["map_name"] for u in c["uses"]) else None
        m = m or (c or {}).get("inferred_map")
        if m:
            return f"maps/{slug(m)}"
        return f"script/region-{e['offset'] >> 16:03X}0000"
    if cat == "battle-script":
        return "battle"
    if cat == "name-table":
        kinds = [r["kind"][6:] for r in e["refs"] if r["kind"].startswith("table:")]
        return f"tables/{kinds[0]}" if kinds else "system/misc"
    # data-table / code: group by pointer run of first verified ref
    refs = [r for r in e["refs"] if r["kind"] in ("table", "struct", "pool")]
    if refs:
        at = refs[0]["at"]
        start = table_runs.get(at, at)
        if run_sizes.get(start, 1) >= 8:
            return f"system/table-{start:07X}"
    return f"system/region-{e['offset'] >> 16:03X}0000"


def pointer_runs(inv: dict) -> dict[int, int]:
    """Map each table/struct reference position to the start of its run."""
    pos = sorted({r["at"] for e in inv["entries"] for r in e["refs"] if r["kind"] in ("table", "struct", "pool")})
    run_start: dict[int, int] = {}
    i = 0
    while i < len(pos):
        j = i
        stride = pos[i + 1] - pos[i] if i + 1 < len(pos) else 0
        if 4 <= stride <= 64:
            while j + 1 < len(pos) and pos[j + 1] - pos[j] == stride:
                j += 1
        for k in range(i, j + 1):
            run_start[pos[k]] = pos[i]
        i = j + 1
    return run_start


def build_entries(inv: dict, ctx: dict, cm: Charmap, glyph_width, profile_name: str,
                  never: dict[str, str] | None = None,
                  aliases: keys_mod.Aliases = keys_mod.EMPTY) -> dict[str, list[PoEntry]]:
    """PO entries per group; `never` tags never-shown entries, `aliases` names them by key."""
    never = never or {}
    key = aliases.key
    runs = pointer_runs(inv)
    run_sizes: dict[int, int] = defaultdict(int)
    for start in runs.values():
        run_sizes[start] += 1
    strings = ctx.get("strings", {})
    pointer_tables = {pt["name"]: pt for pt in inv.get("pointer_tables", [])}
    ordered = sorted(inv["entries"], key=lambda e: e["offset"])
    texts = {e["id"]: e["text"] for e in ordered}
    neighbours: dict[str, tuple[str | None, str | None]] = {}
    ptrs = [e for e in ordered if e["kind"] == "pointer" and "copy" not in e["flags"]]
    for i, e in enumerate(ptrs):
        neighbours[e["id"]] = (ptrs[i - 1]["id"] if i else None, ptrs[i + 1]["id"] if i + 1 < len(ptrs) else None)
    groups_of: dict[str, list[str]] = defaultdict(list)
    for e in ordered:
        if "copy" in e["flags"]:
            neighbours[e["id"]] = neighbours[copy_source(e["id"])]
            groups_of[copy_source(e["id"])].append(e["id"])
    groups: dict[str, list[PoEntry]] = defaultdict(list)
    for e in ordered:
        if "unverified" in e["flags"] or "keep" in e["flags"]:
            continue
        c = strings.get(e["id"])
        g = group_of(e, c, runs, run_sizes)
        ex = [f"category: {e['category']}; mode: {e['mode']}"]
        if e["id"] in never:   # the tag first
            ex.insert(0, f"{NEVER_SHOWN} ({never[e['id']]})")
        if e["kind"] == "fixed":
            n = e["length"] - (0 if "unterminated" in e["flags"] else 1)
            pointer_length = e.get("mode_evidence", {}).get("pointer_length")
            if pointer_length:
                ex.append(f"inline field: {n} characters; long-name pointer: {pointer_length - 1} characters max "
                          f"(table {e['table']}, index {e['index']})")
            else:
                ex.append(f"fixed field: {n} characters max (table {e['table']}, index {e['index']})")
        elif e.get("table"):
            pt = pointer_tables.get(e["table"], {})
            ex.append(f"pointer table {e['table']}, record {e['index']}"
                      + (": " + " ".join(pt["note"].splitlines()) if pt.get("note") else ""))
        if e.get("species"):   # which species this record's text serves
            ex.append("species: " + ", ".join(key(x) for x in e["species"]))
        if "copy" in e["flags"]:
            ex.append(f"group {e['id'].split('-', 1)[1]} of {key(copy_source(e['id']))}: "
                      "own translation for these uses")
        for cid in groups_of.get(e["id"], []):
            ex.append(f"group {cid.split('-', 1)[1]} reads {key(cid)}")
        if "in-place-only" in e["flags"]:
            ex.append(f"in place only: {e['length'] - 1} bytes max")
        if "save-link" in e["flags"]:
            ex.append("save-link: copied into save or link data; translated like all other text")
        for f in e["flags"]:
            if f in ("male", "female"):
                ex.append(f"trainer gender: {f}")
            if f.startswith("class="):
                ex.append(f"trainer class id: {f[6:]}")
        if c:
            for u in c["uses"][:4]:
                bits = [u["map_name"] or "?", u["event"]]
                if u["sprite"] is not None:
                    bits.append(f"sprite {u['sprite']}")
                if u["textcolor_gender"]:
                    bits.append(f"speaker colour {u['textcolor_gender']}")
                if u["box"]:
                    bits.append(f"box {u['box']}")
                ex.append("used: " + ", ".join(bits) + f" (script {u['script']}, line {u['order']})")
                if u.get("trainer"):
                    t = u["trainer"]
                    ex.append(f"trainer: {t['name']} ({t['class_name']}, {t['gender']})")
            if len(c["uses"]) > 4:
                ex.append(f"used in {len(c['uses'])} places")
            if not c["uses"] and c.get("inferred_map"):
                ex.append(f"probable map (inferred from neighbours): {c['inferred_map']}")
            for s in c.get("speakers", []):
                ex.append(f"speaker label: {s['name']} ({s['gender']})")
            for tid, slot in c.get("fills", []):
                ex.append(f"inserted as {slot} into {key(tid)}: {texts.get(tid, '')[:60]}")
            for wid, slot in c.get("receives", []):
                ex.append(f"{slot} may be {key(wid)}: {texts.get(wid, '')[:30]!r}")
        widths = line_widths(e["text"], cm, glyph_width)
        ex.append(f"original lines: {len(widths)}, widths px: {', '.join(map(str, widths))}")
        if e["kind"] == "pointer":
            prev, nxt = neighbours[e["id"]]
            if prev:
                ex.append(f"prev {key(prev)}: {texts[prev][:70]}")
            if nxt:
                ex.append(f"next {key(nxt)}: {texts[nxt][:70]}")
        groups[g].append(PoEntry(msgctxt=key(e["id"]), msgid=e["text"], extracted=ex,
                                 references=[f"{e['offset']:#09x}"]))
    return groups


def export(inv: dict, ctx: dict, cm: Charmap, glyph_width, root: Path, profile_name: str,
           language: str | None = None, never: dict[str, str] | None = None,
           aliases: keys_mod.Aliases = keys_mod.EMPTY, key_only: bool = False,
           digests: dict[str, str] | None = None, *, check: bool = False) -> dict:
    """Export PO files, or report differing files without writing when checking."""
    from ..lang.language import current
    from . import entrynotes
    language = language or current().code
    groups = build_entries(inv, ctx, cm, glyph_width, profile_name, never, aliases)
    existing: dict[str, PoEntry] = {}
    for p in root.rglob("*.po"):
        _, es = read(p, notes_path=entrynotes.path(root))
        for x in es:
            existing[aliases.entry_id(x.msgctxt)] = x
    key_only = key_only or any(x.keyed for x in existing.values())
    stats = {"files": 0, "entries": 0, "kept": 0, "source_changed": 0, "removed": 0}
    if check:
        stats["changed_files"] = []

    def save(path, entries, header):
        if check:
            expected = render(path, entries, header, notes_path=entrynotes.path(root)).encode("utf-8")
            if not path.is_file() or path.read_bytes() != expected:
                stats["changed_files"].append(str(path))
        else:
            write(path, entries, header, notes_path=entrynotes.path(root))

    seen = set()
    for g, es in sorted(groups.items()):
        for e in es:
            sid = aliases.entry_id(e.msgctxt)
            seen.add(sid)
            old = existing.get(sid)
            if old:
                e.msgstr, e.flags, e.translator = old.msgstr, list(old.flags), old.translator
                if old.keyed:
                    changed = (digests or {}).get(sid, digest(e.msgid)) != digest(e.msgid)
                else:
                    changed = old.msgid != e.msgid
                if changed and old.msgstr:
                    e.previous_msgid = None if old.keyed else old.msgid
                    e.set_fuzzy(True)
                    stats["source_changed"] += 1
                if old.msgstr:
                    stats["kept"] += 1
        header = {"Project-Id-Version": profile_name, "Language": language,
                  "MIME-Version": "1.0", "Content-Type": "text/plain; charset=UTF-8",
                  "Content-Transfer-Encoding": "8bit", "X-Group": g}
        if key_only:
            header[SOURCE_FIELD] = KEY_ONLY
        save(root / f"{g}.po", es, header)
        stats["files"] += 1
        stats["entries"] += len(es)
    stats["removed"] = len(set(existing) - seen)
    # tagged entries, tags without an entry
    stats["never_shown"] = len(seen & set(never or {}))
    stats["never_shown_unknown"] = sorted(set(never or {}) - seen)
    # a group without entries leaves a stale file behind:
    # - its entries are written to other groups
    # - remove it when every entry went elsewhere
    written = {root / f"{g}.po" for g in groups}
    stats["stale_files_removed"], stats["stale_files_kept"] = 0, []
    excluded = set(inv.get("excluded", []))
    for p in sorted(root.rglob("*.po")):
        if p in written:
            continue
        header, es = read(p, notes_path=entrynotes.path(root))
        moved = all(aliases.entry_id(x.msgctxt) in seen or aliases.entry_id(x.msgctxt) in excluded for x in es)
        if moved and not read_obsolete(p):
            if check:
                stats["changed_files"].append(str(p))
            else:
                p.unlink()
            stats["stale_files_removed"] += 1
        else:   # an obsolete section keeps its file
            kept = [x for x in es if not moved and aliases.entry_id(x.msgctxt) not in excluded]
            if len(kept) != len(es):
                save(p, kept, header)
            stats["stale_files_kept"].append(str(p))
    return stats


def load_catalog(root: Path, profile=None, source=None, english: bool = True) -> dict[str, tuple[Path, PoEntry]]:
    """Inventory id -> (PO path, entry); a key-only catalog takes its English from the English source catalog."""
    from ..build.keyboard import resolve_catalog
    from ..profile.profile import EFFECTIVE_PROFILE
    from . import entrynotes, extract
    selected = profile if profile is not None else EFFECTIVE_PROFILE.get()
    aliases = keys_mod.for_profile(selected) if selected is not None else None
    out, names, languages = {}, set(), set()
    for p in sorted(root.rglob("*.po")):
        remember_name(p, root)
        header, es = read(p, notes_path=entrynotes.path(root))
        names.add(header.get("Project-Id-Version"))
        languages.add(header.get("Language"))
        if aliases is None:   # the catalog names its own profile
            aliases = keys_mod.for_profile_name(header.get("Project-Id-Version"))
        for e in es:
            out[aliases.entry_id(e.msgctxt)] = (p, e)
    extract.english([e for _, e in out.values()], source, rom_sha1(selected), english,
                    next(iter(names)) if len(names) == 1 else None)
    from ..lang.language import current
    code = next(iter(languages)) if len(languages) == 1 else current().code
    rows = entrynotes.project([e for _, e in out.values()], entrynotes.path(root), code or current().code,
                              aliases or keys_mod.EMPTY)
    out = {sid: (p, e) for (sid, (p, _)), e in zip(out.items(), rows)}
    resolve_catalog(out, selected)
    return out


def rom_sha1(profile) -> str | None:
    """The base ROM SHA-1 of a profile, None where it names none."""
    try:
        return profile.sha1 if profile is not None else None
    except (KeyError, TypeError, AttributeError):
        return None


def tm_report(root: Path) -> str:
    """Duplicate English strings and whether their translations agree."""
    by_src: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for sid, (p, e) in load_catalog(root).items():
        by_src[e.msgid].append((e.msgctxt, e.msgstr))
    dups = {k: v for k, v in by_src.items() if len(v) > 1}
    conflicts = {k: v for k, v in dups.items() if len({t for _, t in v if t}) > 1}
    lines = ["# Translation memory report", "",
             f"Distinct English strings: {len(by_src)}; repeated: {len(dups)} "
             f"(covering {sum(len(v) for v in dups.values())} entries); "
             f"repeated with differing translations: {len(conflicts)}", ""]
    if conflicts:
        lines += ["## Conflicts", ""]
        for k, v in sorted(conflicts.items())[:200]:
            lines.append(f"- {k[:60]!r}: " + "; ".join(f"{s}={t[:40]!r}" for s, t in v))
        lines.append("")
    lines += ["## Most repeated", ""]
    for k, v in sorted(dups.items(), key=lambda kv: -len(kv[1]))[:40]:
        lines.append(f"- {len(v)}x {k[:70]!r}: " + ", ".join(s for s, _ in v[:6]))
    return "\n".join(lines) + "\n"
