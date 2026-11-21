"""Typed overlay cells, refusals, canonical writes and manifest loading."""

import io
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from gen3tl.cli.main import main
from gen3tl.profile import profile, schema, tsv
from gen3tl.verify import profile as proof
from project_fixture import SAMPLE


class TableTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "rows.tsv"

    def refusal(self, call, row, column, message):
        with self.assertRaises(tsv.TSVError) as caught:
            call()
        text = str(caught.exception)
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn(str(self.path), text)
        self.assertIn(f"row {row}, column {column}:", text)
        self.assertIn(message, text)

    def test_canonical_cells_round_trip_without_losing_absent_keys(self):
        columns = {"integer": "int", "address": "hex", "text": "str", "numbers": "int[]",
                   "addresses": "hex[]", "names": "str[]", "optional": "str"}
        canonical = ("integer\taddress\ttext\tnumbers\taddresses\tnames\toptional\n"
                     "-12\t0xcafe\tPokémon, \\tline\\npath\\\\end\t0,-2,15\t0x0,0xff,-1\t one,two \\t\\n\\\\\t\n"
                     "\t\t\t\t\t\tlast\n"
                     "\t\t\t\t\t\t\n").encode("utf-8")
        self.path.write_bytes(canonical)
        expected = [{"integer": -12, "address": 0xcafe, "text": "Pokémon, \tline\npath\\end",
                     "numbers": [0, -2, 15], "addresses": [0, 255, -1], "names": [" one", "two \t\n\\"]},
                    {"optional": "last"}, {}]
        self.assertEqual(tsv.read(self.path, columns), expected)
        tsv.write(self.path, expected, columns)
        self.assertEqual(self.path.read_bytes(), canonical)
        self.assertEqual(tsv.read(self.path, columns), expected)

    def test_toml_integers_and_numeric_looking_strings(self):
        values = ("0", "-0", "+0", "+42", "-123", "1_234", "0xDe_ad", "0o7_7", "0b1_0")
        for kind in ("int", "hex"):
            with self.subTest(kind=kind):
                self.path.write_text("number\ttext\n" + "".join(f"{v}\t{v}\n" for v in values), encoding="utf-8")
                columns = {"number": kind, "text": "str"}
                rows = tsv.read(self.path, columns)
                self.assertEqual(rows, [{"number": tomllib.loads(f"n = {v}")["n"], "text": v} for v in values])
                tsv.write(self.path, rows, columns)
                first = self.path.read_bytes()
                tsv.write(self.path, tsv.read(self.path, columns), columns)
                self.assertEqual(self.path.read_bytes(), first)

    def test_boolean_cells_preserve_false_and_reject_numbers(self):
        columns = {"instant": "bool"}
        rows = [{"instant": True}, {"instant": False}, {}]
        tsv.write(self.path, rows, columns)
        self.assertEqual(self.path.read_text(), "instant\ntrue\nfalse\n\n")
        self.assertEqual(tsv.read(self.path, columns), rows)
        for value in ("True", "FALSE", "1", "0", " true"):
            self.path.write_text(f"instant\n{value}\n")
            self.refusal(lambda: tsv.read(self.path, columns), 2, 1, "expected true or false")
        for value in (1, 0, "true", None):
            self.refusal(lambda: tsv.write(self.path, [{"instant": value}], columns), 2, 1, "expected boolean")

    def test_empty_table_and_optional_final_lf(self):
        columns = {"text": "str"}
        for raw, expected in ((b"text\n", []), (b"text", []), (b"text\nvalue", [{"text": "value"}]),
                              (b"text\n\n", [{}])):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                self.assertEqual(tsv.read(self.path, columns), expected)
                tsv.write(self.path, expected, columns)
                self.assertEqual(tsv.read(self.path, columns), expected)

    def test_strings_have_no_quoting_trimming_or_comment_syntax(self):
        text = ' # "a,b" \u2028 literal\\t '
        tsv.write(self.path, [{"text": text}], {"text": "str"})
        self.assertEqual(tsv.read(self.path, {"text": "str"}), [{"text": text}])

    def test_header_must_match_the_declared_schema(self):
        cases = (("", 1, "missing header"), ("name\tunknown\n", 2, "header expected"),
                 ("count\tname\n", 1, "header expected"), ("name\n", 2, "header expected"),
                 ("name\tname\n", 2, "header expected"), ("name\tcount\textra\n", 3, "header expected"),
                 ("name\t\n", 2, "header expected"), ("\ufeffname\tcount\n", 1, "header expected"))
        for raw, column, message in cases:
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding="utf-8")
                self.refusal(lambda: tsv.read(self.path, {"name": "str", "count": "int"}), 1, column, message)

    def test_refused_rows_report_the_cell(self):
        cases = ((b"name\tcount\nonly\n", 2, "cell count"),
                 (b"name\tcount\na\t1\textra\n", 3, "cell count"),
                 (b"name\tcount\n\n", 2, "cell count"),
                 (b"name\tcount\na\t1\r\n", 2, "carriage return"),
                 (b"name\tcount\na\r\t1\n", 1, "carriage return"),
                 (b"name\tcount\na\t\xff\n", 2, "invalid UTF-8"),
                 (b"name\tcount\nends\\\t1\n", 1, "unfinished escape"),
                 (b"name\tcount\nunknown\\r\t1\n", 1, "unknown escape"))
        for raw, column, message in cases:
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                self.refusal(lambda: tsv.read(self.path, {"name": "str", "count": "int"}), 2, column, message)

    def test_refused_integer_forms(self):
        for value in ("true", "false", "1.0", "nan", "inf", "01", "-01", "0X12", "-0x1", "+0x1",
                      "0x", "0x_1", "0b2", "0o8", "1__0", "1_", "_1", "1 # note", " 1", "1 ",
                      "[1]", '"1"', "\\n1", "١", "1e3"):
            with self.subTest(value=value):
                self.path.write_text(f"number\n{value}\n", encoding="utf-8")
                self.refusal(lambda: tsv.read(self.path, {"number": "int"}), 2, 1, "invalid TOML integer")

    def test_refused_list_forms_and_escapes(self):
        for kind in ("str[]", "int[]", "hex[]"):
            for value, message in ((",one", "empty list item"), ("one,", "empty list item"),
                                   ("one,,two", "empty list item"), ("1,\\q", "unknown escape"),
                                   ("1,\\", "unfinished escape")):
                with self.subTest(kind=kind, value=value):
                    self.path.write_text(f"items\n{value}\n", encoding="utf-8")
                    self.refusal(lambda: tsv.read(self.path, {"items": kind}), 2, 1, message)
        self.path.write_text("items\n1,no\n", encoding="utf-8")
        self.refusal(lambda: tsv.read(self.path, {"items": "int[]"}), 2, 1, "invalid TOML integer")

    def test_writer_refuses_lossy_or_wrongly_typed_values_without_overwriting(self):
        cases = (("str", "", "nonempty string"), ("str", 1, "nonempty string"),
                 ("str", "a\rb", "carriage return"), ("str", "\ud800", "surrogates"),
                 ("int", True, "integer"), ("int", 1.0, "integer"), ("hex", "1", "integer"),
                 ("int", None, "integer"), ("str[]", [], "nonempty list"),
                 ("int[]", (1,), "nonempty list"), ("int[]", [True], "integer"),
                 ("hex[]", [[1]], "integer"), ("str[]", [""], "nonempty string"),
                 ("str[]", ["a,b"], "comma in list item"))
        self.path.write_bytes(b"original")
        for kind, value, message in cases:
            with self.subTest(kind=kind, value=value):
                self.refusal(lambda: tsv.write(self.path, [{"value": value}], {"value": kind}), 2, 1, message)
                self.assertEqual(self.path.read_bytes(), b"original")
        self.refusal(lambda: tsv.write(self.path, [1], {"value": "str"}), 2, 1, "expected table")
        self.refusal(lambda: tsv.write(self.path, [{"extra": 1}], {"value": "str"}), 2, 2, "unknown key")

    def test_invalid_schemas_and_io_errors_have_locations(self):
        for columns, column, message in (({}, 1, "empty column schema"), ({"bad.name": "str"}, 1, "column name"),
                                         ({"name": "str", "flag": "float"}, 2, "unknown cell type")):
            with self.subTest(columns=columns):
                self.refusal(lambda: tsv.read(self.path, columns), 1, column, message)
                self.refusal(lambda: tsv.write(self.path, [], columns), 1, column, message)
        self.refusal(lambda: tsv.read(self.path, {"value": "str"}), 1, 1, "read failed")
        self.path.mkdir()
        self.refusal(lambda: tsv.write(self.path, [], {"value": "str"}), 1, 1, "write failed")


class OverlayTableTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "projects.toml").write_text("", encoding="utf-8")
        self.directory = self.root / "game"
        self.overlay = self.directory / "overlay"
        (self.overlay / "pokedex/items").mkdir(parents=True)
        (self.directory / "profile.toml").write_text('[discovery]\nschema = 1\n', encoding="utf-8")
        self.identity = profile.PROFILE_DIR / f"{SAMPLE}.toml"
        (self.overlay / "identity.toml").write_bytes(self.identity.read_bytes())
        self.order = self.overlay / "order.txt"
        self.order.write_text("identity.toml\npokedex/items/shops.tsv\tpokedex.items.sources.shops\n", encoding="utf-8")
        self.table = self.overlay / "pokedex/items/shops.tsv"
        self.table.write_text("map\tmap_section\toffset\tlist\titems\n"
                              "2.0\t0x12\t0x100\t0x200\t4,0x10\n"
                              "1.0\t\t0x300\t\t\n", encoding="utf-8")

    def test_sample_manifest_matches_toml_and_proof_bytes(self):
        single = self.root / "single.toml"
        single.write_text(self.identity.read_text(encoding="utf-8") + '\n[[pokedex.items.sources.shops]]\n'
                          'map = "2.0"\nmap_section = 0x12\noffset = 0x100\nlist = 0x200\nitems = [4, 0x10]\n'
                          '[[pokedex.items.sources.shops]]\nmap = "1.0"\noffset = 0x300\n', encoding="utf-8")
        self.assertEqual(schema.load(self.directory), schema.load(single))
        self.assertEqual(proof.record(self.directory, self.root / "tsv-proof"),
                         proof.record(single, self.root / "toml-proof"))
        self.assertEqual(profile.source_files(self.directory), [self.directory / "profile.toml", self.order,
                                                               self.overlay / "identity.toml", self.table])
        self.assertEqual(profile.locate(str(self.table)), self.directory)

    def test_evidence_and_string_lists_reach_facts_in_row_order(self):
        folder = self.overlay / "reach"
        folder.mkdir()
        file = folder / "never-shown.tsv"
        rows = [{"ids": ["entry:2", "entry:1"], "evidence": "Two paths.\nOne reader."},
                {"ids": ["entry:3"], "evidence": "Unused branch."}]
        tsv.write(file, rows, schema.OVERLAY_TABLES["reach/never-shown.tsv"][1])
        self.order.write_text(self.order.read_text() + "reach/never-shown.tsv\treach.never_shown\n", encoding="utf-8")
        data = schema.load(self.directory)
        self.assertEqual(data["reach"]["never_shown"], rows)
        facts = [fact for fact in schema.facts(data) if fact.section == "reach.never_shown"]
        self.assertEqual([fact.id for fact in facts],
                         ["reach.never_shown[ids=entry:2,entry:1]", "reach.never_shown[ids=entry:3]"])

    def test_manifest_refusals_and_cli_error_lines(self):
        cases = (("pokedex/items/shops.tsv", 2, "expected document path"),
                 ("pokedex/items/shops.tsv\twrong", 2, "expected document path"),
                 ("pokedex/items/shops.tsv\tpokedex.items.sources.shops\textra", 2, "expected document path"),
                 ("other.tsv\tpokedex.items.sources.shops", 1, "undeclared TSV file"),
                 ("identity.toml\trom", 2, "TOML fragment takes no document path"),
                 ("../shops.tsv\tpokedex.items.sources.shops", 1, "invalid overlay file"),
                 ("pokedex/items/shops.tsv\tpokedex.items.sources.shops\n"
                  "pokedex/items/shops.tsv\tpokedex.items.sources.shops", 1, "repeated file"))
        for line, column, message in cases:
            with self.subTest(line=line):
                self.order.write_text("identity.toml\n" + line + "\n", encoding="utf-8")
                self.assert_cli_error(message, self.order, 3 if message == "repeated file" else 2, column)
        self.order.write_text("identity.toml\npokedex/items/shops.tsv\tpokedex.items.sources.shops\n", encoding="utf-8")
        self.table.write_text("map\tmap_section\toffset\tlist\titems\nx\tbad\t\t\t\n", encoding="utf-8")
        self.assert_cli_error("invalid TOML integer", self.table, 2, 2)
        self.table.unlink()
        self.assert_cli_error("missing file", self.order, 2, 1)

    def test_form_labels_preserve_optional_values_and_proof_bytes(self):
        file = self.overlay / "pokedex/form-names.tsv"
        file.write_text("species\ttext\tfull\texpected_gender\n"
                        "7\tpage:Alternate\ttrue\t\n"
                        "8\tentry:8\t\t\n"
                        "9\tpage:Other\tfalse\t0\n"
                        "10\tpage:Variant\t\t254\n", encoding="utf-8")
        fragment = ('[pokedex.form_names]\nlabels = [\n'
                    '{species = 7, text = "page:Alternate", full = true},\n'
                    '{species = 8, text = "entry:8"},\n'
                    '{species = 9, text = "page:Other", full = false, expected_gender = 0},\n'
                    '{species = 10, text = "page:Variant", expected_gender = 254},\n]\n')
        self.assert_table_proof(file, "pokedex.form_names.labels", fragment)

    def test_progress_preserves_lists_evidence_and_proof_bytes(self):
        file = self.overlay / "progress.tsv"
        file.write_text("topic\tfiles\tevidence\n"
                        "Names\tnames,labels\tTwo tables.\\nOne reader.\n"
                        "Descriptions\tdescriptions\t\n", encoding="utf-8")
        fragment = ('[progress]\ntopics = [\n'
                    '{topic = "Names", files = ["names", "labels"], evidence = "Two tables.\\nOne reader."},\n'
                    '{topic = "Descriptions", files = ["descriptions"]},\n]\n')
        self.assert_table_proof(file, "progress.topics", fragment)

    def assert_table_proof(self, file, document_path, fragment):
        self.table.unlink()
        self.order.write_text(f"identity.toml\n{file.relative_to(self.overlay)}\t{document_path}\n",
                              encoding="utf-8")
        single = self.root / "single.toml"
        single.write_text(self.identity.read_text(encoding="utf-8") + "\n" + fragment, encoding="utf-8")
        self.assertEqual(proof.record(self.directory, self.root / "tsv-proof"),
                         proof.record(single, self.root / "toml-proof"))

    def test_toml_and_tsv_cannot_define_the_same_array(self):
        (self.overlay / "identity.toml").write_text('[[pokedex.items.sources.shops]]\nmap = "2.0"\n', encoding="utf-8")
        self.assert_cli_error("duplicate overlay key pokedex.items.sources.shops", self.table, 1, 1)

    def assert_cli_error(self, message, file, row, column):
        with self.assertRaisesRegex(schema.ProfileError, message):
            schema.load(self.directory)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(["--project-root", str(self.root), "--profile", str(self.directory),
                           "verify", "profile", "--record", str(self.root / "proof")])
        self.assertNotEqual(status, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual([line.split()[0] for line in err.getvalue().splitlines()], ["error", "result"])
        self.assertIn(str(file), err.getvalue())
        self.assertIn(f"row {row}, column {column}:", err.getvalue())
        self.assertIn(message, err.getvalue())


if __name__ == "__main__":
    unittest.main()
