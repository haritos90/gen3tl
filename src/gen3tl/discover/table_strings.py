"""Credit record arrays with distinct current-reader and unreferenced tiers."""

from __future__ import annotations

import struct

from ..core.charmap import Charmap
from ..core.rom import ROM_BASE
from .foundations import evidence
from .table_core import _issue, _record, text


def _positions(data, pattern):
    start, found = 0, []
    while (at := data.find(pattern, start)) >= 0:
        found.append(at)
        start = at + len(pattern)
    return found


def _pair(rom, at):
    if not 0 <= at <= len(rom) - 8:
        return False
    pointers = [rom.ptr_at(at + offset) for offset in (0, 4)]
    return all(pointer is not None and text(rom, pointer, min(2048, len(rom) - pointer)) is not None
               for pointer in pointers)


def _labels(rom, index, label):
    found = set()
    for at in _positions(rom.data, Charmap().encode(label)[:-1]):
        for start in range(max(0, at - 64), at + 1):
            if start not in index.pointers:
                continue
            value = text(rom, start, min(2048, len(rom) - start))
            if value is not None and value[0].replace("\\n", "\n").strip() == label:
                found.add(start)
    return found


def run(rom, result, config, index, knowledge=None, readers=None):
    declaration = config.get("credits")
    if not declaration:
        return
    headings = {at for label in declaration["headings"] for at in _labels(rom, index, label)}
    names = _labels(rom, index, declaration["names"])
    starts = set()
    for heading in headings:
        for at in _positions(rom.data, struct.pack("<I", ROM_BASE + heading)):
            if at % 4 == 0 and at + 8 <= len(rom) and rom.ptr_at(at + 4) in names:
                starts.add(at)
    candidates = {"credits": [], "credits-firered": []}
    for base in sorted(starts):
        stride = next((step for step in range(8, 65, 4) if _pair(rom, base + step)), None)
        if stride is None:
            continue
        count = 0
        while count < 512 and _pair(rom, base + count * stride):
            count += 1
        if not 1 < count < 512:
            continue
        native = index.readers(base, stride, offsets=range(stride))
        if readers is not None:
            for role in (knowledge or {}).get("credit_readers", []):
                for reader in readers.fields(role, base):
                    if any(value == stride for _, value in reader.indices):
                        native.append({"site": reader.site, "field": reader.field_offset, "width": reader.width})
        fields = {row["field"] for row in native if row["width"] == 4}
        direct = _positions(rom.data, struct.pack("<I", ROM_BASE + base))
        if {0, 4} <= fields:
            candidates["credits"].append((base, stride, count, native, direct))
        elif not direct:
            candidates["credits-firered"].append((base, stride, count, native, direct))
    for name, rows in candidates.items():
        identifier = f"strings.pointer_table[name={name}]"
        if len(rows) != 1:
            _issue(result, identifier, "Anchored credit pairs and reader/reference evidence do not select one complete array.",
                   "credit-pair-anchors+record-boundary", candidates=len(rows))
            continue
        base, stride, count, native, direct = rows[0]
        value = {"name": name, "base": base, "stride": stride, "count": count, "fields": [0, 4]}
        if name == "credits":
            proof = evidence("Anchored headings and names form bounded rows read by native heading/name accessors.",
                "credit-pair-anchors+record-boundary+accessor", [base, *direct],
                [row["site"] for row in native], stride=stride, rows=count, fields=2)
            tier = "crossref"
        else:
            proof = evidence("The distinct anchored copy has complete row shape and no direct base-pointer reference; live reach is unproved.",
                "credit-pair-anchors+unreferenced-shape", [base], stride=stride, rows=count,
                fields=2, direct_base_references=0)
            tier = "shape"
        result.data.setdefault("strings", {}).setdefault("pointer_table", []).append(value)
        _record(result, identifier, "table", value, proof, tier)
