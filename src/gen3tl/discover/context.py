"""Context studies and facts derived from map and script walks."""

from __future__ import annotations

from collections import defaultdict

from ..core.notes import has_prose
from ..core.rom import Rom
from .maps import Branch, BufferFill, Join, Walker, gender_source, identity_context, native_fill_sources

SPEAKER = __import__("re").compile(
    r"(?:^|\\p)(?:\{COLOR [0-9A-F]{2}\})?([A-Z][A-Za-z0-9.'’\- ]{0,24}?): ?\{COLOR ([0-9A-F]{2})\}")


def inline_speakers(text: str, colours: dict[str, str]) -> list[dict]:
    """Speaker labels written into the text ("Name: {COLOR 08}...")."""
    out = []
    for m in SPEAKER.finditer(text):
        out.append({"name": m.group(1), "colour": m.group(2), "gender": colours.get(m.group(2), "unknown")})
    return out


LEADING_COLOUR = __import__("re").compile(r"\{COLOR ([0-9A-F]{2})\}((?:[^{]|\{(?!COLOR )[^}]*\})*)")


def leading_gender(text: str, colours: dict[str, str]) -> str | None:
    """The speaker gender of a colour the text opens with."""
    m = LEADING_COLOUR.match(text)
    if not m or colours.get(m.group(1)) not in ("male", "female"):
        return None
    run = __import__("re").sub(r"\{[^}]*\}|\\[nlp]", "", m.group(2))
    return colours[m.group(1)] if sum(ch.isalpha() for ch in run) >= 4 else None


def participant_rules(prof, ids: set[str]) -> dict:
    """Validate explicit scene identities against the inventory."""
    out = defaultdict(list)
    for rule in prof.get("context", {}).get("participants", []):
        if (not isinstance(rule, dict) or not {"text", "role", "evidence"} <= rule.keys()
                or rule.keys() - {"text", "role", "evidence", "sprite", "trainer", "textcolor",
                                  "in_source", "basis", "player"}
                or not isinstance(rule["text"], str) or rule["text"] not in ids
                or rule["role"] not in ("speaker", "addressee")
                or not has_prose(rule["evidence"])
                or any(type(rule[k]) is not int or rule[k] < 0
                       for k in ("sprite", "trainer", "textcolor") if k in rule)
                or "player" in rule and rule["player"] is not True
                or rule.get("in_source", "unknown") not in ("stated", "not-stated", "unknown")
                or rule.get("basis", "") not in ("", "pronoun", "gendered word", "established character")
                or rule.get("in_source") == "stated" and not rule.get("basis")
                or rule.get("basis") and rule.get("in_source") != "stated"
                or any(r["role"] == rule["role"] for r in out[rule["text"]])):
            raise ValueError("invalid context participant declaration")
        out[rule["text"]].append(rule)
    return out


def context_identities(records: dict, entries: dict, prof, walker) -> None:
    """Attach independent evidence to each known participant."""
    config = prof.get("context", {})
    sprites = config.get("sprite_genders", {})
    for key, value in sprites.items():
        if (not isinstance(key, str) or not key.isdecimal()
                or not isinstance(value, dict) or set(value) != {"gender", "evidence"}
                or value["gender"] not in ("male", "female", "neutral", "unknown")
                or not has_prose(value["evidence"])):
            raise ValueError("invalid sprite gender declaration")
    declared = participant_rules(prof, set(records))
    colours = config.get("textcolor_genders", {})

    def add(role, source):
        if source not in role["sources"]:
            role["sources"].append(source)

    def sprite(role, number, evidence):
        fact = sprites.get(str(number), {})
        add(role, gender_source("sprite", fact.get("gender"), number,
                               "; ".join(x for x in (evidence, fact.get("evidence")) if x)))

    def trainer(role, info, evidence):
        if info:
            add(role, gender_source("trainer", info["gender"],
                                   f"{info['id']} / {info['class']}", evidence))

    for sid, rec in records.items():
        identity = identity_context(entries[sid]["text"])
        speaker = identity["speaker"]
        lead = leading_gender(entries[sid]["text"], config.get("speaker_colours", {}))
        for use in rec.get("uses", []):
            if use.get("buffer") is not None:
                continue
            evidence = f"{use['map']} / {use['script']} / {use['order']}"
            if use.get("sprite") is not None:
                sprite(speaker, use["sprite"], evidence)
            trainer(speaker, use.get("trainer"), evidence)
            if use.get("trainer") and not any(r["role"] == "addressee" for r in declared.get(sid, [])):
                add(identity["addressee"], gender_source("player", "variable", "", evidence))
            if use.get("textcolor") is not None and not lead:
                add(speaker, gender_source("text colour", use["textcolor_gender"],
                                           use.get("textcolor", ""), evidence))
        for label in rec.get("speakers", []):
            add(speaker, gender_source("label", label.get("gender"), label.get("colour", "")))
        if lead:
            colour = LEADING_COLOUR.match(entries[sid]["text"])[1]
            add(speaker, gender_source("text colour", lead, colour, "inline"))
        for rule in declared.get(sid, []):
            role = identity[rule["role"]]
            if "sprite" in rule:
                sprite(role, rule["sprite"], rule["evidence"])
            if "trainer" in rule:
                info = walker.trainer_info(rule["trainer"])
                if info is None:
                    raise ValueError("context participant trainer is missing")
                trainer(role, info, rule["evidence"])
            if "textcolor" in rule:
                add(role, gender_source("text colour", colours.get(str(rule["textcolor"])),
                                        rule["textcolor"], rule["evidence"]))
            if rule.get("player"):
                add(role, gender_source("player", "variable", "", rule["evidence"]))
            if "in_source" in rule:
                role.update(in_source=rule["in_source"], basis=rule.get("basis", ""))
        rec["identity"] = identity


def split_uses(out: dict[str, dict], inv: dict) -> None:
    """Give each copy entry the uses at its references, taken from its original."""
    from ..catalog.keys import copy_source
    for e in inv["entries"]:
        if "copy" not in e.get("flags", ()) or e["kind"] != "pointer":
            continue
        base = out[copy_source(e["id"])]
        ats = {r["at"] for r in e["refs"]}
        out[e["id"]] = {"uses": [u for u in base["uses"] if u["at"] in ats], "inferred_map": None,
                        "speakers": list(base["speakers"])}
        base["uses"] = [u for u in base["uses"] if u["at"] not in ats]


def _analyze_context(rom: Rom, prof, inv: dict, window: int = 0x4000) -> dict:
    """string id -> context record (direct map uses, inferred map, inline speakers)."""
    w = Walker(rom, prof)
    w.walk_all()
    ctx = w.walk_structural()
    engine = w.engine_map_ids()
    map_records = {
        mid: {"header": f"{h:#x}", "name": w.mapsec_name(rom.data[h + 20]),
              "engine_maps": engine.get(h, []) if engine is not None else None}
        for mid, h in ctx.headers.items()
    }
    colours = {k.upper(): v for k, v in prof.get("context", {}).get("speaker_colours", {}).items()}
    textcolors = {int(k): v for k, v in prof.get("context", {}).get("textcolor_genders", {}).items()}
    trainer_classes = {}
    tc = next((t for t in prof.get("table", []) if t["name"] == "trainer_classes"), None)
    by_off = {e["offset"]: e for e in inv["entries"] if e["kind"] == "pointer" and "copy" not in e.get("flags", ())}
    if tc:
        for e in inv["entries"]:
            if e.get("table") == "trainer_classes":
                trainer_classes[e["index"]] = e["text"]
    out: dict[str, dict] = {}
    direct = sorted(o for o in ctx.uses if o in by_off)
    import bisect
    for off, e in by_off.items():
        rec: dict = {"uses": [], "inferred_map": None, "speakers": inline_speakers(e["text"], colours)}
        lead = leading_gender(e["text"], colours)   # the text's own colour
        for u in ctx.uses.get(off, []):
            d = {"map": u.map, "map_name": u.map_name, "event": u.event, "local_id": u.local_id,
                 "engine_maps": map_records[u.map]["engine_maps"],
                 "sprite": u.sprite, "textcolor": u.textcolor,
                 # the text's own colour wins
                 "textcolor_gender": lead or textcolors.get(u.textcolor),
                 "box": u.box, "order": u.order, "script": f"{u.script:#x}", "buffer": u.buffer,
                 "at": u.at, "trainer_role": u.trainer_role}
            if u.trainer:
                t = dict(u.trainer)
                t["class_name"] = trainer_classes.get(t["class"], f"class {t['class']}")
                d["trainer"] = t
            rec["uses"].append(d)
        if not rec["uses"]:
            i = bisect.bisect_left(direct, off)
            left = direct[i - 1] if i > 0 else None
            right = direct[i] if i < len(direct) else None
            lmap = ctx.uses[left][0].map_name if left is not None and off - left <= window else None
            rmap = ctx.uses[right][0].map_name if right is not None and right - off <= window else None
            if lmap and lmap == rmap:
                rec["inferred_map"] = lmap
        out[e["id"]] = rec
    # buffer links: a command fills STR_VAR_(n+1) for later texts;
    # events replay in order; fills replace, branches merge values;
    # slots and slot_refs record fills, paths and unknown references.
    w.walk_buffer_starts([r["at"] for e in inv["entries"] for r in e["refs"] if r["kind"] == "script:bufferstring"])
    id_of = {off: e["id"] for off, e in by_off.items()}
    events: dict[int, list] = defaultdict(list)
    met: dict[int, set[int]] = defaultdict(set)   # text offset -> pointer positions the walks met
    for uses in (ctx.uses, ctx.side_uses):
        for off, us in uses.items():
            for u in us:
                if u.buffer is None:   # a buffered word itself comes as its BufferFill
                    events[u.walk].append(((u.order, 1), off, u))
                met[off].add(u.at)
    for x in ctx.fills + ctx.joins + ctx.branches:
        events[x.walk].append(((x.order, 0, x.seq), None, x))
    unset = ("unset", None)   # a path that sets no value
    native_words = {r["name"]: native_fill_sources(r, inv) for r in w.native_fills.values()}

    def shows(off: int, slot_i: int) -> bool:
        return off in id_of and "{STR_VAR_%d}" % (slot_i + 1) in by_off[off]["text"]

    def reach(tid: str, slot: str, fills: list) -> None:
        s = out[tid].setdefault("slots", {}).setdefault(slot, {"fills": [], "uses": 0, "unfilled": 0})
        s["uses"] += 1
        if not fills or unset in fills:
            s["unfilled"] += 1
        for fill in fills:
            if fill == unset or list(fill) in s["fills"]:
                continue
            s["fills"].append(list(fill))
            words = [fill[1]] if fill[0] == "string" and fill[1] is not None else []
            if fill[0] == "native":   # one-string native sources
                words = [ids[0] for ids in native_words.get(fill[1], []) if len(ids) == 1 and ids[0] in out]
            for word in words:
                if [tid, slot] not in out[word].setdefault("fills", []):
                    out[word]["fills"].append([tid, slot])
                if [word, slot] not in out[tid].setdefault("receives", []):
                    out[tid]["receives"].append([word, slot])

    def merge(states: list[dict]) -> dict:
        """Slot values of several paths; a path without a value of the slot adds `unset`."""
        slots = {k for st in states for k in st}
        return {k: list(dict.fromkeys(f for st in states for f in (st.get(k) or [unset]))) for k in slots}

    for walk, items in events.items():
        items.sort(key=lambda x: x[0])
        keys = [k for k, _, _ in items]
        values: dict[int, list] = {}   # slot -> fills the path may hold
        stack: list[dict] = []
        left: dict[tuple, dict] = {}   # called script (its first event) -> slot values it returned with
        pair_prev: list = []   # states of the last call_if
        for i, (_, off, u) in enumerate(items):
            if isinstance(u, Branch) and u.how is None:
                stack.append({"kind": u.kind, "key": (u.order, u.seq), "before": values, "returned": []})
            elif isinstance(u, Branch):
                if not stack:
                    continue
                fr = stack.pop()
                if fr["kind"] == "goto_if":
                    if u.how == "return":   # returns from the called script it lies in
                        caller = next((x for x in reversed(stack) if x["kind"] != "goto_if"), None)
                        if caller is not None:
                            caller["returned"].append(values)
                    values = fr["before"]
                    continue
                own = fr["returned"] + ([values] if u.how in ("return", "join") else [])
                if own and u.how == "return" and fr["key"] not in left:
                    res = merge(own)
                    left[fr["key"]] = {k: v for k, v in res.items() if v != fr["before"].get(k)}
                if fr["kind"] == "call_if_pair" and own and pair_prev:
                    states = pair_prev + own   # one of the two callees ran
                else:
                    states = own + ([fr["before"]] if fr["kind"] in ("call_if", "call_if_pair") or not own else [])
                if fr["kind"] == "call_if":
                    pair_prev = own
                values = merge(states)
            elif isinstance(u, Join):
                for slot_i in range(3):
                    # texts on every path from the code
                    for off2 in sorted(w.flow(u.pc, slot_i)[0]) if u.pc is not None else ():
                        if shows(off2, slot_i):
                            reach(id_of[off2], "{STR_VAR_%d}" % (slot_i + 1), values.get(slot_i, []))
                prev = items[i - 1][2] if i else None
                if isinstance(prev, Branch) and prev.how is None and prev.kind != "goto_if" and u.target in left:
                    # a call of a walked script: the values it returned
                    values = dict(values)
                    for k, v in left[u.target].items():
                        values[k] = list(dict.fromkeys(
                            f for x in v for f in ((values.get(k) or [unset]) if x == unset else [x])))
            elif isinstance(u, BufferFill):
                values = dict(values)
                values[u.slot] = [("string", id_of.get(u.value)) if u.kind == "string" else (u.kind, u.value)]
            else:
                for slot_i in range(3):
                    if shows(off, slot_i):
                        reach(id_of[off], "{STR_VAR_%d}" % (slot_i + 1), values.get(slot_i, []))
    for off, e in by_off.items():
        if "slots" in out[e["id"]] or "{STR_VAR_" in e["text"]:
            script = [r for r in e["refs"] if r["kind"].startswith("script:") and r["kind"] != "script:bufferstring"]
            out[e["id"]]["slot_refs"] = {
                "unwalked": sum(1 for r in script if r["at"] not in met[off]),
                "code": sum(1 for r in e["refs"] if not r["kind"].startswith("script:"))}
    stats = {"maps": ctx.maps, "scripts": ctx.scripts, "buffer_fills": len(ctx.fills),
             "side_walk_texts": sum(len(us) for us in ctx.side_uses.values()),
             "unknown_ops": {f"{k:#x}": v for k, v in sorted(ctx.unknown_ops.items(), key=lambda x: -x[1])[:20]}}
    split_uses(out, inv)
    context_identities(out, {e["id"]: e for e in inv["entries"]}, prof, w)
    return {"schema": "1.0.0", "stats": stats, "strings": out, "maps": map_records}


def discover_context(rom: Rom, prof, inv: dict, window: int = 0x4000):
    """Derive context facts while retaining declared semantics as inputs."""
    from .flow_facts import collect

    return collect("context", _analyze_context(rom, prof, inv, window), inv, prof)


def build_context(rom: Rom, prof, inv: dict, window: int = 0x4000) -> dict:
    """Render the context study from evidence-bearing flow facts."""
    from .flow_facts import project

    result = project(discover_context(rom, prof, inv, window))
    return {key: result[key] for key in ("schema", "stats", "strings", "maps")}
