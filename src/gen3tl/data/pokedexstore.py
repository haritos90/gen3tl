"""The stored form of the Pokedex datasets: each repeated value once, defaults absent.

A stored file is its document with `codec` beside the fields. For each collection path the
codec names the keys of the records written there, their defaults, derived values and value
tables. The pages read it through web/js/pokedexstore.js, which rebuilds the documents.
"""

from __future__ import annotations

import copy
import json
import re
import string
from collections import Counter
from pathlib import Path

CODEC = "codec"
NUMBER = re.compile(r"\d+")   # map keys share one path
HEX = re.compile(r"0x([0-9a-fA-F]+)")
PLACE = "{}"   # where a template takes its field
NAMES = string.ascii_letters   # records store their keys by these
SCALARS = (str, int, float, bool, type(None))


class StoreError(ValueError):
    """A stored dataset that does not decode."""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def step(path: str, key: str) -> str:
    """The path below `path` through `key`; decimal keys share one."""
    part = "*" if NUMBER.fullmatch(key) else key.replace("~", "~0").replace("/", "~1")
    return f"{path}/{part}" if path else part


def name(n: int) -> str:
    """The stored name of the n-th key of a collection."""
    size = len(NAMES)
    return NAMES[n] if n < size else NAMES[n // size - 1] + NAMES[n % size]


def _hex(rule: dict, number: int) -> str:
    return "0x" + format(number, rule["hex"])


def _fill(value, text: str):
    if isinstance(value, str):
        return value.replace(PLACE, text)
    if isinstance(value, list):
        return [_fill(item, text) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, text) for key, item in value.items()}
    return value


def _field(value) -> str:
    return value if isinstance(value, str) else str(value)


def _records(rows: list, path: str, head: dict, codec: dict) -> list:
    keys = {name(n): key for n, key in enumerate(head["keys"])}
    defaults, tables, derived = head.get("defaults", {}), head.get("tables", {}), head.get("derived", {})
    out, last = [], {}
    for stored in rows:
        values = {keys[alias]: value for alias, value in stored.items()}
        record = {}
        for key, value in values.items():
            if key not in derived:
                record[key] = copy.deepcopy(tables[key][value]) if key in tables else _decode(value, step(path, key), codec)
        for key, value in defaults.items():
            if key not in values:
                record[key] = copy.deepcopy(value)
        for key, rule in derived.items():   # offsets first: templates may read them
            if "hex" in rule:
                delta = values.get(key, rule["step"])
                if delta is None:
                    record[key] = None
                    continue
                last[key] = last.get(key, 0) + delta
                record[key] = _hex(rule, last[key])
        for key, rule in derived.items():
            if "text" in rule:
                record[key] = (_decode(values[key], step(path, key), codec) if key in values
                               else _fill(copy.deepcopy(rule["text"]), _field(record[rule["of"]])))
        out.append(dict(sorted(record.items())))
    return out


def _rows(rows: list, head: dict) -> list:
    defaults, tables, derived = head.get("defaults", {}), head.get("tables", {}), head.get("derived", {})
    out, last = [], {}
    for stored in rows:
        row = []
        for column in range(head["columns"]):
            key = str(column)
            if key in derived:
                rule = derived[key]
                delta = stored[column] if column < len(stored) else rule["step"]
                if delta is None:
                    row.append(None)
                    continue
                last[key] = last.get(key, 0) + delta
                row.append(_hex(rule, last[key]))
            elif column < len(stored):
                row.append(copy.deepcopy(tables[key][stored[column]]) if key in tables else stored[column])
            else:
                row.append(copy.deepcopy(defaults[key]))
        out.append(row)
    return out


def _decode(value, path: str, codec: dict):
    if isinstance(value, dict):
        return {key: _decode(item, step(path, key), codec) for key, item in value.items()}
    if isinstance(value, list):
        head = codec.get(path)
        if head is not None and value:
            if "columns" in head and all(isinstance(item, list) for item in value):
                return _rows(value, head)
            if "keys" in head and all(isinstance(item, dict) for item in value):
                return _records(value, path, head, codec)
        return [_decode(item, path, codec) for item in value]
    return value


def decode(stored: dict) -> dict:
    """The document of a stored dataset; a document without a codec is its own."""
    if not isinstance(stored, dict):
        raise StoreError("a stored dataset is a JSON object")
    if CODEC not in stored:
        return stored
    codec = stored[CODEC]
    try:
        return {key: _decode(value, step("", key), codec) for key, value in stored.items() if key != CODEC}
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
        raise StoreError(f"invalid stored dataset: {exc!r}") from exc


def load(path) -> dict:
    """Read and decode one stored dataset file."""
    path = Path(path)
    try:
        return decode(json.loads(path.read_text(encoding="utf-8")))
    except StoreError as exc:
        raise StoreError(f"{path}: {exc}") from exc


# The encoder chooses, per collection path, what each key stores.

ALIAS = 5   # the bytes a stored key and its separators take


def _collect(value, path: str, records: dict, rows: dict) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _collect(item, step(path, key), records, rows)
    elif isinstance(value, list) and value:
        if all(isinstance(item, dict) for item in value):
            records.setdefault(path, []).append(value)
        elif all(isinstance(item, list) and item and all(isinstance(cell, SCALARS) for cell in item)
                 for item in value):
            rows.setdefault(path, []).append(value)
        for item in value:
            _collect(item, path, records, rows)


def _nested(value) -> bool:
    """Whether a value holds a collection that a deeper path encodes."""
    if isinstance(value, dict):
        return any(_nested(item) for item in value.values())
    if isinstance(value, list):
        return any(isinstance(item, (dict, list)) for item in value)
    return False


def _formats(text) -> set[str]:
    """The formats that write an offset as this text."""
    match = HEX.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return set()
    digits = match.group(1)
    lower, upper = any(c in "abcdef" for c in digits), any(c in "ABCDEF" for c in digits)
    if lower and upper:
        return set()
    cases = {"x"} if lower else {"X"} if upper else {"x", "X"}
    widths = {len(digits)} if digits[0] == "0" and len(digits) > 1 else set(range(1, len(digits) + 1))
    return {f"0{width}{case}" for width in widths for case in cases}


def _hex_rule(lists: list[list]) -> tuple[dict, list[list]] | None:
    """A run of offsets and each list's deltas; None when a value is no offset."""
    common = None
    for values in lists:
        for value in values:
            if value is not None:
                common = _formats(value) if common is None else common & _formats(value)
                if not common:
                    return None
    if not common:
        return None
    deltas = []
    for values in lists:
        last, row = 0, []
        for value in values:
            if value is None:
                row.append(None)
                continue
            number = int(value, 16)
            row.append(number - last)
            last = number
        deltas.append(row)
    counts = Counter(delta for row in deltas for delta in row if delta is not None)
    rule = {"hex": max(common, key=lambda f: (int(f[:-1]), f[-1] == "x")), "step": counts.most_common(1)[0][0]}
    return rule, deltas


def _table(texts: list[str], nested: bool) -> list | None:
    """The distinct values, most frequent first, when indexes into them are shorter."""
    counts = Counter(texts)
    if len(counts) == len(texts):
        return None
    order = sorted(counts, key=lambda text: -counts[text])
    plain = sum(len(text) for text in texts)
    tabled = sum(len(text) + 1 for text in order) + sum(len(str(n)) * counts[text] for n, text in enumerate(order))
    return [json.loads(text) for text in order] if tabled * (2 if nested else 1) < plain else None


def _replace(value, text: str):
    if isinstance(value, str):
        return value.replace(text, PLACE)
    if isinstance(value, list):
        return [_replace(item, text) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, text) for key, item in value.items()}
    return value


def _template(records: list[dict], key: str, fields: list[str]) -> tuple[int, dict] | None:
    """The template that rebuilds most values of `key` from one field, and its hits."""
    best = None
    for field in fields:
        for sample in (records[0], records[len(records) // 2], records[-1]):
            text, written = _field(sample[field]), _json(sample[key])
            if not text or PLACE in written or text not in written:
                continue
            template = _replace(sample[key], text)
            hits = sum(1 for record in records if _json(_fill(template, _field(record[field]))) == _json(record[key]))
            if hits > 1 and (best is None or hits > best[0]):
                best = (hits, {"of": field, "text": template})
    return best


def _record_head(lists: list[list]) -> dict | None:
    records = [record for values in lists for record in values]
    keys = sorted({key for record in records for key in record})
    if len(keys) > len(NAMES) * (len(NAMES) + 1):
        return None
    full = [key for key in keys if all(key in record for record in records)]
    sources = [key for key in full if all(type(record[key]) in (int, str) for record in records)]
    defaults, derived, tables = {}, {}, {}
    for key in full:
        texts = [_json(record[key]) for record in records]
        plain = sum(len(text) + ALIAS for text in texts)
        options = []
        if found := _hex_rule([[record[key] for record in values] for values in lists]):
            rule, deltas = found
            cost = sum(len(_json(delta)) + ALIAS for row in deltas for delta in row if delta != rule["step"])
            options.append((cost, "derived", rule))
        read = any(rule.get("of") == key for rule in derived.values())   # a template reads it
        if not read and all(isinstance(record[key], (str, dict, list)) and not _nested(record[key])
                            for record in records):
            fields = [field for field in sources if field != key and "text" not in derived.get(field, {})]
            if found := _template(records, key, fields):
                template = found[1]
                cost = sum(len(text) + ALIAS for record, text in zip(records, texts)
                           if _json(_fill(template["text"], _field(record[template["of"]]))) != text)
                options.append((cost + len(_json(template)), "derived", template))
        value, count = Counter(texts).most_common(1)[0]
        if count > 1:
            options.append((plain - count * (len(value) + ALIAS) + len(value), "default", json.loads(value)))
        if options:
            cost, kind, choice = min(options, key=lambda option: option[0])
            if cost < plain:
                (derived if kind == "derived" else defaults)[key] = choice
    for key in keys:
        if key in derived:
            continue
        stored = [record[key] for record in records
                  if key in record and (key not in defaults or _json(record[key]) != _json(defaults[key]))]
        if stored and (table := _table([_json(value) for value in stored], any(map(_nested, stored)))):
            tables[key] = table
    return {"keys": keys, **{part: rules for part, rules in
                             (("defaults", defaults), ("derived", derived), ("tables", tables)) if rules}}


def _row_head(lists: list[list]) -> dict | None:
    rows = [row for values in lists for row in values]
    columns = len(rows[0])
    if any(len(row) != columns for row in rows):
        return None
    defaults, derived, tables = {}, {}, {}
    trailing = True   # only the last cells of a row are left out
    for column in reversed(range(columns)):
        key = str(column)
        if found := _hex_rule([[row[column] for row in values] for values in lists]):
            derived[key] = found[0]
            continue
        texts = [_json(row[column]) for row in rows]
        value, count = Counter(texts).most_common(1)[0]
        if trailing and count > 1:
            defaults[key] = json.loads(value)
        trailing = trailing and count > 1
        if table := _table(texts, False):
            tables[key] = table
    if not derived and not tables:
        return None
    return {"columns": columns, **{part: dict(sorted(rules.items(), key=lambda item: int(item[0])))
                                   for part, rules in (("defaults", defaults), ("derived", derived),
                                                       ("tables", tables)) if rules}}


def _encode_records(values: list, path: str, head: dict, codec: dict) -> list:
    alias = {key: name(n) for n, key in enumerate(head["keys"])}
    defaults, tables, derived = head.get("defaults", {}), head.get("tables", {}), head.get("derived", {})
    index = {key: {_json(value): n for n, value in enumerate(table)} for key, table in tables.items()}
    default = {key: _json(value) for key, value in defaults.items()}
    out, last = [], {}
    for record in values:
        stored = {}
        for key, value in record.items():
            rule = derived.get(key)
            if rule is not None and "hex" in rule:
                if value is None:
                    stored[alias[key]] = None
                    continue
                number = int(value, 16)
                if number - last.get(key, 0) != rule["step"]:
                    stored[alias[key]] = number - last.get(key, 0)
                last[key] = number
            elif rule is not None:
                if _json(_fill(rule["text"], _field(record[rule["of"]]))) != _json(value):
                    stored[alias[key]] = _encode(value, step(path, key), codec)
            elif key in default or key in index:
                text = _json(value)
                if text != default.get(key):
                    stored[alias[key]] = index[key][text] if key in index else _encode(value, step(path, key), codec)
            else:
                stored[alias[key]] = _encode(value, step(path, key), codec)
        out.append(stored)
    return out


def _encode_rows(values: list, head: dict) -> list:
    defaults, tables, derived = head.get("defaults", {}), head.get("tables", {}), head.get("derived", {})
    index = {key: {_json(value): n for n, value in enumerate(table)} for key, table in tables.items()}
    out, last = [], {}
    for row in values:
        stored, omitted = [], []
        for column, value in enumerate(row):
            key = str(column)
            if key in derived and value is not None:
                number = int(value, 16)
                stored.append(number - last.get(key, 0))
                omitted.append(stored[-1] == derived[key]["step"])
                last[key] = number
            elif key in derived:
                stored.append(None)
                omitted.append(False)
            else:
                stored.append(index[key][_json(value)] if key in tables else value)
                omitted.append(key in defaults and _json(value) == _json(defaults[key]))
        while omitted and omitted[-1]:
            stored.pop()
            omitted.pop()
        out.append(stored)
    return out


def _encode(value, path: str, codec: dict):
    if isinstance(value, dict):
        return {key: _encode(item, step(path, key), codec) for key, item in value.items()}
    if isinstance(value, list):
        head = codec.get(path)
        if head is not None and value:
            if "columns" in head and all(isinstance(item, list) for item in value):
                return _encode_rows(value, head)
            if "keys" in head and all(isinstance(item, dict) for item in value):
                return _encode_records(value, path, head, codec)
        return [_encode(item, path, codec) for item in value]
    return value


def encode(doc: dict) -> dict:
    """The stored form of a dataset document; decode() returns the document."""
    if CODEC in doc:
        raise StoreError(f"a dataset field is named {CODEC}")
    doc = json.loads(_json(doc))
    records, rows = {}, {}
    _collect(doc, "", records, rows)
    codec = {}
    for path, lists in records.items():
        if sum(map(len, lists)) > 1 and (head := _record_head(lists)):
            codec[path] = head
    for path, lists in rows.items():
        if path not in records and sum(map(len, lists)) > 1 and (head := _row_head(lists)):
            codec[path] = head
    stored = {key: _encode(value, step("", key), codec) for key, value in doc.items()}
    if codec:
        stored[CODEC] = dict(sorted(codec.items()))
    if _json(decode(json.loads(_json(stored)))) != _json(doc):
        raise StoreError("the stored dataset does not decode to its document")
    return stored
