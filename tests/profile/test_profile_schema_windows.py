from test_roots import TOOLKIT_ROOT
"""Profile schema windows checks and fixtures."""

import sys
from pathlib import Path
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from unittest.mock import patch

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.cli.main import main
from gen3tl.profile import profile, schema, tsv
from gen3tl.profile.profile import Profile
from project_fixture import SAMPLE
from profile_schema_support import HEADER, write_dir


class WindowTableTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "projects.toml").write_text("", encoding="utf-8")
        self.path = write_dir(self.root, "game", HEADER, None)
        self.overlay = self.path / "overlay"
        (self.overlay / "layout").mkdir(parents=True)
        source = profile.PROFILE_DIR / f"{SAMPLE}.toml"
        (self.overlay / "identity.toml").write_bytes(source.read_bytes())
        self.windows = self.overlay / "layout/windows.tsv"
        self.strings = self.overlay / "layout/window-strings.tsv"
        self.maps = self.overlay / "layout/maps.toml"
        self.maps.write_text("", encoding="utf-8")
        self.rows = [{"name": "menu-status", "width": 32, "style": "small",
                      "evidence": "One call per status row.\nThe reader selects the small font."},
                     {"name": "menu-message", "width": 220, "box": "dialogue"},
                     {"name": "menu-message-two-lines", "width": 220, "box": "dialogue", "lines": 2}]
        self.bindings = [{"string": "s0000300", "window": "menu-status"},
                         {"string": "items:0", "window": "menu-status"},
                         {"string": "s0000310", "window": "menu-message"},
                         {"string": "s0000330", "window": "menu-message-two-lines"}]
        self.manifest = ["identity.toml", "layout/maps.toml", "layout/windows.tsv\tlayout.window",
                         "layout/window-strings.tsv\tlayout.window_strings"]
        self.write_tables()

    def write_tables(self, reverse=False):
        for file, rows in ((self.windows, self.rows), (self.strings, self.bindings)):
            columns = schema.OVERLAY_TABLES[file.relative_to(self.overlay).as_posix()][1]
            tsv.write(file, rows, columns)
        lines = list(reversed(self.manifest)) if reverse else self.manifest
        (self.overlay / "order.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def assert_refusal(self, file, row, column, message):
        with self.assertRaises(schema.ProfileError) as caught:
            schema.load(self.path)
        text = str(caught.exception)
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn(str(file), text)
        self.assertIn(f"row {row}, column {column}:", text)
        self.assertIn(message, text)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(["--project-root", str(self.root), "--profile", str(self.path),
                           "verify", "profile", "--record", str(self.root / "proof")])
        self.assertNotEqual(status, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual([line.split()[0] for line in err.getvalue().splitlines()], ["error", "result"])
        self.assertIn(str(file), err.getvalue())
        self.assertIn(f"row {row}, column {column}:", err.getvalue())
        self.assertIn(message, err.getvalue())

    def test_sample_windows_keep_map_values_and_fact_ids(self):
        old = ('[layout.window_width]\ns0000300 = 32\n"items:0" = 32\ns0000310 = 220\ns0000330 = 220\n'
               '[layout.window_box]\ns0000310 = "dialogue"\ns0000330 = "dialogue"\n'
               '[layout.window_lines]\ns0000330 = 2\n'
               '[layout.window_style]\ns0000300 = "small"\n"items:0" = "small"\n')
        single = self.root / "before.toml"
        single.write_text((self.overlay / "identity.toml").read_text() + "\n" + old, encoding="utf-8")
        expected = schema.load(single)
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.write_tables(reverse)
                loaded = Profile.load(self.path)
                data = deepcopy(loaded.data)
                self.assertEqual(data["layout"].pop("window"), self.rows)
                self.assertEqual(data, expected)
                old_facts = {fact.id: fact.value for fact in schema.facts(expected)}
                facts = {fact.id: fact for fact in schema.overlay_facts(schema.read(self.path))}
                window_ids = {f"layout.window[name={row['name']}]" for row in self.rows}
                self.assertEqual(set(facts) - set(old_facts), window_ids)
                self.assertEqual({key: fact.value for key, fact in facts.items() if key not in window_ids}, old_facts)
                self.assertEqual(facts['layout.window_width."items:0"'].value, 32)
                self.assertEqual(facts['layout.window_style."items:0"'].value, "small")
                for row in self.rows:
                    fact = facts[f"layout.window[name={row['name']}]"]
                    self.assertEqual(fact.value, row)
                    self.assertEqual(fact.kind, "window")
                    if "evidence" in row:
                        self.assertEqual(fact.evidence.sentence, row["evidence"])
                self.assertIn(self.windows, profile.source_files(self.path))
                self.assertIn(self.strings, profile.source_files(self.path))

    def test_window_description_reaches_editor_labels(self):
        from gen3tl.catalog import keys, po
        from gen3tl.web import editordata

        description = "Menu status window.\nRight-aligned labels."
        self.rows[0]["where"] = description
        self.write_tables()
        loaded = Profile.load(self.path)
        self.assertEqual(loaded["layout"]["window"][0]["where"], description)
        self.assertNotIn("where", loaded["layout"]["window"][1])
        self.assertNotIn("window_where", loaded["layout"])
        labels = self.root / "labels.po"
        po.write(labels, [po.PoEntry("layout.window[name=menu-status].where", description, "Status label")], {})
        with patch.object(editordata, "profile_label_inputs", return_value={"aa": labels}):
            self.assertEqual(editordata.where_texts(loaded, keys.EMPTY), {description: {"aa": "Status label"}})

    def test_window_description_reaches_bound_consumer(self):
        from gen3tl.check.qa import window_consumer

        self.rows[0].update(where="Menu status window", newline_scroll_after=1)
        self.write_tables()
        loaded = Profile.load(self.path)
        rule = loaded["layout"]["window_binding"]["s0000300"]
        self.assertEqual(rule["where"], "Menu status window")
        consumer = window_consumer(32, {"rule": "measured window"}, 0, rule)
        self.assertEqual(consumer["name"], rule["where"])

    def test_window_description_round_trip_preserves_optional_cells(self):
        self.rows[0]["where"] = "Menu\tstatus\\label\nSecond line"
        self.write_tables()
        columns = schema.OVERLAY_TABLES["layout/windows.tsv"][1]
        rows = tsv.read(self.windows, columns)
        self.assertEqual(rows, self.rows)
        self.assertNotIn("where", rows[1])
        before = self.windows.read_bytes()
        tsv.write(self.windows, rows, columns)
        self.assertEqual(self.windows.read_bytes(), before)
        self.assertEqual(schema.load(self.path)["layout"]["window"], rows)

    def test_prompt_arrow_survives_window_expansion(self):
        self.rows[0]["prompt_arrow_width"] = 0
        self.write_tables()
        data = schema.load(self.path)
        self.assertEqual(data["layout"]["window_prompt_arrow_width"], {"s0000300": 0, "items:0": 0})
        flat = self.root / "arrows.toml"
        flat.write_text('[[layout.window]]\nname = "help-window"\nprompt_arrow_width = 0\n'
                        'evidence = "The bitmap printer advances without waiting."\n'
                        '[[layout.window_strings]]\nstring = "s0000300"\nwindow = "help-window"\n')
        self.assertEqual(schema.load(flat)["layout"]["window_prompt_arrow_width"], {"s0000300": 0})
        self.assertNotIn("window_strings", schema.load(flat)["layout"])

    def test_runtime_scroll_binding_and_validation(self):
        self.rows[0]["newline_scroll_after"] = 1
        self.write_tables()
        data = schema.load(self.path)["layout"]
        self.assertEqual(data["window_newline_scroll_after"], {"s0000300": 1, "items:0": 1})
        self.assertEqual(data["window_binding"]["s0000300"], self.rows[0])
        flat = self.root / "scroll.toml"
        flat.write_text('[[layout.window]]\nname = "scroll"\nnewline_scroll_after = 1\n'
                        'evidence = "The printer converts later newlines."\n'
                        '[[layout.window_strings]]\nstring = "s0000300"\nwindow = "scroll"\n')
        self.assertEqual(schema.load(flat)["layout"]["window_newline_scroll_after"], {"s0000300": 1})
        for section in ("window", "table_window", "record_window"):
            for value in (-1, True, 1.5, "1", None):
                with self.subTest(section=section, value=value), self.assertRaisesRegex(
                        schema.ProfileError, "nonnegative integer"):
                    schema.validate_prompt_arrows({section: [{"newline_scroll_after": value, "evidence": "Printer."}]})
            with self.assertRaisesRegex(schema.ProfileError, "requires printer evidence"):
                schema.validate_prompt_arrows({section: [{"newline_scroll_after": 1}]})
            for value in (0, 1, 2):
                schema.validate_prompt_arrows({section: [{"newline_scroll_after": value, "evidence": "Printer."}]})

    def test_instant_print_binding_and_validation(self):
        self.rows[0]["instant_print"] = True
        self.rows[1].update(instant_print=False, evidence="The printer waits for input.")
        self.write_tables()
        data = schema.load(self.path)["layout"]
        self.assertEqual(data["window_instant_print"], {"s0000300": True, "items:0": True, "s0000310": False})
        self.assertEqual(data["window_binding"]["s0000300"], self.rows[0])
        flat = self.root / "instant.toml"
        flat.write_text('[[layout.window]]\nname = "banner"\ninstant_print = true\n'
                        'evidence = "The printer runs instantly."\n'
                        '[[layout.window_strings]]\nstring = "s0000300"\nwindow = "banner"\n')
        self.assertEqual(schema.load(flat)["layout"]["window_instant_print"], {"s0000300": True})
        for section in ("window", "table_window", "record_window"):
            for value in (0, 1, -1, 1.5, "true", None):
                with self.subTest(section=section, value=value), self.assertRaisesRegex(schema.ProfileError, "boolean"):
                    schema.validate_prompt_arrows({section: [{"instant_print": value, "evidence": "Printer."}]})
            for value in (True, False):
                with self.assertRaisesRegex(schema.ProfileError, "requires printer evidence"):
                    schema.validate_prompt_arrows({section: [{"instant_print": value}]})
                schema.validate_prompt_arrows({section: [{"instant_print": value, "evidence": "Printer."}]})

    def test_prompt_arrow_requires_width_and_evidence(self):
        for section in ("window", "table_window", "record_window"):
            for value in (-1, True, 1.5, "10", None):
                with self.subTest(section=section, value=value), self.assertRaisesRegex(
                        schema.ProfileError, "nonnegative integer"):
                    schema.validate_prompt_arrows({section: [{"prompt_arrow_width": value, "evidence": "Printer."}]})
            with self.assertRaisesRegex(schema.ProfileError, "requires printer evidence"):
                schema.validate_prompt_arrows({section: [{"prompt_arrow_width": 0}]})
            for value in (0, 12):
                schema.validate_prompt_arrows({section: [{"prompt_arrow_width": value, "evidence": "Printer."}]})

    def test_toml_maps_merge_and_keep_existing_keys_first(self):
        self.maps.write_text('[layout.window_width]\nlegacy = 10\n'
                             '[layout.window_box]\nlegacy = "menu"\n'
                             '[layout.window_lines]\nlegacy = 1\n'
                             '[layout.window_style]\nlegacy = "main"\n', encoding="utf-8")
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.write_tables(reverse)
                layout = schema.load(self.path)["layout"]
                for field, value in (("width", 10), ("box", "menu"), ("lines", 1), ("style", "main")):
                    self.assertEqual(next(iter(layout[f"window_{field}"])), "legacy")
                    self.assertEqual(layout[f"window_{field}"]["legacy"], value)
                self.assertEqual(list(layout["window_width"]),
                                 ["legacy", "s0000300", "items:0", "s0000310", "s0000330"])
                self.assertEqual(layout["window_lines"], {"legacy": 1, "s0000330": 2})

    def test_string_in_any_toml_map_is_refused_in_either_order(self):
        for field, value in (("width", "32"), ("box", '"menu"'), ("lines", "2"), ("style", '"small"')):
            for reverse in (False, True):
                with self.subTest(field=field, reverse=reverse):
                    self.maps.write_text(f'[layout.window_{field}]\n"items:0" = {value}\n', encoding="utf-8")
                    self.write_tables(reverse)
                    self.assert_refusal(self.strings, 3, 1, "already in an overlay TOML window map")

    def test_unknown_window_is_refused_at_the_binding(self):
        self.bindings[1]["window"] = "unknown\nwindow"
        self.write_tables()
        self.assert_refusal(self.strings, 3, 2, "unknown window")

    def test_unused_window_is_refused_at_its_name(self):
        self.bindings.pop()
        self.write_tables()
        self.assert_refusal(self.windows, 4, 1, "has no strings")

    def test_duplicate_window_name_is_refused_even_with_equal_values(self):
        self.rows.append(dict(self.rows[0]))
        self.write_tables()
        self.assert_refusal(self.windows, 5, 1, "duplicate window name")

    def test_duplicate_string_is_refused_for_same_or_different_windows(self):
        for name in ("menu-status", "menu-message"):
            with self.subTest(name=name):
                self.bindings[1] = {"string": "s0000300", "window": name}
                self.write_tables()
                self.assert_refusal(self.strings, 3, 1, "duplicate string")

    def test_window_name_uses_lowercase_hyphenated_words(self):
        for name in (None, "Menu-status", "menu_status", "menu--status", "menu-", "-menu", "1-menu", " "):
            with self.subTest(name=name):
                self.rows[0].pop("name", None)
                if name is not None:
                    self.rows[0]["name"] = name
                self.write_tables()
                self.assert_refusal(self.windows, 2, 1, "invalid window name")

    def test_binding_cells_are_required(self):
        for column, key in enumerate(("string", "window"), 1):
            with self.subTest(key=key):
                self.bindings[0] = {"string": "s0000300", "window": "menu-status"}
                del self.bindings[0][key]
                self.write_tables()
                self.assert_refusal(self.strings, 2, column, f"missing {key}")

    def test_missing_companion_table_reports_the_unresolved_row(self):
        self.strings.unlink()
        self.manifest.pop()
        (self.overlay / "order.txt").write_text("\n".join(self.manifest) + "\n", encoding="utf-8")
        self.assert_refusal(self.windows, 2, 1, "has no strings")
        self.write_tables()
        self.windows.unlink()
        self.manifest[-1] = "layout/window-strings.tsv\tlayout.window_strings"
        (self.overlay / "order.txt").write_text("\n".join(self.manifest) + "\n", encoding="utf-8")
        self.assert_refusal(self.strings, 2, 2, "unknown window")

    def test_empty_measures_create_no_default_map_entries(self):
        self.rows = [{"name": "menu-status", "style": "small"}]
        self.bindings = self.bindings[:2]
        self.write_tables()
        self.assertEqual(schema.load(self.path)["layout"],
                         {"window": self.rows, "window_style": {"s0000300": "small", "items:0": "small"}})
