"""Byte and set proofs of the sample profile and its merged facts."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from gen3tl.cli.main import main
from gen3tl.profile import schema, tsv
from gen3tl.profile.profile import PROFILE_DIR
from gen3tl.verify import profile
from project_fixture import SAMPLE


class ProfileProofTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "projects.toml").write_text("", encoding="utf-8")
        self.source = PROFILE_DIR / f"{SAMPLE}.toml"
        self.before, self.after = self.root / "before", self.root / "after"

    def invoke(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                status = main(["--project-root", str(self.root), *map(str, arguments)])
            except SystemExit as exc:
                status = exc.code
        return status, out.getvalue(), err.getvalue()

    def changed_record(self, change):
        profile.record(self.source, self.before)
        data = schema.load(self.source)
        change(data)
        self.write_after(data)

    def write_after(self, data):
        self.after.mkdir(exist_ok=True)
        for name, value in zip(profile.FILES, (data, [fact.id for fact in schema.facts(data)])):
            (self.after / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def window_records(self):
        source = self.root / "windows.toml"
        source.write_text(self.source.read_text(encoding="utf-8") +
                          '\n[layout.window_width]\nfirst = 32\nsecond = 32\n'
                          '[layout.window_box]\nfirst = "menu"\nsecond = "menu"\n'
                          '[layout.window_lines]\nfirst = 1\nsecond = 1\n'
                          '[layout.window_style]\nfirst = "small"\nsecond = "small"\n', encoding="utf-8")
        directory = self.root / "windows"
        overlay = directory / "overlay"
        (overlay / "layout").mkdir(parents=True)
        (directory / "profile.toml").write_text('[discovery]\nschema = 1\n', encoding="utf-8")
        (overlay / "identity.toml").write_bytes(self.source.read_bytes())
        (overlay / "order.txt").write_text('identity.toml\nlayout/windows.tsv\tlayout.window\n'
                                          'layout/window-strings.tsv\tlayout.window_strings\n', encoding="utf-8")
        rows = [{"name": "menu-status", "width": 32, "box": "menu", "lines": 1, "style": "small",
                 "evidence": "One call per status row."}]
        bindings = [{"string": "second", "window": "menu-status"}, {"string": "first", "window": "menu-status"}]
        for name, values in (("layout/windows.tsv", rows), ("layout/window-strings.tsv", bindings)):
            tsv.write(overlay / name, values, schema.OVERLAY_TABLES[name][1])
        profile.record(source, self.before)
        profile.record(directory, self.after)

    def test_record_is_deterministic_and_preserves_inputs(self):
        original = self.source.read_bytes()
        first = profile.record(self.source, self.before)
        second = profile.record(self.source, self.after)
        self.assertEqual(first, second)
        self.assertEqual(self.source.read_bytes(), original)
        data = schema.load(self.source)
        self.assertEqual(json.loads(first["document.json"]), data)
        self.assertEqual(json.loads(first["fact-ids.json"]), [fact.id for fact in schema.facts(data)])
        self.assertTrue(all(row.equal for row in profile.compare(self.before, self.after)))
        self.assertTrue(all(row.equal for row in profile.compare(self.before, self.before)))
        with self.assertRaises(FileExistsError):
            profile.record(self.source, self.before)
        self.assertEqual((self.before / "document.json").read_bytes(), first["document.json"])

    def test_directory_merge_records_the_consumer_document(self):
        directory = self.root / "merged"
        directory.mkdir()
        (directory / "profile.toml").write_text(
            '[discovery]\nschema = 1\n[rom]\nsize = 2048\n[[fact]]\nid = "rom.size"\n'
            'tier = "crossref"\nsource = "fixture"\nevidence = {sentence = "fixture size"}\n',
            encoding="utf-8")
        (directory / "overlay.toml").write_bytes(self.source.read_bytes())
        profile.record(self.source, self.before)
        profile.record(directory, self.after)
        self.assertTrue(all(row.equal for row in profile.compare(self.before, self.after)))

    def test_changed_value_reports_its_path(self):
        self.changed_record(lambda data: data["rom"].update(size=4096))
        document, ids = profile.compare(self.before, self.after)
        self.assertFalse(document.equal)
        self.assertEqual((document.path, document.reason), ("$.rom.size", "value"))
        self.assertTrue(ids.equal)
        status, out, err = self.invoke("verify", "profile", "--compare", self.before, self.after)
        self.assertEqual(status, 1)
        self.assertIn("$.rom.size", out)
        self.assertIn("result       failed: profile proof differs", err)

    def test_key_order_is_significant(self):
        def change(data):
            data["rom"] = dict(reversed(list(data["rom"].items())))
        self.changed_record(change)
        document, ids = profile.compare(self.before, self.after)
        self.assertEqual((document.path, document.reason), ("$.rom.name", "key order"))
        self.assertFalse(ids.equal)

    def test_missing_fact_reports_its_path(self):
        self.changed_record(lambda data: data["rom"].pop("size"))
        document, ids = profile.compare(self.before, self.after)
        self.assertEqual((document.path, document.reason), ("$.rom.size", "missing key"))
        self.assertFalse(ids.equal)

    def test_fact_ids_are_compared_independently(self):
        profile.record(self.source, self.before)
        profile.record(self.source, self.after)
        path = self.after / "fact-ids.json"
        ids = json.loads(path.read_bytes())
        ids.pop()
        path.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
        document, changed = profile.compare(self.before, self.after)
        self.assertTrue(document.equal)
        self.assertEqual((changed.path, changed.reason), (f"$[{len(ids)}]", "missing item"))

    def test_byte_comparison_rejects_formatting_changes(self):
        profile.record(self.source, self.before)
        profile.record(self.source, self.after)
        path = self.after / "document.json"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        document, ids = profile.compare(self.before, self.after)
        self.assertEqual((document.path, document.reason), ("$", "encoding"))
        self.assertFalse(document.equal)
        self.assertTrue(ids.equal)

    def test_nested_types_lists_and_quoted_keys(self):
        self.source = self.root / "values.toml"
        self.source.write_text('[layout]\n"odd.key" = [{width = 1, enabled = true}, {width = 2}]\n',
                               encoding="utf-8")
        original = schema.load(self.source)
        for replacement, expected in ((1.0, "type"), (True, "type"), (2, "value")):
            data = copy.deepcopy(original)
            data["layout"]["odd.key"][0]["width"] = replacement
            self.assertEqual(profile._difference(original, data), ('$.layout."odd.key"[0].width', expected))
        data = copy.deepcopy(original)
        data["layout"]["odd.key"].append({"width": 3})
        self.assertEqual(profile._difference(original, data), ('$.layout."odd.key"[2]', "added item"))

    def test_cli_records_without_translation_or_rom(self):
        status, out, err = self.invoke("--profile", SAMPLE, "verify", "profile", "--record", self.before)
        self.assertEqual((status, err), (0, ""))
        self.assertIn("document.json", out)
        self.assertIn("fact-ids.json", out)
        self.assertIn("result       passed", out)
        (self.root / "projects.toml").unlink()
        status, out, err = self.invoke("--profile", "absent", "verify", "profile", "--compare",
                                       self.before, self.before)
        self.assertEqual((status, err), (0, ""))
        self.assertIn("equal", out)
        self.assertIn("result       passed", out)

    def test_help_and_invalid_modes(self):
        status, out, err = self.invoke("verify", "profile", "--help")
        self.assertEqual((status, err), (0, ""))
        for text in ("about", "usage", "--record", "--compare", "BEFORE", "AFTER", "--set",
                     "--allow-added-prefix", "PREFIX", "canonical values", "reordered sections"):
            self.assertIn(text, out)
        for arguments in ((), ("--compare", self.before),
                          ("--record", self.before, "--compare", self.before, self.after)):
            status, out, err = self.invoke("verify", "profile", *arguments)
            self.assertEqual(status, 2)
            self.assertIn("result       failed", err)

    def test_missing_and_malformed_records_fail_cleanly(self):
        profile.record(self.source, self.before)
        profile.record(self.source, self.after)
        path = self.after / "fact-ids.json"
        for raw in (b"{", b"{}", b"[7]"):
            path.write_bytes(raw)
            status, out, err = self.invoke("verify", "profile", "--compare", self.before, self.after)
            self.assertEqual(status, 1)
            self.assertIn("result       failed", err)
            self.assertNotIn("Traceback", out + err)
        path.unlink()
        status, out, err = self.invoke("verify", "profile", "--compare", self.before, self.after)
        self.assertEqual(status, 1)
        self.assertIn("missing path", err)

    def test_set_mode_accepts_reordered_maps_and_named_window_additions(self):
        self.window_records()
        self.assertFalse(all(row.equal for row in profile.compare(self.before, self.after)))
        rows = profile.compare_sets(self.before, self.after, allow_added_prefix="layout.window[")
        self.assertTrue(all(row.allowed for row in rows))
        self.assertEqual([row.path for row in rows if row.reason == "added fact"],
                         ["layout.window[name=menu-status]"])
        self.assertEqual([row.path for row in rows if row.reason == "key order"],
                         [f"$.layout.window_{field}" for field in ("box", "lines", "style", "width")])
        (self.root / "projects.toml").unlink()
        status, out, err = self.invoke("--profile", "absent", "verify", "profile", "--compare", self.before,
                                      self.after, "--set", "--allow-added-prefix", "layout.window[")
        self.assertEqual((status, err), (0, ""))
        for text in ("fact / section", "before", "after", "allowed", "key order", "added fact",
                     "layout.window[name=menu-status]"):
            self.assertIn(text, out)
        self.assertEqual(out.splitlines()[-1], "result       passed")

    def test_set_mode_rejects_changed_lost_and_unpermitted_facts(self):
        self.window_records()
        baseline = json.loads((self.before / "document.json").read_bytes())
        migrated = json.loads((self.after / "document.json").read_bytes())
        cases = ((lambda data: data["layout"]["window_width"].update(first=33),
                  "layout.window_width.first", "changed value"),
                 (lambda data: data["layout"]["window_width"].pop("first"),
                  "layout.window_width.first", "lost fact"),
                 (lambda data: data["rom"].update(extra=1), "rom.extra", "addition outside prefix"),
                 (lambda data: data["layout"].update(window_extra=[{"name": "menu", "width": 32}]),
                  "layout.window_extra[name=menu]", "addition outside prefix"))
        for change, path, reason in cases:
            with self.subTest(reason=reason, path=path):
                data = copy.deepcopy(migrated)
                change(data)
                self.write_after(data)
                rows = profile.compare_sets(self.before, self.after, allow_added_prefix="layout.window[")
                rejected = [row for row in rows if not row.allowed]
                self.assertEqual([(row.path, row.reason) for row in rejected], [(path, reason)])
                if path == "layout.window_width.first":
                    self.assertNotIn("$.layout.window_width", [row.path for row in rows if row.reason == "key order"])
                status, out, err = self.invoke("verify", "profile", "--compare", self.before, self.after,
                                              "--set", "--allow-added-prefix", "layout.window[")
                self.assertEqual(status, 1)
                self.assertIn(reason, out)
                self.assertEqual(err.splitlines()[-1], f"result       failed: profile proof differs: {path} ({reason})")
        self.assertEqual(json.loads((self.before / "document.json").read_bytes()), baseline)

    def test_set_mode_requires_explicit_nonempty_addition_prefix(self):
        self.window_records()
        for prefix in (None, "rom."):
            with self.subTest(prefix=prefix):
                rows = profile.compare_sets(self.before, self.after, allow_added_prefix=prefix)
                self.assertEqual([(row.path, row.reason) for row in rows if not row.allowed],
                                 [("layout.window[name=menu-status]", "addition outside prefix")])
        cases = (("--record", self.root / "unused", "--set"),
                 ("--compare", self.before, self.after, "--allow-added-prefix", "layout.window["),
                 ("--compare", self.before, self.after, "--set", "--allow-added-prefix", ""))
        for arguments, reason in zip(cases, ("--set requires --compare", "--allow-added-prefix requires --set",
                                            "empty allowed fact prefix")):
            with self.subTest(arguments=arguments):
                status, out, err = self.invoke("verify", "profile", *arguments)
                self.assertEqual(status, 1)
                self.assertEqual(err.splitlines()[-1], f"result       failed: {reason}")
        self.assertFalse((self.root / "unused").exists())

    def test_set_mode_does_not_exempt_existing_window_facts(self):
        self.window_records()
        original = json.loads((self.after / "document.json").read_bytes())
        self.before, self.after = self.after, self.root / "changed"
        for change, reason in ((lambda data: data["layout"]["window"][0].update(width=33), "changed value"),
                               (lambda data: data["layout"].pop("window"), "lost fact")):
            with self.subTest(reason=reason):
                data = copy.deepcopy(original)
                change(data)
                self.write_after(data)
                rows = profile.compare_sets(self.before, self.after, allow_added_prefix="layout.window[")
                self.assertEqual([(row.path, row.reason) for row in rows if not row.allowed],
                                 [("layout.window[name=menu-status]", reason)])

    def test_set_mode_canonical_values_preserve_types_lists_and_prose(self):
        profile.record(self.source, self.before)
        original = schema.load(self.source)
        for values in ({"offset": 768.0}, {"offset": True}, {"pointers": [0x144, 0x140]},
                       {"evidence": "Changed evidence."}):
            with self.subTest(values=values):
                data = copy.deepcopy(original)
                data["probe"]["menu"].update(values)
                self.write_after(data)
                rows = profile.compare_sets(self.before, self.after)
                self.assertEqual([(row.path, row.reason, row.allowed) for row in rows],
                                 [("probe.menu", "changed value", False)])

    def test_set_mode_accepts_canonical_key_order_and_formatting(self):
        self.changed_record(lambda data: data["probe"].update(menu=dict(reversed(list(data["probe"]["menu"].items())))))
        path = self.after / "document.json"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        rows = profile.compare_sets(self.before, self.after)
        self.assertEqual([(row.path, row.reason, row.allowed) for row in rows], [("$.probe.menu", "key order", True)])
        self.assertEqual(profile.compare_sets(self.before, self.before), [])

    def test_set_mode_validates_recorded_fact_ids(self):
        profile.record(self.source, self.before)
        profile.record(self.source, self.after)
        path = self.after / "fact-ids.json"
        ids = json.loads(path.read_bytes())
        for changed in (ids[:-1], ids + [ids[0]], ids + ["layout.window[name=extra]"]):
            with self.subTest(ids=changed):
                path.write_text(json.dumps(changed) + "\n", encoding="utf-8")
                status, out, err = self.invoke("verify", "profile", "--compare", self.before, self.after,
                                              "--set", "--allow-added-prefix", "layout.window[")
                self.assertEqual(status, 1)
                self.assertIn("fact ids differ from document", err.splitlines()[-1])
                self.assertNotIn("Traceback", out + err)


if __name__ == "__main__":
    unittest.main()
