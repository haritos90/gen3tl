"""Naming screen keyboard pages as a data-only patch: table copies, repointed pool words, page counts."""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from dataclasses import dataclass, field

from ..core.charmap import Charmap
from ..core.rom import ROM_BASE, Rom, ptr_bytes

INDEX_KINDS = ("keyboard", "page-keyboard", "next-keyboard", "next-look", "next-label",
               "label-palette", "label-tile", "label-subsprites", "sprite-sheets")
LABEL_KINDS = ("label-palette", "label-tile", "label-subsprites", "sprite-sheets")
TEXT_POINTER = 4


class KeyboardError(ValueError):
    pass


@dataclass
class Patch:
    count: int                                   # pages
    page_count: list[int]                        # offsets of the immediate bytes
    tables: dict[str, int] = field(default_factory=dict)          # table name -> relocated offset
    writes: list[tuple[int, bytes, str]] = field(default_factory=list)  # offset, bytes, what
    refs: dict[int, int] = field(default_factory=dict)            # literal word -> relocated offset
    allowed: set[int] = field(default_factory=set)

    @property
    def size(self) -> int:
        return sum(len(b) for _, b, _ in self.writes)


def layout_path(code: str, filename, root=None) -> Path:
    """The layout file of a language pack; it stays inside the pack."""
    from ..lang.language import load
    pack = load(code, root).root
    if (not isinstance(filename, str) or not filename or Path(filename).is_absolute()
            or not (pack / filename).resolve().is_relative_to(pack.resolve())):
        raise KeyboardError("keyboard: select a layout inside the language pack")
    return pack / filename


def load_layout(code: str, filename, base: dict, root=None) -> dict:
    """Validate selected pack content against ROM bindings."""
    path = layout_path(code, filename, root)
    try:
        with path.open("rb") as stream:
            doc = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise KeyboardError(f"keyboard: cannot read {path}: {exc}") from exc
    result = check_layout(doc, code, base)
    labels = {}
    for page in result["pages"]:
        if "label" not in page:
            continue
        image = layout_path(code, page["label"], root)
        try:
            labels[page["name"]] = image.read_bytes().hex()
        except OSError as exc:
            raise KeyboardError(f"keyboard: cannot read {image}: {exc}") from exc
    if labels:
        result["_label_data"] = labels
    return result


def check_layout(doc: dict, code: str, base: dict) -> dict:
    """The keyboards, pages and rows of a parsed layout, validated against ROM bindings."""
    if (set(doc) != {"schema", "language", "keyboards", "pages", "rows"}
            or doc["schema"] != "2.0.0" or doc["language"] != code):
        raise KeyboardError("keyboard: layout schema, language or fields differ")
    for kind, fields, optional in (("keyboards", {"name", "like", "base", "columns"}, {"base", "columns"}),
                                   ("pages", {"name", "keyboard", "look", "label"}, {"label"})):
        entries = doc[kind]
        if not isinstance(entries, list) or not entries:
            raise KeyboardError(f"keyboard: {kind} must be a nonempty array")
        names = set()
        for row in entries:
            if (not isinstance(row, dict) or set(row) - fields
                    or not isinstance(row.get("name"), str) or not row["name"]
                    or row["name"] in names):
                raise KeyboardError(f"keyboard: invalid or duplicate {kind} entry")
            names.add(row["name"])
            for field in fields - {"name"} - optional:
                if type(row.get(field)) is not int or row[field] < 0:
                    raise KeyboardError(f"keyboard: {field} must be a nonnegative integer")
            if "base" in row and type(row["base"]) is not bool:
                raise KeyboardError("keyboard: base must be boolean")
            if "columns" in row and (type(row["columns"]) is not int or row["columns"] < 1):
                raise KeyboardError("keyboard: columns must be a positive integer")
            if "label" in row and (not isinstance(row["label"], str) or not row["label"]):
                raise KeyboardError("keyboard: label must name an image inside the language pack")
    bindings, rows = base.get("row_bindings", {}), doc["rows"]
    if (not isinstance(bindings, dict) or not isinstance(rows, dict)
            or set(bindings) != set(rows)
            or any(not isinstance(k, str) or not k for k in bindings)
            or any(not isinstance(v, str) or not v for v in bindings.values())
            or len(set(bindings.values())) != len(bindings)
            or any(not isinstance(v, str) or not v for v in rows.values())):
        raise KeyboardError("keyboard: rows need matching unique ROM bindings")
    result = {key: doc[key] for key in ("keyboards", "pages", "rows")}
    _check_config({**base, **result})
    return result


def _string_end(text: str, at: int) -> int:
    """The index after the TOML basic string that opens at `at`."""
    at += 1
    while text[at] != '"':
        at += 2 if text[at] == "\\" else 1
    return at + 1


def _array_end(text: str, at: int) -> int:
    """The index after the array that opens at `at`."""
    depth = 0
    while True:
        c = text[at]
        if c == '"':
            at = _string_end(text, at)
            continue
        depth += {"[": 1, "]": -1}.get(c, 0)
        at += 1
        if depth == 0:
            return at


def _literal(value) -> str:
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_literal(v)}" for k, v in value.items()) + " }"
    return json.dumps(value, ensure_ascii=False)


def edit_layout(text: str, rows: dict[str, str], pages: list[dict] | None) -> str:
    """The layout file text with new row values and page order, the rest as written."""
    for name, value in rows.items():
        found = re.search(rf'^("?){re.escape(name)}\1[ \t]*=[ \t]*(?=")', text, re.M)
        if found is None:
            raise KeyboardError(f"keyboard: the layout writes no row {name}")
        end = _string_end(text, found.end())
        text = text[:found.end()] + json.dumps(value, ensure_ascii=False) + text[end:]
    if pages is not None:
        found = re.search(r'^("?)pages\1[ \t]*=[ \t]*(?=\[)', text, re.M)
        if found is None:
            raise KeyboardError("keyboard: the layout writes no pages array")
        start, end = found.end(), _array_end(text, found.end())
        old = text[start:end]
        if "\n" in old:   # one page per line
            indent = re.search(r"\n([ \t]*)\{", old)
            pad = indent.group(1) if indent else "  "
            new = "[\n" + "".join(f"{pad}{_literal(p)},\n" for p in pages) + "]"
        else:
            new = "[" + ", ".join(_literal(p) for p in pages) + "]"
        text = text[:start] + new + text[end:]
    return text


def apply_edit(code: str, filename, base: dict, edit, english: dict[str, str] | None = None, root=None,
               write: bool = True) -> dict:
    """Apply an editor keyboard edit to the pack's layout file."""
    from ..check.entrycheck import placeholders_differ
    if (not isinstance(edit, dict) or set(edit) - {"rows", "pages", "base"} or not isinstance(edit.get("base"), dict)
            or set(edit["base"]) - {"rows", "pages"}):
        raise KeyboardError("keyboard: an edit holds rows, pages and their base")
    rows, pages = edit.get("rows", {}), edit.get("pages")
    was_rows, was_pages = edit["base"].get("rows", {}), edit["base"].get("pages")
    if (not isinstance(rows, dict) or not isinstance(was_rows, dict) or set(rows) != set(was_rows)
            or any(not isinstance(v, str) for v in [*rows.values(), *was_rows.values()])):
        raise KeyboardError("keyboard: edited rows and their base must name the same rows with strings")
    if (pages is None) != (was_pages is None) or (pages is not None and (
            not isinstance(pages, list) or not isinstance(was_pages, list)
            or any(not isinstance(n, str) for n in [*pages, *was_pages]))):
        raise KeyboardError("keyboard: an edited page order needs its base order")
    if not rows and pages is None:
        raise KeyboardError("keyboard: the edit changes nothing")
    path = layout_path(code, filename, root)
    text = path.read_text(encoding="utf-8")
    current = load_layout(code, filename, base, root)
    names = [p["name"] for p in current["pages"]]
    chat = base.get("chat", {})
    chat_names = {f"{prefix}_{r}" for prefix in chat.get("pages", [])
                  for r in range(1, chat["rows"] + 1)}
    for name, value in rows.items():
        if name not in current["rows"]:
            raise KeyboardError(f"keyboard: the layout has no row {name}")
        if was_rows[name] != current["rows"][name]:
            raise KeyboardError(f"keyboard: {name} changed after the edit; edit it again")
        problem = (placeholders_differ(english[name], value, set())
                   if english and name in english and name not in chat_names else None)
        if problem:
            raise KeyboardError(f"keyboard: {name}: {problem}")
    order = None
    if pages is not None:
        if was_pages != names:
            raise KeyboardError("keyboard: the page order changed after the edit; edit it again")
        if sorted(pages) != sorted(names) or len(set(pages)) != len(pages):
            raise KeyboardError("keyboard: the new page order must list every page once")
        by_name = {p["name"]: p for p in current["pages"]}
        order = [by_name[n] for n in pages]
    edited = edit_layout(text, rows, order)
    doc = check_layout(tomllib.loads(edited), code, base)
    check_opening({**base, **doc})
    if doc["rows"] != {**current["rows"], **rows} or doc["pages"] != (order or current["pages"]):
        raise KeyboardError("keyboard: the edited layout does not read back as the edit")
    if write:
        temporary = path.with_name(f".{path.name}.edit")
        temporary.write_text(edited, encoding="utf-8")
        os.replace(temporary, path)
    return {"path": path, "rows": sorted(n for n in rows if rows[n] != current["rows"][n]),
            "pages": order is not None and pages != names}


def owned_entries(profile=None) -> dict[str, str]:
    """Canonical text keyed by inventory ID for the selected project."""
    from ..profile.profile import EFFECTIVE_PROFILE
    selected = profile if profile is not None else EFFECTIVE_PROFILE.get()
    kb = selected.get("keyboard", {}) if selected is not None else {}
    if kb.get("row_bindings") and "rows" not in kb:
        raise KeyboardError("keyboard: select an effective language profile before loading its catalog")
    return {sid: kb["rows"][key] for key, sid in kb.get("row_bindings", {}).items()
            if key in kb.get("rows", {})}


def reject_owned(ids, profile=None) -> None:
    """Refuse catalog edits to canonical pack rows."""
    protected = sorted(set(ids) & owned_entries(profile).keys())
    if protected:
        raise KeyboardError(f"language-pack keyboard entries are read-only: {', '.join(protected)}")


def resolve_catalog(catalog: dict, profile=None) -> None:
    """Resolve pack targets without introducing a second editable source."""
    for sid, text in owned_entries(profile).items():
        if sid not in catalog:
            continue
        entry = catalog[sid][1]
        if entry.owner == "keyboard" and entry.msgstr == text:
            continue
        if entry.msgstr or entry.fuzzy or "empty" in entry.flags:
            raise KeyboardError(f"{sid}: catalog translation conflicts with its language-pack keyboard row")
        entry.msgstr, entry.owner = text, "keyboard"


def _check_config(kb: dict) -> None:
    n_base = kb["base_pages"]
    keyboards, pages = kb["keyboards"], kb["pages"]
    if not 1 <= len(pages) <= 255:
        raise KeyboardError(f"page count {len(pages)} does not fit an 8-bit immediate")
    for k in keyboards:
        if not 0 <= k["like"] < n_base:
            raise KeyboardError(f"keyboard {k['name']}: like = {k['like']} is not a base keyboard")
    for p in pages:
        if not 0 <= p["keyboard"] < len(keyboards):
            raise KeyboardError(f"page {p['name']}: keyboard {p['keyboard']} is not declared")
        if not 0 <= p["look"] < n_base:
            raise KeyboardError(f"page {p['name']}: look = {p['look']} is not a base page")
    custom = [p for p in pages if "label" in p]
    if n_base + len(custom) > 256:
        raise KeyboardError("keyboard: label ids do not fit an 8-bit table entry")
    indexes = {t["index"] for t in kb["table"]}
    if custom and not {"next-label", *LABEL_KINDS} <= indexes:
        raise KeyboardError("keyboard: page labels need their label-indexed tables")
    for t in kb["table"]:
        if t["index"] not in INDEX_KINDS:
            raise KeyboardError(f"table {t['name']}: unknown index {t['index']!r}")
        if t["index"] in ("page-keyboard", "next-keyboard", "next-label") and t["entry"] != 1:
            raise KeyboardError(f"table {t['name']}: page ids are single bytes")
        if t["index"] in ("label-palette", "label-tile") and t["entry"] != 2:
            raise KeyboardError(f"table {t['name']}: tags are halfwords")
        if t["index"] in ("label-subsprites", "sprite-sheets") and t["entry"] != 8:
            raise KeyboardError(f"table {t['name']}: sprite records are 8-byte entries")
        if t.get("text") and t["entry"] % TEXT_POINTER:
            raise KeyboardError(f"table {t['name']}: text entries hold whole pointers")


def _opening(kb: dict) -> tuple[list[int], list[int]]:
    opening = kb.get("opening_keyboards")
    offsets = kb.get("opening_keyboard_offsets")
    if (not isinstance(opening, list) or not opening
            or any(type(value) is not int or not 0 <= value <= 0xFF for value in opening)):
        raise KeyboardError("keyboard: opening_keyboards must be a nonempty array of byte values")
    if (not isinstance(offsets, list) or len(offsets) != len(opening)
            or any(type(at) is not int or at < 0 for at in offsets)):
        raise KeyboardError("keyboard: opening_keyboard_offsets must locate every opening keyboard immediate")
    return opening, offsets


def opening_pages(kb: dict) -> list[dict]:
    """Fade-in bypasses the extended page tables."""
    opening, _ = _opening(kb)
    first = kb.get("first_page", 0)
    return [{"page": (first + i) % len(kb["pages"]), "keyboard": keyboard,
             "look": (first + i) % kb["base_pages"]} for i, keyboard in enumerate(opening)]


def check_opening(kb: dict) -> None:
    """Opening pages must match the fade-in."""
    pages, first = kb["pages"], kb.get("first_page", 0)
    opening = opening_pages(kb)
    keyboards = tuple(p["keyboard"] for p in opening)
    at = [p["page"] for p in opening]
    shown = tuple(pages[i]["keyboard"] for i in at)
    if first >= len(pages) or shown != keyboards:
        raise KeyboardError(f"the opening pages {' and '.join(map(str, at))} use keyboards "
                            f"{' and '.join(map(str, shown))}; the naming screen prints keyboards "
                            f"{' and '.join(map(str, keyboards))} when it opens")
    for p in opening:
        look = pages[p["page"]]["look"]
        if look != p["look"]:
            raise KeyboardError(f"opening page {p['page']}: keyboard {p['keyboard']} uses look {look}; "
                                f"the fade-in draws base page look {p['look']}")


def check_base(kb: dict, base: Rom) -> list[str]:
    """The base ROM holds declared keyboard bytes."""
    fails = []
    opening, offsets = _opening(kb)
    for at, expected in zip(offsets, opening):
        if base.u8(at) != expected or base.u8(at + 1) & 0xF8 != 0x20:
            fails.append(f"keyboard: opening keyboard {at:#x} is not movs rX,#{expected} "
                         f"({base.read(at, 2).hex(' ')})")
    for at in kb["page_count"]:
        if base.u8(at) != kb["base_pages"] or base.u8(at + 1) & 0xF8 != 0x20:
            fails.append(f"keyboard: {at:#x} is not movs rX,#{kb['base_pages']} "
                         f"({base.read(at, 2).hex(' ')})")
    for t in kb["table"]:
        for at in t["refs"]:
            if base.u32(at) != ROM_BASE + t["base"]:
                fails.append(f"keyboard: literal {at:#x} does not point to {t['name']} "
                             f"{ROM_BASE + t['base']:#x} ({base.u32(at):#x})")
    lo, hi = kb["reserved"]
    if set(base.read(lo, hi - lo)) != {0xFF}:
        fails.append(f"keyboard: reserved {lo:#x}-{hi:#x} is not free (0xFF) in the base ROM")
    return fails


def _text_end(data: bytes | bytearray, start: int) -> int:
    return Charmap().decode(data, start)[1]


def _label_setup(kb: dict, rom: Rom):
    """Extra label ids, source looks, sprite sheets and encoded pack images."""
    import zlib
    from ..check.render import png_image
    from .graphics import pixels_to_tiles

    pages, n_base = kb["pages"], kb["base_pages"]
    custom = [p for p in pages if "label" in p]
    tables = {t["index"]: t for t in kb["table"]}
    next_label, sheet = tables["next-label"], tables["sprite-sheets"]
    base_ids = [rom.u8(next_label["base"] + ((p["look"] - 1) % n_base)) for p in custom]
    base_sheets = []
    for at in range(sheet["base"], len(rom.data), sheet["entry"]):
        entry = rom.read(at, sheet["entry"])
        if not any(entry):
            break
        base_sheets.append(entry)
    else:
        raise KeyboardError("keyboard: sprite-sheet table has no empty terminator")
    tile = tables["label-tile"]
    tile_tags = [rom.read(tile["base"] + i * 2, 2) for i in range(n_base)]
    by_tag = {int.from_bytes(entry[6:8], "little"): entry for entry in base_sheets}
    first_tag = max(by_tag) + 1
    if first_tag + len(custom) - 1 > 0xFFFF:
        raise KeyboardError("keyboard: label tile tags do not fit a halfword")
    new_sheets, images = [], []
    for i, (p, base_id) in enumerate(zip(custom, base_ids)):
        source = by_tag.get(int.from_bytes(tile_tags[base_id], "little"))
        if source is None:
            raise KeyboardError(f"page {p['name']}: base look has no label sprite sheet")
        size = int.from_bytes(source[4:6], "little")
        if not size or size % 32:
            raise KeyboardError(f"page {p['name']}: base label is not whole 4bpp tiles")
        try:
            data = bytes.fromhex(kb.get("_label_data", {})[p["name"]])
            image = png_image(data, p["label"])
        except (KeyError, ValueError, zlib.error) as exc:
            raise KeyboardError(f"page {p['name']}: cannot read label image: {exc}") from exc
        tiles = size // 32
        if image.indices is None or (image.width, image.height) != (tiles * 8, 8):
            raise KeyboardError(f"page {p['name']}: label must be an indexed {tiles * 8}x8 PNG")
        if max((v for row in image.indices for v in row), default=0) > 15:
            raise KeyboardError(f"page {p['name']}: label palette indices exceed 4bpp")
        images.append(pixels_to_tiles(image.indices, tiles, 4, tiles))
        tag = (first_tag + i).to_bytes(2, "little")
        new_sheets.append(bytes(4) + source[4:6] + tag)
    return custom, base_ids, base_sheets, new_sheets, images, tile_tags


def plan(kb: dict, base: Rom, rom: Rom) -> Patch:
    """Lay out the tables in the reserved range after text and graphics injection."""
    _check_config(kb)
    check_opening(kb)
    fails = check_base(kb, base)
    if fails:
        raise KeyboardError("; ".join(fails))
    keyboards, pages, n_base = kb["keyboards"], kb["pages"], kb["base_pages"]
    n = len(pages)
    lo, hi = kb["reserved"]
    patch = Patch(n, list(kb["page_count"]))
    custom = [p for p in pages if "label" in p]
    label_ids = {p["name"]: n_base + i for i, p in enumerate(custom)}
    if custom:
        custom, base_ids, base_sheets, new_sheets, images, tile_tags = _label_setup(kb, rom)
    contents: list[tuple[dict, list[bytes]]] = []
    copies: list[tuple[str, int, bytes]] = []   # what, pointer position in its table, string
    for t in kb["table"]:
        if not custom and t["index"] in LABEL_KINDS:
            continue
        size, src = t["entry"], t["base"]
        entries = []
        if t["index"] == "keyboard":
            for k in keyboards:
                rom_from = base if k.get("base") else rom
                entry = rom_from.read(src + k["like"] * size, size)
                if t["name"] == "columns" and "columns" in k:   # the keyboard bounds its own columns
                    if k["columns"] > entry[0]:
                        raise KeyboardError(f"keyboard {k['name']}: columns = {k['columns']} exceeds the "
                                            f"{entry[0]} of base keyboard {k['like']}")
                    entry = bytes([k["columns"]])
                if t.get("text") and k.get("base"):
                    for j in range(0, size, TEXT_POINTER):
                        s = base.ptr_at(src + k["like"] * size + j)
                        if s is None:
                            raise KeyboardError(f"table {t['name']}: entry {k['like']} holds no pointer at +{j}")
                        copies.append((t["name"], len(entries) * size + j, base.read(s, _text_end(base.data, s) - s)))
                entries.append(entry)
        elif t["index"] == "page-keyboard":
            entries = [bytes([p["keyboard"]]) for p in pages]
        elif t["index"] == "next-keyboard":
            entries = [bytes([pages[(i + 1) % n]["keyboard"]]) for i in range(n)]
        elif t["index"] == "next-label":
            entries = [bytes([label_ids[pages[(i + 1) % n]["name"]]]) if pages[(i + 1) % n] in custom
                       else rom.read(src + ((pages[(i + 1) % n]["look"] - 1) % n_base) * size, size)
                       for i in range(n)]
        elif t["index"] in ("label-palette", "label-subsprites"):
            entries = [rom.read(src + i * size, size) for i in range(n_base)]
            entries += [entries[i] for i in base_ids]
        elif t["index"] == "label-tile":
            entries = tile_tags + [(max(int.from_bytes(s[6:8], "little") for s in base_sheets) + i + 1)
                                   .to_bytes(2, "little") for i in range(len(custom))]
        elif t["index"] == "sprite-sheets":
            entries = base_sheets + new_sheets + [bytes(size)]
        else:  # next-look: base entry m describes base page m + 1
            entries = [rom.read(src + ((pages[(i + 1) % n]["look"] - 1) % n_base) * size, size)
                       for i in range(n)]
        contents.append((t, entries))
    at = lo
    blobs: dict[str, bytearray] = {}
    for t, entries in contents:
        at = (at + 3) & ~3
        patch.tables[t["name"]] = at
        blobs[t["name"]] = bytearray(b"".join(entries))
        at += len(blobs[t["name"]])
    label_writes = []
    if custom:
        sheet = next(t for t in kb["table"] if t["index"] == "sprite-sheets")
        for i, (p, image) in enumerate(zip(custom, images)):
            at = (at + 3) & ~3
            pos = (len(base_sheets) + i) * sheet["entry"]
            blobs[sheet["name"]][pos:pos + 4] = ptr_bytes(at)
            label_writes.append((at, image, f"{p['name']} label"))
            at += len(image)
    strings = []
    for name, pos, text in copies:
        blobs[name][pos:pos + TEXT_POINTER] = ptr_bytes(at)
        strings.append((at, text, f"{name} text"))
        at += len(text)
    if at > hi:
        raise KeyboardError(f"keyboard tables need {at - lo} bytes, reserved {hi - lo}")
    for t, _ in contents:
        patch.writes.append((patch.tables[t["name"]], bytes(blobs[t["name"]]), t["name"]))
        for ref in t["refs"]:
            patch.refs[ref] = patch.tables[t["name"]]
    patch.writes += label_writes + strings
    patch.allowed.update(patch.page_count)
    for ref in patch.refs:
        patch.allowed.update(range(ref, ref + 4))
    for off, blob, _ in patch.writes:
        patch.allowed.update(range(off, off + len(blob)))
    return patch


def apply(rom: Rom, patch: Patch) -> None:
    for at in patch.page_count:
        rom.data[at] = patch.count
    for ref, off in patch.refs.items():
        rom.write(ref, ptr_bytes(off))
    for off, blob, _ in patch.writes:
        rom.write(off, blob)


def verify(rom: Rom, kb: dict, patch: Patch) -> list[str]:
    """The built ROM holds the patch: page count, repointed literals, tables inside `reserved`."""
    fails = []
    lo, hi = kb["reserved"]
    for at in patch.page_count:
        if rom.u8(at) != patch.count:
            fails.append(f"keyboard: page count at {at:#x} is {rom.u8(at)}; expected {patch.count}")
    for ref, off in patch.refs.items():
        if rom.u32(ref) != ROM_BASE + off:
            fails.append(f"keyboard: literal {ref:#x} does not point to {ROM_BASE + off:#x}")
    for off, blob, what in patch.writes:
        if not (lo <= off and off + len(blob) <= hi):
            fails.append(f"keyboard: {what} at {off:#x} lies outside the reserved range")
        if rom.read(off, len(blob)) != blob:
            fails.append(f"keyboard: {what} at {off:#x} does not hold the planned bytes")
    return fails


def describe(kb: dict, patch: Patch) -> list[str]:
    """Report lines: pages in Select order and the bytes written."""
    names = [p["name"] for p in kb["pages"]]
    first = kb.get("first_page", 0)
    order = [names[(first + i) % len(names)] for i in range(len(names))]
    lines = [f"pages {patch.count}: {' -> '.join(order)} (opens on page {first})",
             f"page count bytes: {', '.join(f'{a:#x}' for a in patch.page_count)}",
             f"literal words: {', '.join(f'{a:#x}' for a in sorted(patch.refs))}"]
    lines += [f"{what}: {off:#x}-{off + len(blob) - 1:#x} ({len(blob)} bytes)"
              for off, blob, what in patch.writes if not what.endswith(" text")]
    texts = [(off, blob) for off, blob, what in patch.writes if what.endswith(" text")]
    if texts:
        lines.append(f"copied row strings: {len(texts)}, {texts[0][0]:#x}-"
                     f"{texts[-1][0] + len(texts[-1][1]) - 1:#x} ({sum(len(b) for _, b in texts)} bytes)")
    return lines
