"""Mining record and draw-table bindings from native consumers."""
from __future__ import annotations

from ..core.thumb import ThumbError, decode
from .foundations import evidence
from .table_core import _issue, _set


def _instruction(rom, start, offset, mnemonic, immediate=None, width=None):
    instruction = decode(rom.data, start + offset)
    if instruction.mnemonic != mnemonic or immediate is not None and instruction.immediate != immediate:
        return None
    if width is not None and instruction.width != width:
        return None
    return instruction


def run(rom, result, knowledge, readers, bindings):
    descriptor = knowledge.get("mining")
    items = bindings.get("items")
    if not descriptor or items is None:
        return
    result_matches = readers.matches.get(descriptor["result"], ())
    placement_matches = readers.matches.get(descriptor["placement"], ())
    if len(result_matches) != 1 or len(placement_matches) != 1:
        _issue(result, "pokedex.items.mining", "Native mining readers are missing or ambiguous.",
               "semantic-reader")
        return
    start = result_matches[0]
    try:
        base_load = decode(rom.data, start + descriptor["result_base"])
        stride = decode(rom.data, start + descriptor["result_stride"])
        item = decode(rom.data, start + descriptor["result_item"])
        draw_load = decode(rom.data, placement_matches[0] + descriptor["draw_base"])
        draw_add = decode(rom.data, placement_matches[0] + descriptor["draw_add"])
        draw_count = decode(rom.data, placement_matches[0] + descriptor["draw_count"])
    except ThumbError:
        base_load = stride = item = draw_load = draw_add = draw_count = None
    if (base_load is None or base_load.literal is None
            or stride is None or stride.mnemonic != "mov" or stride.immediate != 24
            or item is None or item.mnemonic != "ldrh" or item.width != 2
            or draw_load is None or draw_load.literal is None
            or draw_add is None or draw_add.mnemonic != "add" or draw_add.immediate is None
            or draw_count is None or draw_count.mnemonic != "mov"
            or draw_count.immediate is None or draw_count.immediate <= 0):
        _issue(result, "pokedex.items.mining", "Decoded mining record and draw-table operands disagree.",
               "semantic-reader+instruction-operands")
        return
    base = rom.ptr_at(base_load.literal)
    draw_base = (rom.ptr_at(draw_load.literal) or 0) + draw_add.immediate
    if base is None or base + 24 * 2 > len(rom) or draw_base < 0 or draw_base + draw_count.immediate > len(rom):
        _issue(result, "pokedex.items.mining", "Mining literals leave ROM bounds.", "table-bounds",
               [base_load.literal, draw_load.literal])
        return
    count = 0
    while count < 4096 and base + (count + 1) * 24 <= len(rom):
        at = base + count * 24
        if not (0 <= rom.u16(at) < items.count and rom.ptr_at(at + 12) is not None):
            break
        count += 1
    if count < 2:
        _issue(result, "pokedex.items.mining", "The complete mining record domain has no valid boundary.",
               "complete-record-domain", [base], rows=count)
        return
    draws = []
    while base > 0 and draw_base + (len(draws) + 1) * draw_count.immediate <= len(rom):
        at = draw_base + len(draws) * draw_count.immediate
        if any(byte >= count for byte in rom.data[at:at + draw_count.immediate]):
            break
        draws.append(at)
    if not draws:
        _issue(result, "pokedex.items.mining", "No complete mining draw array is bounded by the record domain.",
               "draw-array-domain", [draw_base])
        return
    proof = evidence("Native mining readers prove the 24-byte record stride, item halfword and bounded draw arrays.",
        "semantic-readers+complete-record-domain+draw-array-domain",
        [base, draw_base], [base_load.address, draw_load.address, draw_add.address,
                            draw_count.address, item.address, stride.address],
        stride=24, rows=count, draw_count=draw_count.immediate, draw_arrays=len(draws))
    _set(result, "pokedex.items.mining",
         {"base": base, "stride": 24, "count": count, "draws": draws,
          "draw_count": draw_count.immediate}, proof)
    item_proof = evidence("The native mining result reads the item halfword at record offset zero.",
        "semantic-reader+accessor", [base], [item.address], offset=0, width=2)
    _set(result, "pokedex.items.mining.item", {"offset": 0, "width": 2}, item_proof)
