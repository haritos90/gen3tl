"""Reference evidence kept separate from compatibility inventory classifications."""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter

from ..core.charmap import Charmap, CharmapError
from ..core.graphics import GraphicsError, lz77_decompress
from ..core.thumb import ThumbError, decode, walk
from .scan import VERIFIED_KINDS, pointer_index


def binary_spans(rom, index):
    """Bound referenced compressed blocks and direct-sound sample payloads."""
    spans = []
    for start, refs in sorted(index.items()):
        if start + 16 > len(rom):
            continue
        if rom.data[start] == 16 and 0 < rom.u32(start) >> 8 <= 0x40000:
            try:
                data, used = lz77_decompress(rom.data, start)
            except GraphicsError:
                pass
            else:
                if len(data) >= 32 and used >= 8:
                    spans.append({"kind": "lz77", "start": start, "end": start + used,
                                  "decoded_bytes": len(data), "references": refs,
                                  "method": "referenced-header+complete-decode"})
        if start % 4 or rom.u16(start) not in (0, 1) or rom.u16(start + 2) not in (0, 0x4000):
            continue
        rate, loop, size = (rom.u32(start + offset) for offset in (4, 8, 12))
        tones = [at - 4 for at in refs if at % 4 == 0 and at >= 4
                 and rom.data[at - 4] in (0, 8, 32, 40) and rom.data[at - 3] <= 127
                 and rom.data[at - 2] == 0]
        if not tones or not 1000 * 1024 <= rate <= 100000 * 1024 or not 16 <= size <= 0x100000 or loop >= size:
            continue
        compressed = rom.u16(start) == 1
        length = size // 64 * 33 if compressed else size
        if not length or start + 16 + length > len(rom):
            continue
        spans.append({"kind": "dpcm" if compressed else "pcm", "start": start + 16,
                      "end": start + 16 + length, "header": start, "samples": size,
                      "rate": rate, "loop": loop, "tones": tones,
                      "method": "wave-header+tone-record",
                      "assumption": "Family direct-sound layout; compressed bounds cover complete 64-sample blocks only."})
    return sorted(spans, key=lambda row: (row["start"], row["end"], row["kind"]))


class Spans:
    """Find every containing interval, including overlapping intervals."""

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda row: row["start"])
        self.starts = [row["start"] for row in self.rows]
        self.ends = []
        end = 0
        for row in self.rows:
            end = max(end, row["end"])
            self.ends.append(end)

    def containing(self, start, size=4):
        index = bisect_right(self.starts, start) - 1
        found = []
        while index >= 0 and self.ends[index] >= start + size:
            row = self.rows[index]
            if row["end"] >= start + size:
                found.append(row)
            index -= 1
        return found


def function_instructions(rom, start, limit):
    try:
        instructions = walk(rom.data, start, min(len(rom) & ~1, start + limit), limit=2048)
    except ThumbError:
        return ()
    if any(row.mnemonic == "undefined" for row in instructions):
        return ()
    popped = {row.address + row.size: row.registers for row in instructions if row.mnemonic == "pop"}
    if not any(row.mnemonic == "pop" and 15 in row.registers
               or row.mnemonic == "bx" and row.rm in popped.get(row.address, ()) for row in instructions):
        return ()
    return instructions


def instruction_word(rom, position, limit=4096):
    """Require a returning function path covering all four bytes."""
    for start in range(position & ~1, max(-1, position - limit), -2):
        if rom.u16(start) & 0xFF00 != 0xB500:
            continue
        instructions = function_instructions(rom, start, limit)
        covering = [row for row in instructions if row.address < position + 4
                    and row.address + row.size > position]
        covered = {at for row in covering for at in range(row.address, row.address + row.size)}
        if set(range(position, position + 4)) <= covered:
            return {"function": start, "instructions": [
                {"at": row.address, "size": row.size, "mnemonic": row.mnemonic,
                 "halfwords": list(row.raw)} for row in covering],
                "method": "push-lr+control-flow+return",
                "assumption": "A valid returning function shape identifies executable bytes."}
    return None


def literal_word(rom, position):
    if position % 4:
        return None
    loads = []
    for at in range(position - 2, max(-1, position - 1026), -2):
        half = rom.u16(at)
        if half & 0xF800 == 0x4800 and ((at + 4) & ~3) + (half & 255) * 4 == position:
            if proof := instruction_word(rom, at, limit=4096):
                loads.append({"load": at, "register": half >> 8 & 7, **proof})
    return {"pool": position, "loads": loads, "method": "returning-function+literal-load"} if loads else None


def unused_literal(rom, position, index):
    """Recognize skipped pool words without direct readers or addresses."""
    if position % 4 or index.get(position) or index.get(position + 1):
        return None
    nearby = [decode(rom.data, at) for at in range(max(0, position - 1026), position, 2)]
    if any(row.literal == position or row.mnemonic == "add" and row.rn == 15 and row.immediate is not None
           and ((row.address + 4) & ~3) + row.immediate == position for row in nearby):
        return None
    for at in range(max(0, position - 4104) & ~3, min(len(rom) - 3, position + 4096), 4):
        word = rom.u32(at)
        if word & 0x0F7F0000 == 0x051F0000:
            target = at + 8 + ((word & 4095) if word & 0x800000 else -(word & 4095))
            if target == position:
                return None
    for branch in reversed(nearby):
        if (branch.mnemonic != "b" or branch.condition or not position + 4 <= branch.target
                or branch.target - branch.address > 256):
            continue
        siblings = [row for row in nearby if row.address < branch.address and row.literal is not None
                    and branch.address < row.literal < branch.target]
        if len({row.literal for row in siblings}) >= 2:
            return {"start": branch.address + 2, "end": branch.target,
                    "branch": branch.address, "sibling_loads": [row.address for row in siblings],
                    "method": "skipped-pool+no-literal-reader-or-address",
                    "assumption": "Skipped native pools are addressed by literal loads or explicit addresses."}
    return None


def compressed_sequence(rom, position, limit=0x10000):
    """Bound unreferenced payloads by three complete adjacent compressed blocks."""
    for start in range(position & ~3, max(-1, position - limit), -4):
        if rom.data[start] != 16:
            continue
        blocks, at = [], start
        for _ in range(3):
            if at + 4 > len(rom) or rom.data[at] != 16:
                break
            size = rom.u32(at) >> 8
            if not 32 <= size <= 0x40000 or size % 32:
                break
            try:
                data, used = lz77_decompress(rom.data, at)
            except GraphicsError:
                break
            blocks.append({"start": at, "end": at + used, "decoded_bytes": len(data)})
            at = (at + used + 3) & ~3
        if len(blocks) == 3 and blocks[0]["start"] <= position and position + 4 <= blocks[0]["end"]:
            return {**blocks[0], "blocks": blocks, "method": "aligned-sequence+complete-decode"}
    return None


def trampoline_instructions(rom, position, index, limit=4096):
    """Follow literal-loaded hooks through a returning native suffix."""
    for start in range(position & ~1, max(-1, position - limit), -2):
        hooks = []
        for pool in index.get(start + 1, ()):
            if pool % 4:
                continue
            for at in range(max(0, pool - 1026), pool, 2):
                load = decode(rom.data, at)
                if load.literal != pool:
                    continue
                branch = decode(rom.data, at + 2)
                if branch.mnemonic == "bx" and branch.rm == load.rd:
                    hooks.append(at)
        if not hooks:
            continue
        todo, found, edges, returns = [start], {}, [], []
        while todo and len(found) < 10000:
            at = todo.pop()
            if at in found:
                continue
            try:
                row = decode(rom.data, at)
            except ThumbError:
                break
            found[at] = row
            if row.mnemonic == "undefined":
                break
            following = row.successors
            if row.mnemonic == "pop" and 15 in row.registers:
                returns.append(at)
            if row.mnemonic == "bx":
                previous = found.get(at - 2)
                if previous and previous.literal is not None and previous.rd == row.rm:
                    target = rom.ptr_at(previous.literal)
                    if target is None or not target & 1:
                        break
                    following = (target & ~1,)
                    edges.append({"at": at, "pool": previous.literal, "target": target & ~1})
                elif row.rm == 14 or previous and previous.mnemonic == "pop" and row.rm in previous.registers:
                    returns.append(at)
                else:
                    break
            todo.extend(reversed(following))
        else:
            covering = [row for row in found.values() if row.address < position + 4
                        and position < row.address + row.size]
            covered = {at for row in covering for at in range(row.address, row.address + row.size)}
            if not todo and returns and set(range(position, position + 4)) <= covered:
                return {"function": start, "hooks": hooks, "trampolines": edges, "returns": sorted(returns),
                        "instructions": [{"at": row.address, "halfwords": list(row.raw),
                                          "mnemonic": row.mnemonic} for row in covering],
                        "method": "literal-hook+control-flow+return",
                        "assumption": "Returning hook code identifies instruction bytes, not runtime reachability."}
    return None


def overlapping_words(rom, position):
    """Reject an unaligned window spanning distinct aligned pointer fields."""
    if position % 4 == 0:
        return None
    word = position & ~3
    if rom.ptr_at(word) is None or rom.ptr_at(word + 4) is None:
        return None
    start, end = word, word + 8
    while start >= 4 and rom.ptr_at(start - 4) is not None:
        start -= 4
    while end + 4 <= len(rom) and rom.ptr_at(end) is not None:
        end += 4
    if end - start < 12:
        return None
    return {"start": start, "end": end, "stride": 4,
            "words": [{"at": at, "target": rom.ptr_at(at)} for at in (word, word + 4)],
            "method": "overlapping-aligned-pointer-fields"}


def text_record(rom, position, charmap=None):
    """Recognize null-callback menu rows, including letterless labels."""
    if position % 4:
        return None
    cm = charmap or Charmap()

    def valid(at):
        if not 0 <= at <= len(rom) - 8 or rom.u32(at + 4) != 0:
            return False
        target = rom.ptr_at(at)
        if target is None:
            return False
        try:
            text, end = cm.decode(rom.data, target, limit=128)
        except CharmapError:
            return False
        return bool(text) and "{0x" not in text and all(
            byte == 0 or 0xA1 <= byte <= 0xEE or byte in (0xF7, 0xF8, 0xF9, 0xFC, 0xFD, 0xFE, 0xFF)
            for byte in rom.data[target:end])

    if not valid(position):
        return None
    start, end = position, position + 8
    while valid(start - 8):
        start -= 8
    while valid(end):
        end += 8
    if end - start < 24:
        return None
    return {"start": start, "end": end, "stride": 8, "field": 0,
            "method": "text-pointer+null-callback-record-run"}


def text_tables(index, starts):
    """Infer constant-stride text fields from at least three rows."""
    positions = {at for target in starts for at in index.get(target, ()) if at % 4 == 0}
    rows = []
    for stride in range(4, 65, 4):
        for start in sorted(positions):
            if start - stride in positions or start + stride not in positions:
                continue
            end = start + stride
            while end in positions:
                end += stride
            count = (end - start) // stride
            if count >= 3:
                rows.append({"start": start, "end": end - stride + 4, "stride": stride,
                             "fields": [0], "rows": count, "tier": "shape",
                             "method": "constant-stride-text-pointer-run"})
    return rows


def script_operands(rom, profile, handlers=()):
    """Record exact text operands and roots, with independent length evidence."""
    from .maps import Walker

    roots = {}

    class Census(Walker):
        def walk_script(self, start, *args):
            previous = self.decoded
            self.decoded = set()
            super().walk_script(start, *args)
            for pc in self.decoded:
                roots.setdefault(pc, set()).add(start)
            previous.update(self.decoded)
            self.decoded = previous

    walker = Census(rom, profile)
    walker.walk_all()
    walker.walk_structural()
    formats = {row["opcode"]: row for row in handlers}
    operands, commands = {}, []
    for start, end in walker.command_spans():
        opcode = rom.data[start]
        derived = formats.get(opcode, {})
        matched = derived.get("lengths") == [end - start]
        proof = {"command": start, "opcode": opcode, "length": end - start,
                 "roots": sorted(roots.get(start, ())),
                 "length_source": "derived" if matched else "family-compatibility",
                 "handler_status": derived.get("status", "unavailable")}
        commands.append({"start": start, "end": end, **proof})
        offsets = (walker.tb.get(rom.data[start + 1], ()) if opcode == 0x5C else
                   (walker.text_ptr_at[opcode],) if opcode in walker.text_ptr_at else ())
        for offset in offsets:
            if offset + 4 <= end - start:
                operands[start + offset] = {**proof, "field": offset}
    return operands, commands


def classify(rom, position, *, binary, text, operands, commands, known=None, index=None):
    """Classify a word without importing its compatibility reference kind."""
    if rows := binary.containing(position):
        return {"status": "non-reference", "kind": rows[0]["kind"], "evidence": rows}
    if rows := text.containing(position):
        return {"status": "non-reference", "kind": "text-bytes", "evidence": rows}
    if position in operands:
        return {"status": "reference", "kind": "script", "evidence": [operands[position]]}
    if known is not None and (rows := known.containing(position)):
        fields = [row for row in rows if (position - row["start"]) % row["stride"] in row["fields"]]
        if fields:
            return {"status": "reference", "kind": "table", "evidence": fields}
    if proof := overlapping_words(rom, position):
        return {"status": "non-reference", "kind": "overlapping-pointer-fields", "evidence": [proof]}
    if proof := text_record(rom, position):
        return {"status": "reference", "kind": "table", "evidence": [proof]}
    if proof := literal_word(rom, position):
        return {"status": "reference", "kind": "literal-pool", "evidence": [proof]}
    if proof := instruction_word(rom, position):
        return {"status": "non-reference", "kind": "thumb-instructions", "evidence": [proof]}
    if proof := compressed_sequence(rom, position):
        return {"status": "non-reference", "kind": "lz77", "evidence": [proof]}
    if index is not None:
        if proof := unused_literal(rom, position, index):
            return {"status": "non-reference", "kind": "unused-literal", "evidence": [proof]}
        if proof := trampoline_instructions(rom, position, index):
            return {"status": "non-reference", "kind": "thumb-instructions", "evidence": [proof]}
    rows = commands.containing(position)
    return {"status": "unclassified", "kind": "unclassified", "evidence": rows,
            "reason": ("Walked command contains the word but no text operand begins there."
                       if rows else "No proved operand, table field, native reader or containing binary span.")}


def audit(rom, inventory, profile, handlers=()):
    """Census all direct pointer positions and retain unresolved evidence."""
    from .verification import references
    if inventory.get("rom_sha1") != rom.sha1():
        raise ValueError("reference inventory does not identify this ROM")
    index = pointer_index(rom)
    binary = Spans(binary_spans(rom, index))
    text = Spans([{"start": row["offset"], "end": row["offset"] + row["length"],
                   "id": row["id"]} for row in inventory["entries"]])
    operands, spans = script_operands(rom, profile, handlers)
    commands = Spans(spans)
    tables = Spans([{"start": row["base"], "end": row["base"] + row["stride"] * row["count"],
                     "stride": row["stride"], "fields": row["fields"], "name": row["name"],
                     "method": "discovered-pointer-table"}
                    for row in profile.get("strings", {}).get("pointer_table", [])]
                   + text_tables(index, {row["offset"] for row in inventory["entries"]}))
    cached, strings = {}, []
    for row in inventory["entries"]:
        positions = sorted(index.get(row["offset"], []))
        refs = []
        for at in positions:
            if at not in cached:
                cached[at] = classify(rom, at, binary=binary, text=text, operands=operands,
                                      commands=commands, known=tables, index=index)
            refs.append({"at": at, **cached[at]})
        unknown = [ref["at"] for ref in refs if ref["status"] == "unclassified"]
        references(rom, row["offset"], [ref["at"] for ref in refs], index)
        old = {ref["at"] for ref in row["refs"]}
        strings.append({"id": row["id"], "offset": row["offset"], "references": refs,
                        "pointer_index_count": len(positions), "count_equal": len(refs) == len(positions),
                        "compatibility_positions_equal": old == set(positions),
                        "compatibility_only": sorted(old - set(positions)),
                        "index_only": sorted(set(positions) - old),
                        "relocation": "in-place-only" if unknown else "classified",
                        "unclassified": unknown})
    counts = Counter((ref["status"], ref["kind"]) for ref in cached.values())
    return {"strings": strings, "counts": [
        {"status": status, "kind": kind, "count": count} for (status, kind), count in sorted(counts.items())],
        "binary_spans": binary.rows, "commands": len(spans),
        "pointer_count_equal": sum(row["count_equal"] for row in strings),
        "compatibility_positions_equal": sum(row["compatibility_positions_equal"] for row in strings)}


def inventory_classifications(rom, inventory, profile, index):
    """Extend established reference kinds with evidence for unresolved words."""
    unread = {row["at"]: row for row in profile.get("coverage", {}).get("unread_reference", [])}
    binary = Spans(binary_spans(rom, index))
    text = Spans([{"start": row["offset"], "end": row["offset"] + row["length"], "id": row["id"]}
                  for row in inventory["entries"]])
    cached = {}
    for row in inventory["entries"]:
        facts = []
        verified = {ref["at"]: ref["kind"] for ref in row["refs"]
                    if ref["kind"] in VERIFIED_KINDS or ref["kind"].startswith("table:")}
        seen = set()
        for ref in row["refs"]:
            at = ref["at"]
            if at in seen:
                continue
            seen.add(at)
            kind = verified.get(at, ref["kind"])
            if at in unread:
                fact = {"status": "unread", "kind": "profile", "evidence": [unread[at]]}
            elif kind in VERIFIED_KINDS or kind.startswith("table:"):
                fact = {"status": "reference", "kind": kind, "evidence": [{"method": "inventory-reference-kind"}]}
            else:
                if at not in cached:
                    cached[at] = classify(rom, at, binary=binary, text=text, operands={},
                                          commands=Spans([]), index=index)
                fact = cached[at]
            facts.append({"at": at, **fact})
        row["reference_classification"] = facts
        row["relocation"] = {"status": "held" if any(r["status"] == "unclassified" for r in facts) else "classified",
                             "unclassified": sorted({r["at"] for r in facts if r["status"] == "unclassified"})}
