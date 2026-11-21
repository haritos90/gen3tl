"""Create a language pack and translation workspace from tracked facts."""

from __future__ import annotations

import copy
import json
import re
import shutil
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..web import editorsnapshot, sitecatalog
from ..catalog.catalog import build_entries, load_catalog, never_shown
from ..core.charmap import BASE_CHARS, NAMED_BYTES, PREFIXED_ARG, Charmap
from ..build.font import FontTable
from ..discover.inventory import slot_usage
from .langpack import TOOLKIT_ROOT, LangPack
from ..catalog.po import KEY_ONLY, SOURCE_FIELD, write
from ..catalog.source import digest
from ..check.policy import effective
from ..profile.profile import Profile
from ..project import MANIFEST, Manifest, Registry


class LangInitError(ValueError):
    pass


@dataclass(frozen=True)
class Initialized:
    pack: Path
    workspace: Path
    entries: int
    missing: dict[str, int]
    site: Path | None = None   # the site catalog created


KEY_CLEARS = ("0B", "06", "06", "1A", "06", "06", "06", "19")
SYMBOL_ROWS = (
    ("012345  ", "{CLEAR 0B}0{CLEAR 10}1{CLEAR 10}2{CLEAR 10}3{CLEAR 10}4{CLEAR 10}5"),
    ("6789.-  ", "{CLEAR 0B}6{CLEAR 10}7{CLEAR 10}8{CLEAR 10}9{CLEAR 10}.{CLEAR 11}-"),
    ("!?♂♀/   ", "{CLEAR 0B}!{CLEAR 10}?{CLEAR 10}♂{CLEAR 10}♀{CLEAR 10}/{CLEAR 10} "),
    ("¿¡…“”   ", "{CLEAR 0B}¿{CLEAR 10}¡{CLEAR 10}…{CLEAR 10}“{CLEAR 10}”{CLEAR 10} "),
)


def _literal(value) -> str:
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(key)} = {_literal(child)}" for key, child in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_literal(child) for child in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def _unique(text: str) -> list[str]:
    return list(dict.fromkeys(text))


def _letter_rows(letters: str, code: str) -> list[str]:
    if len(letters) > 32:
        raise LangInitError(f"{code}: language template has more than 32 letters per keyboard")
    return [letters[at:at + 8].ljust(8) for at in range(0, len(letters), 8)] or [" " * 8]


def _display(row: str) -> str:
    return "".join(f"{{CLEAR {clear}}}{char}" for clear, char in zip(KEY_CLEARS, row))


def _keyboard(code: str, upper: str, lower: str, bound: dict[str, str | None]) -> str:
    # the screen opens on keyboards 1 and 0, here Latin
    keyboards = [
        {"name": "a–z", "like": 0, "base": True},
        {"name": "A–Z", "like": 1, "base": True},
        {"name": "symbols", "like": 2},
        {"name": "letters uppercase", "like": 1},
        {"name": "letters lowercase", "like": 0},
    ]
    pages = [
        {"name": "symbols", "keyboard": 2, "look": 0},
        {"name": "A–Z", "keyboard": 1, "look": 1},
        {"name": "a–z", "keyboard": 0, "look": 2},
        {"name": "letters uppercase", "keyboard": 3, "look": 1},
        {"name": "letters lowercase", "keyboard": 4, "look": 2},
    ]
    rows = {}
    for name, letters in (("lower", lower), ("upper", upper)):
        letter_rows = _letter_rows(letters, code) + [" " * 8] * 4
        for index in range(4):
            row = letter_rows[index]
            rows[f"{name}_keys_{index + 1}"] = row
            rows[f"{name}_display_{index + 1}"] = _display(row)
    for index, (keys, display) in enumerate(SYMBOL_ROWS, 1):
        rows[f"symbols_keys_{index}"] = keys
        rows[f"symbols_display_{index}"] = display
    for name, text in bound.items():   # other bound rows keep their English
        if name in rows:
            continue
        if text is None:
            raise LangInitError(f"keyboard row {name}: its bound entry is not in the inventory")
        rows[name] = text
    lines = ['schema = "2.0.0"', f"language = {_literal(code)}",
             f"keyboards = {_literal(keyboards)}", f"pages = {_literal(pages)}", "", "[rows]"]
    lines += [f"{key} = {_literal(value)}" for key, value in rows.items()]
    return "\n".join(lines) + "\n"


def _missing_slots(inv: dict, missing: list[str]) -> dict[str, int]:
    candidates = {byte for byte in range(1, min(PREFIXED_ARG))
                  if byte not in BASE_CHARS and byte not in NAMED_BYTES}
    used = {byte for _, byte in slot_usage(inv, candidates, Charmap(), unverified=True)}
    available = iter(sorted(candidates - used))
    try:
        return {char: next(available) for char in missing}
    except StopIteration as exc:
        raise LangInitError("no unused character slots remain for the language letters") from exc


def _write_pack(root: Path, code: str, profile: Profile, inv: dict) -> dict[str, int]:
    template = json.loads((TOOLKIT_ROOT / "assets/lang/templates.json").read_text(encoding="utf-8"))
    if template.get("schema") != "1.0.0":
        raise LangInitError("unsupported language template schema")
    selected = template["languages"].get(code, template["default"])
    upper, lower = selected["upper"], selected["lower"]
    previews = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"]
    if upper:
        previews += [upper, lower]
    previews += ["0123456789 !?…,."]
    metadata = {"schema": "2.0.0", "code": code, "provider": "manual", "preview_lines": previews,
                "pseudo": {"source": "", "target": "", "padding": "x"}}
    english = {entry["id"]: entry["text"] for entry in inv["entries"]}
    bound = {name: english.get(sid) for name, sid in profile.get("keyboard", {}).get("row_bindings", {}).items()}
    layout = _keyboard(code, upper, lower, bound)
    root.mkdir(parents=True)
    (root / "language.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "keyboard.toml").write_text(layout, encoding="utf-8")
    keys = re.findall(r'^(?:lower|upper|symbols)_keys_\d = "(.*)"$', (root / "keyboard.toml").read_text(), re.MULTILINE)
    chars = _unique("".join(previews) + "".join(keys))
    base = {char: byte for byte, char in BASE_CHARS.items()}
    missing = [char for char in chars if char not in base and char.isalpha()]
    slots = _missing_slots(inv, missing)
    lines = ["# REVIEW: verify every allocated slot against the base fonts.", "char\tbyte\tsource"]
    lines += [f"{char}\t{base[char]:02X}\tbase" for char in chars if char in base]
    lines += [f"{char}\t{slots[char]:02X}\tglyph" for char in missing]
    (root / "charmap.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if missing:
        for font in dict.fromkeys(font.style for font in FontTable.from_profile(profile)):
            content = ["# Empty art slots; draw and review every named glyph."]
            content += [f"# {char}: {unicodedata.name(char, 'UNKNOWN')} at slot {slots[char]:02X}" for char in missing]
            art = root / "fonts" / f"{font}.txt"
            art.parent.mkdir(parents=True, exist_ok=True)
            art.write_text("\n".join(content) + "\n", encoding="utf-8")
    return slots


def _reference(template: dict, section: str, key: str, default=1):
    return template.get("layout", {}).get(section, {}).get(key, default)


def _policy_text(code: str, profile: Profile, template: dict, reference_language: str) -> str:
    identity = {"version": "3.0.0", "profile": profile.name, "language": code, "rom_sha1": profile.sha1}
    lines = ["# Generated policy; review every measurement before translating.", "[policy]"]
    lines += [f"{key} = {_literal(value)}" for key, value in identity.items()]
    lines += ["", "[keyboard]", 'layout = "keyboard.toml"', "", "[qa]",
              "# REVIEW: select token permissions for this language.",
              "optional_tokens = []", "droppable = []", "addable = []", "", "[qa.forbidden]"]
    forbidden = template.get("qa", {}).get("forbidden", {})
    needs = template.get("qa", {}).get("forbidden_needs_source", {})
    lines.append("# REVIEW: translate each English reason placeholder." if forbidden
                 else "# Add language-specific typography rules.")
    for pattern, rule in forbidden.items():
        reason = rule.get("reason") if isinstance(rule, dict) else rule
        selected = {"reason": reason, code: reason}
        if pattern in needs:
            selected["needs_source"] = needs[pattern]
        lines.append(f"{_literal(pattern)} = {_literal(selected)}")
    lines += ["", "[layout]"]
    layout = template.get("layout", {})
    for key, default in (("label_min_width", 60), ("target_width", 196), ("sentence_break", 0.5)):
        value = layout.get(key, default)
        lines += [f"# REVIEW: {reference_language} reference is {value}.", f"{key} = {_literal(value)}"]
    slots = profile.get("policy_slots", {})
    sources = profile.get("layout", {}).get("slot_source", {})
    code_sources = profile.get("layout", {}).get("code_slot_source", {})
    marks = {key: True for key in ("label_min_width", "target_width", "sentence_break")}
    for section in ("window_width", "placeholders", "table_windows", "table_window"):
        lines += ["", f"[layout.{section}]"]
        keys = [key for key in slots.get(section, []) if section != "placeholders" or key not in sources]
        marks[section] = keys
        for key in keys:
            value = _reference(template, section, key)
            lines += [f"# REVIEW: {reference_language} reference is {value} px; measure {code}.",
                      f"{_literal(key)} = {_literal(value)}"]
    lines += ["", "[layout.code_fill]"]
    for name, tokens in slots.get("code_fill", {}).items():
        values = {token: template.get("layout", {}).get("code_fill", {}).get(name, {}).get(token, 1)
                  for token in tokens if token not in code_sources.get(name, {})}
        if not values:
            continue
        marks.setdefault("code_fill", []).append(name)
        reference = template.get("layout", {}).get("code_fill", {}).get(name, {})
        lines += [f"# REVIEW: {reference_language} reference is {_literal(reference or values)}; measure {code}.",
                  f"{_literal(name)} = {_literal(values)}"]
    lines += ["", "[layout.unverified]"]
    lines += [f"{key} = {_literal(value)}" for key, value in marks.items()]
    lines += ["", "[graphics]", "# Translation graphics start empty.", "edit = []", "tilemap = []"]
    return "\n".join(lines) + "\n"


def _snapshot(source: dict, profile: Profile, code: str, inv: dict, ctx: dict, reach: dict,
              catalog: dict, po_root: Path) -> dict:
    if (source.get("schema"), source.get("profile"), source.get("rom_sha1")) != (
            editorsnapshot.SCHEMA, profile.name, profile.sha1):
        raise LangInitError("measurement snapshot schema or profile identity differs")
    inventory = []
    for entry in inv["entries"]:
        row = {key: entry[key] for key in editorsnapshot.INVENTORY if key not in ("refs", "source_sha256")}
        row["refs"] = [[ref["at"], ref["kind"]] for ref in entry["refs"]]
        row["source_sha256"] = digest(entry["text"])
        inventory.append(row)
    context = {}
    for sid, record in ctx["strings"].items():
        uses = [{key: use.get(key) for key in ("event", "box", "at", "trainer_role")}
                for use in record.get("uses", [])]
        slots, refs, fills = record.get("slots", {}), record.get("slot_refs", {}), record.get("fills", [])
        if slots or refs or fills or any(use["event"] == "sign" or use["box"] == "sign"
                                        or use["trainer_role"] for use in uses):
            context[sid] = {"uses": uses, "slots": slots, "fills": fills,
                            "slot_refs": {key: refs.get(key, 0) for key in ("unwalked", "code")}}
    study = {sid: {"class": record["class"], "group": record.get("group", "script"),
                   "evidence": [{key: item.get(key, "") for key in ("kind", "at")}
                                for item in record.get("evidence", [])[-1:]]}
             for sid, record in reach["strings"].items()}
    # reference-language facts until it has its own snapshot
    rows = {sid: copy.deepcopy(source["source"][sid]) for sid in sorted(catalog) if sid in source["source"]}
    doc = {"schema": editorsnapshot.SCHEMA, "profile": profile.name, "language": code,
           "rom_sha1": profile.sha1, "source_sha256": editorsnapshot.source_digest(catalog, po_root, rows),
           "configuration_sha256": editorsnapshot.configuration(profile), "fonts": copy.deepcopy(source["fonts"]),
           "inventory": inventory, "context": context, "reach": study, "notes": copy.deepcopy(source["notes"]),
           "records": copy.deepcopy(source["records"]), "source": rows}
    from ..check.fillsources import graph
    doc["records"]["fill_alphabet"] = graph(profile, inv, catalog)["alphabet"]
    doc["facts_sha256"] = editorsnapshot.digest(doc)
    editorsnapshot.validate(doc, profile, code, catalog, po_root)
    return doc


def _source(registry: Registry, profile: Profile):
    selected = next((project for project in registry.projects
                     if project.profile == profile.name and project.language == registry.default_language
                     and project.stage == "translation"), None)
    root = selected.root if selected else registry.root
    policy = selected.policy if selected else root / "translation/policy.toml"
    snapshot = selected.snapshot if selected else root / "translation/editor-inputs.json"
    return root, policy, snapshot, bool(selected and selected.game)


def _copy_graphics_inputs(source: Path, workspace: Path, profile: Profile) -> None:
    for key in ("inventory", "previews"):
        value = profile.get("graphics", {}).get(key)
        if not value:
            continue
        relative = Path(value)
        origin = (source / relative).resolve()
        target = (workspace / relative).resolve()
        if relative.is_absolute() or not origin.is_relative_to(source.resolve()) or not target.is_relative_to(workspace.resolve()):
            raise LangInitError(f"graphics {key} must stay inside the project")
        if not origin.is_file():
            raise LangInitError(f"missing graphics {key}: {origin}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)


def create(code: str, profile: Profile, registry: Registry, *, pack_root: Path | None = None,
           policy_template: Path | None = None, snapshot_path: Path | None = None) -> Initialized:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", code):
        raise LangInitError("invalid language code")
    if any(project.profile == profile.name and project.language == code for project in registry.projects):
        raise LangInitError(f"project already registered: {profile.name}/{code}")
    source_root, default_policy, default_snapshot, game = _source(registry, profile)
    pack_root = pack_root or TOOLKIT_ROOT / "assets/lang"
    pack = pack_root / code
    workspace = source_root / "translation" / code if game else registry.root / "translation/projects" / profile.name / code
    folder = workspace if game else workspace / "translation"
    project_root = source_root if game else workspace
    snapshot_target = source_root / "source/snapshot" / f"{code}.snapshot.json" if game else folder / "editor-inputs.json"
    if pack.exists():
        raise LangInitError(f"language pack already exists: {pack}")
    if workspace.exists():
        raise LangInitError(f"project workspace already exists: {workspace}")
    documents = {}
    for name in ("inventory", "context", "reach"):
        path = source_root / "build" / f"{name}.json"
        if not path.is_file():
            raise LangInitError(f"missing tracked {name} facts: {path}")
        documents[name] = json.loads(path.read_text(encoding="utf-8"))
        if (documents[name].get("profile"), documents[name].get("rom_sha1")) != (profile.name, profile.sha1):
            raise LangInitError(f"{path}: profile or ROM identity differs")
    template_path = policy_template or default_policy
    snapshot_source = snapshot_path or default_snapshot
    try:
        with template_path.open("rb") as stream:
            template = tomllib.load(stream)
        source_snapshot = editorsnapshot.load(snapshot_source)
        language_template = json.loads((TOOLKIT_ROOT / "assets/lang/templates.json").read_text(encoding="utf-8"))
    except OSError as exc:
        raise LangInitError(f"missing language template or measurement snapshot: {exc}") from exc
    created_pack = created_workspace = registered = False
    site = None
    registry_path = registry.root / "projects.toml"
    original_registry = registry_path.read_bytes()
    manifest_path = source_root / MANIFEST
    original_manifest = manifest_path.read_bytes() if game else None
    try:
        missing = _write_pack(pack, code, profile, documents["inventory"])
        created_pack = True
        LangPack.load(code, root=pack_root)
        workspace.mkdir(parents=True)
        created_workspace = True
        for name, document in documents.items():
            if game:   # the languages share build/
                break
            target = workspace / "build" / f"{name}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(document, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        policy_path = folder / "policy.toml"
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(_policy_text(code, profile, template, language_template["reference_language"]),
                               encoding="utf-8")
        (folder / "release.toml").write_text("# Initial translation version.\nversion = \"0.0.0\"\n",
                                             encoding="utf-8")
        if not game:   # a game project shares source/graphics.toml
            _copy_graphics_inputs(source_root, workspace, profile)
        selected = effective(profile, code, policy_path, project_root, pack_root=pack_root)
        inv, ctx, reach = documents["inventory"], documents["context"], documents["reach"]
        never = never_shown(reach, profile.get("reach", {}).get("never_shown", []),
                            profile.get("reach", {}).get("observed", []))
        from ..catalog import keys
        groups = build_entries(inv, ctx, Charmap(),
                               lambda byte: source_snapshot["fonts"][profile["font_style"]["style"]]["widths"][byte],
                               profile.name, never, keys.load(profile.sha1, registry.root))
        entries = [entry for group in groups.values() for entry in group]
        po_root = folder / "po"
        header = {"Project-Id-Version": profile.name, "Language": code, "Content-Type": "text/plain; charset=UTF-8"}
        if game:   # a game project holds its catalogs by key
            header[SOURCE_FIELD] = KEY_ONLY
        write(po_root / "catalog.po", entries, header)
        catalog = load_catalog(po_root, selected, entries)
        snapshot = _snapshot(source_snapshot, selected, code, inv, ctx, reach, catalog, po_root)
        editorsnapshot.write(snapshot_target, snapshot)
        if sitecatalog.CODE.fullmatch(code):
            site = sitecatalog.seed(code, TOOLKIT_ROOT / "web/i18n")
        relative = project_root.relative_to(registry.root).as_posix()
        policy_relative = policy_path.relative_to(project_root).as_posix()
        block = (f'\n[[project]]\nprofile = "{profile.name}"\nlanguage = "{code}"\n'
                 f'root = "{relative}"\npolicy = "{policy_relative}"\nstage = "translation"\n')
        existing = next((item for item in tomllib.loads(original_registry.decode()).get("project", [])
                         if item["profile"] == profile.name), {})
        for field in ("profile_path", "dataset_path"):
            if field in existing:
                block += f'{field} = {json.dumps(existing[field])}\n'
        with registry_path.open("a", encoding="utf-8") as stream:
            stream.write(block)
        registered = True
        if game:
            manifest = Manifest.read(manifest_path)
            manifest_path.write_text(Manifest(manifest_path, manifest.profile, manifest.rom_sha1, manifest.toolkit,
                                              manifest.languages + (code,), manifest.default_language or code).text(),
                                     encoding="utf-8")
        project = Registry.load(registry.root).resolve(profile, code)
        project.validate(selected)
    except Exception:
        if registered:
            registry_path.write_bytes(original_registry)
        if original_manifest is not None:
            manifest_path.write_bytes(original_manifest)
        if created_workspace:
            shutil.rmtree(workspace)
            if game and snapshot_target.exists():
                from ..web.snapshotstore import remove
                remove(snapshot_target)
        if created_pack:
            shutil.rmtree(pack)
        if site:
            site.unlink(missing_ok=True)
        raise
    return Initialized(pack, workspace, len(entries), missing, site)
