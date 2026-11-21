"""Referenced graphics decoded and rendered as indexed PNG candidates.

Palette choices prefer consumer-sequence, descriptor-triple,
descriptor-triple-candidate, adjacent-after-block+palette-copy-candidate,
then code-window-candidate evidence. The next keys are the fewest alternative
specs at the same evidence site, the lowest site, and finally spec and bank.
A colour depth without a variant uses a grey ramp. Choices never affect
the trusted-palette requirement for materializing a build record.
"""

from __future__ import annotations

import json
import re
import struct
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from pathlib import Path

from ..check.render import write_indexed_png
from ..core import thumb
from ..core.graphics import GREY, GraphicsError, _PALETTE, lz77_decompress, palette_colours, tiles_to_pixels
from ..core.rom import Rom
from .consumerproof import (COPIES, LZ77, consumer_paths, consumer_start, dma_transfers, event_length,
                            graphic_proofs, palette_proofs)
from .graphicevidence import (adjacent_palette_records, descriptor_triples, literal_loads,
                              palette_evidence, palette_loads, raw_records, verified_descriptor_triples)
from .scan import pointer_index, thumb_pool_loads

SCHEMA = 2
GENERATOR = "graphics-candidates/2.0.0"
DEFAULT_WIDTH_TILES = 16
BYTE_MAP_WIDTH = 16
CODE_WINDOW = 192
MODULE_WINDOW = 0x800
VALID_CATEGORIES = ("translate", "optional", "keep")
ROM_EVIDENCE_TIERS = ("consumer", "descriptor")
BGCNT = (0x04000008, 0x04000010)
PALETTE_METHOD_ORDER = (
    "consumer-sequence",
    "descriptor-triple",
    "descriptor-triple-candidate",
    "adjacent-after-block+palette-copy-candidate",
    "code-window-candidate",
)


def _palette_method(record: dict) -> str:
    return record.get("evidence", {}).get("method", "")


def _palette_code_site(record: dict) -> int | None:
    evidence = record.get("evidence", {})
    for site in (evidence.get("graphic_load"), evidence.get("at")):
        if isinstance(site, int):
            return site
    palette = evidence.get("palette", {})
    for site in (palette.get("load"), palette.get("at")):
        if isinstance(site, int):
            return site
    return None


def _palette_variant(record: dict) -> dict:
    return {"spec": record["spec"], "bpp": record["bpp"], "bank": record["bank"],
            "tier": record.get("tier", "candidate"), "method": _palette_method(record),
            "code_site": _palette_code_site(record)}


def _choose_palette(variants: list[dict], bpp: int) -> dict | None:
    matches = [p for p in variants if p["bpp"] == bpp]
    if not matches:
        return None

    def key(record: dict) -> tuple:
        method = _palette_method(record)
        site = _palette_code_site(record)
        alternatives = len({p["spec"] for p in matches
                            if _palette_method(p) == method and _palette_code_site(p) == site}) - 1
        bank = record["bank"]
        return (PALETTE_METHOD_ORDER.index(method) if method in PALETTE_METHOD_ORDER
                else len(PALETTE_METHOD_ORDER), alternatives, site is None,
                site if site is not None else 0, record["spec"],
                bank if bank is not None else -1, method)

    return min(matches, key=key)


@dataclass
class Ref:
    at: int
    kind: str

    def to_dict(self) -> dict:
        return {"at": self.at, "kind": self.kind, "aligned": self.at % 4 == 0}


@dataclass
class Candidate:
    offset: int
    block_length: int
    decoded_size: int
    refs: list[Ref] = field(default_factory=list)
    compression: str = "lz77"
    size_evidence: dict = field(default_factory=dict)
    palettes: list[dict] = field(default_factory=list)
    tilemaps: list[dict] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"g{self.offset:07X}"

    def data(self, rom: Rom) -> bytes:
        if self.compression == "none":
            return bytes(rom.read(self.offset, self.decoded_size))
        return lz77_decompress(rom.data, self.offset)[0]

    def shape(self) -> dict:
        size = self.decoded_size
        whole4 = size // 32 if self.compression == "none" and size >= 32 else (size // 32 if size % 32 == 0 else None)
        return {"bpp4_tiles": whole4,
                "bpp8_tiles": size // 64 if self.compression == "lz77" and size % 64 == 0 else None,
                "tilemap_cells": size // 2 if size % 2 == 0 else None,
                "trailing_bytes": size % 32 if self.compression == "none" else 0}

    def render_modes(self) -> list[dict]:
        """The evidenced map or possible sheet interpretations of a candidate."""
        if self.tilemaps:
            return [{"mode": "tilemap", "association": i, "bpp": m["bpp"],
                     "tiles": m["width"] * m["height"], "width": m["width"], "height": m["height"],
                     "tier": m.get("tier", "consumer")} for i, m in enumerate(self.tilemaps)]
        size, modes = self.decoded_size, []
        if self.compression == "none" and size >= 32:
            modes.append({"mode": "tile-sheet-4bpp", "bpp": 4, "tiles": size // 32})
        else:
            if size % 32 == 0:
                modes.append({"mode": "tile-sheet-4bpp", "bpp": 4, "tiles": size // 32})
            if size % 64 == 0:
                modes.append({"mode": "tile-sheet-8bpp", "bpp": 8, "tiles": size // 64})
        if not modes:
            modes.append({"mode": "bytes", "bpp": 8, "tiles": 0})
        return modes

    def palette_for(self, bpp: int) -> dict | None:
        """The unique trusted (consumer/descriptor) palette for a bpp, if any."""
        variants = [p for p in self.palettes if p["bpp"] == bpp and p.get("tier") in ROM_EVIDENCE_TIERS]
        specs = {p["spec"] for p in variants}
        return variants[0] if len(specs) == 1 and variants else None

    def palette_choice(self, bpp: int) -> dict | None:
        """The best-evidenced candidate palette for one colour depth."""
        return _choose_palette(self.palettes, bpp)

    def reference_kinds(self) -> list[str]:
        return sorted({r.kind for r in self.refs})

    def relocation_ambiguous(self) -> bool:
        """Unaligned or non-word references would not all be rewritten by relocation."""
        return any(r.at % 4 != 0 or r.kind == "unaligned" for r in self.refs)

    def to_dict(self) -> dict:
        refs = [r.to_dict() for r in sorted(self.refs, key=lambda r: r.at)]
        return {"id": self.id, "offset": self.offset, "compression": self.compression,
                "block_length": self.block_length, "decoded_size": self.decoded_size,
                "size_evidence": self.size_evidence, "shape": self.shape(), "render_modes": self.render_modes(),
                "palette_variants": self.palettes, "tilemap_associations": self.tilemaps,
                "reference_kinds": self.reference_kinds(), "refs": refs,
                "relocation_ambiguous": self.relocation_ambiguous()}


def _classify_ref(rom: Rom, at: int) -> str:
    """The reference kind of a pointer word at `at`: code pool, data table or bare word."""
    if at % 4 != 0:
        return "unaligned"
    if thumb_pool_loads(rom, at):
        return "pool"
    prev = rom.ptr_at(at - 4) if at >= 4 else None
    nxt = rom.ptr_at(at + 4) if at + 8 <= len(rom) else None
    if prev is not None or nxt is not None:
        return "table"
    return "aligned"


def _palette_site(record: dict) -> int | None:
    evidence = record.get("evidence", {})
    palette = evidence.get("palette", {})
    return palette.get("load", evidence.get("at", palette.get("at")))


def _palette_key(record: dict) -> tuple:
    return record["spec"], record["bpp"], record["bank"]


_TIER_RANK = {"descriptor": 3, "consumer": 3, "candidate": 1, "module": 1}


def _add_palette(candidate: Candidate, record: dict) -> None:
    key = _palette_key(record)
    for index, existing in enumerate(candidate.palettes):
        if _palette_key(existing) == key:
            if _TIER_RANK.get(record.get("tier"), 0) > _TIER_RANK.get(existing.get("tier"), 0):
                candidate.palettes[index] = record
            return
    candidate.palettes.append(record)


def _map_geometries(size: int) -> list[tuple[tuple[int, int], bool]]:
    """GBA background geometries consistent with a decoded byte size."""
    if size == 1280:
        return [((32, 20), True)]
    if size == 2048:
        return [((32, 32), True)]
    if size == 4096:
        return [((64, 32), False), ((32, 64), False)]
    return []


def _map_geometry(size: int) -> list[tuple[int, int]]:
    return [geometry for geometry, _verified in _map_geometries(size)]


def _consumer_sequence_associate(rom: Rom, candidates: list[Candidate], loads: dict,
                                 palettes: list[dict]) -> list[dict]:
    """Share ordered consumer events only within one executed path."""
    verified = [p for p in palettes if p.get("tier") == "consumer"
                and p["evidence"].get("method") == "palette-copy"]
    sites = sorted((load.address, candidate) for candidate in candidates
                   for load in loads.get(candidate.offset, []))
    addresses = [at for at, _candidate in sites]
    paths_cache = {}
    sequences = {}
    for palette in verified:
        evidence = palette["evidence"]
        p_load, p_call = evidence["load"], evidence["call"]
        for i in range(bisect_left(addresses, p_load - 0x300), bisect_right(addresses, p_load + 0x300)):
            g_address, candidate = sites[i]
            if candidate.offset == palette["target"]:
                continue
            start = consumer_start(rom, min(g_address, p_load))
            end = max(g_address + 0x40, p_call + 4)
            if end - start > 0x340:
                continue
            if (start, end) not in paths_cache:
                try:
                    paths_cache[start, end] = consumer_paths(rom, start, min(len(rom), end))
                except thumb.ThumbError:
                    paths_cache[start, end] = []
            for path_index, path in enumerate(paths_cache[start, end]):
                proved = [proof for proof in palette_proofs(rom, path)
                          if proof.target == evidence.get("source_target", palette["target"])
                          and proof.sub == evidence.get("source_sub", 0)
                          and proof.length == evidence["length"]
                          and proof.bank == palette["bank"]
                          and proof.destination == evidence["destination"]
                          and proof.destination_kind == evidence["destination_kind"]
                          and any(event.chain and event.root_call == p_call
                                  for event in path.events[:proof.event_index + 1])
                          and path.events[proof.event_index].site == evidence["bios_swi"][0]]
                if not proved:
                    continue
                key = start, end, path_index
                sequence = sequences.setdefault(key, {"span": [start, end], "path": path_index,
                                                      "proof_path": path, "graphics": {}, "palettes": []})
                if _palette_key(palette) not in {_palette_key(p) for p in sequence["palettes"]}:
                    sequence["palettes"].append(palette)
                for proof in graphic_proofs(rom, path):
                    if proof.target != palette["target"]:
                        sequence["graphics"][proof.target] = proof

    by_offset = {candidate.offset: candidate for candidate in candidates}
    for sequence in sequences.values():
        path = sequence["proof_path"]
        for offset, proof in sequence["graphics"].items():
            candidate = by_offset.get(offset)
            event = path.events[proof.event_index]
            if candidate is None or not event.chain:
                continue
            for palette in sequence["palettes"]:
                palette_region = "obj" if palette["evidence"]["destination"] >= 0x05000200 else "bg"
                if proof.region != palette_region:
                    continue
                record = {"spec": palette["spec"], "bpp": palette["bpp"], "bank": palette["bank"],
                          "tier": "consumer",
                          "evidence": {"method": "consumer-sequence",
                                       "palette": palette["evidence"],
                                       "graphic_load": next((load.address for load in loads.get(offset, [])), None),
                                       "graphic_consumer": {"call": event.root_call,
                                                            "bios_swi": (event.site, event.swi),
                                                            "region": proof.region,
                                                            "destination": [proof.destination_low,
                                                                            proof.destination_high]},
                                       "analysis_span": sequence["span"], "path": sequence["path"]}}
                _add_palette(candidate, record)
    return list(sequences.values())


def _associate_palettes(rom: Rom, candidates: list[Candidate], palettes: list[dict], loads: dict,
                        adjacent: list[dict], triples: list[dict],
                        verified_triples: list[dict], staged: list[dict] = ()) -> list[dict]:
    by_offset = {c.offset: c for c in candidates}
    sites = sorted((load.address, candidate) for candidate in candidates for load in loads.get(candidate.offset, []))
    addresses = [at for at, _candidate in sites]

    # Exploratory code-window variants; never trusted and never materializable.
    for palette in [*palettes, *staged]:
        site = palette["evidence"].get("load")
        if site is None:
            continue
        for i in range(bisect_left(addresses, site - CODE_WINDOW), bisect_right(addresses, site + CODE_WINDOW)):
            graphic_site, candidate = sites[i]
            if candidate.offset == palette["target"]:
                continue
            record = {"spec": palette["spec"], "bpp": palette["bpp"], "bank": palette["bank"],
                      "tier": "candidate",
                      "evidence": {"method": "code-window-candidate", "palette": palette["evidence"],
                                   "graphic_load": graphic_site}}
            _add_palette(candidate, record)

    sequences = _consumer_sequence_associate(rom, candidates, loads, palettes)

    verified_at = {triple["at"]: triple for triple in verified_triples}
    for triple in triples:
        palette = next((p for p in palettes if p["target"] == triple["third"] and p["bpp"] == 4), None)
        if palette is None:
            continue
        verified = verified_at.get(triple["at"])
        tier = "descriptor" if verified else "candidate"
        for offset in (triple["first"], triple["second"]):
            candidate = by_offset.get(offset)
            if candidate is None:
                continue
            record = {"spec": palette["spec"], "bpp": 4,
                      "bank": verified["bank"] if verified else palette["bank"], "tier": tier,
                      "evidence": {"method": "descriptor-triple" if tier == "descriptor"
                                   else "descriptor-triple-candidate",
                                   "at": triple["at"], "first": triple["first"],
                                   "second": triple["second"], "third": triple["third"],
                                   "palette": verified["evidence"] if verified else palette["evidence"]}}
            _add_palette(candidate, record)

    for record in adjacent:
        candidate = by_offset.get(record["evidence"]["block"])
        if candidate is None:
            continue
        palette = next((p for p in palettes if p["target"] == record["target"] and p["bpp"] == 4
                        and p.get("tier") == "consumer"
                        and p["evidence"].get("method") == "palette-copy"), None)
        if palette is None:
            continue
        association = {"spec": palette["spec"], "bpp": 4, "bank": palette["bank"], "tier": "candidate",
                       "evidence": {"method": "adjacent-after-block+palette-copy-candidate",
                                    "block": record["evidence"]["block"], "palette": palette["evidence"]}}
        _add_palette(candidate, association)
    return sequences


def _palette_selections(banks: set[int], variants: list[dict], bpp: int,
                        site: int | None) -> tuple[list[dict], dict[int, dict[str, int]]]:
    """Every palette-spec selection that covers each used bank, plus load distances."""
    selections: list[dict] = [{}]
    variant_distances: dict[int, dict[str, int]] = {}
    for bank in sorted(banks):
        matches = [p for p in variants if p["bpp"] == bpp and
                   (p["bank"] == bank or (p["bank"] is None and bank == 0))]
        if not matches:
            return [], {}
        distances: dict[str, int] = {}
        for record in matches:
            spec = record["spec"]
            palette_site = _palette_site(record)
            distance = abs(palette_site - site) if palette_site is not None and site is not None else 0
            distances[spec] = min(distances.get(spec, distance), distance)
        variant_distances[bank] = distances
        options = sorted(distances, key=lambda spec: (distances[spec], spec))
        selections = [{**selected, bank: spec} for selected in selections for spec in options]
    return selections, variant_distances


def _may_write(rom: Rom, event, address: int, length: int) -> bool:
    if event.swi not in (*COPIES, *LZ77):
        return True
    written = event_length(rom, event)
    base = event.destination.constant
    return written is None or base is None or base < address + length and address < base + written


def _sequence_bg_configuration(rom: Rom, sequence: dict, map_proof, sheet_proof) -> dict | None:
    if map_proof.region != "bg" or sheet_proof.region != "bg":
        return None
    path = sequence["proof_path"]
    seen = set()
    for write in reversed(path.writes):
        address = write.destination.constant
        # An unanalysed write may replace any BGxCNT value.
        if address is None or write.length is None:
            return None
        if address + write.length <= BGCNT[0] or address >= BGCNT[1]:
            continue
        if address % 2 or write.length != 2:
            return None
        if address in seen:
            continue
        seen.add(address)
        value = write.value.constant if write.value is not None else None
        later = path.events[write.event_index:]
        if value is None or any(_may_write(rom, event, address, 2) for event in later):
            return None
        char_base = 0x06000000 + ((value >> 2) & 3) * 0x4000
        screen_base = 0x06000000 + ((value >> 8) & 0x1F) * 0x800
        if sheet_proof.destination_low == char_base and map_proof.destination_low == screen_base:
            return {"address": address, "value": value, "site": write.site,
                    "bpp": 8 if value & 0x80 else 4}
    return None


def _associate_tilemaps(rom: Rom, candidates: list[Candidate], loads: dict, triples: list[dict],
                        verified_triples: list[dict], sequences: list[dict]) -> None:
    sites = sorted((load.address, candidate) for candidate in candidates for load in loads.get(candidate.offset, []))
    addresses = [at for at, _candidate in sites]
    by_offset = {c.offset: c for c in candidates}
    decoded: dict[int, bytes] = {}

    def data(candidate: Candidate) -> bytes:
        if candidate.offset not in decoded:
            decoded[candidate.offset] = candidate.data(rom)
        return decoded[candidate.offset]

    def add(candidate: Candidate, sheet: Candidate, bpp: int, geometry: tuple[int, int],
            geometry_verified: bool, selected: dict, distances: dict, method: str,
            site: int | None, sheet_site: int | None, tier: str, sequence: dict | None) -> None:
        width, height = geometry
        evidence = {"method": method, "map_load": site, "sheet_load": sheet_site,
                    "cells": len(entries), "geometry": "GBA background entries",
                    "geometry_verified": geometry_verified,
                    "tile_count": sheet.decoded_size // (8 * bpp),
                    "palette_load_distances": distances}
        if sequence is not None:
            evidence["sequence"] = {"analysis_span": sequence["span"], "path": sequence["path"]}
            if sequence.get("bg_configuration") is not None:
                evidence["bg_configuration"] = sequence["bg_configuration"]
        association = {"sheet_offset": sheet.offset, "bpp": bpp, "width": width, "height": height,
                       "palettes": selected, "tier": tier, "evidence": evidence}
        key = (sheet.offset, bpp, (width, height), tuple(sorted(selected.items())))
        for index, existing in enumerate(candidate.tilemaps):
            if (existing["sheet_offset"], existing["bpp"], (existing["width"], existing["height"]),
                    tuple(sorted(existing["palettes"].items()))) != key:
                continue
            if _TIER_RANK.get(tier, 0) > _TIER_RANK.get(existing.get("tier"), 0):
                candidate.tilemaps[index] = association
            return
        candidate.tilemaps.append(association)

    def compose(candidate: Candidate, sheet: Candidate, bpp: int, geometry: tuple[int, int],
                geometry_verified: bool, method: str, site: int | None, sheet_site: int | None,
                tier: str, trusted: bool, variants_override=None, sequence: dict | None = None) -> None:
        tile_bytes = 8 * bpp
        tiles = sheet.decoded_size // tile_bytes
        if sheet.decoded_size % tile_bytes or not tiles:
            return
        if any((entry & 0x3FF) >= tiles for entry in entries):
            return
        variants = variants_override
        if variants is None:
            variants = [p for p in candidate.palettes + sheet.palettes if p["bpp"] == bpp]
        if trusted:
            variants = [p for p in variants if p.get("tier") in ROM_EVIDENCE_TIERS]
        if not variants:
            return
        selections, distances = _palette_selections(banks, variants, bpp, site)
        for selected in selections:
            add(candidate, sheet, bpp, geometry, geometry_verified, selected, distances,
                method, site, sheet_site, tier, sequence)

    # One path must prove sheet, map and every palette used.
    for sequence in sequences:
        for map_offset, map_proof in sequence["graphics"].items():
            candidate = by_offset.get(map_offset)
            if candidate is None or map_proof.region != "bg":
                continue
            geometries = _map_geometries(candidate.decoded_size)
            if not geometries:
                continue
            raw = data(candidate)
            entries = list(struct.unpack(f"<{len(raw) // 2}H", raw))
            banks = {entry >> 12 for entry in entries}
            for sheet_offset, sheet_proof in sequence["graphics"].items():
                sheet = by_offset.get(sheet_offset)
                if sheet is None or sheet.offset == candidate.offset or sheet_proof.region != "bg":
                    continue
                configuration = _sequence_bg_configuration(rom, sequence, map_proof, sheet_proof)
                scoped = {**sequence, "bg_configuration": configuration}
                palettes = [p for p in sequence["palettes"] if p["evidence"]["destination"] < 0x05000200]
                for bpp in (4, 8):
                    for geometry, geometry_verified in geometries:
                        trusted = (configuration is not None and geometry_verified
                                   and configuration["bpp"] == bpp)
                        compose(candidate, sheet, bpp, geometry, geometry_verified,
                                "consumer-sequence", None, None,
                                "consumer" if trusted else "candidate", trusted,
                                palettes, scoped)

    # Exploratory composition from nearby loads and decoded map entries.
    for candidate in candidates:
        geometries = _map_geometries(candidate.decoded_size)
        if not geometries:
            continue
        raw = data(candidate)
        entries = list(struct.unpack(f"<{len(raw) // 2}H", raw))
        banks = {entry >> 12 for entry in entries}
        for map_load in loads.get(candidate.offset, []):
            site = map_load.address
            nearby = range(bisect_left(addresses, site - CODE_WINDOW), bisect_right(addresses, site + CODE_WINDOW))
            for i in nearby:
                sheet_site, sheet = sites[i]
                if sheet.offset == candidate.offset:
                    continue
                for bpp in (4, 8):
                    for geometry, geometry_verified in geometries:
                        compose(candidate, sheet, bpp, geometry, geometry_verified,
                                "code-window-candidate", site, sheet_site, "candidate", False)

    # Descriptor path: aligned [sheet, map, palette] pointer triples.
    verified_at = {triple["at"]: triple for triple in verified_triples}
    for triple in triples:
        candidate = by_offset.get(triple["second"])
        sheet = by_offset.get(triple["first"])
        if candidate is None or sheet is None:
            continue
        geometries = _map_geometries(candidate.decoded_size)
        if not geometries:
            continue
        raw = data(candidate)
        entries = list(struct.unpack(f"<{len(raw) // 2}H", raw))
        banks = {entry >> 12 for entry in entries}
        verified = verified_at.get(triple["at"])
        trusted = verified is not None and verified["evidence"]["region"] == "bg"
        for bpp in (4, 8):
            for geometry, geometry_verified in geometries:
                tier = "descriptor" if trusted and geometry_verified else "candidate"
                compose(candidate, sheet, bpp, geometry, geometry_verified,
                        "descriptor-triple", triple["at"] + 4, triple["at"], tier, trusted)

    # Module path: explicit fallback when the code window differs
    for candidate in candidates:
        if candidate.tilemaps:
            continue
        geometries = _map_geometries(candidate.decoded_size)
        if not geometries:
            continue
        map_loads = loads.get(candidate.offset, [])
        if not map_loads:
            continue
        raw = data(candidate)
        entries = list(struct.unpack(f"<{len(raw) // 2}H", raw))
        banks = {entry >> 12 for entry in entries}
        site = map_loads[0].address
        nearby = range(bisect_left(addresses, site - MODULE_WINDOW), bisect_right(addresses, site + MODULE_WINDOW))
        module_sheets = 0
        tried = 0
        for i in nearby:
            sheet_site, sheet = sites[i]
            if sheet.offset == candidate.offset:
                continue
            tried += 1
            before = len(candidate.tilemaps)
            for bpp in (4, 8):
                for geometry, geometry_verified in geometries:
                    compose(candidate, sheet, bpp, geometry, geometry_verified,
                            "module-candidate", site, sheet_site, "module", False)
            if len(candidate.tilemaps) > before:
                module_sheets += 1
            if module_sheets >= 4 or tried >= 64:
                break


def _descriptor_map_triples(rom: Rom, triples: list[dict], by_offset: dict[int, Candidate]) -> list[dict]:
    """Descriptor triples whose second pointer is a map and first a fitting sheet."""
    out = []
    decoded: dict[int, bytes] = {}
    for triple in triples:
        sheet = by_offset.get(triple["first"])
        mapc = by_offset.get(triple["second"])
        if sheet is None or mapc is None or not _map_geometry(mapc.decoded_size):
            continue
        if mapc.offset not in decoded:
            decoded[mapc.offset] = lz77_decompress(rom.data, mapc.offset)[0]
        raw = decoded[mapc.offset]
        entries = struct.unpack(f"<{len(raw) // 2}H", raw)
        max_tile = max((entry & 0x3FF for entry in entries), default=0)
        if any(sheet.decoded_size % (8 * bpp) == 0 and sheet.decoded_size // (8 * bpp) > max_tile
               for bpp in (4, 8)):
            out.append(triple)
    return out


def scan(rom: Rom) -> dict:
    """Find referenced LZ77 and evidenced raw graphics without catalogue inputs."""
    idx = pointer_index(rom)
    loads = literal_loads(rom)
    pool_words = {load.literal for sites in loads.values() for load in sites}

    def refs_for(offset: int) -> list[Ref]:
        refs = []
        for at in sorted(idx.get(offset, [])):
            if at % 4:
                kind = "unaligned"
            elif at in pool_words:
                kind = "pool"
            elif (at >= 4 and rom.ptr_at(at - 4) is not None) or (at + 8 <= len(rom) and rom.ptr_at(at + 4) is not None):
                kind = "table"
            else:
                kind = "aligned"
            refs.append(Ref(at, kind))
        return refs

    candidates: list[Candidate] = []
    rejected: list[dict] = []
    for offset in sorted(idx):
        if rom.data[offset] != 0x10:
            continue
        refs = refs_for(offset)
        try:
            data, used = lz77_decompress(rom.data, offset)
        except GraphicsError as exc:
            rejected.append({"offset": offset, "refs": [r.to_dict() for r in refs],
                             "reason": str(exc)})
            continue
        if not data:
            rejected.append({"offset": offset, "refs": [r.to_dict() for r in refs],
                             "reason": "the referenced LZ77 block decodes to zero bytes"})
            continue
        candidates.append(Candidate(offset, used, len(data), refs,
                                    size_evidence={"method": "lz77-header", "offset": offset}))
    lz_offsets = {c.offset for c in candidates}
    lz_blocks = [(c.offset, c.block_length) for c in candidates]
    by_offset = {c.offset: c for c in candidates}
    shape_triples = descriptor_triples(rom, idx)
    map_triples = _descriptor_map_triples(rom, shape_triples, by_offset)
    verified_triples = verified_descriptor_triples(rom, shape_triples, loads)
    adjacent = adjacent_palette_records(rom, lz_blocks)
    palettes, staged = palette_evidence(rom, loads, dma_transfers(rom))
    verified_by_at = {triple["at"]: triple for triple in verified_triples}
    for triple in map_triples:
        verified = verified_by_at.get(triple["at"])
        tier = "descriptor" if verified else "candidate"
        kind = "lz" if rom.data[triple["third"]] == 0x10 else "raw"
        record = {"target": triple["third"], "spec": f"{kind}:0x{triple['third']:07X}",
                  "bpp": 4, "bank": verified["bank"] if verified else 0, "tier": tier,
                  "evidence": {"method": "descriptor-triple", "at": triple["at"],
                               "first": triple["first"], "second": triple["second"],
                               "third": triple["third"],
                               "verified": verified["evidence"] if verified else None}}
        key = (record["spec"], record["bpp"], record["bank"])
        if not any((p["spec"], p["bpp"], p["bank"]) == key for p in palettes):
            palettes.append(record)
    for record in raw_records(rom, idx, loads, lz_offsets, {p["target"] for p in palettes}):
        if record["offset"] in lz_offsets:
            continue
        candidates.append(Candidate(record["offset"], record["size"], record["size"], refs_for(record["offset"]),
                                    "none", record["evidence"]))
    candidates.sort(key=lambda c: c.offset)
    sequences = _associate_palettes(rom, candidates, palettes, loads, adjacent, map_triples,
                                    verified_triples, staged)
    _associate_tilemaps(rom, candidates, loads, map_triples, verified_triples, sequences)
    palettes = palettes + staged
    stats = {"rom_size": len(rom), "pointer_targets": len(idx),
             "referenced_lz77_headers": len(lz_offsets) + len(rejected),
             "candidates": len(candidates), "lz77_candidates": len(lz_offsets),
             "raw_candidates": len(candidates) - len(lz_offsets), "rejected": len(rejected),
             "tile_sheet_4bpp": sum(1 for c in candidates if c.decoded_size >= 32),
             "tile_sheet_8bpp": sum(1 for c in candidates if c.compression == "lz77" and c.decoded_size % 64 == 0),
             "tilemap_shaped": sum(1 for c in candidates if c.decoded_size % 2 == 0),
             "tilemaps": sum(bool(c.tilemaps) for c in candidates),
             "palette_objects": len({p["target"] for p in palettes}),
             "palette_objects_trusted": len({p["target"] for p in palettes
                                             if p.get("tier") in ROM_EVIDENCE_TIERS}),
             "palette_resolved": sum(c.palette_for(4) is not None or c.palette_for(8) is not None
                                     for c in candidates),
             "palette_selected": sum(c.palette_choice(4) is not None or c.palette_choice(8) is not None
                                     for c in candidates),
             "palette_grey": sum(c.palette_choice(4) is None and c.palette_choice(8) is None
                                 for c in candidates),
             "palette_variants": sum(len(c.palettes) for c in candidates)}
    return {"schema": SCHEMA, "generator": GENERATOR, "stats": stats,
            "candidates": candidates, "palettes": palettes, "rejected": rejected}


def _render_id(c: Candidate, mode: dict) -> str:
    if mode["mode"] == "tilemap":
        return f"{c.id}-map-{mode['association']}"
    return f"{c.id}-8bpp" if mode["mode"] == "tile-sheet-8bpp" and len(c.render_modes()) > 1 else c.id


def _palette_choice_fields(c: Candidate, bpp: int) -> dict:
    choice = c.palette_choice(bpp)
    return {"palette_tier": choice.get("tier", "candidate") if choice else None,
            "palette_choice": _palette_variant(choice) if choice else None,
            "palette_variants_not_chosen": [_palette_variant(p) for p in c.palettes
                                            if p["bpp"] == bpp and p is not choice]}


def _palette_source(choice: dict | None) -> str:
    if choice is None:
        return "missing"
    return "rom-evidence" if choice.get("tier") in ROM_EVIDENCE_TIERS else "candidate"


def _tile_render(rom: Rom, c: Candidate, mode: dict, data: bytes, png_dir: Path) -> dict:
    """Write one tile-sheet interpretation as an indexed PNG and return its record."""
    render_id = _render_id(c, mode)
    bpp = mode["bpp"]
    palette = c.palette_choice(bpp)
    spec = palette["spec"] if palette is not None else ""
    choice_fields = _palette_choice_fields(c, bpp)
    wt = min(DEFAULT_WIDTH_TILES, mode["tiles"]) if mode["tiles"] else 1
    colours, _colour_source = palette_colours(rom, spec, bpp)
    pixels = tiles_to_pixels(data, mode["tiles"], bpp, wt)
    png = png_dir / f"{render_id}.png"
    write_indexed_png(png, pixels, colours, 4 if bpp == 4 else 8)
    side = png_dir / f"{render_id}.json"
    meta = {"id": c.id, "offset": f"0x{c.offset:07X}", "mode": mode["mode"],
            "compression": c.compression, "block_length": c.block_length,
            "decoded_size": c.decoded_size, "tiles": mode["tiles"], "bpp": bpp,
            "width_tiles": wt, "width": wt * 8, "height": -(-mode["tiles"] // wt) * 8,
            "palette": spec or "grey (palette not located)", "palette_source": _palette_source(palette),
            "palette_variants": c.palettes, "size_evidence": c.size_evidence,
            "refs": [r.to_dict() for r in c.refs], "png": png.name, "base_sha1": rom.sha1(),
            **choice_fields}
    side.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"id": c.id, "mode": mode["mode"], "bpp": bpp, "tiles": mode["tiles"],
            "width": wt * 8, "height": -(-mode["tiles"] // wt) * 8,
            "palette": spec or "grey (palette not located)", "palette_source": _palette_source(palette),
            "palette_ambiguous": len([p for p in c.palettes if p["bpp"] == bpp]) > 1,
            "png": f"png/{png.name}", **choice_fields}


def _tilemap_render(rom: Rom, c: Candidate, mode: dict, data: bytes,
                    candidates: dict[int, Candidate], png_dir: Path) -> dict:
    association = c.tilemaps[mode["association"]]
    bpp = association["bpp"]
    width, height = association["width"], association["height"]
    sheet = candidates[association["sheet_offset"]]
    sheet_data = sheet.data(rom)
    tile_count = sheet.decoded_size // (8 * bpp)
    strip = tiles_to_pixels(sheet_data, tile_count, bpp, 1)
    entries = struct.unpack(f"<{len(data) // 2}H", data)
    pixels = [[0] * (width * 8) for _ in range(height * 8)]
    for i, entry in enumerate(entries):
        x, y = (i % width) * 8, (i // width) * 8
        tile = entry & 0x3FF
        bank = entry >> 12
        for dy in range(8):
            sy = tile * 8 + (7 - dy if entry & 0x800 else dy)
            for dx in range(8):
                sx = 7 - dx if entry & 0x400 else dx
                value = strip[sy][sx]
                pixels[y + dy][x + dx] = value + bank * 16 if value else 0
    palette = sheet.palette_choice(bpp)
    spec = palette["spec"] if palette else ""
    if bpp == 4:
        colours = (palette_colours(rom, spec, 4)[0] if palette else GREY) * 16
    else:
        colours = palette_colours(rom, spec, 8)[0]
    choice_fields = _palette_choice_fields(sheet, bpp)
    render_id = _render_id(c, mode)
    png = png_dir / f"{render_id}.png"
    write_indexed_png(png, pixels, colours, 8)
    side = png_dir / f"{render_id}.json"
    meta = {"id": c.id, "offset": f"0x{c.offset:07X}", "mode": "tilemap",
            "compression": c.compression, "block_length": c.block_length, "decoded_size": c.decoded_size,
            "tiles": len(entries), "bpp": bpp, "width_tiles": width, "height_tiles": height,
            "width": width * 8, "height": height * 8,
            "palette": spec or "grey (palette not located)", "palette_source": _palette_source(palette),
            "palette_ambiguous": len([p for p in sheet.palettes if p["bpp"] == bpp]) > 1,
            "tilemap_ambiguous": len(c.tilemaps) > 1, "tilemap": association,
            "refs": [r.to_dict() for r in c.refs], "png": png.name, "base_sha1": rom.sha1(),
            **choice_fields}
    side.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"id": c.id, "mode": "tilemap", "bpp": bpp, "tiles": len(entries),
            "width": width * 8, "height": height * 8,
            "palette": spec or "grey (palette not located)", "palette_source": _palette_source(palette),
            "palette_ambiguous": len([p for p in sheet.palettes if p["bpp"] == bpp]) > 1,
            "tilemap_ambiguous": len(c.tilemaps) > 1, "tilemap": association,
            "png": f"png/{png.name}", **choice_fields}


def render_candidates(rom: Rom, result: dict, directory: Path, palettes: dict[int, dict] | None = None,
                      limit: int | None = None) -> list[dict]:
    """Render candidates with ROM evidence; catalogue palettes never participate."""
    png_dir = directory / "png"
    png_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict] = []
    by_offset = {c.offset: c for c in result["candidates"]}
    for c in result["candidates"]:
        if limit is not None and len(rendered) >= limit:
            break
        data = c.data(rom)
        for mode in c.render_modes():
            if mode["mode"] == "tilemap":
                rendered.append(_tilemap_render(rom, c, mode, data, by_offset, png_dir))
                continue
            if mode["mode"] == "bytes":
                rows = []
                for i in range(0, len(data), BYTE_MAP_WIDTH):
                    row = list(data[i:i + BYTE_MAP_WIDTH])
                    rows.append(row + [0] * (BYTE_MAP_WIDTH - len(row)))
                palette = c.palette_choice(4) or c.palette_choice(8)
                palette_bpp = palette["bpp"] if palette else 8
                spec = palette["spec"] if palette else ""
                colours = palette_colours(rom, spec, palette_bpp)[0]
                if palette_bpp == 4:
                    colours *= 16
                choice_fields = _palette_choice_fields(c, palette_bpp)
                png = png_dir / f"{c.id}.png"
                write_indexed_png(png, rows, colours, 8)
                side = png_dir / f"{c.id}.json"
                meta = {"id": c.id, "offset": f"0x{c.offset:07X}", "mode": mode["mode"],
                        "compression": c.compression, "block_length": c.block_length,
                        "decoded_size": c.decoded_size, "palette": spec or "grey (palette not located)",
                        "palette_source": _palette_source(palette), "palette_bpp": palette_bpp,
                        "refs": [r.to_dict() for r in c.refs], "png": png.name, "base_sha1": rom.sha1(),
                        **choice_fields}
                side.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                rendered.append({"id": c.id, "mode": mode["mode"], "bpp": mode["bpp"],
                                 "tiles": 0, "width": BYTE_MAP_WIDTH,
                                 "height": -(-len(data) // BYTE_MAP_WIDTH),
                                 "palette": spec or "grey (palette not located)",
                                 "palette_source": _palette_source(palette), "palette_bpp": palette_bpp,
                                 "png": f"png/{png.name}", **choice_fields})
                continue
            rendered.append(_tile_render(rom, c, mode, data, png_dir))
    return rendered


def catalog_palettes(rows: list[dict]) -> dict[int, dict]:
    """Palette specs of a graphics document, by ROM offset, for rendering crossrefs only."""
    out: dict[int, dict] = {}
    for row in rows:
        if not row.get("rom_offset"):
            continue
        m = _PALETTE.match(row.get("palette", "") or "")
        if not m:
            continue
        bpp = 8 if "(8bpp)" in row.get("tiles", "") else 4
        out[int(row["rom_offset"], 16)] = {"spec": m.group(0).strip(), "bpp": bpp,
                                           "source": f"catalog:{row['id']}"}
    return out


def _palette_colour_key(rom: Rom, spec: str, bpp: int) -> tuple | None:
    try:
        colours, _ = palette_colours(rom, spec, bpp)
        return tuple(colours)
    except (GraphicsError, ValueError):
        return None


def recall(result: dict, rows: list[dict], rom: Rom | None = None) -> dict:
    """Compare every catalogue row against discovery, including exact raw spans."""
    by_offset = {c.offset: c for c in result["candidates"]}
    matched, raw, missing, palette_comparison = [], [], [], []
    for row in rows:
        if not row.get("rom_offset"):
            missing.append({"id": row["id"], "reason": "no ROM offset"})
            continue
        offset = int(row["rom_offset"], 16)
        c = by_offset.get(offset)
        if c is None:
            missing.append({"id": row["id"], "offset": offset})
            continue
        compressed = row.get("compressed", "").startswith("yes")
        declared_size = int(row["size_bytes"])
        m = re.search(r"(\d+) bytes", row.get("compressed", ""))
        declared_block = int(m.group(1)) if compressed and m else declared_size
        item = {"id": row["id"], "offset": offset, "candidate": c.id,
                "compression": c.compression, "decoded_size": c.decoded_size,
                "size_matches": c.decoded_size == declared_size,
                "block_length": c.block_length, "block_matches": c.block_length == declared_block,
                "compression_matches": c.compression == ("lz77" if compressed else "none"),
                "size_evidence": c.size_evidence, "references": [r.to_dict() for r in c.refs]}
        matched.append(item)
        if not compressed:
            raw.append(item)
        bpp = 8 if "(8bpp)" in row.get("tiles", "") else 4
        baseline = _PALETTE.match(row.get("palette", "") or "")
        discovered = c.palette_choice(bpp)
        baseline_spec = baseline.group(0).strip() if baseline else ""
        colours_equal = None
        if baseline is None:
            status = "baseline-unresolved"
        elif discovered is None:
            status = "discovery-unresolved"
        elif rom is not None:
            chosen_key = _palette_colour_key(rom, discovered["spec"], bpp)
            colours_equal = chosen_key is not None and chosen_key == _palette_colour_key(rom, baseline_spec, bpp)
            status = "matched" if colours_equal else "different"
        else:
            colours_equal = discovered["spec"] == baseline_spec
            status = "matched" if colours_equal else "different"
        palette_comparison.append({"id": row["id"], "candidate": c.id, "baseline": baseline_spec,
                                   "discovered": discovered["spec"] if discovered else "",
                                   "tier": discovered.get("tier", "candidate") if discovered else None,
                                   "method": _palette_method(discovered) if discovered else "",
                                   "colours_equal": colours_equal, "status": status})
    complete = (not missing and all(m["size_matches"] and m["block_matches"] and m["compression_matches"]
                                    for m in matched))
    return {"rows": len(rows), "matched": matched, "raw": raw, "missing": missing,
            "palette_comparison": palette_comparison, "complete": complete}


def write(result: dict, rom: Rom, directory: Path, rows: list[dict] | None = None,
          palettes: dict[int, dict] | None = None, render: bool = True,
          limit: int | None = None, profile: str = "") -> dict:
    """Write the candidate index, the rendered PNG gallery and the recall report."""
    directory.mkdir(parents=True, exist_ok=True)
    rendered = render_candidates(rom, result, directory, limit=limit) if render else []
    candidates = []
    for c in result["candidates"]:
        rec = c.to_dict()
        rec["renders"] = [r for r in rendered if r["id"] == c.id]
        choices = [choice for choice in (c.palette_choice(4), c.palette_choice(8)) if choice is not None]
        palette = choices[0] if choices else None
        rec["palette"] = palette["spec"] if palette else ""
        rec["palette_bpp"] = palette["bpp"] if palette else None
        rec["palette_source"] = _palette_source(palette)
        rec["palette_choices"] = [_palette_variant(choice) for choice in choices]
        rec.update(_palette_choice_fields(c, palette["bpp"]) if palette else
                   {"palette_tier": None, "palette_choice": None, "palette_variants_not_chosen": []})
        candidates.append(rec)
    doc = {"schema": SCHEMA, "generator": GENERATOR, "profile": profile,
           "rom_sha1": rom.sha1(), "stats": result["stats"],
           "method": "rom-code-and-data-evidence",
           "limitations": [
               "Candidates are research objects, not verified text graphics.",
               "Bare pointer words do not establish graphic, tilemap or palette semantics.",
               "Grey, candidate and ambiguous palettes are unresolved and do not count as actual palette coverage.",
               "Raw anchor and bounded duplicate spans retain their explicit inference evidence.",
               "An LZ77 size divisible by both 32 and 64 renders as 4bpp and as 8bpp without map evidence.",
               "4096-byte maps keep 64x32 and 32x64 screen-block orders as unverified alternatives.",
               "DMA-staged palettes are candidates: the DMA transfer is not on the proved path.",
           ],
           "palette_objects": result["palettes"], "candidates": candidates, "rejected": result["rejected"]}
    if rows is not None:
        doc["recall"] = recall(result, rows, rom)
    (directory / "index.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (directory / "report.md").write_text(report(doc, rendered), encoding="utf-8")
    return doc


def report(doc: dict, rendered: list[dict]) -> str:
    stats = doc["stats"]
    selected = stats.get("palette_selected", 0)
    share = 100 * selected / stats["candidates"] if stats["candidates"] else 0
    lines = ["# Graphics candidates", "",
             f"Profile `{doc.get('profile')}`; ROM SHA-1 `{doc['rom_sha1']}`.", "",
             f"Referenced LZ77 headers: {stats['referenced_lz77_headers']}; candidates "
             f"{stats['candidates']} ({stats['raw_candidates']} raw); rejected false candidates {stats['rejected']}.", "",
             f"4bpp tile sheets: {stats['tile_sheet_4bpp']}; 8bpp-capable: {stats['tile_sheet_8bpp']}; "
             f"composed tilemaps: {stats['tilemaps']}; trusted palette objects: "
             f"{stats.get('palette_objects_trusted', 0)}; palette-resolved candidates: "
             f"{stats['palette_resolved']}.", "",
             f"Chosen palette variants: {selected} of {stats['candidates']} candidates ({share:.2f}%); "
             f"grey: {stats.get('palette_grey', stats['candidates'] - selected)}.", "",
             "Palette choice order: " + ", ".join(PALETTE_METHOD_ORDER) +
             "; then fewest alternative specs at the same evidence site, lowest site, spec and bank. "
             "Candidates without a variant use a grey ramp.", "",
             "Grey, candidate and ambiguous palettes remain unresolved; catalogue palettes are comparison data only.", "",
             "## Rejected false candidates", ""]
    if doc["rejected"]:
        lines += ["| Offset | References | Reason |", "|---|---|---|"]
        for item in doc["rejected"][:200]:
            refs = ", ".join(f"0x{r['at']:07X}:{r['kind']}" for r in item["refs"][:8])
            lines.append(f"| 0x{item['offset']:07X} | {refs} | {item['reason']} |")
        if len(doc["rejected"]) > 200:
            lines.append(f"| ... {len(doc['rejected']) - 200} more in index.json | | |")
    else:
        lines.append("None.")
    lines += ["", "## Candidates by reference kind", ""]
    groups: dict[str, list[dict]] = {}
    for c in doc["candidates"]:
        key = "+".join(c["reference_kinds"]) if c["reference_kinds"] else "none"
        groups.setdefault(key, []).append(c)
    for key in sorted(groups):
        lines += [f"### `{key}` ({len(groups[key])})", "",
                  "| Id | Offset | Block | Decoded | 4bpp | 8bpp | Renders |",
                  "|---|---|---|---|---|---|---|"]
        for c in groups[key][:200]:
            lines.append(f"| {c['id']} | 0x{c['offset']:07X} | {c['block_length']} | {c['decoded_size']} | "
                         f"{c['shape']['bpp4_tiles'] or '-'} | {c['shape']['bpp8_tiles'] or '-'} | "
                         f"{', '.join(r['png'] for r in c['renders']) or '-'} |")
        if len(groups[key]) > 200:
            lines.append(f"... {len(groups[key]) - 200} more in index.json")
    if "recall" in doc:
        rec = doc["recall"]
        mismatches = [m for m in rec["matched"]
                      if not (m["size_matches"] and m["block_matches"] and m["compression_matches"])]
        lines += ["", "## Catalogue recall", "",
                  f"Rows: {rec['rows']}; matched candidates: {len(rec['matched'])}; "
                  f"raw candidates matched: {len(rec['raw'])}; missing: {len(rec['missing'])}; "
                  f"mismatched: {len(mismatches)}; complete: {rec['complete']}.", ""]
        comparison = rec.get("palette_comparison", [])
        if comparison:
            matched_colours = sum(item["status"] == "matched" for item in comparison)
            lines += [f"Chosen variants with catalogue colours: {matched_colours} of {len(comparison)} rows.", "",
                      "| Row | Candidate | Chosen variant | Tier | Catalogue palette | Colours equal |",
                      "|---|---|---|---|---|---|"]
            for item in comparison:
                equal = {True: "yes", False: "no"}.get(item["colours_equal"], "-")
                lines.append(f"| {item['id']} | {item['candidate']} | {item['discovered'] or 'grey'} | "
                             f"{item['tier'] or '-'} | {item['baseline'] or '-'} | {equal} |")
            lines.append("")
        if rec["missing"]:
            lines += ["| Missing id | Offset |", "|---|---|"]
            lines += [f"| {m['id']} | 0x{m.get('offset', 0):07X} |" for m in rec["missing"]]
        if mismatches:
            lines += ["", "Size, block-length or compression mismatches:", ""]
            lines += [f"- {m['id']}: 0x{m['offset']:07X}" for m in mismatches]
    return "\n".join(lines) + "\n"


def _graphic_blocks(text: str) -> dict[str, tuple[int, int]]:
    """Row id -> (start, end) of each [[graphic]] block, in file order."""
    blocks: dict[str, tuple[int, int]] = {}
    starts = [m.start() for m in re.finditer(r"(?m)^\[\[graphic\]\]\s*$", text)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        block = text[start:end]
        m = re.search(r"(?m)^id\s*=\s*\"([^\"]+)\"\s*$", block)
        if m:
            blocks[m.group(1)] = (start, end)
    return blocks


def _rom_offset_of(text: str, start: int, end: int) -> str | None:
    m = re.search(r"(?m)^rom_offset\s*=\s*\"([^\"]+)\"\s*$", text[start:end])
    return m.group(1) if m else None


def _replace_flag(text: str, block: tuple[int, int], category: str, reason: str) -> str:
    """Replace only the category and reason lines of one [[graphic]] block."""
    start, end = block
    body = text[start:end]
    for key, value in (("category", category), ("reason", reason)):
        quoted = json.dumps(value, ensure_ascii=False)
        line = f'{key} = {quoted}'
        pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
        if pattern.search(body):
            body = pattern.sub(lambda _m: line, body, count=1)
        else:
            raise ValueError(f"{key} line missing from [[graphic]] block")
    return body


def _candidate_row(candidate: dict, category: str, reason: str) -> str:
    """A complete build record from one unambiguous discovery candidate."""
    try:
        gid = candidate["id"]
        offset = int(candidate["offset"])
        compression = candidate["compression"]
        block_length = int(candidate["block_length"])
        size = int(candidate["decoded_size"])
        refs = candidate["refs"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{candidate.get('id', '?')}: incomplete discovery record") from exc
    if not re.fullmatch(r"g[0-9A-Fa-f]{7}", gid) or int(gid[1:], 16) != offset:
        raise ValueError(f"{gid}: candidate id and offset differ")
    if compression not in ("lz77", "none") or size < 1 or block_length < 1:
        raise ValueError(f"{gid}: invalid compression or size")
    modes = [m for m in candidate.get("render_modes", []) if m["mode"].startswith("tile-sheet-")]
    if len(modes) != 1:
        raise ValueError(f"{gid}: tile count and bpp are unresolved")
    mode = modes[0]
    bpp, tiles = int(mode["bpp"]), int(mode["tiles"])
    if tiles < 1 or tiles * 8 * bpp > size:
        raise ValueError(f"{gid}: tile geometry exceeds the decoded size")
    palette = candidate.get("palette", "")
    variants = [p for p in candidate.get("palette_variants", []) if p.get("bpp") == bpp
                and p.get("tier") in ROM_EVIDENCE_TIERS]
    specs = {p.get("spec", "") for p in variants}
    if len(specs) != 1:
        raise ValueError(f"{gid}: an unambiguous ROM-evidenced palette is required")
    proved_palette = next(iter(specs))
    if (candidate.get("palette_source") != "rom-evidence" or candidate.get("palette_bpp") != bpp
            or palette not in ("", proved_palette)):
        raise ValueError(f"{gid}: palette metadata does not match its proof")
    palette = proved_palette
    if not _PALETTE.fullmatch(palette):
        raise ValueError(f"{gid}: invalid palette location")
    if not isinstance(refs, list) or any(not isinstance(r, dict) or not isinstance(r.get("at"), int) for r in refs):
        raise ValueError(f"{gid}: invalid pointer metadata")
    pointers = ", ".join(f"0x{r['at']:07X}" for r in sorted(refs, key=lambda r: r["at"]))
    if candidate.get("relocation_ambiguous"):
        pointers += " (+ unaligned or incomplete references; relocation unsafe)"
    if not pointers:
        pointers = "none found; unreferenced bounded duplicate"
    fields = [("id", gid), ("rom_offset", f"0x{offset:07X}"),
              ("compressed", f"yes (LZ77, {block_length} bytes)" if compression == "lz77" else "no"),
              ("size_bytes", str(size)), ("tiles", f"{tiles} (8bpp)" if bpp == 8 else str(tiles)),
              ("palette", palette + " (ROM code/data evidence)"), ("pointer_at", pointers),
              ("size_evidence", json.dumps(candidate.get("size_evidence", {}), sort_keys=True)),
              ("palette_evidence", json.dumps([p for p in candidate.get("palette_variants", [])
                                                if p.get("bpp") == bpp], sort_keys=True)),
              ("reference_evidence", json.dumps(refs, sort_keys=True)),
              ("category", category), ("reason", reason)]
    return "\n[[graphic]]\n" + "".join(f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in fields)


def write_flags(path: str | Path, flags: list[dict], candidates=None, rom: Rom | None = None) -> list[str]:
    """Transactionally flag existing rows or materialize evidenced new candidates."""
    import tomllib
    from ..core.graphics import graphics_document

    path = Path(path)
    migrated = {row["id"] for row in graphics_document(path)["graphic"] if "notes_schema" in row}
    text = path.read_text(encoding="utf-8")
    blocks = _graphic_blocks(text)
    offsets: dict[int, list[str]] = {}
    for gid, (start, end) in blocks.items():
        offset = _rom_offset_of(text, start, end)
        if offset is not None:
            offsets.setdefault(int(offset, 16), []).append(gid)
    candidate_document = candidates if isinstance(candidates, dict) else None
    if candidate_document is not None and rom is not None and candidate_document.get("rom_sha1") != rom.sha1():
        raise ValueError("discovery index ROM does not match the supplied ROM")
    if isinstance(candidates, dict):
        candidates = candidates["candidates"]
    by_id = {c["id"].lower(): c for c in candidates or []}
    updated = []
    planned = {}
    additions = {}
    fresh_candidates = None
    for flag in flags:
        if not isinstance(flag, dict):
            raise ValueError(f"flag {flag!r} must be a table")
        if set(flag) != {"id", "category", "reason"}:
            raise ValueError(f"flag {flag!r} needs exactly id, category and reason")
        gid, category, reason = flag["id"], flag["category"], flag["reason"]
        if category not in VALID_CATEGORIES:
            raise ValueError(f"{gid}: category must be one of {', '.join(VALID_CATEGORIES)}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{gid}: reason must be a non-empty string")
        row_id = gid if gid in blocks else None
        if row_id is None and re.fullmatch(r"g[0-9A-Fa-f]{7}", gid):
            rows = offsets.get(int(gid[1:], 16), [])
            if len(rows) == 1:
                row_id = rows[0]
            elif len(rows) > 1:
                raise ValueError(f"{gid}: rom_offset matches {', '.join(rows)}; name the row id")
        if row_id is None and gid.lower() in by_id:
            if rom is None:
                raise ValueError(f"{gid}: the original ROM is required to re-derive candidate evidence")
            if fresh_candidates is None:
                fresh_candidates = scan(rom)["candidates"]
            candidate = next((c for c in fresh_candidates if c.id.lower() == gid.lower()), None)
            if candidate is None:
                raise ValueError(f"{gid}: candidate evidence cannot be re-derived from the supplied ROM")
            palette = candidate.palette_for(4) or candidate.palette_for(8)
            record = candidate.to_dict()
            record["palette"] = palette["spec"] if palette else ""
            record["palette_bpp"] = palette["bpp"] if palette else None
            record["palette_source"] = "rom-evidence" if palette else "missing"
            row_id = candidate.id
            additions[row_id] = _candidate_row(record, category, reason)
            updated.append(row_id)
            continue
        if row_id is None or row_id not in blocks:
            raise ValueError(f"{gid}: no [[graphic]] row or discovery record in {path}")
        if row_id in migrated:
            raise ValueError(f"{row_id}: edit the structured notes")
        planned[row_id] = (category, reason)
        updated.append(row_id)
    edits = []
    for row_id, (category, reason) in planned.items():
        start, end = blocks[row_id]
        edits.append((start, end, _replace_flag(text, blocks[row_id], category, reason)))
    for start, end, body in sorted(edits, reverse=True):
        text = text[:start] + body + text[end:]
    text += "".join(additions[gid] for gid in additions)
    document = tomllib.loads(text)
    rows = document.get("graphic", [])
    required = {"id", "rom_offset", "compressed", "size_bytes", "tiles", "palette", "pointer_at",
                "category", "reason"}
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("duplicate graphic id after flagging")
    for gid in additions:
        row = next(row for row in rows if row["id"] == gid)
        if not required <= set(row) or any(not isinstance(value, str) for value in row.values()):
            raise ValueError(f"{gid}: incomplete build record")
    path.write_text(text, encoding="utf-8")
    return updated
