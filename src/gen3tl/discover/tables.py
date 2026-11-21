"""Header, anchor and indexed-reader evidence for ROM table bindings."""

from __future__ import annotations

from ..core.rom import Rom
from .foundations import evidence
from . import table_dex, table_encounters, table_evolution, table_graphics, table_learnsets, \
    table_maps, table_mining, table_sources, table_strings, table_trainers
from .table_core import (SOURCE, Index, Names, Result, _issue, _record, _set, find_names,
                         item_names, name_stride, names, pointer_name_dispatch, pointer_texts,
                         record_layout, sign_extension, sprite_layout, text)
from .table_readers import (Readers, _bounded_analysis, _copy_tables, _items, _keyboard,
                            _move_lists, _patch_tables, scan_bounds)


def _extra_names(rom, result, config, index, bindings):
    for name, declaration in config.get("extra_names", {}).items():
        identifier = f"table[name={name}]"
        candidates = find_names(rom, name, declaration)
        if len(candidates) != 1:
            _issue(result, identifier, "Independent name anchors do not identify one complete table and stride.",
                   "global-name-anchors", candidates=len(candidates))
            continue
        binding = candidates[0]
        readers = index.readers(binding.base, binding.stride, offsets=range(binding.stride))
        if not readers:
            reason = declaration.get("candidate_reason")
            _issue(result, identifier, reason or "The anchored name table lacks a linked indexed reader.",
                   "accessor", binding.anchors, status="candidate" if reason else "unresolved",
                   base=binding.base, stride=binding.stride, rows=binding.count, readers=0)
            continue
        value = {"name": name, "base": binding.base, "stride": binding.stride, "count": binding.count,
                 "field_offset": 0, "field_length": binding.length}
        dispatch = pointer_name_dispatch(rom, readers) if declaration.get("pointers") else []
        if dispatch:
            value["long_name_pointers"] = True
        proof = evidence("Independent names and an exact bounded row layout agree with linked native indexing.",
                         "global-name-anchors+stride+accessor", binding.anchors,
                         [reader["site"] for reader in readers], stride=binding.stride,
                         rows=binding.count, readers=len(readers))
        result.data.setdefault("table", []).append(value)
        _record(result, identifier, "table", value, proof)
        if name == "trainer_classes" and dispatch:
            alias = {key: value[key] for key in ("base", "stride", "count")}
            alias["pointer"] = 0
            alias_proof = evidence("The class-name alias reuses the anchored array and native ROM-bank pointer dispatch.",
                "shared-binding+pointer-dispatch", binding.anchors, dispatch, stride=binding.stride, rows=binding.count)
            _set(result, "pokedex.trainers.class_names", alias, alias_proof)
        bindings[name] = binding
        result.metadata["methods"]["anchor-stride"].append({"name": name, "base": binding.base,
            "stride": binding.stride, "count": binding.count, "anchors": list(binding.anchors)})
        result.metadata["methods"]["accessor"].append({"name": name, "readers": readers})


def _records(rom, result, config, words, index, bindings):
    for name, anchors in config.get("records", {}).items():
        row = words.get(anchors["header"], {})
        base, names_binding = row.get("pointer"), bindings.get(anchors["names"])
        layout = record_layout(rom, base, anchors) if base is not None else None
        identifier = f"pokedex.{name}"
        if layout is None or names_binding is None:
            _issue(result, identifier, "The header and independent field vectors do not yield a unique bounded layout.",
                   "anchor-field-layout")
            continue
        stride, fields = layout
        count = names_binding.count
        readers = index.readers(base, stride, row["site"], range(stride))
        if not readers or base + stride * count > len(rom):
            _issue(result, identifier, "The anchored layout lacks a linked indexed reader or complete row bounds.",
                   "accessor", [row["site"], base], stride=stride)
            continue
        sites = [base + number * stride for number in anchors["indices"]]
        proof = evidence("Independent field vectors select one layout; header and linked indexed readers agree.",
                         "header+anchor-field-layout+accessor", [row["site"], *sites],
                         [reader["site"] for reader in readers], stride=stride, rows=count,
                         field_vectors=len(fields), readers=len(readers))
        value = {"base": base, "stride": stride, "count": count}
        if name == "species":
            value["first"] = next(number for number in range(count) if any(
                rom.u8(base + number * stride + fields[field])
                for field in ("hp", "attack", "defense", "speed", "sp_attack", "sp_defense")))
        _set(result, identifier, value, proof)
        for field, offset in fields.items():
            if field == "priority":
                signed = [(reader, sign_extension(rom, reader["site"], reader["entry"] + 1024))
                          for reader in readers if reader["field"] == offset and reader["width"] == 1]
                signed = [(reader, sites) for reader, sites in signed if sites]
                if not signed:
                    _issue(result, identifier + ".fields.priority", "No signed byte accessor confirms the priority field.",
                           "accessor", sites)
                    continue
                signed_proof = evidence("The anchored byte reaches an explicit native signed-byte extension.",
                    "anchor-field-layout+signed-extension", sites,
                    [site for _, path in signed for site in path], readers=len(signed))
                for key, value in {"offset": offset, "width": signed[0][0]["width"], "signed": True}.items():
                    _set(result, identifier + ".fields.priority." + key, value, signed_proof)
            else:
                _set(result, identifier + ".fields." + field, offset, proof)
        result.metadata["methods"]["anchor-stride"].append({"name": name + " records", "base": base,
            "stride": stride, "count": count, "fields": fields, "anchors": sites})
        result.metadata["methods"]["accessor"].append({"name": name + " records", "readers": readers})


def _header_bindings(rom, result, words, index, bindings):
    row = words.get("ability_descriptions", {})
    base = row.get("pointer")
    if base is not None:
        count = pointer_texts(rom, base)
        readers = index.readers(base, 4, row["site"])
        if count > 3 and readers:
            proof = evidence("The header pointer has bounded decoded strings and linked pointer-array indexing.",
                "header+pointer-text-boundary+accessor", [row["site"], base],
                [reader["site"] for reader in readers], rows=count, stride=4, readers=len(readers))
            _set(result, "pokedex.abilities.descriptions", {"base": base, "stride": 4, "count": count}, proof)
        else:
            _issue(result, "pokedex.abilities.descriptions", "Header text pointers lack a complete boundary or indexed reader.",
                   "header+pointer-text-boundary+accessor", [row["site"], base])
    elif "ability_descriptions" in words:
        _issue(result, "pokedex.abilities.descriptions", "The declared description header field is not a ROM pointer.",
               "header-block", [row["site"]])
    species = bindings.get("species")
    if species is None:
        return
    for name in ("front", "back", "palettes", "shiny_palettes"):
        if name not in words:
            continue
        row = words.get(name, {})
        base = row.get("pointer")
        value = sprite_layout(rom, base, species.count, "palette" in name) if base is not None else None
        readers = index.readers(base, value["stride"], row["site"]) if value is not None else []
        identifier = "pokedex.sprites." + name
        if value is None or not readers:
            _issue(result, identifier, "Header sprite records lack a unique tag layout or linked indexed reader.",
                   "header+tag-layout+accessor")
            continue
        proof = evidence("The named header field, bounded slot tags, complete pointer rows and indexed readers agree.",
            "header+tag-layout+accessor", [row["site"], base],
            [reader["site"] for reader in readers], rows=species.count, stride=value["stride"], readers=len(readers))
        _set(result, identifier, value, proof)
        result.metadata["methods"]["accessor"].append({"name": name, "readers": readers})


def run(rom: Rom, family, shared_readers=None) -> Result:
    result = Result()
    config = family.anchors.get("tables", {})
    header = family.anchors["identity"]["header_offset"]
    words = {}
    for name, offset in config.get("header", {}).items():
        site = header + offset
        pointer = rom.ptr_at(site) if site + 4 <= len(rom) else None
        words[name] = {"site": site, "pointer": pointer}
    result.metadata["header"] = words
    index = Index(rom)
    result.metadata["methods"]["header-block"] = [
        {"name": name, "site": row["site"], "value": row["pointer"]} for name, row in words.items()]
    anchor_proofs, accessor_proofs = [], []
    result.metadata["methods"]["anchor-stride"] = anchor_proofs
    result.metadata["methods"]["accessor"] = accessor_proofs
    bindings = {}
    for name, anchors in config.get("names", {}).items():
        row = words.get(name, {})
        base = row.get("pointer")
        if base is None:
            _issue(result, f"table[name={name}]", "The declared header field is missing or not a ROM pointer.", "header-block")
            continue
        binding = item_names(rom, base, anchors) if name == "items" else names(rom, name, base, anchors)
        if binding is None:
            _issue(result, f"table[name={name}]", "No unique anchored stride and complete bounded name array were found.",
                   "anchor-stride", [row["site"], base])
            continue
        readers = index.readers(base, binding.stride, row["site"])
        anchor_proofs.append({"name": name, "base": base, "stride": binding.stride,
                              "count": binding.count, "anchors": list(binding.anchors)})
        accessor_proofs.append({"name": name, "readers": readers})
        if not readers:
            _issue(result, f"table[name={name}]", "Anchored rows lack a confirmed linked indexed reader.",
                   "accessor", binding.anchors, stride=binding.stride, rows=binding.count)
            continue
        value = {"name": name, "base": base, "stride": binding.stride, "count": binding.count,
                 "field_offset": 0, "field_length": binding.length}
        proof = evidence("The header pointer, independent name anchors, bounded rows and linked indexing agree.",
                         "header+anchor-stride+accessor", [row["site"], *binding.anchors],
                         [reader["site"] for reader in readers], stride=binding.stride, rows=binding.count,
                         matched_anchors=len(binding.anchors), readers=len(readers))
        result.data.setdefault("table", []).append(value)
        _record(result, f"table[name={name}]", "table", value, proof)
        bindings[name] = binding
        if name == "abilities":
            alias = {key: value[key] for key in ("base", "stride", "count")}
            result.data.setdefault("pokedex", {})["abilities"] = alias
            _record(result, "pokedex.abilities", "table", alias, proof)
        result.metadata["bindings"].append({"name": name, "base": base, "stride": binding.stride,
                                            "count": binding.count, "reader_count": len(readers)})
    _records(rom, result, config, words, index, bindings)
    _extra_names(rom, result, config, index, bindings)
    _header_bindings(rom, result, words, index, bindings)
    knowledge = getattr(family, "readers", {})
    if knowledge:
        readers = shared_readers or Readers(rom, knowledge)
        _keyboard(rom, result, knowledge, readers)
        _items(rom, result, knowledge, readers, bindings)
        _patch_tables(rom, result, knowledge, readers)
        _copy_tables(rom, result, knowledge, readers)
        _move_lists(rom, result, config, knowledge, readers, bindings)
        table_dex.run(rom, result, config, knowledge, readers, bindings, index)
        table_encounters.run(rom, result, knowledge, readers, bindings)
        table_learnsets.run(rom, result, knowledge, readers, bindings)
        table_strings.run(rom, result, config, index, knowledge, readers)
        table_evolution.run(rom, result, knowledge, readers, bindings)
        table_graphics.run(rom, result, knowledge, readers, bindings)
        table_trainers.run(rom, result, knowledge, readers, index, bindings)
        table_mining.run(rom, result, knowledge, readers, bindings)
        table_sources.run(rom, result, knowledge, readers, bindings)
        table_maps.run(rom, result, knowledge, readers)
    return result
