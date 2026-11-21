"""Provenance texts of limits and reach: one template per code, stored as the code with its arguments."""

from __future__ import annotations

import re
from functools import lru_cache

# {name}: an argument; via and rule nest provenance texts
MESSAGES = {
    "record_window": "[[layout.record_window]] {name}",
    "record_window_where": "[[layout.record_window]] {name}: {where}",
    "table_window": "[[layout.table_window]] {name}",
    "table_window_where": "[[layout.table_window]] {name}: {where}",
    "policy_table_window": "[language policy] table_window.{name}",
    "policy_table_window_where": "[language policy] table_window.{name}: {where}",
    "layout_window": "[[layout.window]] {name}",
    "layout_window_where": "[[layout.window]] {name}: {where}",
    "layout_default": "[layout] {key}",
    "box_width": "[layout.box_width] {box}: the message box, measured in game",
    "box_width_via": "[layout.box_width] {box}: the message box, measured in game ({via})",
    "box_lines": "two lines per message box",
    "box_lines_via": "two lines per message box ({via})",
    "window_box": "[layout.window_box] {entry}",
    "window_width": "[layout.window_width] {entry}: measured in game",
    "window_lines": "[layout.window_lines] {entry}: measured in game",
    "policy_window_width": "[language policy] window_width.{entry}: text budget",
    "english_widest": "the English: its widest line",
    "english_file_widest": "the English: the widest line of {file}",
    "english_run_widest": "the English: the widest line of its pointer-table run",
    "english_one_line": "the English: one line",
    "english_line_count": "the English: its line count",
    "english_box_lines": "the English: its box lines",
    "english_file_lines": "the English: the most lines of {file}",
    "table_field_window": "[[table]] {table} window: {where}",
    "table_buffer": "[[table]] {table} buffer: {where}",
    "table_one_line": "[[table]] {table}: a one-line field",
    "table_field": "[[table]] {table}: a field of {bytes} bytes with the terminator",
    "table_field_unterminated": "[[table]] {table}: a field of {bytes} bytes",
    "table_field_byte": "[[table]] {table}: a field of {bytes} byte with the terminator",
    "table_field_byte_unterminated": "[[table]] {table}: a field of {bytes} byte",
    "table_long_name": "[[table]] {table}: a long-name pointer of {bytes} bytes with the terminator",
    "keyboard_columns": "[keyboard] columns: the key cells of its keyboard",
    "pack_keyboard_columns": "keyboard layout columns: the columns its pack keyboard reaches",
    "in_place": "in place: its {bytes} bytes with the terminator, a reference the build cannot rewrite ({references})",
    "table_base": "[[strings.table_base]] {id}: in place in its {bytes}-byte field with the terminator",
    "interior": "[[strings.interior]] {pointer}: read from byte {offset}, in place in its {bytes} bytes",
    "centred": "{rule}, centred against {reference}",
    "host": "the window of {host}, which inserts it: {rule}",
    "host_default": "its host",
    "reach_study": "reach study: {class}",
    "reach_study_root": "reach study: {class} ({root})",
}
NESTED = frozenset({"rule", "via"})
FIELD = re.compile(r"\{(\w+)\}")


def text(code: str, **args) -> str:
    """The text of `code` with its arguments."""
    return MESSAGES[code].format(**args)


def formatted(coded) -> str:
    """The text of a coded provenance; a plain string is its own text."""
    if isinstance(coded, str):
        return coded
    return FIELD.sub(lambda m: formatted(coded[m[1]]) if m[1] in NESTED else str(coded[m[1]]),
                     MESSAGES[coded["code"]])


@lru_cache(maxsize=None)
def _patterns() -> list[tuple[str, re.Pattern]]:
    def pattern(template):
        parts = FIELD.split(template)
        return re.compile("".join(re.escape(part) if n % 2 == 0 else f"(?P<{part}>.*?)"
                                  for n, part in enumerate(parts)) + r"\Z", re.S)
    # wrappers, then the most literal: an argument never swallows structure
    order = sorted(MESSAGES, key=lambda code: (not NESTED & set(FIELD.findall(MESSAGES[code])),
                                               -len(FIELD.sub("", MESSAGES[code]))))
    return [(code, pattern(MESSAGES[code])) for code in order]


def _value(value: str):
    return int(value) if value.isascii() and value.isdigit() and str(int(value)) == value else value


@lru_cache(maxsize=None)
def _coded(value: str):
    for code, pattern in _patterns():
        m = pattern.match(value)
        if m is None:
            continue
        args = {}
        for name, arg in m.groupdict().items():
            args[name] = _coded(arg) if name in NESTED else _value(arg)
            if args[name] is None:
                break
        else:
            out = {"code": code, **args}
            if formatted(out) == value:
                return out
    return None


def coded(value: str) -> dict:
    """The code and arguments that reproduce a provenance text, else ValueError."""
    out = _coded(value)
    if out is None:
        raise ValueError(f"provenance without a message code: {value!r}")
    from copy import deepcopy
    return deepcopy(out)
