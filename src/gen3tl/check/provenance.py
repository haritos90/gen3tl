"""Explanations of limits, reach and inserted widths: one template per code, stored as the code with its arguments."""

from __future__ import annotations

import re

# {name}: an argument; nested names hold explanations
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
    "slot_profile": "profile width",
    "slot_profile_estimate": "profile width, unverified estimate",
    "slot_default": "default width",
    "slot_context": "context records",
    "slot_profile_above": "profile width, above the widest known {source} {width} px",
    "slot_profile_estimate_above": "profile width, unverified estimate, above the widest known {source} {width} px",
    "slot_reasons": "{source} ({reasons})",
    "slot_no_fill": "no fill known",
    "slot_unfilled": "{unfilled} of {uses} walked paths set no value",
    "slot_unnamed": "a value the scripts do not name: {kinds}",
    "slot_unwalked": "{references} script references not walked",
    "slot_by_code": "shown by code",
    "slot_word": "{id} {text}",
    "slot_untranslated": "{id} (untranslated)",
    "slot_empty": "{id} '' (translated as empty)",
    "slot_record": "{kind} {word}",
    "slot_table": "widest of table {table}: {word}",
    "slot_native": "native table {table}: {ids}",
    "slot_number_variable": "number of up to {digits} digits (a variable)",
    "slot_number": "number {value}",
    "slot_record_number": "number of {digits} digits, the record's {value}",
    "slot_label": "{label}",
    "slot_widest_source": "widest source {id}",
    "slot_digit": "number of {digits} digit",
    "slot_digits": "number of {digits} digits",
    "slot_width": "{width} px",
    "slot_width_estimate": "{width} px, unverified estimate",
    "slot_english": "{fill}, English {source_width} px",
    "slot_fill": "{fill}, {where}",
    "slot_code_fill": "{fill}, code fill",
}
NESTED = frozenset({"rule", "via", "source", "word", "fill"})
# list arguments and their separators
JOINED = {"reasons": "; ", "kinds": ", ", "ids": " + "}
FIELD = re.compile(r"\{(\w+)\}")


def code(name: str, /, **args) -> dict:
    """The explanation `name` with its arguments, in the order of its template."""
    fields = list(dict.fromkeys(FIELD.findall(MESSAGES[name])))
    if set(args) != set(fields):
        raise ValueError(f"{name}: arguments {sorted(args)} differ from {fields}")
    return {"code": name, **{field: args[field] for field in fields}}


def canonical(coded: dict) -> dict:
    """An explanation with every argument, nested ones too, in the order of its template."""
    def value(name, item):
        if name in JOINED:
            return [canonical(part) if isinstance(part, dict) else part for part in item]
        return canonical(item) if name in NESTED and isinstance(item, dict) else item
    return code(coded["code"], **{name: value(name, item) for name, item in coded.items() if name != "code"})


def formatted(coded) -> str:
    """The English text of an explanation; a plain string is its own text."""
    if isinstance(coded, str):
        return coded

    def value(name):
        item = coded[name]
        if name in JOINED:
            return JOINED[name].join(formatted(part) for part in item)
        return formatted(item) if name in NESTED else str(item)
    return FIELD.sub(lambda m: value(m[1]), MESSAGES[coded["code"]])
