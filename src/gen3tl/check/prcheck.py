"""ROM-free pull request checks and approximate box previews."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from ..core.terminal import line
from ..web import editorsnapshot
from . import qa
from ..catalog.catalog import load_catalog
from ..core.charmap import BASE_CHARS, NAMED_BYTES, Charmap, CharmapError
from ..web.editordata import fonts_data, window
from ..build.font import INK, OUTSIDE, SHADOW
from ..lang import language
from ..lang.langpack import load_styles
from ..catalog.po import read
from ..build.release import load as load_version
from .render import Canvas, PALETTE, draw_text, placeholder_px, write_png
from .layout import entry_measure

WARNING = "approximate: stand-in glyphs"

LETTERS = {
    "A": ".##./#..#/#..#/####/#..#/#..#/#..#", "B": "###./#..#/#..#/###./#..#/#..#/###.",
    "C": ".###/#.../#.../#.../#.../#.../.###", "D": "###./#..#/#..#/#..#/#..#/#..#/###.",
    "E": "####/#.../#.../###./#.../#.../####", "F": "####/#.../#.../###./#.../#.../#...",
    "G": ".###/#.../#.../#.##/#..#/#..#/.###", "H": "#..#/#..#/#..#/####/#..#/#..#/#..#",
    "I": "###/.#./.#./.#./.#./.#./###", "J": "..##/...#/...#/...#/...#/#..#/.##.",
    "K": "#..#/#..#/#.#./##../#.#./#..#/#..#", "L": "#.../#.../#.../#.../#.../#.../####",
    "M": "#..#/####/####/#..#/#..#/#..#/#..#", "N": "#..#/##.#/##.#/#.##/#.##/#..#/#..#",
    "O": ".##./#..#/#..#/#..#/#..#/#..#/.##.", "P": "###./#..#/#..#/###./#.../#.../#...",
    "Q": ".##./#..#/#..#/#..#/#..#/#.##/.###", "R": "###./#..#/#..#/###./#.#./#..#/#..#",
    "S": ".###/#.../#.../.##./...#/...#/###.", "T": "####/.#../.#../.#../.#../.#../.#..",
    "U": "#..#/#..#/#..#/#..#/#..#/#..#/.##.", "V": "#..#/#..#/#..#/#..#/#..#/.##./.##.",
    "W": "#..#/#..#/#..#/#..#/####/####/#..#", "X": "#..#/#..#/.##./.##./.##./#..#/#..#",
    "Y": "#..#/#..#/.##./.#../.#../.#../.#..", "Z": "####/...#/..#./.#../#.../#.../####",
    "a": ".##./...#/.###/#..#/.###", "b": "#.../#.../###./#..#/#..#/#..#/###.",
    "c": ".###/#.../#.../#.../.###", "d": "...#/...#/.###/#..#/#..#/#..#/.###",
    "e": ".##./#..#/####/#.../.###", "f": "..##/.#../###./.#../.#../.#../.#..",
    "g": ".###/#..#/#..#/.###/...#/...#/.##.", "h": "#.../#.../###./#..#/#..#/#..#/#..#",
    "i": "#/./#/#/#/#/#", "j": "..#/.../..#/..#/..#/..#/..#/#.#/.#.",
    "k": "#.../#.../#..#/#.#./##../#.#./#..#", "l": "## /.# /.# /.# /.# /.# /..#",
    "m": "####/#.##/#..#/#..#/#..#", "n": "###./#..#/#..#/#..#/#..#",
    "o": ".##./#..#/#..#/#..#/.##.", "p": "###./#..#/#..#/#..#/###./#.../#...",
    "q": ".###/#..#/#..#/#..#/.###/...#/...#", "r": "#.##/##../#.../#.../#...",
    "s": ".###/#.../.##./...#/###.", "t": ".#../.#../###./.#../.#../.#../..##",
    "u": "#..#/#..#/#..#/#..#/.###", "v": "#..#/#..#/#..#/.##./.##.",
    "w": "#..#/#..#/#..#/#.##/####", "x": "#..#/.##./.##./.##./#..#",
    "y": "#..#/#..#/#..#/.###/...#/#..#/.##.", "z": "####/..#./.#../#.../####",
}
DIGITS = {
    "0": ".##./#..#/#.##/##.#/#..#/#..#/.##.", "1": ".#../##../.#../.#../.#../.#../###.",
    "2": ".##./#..#/...#/..#./.#../#.../####", "3": "###./...#/...#/.##./...#/...#/###.",
    "4": "..#./.##./#.#./#.#./####/..#./..#.", "5": "####/#.../#.../###./...#/...#/###.",
    "6": ".##./#.../#.../###./#..#/#..#/.##.", "7": "####/...#/..#./..#./.#../.#../.#..",
    "8": ".##./#..#/#..#/.##./#..#/#..#/.##.", "9": ".##./#..#/#..#/.###/...#/...#/.##.",
}
SYMBOLS = {
    " ": ("", 10), ".": ("#", 10), ",": (".#/.#/#.", 10), ":": ("#/././#", 6),
    ";": (".#/../../.#/.#/#.", 6), "!": ("#/#/#/#/#/./#", 4),
    "?": (".##./#..#/...#/..#./.#../..../.#..", 4), "-": ("####", 7), "·": ("#", 7),
    "…": ("#.#.#", 10), "+": ("..#../..#../#####/..#../..#..", 5), "=": ("####/..../####", 6),
    "/": ("...#/...#/..#./..#./.#../.#../#...", 4), "(": ("..#/.#./#../#../#../.#./..#", 4),
    ")": ("#../.#./..#/..#/..#/.#./#..", 4), "<": ("..#/.#./#../.#./..#", 5),
    ">": ("#../.#./..#/.#./#..", 5), "“": (".#.#/#.#./#.#.", 3), "”": (".#.#/.#.#/#.#.", 3),
    "‘": (".#/#./#.", 3), "’": (".#/.#/#.", 3), "×": ("#..#/.##./.##./#..#", 6),
    "▶": ("#.../##../###./####/###./##../#...", 4), "&": (".##./#..#/.##./.#../#.#./#..#/.###", 4),
    "%": ("#..#/..#./..#./.#../.#../#.../#..#", 4), "¥": ("#..#/#..#/.##./####/.#../####/.#..", 4),
    "♂": (".###/..##/.#.#/.##./#..#/#..#/.##.", 4), "♀": (".##./#..#/#..#/.##./.#../###./.#..", 4),
    "º": (".##/#.#/.##", 3), "ª": (".##/#.#/.##", 3),
    "↑": ("..#../.###./#.#.#/..#../..#..", 5), "↓": ("..#../..#../#.#.#/.###./..#..", 5),
    "←": ("..#../.#.../#####/.#.../..#..", 5), "→": ("..#../...#./#####/...#./..#..", 5),
    "¿": (".#../..../.#../#.../#.../#..#/.##.", 4), "¡": ("#/./#/#/#/#/#", 4),
    "ß": (".##./#..#/#.#./#.#./#..#/#..#/#.#.", 4), "Œ": (".####/#.#../#.#../#.##./#.#../#.#../.####", 4),
    "œ": (".####/#.#.#/#.###/#.#../.####", 6),
}
NAMED = {"UP_ARROW": "↑", "DOWN_ARROW": "↓", "LEFT_ARROW": "←", "RIGHT_ARROW": "→"}
LIGATURE = {"LV": "Lv", "PK": "PK", "MN": "MN", "SUPER_ER": "er", "SUPER_E": "e", "SUPER_RE": "re",
            "POKEBLOCK_PO": "PO", "POKEBLOCK_KE": "KE", "POKEBLOCK_BL": "BL", "POKEBLOCK_OC": "OC",
            "POKEBLOCK_K": "K"}
MINI = {"L": "#./#./##", "v": "#.#/#.#/.#.", "P": "##/#.#/##/#.", "K": "#.#/##./#.#",
        "M": "#.#/###/#.#", "N": "#.#/###/#.#", "e": "##/##/#./##", "r": "##/#./#.",
        "O": "##/#.#/##", "E": "##/#./##/#./##", "B": "##/#.#/##/#.#/##", "C": "##/#./##"}
ACCENTS = {"̀": "#./.#", "́": ".#/#.", "̂": ".#./#.#", "̈": "#.#", "̃": ".#.#/#.#.", "̧": ".#/#."}


def stand_in(text: str, width: int, small: bool = False) -> list[list[int]]:
    """Project-drawn 16x16 glyph matching the page's stand-in."""
    px = [[OUTSIDE] * 16 for _ in range(16)]
    room = max(1, min(15, width - 1))
    key = text
    decomposed = unicodedata.normalize("NFD", key)
    base, accent = decomposed[:1], decomposed[1:2]
    if len(key) > 1 and key in LIGATURE.values():
        parts = [[*r] for r in (MINI.get(ch, MINI["E"]).split("/") for ch in key)]
        height = max(map(len, parts))
        rows = [".".join("".join(part[y]) if y < len(part) else "." * len(part[0])
                         for part in parts) for y in range(height)]
        top = 10 - height + 1
    elif base in LETTERS or base in DIGITS:
        rows = (LETTERS.get(base) or DIGITS[base]).split("/")
        if accent and base == "i":
            rows[0] = "."
        top = 11 - len(rows) + (2 if base in "gpqy" else 0)
        if base == "j":
            top = 4
        if not small and (base.isupper() or base.isdigit()):
            rows.insert(3, rows[3])
            top -= 1
    else:
        source, top = SYMBOLS.get(key, SYMBOLS["?"])
        rows = source.split("/")
    shift = -1 if small else 0

    def put(x, y):
        if 0 <= x < room and 0 <= y < 15:
            px[y][x] = INK

    source_width = max((len(row) for row in rows), default=1)
    scale = min(1, room / max(1, source_width))
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            if value == "#":
                put(int(x * scale), top + shift + y)
    if accent in ACCENTS:
        for y, row in enumerate(ACCENTS[accent].split("/")):
            for x, value in enumerate(row):
                if value == "#":
                    put(x, (12 if accent == "̧" else top - 3) + shift + y)
    for y in range(14, -1, -1):
        for x in range(room - 1, -1, -1):
            if px[y][x] != INK:
                continue
            for dx, dy in ((1, 0), (0, 1), (1, 1)):
                if px[y + dy][x + dx] != INK:
                    px[y + dy][x + dx] = SHADOW
    return px


class StandInGlyphs:
    """Pack art where present, project-drawn glyphs elsewhere."""

    def __init__(self, pack, widths: list[int], small: bool = False):
        self.pack = pack
        self.stand_ins = language.load(pack.code).stand_ins()
        self.slots = pack.glyph_slots()
        self.copies = pack.copy_slots()
        self.widths = widths
        self.small = small
        self.cache = {}
        self.names = {byte: char for byte, char in BASE_CHARS.items()}
        self.names.update({byte: char for char, byte in pack.chars.items()})
        self.names.update({byte: NAMED.get(name, LIGATURE.get(name, "?"))
                           for byte, name in NAMED_BYTES.items()})

    def get(self, byte: int):
        if byte not in self.cache:
            if byte in self.slots:
                glyph = self.slots[byte]
                self.cache[byte] = (glyph.pixels, glyph.width)
            else:
                source = self.copies.get(byte, byte)
                width = self.widths[source] if source < len(self.widths) else 6
                text = self.names.get(byte, "?")
                self.cache[byte] = (stand_in(self.stand_ins.get(text, text), width, self.small), width)
        return self.cache[byte]

    def width(self, byte: int) -> int:
        return self.get(byte)[1]


@dataclass
class Change:
    project: object
    path: Path
    sid: str
    before: str | None
    after: str | None
    before_state: str | None = None
    after_state: str | None = None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True)


def _paths(root: Path, value: bytes) -> list[Path]:
    return [root / part.decode("utf-8") for part in value.split(b"\0") if part]


def changed_files(registry, base: str) -> list[Path]:
    """Changed and untracked inputs, including policy and profile files."""
    root = registry.root
    diff = _git(root, "diff", "--name-only", "-z", base, "--")
    if diff.returncode:
        raise ValueError(f"git diff failed: {diff.stderr.decode('utf-8', 'replace').strip()}")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked.returncode:
        raise ValueError(f"git ls-files failed: {untracked.stderr.decode('utf-8', 'replace').strip()}")
    return sorted(set(_paths(root, diff.stdout) + _paths(root, untracked.stdout)))


def changes(registry, base: str) -> list[Change]:
    """Translation changes keyed by PO msgctxt."""
    out = []
    with tempfile.TemporaryDirectory(prefix="gen3tl-pr-base-") as tmp:
        for path in changed_files(registry, base):
            project = next((p for p in registry.projects if path.is_relative_to(p.catalog)), None)
            if project is None or path.suffix != ".po":
                continue
            relative = path.relative_to(registry.root).as_posix()
            old = _git(registry.root, "show", f"{base}:{relative}")
            if old.returncode and not path.exists():
                raise ValueError(f"cannot read {relative} at {base}: {old.stderr.decode('utf-8', 'replace').strip()}")
            before = []
            if old.returncode == 0:
                old_path = Path(tmp) / relative
                old_path.parent.mkdir(parents=True, exist_ok=True)
                old_path.write_bytes(old.stdout)
                _, before = read(old_path)
            _, after = read(path) if path.exists() else (None, [])
            from ..catalog import keys
            from ..profile.profile import Profile
            key = keys.load(Profile.by_name(project.profile).sha1).key   # either side may carry keys
            old_by_id = {key(entry.msgctxt): entry for entry in before}
            new_by_id = {key(entry.msgctxt): entry for entry in after}
            for sid in sorted(old_by_id.keys() | new_by_id.keys()):
                old_text = old_by_id[sid].msgstr if sid in old_by_id else None
                new_text = new_by_id[sid].msgstr if sid in new_by_id else None
                old_state = old_by_id[sid].state if sid in old_by_id else None
                new_state = new_by_id[sid].state if sid in new_by_id else None
                old_flags = old_by_id[sid].flags if sid in old_by_id else set()
                new_flags = new_by_id[sid].flags if sid in new_by_id else set()
                if (old_text, old_state, old_flags) != (new_text, new_state, new_flags):
                    out.append(Change(project, path, sid, old_text, new_text, old_state, new_state))
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "entry"


def _cell(value) -> str:
    return html.escape(str(value)).replace("|", "\\|").replace("\n", " ")


def rule_changes(before_prof, prof, before, after, *, context=(), recheck=False):
    """Compare complete proposals through the editor's shared rule validators."""
    from ..catalog import keys
    from ..web.editorrules import catalog_preview, rules_data
    from ..web.policyrules import preview, value_of
    aliases = keys.for_profile(prof)

    def entries(catalog):
        return [{"id": aliases.key(sid), "source_text": None if entry.keyed else entry.msgid,
                 "translation": entry.msgstr, "state": entry.state, "flags": sorted(entry.flags)}
                for sid, (_, entry) in catalog.items()]

    previous, proposed = entries(before), entries(after)
    old_entries = {row["id"]: row for row in previous}
    new_entries = {row["id"]: row for row in proposed}
    changed = {key for key in old_entries.keys() | new_entries.keys()
               if old_entries.get(key) != new_entries.get(key)}
    old_rules = {rule["id"]: rule for rule in rules_data(before_prof, previous)["rules"]}
    new_rules = {rule["id"]: rule for rule in rules_data(prof, proposed)["rules"]}
    result = []
    for identity in sorted(old_rules.keys() | new_rules.keys()):
        old, rule = old_rules.get(identity), new_rules.get(identity)
        model = rule or old
        linked = {source["entry"] for version in (old, rule) if version for source in version["sources"]}
        if not ((recheck and model["kind"] in ("catalog", "policy")) or changed & linked or old != rule):
            continue
        report = {"id": identity, "description": model["description"], "entries": sorted(linked),
                  "operands": [], "examples": [], "blockers": [], "policies": [],
                  "related": [{"entry": key, "current": old_entries.get(key), "proposed": new_entries.get(key)}
                              for key in sorted(set(model.get("related", [])))]}
        result.append(report)
        if old is None or rule is None:
            report["blockers"].append("Rule declaration added or removed; baseline comparison unavailable")
            continue
        if old["capability_sha256"] != rule["capability_sha256"]:
            report["blockers"].append("Rule capability changed; verify the new declaration against the ROM")
        if rule["limit_status"] == "unverified":
            report["blockers"].append("Unverified limit")
        for key in sorted(linked):
            if key not in new_entries:
                report["blockers"].append(f"{key}: catalog entry unavailable")
        if rule["kind"] == "catalog":
            if context:
                cm, glyphs = context[:2]
            else:
                pack = load_styles(prof.language, prof)[prof["font_style"]["style"]]
                cm, glyphs = pack.charmap(), set(Charmap().decode_table) | pack.allocated_slots()
            # Keep deletions unavailable in the combined proposal.
            rows = [row for row in previous if row["id"] in new_entries]
            rows += [row for row in proposed if row["id"] not in old_entries]
            quantities = {0, 1, 2}
            for branch in rule["branches"]:
                for condition in branch["when"]:
                    if "value" in condition:
                        quantities.update((condition["value"], condition["value"] + 1))
            for example in rule["examples"] or [None]:
                for quantity in sorted(quantities):
                    request = {"quantity": quantity, **({"example": example} if example else {})}
                    comparison = catalog_preview(rule, rows, proposed, request, cm, glyphs)
                    current = catalog_preview(old, previous, [], request, cm, glyphs)
                    if not report["operands"]:
                        for operand in rule["operands"]:
                            for binding in operand["bindings"]:
                                key = binding["entry"]
                                report["operands"].append({**binding, "operand": operand["id"],
                                    "current": old_entries.get(key), "proposed": new_entries.get(key)})
                    modes = {"original": current["original"], "current": current["current"],
                             "proposed": comparison["proposed"]}
                    report["examples"].append({"entry": example, "quantity": quantity, "modes": modes})
                    target = modes["proposed"]
                    report["blockers"].extend(target["problems"])
                    report["blockers"].extend(f"{key}: original text or catalog input unavailable"
                                              for key in target["unavailable"])
                    for row in target["results"]:
                        report["blockers"].extend(f"{example}/{row['routine']}: {problem}"
                                                  for problem in row["problems"])
        elif rule["kind"] == "policy":
            if any(policy["kind"] == "droppable" for policy in rule["policies"]):
                report["policies"].append({"original": "original host tokens",
                                         "current": [policy["current"] for policy in old["policies"]],
                                         "proposed": [policy["current"] for policy in rule["policies"]]})
                continue
            try:
                comparison = preview(old, {"value": value_of(rule)}, *context)
                report["policies"].append(comparison)
                report["blockers"].extend(comparison["problems"])
            except ValueError as exc:
                report["blockers"].append(str(exc))
        report["blockers"] = sorted(set(report["blockers"]))
    return result


def rules_report(reports):
    """Render rule evidence and merge requirements without hiding unavailable inputs."""
    if not reports:
        return ""
    lines = ["## Engine language rules", "",
             "- [ ] Approve the combined catalog and policy behavior.",
             "- [ ] Verify the complete rule change against the ROM before merge.", ""]
    for report in reports:
        lines += [f"### {_cell(report['id'])}", "", _cell(report["description"]), "",
                  "Affected entries: " + (", ".join(_cell(key) for key in report["entries"]) or "none"), ""]
        def state(value):
            if value is None:
                return "unavailable"
            text = value["source_text"] if value["state"] == "original" else value["translation"]
            return f"{value['state']}: {text if text is not None else 'unavailable'}"

        if report["operands"]:
            lines += ["| Operand | Entry | Routine | ROM offset | Byte budget | Current | Proposed |",
                      "|---|---|---|---|---|---|---|"]
            for row in report["operands"]:
                lines.append("| " + " | ".join(_cell(value) for value in (
                    row["operand"], row["entry"], row.get("routine", "-"),
                    f"0x{row['offset']:X}", row["byte_budget"], state(row["current"]), state(row["proposed"]))) + " |")
            lines.append("")
        if report["related"]:
            lines += ["| Related text | Current | Proposed |", "|---|---|---|"]
            for row in report["related"]:
                lines.append("| " + " | ".join(_cell(value) for value in (
                    row["entry"], state(row["current"]), state(row["proposed"]))) + " |")
            lines += ["", "Related host wording requires combined review with the generated results.", ""]
        for comparison in report["policies"]:
            lines += ["| Original | Current | Proposed |", "|---|---|---|",
                      "| " + " | ".join(_cell(json.dumps(comparison[mode], ensure_ascii=False, sort_keys=True))
                                        for mode in ("original", "current", "proposed")) + " |", ""]
            if comparison.get("fields"):
                lines += ["| Field | Text | Terminated bytes / limit | Pixels / limit |", "|---|---|---|---|"]
                for field in comparison["fields"]:
                    lines.append("| " + " | ".join(_cell(value) for value in (
                        field["field"], field["text"], f"{field['used']} / {field['limit']}",
                        f"{field.get('pixels')} / {field.get('pixel_limit')}")) + " |")
                lines.append("")
        if report["examples"]:
            lines += ["| Example | Quantity | State | Routine | Complete result | Stored bytes / limit |",
                      "|---|---|---|---|---|---|"]
            for example in report["examples"]:
                for mode, preview in example["modes"].items():
                    for row in preview["results"]:
                        text = row["text"] if row["text"] is not None else "unavailable"
                        lines.append("| " + " | ".join(_cell(value) for value in (
                            example["entry"] or "empty input", example["quantity"], mode.title(),
                            row["routine"] or "-", text, f"{row['used']} / {row['limit']}")) + " |")
            lines.append("")
        lines += ["Blockers:", "", *[f"- {_cell(problem)}" for problem in report["blockers"] or
                                      ["No automatic blockers; approval and ROM verification remain required."]], ""]
    return "\n".join(lines)


def _baseline(project, base, raw, catalog, files):
    """Read baseline inputs without checking out or executing another revision."""
    from ..catalog import keys
    from ..catalog.extract import english
    from ..check.policy import effective
    from ..profile.profile import Profile, source_files
    before = deepcopy(catalog)
    aliases = keys.for_profile(raw)
    sources = set(source_files(raw.path))
    with tempfile.TemporaryDirectory(prefix="gen3tl-pr-rules-") as tmp:
        root = Path(tmp)
        for path in sorted(sources | {project.policy} |
                           {path for path in files if path.is_relative_to(project.catalog) and path.suffix == ".po"}):
            if not path.is_relative_to(project.registry_root):
                continue
            if path in files & sources and path != raw.path and not path.is_relative_to(raw.path):
                raise ValueError("shared profile defaults changed; baseline verification required")
            relative = path.relative_to(project.registry_root)
            old = _git(project.registry_root, "show", f"{base}:{relative.as_posix()}")
            staged = root / relative
            if old.returncode == 0:
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(old.stdout)
            if path.is_relative_to(project.catalog):
                before = {sid: pair for sid, pair in before.items() if pair[0] != path}
                if old.returncode == 0:
                    for entry in read(staged)[1]:
                        before[aliases.entry_id(entry.msgctxt)] = (path, entry)
        original = (Profile.load(root / raw.path.relative_to(project.registry_root))
                    if raw.path.is_relative_to(project.registry_root) else deepcopy(raw))
        original.path = raw.path
        original = effective(original, project.language,
                             root / project.policy.relative_to(project.registry_root), project.root)
    if original.sha1 != raw.sha1:
        raise ValueError("base ROM identity changed; original behavior comparison unavailable")
    english([entry for _, entry in before.values()], None, raw.sha1, False, raw.name)
    return original, before


def render_box(path: Path, text: str, result: dict, lim, glyphs: StandInGlyphs, cm: Charmap,
               measure, label: str) -> None:
    """Render one laid-out entry at two pixels per cell pixel."""
    laid = result["laid"]
    slots = placeholder_px(laid, entry_measure(measure, lim), cm)
    box_w = max(80, lim.width + 16)
    paragraphs = laid.split("\\p")
    heights = [16 * len(re.split(r"\\[nl]", par)) + 8 for par in paragraphs]
    canvas = Canvas(box_w + 8, sum(heights) + 40)
    draw_text(canvas, glyphs, 4, 3, cm.encode(label))
    draw_text(canvas, glyphs, 4, 18, cm.encode(WARNING))
    y = 36
    for par, height in zip(paragraphs, heights):
        lines = re.split(r"\\[nl]", par)
        canvas.rect(4, y, box_w + 4, y + height - 3, PALETTE["frame"])
        try:
            data = cm.encode(par.replace("\\l", "\\n"))
        except CharmapError:
            data = cm.encode("not encodable")
        interior = Canvas(box_w - 1, height - 4)
        draw_text(interior, glyphs, 7, 1, data, slots=slots)
        for dy, row in enumerate(interior.px):
            target = y + 1 + dy
            if target < canvas.h:
                canvas.px[target][5:5 + box_w - 1] = row
        y += height
    path.parent.mkdir(parents=True, exist_ok=True)
    write_png(path, canvas.scaled(2))


def _checker(prof, project, packs, snapshot):
    main = prof["font_style"]["style"]
    measure = qa.Measure(lambda _: 0, prof.get("layout", {}).get("placeholders", {}))
    doc = {"language": project.language, "fonts": fonts_data(prof, packs),
           "charmap": {c: f"{b:02X}" for c, b in packs[main].chars.items()
                       if packs[main].sources[c] != "base"},
           "placeholders": {"default": measure.default_ph, "widths": measure.ph}, "glossary": {}}
    return editorsnapshot.checker(doc, snapshot["fonts"], prof.get("qa", {}), load_version(project.release))


def _project_checks(project, items: list[Change], out: Path):
    """Run full project gates and return row data and errors."""
    from ..cli.check import po_check
    from ..profile.profile import Profile
    prof = project.effective(Profile.by_name(project.profile))
    with project.activate(prof):
        po_errors = po_check(SimpleNamespace(po_root=str(project.catalog)))
        catalog = load_catalog(project.catalog, english=False)   # the English where build/catalog.pot holds it
        snapshot = editorsnapshot.load(project.snapshot)
        editorsnapshot.validate(snapshot, prof, project.language, catalog, project.catalog)
        inv, ctx, _reach, facts = editorsnapshot.inputs(snapshot, catalog, prof)
        measure, cm, measures = editorsnapshot.measures(prof, project.language, snapshot["fonts"])
        source = snapshot["source"]
        rows_of = qa.facts_rows(catalog, source)   # without the English, the snapshot's facts
        limits = qa.compute_limits(prof, inv, catalog, measure, cm, ctx, facts=facts, source=source)
        packs = load_styles(project.language, prof)
        main = prof["font_style"]["style"]
        windows = qa.Windows(prof, inv, measures)
        gate = qa.run(prof, inv, project.catalog, cm, measure, packs[main].allocated_slots(),
                      glossary=project.glossary, windows=windows, version=load_version(project.release),
                      ctx=ctx, facts=facts, source=source)
        qa_name = f"qa-{_slug(project.key)}.md"
        (out / qa_name).write_text(qa.report(gate), encoding="utf-8")
        checker = _checker(prof, project, packs, snapshot)
        rows = []
        from ..catalog import keys
        entry_id = keys.load(prof.sha1).entry_id   # changes name entries by key
        for number, item in enumerate(items, 1):
            if item.after is None or entry_id(item.sid) not in catalog:
                rows.append({"change": item, "lines": "-", "width": "-", "status": "error: entry removed",
                             "errors": 1})
                continue
            _path, entry = catalog[entry_id(item.sid)]
            lim = limits.get(entry_id(item.sid))
            if lim is None:
                rows.append({"change": item, "lines": "-", "width": "-",
                             "status": "error: no inventory entry", "errors": 1})
                continue
            data = {"id": item.sid, "source_text": qa.source_of(entry_id(item.sid), entry, rows_of),
                    "window": window(entry_id(item.sid), lim, main, windows)}
            result = checker.check(data, item.after)
            before = checker.check(data, item.before or "")
            style = lim.style or main
            font = packs[style]
            from ..build.font import FontTable
            small = next(f.fmt for f in FontTable.from_profile(prof) if f.style == style) == "8x16"
            glyphs = StandInGlyphs(font, snapshot["fonts"][style]["widths"], small)
            stem = f"{_slug(project.key)}-{number:03d}-{_slug(item.sid)}"
            paths = {}
            for label, result_of in (("before", before), ("after", result)):
                image = f"{stem}-{label}.png"
                render_box(out / image, result_of["text"], result_of, lim, glyphs, cm, measure, label)
                paths[label] = image
            line_counts = ",".join(str(len(box)) for box in result["lines"])
            line_limit = lim.lines if lim.lines is not None else "-"
            widths = [line["width"] for box in result["lines"] for line in box if line["width"] is not None]
            width_text = f"{max(widths) if widths else '-'} / {lim.width}"
            status = "ok" if not result["errors"] else f"error: {result['problems'][0]['message']}"
            if result["warnings"] and not result["errors"]:
                status = f"ok; {result['warnings']} warnings"
            rows.append({"change": item, "lines": f"{line_counts} / {line_limit}", "width": width_text,
                         "status": status, "errors": result["errors"], "paths": paths})
        skipped = ", ".join(gate.skipped_typography) or "none"
        summary = (f"check po: {'failed' if po_errors else 'passed'}; QA: {gate.errors} errors "
                   f"([report]({qa_name}))"
                   + (f"; without the English source catalog: skipped typography rules: {skipped}; "
                      f"glossary skipped" if rows_of else ""))
        return rows, po_errors + gate.errors + sum(row["errors"] for row in rows), summary


def report(base: str, rows: list[dict], projects: list[str], errors: int, rules: list[str] = ()) -> str:
    """One table of changed entries, with project gate summaries."""
    lines = ["# Pull request checks", "", f"Base: `{_cell(base)}`", "",
             f"**Renders are {WARNING}. Widths and checks use the tracked measurement snapshot.**", "",
             "| Project | Entry | File | Lines / limit | Width / limit (px) | Status |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        item = row["change"]
        links = ""
        if row.get("paths"):
            links = "<br>" + ", ".join(f"[{label}]({path})" for label, path in row["paths"].items())
        lines.append(f"| {_cell(item.project.key)} | {_cell(item.sid)} | "
                     f"{_cell(item.path.relative_to(item.project.registry_root).as_posix())} | "
                     f"{_cell(row['lines'])} | {_cell(row['width'])} | {_cell(row['status'])}{links} |")
    if not rows:
        lines.append("| - | - | - | - | - | No translation changes |")
    lines += ["", "## Project checks", ""] + [f"- {text}" for text in projects]
    lines += ["", *rules]
    lines += ["", f"Result: {'failed' if errors else 'passed'}; errors: {errors}.", ""]
    return "\n".join(lines)


def _note_review(project, raw, base: str, files: set[Path], reviewed: set):
    """Count, bounded review and complete review of the changed note files; shared files once."""
    from ..web import notechanges
    prof = project.effective(raw)
    if not notechanges.note_files(project, prof, files):
        return None

    def before(path: Path) -> bytes | None:
        relative = path.resolve().relative_to(project.registry_root.resolve()).as_posix()
        old = _git(project.registry_root, "show", f"{base}:{relative}")
        return old.stdout if old.returncode == 0 else None

    with project.activate(prof):
        catalog = load_catalog(project.catalog, english=False)
        changes = notechanges.compare(project, prof, catalog, files, before)
        subjects = notechanges.subject_texts(project, prof, catalog, changes)
    changes = [change for change in changes if (change["file"], change["id"]) not in reviewed]
    reviewed.update((change["file"], change["id"]) for change in changes)
    if not changes:
        return None
    text, cut = notechanges.review(changes, notechanges.REVIEW_LIMIT, subjects=subjects)
    return len(changes), text, notechanges.review(changes, subjects=subjects)[0] if cut else None


def run(base: str, out: Path, registry) -> int:
    """Check combined catalog, policy and rule inputs with baseline previews."""
    from ..core.rom import RomError
    from ..profile.profile import Profile, source_files
    from ..web import notechanges
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        files = set(changed_files(registry, base))
        items = changes(registry, base)
    except (OSError, ValueError, RomError) as exc:
        (out / "report.md").write_text(report(base, [], [f"Input blocker: {_cell(exc)}"], 1), encoding="utf-8")
        line("errors", 1)
        line("report", out / "report.md")
        return 1
    grouped = {}
    for item in items:
        grouped.setdefault(item.project, []).append(item)
    broad = {}
    discovery_errors = []
    note_reports, note_reviews, note_errors, reviewed, complete = [], [], 0, set(), []
    for project in registry.projects:
        try:
            raw = Profile.by_name(project.profile)
            if any(path.suffix == ".toml" for path in files):
                try:
                    shown = _note_review(project, raw, base, files, reviewed)
                except notechanges.Refused as exc:
                    note_errors += 1
                    note_reports.append(f"{_cell(project.key)}: note error: {notechanges.inline(exc)}")
                else:
                    if shown:
                        note_reports.append(f"{_cell(project.key)}: {shown[0]} note changes validated; review required.")
                        note_reviews.append(f"Project: {_cell(project.key)}\n\n" + shown[1])
                        if shown[2]:
                            complete.append(f"Project: {_cell(project.key)}\n\n" + shown[2])
            snapshot_paths = set(editorsnapshot.paths(project.snapshot))
            inputs = {project.policy, project.glossary, *snapshot_paths}
            profile_files = set(source_files(raw.path))
            pack_root = language.load(project.language).root
            recheck = bool(files & (profile_files | {project.policy, *snapshot_paths,
                                                    registry.root / "projects.toml",
                                                    project.root / "project.toml"})) or any(
                path.is_relative_to(pack_root) for path in files)
            if recheck or files & inputs:
                grouped.setdefault(project, [])
            broad[project] = recheck
        except (OSError, ValueError, RomError) as exc:
            discovery_errors.append(f"{project.key}: error: {_cell(exc)}")
    rows, projects, rules = [], [*discovery_errors, *note_reports], [*note_reviews]
    errors = len(discovery_errors) + note_errors
    for project, selected in grouped.items():
        try:
            raw = Profile.by_name(project.profile)
            prof = project.effective(raw)
            with project.activate(prof):
                catalog = load_catalog(project.catalog, english=False)
                before_prof, before = _baseline(project, base, raw, catalog, files)
                if raw.language_rules or before_prof.language_rules:
                    context = ()
                    try:
                        snapshot = editorsnapshot.load(project.snapshot)
                        editorsnapshot.validate(snapshot, prof, project.language, catalog, project.catalog)
                        measure, cm, _ = editorsnapshot.measures(prof, project.language, snapshot["fonts"])
                        main = snapshot["fonts"][prof["font_style"]["style"]]["widths"]
                        context = cm, {byte for byte in cm.decode_table if byte < len(main)}, measure
                    except (OSError, ValueError, KeyError):
                        pass
                    reports = rule_changes(before_prof, prof, before, catalog, context=context,
                                           recheck=broad.get(project, False))
                    if reports:
                        rules.append(f"Project: {_cell(project.key)}\n\n" + rules_report(reports))
                        errors += sum(len(row["blockers"]) for row in reports)
        except (OSError, ValueError, KeyError, RomError) as exc:
            errors += 1
            rules.append(f"Engine rule blocker ({_cell(project.key)}): {_cell(exc)}\n")
        try:
            project_rows, project_errors, summary = _project_checks(project, selected, out)
        except (OSError, ValueError, KeyError, RomError) as exc:
            project_rows, project_errors = [], 1
            summary = f"{project.key}: error: {exc}"
            for item in selected:
                rows.append({"change": item, "lines": "-", "width": "-", "status": f"error: {exc}", "errors": 1})
        rows.extend(project_rows)
        errors += project_errors
        projects.append(f"{project.key}: {summary}" if not summary.startswith(project.key) else summary)
    text = report(base, rows, projects, errors, rules)
    (out / "report.md").write_text(text, encoding="utf-8")
    if complete:
        (out / "note-review.md").write_text("\n".join(complete), encoding="utf-8")
    line("changed", f"{len(items)} entries")
    line("errors", f"{errors} errors")
    line("report", out / "report.md")
    line("verification", "approximate")
    return 1 if errors else 0
