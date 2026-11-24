import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog import guidance, keys  # noqa: E402
from gen3tl.catalog.catalog import export, load_catalog  # noqa: E402
from gen3tl.catalog.merge import Match, THRESHOLD, load, merge  # noqa: E402
from gen3tl.catalog.rekey import KeyMap  # noqa: E402
from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD, PoEntry, parse, read, read_obsolete, write  # noqa: E402
from gen3tl.catalog.source import digest  # noqa: E402
from gen3tl.check import qa  # noqa: E402
from gen3tl.check.layout import Measure  # noqa: E402
from gen3tl.cli.main import main  # noqa: E402
from gen3tl.core.charmap import Charmap  # noqa: E402
from gen3tl.lang.langpack import load_styles  # noqa: E402
from gen3tl.profile.profile import Profile  # noqa: E402
from gen3tl.project import REGISTRY_ROOT  # noqa: E402
from gen3tl.web import editordata  # noqa: E402
from gen3tl.web.editordata import status  # noqa: E402
from project_fixture import write_policy  # noqa: E402
from editordata_support import PROF  # noqa: E402

BELLIN = {"header": "0x600", "engine_maps": ["3.4"]}
# header -> (event, n, script) of the map walk
EVENTS = {0x600: [("npc", n, 0x500 + 0x10 * n) for n in range(4)]}


def text(sid, offset, english):
    return {"id": sid, "offset": offset, "length": len(english) + 1, "kind": "pointer", "category": "script",
            "mode": "field", "text": english, "refs": [], "table": None, "index": None, "flags": []}


def species(index, english):
    return {"id": f"species:{index}", "offset": 0x8000 + 11 * index, "length": 11, "kind": "fixed",
            "category": "table:species", "mode": "field", "text": english, "refs": [], "table": "species",
            "index": index, "flags": []}


def npc(n):
    return {"uses": [{"map": "3.4", "map_name": "Bellin Town", "event": "npc", "sprite": None,
                      "textcolor_gender": None, "box": None, "script": hex(0x500 + 0x10 * n), "order": 0}]}


# base version: four map texts, five other texts
V1 = [text("s0001000", 0x1000, "Hello there!"), text("s0001040", 0x1040, "Go north."),
      text("s0001080", 0x1080, "Watch your step."), text("s00010C0", 0x10C0, "Fine weather today."),
      text("s0002000", 0x2000, "A sign."), text("s0002040", 0x2040, "The door is locked tight."),
      text("s0003000", 0x3000, "Thanks a lot!"), text("s0010000", 0x10000, "Unused text."),
      text("s0010040", 0x10040, "See you later!"), species(1, "BULBASAUR"), species(2, "IVYSAUR")]
# next version: moved, edited, removed and added texts
V2 = [text("s0001100", 0x1100, "Hello there!"), text("s0001140", 0x1140, "Go north, then east."),
      text("s0001180", 0x1180, "Watch your step!"), text("s00011C0", 0x11C0, "Fine weather today."),
      text("s0002100", 0x2100, "A sign."), text("s0002140", 0x2140, "The door is locked tightly."),
      text("s0002180", 0x2180, "Brand-new words from the update."),
      text("s0003100", 0x3100, "Goodbye, my friend."),
      species(1, "BULBASAUR"), species(2, "IVYSAUR"), species(3, "VENUSAUR")]
NPCS = {1: ("s0001000", "s0001040", "s0001080", "s00010C0"), 2: ("s0001100", "s0001140", "s0001180", "s00011C0")}

# English -> translation, flags, translator comments of the base catalog
TRANSLATED = {"Hello there!": ("Привет!", [], []), "Go north.": ("Иди на север.", [], []),
              "Fine weather today.": ("Хорошая погода.", ["fuzzy"], []),
              "A sign.": ("Табличка.", [], []),
              "The door is locked tight.": ("Дверь крепко заперта.", [], ["source: door wording"]),
              "Thanks a lot!": ("Большое спасибо!", [], []), "Unused text.": ("Неиспользуемый текст.", [], []),
              "BULBASAUR": ("БУЛЬБАЗАВР", [], [])}


def version(n):
    entries = V1 if n == 1 else V2
    inv = {"entries": entries}
    ctx = {"maps": {"3.4": BELLIN}, "strings": {sid: npc(i) for i, sid in enumerate(NPCS[n])}}
    return inv, ctx, keys.assign(inv, ctx, events=EVENTS.get)


def hashed(n, english):
    """The hash key of a text of a version."""
    sid = next(e["id"] for e in (V1 if n == 1 else V2) if e["text"] == english)
    return keys.text_hash(english, sid)


def exported(n, root):
    inv, ctx, aliases = version(n)
    export(inv, ctx, Charmap(), lambda b: 6, root, f"game-1.{n - 1}", language="ru", aliases=aliases)
    return root


def translate(root):
    for path in sorted(root.rglob("*.po")):
        header, entries = read(path)
        for e in entries:
            e.msgstr, e.flags, e.translator = TRANSLATED.get(e.msgid, ("", [], []))
        write(path, entries, header)
    return root


HINT = "hint input: command --help format"


def run_cmd(tmp, command, *argv):
    """The command in a project of its own, its exit code and output lines."""
    rom = tmp / "base.gba"
    rom.write_bytes(b"\xff" * 64)
    prof = tmp / "p.toml"
    prof.write_text(f'[rom]\nname = "t"\npath = "{rom}"\nsha1 = "{hashlib.sha1(rom.read_bytes()).hexdigest()}"\n',
                    encoding="utf-8")
    write_policy(tmp, Profile.load(prof))
    (tmp / "projects.toml").write_text('[defaults]\nprofile = "p"\nlanguage = "ru"\n[[project]]\nprofile = "p"\n'
                                       'language = "ru"\nroot = "."\npolicy = "policy.toml"\n',
                                       encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["--project-root", str(tmp), "--profile", str(prof), "catalog", command, *map(str, argv)])
    return code, [" ".join(line.split()) for line in out.getvalue().splitlines()]


def run(tmp, *argv):
    """The merge command in a project of its own."""
    return run_cmd(tmp, "merge", *argv)


def remap_materials(tmp, language="ru"):
    """Key-named files of one language project that name fixture keys."""
    trans = tmp / "translation" if language == "ru" else tmp / f"translation-{language}"
    moved_old, moved_new = hashed(1, "A sign."), hashed(2, "A sign.")
    sim_old, sim_new = hashed(1, "The door is locked tight."), hashed(2, "The door is locked tightly.")
    obsolete = [hashed(1, text) for text in ("Thanks a lot!", "Unused text.", "See you later!")]
    same = "table:species/1"
    files = {
        "policy.toml": f'[layout.window_width]\n"{moved_old}" = 83\n"{same}" = 60\n"{obsolete[0]}" = 42\n',
        "rules.tsv": f"ids\tsource_text\trule\tsource\n{moved_old} {sim_old}\t\tKeep {obsolete[0]} and {same}.\tx\n",
        "term-index.tsv": f"term\tstring\tform\ttext\tsource_names_term\tuse\n"
                          f"move:x\t{moved_old}\tnomn.sing\t-\tyes\tyes\n",
        "batches/b/translation.json": json.dumps({moved_old: "Табличка.", sim_old: "Дверь."}),
        "graphics/labels.tsv": f"id\tkey\ttext\nG01\t{sim_old}\tlabel\n",
        "research/notes.md": f"see {moved_old} and {obsolete[1]}.\n",
        "pokedex-aliases.toml": f'[species]\n"{same}" = {{}}\n',
    }
    for rel, text in files.items():
        path = trans / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    notes = tmp / "source" / "notes.toml"
    guidance.write_notes(notes, [
        {"id": moved_old, "source_sha256": digest("A sign."), "text": "guidance survives",
         "evidence": "Two-version fixture: identical English source."},
        {"id": obsolete[0], "source_sha256": digest("Thanks a lot!"), "text": "obsolete guidance stays",
         "evidence": "Two-version fixture: unmatched source remains unchanged."},
    ])
    return trans, notes, (moved_old, moved_new, sim_old, sim_new, obsolete, same)


class SecondVersionTest(unittest.TestCase):
    """A synthetic second version, exported like the first, merged by the command."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp_dir.name)
        cls.old, cls.new, cls.out = translate(exported(1, tmp / "old")), exported(2, tmp / "new"), tmp / "merged"
        cls.code, cls.lines = run(tmp, cls.old, cls.new, "--out", cls.out)
        cls.files = load(cls.out)
        cls.entries = {e.msgctxt: e for _, es, _ in cls.files.values() for e in es}

    @classmethod
    def tearDownClass(cls):
        cls.tmp_dir.cleanup()

    def test_report(self):
        self.assertEqual(self.code, 0)
        self.assertEqual(self.lines, [f"file {self.old.resolve()}", f"source {self.new.resolve()}",
                                      "same 4 entries", "changed 2 entries", "moved 1 entries", "similar 1 entries",
                                      "new 3 entries", "obsolete 3 entries", "fuzzy 2 entries", "status merged",
                                      f"wrote {self.out.resolve()}"])

    def test_status_of_every_entry(self):
        door = "The door is locked tight."
        expected = {
            "map:3.4/npc:0/0": ("Hello there!", "approved", "Привет!", None),
            "map:3.4/npc:1/0": ("Go north, then east.", "fuzzy", "Иди на север.", "Go north."),
            "map:3.4/npc:2/0": ("Watch your step!", "untranslated", "", None),
            "map:3.4/npc:3/0": ("Fine weather today.", "fuzzy", "Хорошая погода.", None),
            hashed(2, "A sign."): ("A sign.", "approved", "Табличка.", None),
            hashed(2, "The door is locked tightly."): ("The door is locked tightly.", "fuzzy",
                                                       "Дверь крепко заперта.", door),
            hashed(2, "Brand-new words from the update."): ("Brand-new words from the update.", "untranslated",
                                                            "", None),
            hashed(2, "Goodbye, my friend."): ("Goodbye, my friend.", "untranslated", "", None),
            "table:species/1": ("BULBASAUR", "approved", "БУЛЬБАЗАВР", None),
            "table:species/2": ("IVYSAUR", "untranslated", "", None),
            "table:species/3": ("VENUSAUR", "untranslated", "", None),
        }
        actual = {k: (e.msgid, status(e), e.msgstr, e.previous_msgid) for k, e in self.entries.items()}
        self.assertEqual(actual, expected)
        self.assertEqual(self.entries[hashed(2, "The door is locked tightly.")].translator, ["source: door wording"])
        self.assertEqual(self.entries[hashed(2, "A sign.")].references, ["0x0002100"])

    def test_outcome_of_every_entry(self):
        result = merge(load(self.old), load(self.new))
        ratio = next(m.ratio for m in result.matches if m.outcome == "similar")
        self.assertGreater(ratio, THRESHOLD)
        self.assertEqual(result.matches, [
            Match("same", "map:3.4/npc:0/0", "map:3.4/npc:0/0"),
            Match("changed", "map:3.4/npc:1/0", "map:3.4/npc:1/0"),
            Match("changed", "map:3.4/npc:2/0", "map:3.4/npc:2/0"),
            Match("same", "map:3.4/npc:3/0", "map:3.4/npc:3/0"),
            Match("moved", hashed(2, "A sign."), hashed(1, "A sign.")),
            Match("similar", hashed(2, "The door is locked tightly."), hashed(1, "The door is locked tight."), ratio),
            Match("new", hashed(2, "Brand-new words from the update."), None),
            Match("new", hashed(2, "Goodbye, my friend."), None),
            Match("same", "table:species/1", "table:species/1"),
            Match("same", "table:species/2", "table:species/2"),
            Match("new", "table:species/3", None),
            Match("obsolete", None, hashed(1, "Thanks a lot!")),
            Match("obsolete", None, hashed(1, "Unused text.")),
            Match("obsolete", None, hashed(1, "See you later!")),
        ])

    def test_obsolete_entries_stay_in_the_files_of_their_old_paths(self):
        dead = {rel: [(e.msgctxt, e.msgstr, e.extracted, e.references) for e in d]
                for rel, (_, _, d) in self.files.items() if d}
        self.assertEqual(dead, {
            "script/region-0000000.po": [(hashed(1, "Thanks a lot!"), "Большое спасибо!", [], [])],
            "script/region-0010000.po": [(hashed(1, "Unused text."), "Неиспользуемый текст.", [], []),
                                         (hashed(1, "See you later!"), "", [], [])],
        })
        header, active, _ = self.files["script/region-0010000.po"]
        self.assertEqual((header["Project-Id-Version"], header["Language"], active), ("game-1.1", "ru", []))
        self.assertTrue(all(h["Project-Id-Version"] == "game-1.1" for h, _, _ in self.files.values()))

    def test_build_and_editor_never_read_obsolete_entries(self):
        inv, ctx, aliases = version(2)
        sha1 = "d" * 40
        root = Path(self.tmp_dir.name)
        keys.write(keys.alias_path(sha1, root), aliases)
        token = REGISTRY_ROOT.set(root)
        try:
            prof = Profile({**PROF.data, "rom": {"sha1": sha1}}, Path("test.toml"))
            catalog = load_catalog(self.out, prof)
            packs = load_styles("ru", prof)
            cm, measure = packs["unbound"].charmap(), Measure(lambda b: 6, {})
            doc = editordata.build("ru", prof, catalog, qa.compute_limits(prof, inv, catalog, measure, cm, ctx),
                                   measure, cm, packs, toolkit="0.0.0")
        finally:
            REGISTRY_ROOT.reset(token)
        self.assertEqual(sorted(catalog), sorted(e["id"] for e in V2))
        self.assertEqual(sorted(e["id"] for e in doc["entries"]), sorted(self.entries))
        self.assertEqual(sum(e.fuzzy and bool(e.msgstr) for _, e in catalog.values()), 3)

    @unittest.skipUnless(shutil.which("msgfmt"), "msgfmt not installed")
    def test_msgfmt_accepts_the_merged_files(self):
        for path in sorted(self.out.rglob("*.po")):
            result = subprocess.run(["msgfmt", "--check-format", "-o", "/dev/null", str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


def po(*entries):
    return {"catalog.po": ({"Project-Id-Version": "game", "Language": "ru"}, list(entries), [])}


class RuleTest(unittest.TestCase):
    def outcomes(self, old, new, **options):
        return [(m.outcome, m.key, m.old) for m in merge(old, new, **options).matches]

    def test_inventory_ids_match_by_text_only(self):
        old = po(PoEntry("s0001000", "Hello", "Привет"))
        new = po(PoEntry("s0001000", "Goodbye"), PoEntry("s0002000", "Hello"))
        self.assertEqual(self.outcomes(old, new), [("new", "s0001000", None), ("moved", "s0002000", "s0001000")])

    def test_repeated_texts_pair_translated_entries_first_in_offset_order(self):
        old = po(PoEntry("hash:00000001", "Yes.", references=["0x0000100"]),
                 PoEntry("hash:00000003", "Yes.", "Ага.", references=["0x0000300"]),
                 PoEntry("hash:00000002", "Yes.", "Да.", references=["0x0000200"]))
        new = po(PoEntry("hash:0000000b", "Yes.", references=["0x0001100"]),
                 PoEntry("hash:0000000a", "Yes.", references=["0x0001000"]))
        result = merge(old, new)
        self.assertEqual([(e.msgctxt, e.msgstr) for e in result.files["catalog.po"][1]],
                         [("hash:0000000b", "Ага."), ("hash:0000000a", "Да.")])
        self.assertEqual([e.msgctxt for e in result.files["catalog.po"][2]], ["hash:00000001"])

    def test_the_previous_text_is_the_one_translated(self):
        old = po(PoEntry("map:1.1/npc:0/0", "Go north.", "Иди на север.", flags=["fuzzy"], previous_msgid="Go up."),
                 PoEntry("map:1.1/npc:1/0", "Go west.", "Иди на запад.", previous_msgid="Go left."),
                 PoEntry("map:1.1/npc:2/0", "", "", flags=["empty"]))
        new = po(PoEntry("map:1.1/npc:0/0", "Go north now."), PoEntry("map:1.1/npc:1/0", "Go west now."),
                 PoEntry("map:1.1/npc:2/0", "Hm?"))
        merged = merge(old, new).files["catalog.po"][1]
        self.assertEqual([(e.previous_msgid, e.fuzzy) for e in merged],
                         [("Go up.", True), ("Go west.", True), ("", True)])

    def test_threshold(self):
        old = po(PoEntry("hash:00000001", "Thanks a lot!", "Спасибо!"))
        new = po(PoEntry("hash:0000000a", "Thanks a ton!"))
        ratio = merge(old, new).matches[0].ratio
        self.assertEqual(self.outcomes(old, new, threshold=ratio),
                         [("new", "hash:0000000a", None), ("obsolete", None, "hash:00000001")])
        self.assertEqual(self.outcomes(old, new, threshold=ratio - 0.01)[0], ("similar", "hash:0000000a",
                                                                               "hash:00000001"))
        with self.assertRaisesRegex(ValueError, "threshold"):
            merge(old, new, threshold=1)

    def test_duplicate_keys_are_refused(self):
        with self.assertRaisesRegex(ValueError, "catalog: duplicate key hash:00000001"):
            merge(po(PoEntry("hash:00000001", "A"), PoEntry("hash:00000001", "B")), po())

    def test_obsolete_entries_come_back_or_stay_and_go_into_a_single_file(self):
        back = PoEntry("hash:00000008", "Back again.", "Снова тут.")
        gone = PoEntry("hash:00000009", "Long gone.", "Давно нет.")
        old = {"a.po": ({"Language": "ru"}, [PoEntry("hash:00000001", "Gone.", "Нет.")], [back, gone])}
        new = po(PoEntry("hash:00000008", "Back again."), PoEntry("hash:0000000a", "Here."))
        result = merge(old, new, into="catalog.po")
        self.assertEqual(list(result.files), ["catalog.po"])
        _, active, dead = result.files["catalog.po"]
        self.assertEqual([(e.msgctxt, e.msgstr, status(e)) for e in active],
                         [("hash:00000008", "Снова тут.", "approved"), ("hash:0000000a", "", "untranslated")])
        self.assertEqual([e.msgctxt for e in dead], ["hash:00000009", "hash:00000001"])
        self.assertEqual([result.counts[k] for k in ("same", "new", "retained", "obsolete")], [1, 1, 1, 1])


class ObsoleteSectionTest(unittest.TestCase):
    def test_readers_skip_it_and_writers_keep_it(self):
        dead = [PoEntry("hash:00000002", "Unused text that runs long enough to be written on several lines, "
                        "as long texts are.", "Нет", translator=["note"], flags=["fuzzy"], previous_msgid="Before"),
                PoEntry("table:x/1", "", "")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.po"
            write(path, [PoEntry("hash:00000001", "Hello", "Привет")], {"Language": "ru"}, dead)
            text = path.read_text(encoding="utf-8")
            self.assertIn('#~| msgid "Before"\n#~ msgctxt "hash:00000002"\n#~ msgid ""\n#~ "Unused', text)
            self.assertEqual([e.msgctxt for e in read(path)[1]], ["hash:00000001"])
            self.assertEqual(parse(path)[2], dead)
            write(path, [PoEntry("hash:00000001", "Hello", "Здравствуй")], {"Language": "ru"})
            self.assertEqual((read(path)[1][0].msgstr, read_obsolete(path)), ("Здравствуй", dead))
            write(path, [], {"Language": "ru"}, [])
            self.assertEqual(read_obsolete(path), [])

    def test_export_keeps_obsolete_sections_and_their_files(self):
        inv = {"entries": [text("s0001000", 0x1000, "Hello")]}
        dead = [PoEntry("hash:00000002", "Gone.", "Нет.")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "script/region-0000000.po", [PoEntry("s0001000", "Hello", "Привет")], {}, dead)
            write(root / "maps/gone.po", [], {}, dead)
            write(root / "maps/stale.po", [PoEntry("s0001000", "Hello", "Привет")], {})
            stats = export(inv, {"strings": {}}, Charmap(), lambda b: 6, root, "game", language="ru")
            self.assertEqual(sorted(p.relative_to(root).as_posix() for p in root.rglob("*.po")),
                             ["maps/gone.po", "script/region-0000000.po"])
            self.assertEqual(stats["stale_files_kept"], [str(root / "maps/gone.po")])
            for rel in ("maps/gone.po", "script/region-0000000.po"):
                self.assertEqual(read_obsolete(root / rel), dead)


class CommandTest(unittest.TestCase):
    def test_dry_run_and_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            old, new = translate(exported(1, tmp / "old")), exported(2, tmp / "new")
            (tmp / "full").mkdir()
            (tmp / "full/a.po").write_text("", encoding="utf-8")
            code, lines = run(tmp, old, new, "--dry-run", "--threshold", "0.99")
            self.assertEqual((code, lines[5], lines[-1]), (0, "similar 0 entries", "status planned"))
            for argv, error in (((old, new), "error --out is required unless --dry-run"),
                                ((old, new, "--out", tmp / "full"),
                                 "error the merged catalog needs a new or empty directory"),
                                ((old, tmp / "new/tables/species.po", "--out", tmp / "full"),
                                 "error a source file merges into a file"),
                                ((tmp / "missing", new, "--dry-run"), "error missing")):
                with self.subTest(error=error):
                    code, lines = run(tmp, *argv)
                    self.assertEqual((code, lines[-1]), (1, error))
            self.assertFalse((tmp / "merged").exists())

    def test_a_key_only_catalog_merges_with_the_english_of_its_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            old, new = translate(exported(1, tmp / "old")), exported(2, tmp / "new")
            code, full = run(tmp, old, new, "--dry-run")
            english = [e for _, es, _ in load(old).values() for e in es]
            write(tmp / "english.pot", english, {"Project-Id-Version": "game-1.0", "Language": ""})
            for path in old.rglob("*.po"):
                header, entries = read(path)
                write(path, entries, {**header, SOURCE_FIELD: KEY_ONLY})
            code, lines = run(tmp, old, new, "--dry-run")
            self.assertEqual((code, lines[-1]), (1, f"error a key-only catalog without its English source catalog "
                                                    f"{tmp.resolve() / 'build/catalog.pot'}; run gen3tl catalog extract"))
            self.assertNotIn(HINT, lines)
            code, lines = run(tmp, old, new, "--english", tmp / "english.pot", "--out", tmp / "merged")
            self.assertEqual(code, 0)
            self.assertEqual(lines[2:9], full[2:9])   # the outcomes of the full catalog
            for path in (tmp / "merged").rglob("*.po"):
                header, entries, dead = parse(path)
                self.assertEqual(header[SOURCE_FIELD], KEY_ONLY)
                self.assertTrue(all(e.msgid == e.msgctxt and e.previous_msgid is None for e in entries + dead))

    def test_a_source_file_merges_into_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            old = translate(exported(1, tmp / "old"))
            entries = [e for _, es, _ in load(exported(2, tmp / "new")).values() for e in es]
            write(tmp / "source.pot", entries, {"Project-Id-Version": "game-1.1", "Language": ""})
            code, lines = run(tmp, old, tmp / "source.pot", "--out", tmp / "merged.po")
            self.assertEqual(code, 0)
            header, active, dead = parse(tmp / "merged.po")
            self.assertEqual((header["Language"], len(active), len(dead)), ("ru", 11, 3))


class KeyMapTest(unittest.TestCase):
    def test_token_safe_simultaneous_mapping(self):
        keymap = KeyMap([Match("moved", "hash:0000000b", "hash:0000000a"),
                         Match("moved", "hash:0000000c", "hash:0000000b")])
        self.assertEqual(keymap.replace("hash:0000000a hash:0000000b"),
                         ("hash:0000000b hash:0000000c", 2))
        self.assertEqual(keymap.replace("hash:0000000a2 hash:0000000a-hash:0000000b"),
                         ("hash:0000000a2 hash:0000000a-hash:0000000b", 0))
        cycle = KeyMap([Match("moved", "hash:0000000b", "hash:0000000a"),
                        Match("moved", "hash:0000000a", "hash:0000000b")])
        self.assertEqual(cycle.replace("hash:0000000a hash:0000000b"),
                         ("hash:0000000b hash:0000000a", 2))

    def test_complete_canonical_key_boundaries(self):
        keymap = KeyMap([Match("moved", "table:items/2", "table:items/1")])
        self.assertEqual(keymap.replace("table:items/1 table:items/1/description table:items/10 /table:items/1"),
                         ("table:items/2 table:items/1/description table:items/10 /table:items/1", 1))

    def test_collisions_and_unmatched_keys(self):
        with self.assertRaisesRegex(ValueError, "same key"):
            KeyMap([Match("moved", "hash:0000000b", "hash:0000000a"),
                    Match("moved", "hash:0000000b", "hash:0000000c")])
        with self.assertRaisesRegex(ValueError, "to both"):
            KeyMap([Match("moved", "hash:0000000b", "hash:0000000a"),
                    Match("moved", "hash:0000000c", "hash:0000000a")])
        keymap = KeyMap([Match("obsolete", None, "hash:0000000a"), Match("new", "hash:0000000b", None)])
        self.assertEqual(keymap.unmatched, ["hash:0000000a"])
        self.assertEqual(keymap.mapping, {})


class RemapCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.old, self.new = translate(exported(1, tmp / "old")), exported(2, tmp / "new")
        self.trans, self.notes, self.keys = remap_materials(tmp)
        self.moved_old, self.moved_new, self.sim_old, self.sim_new, self.obsolete, self.same = self.keys

    def tearDown(self):
        self.tmp.cleanup()

    def changed(self):
        return sorted([(self.trans / "policy.toml").resolve(), (self.trans / "rules.tsv").resolve(),
                       (self.trans / "term-index.tsv").resolve(),
                       (self.trans / "batches/b/translation.json").resolve(),
                       (self.trans / "graphics/labels.tsv").resolve(),
                       (self.trans / "research/notes.md").resolve(), self.notes.resolve()])

    def test_dry_run_lists_every_rewrite_and_writes_nothing(self):
        before = {p: p.read_bytes() for p in self.trans.rglob("*") if p.is_file()}
        before[self.notes] = self.notes.read_bytes()
        outside = Path(self.tmp.name) / "outside.toml"
        outside.write_text(f'id = "{self.moved_old}"\n', encoding="utf-8")
        code, lines = run_cmd(Path(self.tmp.name), "remap", self.old, self.new,
                              "--translation", self.trans, "--reference", self.notes, "--dry-run")
        self.assertEqual((code, lines[-1]), (0, "status planned"))
        for path in self.changed():
            self.assertIn(f"file {path}", lines)
        expected_rewrites = [
            (self.notes, self.moved_old, self.moved_new),
            (self.trans / "policy.toml", self.moved_old, self.moved_new),
            (self.trans / "rules.tsv", self.moved_old, self.moved_new),
            (self.trans / "rules.tsv", self.sim_old, self.sim_new),
            (self.trans / "term-index.tsv", self.moved_old, self.moved_new),
            (self.trans / "batches/b/translation.json", self.moved_old, self.moved_new),
            (self.trans / "batches/b/translation.json", self.sim_old, self.sim_new),
            (self.trans / "graphics/labels.tsv", self.sim_old, self.sim_new),
            (self.trans / "research/notes.md", self.moved_old, self.moved_new),
        ]
        self.assertCountEqual([line for line in lines if line.startswith("rewrite ")],
                              [f"rewrite {path.resolve()}: {old} -> {new} (1)" for path, old, new in expected_rewrites])
        self.assertIn("rewrites 9 keys", lines)
        self.assertNotIn(f"file {(self.trans / 'pokedex-aliases.toml').resolve()}", lines)
        for key in self.obsolete:
            self.assertIn(f"unmatched {key}", lines)
        self.assertEqual({p: p.read_bytes() for p in self.trans.rglob("*") if p.is_file()},
                         {p: b for p, b in before.items() if p != self.notes})
        self.assertEqual(self.notes.read_bytes(), before[self.notes])
        self.assertEqual(outside.read_text(), f'id = "{self.moved_old}"\n')

    def test_rewrites_every_key_named_file_and_notes(self):
        code, lines = run_cmd(Path(self.tmp.name), "remap", self.old, self.new,
                              "--translation", self.trans, "--reference", self.notes)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        self.assertEqual((self.trans / "policy.toml").read_text(),
                         f'[layout.window_width]\n"{self.moved_new}" = 83\n"{self.same}" = 60\n'
                         f'"{self.obsolete[0]}" = 42\n')
        self.assertEqual((self.trans / "rules.tsv").read_text(),
                         f"ids\tsource_text\trule\tsource\n{self.moved_new} {self.sim_new}\t\t"
                         f"Keep {self.obsolete[0]} and {self.same}.\tx\n")
        self.assertEqual((self.trans / "term-index.tsv").read_text().splitlines()[1].split("\t")[1],
                         self.moved_new)
        self.assertEqual(json.loads((self.trans / "batches/b/translation.json").read_text()),
                         {self.moved_new: "Табличка.", self.sim_new: "Дверь."})
        self.assertEqual((self.trans / "graphics/labels.tsv").read_text().splitlines()[1].split("\t")[1],
                         self.sim_new)
        self.assertEqual((self.trans / "research/notes.md").read_text(), f"see {self.moved_new} and {self.obsolete[1]}.\n")
        self.assertEqual((self.trans / "pokedex-aliases.toml").read_text(), f'[species]\n"{self.same}" = {{}}\n')
        note = self.notes.read_text()
        self.assertIn(f'id = "{self.moved_new}"', note)
        self.assertIn('text = "guidance survives"', note)
        self.assertNotIn(self.moved_old, note)
        rows = guidance.read_notes(self.notes)
        self.assertEqual(rows[0], {"id": self.moved_new, "source_sha256": digest("A sign."),
                                   "text": "guidance survives", "evidence": "Two-version fixture: identical English source."})
        self.assertEqual(rows[1], {"id": self.obsolete[0], "source_sha256": digest("Thanks a lot!"),
                                   "text": "obsolete guidance stays",
                                   "evidence": "Two-version fixture: unmatched source remains unchanged."})
        self.assertIn(f"unmatched {self.obsolete[0]}", lines)
        code, again = run_cmd(Path(self.tmp.name), "remap", self.old, self.new,
                              "--translation", self.trans, "--reference", self.notes, "--dry-run")
        self.assertEqual((code, again[-1]), (0, "status unchanged"))
        self.assertIn("files 0 files", again)

    def test_shared_notes_and_selected_languages_follow_the_merge(self):
        trans_es, _, _ = remap_materials(Path(self.tmp.name), "es")
        code, lines = run_cmd(Path(self.tmp.name), "remap", self.old, self.new,
                              "--translation", self.trans, trans_es, "--reference", self.notes)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        for trans in (self.trans, trans_es):
            self.assertIn(self.moved_new, (trans / "policy.toml").read_text())
            self.assertNotIn(self.moved_old, (trans / "policy.toml").read_text())
            self.assertIn(self.moved_new, (trans / "rules.tsv").read_text())
        self.assertIn(f'id = "{self.moved_new}"', self.notes.read_text())
        self.assertIn('text = "guidance survives"', self.notes.read_text())

    def test_destination_collisions_preserve_the_complete_plan(self):
        tmp = Path(self.tmp.name)
        good = tmp / "good.tsv"
        good.write_text(f"ids\n{self.moved_old}\n", encoding="utf-8")
        good_before = good.read_bytes()
        documents = {
            "collision.json": json.dumps({self.moved_old: "old value", self.moved_new: "existing new value"}),
            "collision.toml": f'[layout.window_width]\n"{self.moved_old}" = 83\n"{self.moved_new}" = 40\n',
        }
        for name, text in documents.items():
            with self.subTest(name=name):
                target = tmp / name
                target.write_text(text, encoding="utf-8")
                before = target.read_bytes()
                code, lines = run_cmd(tmp, "remap", self.old, self.new, "--reference", good, target)
                self.assertEqual(code, 1)
                self.assertIn("colliding definitions", lines[-1])
                if name.endswith(".toml"):
                    self.assertTrue(lines[-1].endswith("invalid TOML"))
                self.assertNotIn(HINT, lines)
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual(good.read_bytes(), good_before)
        self.assertEqual(set(json.loads((tmp / "collision.json").read_text())), {self.moved_old, self.moved_new})
        self.assertEqual(set(tomllib.loads((tmp / "collision.toml").read_text())["layout"]["window_width"]),
                         {self.moved_old, self.moved_new})
        colliding_notes = tmp / "colliding-notes.toml"
        rows = [{"id": key, "source_sha256": digest("A sign."), "text": key, "evidence": "Collision fixture."}
                for key in (self.moved_old, self.moved_new)]
        guidance.write_notes(colliding_notes, rows)
        before = colliding_notes.read_bytes()
        code, lines = run_cmd(tmp, "remap", self.old, self.new, "--reference", good, colliding_notes)
        self.assertEqual(code, 1)
        self.assertIn("colliding definitions", lines[-1])
        self.assertNotIn(HINT, lines)
        self.assertEqual(colliding_notes.read_bytes(), before)
        self.assertEqual(good.read_bytes(), good_before)

    def test_batch_definition_collisions_preserve_the_complete_plan(self):
        tmp = Path(self.tmp.name)
        good = tmp / "00-good.tsv"
        good.write_text(f"ids\n{self.moved_old}\n", encoding="utf-8")
        source = self.trans / "batches/b/source.json"
        rows = [{"id": key, "file": "a.po", "source_text": "A sign.", "context": {}, "limit": None,
                 "current": current, "glossary": []}
                for key, current in ((self.moved_old, "Old state"), (self.moved_new, "Existing destination state"))]
        source.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        before = {p: p.read_bytes() for p in [good, *(p for p in self.trans.rglob("*") if p.is_file())]}
        code, lines = run_cmd(tmp, "remap", self.old, self.new, "--translation", self.trans, "--reference", good)
        self.assertEqual(code, 1)
        self.assertIn("colliding definitions", lines[-1])
        self.assertIn(f"duplicate definition {self.moved_new}", lines[-1])
        self.assertNotIn(HINT, lines)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_batch_review_findings_may_repeat_an_id(self):
        review = self.trans / "batches/b/review-accuracy-1.json"
        findings = [{"id": key, "problem": "fixture finding", "fix": "fixture fix"}
                    for key in (self.moved_old, self.moved_old, self.moved_new)]
        review.write_text(json.dumps(findings), encoding="utf-8")
        code, lines = run_cmd(Path(self.tmp.name), "remap", self.old, self.new, "--translation", self.trans)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        self.assertEqual([row["id"] for row in json.loads(review.read_text())],
                         [self.moved_new, self.moved_new, self.moved_new])

    def test_repeated_references_are_not_definition_collisions(self):
        tmp = Path(self.tmp.name)
        json_path = tmp / "references.json"
        toml_path = tmp / "references.toml"
        references = [self.moved_old, self.moved_old, self.moved_new]
        json_path.write_text(json.dumps({"references": references}), encoding="utf-8")
        toml_path.write_text(f'references = ["{self.moved_old}", "{self.moved_old}", "{self.moved_new}"]\n',
                             encoding="utf-8")
        code, lines = run_cmd(tmp, "remap", self.old, self.new, "--reference", json_path, toml_path)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        expected = [self.moved_new, self.moved_new, self.moved_new]
        self.assertEqual(json.loads(json_path.read_text())["references"], expected)
        self.assertEqual(tomllib.loads(toml_path.read_text())["references"], expected)

    def test_canonical_prefixes_and_obsolete_references_stay_unchanged(self):
        tmp = Path(self.tmp.name)
        old, new, target = tmp / "prefix-old.po", tmp / "prefix-new.pot", tmp / "prefix.tsv"
        write(old, [PoEntry("table:items/1", "Item one", "Один"),
                    PoEntry("table:items/1/description", "Long description", "Описание")], {"Language": "ru"})
        write(new, [PoEntry("table:items/2", "Item one")], {})
        target.write_text("ids\ntable:items/1 table:items/1/description table:items/10 /table:items/1\n",
                          encoding="utf-8")
        code, lines = run_cmd(tmp, "remap", old, new, "--reference", target)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        self.assertEqual(target.read_text(),
                         "ids\ntable:items/2 table:items/1/description table:items/10 /table:items/1\n")
        self.assertIn("rewrites 1 keys", lines)
        self.assertIn("unmatched table:items/1/description", lines)

    def test_overlapping_scopes_apply_a_chain_once(self):
        tmp = Path(self.tmp.name)
        old, new = tmp / "chain-old.po", tmp / "chain-new.pot"
        translation = tmp / "translation-chain"
        translation.mkdir()
        target = translation / "rules.tsv"
        write(old, [PoEntry("s0001000", "First", "Один"), PoEntry("s0002000", "Second", "Два")], {"Language": "ru"})
        write(new, [PoEntry("s0002000", "First"), PoEntry("s0003000", "Second")], {})
        target.write_text("ids\ns0001000 s0002000\n", encoding="utf-8")
        code, lines = run_cmd(tmp, "remap", old, new, "--translation", translation, "--reference", target)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        self.assertEqual(target.read_text(), "ids\ns0002000 s0003000\n")
        self.assertIn("rewrites 2 keys", lines)
        self.assertIn("files 1 files", lines)
        self.assertEqual(lines.count(f"file {target.resolve()}"), 1)
        self.assertCountEqual([line for line in lines if line.startswith("rewrite ")],
                              [f"rewrite {target.resolve()}: s0001000 -> s0002000 (1)",
                               f"rewrite {target.resolve()}: s0002000 -> s0003000 (1)"])

    def test_guidance_binding_survives_moves_and_rejects_changed_source(self):
        tmp = Path(self.tmp.name)
        code, lines = run_cmd(tmp, "remap", self.old, self.new, "--reference", self.notes)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        moved = guidance.read_notes(self.notes)[0]
        self.assertEqual(moved["source_sha256"], digest("A sign."))
        for language in ("ru", "es", "de", "fr"):
            with self.subTest(outcome="moved", language=language):
                profile = Profile({"translator_notes": [moved]}, Path(f"guidance-{language}.toml"))
                catalog = {self.moved_new: ("fixture", PoEntry(self.moved_new, "A sign.", language))}
                selected = guidance.entry_notes(profile, catalog)
                self.assertEqual(selected[self.moved_new],
                                 [{"text": "guidance survives", "evidence": "Two-version fixture: identical English source."}])
        similar_notes = tmp / "similar-notes.toml"
        guidance.write_notes(similar_notes, [{"id": self.sim_old, "source_sha256": digest("The door is locked tight."),
                                              "text": "changed source needs review", "evidence": "Similar-source fixture."}])
        code, lines = run_cmd(tmp, "remap", self.old, self.new, "--reference", similar_notes)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        similar = guidance.read_notes(similar_notes)[0]
        self.assertEqual(similar["id"], self.sim_new)
        self.assertEqual(similar["source_sha256"], digest("The door is locked tight."))
        for language in ("ru", "es", "de", "fr"):
            with self.subTest(outcome="similar", language=language):
                profile = Profile({"translator_notes": [similar]}, Path(f"guidance-{language}.toml"))
                catalog = {self.sim_new: ("fixture", PoEntry(self.sim_new, "The door is locked tightly.", language))}
                with self.assertRaisesRegex(ValueError, "source differs"):
                    guidance.entry_notes(profile, catalog)

    def test_invalid_and_generated_only_profile_directories(self):
        tmp = Path(self.tmp.name)
        empty = (tmp / "empty-profile").resolve()
        empty.mkdir()
        code, lines = run_cmd(tmp, "remap", self.old, self.new, "--profile-file", empty)
        self.assertEqual((code, lines[-1]), (1, f"error {empty}: profile directory has no profile.toml"))
        self.assertNotIn(HINT, lines)
        generated = tmp / "generated-profile"
        generated.mkdir()
        profile = generated / "profile.toml"
        profile.write_text(f'[policy_slots]\nwindow_width = ["{self.moved_old}"]\n', encoding="utf-8")
        before = profile.read_bytes()
        code, lines = run_cmd(tmp, "remap", self.old, self.new, "--profile-file", generated)
        self.assertEqual((code, lines[-1]), (0, "status unchanged"))
        self.assertIn("files 0 files", lines)
        self.assertEqual(profile.read_bytes(), before)

    def test_full_and_profile_scopes_conflict_without_writes(self):
        tmp = Path(self.tmp.name)
        overlay = tmp / "overlay.toml"
        overlay.write_text(f'[policy_slots]\nwindow_width = ["{self.moved_old}"]\n', encoding="utf-8")
        before = overlay.read_bytes()
        code, lines = run_cmd(tmp, "remap", self.old, self.new, "--reference", overlay, "--profile-file", overlay)
        self.assertEqual(code, 1)
        self.assertIn("selected as both a full file and a profile-only file", lines[-1])
        self.assertNotIn(HINT, lines)
        self.assertEqual(overlay.read_bytes(), before)

    def test_remap_ordered_profile_fragments(self):
        root = Path(self.tmp.name) / "profile"
        overlay = root / "overlay"
        overlay.mkdir(parents=True)
        generated = root / "profile.toml"
        generated.write_text(f'[policy_slots]\nwindow_width = ["{self.moved_old}"]\n')
        order = overlay / "order.txt"
        order.write_text("reach.toml\npolicy-slots.toml\n")
        reach = overlay / "reach.toml"
        reach.write_text(f'[reach]\nids = ["{self.moved_old}"]\n')
        slots = overlay / "policy-slots.toml"
        slots.write_text(f'[policy_slots]\nwindow_width = ["{self.moved_old}"]\n')
        before = {path: path.read_bytes() for path in (generated, order, reach, slots)}
        code, lines = run_cmd(Path(self.tmp.name), "remap", self.old, self.new,
                              "--profile-file", root, "--dry-run")
        self.assertEqual((code, lines[-1]), (0, "status planned"))
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        code, lines = run_cmd(Path(self.tmp.name), "remap", self.old, self.new, "--profile-file", root)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        self.assertIn(f'window_width = ["{self.moved_new}"]', slots.read_text())
        for path in (generated, order, reach):
            self.assertEqual(path.read_bytes(), before[path])

    def test_profile_sections_follow_the_merge(self):
        overlay = Path(self.tmp.name) / "overlay.toml"
        overlay.write_text(f'[reach]\nids = ["{self.moved_old}"]\n\n[policy_slots]\n'
                           f'"window_width" = ["{self.moved_old}"]\n')
        before = overlay.read_bytes()
        code, lines = run_cmd(Path(self.tmp.name), "remap", self.old, self.new,
                              "--profile-file", overlay, "--dry-run")
        self.assertEqual((code, lines[-1]), (0, "status planned"))
        self.assertIn("rewrites 1 keys", lines)
        self.assertIn(f"rewrite {overlay.resolve()}: {self.moved_old} -> {self.moved_new} (1)", lines)
        self.assertEqual(overlay.read_bytes(), before)
        code, lines = run_cmd(Path(self.tmp.name), "remap", self.old, self.new, "--profile-file", overlay)
        self.assertEqual((code, lines[-1]), (0, "status rewritten"))
        text = overlay.read_text()
        self.assertIn(f'"window_width" = ["{self.moved_new}"]', text)
        self.assertIn(f'ids = ["{self.moved_old}"]', text)

    def test_missing_inputs_and_scope_fail(self):
        tmp = Path(self.tmp.name)
        for argv, error in (((tmp / "missing", self.new, "--translation", self.trans), "error missing"),
                            ((self.old, self.new, "--translation", tmp / "missing"), "error missing"),
                            ((self.old, self.new, "--reference", self.trans),
                             "error a reference must be a file"),
                            ((self.old, self.new), "error one of --translation, --reference or --profile-file "
                                                   "is required")):
            with self.subTest(error=error):
                code, lines = run_cmd(tmp, "remap", *argv)
                self.assertEqual((code, lines[-1]), (1, error))


if __name__ == "__main__":
    unittest.main()
