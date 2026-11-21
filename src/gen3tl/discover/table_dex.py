"""Dex records and aliases recovered from native readers and Gen 1 anchors."""

from __future__ import annotations

import math

from .foundations import evidence
from .table_core import _issue, _record, _set, text


def _maps(rom, descriptor, readers, species):
    found = []
    for _, analysis in readers.analyze(descriptor["map_reader"]):
        for reader in analysis.accessors:
            if reader.width != 2 or len(reader.indices) != 1:
                continue
            stride = reader.indices[0][1]
            base = reader.base + reader.field_offset
            if stride != reader.width or not 0 <= base <= len(rom) - species.count * stride:
                continue
            values = [rom.u16(base + number * stride) for number in range(species.count)]
            if values[0] == 0 and values[1:4] == [1, 2, 3] and set(values) == set(range(max(values) + 1)):
                found.append((base, stride, max(values) + 1, reader))
    return found


def run(rom, result, config, knowledge, readers, bindings, index):
    descriptor, anchors = knowledge.get("dex"), config.get("dex")
    species = bindings.get("species")
    if not descriptor or not anchors or species is None:
        return
    maps = _maps(rom, descriptor, readers, species)
    if len({(base, stride) for base, stride, _, _ in maps}) != 1:
        _issue(result, "pokedex.dex.map", "Native slot-to-dex arithmetic and the complete slot domain do not select one mapping.",
               "semantic-reader+slot-domain", candidates=len(maps))
        return
    map_base, map_stride, count, map_reader = maps[0]
    map_proof = evidence("Native one-based indexing normalizes to a complete slot map with a contiguous dex domain.",
        "semantic-reader+normalized-index+slot-domain", [literal.pool for literal in map_reader.literals],
        map_reader.code_sites, stride=map_stride, width=map_reader.width, slots=species.count, dex_records=count)
    _set(result, "pokedex.dex.map", {"base": map_base, "stride": map_stride, "width": map_reader.width}, map_proof)
    candidates = []
    for _, analysis in readers.analyze(descriptor["record_reader"]):
        for reader in analysis.accessors:
            coefficients = [value for _, value in reader.indices]
            if reader.width != 2 or not coefficients:
                continue
            base, stride, height = reader.base, math.gcd(*coefficients), reader.field_offset
            if not 0 < height < stride or not 0 <= base <= len(rom) - count * stride:
                continue
            if not all((value := text(rom, base + number * stride, height, name=True)) is not None
                       and value[0] == category and rom.u16(base + number * stride + height) == size
                       for number, category, size in zip(anchors["indices"], anchors["categories"], anchors["height"])):
                continue
            weights = [offset for offset in range(0, stride - 1, 2) if all(
                rom.u16(base + number * stride + offset) == value
                for number, value in zip(anchors["indices"], anchors["weight"]))]
            pointers = [offset for offset in range(0, stride - 3, 4) if all(
                (pointer := rom.ptr_at(base + number * stride + offset)) is not None
                and text(rom, pointer, min(2048, len(rom) - pointer)) is not None
                for number in range(count))]
            if len(weights) != 1 or len(pointers) != 1:
                continue
            weight, description = weights[0], pointers[0]
            if not all(text(rom, base + number * stride, height, name=True) is not None for number in range(count)):
                continue
            native = index.readers(base, stride, offsets=range(stride))
            if not any(row["field"] == weight and row["width"] == 2 for row in native):
                continue
            description_readers = readers.fields(descriptor["description_reader"], base)
            matched = [row for row in description_readers if row.field_offset == description and row.width == 4
                       and math.gcd(*(value for _, value in row.indices)) == stride]
            if not matched:
                continue
            native.extend({"site": row.site, "field": row.field_offset, "width": row.width} for row in matched)
            candidates.append((base, stride, height, weight, description, reader, native))
    identities = {row[:5] for row in candidates}
    if len(identities) != 1:
        _issue(result, "pokedex.dex.records", "Native record arithmetic, categories, sizes and complete description pointers disagree.",
               "semantic-reader+category-size-anchors+pointer-domain", candidates=len(identities))
        return
    base, stride, height, weight, description, reader, native = candidates[0]
    proof = evidence("Categories and sizes anchor the decoded layout; native reads and the entire mapped dex domain agree.",
        "semantic-reader+category-size-anchors+pointer-domain", [base, map_base],
        [row["site"] for row in native], stride=stride, rows=count, category_bytes=height,
        description_offset=description, weight_offset=weight)
    _set(result, "pokedex.dex.records", {"base": base, "stride": stride}, proof)
    _set(result, "pokedex.dex.records.fields.category", 0, proof)
    _set(result, "pokedex.dex.records.fields.height", height, proof)
    _set(result, "pokedex.dex.records.fields.weight", {"offset": weight, "width": 2}, proof)
    _set(result, "pokedex.dex.records.fields.description", {"offset": description, "width": 4}, proof)
    value = {"name": "pokedex_categories", "base": base, "stride": stride, "count": count,
             "field_offset": 0, "field_length": height}
    result.data.setdefault("table", []).append(value)
    _record(result, "table[name=pokedex_categories]", "table", value, proof)
    value = {"name": "pokedex_entries", "base": base + description, "stride": stride, "count": count,
             "fields": [0], "species_dex_map": True}
    result.data.setdefault("strings", {}).setdefault("pointer_table", []).append(value)
    alias_proof = evidence("The description-table alias reuses the record layout and the independently recovered slot map.",
        "shared-binding+dex-map", [base + description, map_base], reader.code_sites, stride=stride, rows=count)
    _record(result, "strings.pointer_table[name=pokedex_entries]", "table", value, alias_proof)
