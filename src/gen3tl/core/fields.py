"""Validated pointer conversions for inline name fields."""

from __future__ import annotations

from .notes import has_prose


def long_name_fields(table: dict) -> dict[int, dict]:
    """Declared entries and byte capacities, including the terminator."""
    rules = table.get("long_name_fields", [])
    if not isinstance(rules, list):
        raise ValueError("long_name_fields must be an array")
    if rules and (table.get("long_name_pointers") is not True
                  or not table.get("terminated", True) or table.get("field_length", 0) < 4):
        raise ValueError("long_name_fields require terminated fields with pointer dispatch")
    result = {}
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"index", "length", "evidence"}:
            raise ValueError("long_name_fields require index, length and evidence")
        index, length, evidence = rule["index"], rule["length"], rule["evidence"]
        if type(index) is not int or not 0 <= index < table["count"] or index in result:
            raise ValueError("long_name_fields need distinct indices within the table")
        if type(length) is not int or length <= table["field_length"]:
            raise ValueError("long_name_fields length must exceed the inline field")
        if not has_prose(evidence, single_line=True):
            raise ValueError("long_name_fields need one-line consumer evidence")
        result[index] = rule
    return result
