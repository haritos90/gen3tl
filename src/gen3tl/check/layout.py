"""Pixel-width layout of translated text and the limit checks of one string."""

from __future__ import annotations

from ..lang import language

import re
from dataclasses import dataclass

from ..core.charmap import Charmap, CharmapError
from .provenance import code as coded, encoding_problem, noted

TOKEN = re.compile(r"(\\[nlp]|\{[^}]*\})")
COLOR_LIKE = re.compile(r"^\{(COLOR|HIGHLIGHT|SHADOW|COLOR_HIGHLIGHT_SHADOW|PALETTE|FONT|RESET_FONT|PAUSE|"
                        r"PAUSE_UNTIL_PRESS|WAIT_SE|PLAY_BGM|PLAY_SE|PAUSE_MUSIC|RESUME_MUSIC|ESCAPE|"
                        r"SHIFT_RIGHT|SHIFT_DOWN|FILL_WINDOW|CLEAR|SKIP|CLEAR_TO|MIN_LETTER_SPACING|FC)\b")
KEY_ICON = "F8"   # control code of a key icon


@dataclass
class Limit:
    kind: str            # dialogue | battle | multiline | single | field
    width: int           # pixels per line (hard limit)
    lines: int | None    # lines per box or in total; None means no limit
    chars: int | None = None   # fixed field character limit
    bytes: int | None = None   # in-place-only byte limit (without terminator)
    strip: list[str] | None = None   # prefixes the printing code skips
    target: int | None = None  # preferred wrap width (style), at most `width`
    sentence_break: float | None = None
    placeholders: dict[str, int] | None = None   # px per placeholder name for this entry
    slot_details: dict[str, dict] | None = None
    slot_context: str | None = None
    slot_sources: dict[str, str] | None = None   # where each of those widths comes from
    host: str | None = None    # inserted word limited by its host sentence
    source_lines: list[int] | None = None   # lines of each English box
    keys: int | None = None    # keyboard key cells, checked instead of width
    style: str | None = None   # font style the code prints in
    head: str | None = None    # text the translation must begin with
    sources: dict[str, dict] | None = None   # field -> where the limit comes from
    arrow: int | None = None   # prompt arrow px after a line that waits
    consumers: list[dict] | None = None   # independent windows sharing this text
    newline_scroll_after: int | None = None   # newlines preserved before runtime scrolling
    icon_spacing: int | None = None   # printer letter spacing after each key icon


def code_arg(tok: str, name: str) -> int | None:
    """The hex argument of a {name xx} token, None for another token."""
    if not (tok.startswith("{" + name + " ") and tok.endswith("}")):
        return None
    try:
        return int(tok[len(name) + 2:-1], 16)
    except ValueError:
        return None


class Measure:
    def __init__(self, glyph_width, placeholder_widths: dict[str, int], default_placeholder: int = 60,
                 styles: dict | None = None, shadow_end=None, shadow_styles: dict | None = None,
                 fonts: dict | None = None):
        self.glyph_width = glyph_width       # byte -> px
        self.ph = placeholder_widths
        self.default_ph = default_placeholder
        self.styles = styles or {}           # style -> glyph widths
        self.shadow_end = shadow_end         # byte -> last column holds no ink
        self.shadow_styles = shadow_styles or {}   # style -> shadow_end
        self.fonts = fonts or {}             # FONT argument -> style
        self._token_widths = {}

    def token_width(self, tok: str, cm: Charmap) -> int:
        key = (self.glyph_width, cm, tok)
        try:
            return self._token_widths[key]
        except KeyError:
            width = self._uncached_token_width(tok, cm)
            self._token_widths[key] = width
            return width

    def _uncached_token_width(self, tok: str, cm: Charmap) -> int:
        if tok.startswith("{") and tok.endswith("}") and len(tok) > 2:
            name = tok[1:-1].split()[0]
            if COLOR_LIKE.match(tok):
                return 0
            extended = code_arg(tok, "F9")
            if extended is not None and 0 <= extended <= 0xFF:
                return self.glyph_width(0x100 + extended)
            if tok[1:-1] in self.ph:   # full name, e.g. "FD 3D"
                return self.ph[tok[1:-1]]
            if name in self.ph:
                return self.ph[name]
            if name in ("FD", "B_BUFF1") or name.startswith("B_") or name.startswith("STR_VAR") or name in (
                    "PLAYER", "RIVAL", "KUN"):
                return self.ph.get(name, self.default_ph)
            # single-byte named symbols ({LV}, {PK}, raw bytes): measure the byte
            try:
                return sum(self.glyph_width(b) for b in cm.encode(tok, terminate=False))
            except CharmapError:
                return self.default_ph
        return sum(self.glyph_width(b) for b in cm.encode(tok, terminate=False))

    def width(self, text: str, cm: Charmap) -> int:
        return self.advance(split_tokens(text), cm)[0]

    def font_measure(self, font: int) -> "Measure":
        """The measure of the style a {FONT xx} code selects; this measure where either is unknown."""
        style = self.fonts.get(f"{font:02X}")
        if style not in self.styles:
            return self
        return Measure(self.styles[style], self.ph, self.default_ph, self.styles, self.shadow_styles.get(style),
                       self.shadow_styles, self.fonts)

    def advance(self, toks: list[str], cm: Charmap) -> tuple[int, list[tuple[int, int, int]], "Measure"]:
        """Where tokens from the start of a line end, as the text printer places them."""
        x, cols, m = 0, [], self
        for i, t in enumerate(toks):
            if t.startswith("{"):
                n = code_arg(t, "CLEAR_TO")
                if n is not None:
                    cols.append((x, n, i))
                    x = max(x, n)
                    continue
                f = code_arg(t, "FONT")
                if f is not None:
                    m = self.font_measure(f)
                    continue
            x += m.token_width(t, cm)
        return x, cols, m

    def end_glyph(self, tok: str, cm: Charmap) -> int | None:
        """The byte of the glyph a token draws, as token_width measures it, or None."""
        if tok.startswith("{") and tok.endswith("}") and len(tok) > 2:
            name = tok[1:-1].split()[0]
            extended = code_arg(tok, "F9")
            if extended is not None and 0 <= extended <= 0xFF:
                return 0x100 + extended
            if COLOR_LIKE.match(tok) or tok[1:-1] in self.ph or name in self.ph:
                return None
            if name in ("FD", "B_BUFF1") or name.startswith("B_") or name.startswith("STR_VAR") or name in (
                    "PLAYER", "RIVAL", "KUN"):
                return None
            try:
                raw = cm.encode(tok, terminate=False)
            except CharmapError:
                return None
        else:
            raw = cm.encode(tok, terminate=False)
        return raw[0] if len(raw) == 1 else None

    def with_placeholders(self, widths: dict[str, int] | None) -> "Measure":
        """The same measure with other placeholder widths (per entry and slot)."""
        if not widths:
            return self
        return Measure(self.glyph_width, {**self.ph, **widths}, self.default_ph, self.styles, self.shadow_end,
                       self.shadow_styles, self.fonts)


def icon_widths(ph: dict[str, int], spacing: int | None) -> dict[str, int]:
    """Key icon widths with the printer's letter spacing, which the printer adds to icons only."""
    return {k: v + spacing for k, v in ph.items() if k.startswith(KEY_ICON + " ")} if spacing else {}


def entry_measure(m: Measure, lim: Limit) -> Measure:
    """The measure of an entry: its placeholder widths and the glyph widths of its font style."""
    m = m.with_placeholders(lim.placeholders)
    m = m.with_placeholders(icon_widths(m.ph, lim.icon_spacing))
    if lim.style is not None and lim.style in m.styles:
        m = Measure(m.styles[lim.style], m.ph, m.default_ph, m.styles, m.shadow_styles.get(lim.style),
                    m.shadow_styles, m.fonts)
    return m


class SpriteMeasure:
    """Widths of text in a sprite font, from its build plan (build.spritefont.measure)."""

    def __init__(self, advance: int, tiles: int, table, steps=None, shared: int = 0):
        self.advance, self.tiles, self.table = advance, tiles, list(table)
        self.steps = list(steps) if steps else None
        self.shared = shared

    @classmethod
    def from_data(cls, data: dict) -> "SpriteMeasure":
        return cls(data["advance"], data["tiles"], data["table"], data.get("steps"), data.get("shared", 0))

    def data(self) -> dict:
        """The measure as the editor data carries it (fonts.sprites)."""
        return {"advance": self.advance, "tiles": self.tiles, "table": self.table,
                **({"steps": self.steps} if self.steps else {}), "shared": self.shared}

    def bytes_width(self, data: bytes) -> int:
        if self.steps is None or all(self.table[b] < self.tiles for b in data):
            return self.advance * len(data)
        return sum(self.steps[b] for b in data)

    def width(self, text: str, cm: Charmap) -> int:
        return self.bytes_width(cm.encode(text, terminate=False))


def slot_items(line: str, lim: Limit) -> list[dict]:
    """The placeholders of a line with their width and coded source, for a message."""
    items = []
    for token, px in (lim.placeholders or {}).items():
        if "{" + token + "}" not in line:
            continue
        source = (lim.slot_sources or {}).get(token, coded("slot_profile"))
        detail = (lim.slot_details or {}).get(token, {})
        if detail.get("unavailable"):
            source = coded("slot_unavailable", source=source)
        parts = []
        for part in detail.get("contributors", []):
            if "text" in part:
                parts.append(coded("slot_part", text=part["text"], width=part["width"]))
            elif part.get("label") == "No prefix":
                parts.append(coded("slot_no_prefix"))
        if parts:
            source = coded("slot_parts", source=source, parts=parts)
        items.append(coded("slot_item", token="{" + token + "}", width=px, source=source))
    return items


def with_slots(name: str, slots: list[dict], **args) -> dict:
    """The problem `name`, or its form naming the inserted words of the line."""
    return coded(f"{name}_slots", slots=slots, **args) if slots else coded(name, **args)


def split_tokens(text: str) -> list[str]:
    """Split into tokens: braces, line breaks, and single characters."""
    out = []
    pos = 0
    for m in TOKEN.finditer(text):
        out.extend(text[pos:m.start()])
        out.append(m.group())
        pos = m.end()
    out.extend(text[pos:])
    return out


def paragraphs(text: str) -> list[str]:
    return text.split("\\p")


def lines_of(par: str) -> list[str]:
    return re.split(r"\\[nl]", par)


def line_width(line: str, m: Measure, cm: Charmap) -> int:
    """Width of a line as it shows, without trailing spaces before its ending codes."""
    toks = split_tokens(line)
    while toks and (toks[-1] == " " or COLOR_LIKE.match(toks[-1])):
        toks.pop()
    return m.advance(toks, cm)[0]


def shadow_overflow(line: str, width: int, m: Measure, cm: Charmap) -> bool:
    """Whether a line is exactly 1 px wider than `width` and the cut clips only shadow."""
    if m.shadow_end is None or line_width(line, m, cm) != width + 1:
        return False
    toks = split_tokens(line)
    while toks and (toks[-1] == " " or COLOR_LIKE.match(toks[-1])):
        toks.pop()
    end = m.advance(toks, cm)[2]   # the style the line ends in
    b = end.end_glyph(toks[-1], cm) if toks else None
    return b is not None and end.shadow_end is not None and bool(end.shadow_end(b))


def waits(par: str, last: bool) -> list[bool]:
    """For each line of a box: whether the printer ends it with the prompt arrow (before \\l or \\p)."""
    seps = re.findall(r"\\[nl]", par) + ([] if last else ["\\p"])
    return [i < len(seps) and seps[i] != "\\n" for i in range(len(lines_of(par)))]


def runtime_text(text: str, after: int | None, newlines: int = 0) -> str:
    """Apply the printer's newline conversion without changing stored text."""
    if after is None:
        return text

    def convert(match):
        nonlocal newlines
        newlines += 1
        return "\\l" if newlines > after else match[0]

    return re.sub(r"\\n", convert, text)


def waiting_windows(text: str, lim: Limit, newlines: int = 0) -> list[list[dict | None]]:
    """The binding waiting consumer at each printed line ending."""
    consumers = lim.consumers if lim.consumers is not None else [
        {"width": lim.width, "arrow": lim.arrow or 0, "newline_scroll_after": lim.newline_scroll_after}]
    out, box = [], []
    for sep in re.findall(r"\\[nlp]", text) + [None]:
        if sep == "\\n":
            newlines += 1
        candidates = [c for c in consumers if c["arrow"] and lim.keys is None and (
            sep in ("\\l", "\\p") or (sep == "\\n" and c.get("newline_scroll_after") is not None
                                      and newlines > c["newline_scroll_after"]))]
        box.append(min(candidates, key=lambda c: c["width"] - c["arrow"]) if candidates else None)
        if sep in ("\\p", None):
            out.append(box)
            box = []
    return out


def arrow_consumer(lim: Limit) -> dict | None:
    """The window with the least room beside its arrow."""
    if lim.keys is not None:
        return None
    candidates = [c for c in lim.consumers or [] if c["arrow"]]
    return min(candidates, key=lambda c: c["width"] - c["arrow"]) if candidates else None


def arrow_room(lim: Limit) -> int | None:
    """The width a line that waits may take beside the prompt arrow; None without an arrow."""
    if lim.keys is not None:
        return None
    consumer = arrow_consumer(lim)
    if consumer is not None:
        return consumer["width"] - consumer["arrow"]
    return lim.width - lim.arrow if lim.arrow and lim.consumers is None else None


def crowded(par: str, last: bool, lim: Limit, m: Measure, cm: Charmap, newlines: int = 0) -> list[int]:
    """Indexes of the lines of a box that wait and leave the prompt arrow no room."""
    waiting = waiting_windows(par + ("" if last else "\\p"), lim, newlines)[0]
    return [i for i, (line, consumer) in enumerate(zip(lines_of(par), waiting))
            if consumer and line_width(line, m, cm) > consumer["width"] - consumer["arrow"]]


def arrow_overflows(laid: str, lim: Limit, m: Measure, cm: Charmap) -> list[dict]:
    """Problems for laid-out lines that wait and leave the prompt arrow no room."""
    me = entry_measure(m, lim)
    pars = paragraphs(laid)
    out = []
    for i, (par, waiting) in enumerate(zip(pars, waiting_windows(laid, lim)), 1):
        for l, consumer in zip(lines_of(par), waiting):
            if not consumer or line_width(l, me, cm) <= consumer["width"] - consumer["arrow"]:
                continue
            args = {"box": i, "width": line_width(l, me, cm), "limit": consumer["width"] - consumer["arrow"],
                    "text": repr(l[:40])}
            out.append(coded("arrow_room_window", where=consumer["name"], **args) if consumer.get("name")
                       else coded("arrow_room", **args))
    return out


def column_overflows(line: str, m: Measure, cm: Charmap) -> list[tuple[int, int, str]]:
    """(end, column, text) for each {CLEAR_TO n} of a line whose text before it ends past column n."""
    toks = split_tokens(line)
    out, start = [], 0
    for x, n, i in m.advance(toks, cm)[1]:
        if x > n:
            while start < i and COLOR_LIKE.match(toks[start]):   # codes not shown
                start += 1
            out.append((x, n, "".join(toks[start:i])))
        start = i + 1
    return out


def fits(line: str, width: int, m: Measure, cm: Charmap) -> bool:
    """Whether a line shows within `width`: at most that wide, or a shadow_overflow."""
    return line_width(line, m, cm) <= width or shadow_overflow(line, width, m, cm)


def key_count(line: str) -> int:
    """Keys of a keyboard row: its characters and glyph tokens, without trailing blank keys."""
    keys = [t for t in split_tokens(line) if not COLOR_LIKE.match(t)]
    while keys and keys[-1] == " ":   # also those between spacing codes
        keys.pop()
    return len(keys)


def extra_lines(english: str, laid_out: str) -> list[tuple[int, int, int]]:
    """(box number, lines, English lines) for each box with more lines than its English box."""
    return box_lines([len(lines_of(par)) for par in paragraphs(english)], laid_out)


def box_lines(boxes, laid_out: str) -> list[tuple[int, int, int]]:
    """extra_lines against the line count of each English box."""
    out = []
    for i, (a, b) in enumerate(zip(boxes, paragraphs(laid_out)), 1):
        if len(lines_of(b)) > a:
            out.append((i, len(lines_of(b)), a))
    return out


def split_words(par: str) -> list[str]:
    """Split on spaces outside brace tokens; line breaks count as spaces."""
    text = re.sub(r"\\[nl]", " ", par)
    words, cur, depth = [], "", 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        if ch == " " and depth == 0:
            words.append(cur)
            cur = ""
        else:
            cur += ch
    words.append(cur)
    return words


DASH = "-"   # spaced hyphen standing for a dash


def keeps_with_next(word: str, tail: str = "") -> bool:
    """Whether a wrap unit is a short word kept with the next word."""
    return not tail and word.lower() in language.current().data.get("wrapping", {}).get("keep_with_next", [])


def visible_word(word: str) -> str:
    """Omit formatting tokens when recognizing word shapes."""
    return "".join(t for t in split_tokens(word) if not COLOR_LIKE.match(t))


def abbreviation_pair(left: str, right: str) -> bool:
    """Recognize configured abbreviations across formatting tokens."""
    rules = language.current().data.get("wrapping", {})
    first, second = rules.get("abbreviation_left"), rules.get("abbreviation_right")
    return bool(first and second and re.fullmatch(first, visible_word(left), re.IGNORECASE)
                and re.fullmatch(second, visible_word(right), re.IGNORECASE))


def abbreviation_start(units: list[tuple[str, str, bool]], right: str) -> int | None:
    """Find the abbreviation head across formatting-only units."""
    if "." not in right:
        return None
    for i in range(len(units) - 1, -1, -1):
        word, tail, forced = units[i]
        if tail or forced:
            return None
        if visible_word(word):
            return i if abbreviation_pair(word, right) else None
    return None


def wrap_units(par: str) -> list[tuple[str, str, bool]]:
    """Words of a paragraph as wrap units: (word, joined hyphens, break before)."""
    units: list[tuple[str, str, bool]] = []
    for li, line in enumerate(lines_of(par)):
        for wi, w in enumerate(x for x in split_words(line) if x != ""):
            if w == DASH and wi == 0 and li > 0:
                units.append((w, "", True))
            elif w == DASH and units:
                units[-1] = (units[-1][0], units[-1][1] + " " + w, units[-1][2])
            else:
                start = abbreviation_start(units, w)
                if start is None:
                    units.append((w, "", False))
                else:
                    joined = " ".join(u[0] for u in units[start:]) + " " + w
                    units[start:] = [(joined, "", units[start][2])]
    return units


def wrap_words(par: str, width: int, m: Measure, cm: Charmap, sentence_break: float | None = None,
               hard: int | None = None, keep: int | None = None) -> list[str]:
    """Greedy wrap at `width`."""
    return wrap_moves(par, width, m, cm, sentence_break, hard, keep)[0]


def wrap_moves(par: str, width: int, m: Measure, cm: Charmap, sentence_break: float | None = None,
               hard: int | None = None, keep: int | None = None) -> tuple[list[str], int]:
    """The lines of wrap_words and the number of short words it moved down."""
    words = wrap_units(par)
    lines: list[str] = []
    cur: list[tuple[str, str]] = []   # (word, joined hyphens) of the current line
    moves = 0

    def joined(units: list[tuple[str, str]]) -> str:
        return " ".join(word + tail for word, tail in units)

    def fits_after(units: list[tuple[str, str]], word: str, tail: str) -> bool:
        """Whether the unit fits at the end of a line of `units`; a joined spaced hyphen may run on up to `hard`."""
        cand = m.width(joined(units + [(word, tail)]), cm)
        return cand <= width or (bool(units) and bool(tail) and hard is not None and cand <= hard
                                 and m.width(joined(units + [(word, "")]), cm) <= width)

    for i, (word, tail, forced) in enumerate(words):
        if forced and cur:
            lines.append(joined(cur))
            cur = []
        if not cur or fits_after(cur, word, tail):
            cur.append((word, tail))
            if (sentence_break is not None and i + 1 < len(words) and re.search(r"[.!?…]$", word + tail)
                    and m.width(joined(cur), cm) >= sentence_break * width):
                lines.append(joined(cur))
                cur = []
        else:
            # short words ending a line move down while they fit
            carry: list[tuple[str, str]] = []
            while (len(cur) > 1 and keeps_with_next(*cur[-1]) and (keep is None or moves < keep)
                   and fits_after([cur[-1]] + carry, word, tail)):
                carry.insert(0, cur.pop())
                moves += 1
            lines.append(joined(cur))
            cur = carry + [(word, tail)]
    if cur or not lines:
        lines.append(joined(cur))
    return lines, moves


def wrap_keeping(par: str, width: int, m: Measure, cm: Charmap, sentence_break: float | None = None,
                 hard: int | None = None, allow: int | None = None) -> list[str]:
    """The wrap of wrap_words that keeps short words with the next word within a line allowance."""
    plain = wrap_words(par, width, m, cm, sentence_break, hard, keep=0)
    most = max(len(plain), allow or 0)
    keep = None
    while True:
        lines, moves = wrap_moves(par, width, m, cm, sentence_break, hard, keep)
        if len(lines) <= most or moves == 0:
            return lines
        keep = moves - 1


def dash_starts(par: str) -> list[bool]:
    """For each spaced hyphen of a paragraph, in order: whether it opens a line."""
    return [wi == 0 for line in lines_of(par)
            for wi, w in enumerate(x for x in split_words(line) if x != "") if w == DASH]


def wrapped_dash_lines(text: str, laid_out: str) -> list[dict]:
    """Problems for lines that start with a spaced hyphen after an automatic wrap."""
    out = []
    for i, (a, b) in enumerate(zip(paragraphs(text), paragraphs(laid_out)), 1):
        src = dash_starts(a)
        n = 0
        for line in lines_of(b):
            ws = [x for x in split_words(line) if x != ""]
            for wi, w in enumerate(ws):
                if w != DASH:
                    continue
                if wi == 0 and not (n < len(src) and src[n]):
                    out.append(coded("wrapped_dash", box=i, text=repr(line[:40])))
                n += 1
    return out


def slot_rewraps(text: str, lim: Limit, m: Measure, cm: Charmap) -> list[dict]:
    """Translator lines that fit with profile placeholder widths and not with the insertion widths."""
    if lim.kind not in ("dialogue", "battle") or not lim.placeholders:
        return []
    me = entry_measure(m, lim)
    out = []
    for i, par in enumerate(paragraphs(text), 1):
        for l in lines_of(par):
            w = line_width(l, me, cm)
            if not fits(l, lim.width, me, cm) and fits(l, lim.width, m, cm):
                out.append(with_slots("slot_rewrap", slot_items(l, lim), box=i, width=w, limit=lim.width,
                                      text=repr(l[:40])))
    return out


def short_line_ends(text: str) -> list[dict]:
    """Written lines ending with a word the wrap keeps with the next."""
    out = []
    for i, par in enumerate(paragraphs(text), 1):
        for j, line in enumerate(lines_of(par)[:-1], 1):
            words = line.rstrip(" ").split(" ")
            if words[-1] and keeps_with_next(words[-1]):
                out.append(coded("short_line_end", word=words[-1], box=i, row=j))
    return out


def inserted(tok: str, m: Measure) -> bool:
    """Whether a brace token is inserted text, measured as a placeholder."""
    if not (tok.startswith("{") and tok.endswith("}")) or COLOR_LIKE.match(tok):
        return False
    name = tok[1:-1].split()[0]
    if name in ("F8", "F9"):   # button icons, extra glyphs
        return False
    return (tok[1:-1] in m.ph or name in m.ph or name in ("FD", "PLAYER", "RIVAL", "KUN")
            or name.startswith("B_") or name.startswith("STR_VAR"))


def split_names(laid: str, lim: Limit, m: Measure) -> list[dict]:
    """Shown breaks that split a noun from the name after it, or a count from its unit."""
    rules = language.current().data.get("wrapping", {})
    heads, units = rules.get("name_heads"), rules.get("count_units", [])
    if lim.kind in ("single", "field", "battle") or not (heads or units) or "{B_" in laid:
        return []
    me = entry_measure(m, lim)
    out = []
    for i, par in enumerate(paragraphs(laid), 1):
        ls = lines_of(par)
        for j, (a, b) in enumerate(zip(ls, ls[1:]), 1):
            left = [t for t in split_tokens(a.rstrip(" ")) if not COLOR_LIKE.match(t)]
            right = [t for t in split_tokens(b) if not COLOR_LIKE.match(t)]
            words = [w for w in split_words(a) if w]
            word = visible_word(words[-1]) if words else ""
            if heads and right and inserted(right[0], me) and re.fullmatch(heads, word, re.IGNORECASE):
                out.append(coded("split_name", word=word, box=i, row=j))
            starts = [w for w in split_words(b) if w]
            first = visible_word(starts[0]) if starts else ""
            unit = next((u for u in units if first.startswith(u)), None)
            if left and inserted(left[-1], me) and unit:
                out.append(coded("split_count", unit=unit, box=i, row=j))
    return out


def dropped_breaks(text: str, lim: Limit, m: Measure, cm: Charmap) -> list[dict]:
    """Written breaks the layout drops in a paragraph whose translator lines exceed the window."""
    if lim.kind in ("single", "field"):
        return []
    me = entry_measure(m, lim)   # as the layout measures
    out = []
    shown = None   # the boxes as the game shows them
    for i, par in enumerate(paragraphs(text), 1):
        ls = lines_of(par)
        if len(ls) < 2:
            continue
        for l in ls:
            w = line_width(l, me, cm)
            if not fits(l, lim.width, me, cm):
                if shown is None:
                    shown = paragraphs(layout(text, lim, m, cm))
                out.append(coded("dropped_breaks", box=i, width=w, limit=lim.width, text=repr(l[:40]),
                                 shown=repr(shown[i - 1])))
    return out


def join_box_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    out = lines[0]
    for i, l in enumerate(lines[1:], 1):
        out += ("\\n" if i == 1 else "\\l") + l
    return out


def layout(text: str, lim: Limit, m: Measure, cm: Charmap, keep: bool = True) -> str:
    """Return text with line breaks that fit the limit where possible."""
    if lim.kind in ("single", "field"):
        return text
    m = entry_measure(m, lim)

    def wrap(par: str, width: int, sentence_break: float | None = None, hard: int | None = None,
             allow: int | None = None) -> list[str]:
        if not keep:
            return wrap_words(par, width, m, cm, sentence_break, hard, keep=0)
        return wrap_keeping(par, width, m, cm, sentence_break, hard, allow)

    # lines short-word moves may add to a fixed window:
    # up to its line limit and the English line count
    spare = 0
    if keep and lim.kind == "multiline" and lim.source_lines:
        most = sum(lim.source_lines) if lim.lines is None else min(lim.lines, sum(lim.source_lines))
        plain = layout(text, lim, m, cm, keep=False)
        spare = max(0, most - sum(len(lines_of(p)) for p in paragraphs(plain)))
    out_pars = []
    pars = paragraphs(text)
    for i, par in enumerate(pars):
        newlines = sum(p.count("\\n") for p in out_pars)
        ls = lines_of(par)
        last = i == len(pars) - 1
        # a box without written breaks also keeps the arrow room
        if all(fits(l, lim.width, m, cm) for l in ls) and (len(ls) > 1 or not crowded(par, last, lim, m, cm, newlines)) and (
                lim.kind != "multiline" or lim.lines is None or len(ls) <= lim.lines or lim.newline_scroll_after is not None):
            out_pars.append(par)  # translator's breaks fit: keep them
            continue
        if lim.kind == "multiline" and lim.newline_scroll_after is None:
            # fixed multi-line window: blank lines are structure; wrap each block
            blocks = par.split("\\n\\n")
            laid = []
            for b in blocks:
                n = len(wrap_words(b, lim.width, m, cm, keep=0))
                bl = wrap(b, lim.width, allow=n + spare)
                spare -= len(bl) - n
                laid.append("\\n".join(bl))
            out_pars.append("\\n\\n".join(laid))
            continue
        # a box takes at most its English box's lines
        allow = None
        if lim.source_lines and i < len(lim.source_lines):
            allow = lim.source_lines[i] if lim.lines is None else min(lim.source_lines[i], lim.lines)
        wrapped = wrap(par, lim.target or lim.width, lim.sentence_break, lim.width, allow)
        if any(line_width(l, m, cm) > lim.width for l in wrapped):
            wrapped = wrap(par, lim.width, allow=allow)
        join = "\\n".join if lim.newline_scroll_after is not None else join_box_lines
        while crowded(join(wrapped), last, lim, m, cm, newlines):
            waiting = waiting_windows(join(wrapped) + ("" if last else "\\p"), lim, newlines)[0]
            room = min(lim.width, min(c["width"] - c["arrow"] for c in waiting if c))
            narrow = wrap(par, min(lim.target or room, room), lim.sentence_break, room, allow)
            if narrow == wrapped or any(line_width(l, m, cm) > lim.width for l in narrow):
                break
            wrapped = narrow
        out_pars.append(join(wrapped))
    return "\\p".join(out_pars)


# the problems of a too wide line
LINE_WIDE = frozenset({"line_wide", "line_wide_window", "line_wide_slots", "line_wide_slots_window",
                       "column_wide", "column_wide_slots"})


def strip_prefixes(text: str, lim: Limit) -> str:
    """The text as the code prints it: without each prefix of lim.strip in turn that it starts with."""
    for p in lim.strip or []:
        if p and text.startswith(p):
            text = text[len(p):]
    return text


def check(text: str, lim: Limit, m: Measure, cm: Charmap) -> list[dict]:
    problems = []
    m = entry_measure(m, lim)
    try:
        raw = cm.encode(text, terminate=False)
    except CharmapError as e:
        return [encoding_problem(e)]
    if lim.chars is not None:
        n = len(raw)
        if n > lim.chars:
            problems.append(coded("field_overflow", n=n, limit=lim.chars))
    if lim.bytes is not None and len(raw) > lim.bytes:
        problems.append(coded("in_place_overflow", n=len(raw), limit=lim.bytes))
    # the code reads from inside the text
    head = b"" if lim.head is None else cm.encode(lim.head, terminate=False)
    if not raw.startswith(head):
        problems.append(coded("head_missing", head=lim.head, byte=len(head)))
    # the rest measured as printed; the head checked once
    shown = strip_prefixes(text, lim)
    if shown != text:
        cut = text[:len(text) - len(shown)]
        rest = check(shown, Limit(**{**vars(lim), "chars": None, "bytes": None, "strip": None, "head": None}),
                     m, cm)
        return problems + [noted(p, "prefix_stripped", prefix=repr(cut)) if p["code"] in LINE_WIDE else p for p in rest]
    total_lines = 0
    for par in paragraphs(runtime_text(text, lim.newline_scroll_after)):
        ls = lines_of(par)
        total_lines += len(ls)
        if lim.kind in ("single", "field") and len(ls) > 1:
            problems.append(coded("single_line_break"))
        if (lim.kind in ("dialogue", "battle") or lim.newline_scroll_after is not None) and lim.lines and len(ls) > lim.lines:
            # over two lines per box need scrolling (\\l)
            if "\\l" not in par:
                problems.append(coded("box_unscrolled", n=len(ls)))
        for l in ls:
            if lim.keys is not None:   # keys, not pixels
                n = key_count(l)
                if n > lim.keys:
                    problems.append(coded("too_many_keys", n=n, limit=lim.keys, text=repr(l[:40])))
                continue
            w = line_width(l, m, cm)
            if w > lim.width and not shadow_overflow(l, lim.width, m, cm):
                consumer = min(lim.consumers, key=lambda c: c["width"]) if len(lim.consumers or []) > 1 else None
                slots = slot_items(l, lim)
                name = "line_wide" + ("_slots" if slots else "") + ("_window" if consumer else "")
                problems.append(coded(name, width=w, limit=lim.width, text=repr(l[:40]),
                                      **({"slots": slots} if slots else {}),
                                      **({"where": consumer["name"]} if consumer else {})))
            # text past its CLEAR_TO column
            for x, n, seg in column_overflows(l, m, cm):
                problems.append(with_slots("column_wide", slot_items(seg, lim), width=x, limit=n,
                                           column=f"{{CLEAR_TO {n:02X}}}", text=repr(seg[:40])))
    if lim.kind == "multiline" and lim.newline_scroll_after is None and lim.lines and total_lines > lim.lines:
        problems.append(coded("too_many_lines", n=total_lines, limit=lim.lines))
    return problems
