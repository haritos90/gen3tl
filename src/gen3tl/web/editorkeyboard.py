"""Naming and chat keyboards expose pack rows and measured limits."""

from __future__ import annotations

from ..build.keyboard import TEXT_POINTER, opening_pages
from ..check.layout import COLOR_LIKE, Measure, code_arg, key_count, split_tokens
from ..core.charmap import BASE_CHARS, NAMED_BYTES, CharmapError

# Key window: 19 x 8 tiles, rows 16 px apart;
# window at screen x 24; cursor centred at x + 38;
# cursor frame spans sprite pixels 2 to 13.
WINDOW = {"width": 152, "height": 64, "row_height": 16, "top": 1, "cursor": {"left": 8, "width": 12}}


def row_entries(kb: dict, tables: list[dict], inv: dict) -> dict[tuple[int, int, str], str]:
    """(base keyboard, row, "keys" or "display") -> the inventory id of that text."""
    tabs = {t["name"]: t for t in kb.get("table", [])}
    keys, rows = tabs.get("keys"), tabs.get("rows")
    if keys is None or rows is None:
        return {}
    count = rows["entry"] // TEXT_POINTER
    width = keys["entry"] // count
    named = {t["name"] for t in tables if t.get("base") == keys["base"] and t.get("stride") == width}
    n = kb["base_pages"]
    out = {}
    for e in inv["entries"]:
        if e["kind"] == "fixed" and e.get("table") in named and e["index"] < n * count:
            k, r = divmod(e["index"], count)
            out[(k, r, "keys")] = e["id"]
        for ref in e["refs"]:
            off = ref["at"] - rows["base"]
            if 0 <= off < n * rows["entry"] and off % TEXT_POINTER == 0:
                k, r = divmod(off // TEXT_POINTER, count)
                out[(k, r, "display")] = e["id"]
    return out


def cursor_columns(prof, rom) -> list[list[int]] | None:
    """The x of each column the cursor reaches, per base keyboard, read from `rom`; None without the tables."""
    kb = prof.get("keyboard") or {}
    tabs = {t["name"]: t for t in kb.get("table", [])}
    cols, xs = tabs.get("columns"), tabs.get("column_x")
    if cols is None or xs is None:
        return None
    return [list(rom.read(xs["base"] + k * xs["entry"], min(rom.u8(cols["base"] + k * cols["entry"]), xs["entry"])))
            for k in range(kb["base_pages"])]


def off_cursor(prof, inv: dict, rows: dict[str, str], measure, cm, cursor: list[list[int]]) -> list[str]:
    """The glyphs the pack's keyboards print outside the cursor frame of their column."""
    kb = prof["keyboard"]
    where = row_entries(kb, prof.get("table", []), inv)
    names = {sid: name for name, sid in kb.get("row_bindings", {}).items()}
    frame, out = WINDOW["cursor"], []
    for k in kb["keyboards"]:
        if k.get("base") or k["like"] >= len(cursor):
            continue
        xs = cursor[k["like"]][:k.get("columns", len(cursor[k["like"]]))]
        for (like, _, part), sid in sorted(where.items()):
            name = names.get(sid)
            if like != k["like"] or part != "display" or name not in rows:
                continue
            for column, (x, g) in enumerate(zip(xs, printed(rows[name], measure, cm))):
                left = x + frame["left"]
                if not (left <= g["x"] and g["x"] + g["width"] <= left + frame["width"]):
                    out.append(f"{name} column {column} {g['char']}")
    return list(dict.fromkeys(out))


def _code(tok: str, cm) -> int | None:
    try:
        b = cm.encode(tok, terminate=False)
    except CharmapError:
        return None
    return b[0] if len(b) == 1 else None


def _hex(code: int | None) -> str | None:
    return None if code is None else f"{code:02X}"


def key_cell(tok: str, cm, drawn, copies: dict[int, int]) -> dict:
    """The character a key enters, its byte and where its glyph comes from."""
    code = _code(tok, cm)
    if code is None:
        source = "none"
    elif code in drawn:
        source = "glyph"
    elif code in copies:
        source = "copy"
    elif code in BASE_CHARS or code in NAMED_BYTES:
        source = "base"
    else:
        source = "none"
    cell = {"char": tok, "code": _hex(code), "source": source}
    if source == "copy":
        cell["copy"] = _hex(copies[code])
    return cell


def printed(text: str, measure, cm) -> list[dict]:
    """The glyphs of a printed row where the text printer places them."""
    x, out = 0, []
    for tok in split_tokens(text):
        if tok.startswith("{") and len(tok) > 2:
            n = code_arg(tok, "CLEAR")
            if n is not None:
                x += n
                continue
            n = code_arg(tok, "CLEAR_TO")
            if n is not None:
                x = max(x, n)
                continue
            n = code_arg(tok, "SKIP")
            if n is not None:
                x = n
                continue
            if COLOR_LIKE.match(tok):
                continue
        code = _code(tok, cm)
        try:
            width = measure.token_width(tok, cm)
        except CharmapError:
            width = 0
        out.append({"char": tok, "code": _hex(code), "x": x, "width": width})
        x += width
    return out


def _text(catalog: dict, sid: str | None, english: bool) -> str | None:
    """The row text the document holds: the pack's translation, None for an English keyboard or a row without one."""
    if sid is None or sid not in catalog:
        return None
    if english:
        return None
    return catalog[sid][1].msgstr or None


def _english(catalog: dict, sid: str | None) -> str | None:
    """The base ROM's row text, for measuring an English keyboard's columns."""
    if sid is None or sid not in catalog:
        return None
    e = catalog[sid][1]
    return None if e.keyed else e.msgid


def chat_pages(kb: dict, inv: dict, cm, measure, key=lambda sid: sid) -> list[dict]:
    """Bound chat rows retain measured widths and storage limits."""
    chat = kb.get("chat")
    if not chat:
        return []
    entries = {e["id"]: e for e in inv["entries"]}
    pages = []
    for prefix in chat["pages"]:
        rows = []
        for r in range(1, chat["rows"] + 1):
            name = f"{prefix}_{r}"
            sid = kb["row_bindings"][name]
            text = kb["rows"][name]
            widths = {}
            for tok in split_tokens(text):
                raw = cm.encode(tok, terminate=False)
                code = 0x100 + raw[1] if len(raw) == 2 and raw[0] == 0xF9 else raw[0]
                widths[f"{code:02X}"] = measure.token_width(tok, cm)
            rows.append({"name": name, "id": key(sid), "text": text, "widths": widths,
                         "bytes": entries[sid]["length"] - 1})
        pages.append({"name": prefix, "rows": rows})
    return pages


def chat_problems(text: str, chat: dict, capacity: int, measure, cm) -> list[str]:
    """Each cursor position requires one fitting, encodable glyph."""
    tokens = split_tokens(text)
    problems = []
    if len(tokens) != chat["columns"]:
        problems.append(f"expected {chat['columns']} keys, got {len(tokens)}")
    width, size = 0, 0
    for tok in tokens:
        try:
            raw = cm.encode(tok, terminate=False)
        except CharmapError:
            problems.append(f"not encodable: {tok}")
            continue
        if not ((len(raw) == 1 and raw[0] < 0xF7) or (len(raw) == 2 and raw[0] == 0xF9)):
            problems.append(f"not a keyboard glyph: {tok}")
            continue
        size += len(raw)
        width += max(chat["column_width"], measure.token_width(tok, cm))
    limit = chat["columns"] * chat["column_width"]
    if width > limit:
        problems.append(f"row width {width} > {limit} px")
    if size > capacity:
        problems.append(f"in-place overflow: {size} > {capacity} bytes")
    return problems


def keyboard_data(prof, catalog: dict, inv: dict, limits: dict, measure, cm, pack, key=lambda sid: sid,
                  cursor: list[list[int]] | None = None) -> dict | None:
    """The "keyboard" of the editor document (see the module); None without a keyboard."""
    kb = prof.get("keyboard")
    if not kb or not kb.get("pages") or not kb.get("keyboards"):
        return None
    where = row_entries(kb, prof.get("table", []), inv)
    if not where:
        return None
    count = max(r for _, r, _ in where) + 1
    names = {sid: name for name, sid in kb.get("row_bindings", {}).items()}
    drawn = set(pack.glyph_slots()) if pack is not None else set()
    copies = pack.copy_slots() if pack is not None else {}
    keyboards = []
    for k in kb["keyboards"]:
        base = bool(k.get("base"))
        rows, columns = [], None
        for r in range(count):
            ids = {part: where.get((k["like"], r, part)) for part in ("keys", "display")}
            texts = {part: _text(catalog, sid, base) for part, sid in ids.items()}
            lim = limits.get(ids["keys"]) if ids["keys"] else None
            if base:   # the limits hold the pack keyboards' columns
                if (en := _english(catalog, ids["keys"])):
                    columns = max(columns or 0, key_count(en))
            elif lim is not None and lim.keys:
                columns = lim.keys
            keys = [key_cell(t, cm, drawn, copies) for t in split_tokens(texts["keys"] or "")]
            glyphs = printed(texts["display"] or "", measure, cm)
            cells = [{"key": keys[i] if i < len(keys) else None, "glyph": glyphs[i] if i < len(glyphs) else None}
                     for i in range(max(len(keys), len(glyphs)))]
            rows.append({part: {"id": key(sid) if sid else None, "text": texts[part], "name": names.get(sid)}
                         for part, sid in ids.items()} | {"cells": cells})
        if cursor and k["like"] < len(cursor):   # the row limits hold the pack's columns
            columns = len(cursor[k["like"]])
        columns = k.get("columns", columns)
        column_x = cursor[k["like"]][:columns] if cursor and k["like"] < len(cursor) else None
        keyboards.append({"name": k["name"], "like": k["like"], "base": base, "columns": columns,
                          "column_x": column_x, "rows": rows})
    result = {"first_page": kb.get("first_page", 0), "style": prof.get("font_style", {}).get("style"),
            "opening": opening_pages(kb),
            "window": {**WINDOW, "cursor": dict(WINDOW["cursor"])},
            "pages": [{"name": p["name"], "keyboard": p["keyboard"], "look": p["look"]} for p in kb["pages"]],
            "labels": list(kb["labels"]) if kb.get("labels") else None, "keyboards": keyboards}
    if chat := kb.get("chat"):
        small = Measure(measure.styles[chat["style"]], {})
        result["chat"] = {**chat, "pages": chat_pages(kb, inv, cm, small, key)}
    return result
