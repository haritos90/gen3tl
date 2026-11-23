"""The checks of one translated entry, shared by check batch and the editor."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from ..core.notes import has_prose
from ..core.charmap import Charmap, CharmapError
from .layout import (Limit, Measure, SpriteMeasure, arrow_overflows, box_lines, check, code_arg, dropped_breaks,
                     entry_measure, extra_lines, layout, line_width, shadow_overflow, short_line_ends, slot_rewraps,
                     split_names, wrapped_dash_lines)
from .provenance import formatted

BRACE = re.compile(r"\{[^}]*\}")
# spacing codes: amounts follow the language's glyph widths;
# only their number must match (naming keyboard rows)
SPACING = re.compile(r"\{(CLEAR|CLEAR_TO) [0-9A-F]{2}\}")
UNMEASURED = " [unmeasured limit: the English width]"
UNMEASURED_LINES = " [unmeasured limit: the English line count]"
UNVERIFIED = " [unverified limit: an estimate for this language]"


@dataclass(frozen=True)
class Source:
    """What the checks read of an English line without its text: brace tokens, box line counts, emptiness."""
    tokens: tuple[str, ...]
    boxes: tuple[int, ...]
    blank: bool = False


TEXT_CHECKS = ("typography", "glossary")   # what a Source cannot check


def token_counts(text) -> Counter:
    tokens = text.tokens if isinstance(text, Source) else BRACE.findall(text)
    return Counter(SPACING.sub(r"{\1}", t) for t in tokens)


def placeholders_differ(english: str | Source, target: str, optional: set[str], leading=()) -> str | None:
    """The placeholder finding of a translation, or None when its brace tokens match the English."""
    src, dst = token_counts(english), token_counts(target)
    first = BRACE.match(target)   # the leading token
    if first:
        t = SPACING.sub(r"{\1}", first.group())
        if t in leading and dst[t] > src[t]:
            dst[t] -= 1
    missing, extra = src - dst, dst - src
    if extra or any(t not in optional for t in missing):
        return f"placeholders differ: missing {dict(missing)} extra {dict(extra)}"
    return None


def droppable_tokens(rules: dict, *, validated: bool = False) -> dict[str, set[str]]:
    """Entry id -> brace tokens its translation may leave out, from the [[qa.droppable]] rules."""
    out: dict[str, set[str]] = {}
    for d in rules.get("droppable", []):
        if not validated and not has_prose(d.get("evidence")):
            raise ValueError(f"[[qa.droppable]] for {d.get('ids')} has no evidence")
        for sid in d["ids"]:
            out.setdefault(sid, set()).update(d["tokens"])
    return out


def addable_tokens(rules: dict, *, validated: bool = False) -> dict[str, dict[str, dict]]:
    """Return permitted leading brace tokens keyed by entry ID."""
    out: dict[str, dict[str, dict]] = {}
    for d in rules.get("addable", []):
        if not validated and not has_prose(d.get("evidence")):
            raise ValueError(f"[[qa.addable]] for {d.get('ids')} has no evidence")
        if "window" in d and "x" not in d:
            raise ValueError(f"[[qa.addable]] for {d.get('ids')} has a window and no x")
        for sid in d["ids"]:
            out.setdefault(sid, {}).update({t: d for t in d["tokens"]})
    return out


def centring_problems(text: str, leading: dict[str, dict], m: Measure, cm: Charmap) -> list[str]:
    """A text against the centre of the window of its {CLEAR_TO} rule."""
    rule = leading.get("{CLEAR_TO}")
    if not rule or "window" not in rule:
        return []
    first = BRACE.match(text)
    n = code_arg(first.group(), "CLEAR_TO") if first else None
    rest = text if n is None else text[first.end():]
    want = max(0, (rule["window"] - line_width(rest, m, cm)) // 2 - rule["x"])
    if (n or 0) == want:
        return []
    return [f"not centred in the {rule['window']} px window: lead with {{CLEAR_TO {want:02X}}}"]


def typography_problems(english: str | None, target: str, forbidden: dict[str, str], keys: bool = False) -> list[str]:
    """The [qa.forbidden] rules a translation breaks, as reasons; `keys` marks a keyboard row."""
    if english is None:
        checked = re.sub(r" {2,}", " ", target) if keys else target
        return [why for pat, why in forbidden.items() if re.search(pat, checked)]
    # trailing padding is allowed where the English pads
    checked = target.rstrip(" ") if english.endswith(" ") else target
    # leading caption-centring spaces too
    if english.startswith(" "):
        checked = checked.lstrip(" ")
    # and inner space runs as long as the English ones
    runs = Counter(len(r) for r in re.findall(r" {2,}", english.strip(" ")))

    def excused(m: re.Match) -> str:
        if keys:   # a run of blank keys is no spacing
            return " "
        if runs[len(m.group())]:
            runs[len(m.group())] -= 1
            return " "
        return m.group()
    checked = re.sub(r" {2,}", excused, checked)
    return [why for pat, why in forbidden.items() if re.search(pat, checked)]


def window_widths(text: str, windows: list[dict], measures: dict) -> list[dict]:
    """A text measured in each window of the other font styles that show it."""
    out = []
    for w in windows:
        if w["font"] not in measures:
            continue
        m, cm = measures[w["font"]]
        if bool(w.get("sprite")) != isinstance(m, SpriteMeasure):
            continue
        if w.get("sprite"):
            n, shadow = m.width(text + w.get("suffix", ""), cm), False
        else:
            n = line_width(text, m, cm)
            shadow = n > w["width"] and shadow_overflow(text, w["width"], m, cm)
        out.append({"font": w["font"], "where": w["where"], "width": w["width"], "value": n,
                    "over": n > w["width"] and not shadow, "shadow": shadow})
    return out


def window_problems(text: str, windows: list[dict], measures: dict[str, tuple[Measure, Charmap]]) -> list[str]:
    """A text against the measured windows of other font styles that show it."""
    return [f"line too wide: {w['value']} > {w['width']} px in {w['where']} ({w['font']})"
            for w in window_widths(text, windows, measures) if w["over"]]


def pair_width(text: str, pair: dict, measures: dict) -> int | None:
    """A name and the widest name drawn beside it, in px (check.namepairs)."""
    m, cm = measures.get(pair["font"], (None, None))
    if not isinstance(m, SpriteMeasure):
        return None
    return m.width(text + pair.get("suffix", ""), cm) + pair["partner"]["width"] + m.shared


def pair_problems(text: str, pair: dict | None, measures: dict) -> list[str]:
    """A name whose widest pair runs past the pair's width, naming the partner."""
    n = pair_width(text, pair, measures) if pair else None
    if n is None or n <= pair["width"]:
        return []
    p, s = pair["partner"], pair["partner"]["species"]
    return [f"pair too wide: {n} > {pair['width']} px in {pair['where']} ({pair['font']}) "
            f"with «{p.get('text', p['id'])}» {p['id']}, {p['width']} px, "
            f"outline {measures[pair['font']][0].shared} px, for «{s.get('text', s['id'])}» {s['id']}"]


def unmeasured(problems: list[str], lim: Limit) -> list[str]:
    """Problems against a limit taken from the English or an unmeasured policy width, marked as such."""
    src = lim.sources or {}
    wide = src.get("width", {}).get("kind") == "source"
    guess = bool(src.get("width", {}).get("unverified"))
    many = src.get("lines", {}).get("kind") == "source"
    out = []
    for p in problems:
        width_match = f" > {lim.width} px: " in p or f" > {lim.width} px in " in p
        if wide and width_match:
            p += UNMEASURED
        elif guess and width_match:
            p += UNVERIFIED
        elif many and p.startswith("too many lines: ") and p.endswith(f" > {lim.lines}"):
            p += UNMEASURED_LINES
        out.append(p)
    return out


def instant_print_problems(text: str, laid: str, lim: Limit) -> list[str]:
    """Refuse waits that discard an instant window's text."""
    if not any(re.search(r"\\[lp]|\{\s*F[AB]\s*\}", value) for value in (text, laid)):
        return []
    return [f"scroll and page codes are not supported in {name}: "
            "this window prints instantly and drops the remaining text"
            for name in dict.fromkeys(formatted(c["name"]) for c in lim.consumers or [] if c.get("instant_print"))]


def entry_problems(english: str | Source, text: str, lim: Limit, measure: Measure, cm: Charmap,
                   optional: set[str] | None = None, forbidden: dict[str, str] | None = None,
                   windows: list[dict] | None = None, measures: dict | None = None,
                   matcher=None, leading: dict[str, dict] | None = None,
                   pair: dict | None = None) -> tuple[str, list[str]]:
    """The laid-out text of a translation and the problems check batch reports for it, in order."""
    try:
        cm.encode(text, terminate=False)
    except CharmapError as exc:
        return text, [f"not encodable: {exc}"]
    out = []
    facts = isinstance(english, Source)
    if text == "" and (not english.blank if facts else english):
        out.append("empty translation of a non-empty English: imports as an intended empty translation (warning)")
    if placeholders_differ(english, text, optional or set(), leading or {}):
        out.append("placeholders differ")
    laid = layout(text, lim, measure, cm)
    out += instant_print_problems(text, laid, lim)
    out += check(laid, lim, measure, cm) + window_problems(text, windows or [], measures or {})
    out += pair_problems(text, pair, measures or {})
    out += wrapped_dash_lines(text, laid)
    out += arrow_overflows(laid, lim, measure, cm)
    out += [p + " (warning)" for p in slot_rewraps(text, lim, measure, cm)]
    out += [p + " (warning)" for p in dropped_breaks(text, lim, measure, cm)]
    if lim.kind not in ("single", "field") and not dropped_breaks(text, lim, measure, cm):   # written breaks kept
        out += [p + " (warning)" for p in short_line_ends(text)]
    out += [p + " (warning)" for p in split_names(laid, lim, measure)]
    out += [f"box {box} wraps to {n} lines, the English box has {n_en} (warning)"
            for box, n, n_en in (box_lines(english.boxes, laid) if facts else extra_lines(english, laid))]
    if not facts:
        out += [f"typography: {why}"
                for why in typography_problems(english, text, forbidden or {}, lim.keys is not None)]
    me = entry_measure(measure, lim)   # centring measures the entry's style
    out += [p + " (warning)" for p in centring_problems(text, leading or {}, me, cm)]
    if matcher is not None and not facts:
        out += [f"glossary term {t.source_text!r} -> {t.translation!r} not found (warning)"
                for t in matcher.missing(english, text)]
    return laid, unmeasured(out, lim)
