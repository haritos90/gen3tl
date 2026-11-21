"""Semantic native readers for keyboard, item and text tables."""

from __future__ import annotations

from ..core.charmap import Charmap
from ..core.rom import ROM_BASE, Rom
from ..core.thumb import ThumbError, decode, walk
from ..families.fingerprints import Fingerprint, find
from .accessors import analyze
from .foundations import evidence
from .table_core import _issue, _record, _set, text


class Readers:
    def __init__(self, rom: Rom, knowledge: dict):
        self.rom = rom
        self.rows = {row["name"]: row for row in knowledge.get("functions", [])}
        self.matches = find(rom.data, tuple(Fingerprint.read(row) for row in self.rows.values()))
        self.analyses = {}
        for _ in knowledge.get("constraints", []):
            for rule in knowledge["constraints"]:
                callers = self.matches.get(rule["caller"], ())
                if len(callers) != 1:
                    continue
                instruction = decode(rom.data, callers[0] + rule["offset"])
                if instruction.mnemonic == "bl":
                    self.matches[rule["reader"]] = tuple(at for at in self.matches.get(rule["reader"], ())
                                                         if at == instruction.target)

    def loads(self, descriptor: dict) -> list[dict]:
        name = descriptor["reader"]
        matches = self.matches.get(name, ())
        found = []
        for start in matches:
            end = start + self.rows[name]["length"]
            site = start + descriptor["offset"]
            code = walk(self.rom.data, start, end)
            instruction = next((item for item in code if item.address == site), None)
            if instruction is None or instruction.literal is None:
                continue
            base = self.rom.ptr_at(instruction.literal)
            if base is not None:
                found.append({"base": base, "pool": instruction.literal, "site": site, "start": start,
                              "end": end, "reader": name})
        return found

    def load(self, descriptor: dict) -> dict | None:
        matches = self.loads(descriptor)
        return matches[0] if len(matches) == 1 else None

    def indexed_entry(self, load: dict) -> tuple[int, tuple[int, ...]] | None:
        code = walk(self.rom.data, load["start"], load["end"])
        reachable = {instruction.address for instruction in code}
        starts = [load["start"], *(at for at in range(max(load["start"], load["site"] - 64), load["site"] + 1, 2)
                                 if at in reachable)]
        candidates = {}
        for start in starts:
            end = min(load["end"], load["site"] + 64)
            for instruction in code:
                if load["site"] < instruction.address < end and instruction.mnemonic == "b" and (
                        instruction.target is not None and instruction.target <= instruction.address):
                    end = instruction.address
            try:
                analysis = analyze(self.rom.data, start, end, limit=512)
            except ThumbError:
                continue
            for reader in (*analysis.accessors, *analysis.addresses):
                if reader.base != load["base"] or not reader.indices:
                    continue
                scale = max(coefficient for _, coefficient in reader.indices)
                if 0 < scale <= 128:
                    candidates.setdefault(scale, set()).update(reader.code_sites)
        if not candidates:
            return None
        scale = max(candidates)
        return scale, tuple(sorted(candidates[scale]))

    def fields(self, name: str, base: int) -> list:
        return [reader for _, analysis in self.analyze(name) for reader in analysis.accessors if reader.base == base]

    def analyze(self, name: str) -> list:
        if name not in self.analyses:
            found = []
            for start in self.matches.get(name, ()):
                try:
                    row = self.rows[name]
                    window = row.get("analysis", {"start": 0, "end": row["length"]})
                    found.append((start, analyze(self.rom.data, start + window["start"], start + window["end"])))
                except ThumbError:
                    pass
            self.analyses[name] = found
        return self.analyses[name]


def _items(rom, result, knowledge, readers, bindings):
    binding = bindings.get("items")
    if binding is None:
        return
    root = "pokedex.items"
    descriptor = knowledge.get("item_name")
    pointer_names = False
    sites = []
    if descriptor:
        fields = readers.fields(descriptor["reader"], binding.base)
        matches = readers.matches.get(descriptor["reader"], ())
        if len(matches) == 1:
            start = matches[0]
            subtract = decode(rom.data, start + descriptor["bank_subtract"])
            compare = decode(rom.data, start + descriptor["bank_compare"])
            pointer_names = ({reader.field_offset for reader in fields if reader.width == 1} == set(range(4))
                and subtract.mnemonic == "sub" and compare.mnemonic == "cmp" and compare.rn == subtract.rd
                and subtract.immediate == ROM_BASE >> 24
                and compare.immediate == ((ROM_BASE + len(rom) - 1) >> 24) - subtract.immediate)
            sites = [reader.site for reader in fields] + [subtract.address, compare.address]
    value = {"base": binding.base, "stride": binding.stride, "count": binding.count}
    proof = evidence("Anchored item rows and native getters agree on the complete item array and field representation.",
                     "header+name-anchors+semantic-getter", binding.anchors, sites,
                     stride=binding.stride, rows=binding.count)
    if pointer_names:
        value["long_name_pointers"] = True
        table = next(row for row in result.data["table"] if row["name"] == "items")
        table["long_name_pointers"] = True
        fact = next(fact for fact in result.facts if fact.id == "table[name=items]")
        fact.value["long_name_pointers"] = True
        fact.evidence = proof
    else:
        _issue(result, root + ".long_name_pointers", "The native name getter lacks a verified ROM-bank pointer dispatch.",
               "semantic-getter", binding.anchors)
    _set(result, root, value, proof)
    for descriptor in knowledge.get("item_fields", []):
        members = descriptor.get("members", {"": descriptor.get("reader")})
        fields, code = {}, []
        for name, role in members.items():
            actual = readers.fields(role, binding.base)
            values = {(reader.field_offset, reader.width, reader.signed) for reader in actual}
            if len(values) != 1:
                break
            offset, width, signed = values.pop()
            fields[name] = offset if name else {"offset": offset, "width": width}
            code.extend(reader.site for reader in actual)
        else:
            proof = evidence("Named native getters decode actual record offsets and memory access widths.",
                             "semantic-getter+accessor", [binding.base], code, readers=len(members))
            if "members" in descriptor:
                for name, offset in fields.items():
                    _set(result, descriptor["id"] + "." + name, offset, proof)
            else:
                _set(result, descriptor["id"], fields[""], proof)
            continue
        _issue(result, descriptor["id"], "Native getter matches do not yield one consistent item field layout.",
               "semantic-getter+accessor", [binding.base])


def _tagged_stride(rom, base, subsprites=False):
    candidates = []
    for stride in range(8, 33, 4):
        if base + stride * 4 > len(rom):
            continue
        if subsprites:
            valid = (0 < rom.u16(base) <= 8 and rom.ptr_at(base + 4) is not None
                     and all(rom.data[base + number * stride:base + number * stride + 8]
                             == rom.data[base:base + 8] for number in range(3)))
        else:
            valid = all(rom.ptr_at(base + number * stride) is not None
                        and rom.u16(base + number * stride + 4) > 0
                        and rom.u16(base + number * stride + 6) == number for number in range(4))
        if valid:
            candidates.append(stride)
    return candidates[0] if len(candidates) == 1 else None


def _patch_tables(rom, result, knowledge, readers):
    for descriptor in knowledge.get("patches", []):
        identifier = f"table[name={descriptor['name']}]"
        matches = readers.matches.get(descriptor["reader"], ())
        values, sites = [], []
        if len(matches) == 1:
            start = matches[0]
            for offset in descriptor["offsets"]:
                instruction = decode(rom.data, start + offset)
                if descriptor["method"] == "immediate":
                    if instruction.mnemonic != "mov" or instruction.immediate is None or not 0 <= instruction.immediate <= 255:
                        break
                    end = instruction.address + instruction.size
                    stored = False
                    while end < instruction.address + 12:
                        consumer = decode(rom.data, end)
                        if consumer.mnemonic in ("strb", "strh") and consumer.rd == instruction.rd:
                            stored = True
                            sites.append(end)
                            break
                        if instruction.rd in consumer.written:
                            break
                        end += consumer.size
                    if not stored:
                        break
                    values.append((instruction.address, instruction.size, 1))
                elif instruction.literal is not None:
                    value = text(rom, instruction.literal, instruction.width)
                    if value is None or value[1] != instruction.literal + instruction.width:
                        break
                    values.append((instruction.literal, instruction.width, instruction.width))
                sites.append(instruction.address)
        if not values or len(values) != len(descriptor["offsets"]):
            _issue(result, identifier, "A unique semantic writer and its complete literal/store operands are required.",
                   "semantic-writer+operand")
            continue
        stride = values[1][0] - values[0][0] if len(values) > 1 else values[0][1]
        if stride <= 0 or any(value[0] != values[0][0] + number * stride for number, value in enumerate(values)):
            _issue(result, identifier, "The decoded patch operands do not form a uniform table.", "instruction-operand-stride")
            continue
        value = {"name": descriptor["name"], "base": values[0][0], "stride": stride, "count": len(values),
                 "field_offset": 0, "field_length": values[0][2]}
        if descriptor["method"] == "immediate":
            value["terminated"] = False
        proof = evidence("Matched native writers expose actual character operands, store widths and operand spacing.",
                         "semantic-writer+instruction-operand-stride", [value["base"]], sites,
                         stride=stride, rows=len(values), field_length=values[0][2])
        result.data.setdefault("table", []).append(value)
        _record(result, identifier, "table", value, proof)


def _veneer(rom, target):
    seen = set()
    while target not in seen and 0 <= target < len(rom) - 4:
        seen.add(target)
        first = decode(rom.data, target)
        second = decode(rom.data, target + first.size)
        if first.mnemonic == "b" and first.condition is None:
            target = first.target
        elif first.literal is not None and second.mnemonic == "bx" and second.rm == first.rd:
            pointer = rom.ptr_at(first.literal)
            if pointer is None:
                return None
            target = pointer & ~1
        else:
            return target
    return None


def _copy_tables(rom, result, knowledge, readers):
    for descriptor in knowledge.get("text_copies", []):
        identifier = f"table[name={descriptor['name']}]"
        matches = readers.matches.get(descriptor["reader"], ())
        calls = []
        if len(matches) == 1:
            start = matches[0]
            try:
                analysis = analyze(rom.data, start, start + descriptor["span"])
                calls = [call for call in analysis.calls if call.site == start + descriptor["call"]]
            except ThumbError:
                pass
        candidates = []
        for call in calls:
            source, size = call.arguments[1:3]
            if source.terms or size.terms or not 0 < size.constant <= 128:
                continue
            base, length = source.constant - ROM_BASE, size.constant
            value = text(rom, base, length)
            target = _veneer(rom, call.target)
            if value is not None and value[1] == base + length and target in readers.matches.get(descriptor["callee"], ()):
                candidates.append((base, length, call.site, target))
        if len(set(candidates)) != 1:
            _issue(result, identifier, "Matched text-copy code lacks unique constant source, exact text size or verified byte copier.",
                   "semantic-reader+copy-arguments")
            continue
        base, length, site, target = candidates[0]
        value = {"name": descriptor["name"], "base": base, "stride": length, "count": 1,
                 "field_offset": 0, "field_length": length}
        proof = evidence("A matched native reader copies one complete bounded string through a verified byte-copy routine.",
                         "semantic-reader+copy-arguments+text-boundary", [base], [site, target], bytes=length, rows=1)
        result.data.setdefault("table", []).append(value)
        _record(result, identifier, "table", value, proof)


def _bounded_analysis(rom, start, end):
    code = walk(rom.data, start, min(end, len(rom)))
    backwards = [instruction.address for instruction in code if instruction.mnemonic == "b"
                 and instruction.target is not None and start <= instruction.target < instruction.address]
    stop = min(backwards, default=end)
    return analyze(rom.data, start, stop), code


def scan_bounds(analysis, code, minimum, base=None):
    reads = [access for access in analysis.accesses if not access.write and access.width == 2]
    if base is not None:
        reads = [access for access in reads if not access.address.terms
                 and ROM_BASE + base <= access.address.constant < ROM_BASE + base + 1024]
    if not any(instruction.mnemonic == "b" and instruction.target is not None
               and analysis.start <= instruction.target < instruction.address for instruction in code):
        return []
    found = []
    for comparison in analysis.comparisons:
        if comparison.operation != "cmp":
            continue
        for value, bound in ((comparison.left, comparison.right), (comparison.right, comparison.left)):
            if bound.terms or not minimum <= bound.constant <= 65535:
                continue
            for read in reads:
                if read.site not in value.sites:
                    continue
                parameter = None
                if base is None:
                    terms = read.address.terms
                    if len(terms) != 1 or terms[0][1] != 1 or terms[0][0] not in ("r0", "r1", "r2", "r3"):
                        continue
                    parameter = int(terms[0][0][1])
                found.append({"bound": bound.constant, "parameter": parameter,
                              "sites": sorted(set((*value.sites, *bound.sites, comparison.site)))})
    return found


def _move_lists(rom, result, config, knowledge, readers, bindings):
    moves = bindings.get("moves")
    if moves is None:
        return
    for descriptor in knowledge.get("move_lists", []):
        key = descriptor["key"]
        identifier = f"pokedex.moves.lists[key={key}]"
        candidates = []
        for load in readers.loads(descriptor):
            values, stop = [], load["base"]
            while stop + 2 <= len(rom) and len(values) < 512:
                value = rom.u16(stop)
                if value >= moves.count:
                    break
                values.append(value)
                stop += 2
            if len(values) >= 512 or stop + 2 > len(rom) or not set(config["move_lists"][key]) <= set(values):
                continue
            terminator = rom.u16(stop)
            try:
                analysis, code = _bounded_analysis(rom, load["start"], load["end"])
            except ThumbError:
                continue
            bounds = scan_bounds(analysis, code, moves.count, load["base"])
            for call in analysis.calls:
                target = _veneer(rom, call.target)
                if target is None:
                    continue
                try:
                    called, body = _bounded_analysis(rom, target, min(target + 128, len(rom)))
                except ThumbError:
                    continue
                for bound in scan_bounds(called, body, moves.count):
                    argument = call.arguments[bound["parameter"]]
                    if not argument.terms and argument.constant == ROM_BASE + load["base"]:
                        bounds.append({**bound, "sites": [call.site, *bound["sites"]]})
            pointer_register = decode(rom.data, load["site"]).rd
            for transfer in code:
                if transfer.mnemonic != "bx" or transfer.address <= load["site"]:
                    continue
                literals = [literal for literal in analysis.literals if literal.register == transfer.rm
                            and literal.site < transfer.address and ROM_BASE <= literal.value < ROM_BASE + len(rom)]
                if not literals or any(pointer_register in instruction.written for instruction in code
                                       if load["site"] < instruction.address < transfer.address):
                    continue
                literal = max(literals, key=lambda item: item.site)
                if any(transfer.rm in instruction.written for instruction in code
                       if literal.site < instruction.address < transfer.address):
                    continue
                target = _veneer(rom, (literal.value - ROM_BASE) & ~1)
                if target is None:
                    continue
                try:
                    called, body = _bounded_analysis(rom, target, min(target + 128, len(rom)))
                except ThumbError:
                    continue
                for bound in scan_bounds(called, body, moves.count):
                    if bound["parameter"] == pointer_register:
                        bounds.append({**bound, "sites": [literal.site, transfer.address, *bound["sites"]]})
            matching = [bound for bound in bounds if bound["bound"] == terminator]
            if matching:
                candidates.append((load, len(values), terminator, matching))
        identities = {(load["base"], terminator) for load, _, terminator, _ in candidates}
        if len(identities) != 1:
            _issue(result, identifier, "Anchored move membership and native u16 sentinel comparisons do not select one array.",
                   "semantic-reader+move-anchors+sentinel", candidates=len(identities))
            continue
        load, count, terminator, matches = candidates[0]
        sites = [site for _, _, _, bounds in candidates for bound in bounds for site in bound["sites"]]
        value = {"key": key, "base": load["base"], "terminator": terminator}
        proof = evidence("Known move membership selects a native array whose decoded scanner compares the actual terminator.",
                         "semantic-reader+move-anchors+u16-sentinel", [load["base"], load["pool"]], sites,
                         rows=count, stride=2, terminator=terminator)
        result.data.setdefault("pokedex", {}).setdefault("moves", {}).setdefault("lists", []).append(value)
        _record(result, identifier, "table", value, proof)


def _keyboard_immediates(rom, readers, descriptors, callee=None):
    values, sites, anchors = [], [], []
    for descriptor in descriptors:
        matches = readers.matches.get(descriptor["reader"], ())
        if len(matches) != 1:
            return None
        start = matches[0]
        instruction = decode(rom.data, start + descriptor["offset"])
        following = decode(rom.data, instruction.address + instruction.size)
        if (instruction.mnemonic != "mov" or instruction.immediate is None
                or following.mnemonic != "bl"):
            return None
        if callee is not None:
            target = readers.matches.get(callee, ())
            if len(target) != 1 or following.target != target[0]:
                return None
        values.append(instruction.immediate)
        sites.extend((instruction.address, following.address))
        anchors.append(start)
    return values, sites, anchors


def _keyboard_layout(rom, result, knowledge, readers):
    config = knowledge.get("keyboard_layout", {})
    values = {}
    anchors, sites = [], []
    reserved = config.get("reserved_bytes")
    if reserved:
        start = len(rom) - reserved
        valid = start >= 0 and start % reserved == 0 and all(byte == 0xFF for byte in rom.data[start:])
        if valid:
            values["reserved"] = [start, len(rom)]
            anchors.append(start)
        else:
            _issue(result, "keyboard.reserved", "The final aligned allocation window is not entirely erased.",
                   "aligned-erased-tail", [max(start, 0)])
    for name, descriptor in config.get("immediates", {}).items():
        identifier = "keyboard." + name
        found = _keyboard_immediates(rom, readers, descriptor["sites"], descriptor.get("callee"))
        if found is None or name == "page_count" and len(set(found[0])) != 1:
            _issue(result, identifier, "Named native functions lack consistent immediate-and-call evidence.",
                   "semantic-function+immediate-call")
            continue
        immediates, code, pools = found
        values[name] = code[::2]
        anchors.extend(pools)
        sites.extend(code)
    if values:
        result.data.setdefault("keyboard", {}).update(values)
        proof = evidence("Erased allocation space and naming-screen immediates establish the native keyboard layout.",
                         "aligned-erased-tail+semantic-function+immediate-call", anchors, sites,
                         fields=len(values))
        _record(result, "keyboard", "keyboard", values, proof)


def _keyboard(rom, result, knowledge, readers):
    result.metadata["methods"]["semantic-readers"] = readers.matches
    rows = []
    for descriptor in knowledge.get("keyboard", []):
        identifier = f"keyboard.table[name={descriptor['name']}]"
        loads = [readers.load(item) for item in descriptor["loads"]]
        if not loads or any(load is None for load in loads) or len({load["base"] for load in loads}) != 1:
            _issue(result, identifier, "Semantic readers are missing, ambiguous or disagree on the table pointer.",
                   "semantic-reader")
            continue
        load, method = loads[0], descriptor["entry_method"]
        sites = [item["site"] for item in loads]
        if method == "indexed":
            indexed = readers.indexed_entry(load)
            entry = indexed[0] if indexed else None
            if indexed:
                sites.extend(indexed[1])
        elif method == "narrowed-index":
            left = decode(rom.data, load["start"] + descriptor["scale_left"])
            right = decode(rom.data, load["start"] + descriptor["scale_right"])
            valid = (left.mnemonic == "lsl" and right.mnemonic in ("lsr", "asr") and right.rn == left.rd
                     and left.immediate is not None and right.immediate is not None
                     and 0 <= left.immediate - right.immediate <= 7)
            entry = 1 << (left.immediate - right.immediate) if valid else None
            sites.extend((left.address, right.address))
        else:
            entry = _tagged_stride(rom, load["base"], method == "subsprites")
        if entry is None:
            _issue(result, identifier, "The matched reader lacks a unique decoded index or record-shape stride.",
                   "semantic-reader+record-layout", [load["base"]])
            continue
        value = {"name": descriptor["name"], "refs": sorted({item["pool"] for item in loads}),
                 "base": load["base"], "entry": entry}
        if descriptor["name"] == "rows":
            if not all((pointer := rom.ptr_at(load["base"] + number * 4)) is not None
                       and text(rom, pointer, 128) is not None for number in range(12)):
                _issue(result, identifier, "The indexed row-pointer array does not contain complete keyboard text.",
                       "pointer-text-shape", [load["base"]])
                continue
            value["text"] = True
        proof = evidence("Semantic reader roles locate native pools; decoded indexing and record shape determine entry bytes.",
                         "semantic-reader+index+record-layout", [item["pool"] for item in loads], sites,
                         entry=entry, readers=len(loads))
        rows.append(value)
        _record(result, identifier, "table", value, proof)
    if rows:
        result.data["keyboard"] = {"table": rows}
    bound = {row["name"]: row for row in rows}
    if "keys" in bound and "columns" in bound:
        columns = bound["columns"]["base"]
        counts = []
        while columns + len(counts) < len(rom) and 1 <= rom.u8(columns + len(counts)) <= 16:
            counts.append(rom.u8(columns + len(counts)))
        width = max(counts, default=0)
        base, count = bound["keys"]["base"], 0
        if width:
            while base + (count + 1) * width <= len(rom) and count < 256:
                raw = bytes(rom.data[base + count * width:base + (count + 1) * width])
                value = Charmap().decode(raw + b"\xff", 0, limit=width + 1)[0]
                if 255 in raw or "{" in value or "\\" in value or not all(c.isprintable() for c in value):
                    break
                count += 1
        if 0 < count < 256 and count * width == columns - base:
            value = {"name": "naming_keyboard", "base": base, "stride": width, "count": count,
                     "field_offset": 0, "field_length": width, "terminated": False}
            proof = evidence("The character reader and column array delimit a complete unterminated keyboard grid.",
                             "semantic-reader+character-grid-boundary", [base, columns],
                             [site for row in (bound["keys"], bound["columns"]) for site in row["refs"]],
                             stride=width, rows=count)
            result.data.setdefault("table", []).append(value)
            _record(result, "table[name=naming_keyboard]", "table", value, proof)
    _keyboard_layout(rom, result, knowledge, readers)
