"""Validated project policy over immutable ROM facts."""

from __future__ import annotations

import copy
import json
import re
import tomllib
from pathlib import Path

from ..core.notes import has_prose
from ..profile.profile import Profile


class PolicyError(ValueError):
    pass


def _toml(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_toml(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def _replace_section(text, name, value):
    """Replace one root value; verify parsed equality afterwards."""
    lines, kept = text.splitlines(keepends=True), []
    selected, root, index = False, True, 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("["):
            try:
                header = tomllib.loads(stripped)
            except tomllib.TOMLDecodeError:
                header = {}
            selected = set(header) == {name}
            root = False
        if root and re.match(rf'\s*(?:{name}|"{name}"|\'{name}\')\s*=', line):
            chunk = line
            while True:
                try:
                    tomllib.loads(chunk)
                    break
                except tomllib.TOMLDecodeError:
                    index += 1
                    if index >= len(lines):
                        raise PolicyError(f"{name}: unsupported policy representation")
                    chunk += lines[index]
        elif not selected or not stripped or stripped.startswith("#"):
            kept.append(line)
        index += 1
    return f"{name} = {_toml(value)}\n" + "".join(kept)


def patch_rules(text: str, patches: list[dict]) -> str:
    """Apply resolved controls without changing unrelated policy values."""
    before = tomllib.loads(text)
    wanted = copy.deepcopy(before)
    sections, seen = set(), set()
    for patch in patches:
        kind, name, value = patch["kind"], patch["name"], patch["value"]
        if (kind, name) in seen:
            raise PolicyError("duplicate policy capability")
        seen.add((kind, name))
        if kind == "code_replacement":
            section = "code_replacements"
            selected = wanted.setdefault(section, {})
            current = {"enabled": name in selected, "fields": selected.get(name, {})}
            if current != patch["base"]:
                raise PolicyError("policy changed after rule validation")
            if value["enabled"]:
                selected[name] = copy.deepcopy(value["fields"])
            else:
                selected.pop(name, None)
        elif kind == "text_prefix":
            section = "text_prefixes"
            selected = wanted.setdefault(section, [])
            if {"enabled": name in selected} != patch["base"]:
                raise PolicyError("policy changed after rule validation")
            if value["enabled"] and name not in selected:
                selected.append(name)
            elif not value["enabled"] and name in selected:
                selected.remove(name)
        else:
            raise PolicyError("unsupported policy capability")
        sections.add(section)
    for section in sorted(sections):
        text = _replace_section(text, section, wanted[section])
    if tomllib.loads(text) != wanted:
        raise PolicyError("rule patch would change unrelated policy values")
    return text


def _keys(value, allowed, where):
    if not isinstance(value, dict):
        raise PolicyError(f"{where}: expected a table")
    unknown = value.keys() - set(allowed)
    if unknown:
        raise PolicyError(f"{where}: unsupported keys {', '.join(sorted(unknown))}")


def _number(value, where, minimum=0):
    if type(value) is not int or value < minimum:
        raise PolicyError(f"{where}: expected an integer >= {minimum}")


def _strings(value, where):
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise PolicyError(f"{where}: expected a string array")
    if len(set(value)) != len(value):
        raise PolicyError(f"{where}: duplicate values")


def _widths(value, slots, where, optional=()):
    _keys(value, slots, where)
    missing = set(slots) - value.keys() - set(optional)
    if missing:
        raise PolicyError(f"{where}: missing policy slots {sorted(missing)}")
    for key, width in value.items():
        _number(width, f"{where}.{key}")


def _asset(value, root, where):
    if not isinstance(value, str) or not value:
        raise PolicyError(f"{where}: expected a project-relative asset path")
    if Path(value).is_absolute() or not (root / value).resolve().is_relative_to(root.resolve()):
        raise PolicyError(f"{where}: asset must stay inside the project")


def effective(profile: Profile, language: str, path: Path, root: Path, *,
              pack_root: Path | None = None, notes_path: Path | None = None) -> Profile:
    """Return a project-bound copy; never mutate the ROM profile."""
    try:
        with path.open("rb") as stream:
            doc = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"cannot read language policy {path}: {exc}") from exc
    _keys(doc, ("policy", "keyboard", "qa", "layout", "graphics", "text_prefixes", "code_replacements"), str(path))
    identity = doc.get("policy", {})
    version = identity.get("version")
    if version not in ("2.0.0", "3.0.0"):
        raise PolicyError(f"{path}: policy identity has an unknown version")
    expected = {"version": version, "profile": profile.name, "language": language, "rom_sha1": profile.sha1}
    if identity != expected:
        raise PolicyError(f"{path}: policy identity must match {expected}")
    data = copy.deepcopy(profile.data)
    if notes_path is not None and notes_path.is_file():
        from ..profile import schema
        # A profile built in memory names no files.
        source = profile.path if profile.path.exists() else Profile.by_name(profile.name).path
        schema.add_language_prose(data, schema.language_notes(source, notes_path, language), str(notes_path))
    from ..catalog import keys
    aliases = keys.load(profile.sha1)
    keys.entry_ids(data, aliases)   # project data may name entries by key
    keys.policy_entry_ids(doc, aliases)
    if data.get("qa") or any(k in data.get("keyboard", {}) for k in ("pages", "keyboards", "rows")):
        raise PolicyError(f"{profile.name}: move embedded language rules into the project policy")
    graphics = data.get("graphics", {})
    if graphics.get("edit") or any(t.get("cells") for t in graphics.get("tilemap", [])):
        raise PolicyError(f"{profile.name}: move translated graphics into the project policy")
    _keyboard(doc.get("keyboard", {}), data, language, pack_root)
    _qa(doc.get("qa", {}), data)
    _layout(doc.get("layout", {}), data)
    _graphics(doc.get("graphics", {}), data, root)
    from ..build.textprefix import configure
    try:
        configure(data, doc.get("text_prefixes", []))
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyError(f"text_prefixes: {exc}") from exc
    from ..build.codereplace import configure as select_code
    try:
        select_code(data, doc.get("code_replacements", {}))
    except ValueError as exc:
        raise PolicyError(f"code_replacements: {exc}") from exc
    result = Profile(data, profile.path)
    result.stage = "translation"
    result.language = language
    result.policy_path = path
    result._project_root = root
    return result


def _keyboard(policy, data, code, pack_root=None):
    _keys(policy, ("layout",), "keyboard")
    base = data.get("keyboard")
    if base is None:
        if policy:
            raise PolicyError("keyboard: ROM profile has no keyboard capability")
        return
    from ..build.keyboard import load_layout, KeyboardError
    try:
        layout = (load_layout(code, policy.get("layout"), base) if pack_root is None
                  else load_layout(code, policy.get("layout"), base, pack_root))
        base.update(layout)
    except KeyboardError as exc:
        raise PolicyError(str(exc)) from exc


SITE_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)?")   # a code of the site catalog


def _forbidden(policy) -> tuple[dict, dict, dict]:
    """[qa.forbidden]: patterns, reason texts by language, and the needs_source flags."""
    _keys(policy, policy, "qa.forbidden")
    forbidden, reasons, needs_source = {}, {}, {}
    for pattern, rule in policy.items():
        texts = {}
        if isinstance(rule, dict):
            rule = dict(rule)
            reason = rule.pop("reason", None)
            if "needs_source" in rule:
                value = rule.pop("needs_source")
                if type(value) is not bool:
                    raise PolicyError(f"qa.forbidden: {pattern!r}: needs_source expected a boolean")
                needs_source[pattern] = value
            if any(not SITE_LANGUAGE.fullmatch(code) for code in rule):
                raise PolicyError(f"qa.forbidden: {pattern!r}: expected reason and language codes")
            texts = rule
        else:
            reason = rule
        if not isinstance(reason, str) or not reason or any(not isinstance(t, str) or not t for t in texts.values()):
            raise PolicyError("qa.forbidden: expected explanations")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise PolicyError(f"qa.forbidden: {exc}") from exc
        known = reasons.get(reason, {})
        if any(known.get(code, text) != text for code, text in texts.items()):
            raise PolicyError(f"qa.forbidden: {reason!r}: two texts in one language")
        if texts:
            reasons[reason] = {**known, **texts}
        forbidden[pattern] = reason
    return forbidden, reasons, needs_source


def _qa(policy, data):
    _keys(policy, ("optional_tokens", "forbidden", "droppable", "addable"), "qa")
    rules = {}
    optional = policy.get("optional_tokens", [])
    _strings(optional, "qa.optional_tokens")
    if any(not re.fullmatch(r"\{[^{}]+\}", t) for t in optional):
        raise PolicyError("qa.optional_tokens: expected brace tokens")
    rules["optional_tokens"] = optional
    rules["forbidden"], reasons, needs_source = _forbidden(policy.get("forbidden", {}))
    if needs_source:
        rules["forbidden_needs_source"] = needs_source
    if reasons:
        rules["reasons"] = reasons
    for kind in ("droppable", "addable"):
        selected = policy.get(kind, [])
        if not isinstance(selected, list):
            raise PolicyError(f"qa.{kind}: expected an array")
        capabilities = data.get("text_controls", {}).get(kind, {})
        rules[kind], seen = [], set()
        for selection in selected:
            ids = None
            if isinstance(selection, str):
                name = selection
            else:
                _keys(selection, ("capability", "ids"), f"qa.{kind}")
                name, ids = selection.get("capability"), selection.get("ids")
                _strings(ids, f"qa.{kind}.ids")
                if not ids:
                    raise PolicyError(f"qa.{kind}: empty entry selection")
            if not isinstance(name, str) or name not in capabilities or name in seen:
                raise PolicyError(f"qa.{kind}: unknown or duplicate ROM capability")
            seen.add(name)
            rule = copy.deepcopy(capabilities[name])
            if ids is not None:
                if set(ids) - set(rule["ids"]):
                    raise PolicyError(f"qa.{kind}: entries outside the ROM capability")
                rule["ids"] = ids
            rules[kind].append(rule)
    data["qa"] = rules


SCALARS = ("label_min_width", "target_width", "sentence_break")
MAPS = ("window_width", "placeholders", "table_windows", "table_window", "code_fill")


def _unverified(marks, policy, slots):
    """Name the values estimated without this language's measurements."""
    _keys(marks, (*SCALARS, *MAPS), "layout.unverified")
    result = {}
    for key, value in marks.items():
        where = f"layout.unverified.{key}"
        if key in SCALARS:
            if value is not True or key not in policy:
                raise PolicyError(f"{where}: mark a configured value with true")
            result[key] = True
            continue
        _strings(value, where)
        unknown = set(value) - set(slots.get(key, []))
        if unknown:
            raise PolicyError(f"{where}: unknown policy slots {sorted(unknown)}")
        result[key] = list(value)
    return result


def _layout(policy, data):
    _keys(policy, (*SCALARS, *MAPS, "unverified", "slot_override", "code_slot_override"), "layout")
    layout = data.setdefault("layout", {})
    slots = data.get("policy_slots", {})
    data["policy_unverified"] = _unverified(policy.get("unverified", {}), policy, slots)
    for key in SCALARS:
        if key in policy:
            if key in layout:
                raise PolicyError(f"layout.{key}: language value remains in ROM profile")
            if key == "sentence_break":
                if type(policy[key]) not in (int, float) or not 0 <= policy[key] <= 1:
                    raise PolicyError("layout.sentence_break: expected a fraction between 0 and 1")
            else:
                _number(policy[key], f"layout.{key}", 1)
            layout[key] = policy[key]
    for key in ("window_width", "placeholders"):
        values = policy.get(key, {})
        _widths(values, slots.get(key, []), f"layout.{key}",
                layout.get("slot_source", {}) if key == "placeholders" else ())
        target = layout.setdefault(key, {})
        if target.keys() & values.keys():
            raise PolicyError(f"layout.{key}: cannot override ROM geometry")
        target.update(values)
    for section in ("table_windows", "table_window"):
        values = policy.get(section, {})
        _widths(values, slots.get(section, []), f"layout.{section}")
        records = data.get("table", []) if section == "table_windows" else layout.get("table_window", [])
        by_name = {row["name"]: row for row in records}
        for name, width in values.items():
            if name not in by_name:
                raise PolicyError(f"layout.{section}: unknown ROM rule {name}")
            row = by_name[name]
            target = row["windows"][0] if section == "table_windows" else row
            if "width" in target:
                raise PolicyError(f"layout.{section}.{name}: cannot override ROM geometry")
            target["width"] = width
            target["policy_width"] = True
    fills = policy.get("code_fill", {})
    wanted = slots.get("code_fill", {})
    _keys(fills, wanted, "layout.code_fill")
    optional = layout.get("code_slot_source", {})
    if wanted.keys() - fills.keys() - {k for k, v in optional.items() if set(v) == set(wanted.get(k, []))}:
        raise PolicyError("layout.code_fill: missing policy rules")
    by_name = {row.get("name"): row for row in layout.get("code_fill", [])}
    for name, values in fills.items():
        _widths(values, wanted[name], f"layout.code_fill.{name}", optional.get(name, {}))
        if name not in by_name:
            raise PolicyError(f"layout.code_fill: unknown ROM rule {name}")
        for token, width in values.items():
            target = by_name[name]["slots"][token]
            if "width" in target:
                raise PolicyError(f"layout.code_fill.{name}: cannot override ROM geometry")
            target["width"] = width

    for section, bindings in (("slot_override", layout.get("slot_source", {})),
                              ("code_slot_override", optional)):
        values = policy.get(section, {})
        _keys(values, bindings, "layout." + section)
        groups = [(values, bindings)] if section == "slot_override" else [
            (tokens, bindings[name]) for name, tokens in values.items()]
        for tokens, sources in groups:
            _keys(tokens, sources, "layout." + section)
            for token, override in tokens.items():
                _keys(override, ("width", "source", "reason"), "layout." + section)
                if (override.get("source") != sources[token] or not isinstance(override.get("reason"), str)
                        or not override["reason"].strip()):
                    raise PolicyError("substitution override requires its source and a reason")
                _number(override.get("width"), "layout." + section)
        layout[section] = copy.deepcopy(values)
    marks = data["policy_unverified"]
    if "placeholders" in marks:
        marks["placeholders"] = [s for s in marks["placeholders"] if s not in layout.get("slot_source", {})]
    if "code_fill" in marks:
        marks["code_fill"] = [name for name in marks["code_fill"]
                              if set(wanted[name]) - optional.get(name, {}).keys()]


def _graphics(policy, data, root):
    _keys(policy, ("edit", "tilemap"), "graphics")
    base = data.setdefault("graphics", {})
    edits = policy.get("edit", [])
    if not isinstance(edits, list):
        raise PolicyError("graphics.edit: expected an array")
    seen = set()
    for row in edits:
        _keys(row, ("id", "png", "label", "form", "areas", "tiles"), "graphics.edit")
        gid = row.get("id")
        if not isinstance(gid, str) or gid in seen:
            raise PolicyError("graphics.edit: missing or duplicate graphic id")
        seen.add(gid)
        if ("png" in row) == ("label" in row):
            raise PolicyError(f"graphics.edit.{gid}: choose exactly one image source")
        source = "label" if "label" in row else "png"
        _asset(row[source], root, f"graphics.edit.{gid}.{source}")
        if row.get("form", "sheet") not in ("areas", "sheet"):
            raise PolicyError(f"graphics.edit.{gid}: invalid form")
        areas = row.get("areas", [])
        if not isinstance(areas, list):
            raise PolicyError(f"graphics.edit.{gid}: invalid areas")
        for rect in areas:
            if not isinstance(rect, list) or len(rect) != 4:
                raise PolicyError(f"graphics.edit.{gid}: expected four-value rectangles")
            for value in rect:
                _number(value, f"graphics.edit.{gid}.areas")
            if min(rect[2:]) == 0:
                raise PolicyError(f"graphics.edit.{gid}: empty rectangle")
        facts = base.get("edit_facts", {}).get(gid, {})
        if "tiles" in row:
            _number(row["tiles"], f"graphics.edit.{gid}.tiles", 1)
            if row["tiles"] > facts.get("max_tiles", 0):
                raise PolicyError(f"graphics.edit.{gid}: exceeds verified ROM tile capacity")
        if has_prose(facts.get("evidence")):
            row["evidence"] = facts["evidence"]
    base["edit"] = edits
    maps = {row["name"]: row for row in base.get("tilemap", [])}
    for row in maps.values():
        row["cells"] = []
    seen = set()
    patches = policy.get("tilemap", [])
    if not isinstance(patches, list):
        raise PolicyError("graphics.tilemap: expected an array")
    for patch in patches:
        _keys(patch, ("name", "cells"), "graphics.tilemap")
        name = patch.get("name")
        if not isinstance(name, str) or name not in maps or name in seen:
            raise PolicyError("graphics.tilemap: unknown or duplicate ROM tilemap")
        seen.add(name)
        cells = patch.get("cells", [])
        if not isinstance(cells, list):
            raise PolicyError("graphics.tilemap.cells: expected an array")
        positions = set()
        for cell in cells:
            _keys(cell, ("row", "column", "tile", "hflip", "vflip"), "graphics.tilemap.cells")
            for key in ("row", "column", "tile"):
                _number(cell.get(key), f"graphics.tilemap.{key}")
            for key in ("hflip", "vflip"):
                if key in cell and type(cell[key]) is not bool:
                    raise PolicyError(f"graphics.tilemap.{key}: expected a boolean")
            row = maps[name]
            pos = (cell["row"], cell["column"])
            if (pos in positions or cell["column"] >= row["width"]
                    or cell["row"] * row["width"] + cell["column"] >= row["size"] // 2
                    or cell["tile"] > 1023):
                raise PolicyError(f"graphics.tilemap.{name}: invalid cell")
            positions.add(pos)
        maps[name]["cells"] = cells
    base["tilemap"] = [row for row in maps.values() if row["cells"]]
