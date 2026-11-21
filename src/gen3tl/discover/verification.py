"""Independent byte, row-domain and serialization checks for discovered facts."""

from __future__ import annotations

from dataclasses import replace

from ..core.charmap import Charmap


def decode_back(rom, start, length=None, *, terminated=True, mode="field"):
    """Require the codec to preserve every consumed byte."""
    if type(start) is not int or start < 0 or start >= len(rom):
        raise ValueError("string verification: invalid start")
    if length is not None and (length <= 0 or start + length > len(rom)):
        raise ValueError("string verification: invalid extent")
    cm = Charmap()
    data = rom.data if terminated else rom.read(start, length) + b"\xff"
    value, end = cm.decode(data, start if terminated else 0, mode=mode,
                           limit=length if terminated else None)
    raw = cm.encode(value, terminate=terminated)
    consumed = end - start if terminated else length
    if len(raw) != consumed or raw != rom.read(start, consumed):
        raise ValueError(f"string verification: decode-back differs at {start:#x}")
    return value, consumed


def references(rom, target, positions, index):
    """Check positions and count against the complete pointer index."""
    expected = index.get(target, ())
    if (len(positions) != len(set(positions)) or sorted(positions) != sorted(expected)
            or any(rom.ptr_at(at) != target for at in positions)):
        raise ValueError(f"reference verification: pointer census differs at {target:#x}")
    return len(positions)


def bijection(ids, expected):
    """Require one row for every identifier in the index domain."""
    if len(ids) != len(expected) or len(set(ids)) != len(ids) or set(ids) != set(expected):
        raise ValueError("table verification: row identifiers are not a bijection")
    return len(ids)


def name_rows(rom, table, expected_count):
    """Decode each indexed field, including blank and indirect names."""
    base, stride, count = (table[key] for key in ("base", "stride", "count"))
    if (stride <= 0 or count <= 0 or base < 0 or base + stride * count > len(rom)
            or count != expected_count):
        raise ValueError("table verification: row count or extent differs")
    offset, size = table["field_offset"], table["field_length"]
    if offset < 0 or size <= 0 or offset + size > stride:
        raise ValueError("table verification: field leaves its row")
    ids = []
    for number in range(count):
        at = base + number * stride + offset
        target = rom.ptr_at(at) if table.get("long_name_pointers") else None
        decode_back(rom, target if target is not None else at,
                    min(4096, len(rom) - target) if target is not None else size,
                    terminated=True if target is not None else table.get("terminated", True))
        ids.append((at - base - offset) // stride)
    return bijection(ids, range(expected_count))


def save_round_trip(data):
    """Exercise both flash slots and every rotated RAM mapping."""
    from ..verify.savefile import Layout, SaveFile, blank_save

    layout = Layout.from_dict(data)
    if (layout.sector_size != 4096 or not 0 < layout.data_size <= 0xFF4
            or len(layout.section_sizes) != layout.slot_sectors
            or any(not 0 < size <= layout.data_size for size in layout.section_sizes)):
        raise ValueError("save verification: invalid sector shape")
    count = 0
    for slot in (0, 1):
        for rotation in range(layout.slot_sectors):
            original = blank_save(layout, slot=slot, rotation=rotation)
            save = SaveFile(original, layout)
            active = save.slots[0]
            regions = save.regions(active)
            for index, (ram, off, size) in enumerate(regions):
                if size <= 0 or off < 0 or off + size > layout.size:
                    raise ValueError("save verification: invalid region")
                for other, position, length in regions[index + 1:]:
                    if (ram < other + length and other < ram + size
                            or off < position + length and position < off + size):
                        raise ValueError("save verification: overlapping regions")
                sample = bytes((index + at) % 251 for at in range(size))
                save.write(active, ram, sample)
                if save.read(active, ram, size) != sample:
                    raise ValueError("save verification: RAM mapping differs")
            save.fix_checksums()
            restored = SaveFile(save.to_bytes(), layout)
            for ram, off, size in regions:
                if restored.read(restored.slots[0], ram, size) != save.read(active, ram, size):
                    raise ValueError("save verification: serialization differs")
            for ram, off, size in regions:
                restored.write(restored.slots[0], ram, original[off:off + size])
            restored.fix_checksums()
            if restored.to_bytes() != original:
                raise ValueError("save verification: round trip differs")
            count += 1
    return count


def annotate(proof, method, sentence, **counts):
    details = proof.evidence
    return replace(proof, evidence=replace(details, method=details.method + "+" + method,
        sentence=details.sentence + " " + sentence,
        counts=tuple(sorted({**dict(details.counts), **counts}.items()))))


def verify(rom, result):
    """Keep stage proofs and add checks before any file is emitted."""
    from .scan import pointer_index

    index = pointer_index(rom)
    saves = result.data.get("save", {})
    complete = {"size", "sector_size", "data_size", "signature", "slot_sectors", "section_sizes",
                "blocks", "parasite", "extra", "party", "daycare", "boxes", "box_record"} <= saves.keys()
    rounds = save_round_trip(saves) if complete else 0
    checked = []
    for proof in result.facts:
        if proof.id.startswith("table[name="):
            rows = name_rows(rom, proof.value, dict(proof.evidence.counts).get("rows", proof.value["count"]))
            proof = annotate(proof, "decode-back+row-index-bijection",
                "Every positional row identifier occurs once and every name field round-trips; stored metadata IDs are not row identities.",
                verified_rows=rows)
        elif proof.id.startswith("strings.pointer_table["):
            value = proof.value
            total = 0
            for row in range(value["count"]):
                for field in value["fields"]:
                    at = value["base"] + row * value["stride"] + field
                    target = rom.ptr_at(at)
                    if target is None:
                        raise ValueError(f"table verification: invalid text pointer at {at:#x}")
                    decode_back(rom, target)
                    total += 1
            proof = annotate(proof, "decode-back", "Every indexed text pointer decodes and round-trips.", verified_strings=total)
        elif proof.id.startswith("strings.classified["):
            target = proof.value["offset"]
            total = references(rom, target, index.get(target, ()), index)
            if proof.value["action"] == "include":
                decode_back(rom, target)
            proof = annotate(proof, "pointer-census+decode-back" if proof.value["action"] == "include" else "pointer-census",
                "The pointer census agrees; classifier shape alone does not prove live reference semantics.", verified_pointers=total)
        elif proof.kind == "save":
            proof = annotate(proof, "synthetic-save-round-trip",
                "Both flash slots round-trip every rotation; no emulator fixture was observed." if rounds else
                "The incomplete layout cannot round-trip; only its structural witnesses are verified.", round_trips=rounds)
            if not rounds or "family" in proof.evidence.method or proof.id.endswith(".sections"):
                proof = replace(proof, tier="shape")
        checked.append(proof)
    result.facts[:] = checked
