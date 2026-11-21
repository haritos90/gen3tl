"""Native encounter header, case-dispatch and finite residue-selector evidence."""

from __future__ import annotations

import math

from ..core.thumb import ThumbError, decode
from .foundations import evidence
from .table_core import _issue, _set
from .table_selectors import distribution


def method_cases(rom, descriptor, readers):
    matches = readers.matches.get(descriptor["reader"], ())
    if len(matches) != 1:
        return None
    start = matches[0]
    guard = decode(rom.data, start + descriptor["case_guard"])
    branch = decode(rom.data, guard.address + guard.size)
    move = decode(rom.data, start + descriptor["case_call"] - 2)
    call = decode(rom.data, start + descriptor["case_call"])
    if (guard.mnemonic != "cmp" or guard.immediate != max(descriptor["methods"].values())
            or branch.mnemonic != "b" or branch.condition != "hi"
            or move.mnemonic != "lsl" or move.immediate != 0 or move.rn != guard.rn or move.rd != 0
            or call.mnemonic != "bl" or call.target not in readers.matches.get(descriptor["case_helper"], ())):
        return None
    base, count = call.address + call.size, guard.immediate + 1
    if base + count > len(rom):
        return None
    found, sites, fallbacks = {}, [guard.address, branch.address, move.address, call.address, call.target], set()
    for name, number in descriptor["methods"].items():
        target = base + rom.u8(base + number) * 2
        if not base + count <= target <= min(base + 512, len(rom) - 14):
            return None
        load, compare, finish, fallback, second, done = [decode(rom.data, target + offset)
                                                       for offset in (0, 2, 4, 6, 10, 12)]
        if (load.mnemonic != "ldr" or load.width != 4 or load.rn != 3 or load.rd != 0
                or load.immediate is None or not 0 < load.immediate < 64
                or compare.mnemonic != "cmp" or compare.rn != 0 or compare.immediate != 0
                or finish.mnemonic != "b" or finish.condition != "ne" or fallback.mnemonic != "bl"
                or second.mnemonic != "ldr" or second.rn != 0 or second.rd != 0
                or second.immediate != load.immediate or second.width != 4
                or done.mnemonic != "b" or done.condition is not None or done.target != finish.target):
            return None
        epilogue = decode(rom.data, finish.target)
        if epilogue.mnemonic != "pop" or 15 not in epilogue.registers:
            return None
        found[name] = load.immediate
        fallbacks.add(fallback.target)
        sites.extend((load.address, compare.address, finish.address, fallback.address, second.address, done.address))
    if len(set(found.values())) != len(found) or len(fallbacks) != 1:
        return None
    return found, base, sorted(set(sites))


def selector_rates(rom, descriptor, readers, inputs=None):
    matches = readers.matches.get(descriptor["reader"], ())
    if len(matches) != 1:
        return None
    start = matches[0]
    modulus = decode(rom.data, start + descriptor["modulus"])
    if modulus.mnemonic != "mov" or modulus.rd != 1 or modulus.immediate != 100:
        return None
    try:
        first, rates, sites = distribution(rom.data, start, start + readers.rows[descriptor["reader"]]["length"],
                                            start + descriptor["sample"], descriptor["register"], inputs)
    except ThumbError:
        return None
    return first, rates, sites


def header_count(rom, base, stride, methods, species):
    count = 0
    while count < 4096 and base + (count + 1) * stride <= len(rom):
        at = base + count * stride
        if rom.u8(at) == 255:
            return count
        for method in methods.values():
            pointer = rom.ptr_at(at + method["pointer"])
            if pointer is None:
                if rom.u32(at + method["pointer"]) != 0:
                    return None
                continue
            if pointer + 8 > len(rom):
                return None
            entries = rom.ptr_at(pointer + 4)
            if entries is None or entries + method["slots"] * 4 > len(rom):
                return None
            for number in range(method["slots"]):
                row = entries + number * 4
                if rom.u16(row + 2) >= species:
                    return None
        count += 1
    return None


def run(rom, result, knowledge, readers, bindings):
    descriptor = knowledge.get("encounters")
    species = bindings.get("species")
    if not descriptor or species is None:
        return
    cases = method_cases(rom, descriptor, readers)
    if cases is None:
        _issue(result, "pokedex.encounters.methods", "A full case-helper match, index guard and consistent header-field cases are required.",
               "semantic-reader+bounded-case-table")
        return
    pointers, case_base, case_sites = cases
    rates = {name: selector_rates(rom, row, readers) for name, row in descriptor["selectors"].items() if name != "fishing"}
    fishing = {name: selector_rates(rom, descriptor["selectors"]["fishing"], readers, {0: value})
               for name, value in descriptor["rods"].items()}
    if any(value is None for value in (*rates.values(), *fishing.values())):
        _issue(result, "pokedex.encounters.methods", "Every native slot selector must accept all 100 normalized residues with consecutive outputs.",
               "finite-native-selector")
        return
    methods = {}
    for name, pointer in pointers.items():
        if name == "fishing":
            slots = max(first + len(values) for first, values, _ in fishing.values())
            value = {"pointer": pointer, "slots": slots}
            sites = [site for _, _, rows in fishing.values() for site in rows]
        else:
            first, values, sites = rates["water" if name == "rock_smash" else name]
            if first != 0:
                _issue(result, "pokedex.encounters.methods." + name, "A non-fishing method does not start at slot zero.",
                       "finite-native-selector", first=first)
                continue
            value = {"pointer": pointer, "slots": len(values), "rates": values}
        proof = evidence("Checked byte-case targets supply field offsets; all 100 normalized residues supply nominal slot weights.",
            "full-helper-match+bounded-case-table+finite-native-selector", [case_base], [*case_sites, *sites],
            residues=100, slots=value["slots"], pointer=pointer)
        methods[name] = value
        _set(result, "pokedex.encounters.methods." + name, value, proof)
    if len(methods) != len(pointers):
        return
    for name, (first, values, sites) in fishing.items():
        proof = evidence("The shared native fishing selector maps every normalized residue to this rod's consecutive slot range.",
            "shared-selector+finite-native-residues", sites=sites, residues=100, first=first, slots=len(values))
        _set(result, "pokedex.encounters.fishing." + name, {"first": first, "rates": values}, proof)
    day = readers.load(descriptor["day"])
    if day is None:
        _issue(result, "pokedex.encounters.headers", "The native daytime row reader is missing or ambiguous.", "semantic-reader")
        return
    native = readers.fields(descriptor["day"]["reader"], day["base"])
    strides = {math.gcd(*(value for _, value in reader.indices)) for reader in native if reader.width == 1}
    if len(strides) != 1:
        _issue(result, "pokedex.encounters.headers.stride", "The native daytime row indexing does not yield one stride.",
               "accessor", [day["base"]])
        return
    stride = strides.pop()
    proof = evidence("The daytime row reader computes the actual indexed header stride.", "semantic-reader+affine-stride",
        [day["pool"]], [reader.site for reader in native], stride=stride)
    _set(result, "pokedex.encounters.headers.stride", stride, proof)
    arrays = {"day": day}
    for name, row in descriptor["variants"].items():
        arrays[name] = readers.load(row) if len(readers.matches.get(row["guard"], ())) == 1 else None
    for name, load in arrays.items():
        identifier = "pokedex.encounters.headers." + name
        count = header_count(rom, load["base"], stride, methods, species.count) if load is not None else None
        if count is None:
            _issue(result, identifier, "A verified selector pointer, complete slot rows and final group terminator are required.",
                   "semantic-selector+header-domain+terminator")
            continue
        proof = evidence("Native day/time selectors identify complete header rows ending at the observed group terminator.",
            "semantic-selector+header-domain+terminator", [load["pool"], load["base"]], [load["site"]],
            rows=count, stride=stride, terminator=255)
        _set(result, identifier, {"base": load["base"], "count": count}, proof)
