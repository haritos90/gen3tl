"""Declared substitution sources shared by checks and the editor."""

from __future__ import annotations

from collections import Counter
from itertools import product

from ..core.charmap import CharmapError
from ..core.lists import append_unique
from .layout import Measure, line_width, split_tokens

OPS = frozenset({"source", "entries", "table", "pointers", "max", "concat", "paired", "letters", "digits", "empty"})


class Unavailable(ValueError):
    """Valid sources lack required measurement inputs."""


def validate(prof) -> None:
    """Validate source expressions, references and consumer restrictions."""
    lay = prof.get("layout", {})
    sources = lay.get("fill_source", {})
    fonts = {f["name"] for f in prof.get("font", []) if "name" in f}
    tables = {t["name"]: t for t in prof.get("table", []) if "name" in t}
    if not isinstance(sources, dict):
        raise ValueError("fill_source: expected named sources")
    visiting, visited = set(), set()

    def strings(value):
        return isinstance(value, list) and bool(value) and all(isinstance(s, str) and s for s in value) \
            and len(value) == len(set(value))

    def expression(expr):
        if not isinstance(expr, dict) or len(OPS & expr.keys()) != 1:
            raise ValueError("fill_source: expected one expression operation")
        op = next(iter(OPS & expr.keys()))
        allowed = {op} | ({"indices"} if op == "table" else {"alphabet"} if op == "letters" else set())
        if expr.keys() - allowed:
            raise ValueError("fill_source: unknown expression fields")
        value = expr[op]
        if op == "source":
            visit(value)
        elif op == "entries":
            if not strings(value):
                raise ValueError("fill_source: invalid entries")
        elif op == "table":
            if value not in tables:
                raise ValueError(f"fill_source: unknown table {value}")
            indices = expr.get("indices", list(range(tables[value]["count"])))
            if (not isinstance(indices, list) or not indices or len(indices) != len(set(indices))
                    or any(type(i) is not int or not 0 <= i < tables[value]["count"] for i in indices)):
                raise ValueError("fill_source: invalid table indices")
        elif op == "pointers":
            if (not isinstance(value, dict) or set(value) != {"base", "count", "stride", "fields"}
                    or any(type(value[k]) is not int or value[k] < (k != "base")
                           for k in ("base", "count", "stride"))
                    or not isinstance(value["fields"], list) or not value["fields"]
                    or any(type(f) is not int or not 0 <= f < value["stride"] for f in value["fields"])):
                raise ValueError("fill_source: invalid pointer domain")
        elif op in ("max", "concat"):
            if op == "max":
                if not isinstance(value, dict) or not value or any(not isinstance(k, str) or not k for k in value):
                    raise ValueError("fill_source: alternatives require names")
                value = value.values()
            elif not isinstance(value, list) or not value:
                raise ValueError("fill_source: empty concatenation")
            for child in value:
                expression(child)
        elif op == "paired":
            if (not isinstance(value, dict) or set(value) != {"with", "rows"}
                    or not isinstance(value["rows"], list) or not value["rows"]
                    or any(not isinstance(row, list) or len(row) != 2 for row in value["rows"])):
                raise ValueError("fill_source: invalid paired domain")
            visit(value["with"])
            for row in value["rows"]:
                for child in row:
                    expression(child)
        elif op == "empty":
            if value is not True:
                raise ValueError("fill_source: empty must be true")
        elif type(value) is not int or not 1 <= value <= 255:
            raise ValueError("fill_source: invalid character count")
        elif op == "letters" and expr.get("alphabet") != "naming":
            raise ValueError("fill_source: unknown input alphabet")

    def visit(name):
        if not isinstance(name, str) or name not in sources:
            raise ValueError(f"fill_source: unknown source {name}")
        if name in visiting:
            raise ValueError(f"fill_source: cycle at {name}")
        if name in visited:
            return
        row = sources[name]
        if (not isinstance(row, dict) or set(row) - {"label", "font", "value", "evidence", "mode"}
                or not all(row.get(k) for k in ("label", "font", "value", "evidence"))
                or row["font"] not in fonts or row.get("mode", "advance") not in ("advance", "extent")):
            raise ValueError(f"fill_source: invalid source {name}")
        visiting.add(name)
        expression(row["value"])
        visiting.remove(name)
        visited.add(name)

    for name in sources:
        visit(name)
    for name in lay.get("slot_source", {}).values():
        visit(name)
    fills = {r.get("name"): r for r in lay.get("code_fill", [])}
    for name, slots in lay.get("code_slot_source", {}).items():
        if name not in fills or slots.keys() - fills[name]["slots"].keys():
            raise ValueError(f"fill_source: unknown code slot {name}")
        for source in slots.values():
            visit(source)
    for name, restrictions in lay.get("slot_context", {}).items():
        if not isinstance(restrictions, dict) or not restrictions.keys() - {"evidence"}:
            raise ValueError(f"slot_context: invalid context {name}")
        for source, alternatives in restrictions.items():
            if source == "evidence":
                if not isinstance(alternatives, str) or not alternatives.strip():
                    raise ValueError("slot_context: evidence must contain text")
                continue
            visit(source)
            available = sources[source]["value"].get("max", {})
            if not strings(alternatives) or set(alternatives) - available.keys():
                raise ValueError(f"slot_context: unknown alternatives in {source}")

    for context in lay.get("window_slot_context", {}).values():
        if context not in lay.get("slot_context", {}):
            raise ValueError(f"slot_context: unknown context {context}")


def table_entries(prof, inv, name: str, indices=None) -> list[str]:
    """Resolve every requested record, including long-name pointers."""
    table = next(t for t in prof["table"] if t["name"] == name)
    wanted = set(range(table["count"]) if indices is None else indices)
    found = {}
    for row in inv["entries"]:
        if row.get("kind") == "fixed" and row.get("table") == name and row.get("index") in wanted:
            found[row["index"]] = row["id"]
        for ref in row.get("refs", []):
            at, kind = (ref["at"], ref["kind"]) if isinstance(ref, dict) else ref
            if kind != "table:" + name:
                continue
            index, rem = divmod(at - table["base"] - table.get("field_offset", 0), table["stride"])
            if not rem and index in wanted:
                found[index] = row["id"]
    if wanted - found.keys():
        raise Unavailable(f"fill_source: incomplete table {name}: {sorted(wanted - found.keys())}")
    return sorted(set(found.values()))


def graph(prof, inv, catalog, *, key=lambda sid: sid, alphabet=None, selected=None) -> dict:
    """Resolve profile domains to a portable graph of catalog dependencies."""
    validate(prof)
    inv = inv or {"entries": []}
    lay = prof.get("layout", {})
    sources = lay.get("fill_source", {})
    fonts = {f["name"]: f.get("style", prof.get("font_style", {}).get("style", f["name"])) for f in prof.get("font", [])}
    if selected is not None:
        needed = set()

        def include(expr):
            if "source" in expr:
                name = expr["source"]
                if name not in needed:
                    needed.add(name)
                    include(sources[name]["value"])
            for child in expr.get("concat", expr.get("max", {}).values()):
                include(child)
            if "paired" in expr:
                include({"source": expr["paired"]["with"]})
                for row in expr["paired"]["rows"]:
                    for child in row:
                        include(child)
        for name in selected:
            include({"source": name})
        sources = {name: row for name, row in sources.items() if name in needed}
    def letters(expr):
        return ("letters" in expr or any(letters(child) for child in expr.get("concat", expr.get("max", {}).values()))
                or "paired" in expr and (letters({"source": expr["paired"]["with"]})
                                         or any(letters(child) for row in expr["paired"]["rows"] for child in row)))

    needs_alphabet = any(letters(row["value"]) for row in sources.values())
    if alphabet is None:
        alphabet = set()
        for name, text in prof.get("keyboard", {}).get("rows", {}).items():
            if "_keys_" in name:
                alphabet.update(text)
        if needs_alphabet and any(t["name"] == "naming_keyboard" for t in prof.get("table", [])):
            try:
                for sid in table_entries(prof, inv, "naming_keyboard"):
                    entry = catalog.get(sid, (None, None))[1]
                    if entry is None or entry.keyed:
                        raise Unavailable("fill_source: input alphabet unavailable")
                    alphabet.update(entry.msgid)
            except Unavailable:
                alphabet.clear()
    alphabet = "".join(sorted(set(alphabet) - {" "}))

    def resolve(expr):
        try:
            return resolve_domain(expr)
        except Unavailable as exc:
            return {"unavailable": str(exc)}

    def resolve_domain(expr):
        if "table" in expr:
            return resolve({"entries": table_entries(prof, inv, expr["table"], expr.get("indices"))})
        if "pointers" in expr:
            spec = expr["pointers"]
            wanted = {spec["base"] + i * spec["stride"] + f for i in range(spec["count"]) for f in spec["fields"]}
            found = {}
            for row in inv["entries"]:
                for ref in row.get("refs", []):
                    at = ref["at"] if isinstance(ref, dict) else ref[0]
                    if at in wanted:
                        found[at] = row["id"]
            if wanted - found.keys():
                raise Unavailable("fill_source: incomplete pointer domain")
            return resolve({"entries": sorted(set(found.values()))})
        if "entries" in expr:
            missing = set(expr["entries"]) - catalog.keys()
            if missing:
                raise Unavailable(f"fill_source: sources missing from catalog: {sorted(missing)}")
            return {"entries": [key(sid) for sid in expr["entries"]]}
        if "max" in expr:
            return {"max": {k: resolve(v) for k, v in expr["max"].items()}}
        if "concat" in expr:
            return {"concat": [resolve(v) for v in expr["concat"]]}
        if "paired" in expr:
            spec = expr["paired"]
            return {"paired": {"with": spec["with"],
                               "rows": [[resolve(child) for child in row] for row in spec["rows"]]}}
        return dict(expr)

    nodes = {name: {"label": row["label"], "style": fonts[row["font"]], "value": resolve(row["value"]),
                    "mode": row.get("mode", "advance"), "evidence": str(row["evidence"])}
             for name, row in sources.items()}
    out = {"sources": nodes, "alphabet": alphabet, "contexts": lay.get("slot_context", {}),
           "globals": dict(lay.get("slot_source", {})), "code": lay.get("code_slot_source", {})}
    out["dependencies"] = {name: dependencies(out, name) for name in nodes}
    return out


def dependencies(graph: dict, source: str) -> list[str]:
    """Keep all candidates, including sources below the current maximum."""
    def walk(expr):
        if "entries" in expr:
            return set(expr["entries"])
        if "source" in expr:
            return walk(graph["sources"][expr["source"]]["value"])
        if "paired" in expr:
            spec = expr["paired"]
            return walk({"source": spec["with"]}) | set().union(
                *(walk(child) for row in spec["rows"] for child in row))
        return set().union(*(walk(child) for child in expr.get("concat", expr.get("max", {}).values())))
    return sorted(walk(graph["sources"][source]["value"]))


def fragment(text: str, cm) -> dict:
    """Source-free counts preserve internal and trailing spaces."""
    measure = Measure(lambda _: 0, {})
    tokens = split_tokens(text)
    glyphs = [measure.end_glyph(token, cm) for token in tokens]
    if any(b is None for b in glyphs) or "\\" in text or "\n" in text:
        raise ValueError("fill_source: unsupported control-bearing original")
    trailing = len(text) - len(text.rstrip(" "))
    return {"glyphs": [[b, n] for b, n in sorted(Counter(glyphs).items())],
            "trailing": trailing, "end": glyphs[-trailing - 1] if len(glyphs) > trailing else None}



class Evaluator:
    """Measure one resolved graph against a catalog state and font revision."""

    def __init__(self, graph, entries, measure, cm, originals=None, drafts=None):
        self.graph, self.entries, self.measure, self.cm = graph, entries, measure, cm
        self.originals, self.drafts = originals or {}, drafts or {}
        self.cache = {}

    def _entry(self, sid, style, original):
        entry = self.entries.get(sid)
        if entry is None:
            if self.originals.get(sid, {}).get("literal"):
                entry = {}
            else:
                raise Unavailable(f"fill_source: dependency unavailable: {sid}")
        if isinstance(entry, tuple):
            obj = entry[1]
            entry = {"translation": obj.msgstr, "flags": obj.flags,
                     **({} if obj.keyed else {"source_text": obj.msgid})}
        state = self.drafts.get(sid, {}) if not original else {}
        if isinstance(state, str):
            state = {"text": state, "status": "draft" if state else "original"}
        text = state.get("text", entry.get("translation", ""))
        empty = state.get("status") == "empty" or not state and "empty" in entry.get("flags", [])
        fallback = original or not empty and (state.get("status") == "original" or not text)
        if fallback:
            text = entry.get("source_text")
        elif empty:
            text = ""
        glyph = self.measure.styles.get(style, self.measure.glyph_width)
        m = Measure(glyph, self.measure.ph, self.measure.default_ph, self.measure.styles, fonts=self.measure.fonts)
        if text is not None:
            try:
                fact = fragment(text, self.cm)
                advance, extent = m.width(text, self.cm), line_width(text, m, self.cm)
                end = fact["end"]
            except CharmapError:
                if sid in self.drafts:
                    raise ValueError(f"fill_source: unmeasurable draft: {sid}")
                if fallback:
                    raise
                return self._entry(sid, style, True)
        else:
            fact = self.originals.get(sid)
            if fact is None:
                raise Unavailable(f"fill_source: original measurement unavailable: {sid}")
            advance = sum(glyph(b) * n for b, n in fact["glyphs"])
            extent = advance - fact["trailing"] * m.width(" ", self.cm)
            end = fact["end"]
            if original and fact.get("literal"):
                advance = extent = fact["source_width"]
        contributor = {"id": sid, "width": advance, "extent": extent, "original": fallback}
        if not fallback:
            contributor["text"] = text
        if self.originals.get(sid, {}).get("literal"):
            contributor["literal"] = True
        return {"advance": advance, "extent": extent, "end": end, "parts": [contributor]}

    @staticmethod
    def _best(rows):
        # Retain both maxima for concatenation.
        found = []
        for field in ("advance", "extent"):
            for row in sorted(rows, key=lambda r: -r[field])[:3]:
                append_unique(found, row)
        return found

    def _rows(self, name, context, original):
        cache_key = (name, context, original)
        if cache_key in self.cache:
            return self.cache[cache_key]
        source = self.graph["sources"][name]
        style = source["style"]
        allowed = self.graph.get("contexts", {}).get(context, {}).get(name)

        def evaluate(expr):
            if "unavailable" in expr:
                raise Unavailable(expr["unavailable"])
            if "empty" in expr:
                return [{"advance": 0, "extent": 0, "end": None,
                         "parts": [{"label": "No prefix", "width": 0, "original": False}]}]
            if "source" in expr:
                child = self.graph["sources"][expr["source"]]
                if child["style"] != style:
                    raise ValueError("fill_source: concatenation requires a shared printer font")
                return self._rows(expr["source"], context, original)
            if "entries" in expr:
                return self._best([self._entry(sid, style, original) for sid in expr["entries"]])
            if "max" in expr:
                return self._best([row for branch, child in expr["max"].items()
                                   if allowed is None or branch in allowed for row in evaluate(child)])
            if "paired" in expr:
                spec = expr["paired"]
                partner = self.graph["sources"][spec["with"]]
                if partner["style"] != style:
                    raise ValueError("fill_source: paired sources require a shared printer font")
                partner_rows = self._rows(spec["with"], context, original)
                glyph = self.measure.styles.get(style, self.measure.glyph_width)
                m = Measure(glyph, self.measure.ph, self.measure.default_ph, self.measure.styles, fonts=self.measure.fonts)
                space = m.width(" ", self.cm)
                space_end = self.cm.encode(" ", terminate=False)[-1]
                rows = []
                for left, right in spec["rows"]:
                    for first, second in product(evaluate(left), evaluate(right)):
                        advance = first["advance"] + space + second["advance"]
                        extent, end = first["extent"] + space, space_end
                        if second["end"] is not None:
                            extent, end = first["advance"] + space + second["extent"], second["end"]
                        rows.append({"advance": advance, "extent": extent, "end": end,
                                     "parts": first["parts"] + second["parts"], "left_parts": first["parts"],
                                     "left_end": first["end"]})
                rows = self._best(rows)
                # The host already supplies the separator.
                return [{"advance": max(0, row["advance"] - space - max(p["advance"] for p in partner_rows)),
                         "extent": max(0, row["extent"] - space - max(p["extent"] for p in partner_rows)),
                         "end": row["left_end"], "parts": row["left_parts"]} for row in rows]
            if "concat" in expr:
                rows = []
                for parts in product(*(evaluate(child) for child in expr["concat"])):
                    advance, extent, end = 0, 0, None
                    for part in parts:
                        if part["end"] is not None:
                            extent, end = advance + part["extent"], part["end"]
                        advance += part["advance"]
                    rows.append({"advance": advance, "extent": extent, "end": end,
                                 "parts": [p for part in parts for p in part["parts"]]})
                return self._best(rows)
            count = expr.get("letters", expr.get("digits"))
            alphabet = self.graph["alphabet"] if "letters" in expr else "0123456789"
            if not alphabet:
                raise Unavailable("fill_source: input alphabet unavailable")
            glyph = self.measure.styles.get(style, self.measure.glyph_width)
            m = Measure(glyph, {})
            char = max(sorted(alphabet), key=lambda c: m.width(c, self.cm))
            width = count * m.width(char, self.cm)
            return [{"advance": width, "extent": width, "end": self.cm.encode(char, terminate=False)[-1],
                     "parts": [{"label": "Typed name" if "letters" in expr else "Number", "count": count,
                                "glyph": char, "width": width, "original": False}]}]
        rows = evaluate(source["value"])
        self.cache[cache_key] = rows
        return rows

    def result(self, source=None, recorded=None, override=None, context=None, *, fallback=None):
        """Return applied and computed widths with linked maximizing contributors."""
        if context is not None and context not in self.graph.get("contexts", {}):
            raise ValueError(f"slot_context: unknown context {context}")
        if source is None:
            return {"width": recorded, "recorded": recorded, "state": "recorded", "contributors": [],
                    "dependencies": []}
        node = self.graph["sources"][source]
        if override is not None:
            if (set(override) != {"width", "reason", "source"} or override["source"] != source
                    or type(override["width"]) is not int or override["width"] < 0
                    or not isinstance(override["reason"], str) or not override["reason"].strip()):
                raise ValueError("fill_source: override requires width, source and reason")
        field = node["mode"]
        try:
            rows = sorted(self._rows(source, context, False), key=lambda row: -row[field])
            source_width = max(row[field] for row in self._rows(source, context, True))
        except Unavailable as exc:
            width = recorded if recorded is not None else fallback if fallback is not None else self.measure.default_ph
            out = {"source": source, "label": node["label"], "width": width, "recorded": width,
                   "source_width": width, "state": "recorded", "unavailable": str(exc),
                   "contributors": [], "dependencies": self.graph["dependencies"][source]}
            if override is not None:
                out.update(width=override["width"], state="override", reason=override["reason"])
            return out
        maximum = rows[0][field]
        winners = [row for row in rows if row[field] == maximum][:3]
        out = {"source": source, "label": node["label"], "width": maximum, "computed": maximum,
               "source_width": source_width,
               "advance": winners[0]["advance"], "extent": winners[0]["extent"],
               "state": "computed", "contributors": winners[0]["parts"],
               "winners": [row["parts"] for row in winners],
               "dependencies": self.graph["dependencies"][source]}
        if recorded is not None:
            out["recorded"] = recorded
        if override is not None:
            out.update(width=override["width"], state="override", reason=override["reason"])
        return out


def code_graph(prof, catalog, originals, measure, cm, *, key=lambda sid: sid, fragments=None, rules=None):
    """Adapt existing catalog and numeric fills to the shared evaluator."""
    fonts = {f["name"]: f.get("style", prof.get("font_style", {}).get("style", f["name"]))
             for f in prof.get("font", [])}
    main = prof.get("font_style", {}).get("style")
    nodes, bindings, literals = {}, {}, {}
    for index, rule in enumerate(prof.get("layout", {}).get("code_fill", [])):
        if rules is not None and id(rule) not in rules:
            continue
        name = rule.get("name", f"reader-{index}")
        for token, slot in rule["slots"].items():
            if not ("strings" in slot or "digits" in slot):
                continue
            source = f"code/{name}/{token}"
            if "strings" in slot:
                ids = list(slot["strings"])
                missing = []
                for original in slot.get("originals", []):
                    sid = original["id"]
                    if sid not in originals:
                        missing.append(sid)
                        continue
                    fact = originals[sid]
                    literals[key(sid)] = {"glyphs": fact["glyph_counts"], "trailing": 0, "end": None,
                                          "source_width": fact["width"], "literal": True}
                    ids.append(sid)
                value = {"entries": [key(sid) for sid in sorted(ids, reverse=True)]}
                missing += [sid for sid in slot["strings"] if sid not in catalog]
                if missing:
                    value = {"unavailable": f"fill_source: dependencies unavailable: {sorted(missing)}"}
                style = fonts[slot["font"]]
            else:
                value, style = {"digits": slot["digits"]}, main
            nodes[source] = {"label": "Number" if "digits" in slot else "Inserted text", "style": style,
                             "value": value, "mode": "extent", "evidence": str(rule["evidence"])}
            bindings.setdefault(name, {})[token] = source
    graph = {"sources": nodes, "alphabet": "", "contexts": {}, "globals": {}, "code": bindings,
             "literals": literals}
    graph["dependencies"] = {name: [sid for sid in dependencies(graph, name) if sid not in literals] for name in nodes}
    wanted = set().union(*map(set, graph["dependencies"].values()))
    facts, values = dict(literals), {}
    for sid, (_, entry) in catalog.items():
        identity = key(sid)
        if identity not in wanted:
            continue
        if entry.keyed:
            fact = (fragments or {}).get(sid)
        else:
            fact = fragment(entry.msgid, cm)
        if fact is not None:
            facts[identity] = fact
        values[identity] = {"translation": entry.msgstr, "flags": list(entry.flags)}
    graph.update(fragments=facts, values=values)
    return graph


def merge_results(results, floor=0):
    """Keep every reader alternative and an undeclared-source floor."""
    known = [row for row in results if row]
    floor = max(floor, *(row.get("floor", 0) for row in known))
    rows = []
    for row in known:
        for choice in row.get("choices", [row]):
            append_unique(rows, choice)
    if len(rows) == 1 and not floor:
        return rows[0]
    winner = max(rows, key=lambda row: row["width"])
    out = {**winner, "choices": rows,
           "dependencies": sorted({sid for row in rows for sid in row["dependencies"]}),
           "source_width": max(row["source_width"] for row in rows)}
    floor = max(floor, *(row.get("floor", 0) for row in rows))
    if floor:
        out.update(floor=floor, width=max(floor, out["width"]), state="recorded")
    return out


def native_graph(prof, inv, catalog, cm, *, key=lambda sid: sid, fragments=None, selected=None):
    """Adapt verified native table variants without inferring prose bindings."""
    from ..discover.maps import native_fill_rules, native_fill_sources
    inv = inv or {"entries": []}
    nodes = {}
    for rule in native_fill_rules(prof).values():
        if selected is not None and rule["name"] not in selected:
            continue
        try:
            variants = native_fill_sources(rule, inv)
            if any(sid not in catalog for ids in variants for sid in ids):
                raise Unavailable("fill_source: native sources missing from catalog")
            value = {"max": {str(i): {"concat": [{"entries": [key(sid)]} for sid in ids]}
                             for i, ids in enumerate(variants)}}
        except ValueError as exc:
            value = {"unavailable": str(exc)}
        nodes["native/" + rule["name"]] = {"label": "Inserted text", "style": prof.get("font_style", {}).get("style"),
                                           "mode": "advance", "value": value}
    result = {"sources": nodes, "contexts": {}}
    result["dependencies"] = {name: dependencies(result, name) for name in nodes}
    wanted = set().union(*map(set, result["dependencies"].values()))
    values, facts = {}, {}
    for sid, (_, entry) in catalog.items():
        if key(sid) not in wanted:
            continue
        fact = (fragments or {}).get(sid) if entry.keyed else fragment(entry.msgid, cm)
        if fact is not None:
            facts[key(sid)] = fact
        values[key(sid)] = {"translation": entry.msgstr, "flags": list(entry.flags)}
    result.update(values=values, fragments=facts)
    return result
