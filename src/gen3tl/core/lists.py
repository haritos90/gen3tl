"""Order-keeping list helpers."""

from __future__ import annotations


def append_unique(items: list, value) -> None:
    """Append `value` unless `items` already holds an equal one."""
    if value not in items:
        items.append(value)
