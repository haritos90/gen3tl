"""Trainer records, party layouts and portrait geometry from native consumers."""
from __future__ import annotations

from ..core.thumb import ThumbError, decode
from .foundations import evidence
from .table_core import GraphicsError, _issue, _record, _set, lz77_decompress, sprite_layout


def _picture_rows(rom, base, palette):
    count = 0
    while count < 256 and base + (count + 1) * 8 <= len(rom.data):
        at = base + count * 8
        tag = 4 if palette else 6
        if rom.ptr_at(at) is None or rom.u16(at + tag) != count:
            break
        if not palette and rom.u16(at + 4) == 0:
            break
        count += 1
    return count


def _pictures(rom, result, readers, descriptor):
    identifiers = ("pokedex.trainers.pictures.front", "pokedex.trainers.pictures.palettes")
    front = readers.load({"reader": descriptor["reader"], "offset": descriptor["front_offset"]})
    palettes = readers.load({"reader": descriptor["reader"], "offset": descriptor["palette_offset"]})
    if front is None or palettes is None:
        for identifier in identifiers:
            _issue(result, identifier, "The native portrait loader lacks its front or palette literal.",
                   "semantic-reader+literal")
        return None
    rows = {}
    for name, load, palette in (("front", front, False), ("palettes", palettes, True)):
        count = _picture_rows(rom, load["base"], palette)
        layout = sprite_layout(rom, load["base"], count, palette)
        if not 0 < count < 256 or layout is None:
            _issue(result, "pokedex.trainers.pictures." + name,
                   "Native portrait indexing and complete tagged rows do not select one array.",
                   "semantic-reader+tagged-row-domain")
            return None
        layout = {key: layout[key] for key in ("base", "stride", "count")}
        rows[name] = (load["base"], count, load, layout)
    if len({count for _, count, _, _ in rows.values()}) != 1:
        _issue(result, "pokedex.trainers.pictures", "Picture and palette domains differ.", "tagged-row-domain")
        return None
    count = rows["front"][1]
    try:
        sizes = []
        for name, (base, _, _, _) in rows.items():
            for number in range(count):
                pointer = rom.ptr_at(base + number * 8)
                sizes.append((name, len(lz77_decompress(rom.data, pointer)[0])))
    except (GraphicsError, TypeError):
        _issue(result, "pokedex.trainers.pictures", "A portrait or palette does not decode completely.",
               "complete-graphics-domain")
        return None
    if any(name == "palettes" and size != 32 for name, size in sizes) or any(
            name == "front" and size < 2048 for name, size in sizes):
        _issue(result, "pokedex.trainers.pictures",
               "The graphics domain does not contain 64-pixel 4bpp portraits and 32-byte palettes.",
               "complete-graphics-domain")
        return None
    sites = [row["site"] for name, (_, _, row, _) in rows.items()]
    values = {}
    for name, (base, count, load, layout) in rows.items():
        proof = evidence("Native portrait indexing, sequential tags and every decoded graphic agree on the complete array.",
            "semantic-reader+tagged-row-domain+graphics-domain", [base], sites,
            stride=8, rows=count, decoded_rows=count)
        identifier = "pokedex.trainers.pictures." + name
        _set(result, identifier, layout, proof)
        values[name] = layout
    side_proof = evidence("Every front portrait supplies at least a 64 by 64 4bpp canvas and every palette supplies 16 colors.",
        "shared-graphics-domain+native-portrait-loader", [rows[name][0] for name in rows], sites,
        rows=count, canvas_bytes=2048, bits_per_pixel=4)
    _set(result, "pokedex.trainers.pictures", {"side": 64}, side_proof)
    return values, sites


def _observed_fields(index, base, stride):
    readers = index.readers(base, stride, offsets=range(stride))
    return {(row["field"], row["width"]) for row in readers}, readers


def _name_layout(observed):
    name_offset = 4
    if (name_offset, 0) not in observed and (name_offset, 4) not in observed:
        return None
    following = sorted({field for field, _ in observed if field > name_offset})
    if not following:
        return None
    return name_offset, following[0] - name_offset


def _party_members(rom, base, stride, count):
    values = []
    for number in range(count):
        at = base + number * stride
        flags, size, pointer = rom.u8(at), rom.u8(at + 32), rom.ptr_at(at + 36)
        if size == 0:
            if pointer is not None:
                return None
            continue
        if pointer is None or not 1 <= size <= 6:
            return None
        step = 16 if flags & 1 else 8
        for member in range(size):
            at = pointer + member * step
            if any(rom.data[at:at + step]):
                values.append((flags, at))
    return values


def _unique(values, candidates, predicate):
    found = [offset for offset in candidates if all(predicate(offset, *row) for row in values)]
    return found[0] if len(found) == 1 else None


def _party_layout(rom, base, stride, count, domains):
    values = list(_party_members(rom, base, stride, count))
    if values is None:
        return None
    level = _unique(values, range(0, stride - 1, 2), lambda off, flags, at: 1 <= rom.u16(at + off) <= 100)
    if level is None:
        return None
    species_candidates = [offset for offset in range(0, stride - 1, 2) if offset != level and all(
        0 < rom.u16(at + offset) < domains["species"] for flags, at in values)]
    species = species_candidates[0] if len(species_candidates) == 1 else None
    if species is None:
        return None
    iv_candidates = [offset for offset in range(0, stride - 1, 2)
                     if offset not in (level, species) and all(rom.u16(at + offset) <= 255 for flags, at in values)]
    iv = iv_candidates[0] if len(iv_candidates) == 1 else None
    if iv is None:
        return None
    held_candidates = [offset for offset in range(0, stride - 1, 2)
                       if offset not in (iv, level, species) and all(
                           not flags & 2 or rom.u16(at + offset) < domains["items"] for flags, at in values)]
    held_item = held_candidates[0] if len(held_candidates) == 1 else None
    if held_item is None:
        return None
    moves_with_item = _unique(values, range(held_item + 2, stride - 7, 2),
                              lambda off, flags, at: not (flags & 1 and flags & 2)
                              or all(rom.u16(at + off + move * 2) < domains["moves"] for move in range(4)))
    if moves_with_item is None:
        return None
    moves = _unique(values, range(species + 2, moves_with_item, 2),
                    lambda off, flags, at: not (flags & 1 and not flags & 2)
                    or all(rom.u16(at + off + move * 2) < domains["moves"] for move in range(4)))
    if moves is None:
        return None
    return {"iv": iv, "level": level, "species": species, "held_item": held_item,
            "moves": moves, "moves_with_item": moves_with_item, "move_count": 4,
            "custom_moves_flag": 1, "held_item_flag": 2, "default_stride": 8, "custom_stride": 16}


def _valid_party(rom, pointer, size, flags, fields, domains):
    if size == 0:
        return pointer is None
    if pointer is None or not 1 <= size <= 6:
        return False
    stride = 16 if flags & 1 else 8
    if pointer + size * stride > len(rom):
        return False
    for number in range(size):
        at = pointer + number * stride
        raw = rom.data[at:at + stride]
        if not any(raw):
            continue
        level, species = (rom.u16(at + fields[name]) for name in ("level", "species"))
        if not 1 <= level <= 100 or not 0 < species < domains["species"]:
            return False
        if flags & 2 and rom.u16(at + fields["held_item"]) >= domains["items"]:
            return False
        if flags & 1:
            base = fields["moves_with_item"] if flags & 2 else fields["moves"]
            if any(rom.u16(at + base + move * 2) >= domains["moves"] for move in range(4)):
                return False
    return True


def _records(rom, result, readers, index, bindings, pictures, picture_sites, descriptor):
    identifiers = ("table[name=trainer_names]", "pokedex.trainers", "pokedex.trainers.fields", "pokedex.trainers.party")
    picture_matches = set(readers.matches.get(descriptor["pictures"]["reader"], ()))
    candidates = []
    for start in readers.matches.get(descriptor["records"], ()):
        try:
            instruction = decode(rom.data, start + descriptor["record_load"])
            call = decode(rom.data, start + descriptor["portrait_call"])
        except ThumbError:
            continue
        if instruction.literal is not None and call.mnemonic == "bl" and call.target in picture_matches:
            base = rom.ptr_at(instruction.literal)
            if base is not None:
                candidates.append((base, instruction))
    if len({base for base, _ in candidates}) != 1:
        for identifier in identifiers:
            _issue(result, identifier, "The trainer-record reader and portrait call do not select one table.",
                   "semantic-reader+call-constraint", candidates=len(candidates))
        return
    base, instruction = candidates[0]
    observed, native = _observed_fields(index, base, 40)
    fields = {field for field, width in observed if width in (1, 4)}
    if not {0, 1, 3, 32} <= fields:
        for identifier in identifiers:
            _issue(result, identifier, "Native trainer readers do not confirm flags, class, picture and party size.",
                   "accessor", [base, instruction.literal])
        return
    name = _name_layout(observed)
    if name is None:
        _issue(result, "table[name=trainer_names]", "Native trainer readers do not bound the padded name field.",
               "accessor-field-boundary", [base])
        return
    domains = {name: bindings[name].count for name in ("species", "moves", "items")}
    provisional = 0
    while provisional < 4096 and base + (provisional + 1) * 40 <= len(rom):
        at = base + provisional * 40
        if rom.u8(at) > 3 or rom.u8(at + 3) >= pictures["front"]["count"]:
            break
        provisional += 1
    party_fields = _party_layout(rom, base, 40, provisional, domains)
    if party_fields is None or party_fields["moves_with_item"] != 8:
        _issue(result, "pokedex.trainers.party", "Complete party domains do not select one flag-dependent field layout.",
               "complete-party-domain")
        return
    count = 0
    while count < 4096 and base + (count + 1) * 40 <= len(rom):
        at = base + count * 40
        flags, size, pointer = rom.u8(at), rom.u8(at + 32), rom.ptr_at(at + 36)
        if flags > 3 or rom.u8(at + 3) >= pictures["front"]["count"] or not _valid_party(
                rom, pointer, size, flags, party_fields, domains):
            break
        count += 1
    if count <= 1:
        for identifier in identifiers:
            _issue(result, identifier, "The complete trainer/party domain does not have a valid boundary.",
                   "complete-record-domain", [base], rows=count)
        return
    sites = [instruction.address, instruction.literal, *picture_sites, *(row["site"] for row in native)]
    proof = evidence("Native record reads and the portrait call agree with complete trainer and party domains.",
        "semantic-reader+call-constraint+accessor+complete-record-domain", [base, instruction.literal], sites,
        stride=40, rows=count, native_fields=len(fields))
    value = {"name": "trainer_names", "base": base, "stride": 40, "count": count,
             "field_offset": name[0], "field_length": name[1], "gender_offset": 2, "class_offset": 1}
    result.data.setdefault("table", []).append(value)
    _record(result, "table[name=trainer_names]", "table", value, proof)
    alias = {key: value[key] for key in ("base", "stride", "count")}
    _set(result, "pokedex.trainers", alias, evidence("The trainer alias reuses the complete native record proof.",
        "shared-binding+complete-record-domain", [base], sites, stride=40, rows=count))
    for name, offset in (("party_flags", 0), ("class", 1), ("picture", 3), ("party_size", 32), ("party", 36)):
        _set(result, "pokedex.trainers.fields." + name, offset, proof)
    party_proof = evidence("Flag-dependent party strides and every complete party validate against species, item and move domains.",
        "complete-party-domain", [base], sites, rows=count,
        default_stride=8, custom_stride=16, move_count=4)
    for name, value in party_fields.items():
        _set(result, "pokedex.trainers.party." + name, value, party_proof)


def run(rom, result, knowledge, readers, index, bindings):
    descriptor = knowledge.get("trainers")
    if not descriptor or not all(name in bindings for name in ("species", "moves", "items")):
        return
    pictures = _pictures(rom, result, readers, descriptor["pictures"])
    if pictures is not None:
        _records(rom, result, readers, index, bindings, *pictures, descriptor)
