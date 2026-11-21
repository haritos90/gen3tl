"""Bounded native string writes from fingerprinted handlers and script calls."""

from __future__ import annotations

import hashlib

from ..core.charmap import Charmap, CharmapError
from ..core.rom import ROM_BASE
from ..core.thumb import ThumbError, decode, walk
from ..families.fingerprints import Fingerprint, find
from ..profile import schema
from .accessors import analyze
from .foundations import Result, evidence, fact
from .references import script_operands


def matched_functions(rom, knowledge):
    """Validate local branch destinations after address-independent matching."""
    signatures = tuple(Fingerprint.read(row) for row in knowledge.get("functions", []))
    candidates = find(rom.data, signatures)
    found = {}
    for row in knowledge.get("functions", []):
        matches = []
        for start in candidates[row["name"]]:
            branches = [[i.address - start, i.target - start]
                        for i in walk(rom.data, start, start + row["length"]) if i.mnemonic == "b"]
            if branches == row.get("branches", []):
                matches.append(start)
        if len(matches) == 1:
            found[row["name"]] = matches[0]
    return found


def copy_target(rom, call):
    """Resolve direct copies and single-register branch veneers."""
    jump = decode(rom.data, call.target)
    if jump.mnemonic != "bx" or jump.rm not in range(4):
        return call.target
    value = call.arguments[jump.rm]
    if value.terms or not value.constant & 1 or not rom.is_ptr(value.constant):
        return None
    return (value.constant - ROM_BASE) & ~1


def buffers(rom, profile, knowledge, matches):
    """Locate buffer slots by their indexed use in bufferstring."""
    rule = knowledge["buffers"]
    start = matches.get(rule["handler"])
    table = profile.get("script", {}).get("command_table")
    if start is None or table is None or rom.ptr_at(table + rule["opcode"] * 4) != start + 1:
        raise ValueError("The bufferstring handler does not match the discovered command table.")
    analysis = analyze(rom.data, start, start + rule["copy"] + 4)
    reads = [a for a in analysis.accessors if a.site == start + rule["read"] and a.width == 4
             and len(a.indices) == 1 and a.indices[0][1] == 4]
    calls = [call for call in analysis.calls if call.site == start + rule["copy"]]
    if len(reads) != 1 or len(calls) != 1 or copy_target(rom, calls[0]) != matches.get("copy"):
        raise ValueError("The bufferstring indexed read and copy call are not proven.")
    read, call = reads[0], calls[0]
    if read.site not in call.arguments[0].sites:
        raise ValueError("The indexed buffer pointer does not reach the copy destination.")
    base = read.base + read.field_offset
    values = [rom.u32(base + slot * 4) for slot in range(rule["slots"])]
    if len(set(values)) != len(values) or any(not 0x02000000 <= value < 0x02040000 for value in values):
        raise ValueError("String-buffer slots are not distinct work-RAM pointers.")
    return {value: slot for slot, value in enumerate(values)}, [base, start, read.site, call.site]


def special_table(rom, profile, knowledge, matches):
    rule = knowledge["specials"]
    start = matches.get(rule["handler"])
    table = profile.get("script", {}).get("command_table")
    if start is None or table is None or rom.ptr_at(table + rule["opcode"] * 4) != start + 1:
        return None
    loads = [decode(rom.data, start + rule[key]) for key in ("base_load", "end_load")]
    if any(row.literal is None for row in loads):
        return None
    base, end = [rom.ptr_at(row.literal) for row in loads]
    if base is None or end is None or not base < end <= len(rom) or (end - base) % 4:
        return None
    return base, end


def text(rom, offset):
    """Validate a bounded terminated text source."""
    if offset is None:
        raise ValueError("The copied source is not a ROM pointer.")
    try:
        value, end = Charmap().decode(rom.data, offset, limit=512)
    except CharmapError as exc:
        raise ValueError("The copied source is not bounded engine text.") from exc
    if not value or "{0x" in value or end <= offset or rom.data[end - 1] != 255:
        raise ValueError("The copied source is not bounded engine text.")
    return f"s{offset:07X}"


def literal_text(rom, start, offset):
    row = decode(rom.data, start + offset)
    if row.literal is None or row.rd != 1:
        raise ValueError("The fallback source is not loaded into the copy argument.")
    return text(rom, rom.ptr_at(row.literal))


def derive(rom, rule, matches, slots):
    """Read an indexed copy, its selector bound and literal fallback."""
    start = matches.get(rule["handler"])
    if start is None:
        raise ValueError("The native handler fingerprint or local control flow is missing or ambiguous.")
    lo, hi = rule["analysis"]
    analysis = analyze(rom.data, start + lo, start + hi)
    reads = [a for a in analysis.accessors if a.site == start + rule["read"]
             and a.width == 4 and len(a.indices) == 1 and a.indices[0][1] == 4]
    calls = [c for c in analysis.calls if c.site == start + rule["copy"]
             and not c.arguments[0].terms and c.arguments[0].constant in slots
             and copy_target(rom, c) == matches.get("copy")]
    linked = [(read, call) for read in reads for call in calls if read.site in call.arguments[1].sites]
    if len(linked) != 1:
        raise ValueError("A unique indexed text pointer must reach a known string-buffer copy.")
    read, call = linked[0]
    base = read.base + read.field_offset
    if rule["domain"] == "boolean":
        sub = decode(rom.data, start + rule["boolean"])
        subtract = decode(rom.data, sub.address + sub.size)
        if (sub.mnemonic != "sub" or sub.immediate != 1 or subtract.mnemonic != "sbc"
                or subtract.rn != sub.rn or subtract.rm != sub.rd or subtract.rd != sub.rn):
            raise ValueError("The carry-dependent index does not prove a zero/nonzero selector.")
        count = 2
    else:
        bound = decode(rom.data, start + rule["bound"])
        if bound.mnemonic != "cmp" or bound.immediate is None:
            raise ValueError("The source selector has no immediate comparison bound.")
        count = bound.immediate + (rule["domain"] == "guard")
    if not 0 < count <= 4096 or not 0 <= base <= len(rom) - count * 4:
        raise ValueError("The source table bound leaves the ROM.")
    for index in range(count):
        text(rom, rom.ptr_at(base + index * 4))
    stored = count
    if rule.get("storage") == "pointer-run":
        while stored < 4096 and base + stored * 4 <= len(rom) - 4:
            try:
                text(rom, rom.ptr_at(base + stored * 4))
            except ValueError:
                break
            stored += 1
        if stored == 4096:
            raise ValueError("The physical pointer run has no bounded end.")
    value = {"name": rule["name"], "address": start + 1, "slot": slots[call.arguments[0].constant],
             "base": base, "stride": 4, "count": stored}
    if stored > count:
        value["exclude"] = list(range(count, stored))
    value["variants"] = [[literal_text(rom, start, rule["fallback"])]] if "fallback" in rule else []
    if "wrapper" in rule:
        wrapper = matches.get(rule["wrapper"])
        branch = decode(rom.data, wrapper + rule["wrapper_call"]) if wrapper is not None else None
        if branch is None or branch.mnemonic != "bl" or branch.target != start:
            raise ValueError("The script wrapper does not call the proven fill handler.")
        value["address"] = wrapper + 1
    sites = sorted({start, read.site, call.site, *(i.site for i in analysis.literals)})
    return value, sites


def discover(rom, profile, knowledge):
    """Discover bounded fills without consulting context declarations."""
    result = Result({}, [], {"native_fills": [], "shortfalls": []})
    if not knowledge:
        return result
    matches = matched_functions(rom, knowledge)
    prefixed = prefixes(rom, knowledge, matches)
    result.metadata.update(prefixed.metadata)
    result.issues.extend(prefixed.issues)
    for row in knowledge.get("shortfalls", []):
        result.metadata["shortfalls"].append(dict(row))
        result.issues.append(fact(row["id"], schema.kind_of(row["id"]), None,
            evidence(row["reason"], "family-semantic-shortfall"), status="unresolved"))
    result.metadata["runtime_observations"] = "overlay-only; merged after static reach projection"
    try:
        slots, buffer_sites = buffers(rom, profile, knowledge, matches)
    except (ValueError, ThumbError) as exc:
        result.metadata["shortfalls"].append({"id": "context.native_fill", "reason": str(exc)})
        result.issues.append(fact("context.native_fill", "context", None,
            evidence(str(exc), "bufferstring-indexed-destination"), status="unresolved"))
        return result
    result.metadata["buffers"] = [{"slot": slot, "address": address} for address, slot in slots.items()]
    _, commands = script_operands(rom, profile)
    natives, specials = {}, {}
    for command in commands:
        pc, op = command["start"], command["opcode"]
        if op in (0x23, 0x24):
            natives.setdefault(rom.ptr_at(pc + 1), []).append(pc)
        if op in (0x25, 0x26):
            offset = 1 if op == 0x25 else 3
            specials.setdefault(rom.u16(pc + offset), []).append(pc)
    dispatch = special_table(rom, profile, knowledge, matches)
    for rule in knowledge.get("native_fills", []):
        identifier = f"context.native_fill[name={rule['name']}]"
        try:
            value, sites = derive(rom, rule, matches, slots)
            if rule.get("special"):
                if dispatch is None:
                    raise ValueError("The bounded special dispatch table is unavailable.")
                base, end = dispatch
                found = [(at - base) // 4 for at in range(base, end, 4)
                         if rom.ptr_at(at) == value["address"]]
                if len(found) != 1:
                    raise ValueError("The native routine has no unique special dispatch slot.")
                value = {("special" if key == "address" else key):
                         (found[0] if key == "address" else item) for key, item in value.items()}
                callers = specials.get(found[0], [])
            else:
                callers = natives.get(value["address"], [])
            if not callers:
                raise ValueError("No discovered map-rooted script calls this native routine.")
            result.metadata["native_fills"].append({"name": rule["name"], "value": value,
                                                     "code_sites": sites, "script_calls": callers})
            if rule.get("shortfall"):
                raise ValueError(rule["shortfall"])
            proof = evidence("Fingerprint-matched control flow bounds the indexed copy and resolves its destination through bufferstring; "
                             "the physical pointer run ends at the first invalid text pointer when requested. "
                             "Saved registers follow the procedure-call convention; callees are assumed to write string buffers "
                             "only through pointer arguments, with other pointers disjoint.",
                             "native-handler+indexed-copy+script-call", [value["base"], *buffer_sites],
                             [*sites, *callers], rows=value["count"], excluded=len(value.get("exclude", [])),
                             variants=len(value["variants"]), callers=len(callers))
            item = fact(identifier, "context", value, proof, tier="shape")
            item.section, item.source = "context.native_fill", "native-context/1.0.0"
            result.data.setdefault("context", {}).setdefault("native_fill", []).append(value)
            result.facts.append(item)
        except (ValueError, ThumbError) as exc:
            reason = str(exc)
            if rule.get("shortfall") and reason != rule["shortfall"]:
                reason += " " + rule["shortfall"]
            result.metadata["shortfalls"].append({"id": identifier, "reason": reason})
            result.issues.append(fact(identifier, "context", None,
                evidence(reason, "native-handler+indexed-copy+script-call"), status="unresolved"))
    return result

def prefixes(rom, knowledge, matches):
    """Report prefix joins separately from unresolved presentation limits."""
    result = Result({}, [], {"prefixes": [], "shortfalls": []})
    for rule in knowledge.get("text_prefixes", []):
        identifier = f"text_prefixes[name={rule['name']}]"
        try:
            start = matches.get(rule["handler"])
            if start is None:
                raise ValueError("The prefix formatter fingerprint or local control flow is missing or ambiguous.")
            analysis = analyze(rom.data, start, start + rule["end"])
            copies = [call for call in analysis.calls if call.site == start + rule["copy"]
                      and copy_target(rom, call) == matches.get("copy")]
            joins = [call for call in analysis.calls if call.site == start + rule["append"]
                     and copy_target(rom, call) == matches.get("append")]
            if (len(copies) != 1 or len(joins) != 1
                    or copies[0].arguments[0].key != joins[0].arguments[0].key
                    or joins[0].arguments[0].terms or joins[0].arguments[1].terms
                    or len(joins[0].arguments[1].origins) != 1):
                raise ValueError("The prefix copy and append do not share a proven destination.")
            join = joins[0]
            source = join.arguments[1].constant - ROM_BASE
            text(rom, source)
            _, end = Charmap().decode(rom.data, source, limit=512)
            value = {"name": rule["name"], "pointer": join.arguments[1].origins[0].pool,
                     "source": {"offset": source, "size": end - source,
                                "sha256": hashlib.sha256(rom.data[source:end]).hexdigest()}}
            proof = evidence("A literal prefix reaches StringAppend after StringCopy initializes the same buffer; "
                             "the formatter continuation and presentation limits remain unproven.",
                             "native-formatter+shared-destination", [value["pointer"], source],
                             [copies[0].site, join.site], prefix_bytes=end - source)
            item = fact(identifier, "string", value, proof, tier="shape", status="candidate")
            item.source = "native-context/1.0.0"
            result.issues.append(item)
            result.metadata["prefixes"].append({"value": value, "destination": join.arguments[0].constant,
                                                "code_sites": list(proof.code_sites)})
            raise ValueError(rule["shortfall"])
        except (ValueError, ThumbError) as exc:
            result.metadata["shortfalls"].append({"id": identifier, "reason": str(exc)})
            result.issues.append(fact(identifier, "string", None,
                evidence(str(exc), "native-formatter+shared-destination"), status="unresolved"))
    return result
