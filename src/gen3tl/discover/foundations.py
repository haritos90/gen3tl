"""ROM-derived identity, family, codec and Latin font facts."""

from __future__ import annotations

import hashlib
import json
import struct
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .. import TOOLKIT_ROOT
from ..core.font import BACKGROUND, OUTSIDE, FontTable, encode_glyph, glyph_size
from ..core.charmap import BASE_CHARS, CTRL_CODES, NAMED_BYTES, PREFIXED_ARG, Charmap
from ..core.rom import ROM_BASE, Rom
from ..core.thumb import ThumbError, decode
from ..families.fingerprints import Fingerprint, find
from ..families.knowledge import load as load_family, load_script
from ..families.windows import Windows, load as load_windows
from ..profile import schema
from .accessors import analyze, literal_loads
from .inventory import slot_usage
from . import scripts as script_discovery

SOURCE = "foundations/1.0.0"


@dataclass
class Family:
    name: str
    engine: dict
    anchors: dict
    fingerprints: tuple[Fingerprint, ...]
    formats: dict[str, str]
    paths: tuple[Path, ...]
    readers: dict = field(default_factory=dict)
    scripts: dict = field(default_factory=dict)
    script_fingerprints: tuple[Fingerprint, ...] = ()
    maps: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    windows: Windows | None = None


@dataclass
class Result:
    data: dict
    facts: list[schema.Fact]
    metadata: dict
    issues: list[schema.Fact] = field(default_factory=list)


def evidence(sentence, method, anchors=(), sites=(), **counts):
    return schema.Evidence(sentence, method, tuple(sorted(set(anchors))), tuple(sorted(set(sites))),
                           tuple(sorted(counts.items())))


def fact(identifier, kind, value, proof, tier="crossref", status="verified"):
    return schema.Fact(identifier, identifier.split(".")[0], kind, value, tier=None if status == "unresolved" else tier,
                       status=status, source=SOURCE, evidence=proof)


def families(root: Path | None = None) -> tuple[Family, ...]:
    directory = Path(root) if root is not None else TOOLKIT_ROOT / "families"
    found = []
    for path in sorted(directory.glob("*/functions.json")):
        with path.open(encoding="utf-8") as stream:
            functions = json.load(stream)
        anchors_path = path.with_name("anchors.toml")
        with anchors_path.open("rb") as stream:
            anchors = tomllib.load(stream)
        name = path.parent.name
        if (functions.get("schema") != 1 or anchors.get("schema") != 1
                or functions.get("family") != name or anchors.get("family") != name):
            raise ValueError(f"{path.parent}: invalid discovery knowledge identity")
        engine = load_family(name, directory)
        readers_path = path.with_name("table-readers.json")
        readers = {}
        paths = (engine.path, path, anchors_path)
        if readers_path.is_file():
            readers = json.loads(readers_path.read_text(encoding="utf-8"))
            if readers.get("schema") != 1 or readers.get("family") != name:
                raise ValueError(f"{readers_path}: invalid table-reader knowledge identity")
            reader_functions = tuple(Fingerprint.read(row) for row in readers.get("functions", []))
            if not reader_functions or len({row.name for row in reader_functions}) != len(reader_functions):
                raise ValueError(f"{readers_path}: missing or duplicate table-reader fingerprints")
            for row in readers["functions"]:
                window = row.get("analysis")
                if window is not None and (not isinstance(window, dict) or set(window) != {"start", "end"}
                        or any(type(value) is not int or value % 2 for value in window.values())
                        or not 0 <= window["start"] < window["end"] <= row["length"]):
                    raise ValueError(f"{readers_path}: analysis window leaves its verified function")
            paths = (*paths, readers_path)
        script = load_script(name, directory)
        signatures = tuple(Fingerprint.read(row) for row in functions["functions"])
        script_signatures = tuple(Fingerprint.read(row) for row in script.discovery["fingerprints"])
        if (not signatures or len({f.name for f in signatures}) != len(signatures)
                or len({f.name for f in script_signatures}) != len(script_signatures)):
            raise ValueError(f"{path}: missing or duplicate function fingerprints")
        formats = {row["name"]: row["format"] for row in functions["functions"] if "format" in row}
        if any(value not in ("8x16", "16x16") for value in formats.values()):
            raise ValueError(f"{path}: unsupported renderer format")
        maps_path = path.with_name("maps.json")
        maps = {}
        if maps_path.is_file():
            maps = json.loads(maps_path.read_text(encoding="utf-8"))
            if maps.get("schema") != 1 or maps.get("family") != name:
                raise ValueError(f"{maps_path}: invalid map knowledge identity")
            paths = (*paths, maps_path)
        context_path = path.with_name("context.json")
        context = {}
        if context_path.is_file():
            context = json.loads(context_path.read_text(encoding="utf-8"))
            if context.get("schema") != 1 or context.get("family") != name:
                raise ValueError(f"{context_path}: invalid context knowledge identity")
            paths = (*paths, context_path)
        windows = load_windows(path.parent)
        found.append(Family(name, engine.data, anchors, signatures, formats,
                            (*paths, script.path, *windows.paths), readers, script.discovery,
                            script_signatures, maps, context, windows))
    return tuple(found)


def identify(rom: Rom) -> dict:
    data = rom.data
    if len(data) < 0xC0:
        raise ValueError("ROM is too short for a cartridge header")
    text = lambda start, length: bytes(data[start:start + length]).rstrip(b"\0").decode("ascii", errors="replace")
    return {"sha1": rom.sha1(), "size": len(data), "title": text(0xA0, 12), "code": text(0xAC, 4),
            "maker": text(0xB0, 2), "revision": data[0xBC], "fixed_byte_valid": data[0xB2] == 0x96,
            "header_checksum_valid": data[0xBD] == (-sum(data[0xA0:0xBD]) - 0x19) & 255}


def classify(rom: Rom, family: Family, identity: dict) -> tuple[dict, dict[str, tuple[int, ...]]]:
    matches = find(rom.data, family.fingerprints)
    config = family.anchors["identity"]
    header = config["header_offset"]
    enough = header + max(config["pointer_offsets"]) + 4 <= len(rom)
    pointers = [header + offset for offset in config["pointer_offsets"]
                if enough and rom.ptr_at(header + offset) is not None]
    header_ok = (enough and rom.u32(header) == config["version"]
                 and rom.u32(header + 4) == config["language"]
                 and bytes(rom.data[header + 8:header + 40]).rstrip(b"\0") == config["name"].encode()
                 and len(pointers) >= config["minimum_pointers"])
    hit = sum(bool(matches[f.name]) for f in family.fingerprints)
    categories = sorted({f.category for f in family.fingerprints if matches[f.name]})
    rules = family.anchors["classification"]
    ratio = hit / len(family.fingerprints)
    selected = (identity["fixed_byte_valid"] and identity["header_checksum_valid"]
                and identity["code"] == config["game_code"] and header_ok
                and ratio >= rules["minimum_ratio"] and set(rules["required_categories"]) <= set(categories))
    return {"name": family.name, "selected": selected, "ratio": ratio, "matched": hit,
            "total": len(family.fingerprints), "categories": categories, "header_valid": header_ok,
            "header_pointer_sites": pointers, "known_retail_hash": identity["sha1"] in config["retail_sha1"],
            "functions": {name: list(addresses) for name, addresses in matches.items()}}, matches


def codec(rom: Rom, family: Family) -> dict:
    config = family.anchors["codec"]
    cm = Charmap()
    scores = []
    for name in (config["candidate"], "ascii"):
        plain, controls = [], []
        for text, output in [(text, plain) for text in config["anchors"]] + [
                (text, controls) for text in config["control_anchors"]]:
            if name == "ascii":
                if text.startswith("{"):
                    continue
                encoded = text.encode("ascii") + b"\xff"
            else:
                encoded = cm.encode(text)
            start = 0
            while (position := rom.data.find(encoded, start)) >= 0:
                if name == "ascii" or cm.decode(rom.data, position)[0] == text:
                    output.append({"text": text, "at": position})
                start = position + len(encoded)
        plain_count = len({row["text"] for row in plain})
        control_count = len({row["text"] for row in controls})
        scores.append({"candidate": name, "anchors": plain, "control_anchors": controls,
                       "matched_anchors": plain_count, "matched_controls": control_count,
                       "score": plain_count + control_count})
    scores.sort(key=lambda row: (-row["score"], row["candidate"]))
    best = scores[0]
    selected = (best["candidate"] == config["candidate"] and best["matched_anchors"] >= config["minimum_anchors"]
                and best["matched_controls"] >= config["minimum_controls"] and best["score"] > scores[1]["score"])
    return {"selected": selected, "candidate": best["candidate"] if selected else "unknown",
            "scores": scores, "control_contract": config["controls"],
            "control_codes": [{"code": code, "name": name, "arguments": size}
                              for code, (name, size) in sorted(CTRL_CODES.items())],
            "limit": "Known family contract selected by anchors; unobserved/custom control semantics are not independently inferred."}


def _latin_literals(rom: Rom, start: int, length: int, register: int, value: int):
    registers = [None] * 16
    registers[register] = value
    pending, seen, visited = [(start, tuple(registers), None)], set(), set()
    end = start + length
    while pending:
        address, previous, zero = pending.pop()
        if not start <= address < end or (address, previous, zero) in seen:
            continue
        if len(seen) >= 4096:
            raise ThumbError("font branch state limit reached")
        seen.add((address, previous, zero))
        instruction = decode(rom.data, address)
        visited.add(address)
        registers = list(previous)
        op, rd = instruction.mnemonic, instruction.rd
        operand = registers[instruction.rm] if instruction.rm is not None else instruction.immediate
        left = registers[instruction.rn] if instruction.rn is not None else None
        if op == "cmp":
            zero = None if left is None or operand is None else left == operand
        elif op == "mov":
            registers[rd] = operand
            if instruction.raw[0] & 0xFC00 != 0x4400:
                zero = None if operand is None else operand == 0
        elif op in ("add", "sub"):
            result = None if left is None or operand is None else (left + operand * (-1 if op == "sub" else 1)) & 0xFFFFFFFF
            registers[rd], zero = result, None if result is None else result == 0
        else:
            for destination in instruction.written:
                registers[destination] = None
            if op in ("lsl", "lsr", "asr", "and", "eor", "adc", "sbc", "ror", "neg", "cmn", "orr", "mul", "bic", "mvn"):
                zero = None
        successors = instruction.successors
        if op == "b" and instruction.condition in ("eq", "ne") and zero is not None:
            take = zero if instruction.condition == "eq" else not zero
            successors = (instruction.target if take else address + instruction.size,)
        for target in successors:
            pending.append((target, tuple(registers), zero))
    return tuple(item for item in literal_loads(rom.data, start, end) if item.site in visited)


def fonts(rom: Rom, family: Family, matches: dict[str, tuple[int, ...]]) -> tuple[list[dict], list[schema.Fact], list[schema.Fact]]:
    config = family.anchors["fonts"]
    required_codes = {code for declaration in family.engine["font"] for code in declaration["codes"]}
    widths = {address: signature for signature in family.fingerprints if signature.category == "width"
              for address in matches[signature.name]}
    ids = config["dispatch_ids"]
    candidates = set()
    for function in widths:
        needle, at = struct.pack("<I", ROM_BASE + function + 1), 0
        while (at := rom.data.find(needle, at)) >= 0:
            row = at - config["function_offset"]
            if row >= 0 and row % 4 == 0:
                index = rom.u32(row + config["id_offset"])
                if index in ids:
                    base = row - index * config["record_size"]
                    if base >= 0 and base + len(ids) * config["record_size"] <= len(rom):
                        values = [(rom.u32(base + i * config["record_size"] + config["id_offset"]),
                                   rom.u32(base + i * config["record_size"] + config["function_offset"])) for i in ids]
                        if (all(code == i and pointer & 1 and rom.is_ptr(pointer) for i, (code, pointer) in zip(ids, values))
                                and all((pointer - ROM_BASE) & ~1 in widths for code, pointer in values if code in required_codes)):
                            candidates.add(base)
            at += 4
    issues, result, proofs = [], [], []
    if len(candidates) != 1:
        proof = evidence("A unique printer/width-function dispatch is required.", "printer-dispatch",
                         candidates, matched_tables=len(candidates))
        return [], [], [fact("font", "font", None, proof, status="unresolved")]
    dispatch, = candidates
    renderers = [(address, signature, literal_loads(rom.data, address, address + signature.length))
                 for signature in family.fingerprints if signature.category == "renderer"
                 for address in matches[signature.name]]
    for declaration in family.engine["font"]:
        name, count = declaration["name"], declaration["count"]
        fmt = declaration.get("format", "16x16")
        width_sets, width_evidence = [], []
        for code in declaration["codes"]:
            function = (rom.u32(dispatch + code * config["record_size"] + config["function_offset"]) - ROM_BASE) & ~1
            signature = widths[function]
            literals = _latin_literals(rom, function, signature.length, config["latin_register"], config["latin_value"])
            accessors = analyze(rom.data, function, function + signature.length).accessors
            selected = {item.value - ROM_BASE for item in literals if rom.is_ptr(item.value)
                        and any(a.base == item.value - ROM_BASE and a.width == 1 and a.field_offset == 0
                                and len(a.indices) == 1 and a.indices[0][1] == 1 for a in accessors)}
            width_sets.append(selected)
            width_evidence.extend(literals)
        shared = set.intersection(*width_sets)
        pairs = {}
        for width in shared:
            for address, signature, literals in renderers:
                if family.formats.get(signature.name) != fmt:
                    continue
                for glyph in literals:
                    for advance in literals:
                        if advance.value - ROM_BASE != width or advance.value - glyph.value != count * glyph_size(fmt):
                            continue
                        table = FontTable(name, glyph.value - ROM_BASE, width, count, fmt=fmt)
                        if table.glyphs < 0 or width + count > len(rom):
                            continue
                        if any(table.width(rom, i) > (8 if fmt == "8x16" else 16) for i in range(count)):
                            continue
                        letters = [byte for byte, char in BASE_CHARS.items() if char.isascii() and char.isalpha()]
                        if any(i >= count or not table.width(rom, i) or
                               all(v in (BACKGROUND, OUTSIDE) for row in table.glyph(rom, i) for v in row) for i in letters):
                            continue
                        if any(encode_glyph(table.glyph(rom, i), fmt) != table.glyph_bytes(rom, i) for i in range(count)):
                            continue
                        pairs.setdefault((table.glyphs, width), []).append((address, glyph, advance))
        if len(pairs) != 1:
            issues.append(fact(f"font[name={name}]", "font", None,
                               evidence("A unique Latin glyph/width pair with valid glyphs is required.",
                                        "renderer-literal-pools", candidates=len(pairs)), status="unresolved"))
            continue
        (glyphs, width), = pairs
        row = {"name": name, "glyphs": glyphs, "widths": width, "count": count, "codes": list(declaration["codes"])}
        if fmt != "16x16":
            row["format"] = fmt
        anchors = [glyphs, width, dispatch]
        sites = [item.site for item in width_evidence]
        for address, glyph, advance in pairs[glyphs, width]:
            anchors.extend((glyph.pool, advance.pool))
            sites.extend((address, glyph.site, advance.site))
        proof = evidence("Printer IDs, the Latin branch and renderer pools agree; every glyph round-trips.",
                         "printer-dispatch+latin-branch+renderer-pools", anchors, sites,
                         glyphs=count, round_trips=count, printer_ids=len(declaration["codes"]), candidates=1)
        result.append(row)
        proofs.append(fact(f"font[name={name}]", "font", row, proof))
    return result, proofs, issues


def free_slots(rom: Rom, discovered: list[dict], inventory: dict | None, expected_count: int | None = None) -> dict:
    if inventory is None:
        return {"status": "unresolved", "scope": "No string inventory supplied; whole-ROM string discovery is pending."}
    if inventory.get("rom_sha1") != rom.sha1() or not isinstance(inventory.get("entries"), list):
        raise ValueError("string inventory does not identify this ROM")
    if expected_count is None or len(discovered) != expected_count:
        return {"status": "unresolved", "scope": "Every required font must be verified for the slot check."}
    from ..catalog.source import line, row
    from .verification import decode_back
    for entry in inventory["entries"]:
        try:
            valid = (isinstance(entry["text"], str) and isinstance(entry["id"], str)
                     and type(entry["offset"]) is int and type(entry["length"]) is int
                     and 0 <= entry["offset"] < entry["offset"] + entry["length"] <= len(rom)
                     and line(rom.data, row(entry), Charmap()) == entry["text"])
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ValueError("invalid string-inventory source row") from exc
        if not valid:
            raise ValueError(f"string-inventory source differs from ROM: {entry.get('id', 'unknown')}")
        decode_back(rom, entry["offset"], entry["length"],
                    terminated=row(entry)[3] != "unterminated", mode=entry["mode"])
    candidates = {byte for byte in range(1, min(PREFIXED_ARG)) if byte not in BASE_CHARS and byte not in NAMED_BYTES}
    used = {byte for _, byte in slot_usage(inventory, candidates, Charmap(), unverified=True)}
    tables = FontTable.from_profile({"font": discovered})
    blank = {byte for byte in candidates if all(all(value in (BACKGROUND, OUTSIDE)
                                                   for row in table.glyph(rom, byte) for value in row) for table in tables)}
    digest = hashlib.sha256(json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"status": "candidate", "scope": "Provided ROM-matched inventory only, including unverified entries; not whole-ROM coverage.",
            "inventory_content_sha256": digest, "inventory_entries": len(inventory["entries"]),
            "candidates": sorted(candidates), "used": sorted(used), "blank": sorted(blank), "free": sorted(blank - used),
            "method": "existing-slot-usage+all-font-blank-pixels"}


def _slots(result: Result, rom: Rom, discovered: list[dict], inventory: dict | None, expected_count=None):
    slots = free_slots(rom, discovered, inventory, expected_count)
    result.metadata["free_slots"] = slots
    proof = evidence(slots["scope"], slots.get("method", "inventory-availability"),
                     [f["glyphs"] for f in discovered], fonts=len(discovered),
                     inventory_entries=slots.get("inventory_entries", 0), slots=len(slots.get("free", [])))
    result.issues.append(fact("font_slots", "font", slots.get("free"), proof, "shape", slots["status"]))


def run(rom: Rom, knowledge: tuple[Family, ...] | None = None, inventory: dict | None = None,
        script_roots=None, script_occurrences=()) -> Result:
    identity = identify(rom)
    if inventory is not None and (not isinstance(inventory, dict) or inventory.get("rom_sha1") != identity["sha1"]
                                   or not isinstance(inventory.get("entries"), list)):
        raise ValueError("string inventory does not identify this ROM")
    data = {"rom": {"sha1": identity["sha1"], "size": identity["size"]}}
    proofs = [fact("rom.sha1", "identity", identity["sha1"], evidence("SHA-1 covers every input byte.", "whole-ROM-hash", bytes=len(rom)), "shape"),
              fact("rom.size", "identity", identity["size"], evidence("The input length is measured directly.", "file-length", bytes=len(rom)), "shape")]
    result = Result(data, proofs, {"schema": 1, "identity": identity, "families": []})
    choices = []
    for family in families() if knowledge is None else knowledge:
        classification, matches = classify(rom, family, identity)
        result.metadata["families"].append(classification)
        if classification["selected"]:
            choices.append((family, classification, matches))
    if len(choices) != 1:
        result.issues.append(fact("engine.family", "family", None,
                                  evidence("One supported family must meet every required witness and confidence bound.",
                                           "family-fingerprint-score", selected_families=len(choices)), status="unresolved"))
        _slots(result, rom, [], None)
        return result
    family, classification, matches = choices[0]
    data["family"] = {"name": family.name}
    data["engine"] = {"family": family.name}
    sites = [at for found in matches.values() for at in found]
    proofs.append(fact("engine.family", "family", family.name,
                       evidence("Header structure and function fingerprints include the required engine-extension witness.",
                                "header+semantic-fingerprint-score", classification["header_pointer_sites"], sites,
                                matched=classification["matched"], total=classification["total"])))
    selected = codec(rom, family)
    result.metadata["codec"] = selected
    if not selected["selected"]:
        result.issues.append(fact("rom.engine", "string", None, evidence("No unique supported codec contract passed anchor scoring.",
                                                                                 "codec-anchor-score"), status="unresolved"))
        _slots(result, rom, [], None)
        return result
    data["rom"]["engine"] = family.engine["rom"]["engine"]
    best = selected["scores"][0]
    proofs.append(fact("rom.engine", "string", data["rom"]["engine"],
                       evidence("The known Western codec/control contract wins the independent anchor score.", "codec-anchor-score",
                                [a["at"] for a in best["anchors"] + best["control_anchors"]],
                                anchors_matched=best["matched_anchors"], controls_matched=best["matched_controls"],
                                contract_codes=len(selected["control_codes"]))))
    rows, font_proofs, issues = fonts(rom, family, matches)
    if rows:
        data["font"] = rows
    proofs.extend(font_proofs)
    result.issues.extend(issues)
    discovered_scripts = script_discovery.discover(rom, family, matches)
    script_crossref = None
    if script_roots is not None:
        defaults = family.scripts["defaults"]
        text = dict(defaults["text_ptr_at"], **family.scripts["text_offsets"])
        code = dict(defaults["code_ptr_at"], **family.scripts.get("code_offsets", {}))
        script_crossref = script_discovery.cross_reference(
            rom, discovered_scripts.handlers, script_roots, end_ops=set(defaults["end_ops"]),
            code_offsets={int(key): value for key, value in code.items()},
            text_offsets={int(key): value for key, value in text.items()},
            occurrences=script_occurrences, rules=family.scripts.get("validation"))
    if script_crossref is not None:
        discovered_scripts.issues = [issue for issue in discovered_scripts.issues if "opcode" not in issue]
        for handler in discovered_scripts.handlers:
            if handler.status in ("variable", "unresolved"):
                discovered_scripts.issues.append({"id": f"script.command[{handler.opcode}]",
                    "status": "candidate" if handler.status == "variable" else "unresolved",
                    "opcode": handler.opcode, "handler": handler.entry,
                    "lengths": list(handler.lengths), "reasons": list(handler.reasons)})
    result.metadata["scripts"] = {key: value for key, value in {
        "table": discovered_scripts.table, "end": discovered_scripts.end,
        "initializer": discovered_scripts.initializer, "dispatcher": discovered_scripts.dispatcher,
        "table_verified": discovered_scripts.table_verified,
        "readers_verified": discovered_scripts.readers_verified,
        "handlers": [{"opcode": handler.opcode, "entry": handler.entry, "status": handler.status,
                      "lengths": list(handler.lengths), "tier": handler.tier,
                      "assumptions": list(handler.assumptions),
                      "assumption_sites": list(handler.assumption_sites),
                      "paths": list(handler.paths), "redirects": list(handler.redirects),
                      "conditions": list(handler.conditions), "advances": list(handler.advances),
                      "calls": list(handler.calls),
                      "crossref": handler.crossref,
                      "reads": [{"site": read.site, "offset": read.offset, "width": read.width,
                                 **({"target": read.target} if read.target is not None else {})}
                                for read in handler.reads],
                      "code_sites": list(handler.code_sites), "reasons": list(handler.reasons)}
                     for handler in discovered_scripts.handlers]}.items() if value is not None}
    if script_crossref is not None:
        result.metadata["scripts"]["crossref"] = script_crossref
    if discovered_scripts.table_verified:
        data["script"] = {"command_table": discovered_scripts.table,
                          "command_table_end": discovered_scripts.end}
        counts = {status: sum(handler.status == status for handler in discovered_scripts.handlers)
                  for status in ("fixed", "shape", "crossref", "variable", "unresolved")}
        proofs.append(fact("script", "script-command", data["script"],
                           evidence("Initializer literals and dispatcher bounds identify the live table; handler formats retain their individual trust statuses.",
                                    "dispatch-fingerprints+handler-stream-analysis",
                                    [discovered_scripts.table, discovered_scripts.end], discovered_scripts.code_sites,
                                    commands=len(discovered_scripts.handlers), **counts)))
    for issue in discovered_scripts.issues:
        opcode = issue.get("opcode")
        handler = discovered_scripts.handlers[opcode] if opcode is not None else None
        reason = issue.get("reason", "; ".join(issue.get("reasons", [])))
        result.issues.append(fact(issue["id"], "script-command", None,
                                  evidence(reason, "dispatch-fingerprints+handler-stream-analysis",
                                           [issue["handler"]] if handler is not None else [],
                                           handler.code_sites if handler is not None else (),
                                           **({"opcode": opcode} if opcode is not None else {})),
                                  status=issue["status"]))
    expected = family.scripts["fixed_lengths"]
    defaults = family.scripts["defaults"]["command_lengths"]
    comparisons = []
    for handler in discovered_scripts.handlers:
        key = str(handler.opcode)
        wanted = defaults.get(key, expected.get(key))
        actual = handler.lengths[0] if len(handler.lengths) == 1 else None
        if handler.status not in ("fixed", "shape", "crossref") or actual != wanted:
            comparisons.append({"opcode": handler.opcode, "status": handler.status,
                                "discovered_lengths": list(handler.lengths),
                                **({"compatibility_length": wanted} if wanted is not None else {}),
                                "reasons": list(handler.reasons)})
    result.metadata["scripts"]["comparison"] = comparisons
    _slots(result, rom, rows, inventory, len(family.engine["font"]))
    from . import save as save_stage
    from . import tables as table_stage
    from .table_readers import Readers
    readers = Readers(rom, family.readers) if family.readers else None
    table_result = table_stage.run(rom, family, readers)
    for key, value in table_result.data.items():
        if key not in data:
            data[key] = value
        elif isinstance(data[key], dict) and isinstance(value, dict):
            data[key] = {**value, **data[key]}
    proofs.extend(table_result.facts)
    result.issues.extend(table_result.issues)
    result.metadata["tables"] = table_result.metadata
    save_result = save_stage.run(rom, family, table_result.data, readers)
    if save_result.data:
        data["save"] = save_result.data["save"]
    proofs.extend(save_result.facts)
    result.issues.extend(save_result.issues)
    result.metadata["save"] = save_result.metadata
    if family.maps:
        from . import map_facts
        maps = map_facts.discover(rom, family.maps, family.name)
        data.setdefault("maps", {}).update(maps.data.get("maps", {}))
        proofs.extend(maps.facts)
        result.issues.extend(maps.issues)
        result.metadata["maps"] = maps.metadata
        section_names = data.get("pokedex", {}).get("map_sections")
        name_proof = next((item for item in table_result.facts if item.id == "pokedex.map_sections"), None)
        if section_names and name_proof is not None:
            for target, source in (("mapsec_names", "base"), ("mapsec_first", "first"), ("mapsec_count", "count")):
                data["maps"][target] = section_names[source]
                proofs.append(fact(f"maps.{target}", "map", section_names[source], name_proof.evidence,
                                   name_proof.tier))
    from . import string_facts
    strings = string_facts.run(rom, data, {
        int(key): tuple(value) for key, value in family.scripts["defaults"].get(
            "trainerbattle_text_offsets", {}).items()})
    data.setdefault("strings", {}).update(strings.data.get("strings", {}))
    proofs.extend(strings.facts)
    result.issues.extend(strings.issues)
    result.metadata["strings"] = strings.metadata
    from . import native_context
    context = native_context.discover(rom, data, family.context)
    data.update(context.data)
    proofs.extend(context.facts)
    result.issues.extend(context.issues)
    result.metadata["context"] = context.metadata
    if family.windows is not None:
        from . import windows as window_stage
        windows = window_stage.discover(rom, family.windows, profile=data)
        data.update(windows.data)
        proofs.extend(windows.facts)
        result.issues.extend(windows.issues)
        result.metadata["windows"] = windows.metadata
    result.metadata["knowledge"] = [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                                    for path in family.paths]
    from .verification import verify
    verify(rom, result)
    return result
