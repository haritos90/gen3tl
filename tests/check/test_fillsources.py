import copy
import unittest
from pathlib import Path

from gen3tl.check.fillsources import Evaluator, fragment, graph, validate
from gen3tl.check.layout import Measure
from gen3tl.core.charmap import Charmap
from gen3tl.catalog.po import PoEntry

class FillSourcesTest(unittest.TestCase):
    def setUp(self):
        self.cm = Charmap()
        self.measure = Measure(lambda b: 2 if b == 0 else 6, {}, styles={"main": lambda b: 2 if b == 0 else 6})
        self.catalog = {sid: (Path("test.po"), PoEntry(sid, text, text))
                        for sid, text in {"p1": "ABC ", "p2": "D ", "name": "AB"}.items()}
        self.prof = {"font": [{"name": "printer", "style": "main"}], "layout": {"fill_source": {
            "prefix": {"label": "Prefix", "font": "printer", "evidence": "Verified literals",
                       "value": {"max": {"first": {"entries": ["p1"]}, "second": {"entries": ["p2"]}}}},
            "name": {"label": "Name", "font": "printer", "evidence": "Verified field",
                     "value": {"max": {"typed": {"letters": 2, "alphabet": "naming"},
                                       "named": {"entries": ["name"]}}}},
            "combined": {"label": "Prefixed name", "font": "printer", "evidence": "Verified concatenation",
                         "value": {"concat": [{"source": "prefix"}, {"source": "name"}]}}}}}

    def evaluator(self, drafts=None, originals=None, catalog=None):
        c = self.catalog if catalog is None else catalog
        g = graph(self.prof, getattr(self, "inventory", {"entries": []}), c, alphabet="AB")
        return Evaluator(g, c, self.measure, self.cm, originals, drafts)

    def test_internal_and_terminal_spaces_are_distinct(self):
        result = self.evaluator().result("combined", 32)
        self.assertEqual(result["width"], 32)
        self.assertEqual(self.evaluator().result("prefix")["advance"], 20)
        self.assertEqual(self.evaluator().result("prefix")["extent"], 18)
        self.assertEqual(result["dependencies"], ["name", "p1", "p2"])
        self.assertEqual(result["contributors"][0]["id"], "p1")

    def test_draft_changes_winner_and_undo_restores_it(self):
        edited = self.evaluator({"p2": "ABCD "}).result("combined")
        self.assertEqual(edited["width"], 38)
        self.assertEqual(edited["contributors"][0]["id"], "p2")
        self.assertEqual(self.evaluator().result("combined")["width"], 32)

    def test_original_facts_match_live_text_and_font_changes(self):
        facts = {sid: fragment(e.msgid, self.cm) for sid, (_, e) in self.catalog.items()}
        offline = {sid: {"translation": ""} for sid in facts}
        evaluator = Evaluator(self.evaluator().graph, offline, self.measure, self.cm, facts)
        self.assertEqual(evaluator.result("combined")["width"], 32)
        self.measure.styles["main"] = lambda b: 3 if b == 0 else 8
        evaluator = Evaluator(evaluator.graph, offline, self.measure, self.cm, facts)
        self.assertEqual(evaluator.result("combined")["width"], 43)

    def test_empty_and_original_states(self):
        empty = self.evaluator({"p1": {"status": "empty", "text": ""}}).result("combined")
        self.assertEqual(empty["width"], 20)
        original = self.evaluator({"p1": {"status": "original", "text": ""}}).result("combined")
        self.assertEqual(original["width"], 32)
        self.assertTrue(original["contributors"][0]["original"])

    def test_recorded_and_reasoned_override_states(self):
        self.assertEqual(self.evaluator().result(recorded=60)["state"], "recorded")
        override = {"width": 34, "source": "combined", "reason": "Printer reserves two pixels"}
        result = self.evaluator().result("combined", 32, override)
        self.assertEqual((result["width"], result["computed"], result["state"]), (34, 32, "override"))
        for change in ({"reason": ""}, {"source": "name"}, {"width": True}):
            with self.assertRaises(ValueError):
                self.evaluator().result("combined", 32, {**override, **change})

    def test_context_selects_declared_alternatives(self):
        self.prof["layout"]["slot_context"] = {"second-only": {"prefix": ["second"]}}
        self.assertEqual(self.evaluator().result("combined", context="second-only")["width"], 20)
        self.assertEqual(self.evaluator().result("combined")["width"], 32)
        with self.assertRaises(ValueError):
            self.evaluator().result("combined", context="unknown")
        self.prof["layout"]["slot_context"]["second-only"]["prefix"] = ["missing"]
        with self.assertRaises(ValueError):
            validate(self.prof)

    def test_paired_rows_reserve_the_host_separator_once(self):
        self.prof["layout"]["fill_source"]["paired-class"] = {
            "label": "Paired class", "font": "printer", "evidence": "Verified records",
            "value": {"paired": {"with": "name", "rows": [
                [{"entries": ["p1"]}, {"entries": ["name"]}],
                [{"entries": ["p2"]}, {"entries": ["p1"]}]]}}}
        resolved = graph(self.prof, {"entries": []}, self.catalog, alphabet="AB", selected={"paired-class"})
        self.assertEqual(resolved["dependencies"]["paired-class"], ["name", "p1", "p2"])
        evaluator = Evaluator(resolved, self.catalog, self.measure, self.cm)
        result = evaluator.result("paired-class")
        self.assertEqual((result["width"], result["source_width"]), (20, 20))
        host = Measure(self.measure.glyph_width, {"CLASS": result["width"], "NAME": 12})
        self.assertEqual(host.width("{CLASS} {NAME}", self.cm), self.measure.width("ABC  AB", self.cm))
        self.assertEqual(result["contributors"][0]["id"], "p1")
        edited = Evaluator(resolved, self.catalog, self.measure, self.cm,
                           drafts={"p2": "ABCDE "}).result("paired-class")
        self.assertEqual(edited["width"], 40)
        self.assertEqual(edited["contributors"][0]["id"], "p2")
        self.measure.styles["main"] = lambda b: 3 if b == 0 else 8
        changed = Evaluator(resolved, self.catalog, self.measure, self.cm).result("paired-class")
        self.assertEqual(changed["width"], 27)

    def test_paired_domain_rejects_invalid_rows_and_cycles(self):
        source = self.prof["layout"]["fill_source"]["name"]
        for value in ({"with": "missing", "rows": []}, {"with": "name", "rows": []},
                      {"with": "name", "rows": [[{"entries": ["p1"]}]]}):
            source["value"] = {"paired": value}
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(self.prof)
        source["value"] = {"paired": {"with": "prefix", "rows": [[{"entries": ["p1"]}, {"entries": ["p2"]}]]}}
        self.prof["layout"]["fill_source"]["prefix"]["value"] = {
            "paired": {"with": "name", "rows": [[{"entries": ["p1"]}, {"entries": ["p2"]}]]}}
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate(self.prof)

    def test_context_bindings_and_evidence_are_validated(self):
        self.prof["layout"]["window_slot_context"] = {"host": "missing"}
        with self.assertRaisesRegex(ValueError, "unknown context"):
            validate(self.prof)
        self.prof["layout"]["window_slot_context"] = {"host": "verified"}
        for row in ({"evidence": "No alternatives"}, {"prefix": ["first"], "evidence": ""}):
            self.prof["layout"]["slot_context"] = {"verified": row}
            with self.assertRaises(ValueError):
                validate(self.prof)

    def test_context_drafts_only_widen_permitted_prefixes(self):
        self.prof["layout"]["fill_source"]["prefix"]["value"]["max"]["none"] = {"empty": True}
        self.prof["layout"]["slot_context"] = {
            "trainer": {"prefix": ["none", "second"], "evidence": "Trainer opponent or player"},
            "player": {"prefix": ["none"], "evidence": "Player only"}}
        for drafts, expected in ((None, (20, 12, 32)), ({"p2": "ABCDE "}, (44, 12, 44)),
                                 ({"p1": "ABCDE "}, (20, 12, 44))):
            with self.subTest(drafts=drafts):
                evaluator = self.evaluator(drafts)
                self.assertEqual(tuple(evaluator.result("combined", context=context)["width"]
                                       for context in ("trainer", "player", None)), expected)
        empty = self.evaluator().result("combined", context="player")
        self.assertEqual(empty["contributors"][0], {"label": "No prefix", "width": 0, "original": False})
        self.assertEqual(self.evaluator().result("combined", context="trainer")["contributors"][0]["text"], "D ")

    def test_invalid_graphs_fail_and_missing_dependencies_retain_recorded_widths(self):
        for expr in ({"source": "missing"}, {"source": "combined"}, {"entries": []}, {"empty": False},
                     {"letters": 0, "alphabet": "naming"}, {"digits": True}, {"max": []}):
            with self.subTest(expr=expr):
                prof = copy.deepcopy(self.prof)
                prof["layout"]["fill_source"]["name"]["value"] = expr
                with self.assertRaises(ValueError):
                    validate(prof)
        del self.catalog["p1"]
        result = self.evaluator().result("combined", 99)
        self.assertEqual((result["width"], result["state"]), (99, "recorded"))
        self.assertIn("missing from catalog", result["unavailable"])
        self.assertEqual(self.evaluator().result("name")["state"], "computed")

    def test_table_coverage_includes_pointer_names(self):
        self.prof["table"] = [{"name": "names", "base": 100, "count": 2, "stride": 10}]
        self.prof["layout"]["fill_source"]["name"]["value"] = {"table": "names"}
        inv = {"entries": [{"id": "name", "kind": "fixed", "table": "names", "index": 0},
                           {"id": "p1", "refs": [{"kind": "table:names", "at": 110}]}]}
        self.assertEqual(graph(self.prof, inv, self.catalog, alphabet="AB")["dependencies"]["name"], ["name", "p1"])
        inv["entries"].pop()
        evaluator = Evaluator(graph(self.prof, inv, self.catalog), self.catalog, self.measure, self.cm)
        self.assertEqual(evaluator.result("name", 70)["width"], 70)
        self.assertIn("incomplete table", evaluator.result("name", 70)["unavailable"])

    def test_table_subset_preserves_pointer_names_and_excludes_other_rows(self):
        self.prof["table"] = [{"name": "names", "base": 100, "count": 3, "stride": 10}]
        self.prof["layout"]["fill_source"]["name"]["value"] = {"table": "names", "indices": [0, 2]}
        self.catalog["excluded"] = (Path("test.po"), PoEntry("excluded", "ABCDE", "ABCDE"))
        inv = {"entries": [{"id": "name", "kind": "fixed", "table": "names", "index": 0},
                           {"id": "excluded", "kind": "fixed", "table": "names", "index": 1},
                           {"id": "p2", "refs": [{"kind": "table:names", "at": 120}]}]}
        resolved = graph(self.prof, inv, self.catalog, alphabet="AB", selected={"name"})
        self.assertEqual(resolved["dependencies"]["name"], ["name", "p2"])
        facts = {sid: fragment(entry.msgid, self.cm) for sid, (_, entry) in self.catalog.items()}
        for values, originals in ((self.catalog, None),
                                  ({sid: {"translation": ""} for sid in facts}, facts)):
            before = Evaluator(resolved, values, self.measure, self.cm, originals).result("name")
            edited = Evaluator(resolved, values, self.measure, self.cm, originals,
                               {"p2": "ABCD", "excluded": "ABCDEFG"}).result("name")
            self.assertEqual((before["width"], edited["width"]), (12, 24))
            self.assertEqual(edited["contributors"][0]["id"], "p2")
        inv["entries"].pop(1)
        self.assertEqual(graph(self.prof, inv, self.catalog, alphabet="AB")["dependencies"]["name"],
                         ["name", "p2"])
        inv["entries"].pop()
        evaluator = Evaluator(graph(self.prof, inv, self.catalog, alphabet="AB"),
                              self.catalog, self.measure, self.cm)
        self.assertEqual(evaluator.result("name", 70)["width"], 70)
        self.assertIn("incomplete table", evaluator.result("name", 70)["unavailable"])

    def test_table_subset_rejects_invalid_membership(self):
        self.prof["table"] = [{"name": "names", "base": 100, "count": 3, "stride": 10}]
        for indices in ([], [0, 0], [-1], [3], [True]):
            with self.subTest(indices=indices):
                self.prof["layout"]["fill_source"]["name"]["value"] = {"table": "names", "indices": indices}
                with self.assertRaisesRegex(ValueError, "invalid table indices"):
                    validate(self.prof)

    def test_unavailable_original_retains_width_and_control_facts_fail(self):
        with self.assertRaisesRegex(ValueError, "control-bearing"):
            fragment("A{FONT 00}", self.cm)
        result = Evaluator(self.evaluator().graph, {"p1": {"translation": ""}},
                           self.measure, self.cm).result("prefix", 82)
        self.assertEqual((result["width"], result["state"]), (82, "recorded"))
        self.assertIn("original measurement unavailable", result["unavailable"])

    def test_missing_alphabet_and_pointer_domains_retain_recorded_widths(self):
        self.prof["table"] = [{"name": "naming_keyboard", "base": 100, "count": 2, "stride": 10}]
        self.prof["keyboard"] = {"rows": {"page_keys_0": "A"}}
        self.prof["layout"]["fill_source"]["prefix"]["value"] = {
            "pointers": {"base": 200, "count": 2, "stride": 4, "fields": [0]}}
        resolved = graph(self.prof, {"entries": []}, self.catalog)
        evaluator = Evaluator(resolved, self.catalog, self.measure, self.cm)
        for source, width, reason in (("prefix", 82, "pointer domain"), ("name", 60, "alphabet")):
            result = evaluator.result(source, width)
            self.assertEqual((result["width"], result["state"]), (width, "recorded"))
            self.assertIn(reason, result["unavailable"])

    def test_restrictions_ignore_unavailable_unselected_alternatives(self):
        del self.catalog["p1"]
        self.prof["layout"]["slot_context"] = {"second-only": {"prefix": ["second"]}}
        result = self.evaluator().result("combined", 99, context="second-only")
        self.assertEqual((result["width"], result["state"]), (20, "computed"))

    def test_exported_code_sources_preserve_unavailable_inputs(self):
        from gen3tl.check.fillsources import code_graph
        self.prof["font_style"] = {"style": "main"}
        self.prof["layout"]["code_fill"] = [{"name": "reader", "evidence": "Verified field", "slots": {
            "STR_VAR_1": {"strings": ["missing"], "font": "printer"}}}]
        resolved = code_graph(self.prof, self.catalog, {}, self.measure, self.cm)
        evaluator = Evaluator(resolved, resolved["values"], self.measure, self.cm, resolved["fragments"])
        result = evaluator.result("code/reader/STR_VAR_1", 70)
        self.assertEqual((result["width"], result["state"]), (70, "recorded"))
        self.assertIn("dependencies unavailable", result["unavailable"])

    def test_unavailable_sources_keep_overrides_and_reject_invalid_overrides(self):
        del self.catalog["p1"]
        override = {"source": "combined", "width": 80, "reason": "Printer allowance"}
        result = self.evaluator().result("combined", 70, override)
        self.assertEqual((result["width"], result["state"], result["recorded"]), (80, "override", 70))
        self.assertIn("missing from catalog", result["unavailable"])
        with self.assertRaisesRegex(ValueError, "override requires"):
            self.evaluator().result("combined", 70, {**override, "reason": ""})

class FillSourceIntegrationTest(unittest.TestCase):
    setUp = FillSourcesTest.setUp

    def test_limits_share_declared_global_and_reader_sources(self):
        from gen3tl.check import qa
        self.prof["font"][0].update(glyphs=0, widths=0, count=256)
        self.prof["layout"].update(slot_source={"B_ATK_NAME_WITH_PREFIX": "combined"},
            code_slot_source={"reader": {"STR_VAR_1": "combined"}},
            code_fill=[{"name": "reader", "ids": ["host"], "evidence": "Verified caller",
                        "slots": {"STR_VAR_1": {"width": 60}}}])
        self.catalog["host"] = (Path("host.po"), PoEntry("host", "{B_ATK_NAME_WITH_PREFIX} {STR_VAR_1}", ""))
        inv = {"entries": [{"id": sid, "kind": "string", "category": "battle-script", "flags": [],
                            "refs": [], "length": 20, "text": e.msgid} for sid, (_, e) in self.catalog.items()]}
        self.prof["keyboard"] = {"rows": {"test_keys_0": "AB"}}
        result = qa.compute_limits(self.prof, inv, self.catalog, self.measure, self.cm)
        self.assertEqual(result["host"].placeholders, {"B_ATK_NAME_WITH_PREFIX": 32, "STR_VAR_1": 32})
        self.prof["layout"]["code_fill"][0]["slots"]["STR_VAR_1"] = {"source_width": 60}
        changed = qa.compute_limits(self.prof, inv, self.catalog, self.measure, self.cm, overrides={"p2": "ABCD "})
        self.assertEqual(changed["host"].placeholders, {"B_ATK_NAME_WITH_PREFIX": 38, "STR_VAR_1": 38})
        self.assertEqual(changed["host"].slot_details["STR_VAR_1"]["contributors"][0]["id"], "p2")

    def test_consumer_context_reaches_check_and_keyed_editor_window(self):
        from dataclasses import asdict

        from gen3tl.catalog.keys import Aliases
        from gen3tl.check import qa
        from gen3tl.check.layout import check, entry_measure, slot_note
        from gen3tl.web.editordata import keyed_window
        self.prof["font"][0].update(glyphs=0, widths=0, count=256)
        self.prof["keyboard"] = {"rows": {"test_keys_0": "AB"}}
        self.prof["layout"]["fill_source"]["prefix"]["value"]["max"]["none"] = {"empty": True}
        self.prof["layout"].update(slot_source={"B_ATK_NAME_WITH_PREFIX": "combined"},
            slot_context={"trainer": {"prefix": ["second", "none"]}, "player": {"prefix": ["none"]}},
            window_slot_context={"trainer-host": "trainer", "player-host": "player"})
        text = "{B_ATK_NAME_WITH_PREFIX}!"
        for sid in ("trainer-host", "player-host", "unrestricted"):
            self.catalog[sid] = (Path("host.po"), PoEntry(sid, text, text))
        inv = {"entries": [{"id": sid, "kind": "string", "category": "battle-script", "flags": [],
                            "refs": [], "length": 20, "text": e.msgid} for sid, (_, e) in self.catalog.items()]}
        limits = qa.compute_limits(self.prof, inv, self.catalog, self.measure, self.cm)
        for sid, width, context, words in (("trainer-host", 20, "trainer", "«D » 8 px"),
                                         ("player-host", 12, "player", "no prefix 0 px"),
                                         ("unrestricted", 32, None, "«ABC » 20 px")):
            with self.subTest(sid=sid):
                limit = limits[sid]
                self.assertEqual(limit.placeholders["B_ATK_NAME_WITH_PREFIX"], width)
                self.assertIn(words, slot_note(text, limit))
                window = keyed_window(asdict(limit), Aliases())
                self.assertEqual(window["slot_context"], context)
                self.assertEqual(window["slot_details"]["B_ATK_NAME_WITH_PREFIX"]["width"], width)
                limit.width = 8
                problems = check(text, limit, entry_measure(self.measure, limit), self.cm)
                self.assertTrue(any(words in problem for problem in problems), problems)

class FillSourcePageTest(unittest.TestCase):
    setUp = FillSourcesTest.setUp
    evaluator = FillSourcesTest.evaluator

    def checker(self, values=None, widths=None):
        from gen3tl.web.editorcheck import Checker
        evaluator = self.evaluator()
        graph_data = {**evaluator.graph,
                      "values": values or {sid: {"translation": entry.msgstr, "flags": []}
                                           for sid, (_, entry) in self.catalog.items()},
                      "fragments": {sid: fragment(entry.msgid, self.cm) for sid, (_, entry) in self.catalog.items()}}
        doc = {"fonts": {"main": "main", "styles": {"main": {"pack": {}, "format": "16x16", "rom": {"count": 256}}}},
               "charmap": {}, "placeholders": {"default": 60, "widths": {"B_ATK_NAME_WITH_PREFIX": 60}},
               "fill_sources": graph_data}
        return Checker(doc, {"main": widths or [2 if b == 0 else 6 for b in range(256)]})

    def host(self):
        return {"id": "host", "tokens": ["{B_ATK_NAME_WITH_PREFIX}"], "source_text": "{B_ATK_NAME_WITH_PREFIX}!",
                "window": {"kind": "battle", "width": 48, "lines": 2,
                           "slot_details": {"B_ATK_NAME_WITH_PREFIX": self.evaluator().result("combined", 60)}}}

    def test_page_recomputes_paired_source_after_draft(self):
        self.prof["layout"]["fill_source"]["paired-class"] = {
            "label": "Paired class", "font": "printer", "evidence": "Verified records",
            "value": {"paired": {"with": "name", "rows": [
                [{"entries": ["p1"]}, {"entries": ["name"]}],
                [{"entries": ["p2"]}, {"entries": ["p1"]}]]}}}
        host, token = self.host(), "B_ATK_NAME_WITH_PREFIX"
        host["window"]["slot_details"][token] = self.evaluator().result("paired-class", 60)
        checker = self.checker()
        self.assertEqual(checker.check(host, host["source_text"])["window"]["placeholders"][token], 20)
        changed = checker.check(host, host["source_text"], drafts={"p2": {"translation": "ABCDE ", "state": "text"}})
        detail = changed["window"]["slot_details"][token]
        self.assertEqual((detail["width"], detail["contributors"][0]["id"]), (40, "p2"))
        self.assertEqual(detail["dependencies"], ["name", "p1", "p2"])

    def test_page_matches_core_after_changed_winner_and_undo(self):
        checker, host = self.checker(), self.host()
        original = checker.check(host, host["source_text"])
        edited = checker.check(host, host["source_text"], drafts={"p2": {"translation": "ABCD ", "state": "text"}})
        undone = checker.check(host, host["source_text"], drafts={})
        token = "B_ATK_NAME_WITH_PREFIX"
        self.assertEqual(original["window"]["placeholders"][token], 32)
        self.assertEqual(edited["window"]["placeholders"][token], 38)
        self.assertEqual(edited["window"]["slot_details"][token]["contributors"][0]["id"], "p2")
        self.assertEqual(edited["window"]["placeholders"][token], self.evaluator({"p2": "ABCD "}).result("combined")["width"])
        self.assertEqual(original["window"], undone["window"])
        self.assertEqual(edited["lines"][0][0]["width"], 44)

    def test_page_retains_recorded_width_when_original_measurements_are_missing(self):
        checker, host = self.checker(), self.host()
        checker.fill_graph["fragments"].pop("p1")
        result = checker.check(host, host["source_text"])
        detail = result["window"]["slot_details"]["B_ATK_NAME_WITH_PREFIX"]
        self.assertEqual((detail["width"], detail["state"]), (60, "recorded"))
        self.assertIn("original measurement unavailable", detail["unavailable"])
        self.assertFalse(any("substitution measurements unavailable" in p["message"] for p in result["problems"]))

    def test_page_original_fallback_empty_and_font_revision(self):
        values = {sid: {"translation": "", "flags": []} for sid in self.catalog}
        host = self.host()
        checker = self.checker(values)
        source = checker.check(host, host["source_text"])["window"]["slot_details"]["B_ATK_NAME_WITH_PREFIX"]
        self.assertEqual(source["width"], 32)
        self.assertTrue(source["contributors"][0]["original"])
        empty = checker.check(host, host["source_text"], drafts={"p1": {"translation": "", "state": "empty"}})
        self.assertEqual(empty["window"]["placeholders"]["B_ATK_NAME_WITH_PREFIX"], 20)
        changed = self.checker(values, [3 if b == 0 else 8 for b in range(256)]).check(host, host["source_text"])
        self.assertEqual(changed["window"]["placeholders"]["B_ATK_NAME_WITH_PREFIX"], 43)

    def test_restricted_page_recomputes_permitted_drafts(self):
        self.prof["layout"]["fill_source"]["prefix"]["value"]["max"]["none"] = {"empty": True}
        self.prof["layout"]["slot_context"] = {
            "trainer": {"prefix": ["none", "second"]}, "player": {"prefix": ["none"]}}
        checker = self.checker()
        for context, expected in (("trainer", [20, 44, 20]), ("player", [12, 12, 12]),
                                  (None, [32, 44, 44])):
            host = self.host()
            host["window"]["slot_context"] = context
            for drafts, width in zip(({}, {"p2": {"translation": "ABCDE ", "state": "text"}},
                                      {"p1": {"translation": "ABCDE ", "state": "text"}}), expected):
                result = checker.check(host, host["source_text"], drafts=drafts)
                self.assertEqual(result["window"]["placeholders"]["B_ATK_NAME_WITH_PREFIX"], width)

    def test_page_refuses_unmeasurable_source_drafts(self):
        host = self.host()
        result = self.checker().check(host, host["source_text"], drafts={"p1": {"translation": "@", "state": "text"}})
        self.assertGreater(result["errors"], 0)
        self.assertIn("substitution measurements unavailable", result["problems"][-1]["message"])

    def test_page_recomputes_table_subset_after_drafts_fonts_and_undo(self):
        self.prof["table"] = [{"name": "names", "base": 100, "count": 3, "stride": 10}]
        self.prof["layout"]["fill_source"]["name"]["value"] = {"table": "names", "indices": [0, 2]}
        self.inventory = {"entries": [{"id": "name", "kind": "fixed", "table": "names", "index": 0},
                                      {"id": "p1", "kind": "fixed", "table": "names", "index": 1},
                                      {"id": "p2", "refs": [{"kind": "table:names", "at": 120}]}]}
        host, token = self.host(), "B_ATK_NAME_WITH_PREFIX"
        host["window"]["slot_details"][token] = self.evaluator().result("name", 60)
        checker = self.checker()
        for drafts, expected, winner in (({}, 12, "name"),
                ({"p1": {"translation": "ABCDEFG", "state": "text"}}, 12, "name"),
                ({"p2": {"translation": "ABCD", "state": "text"}}, 24, "p2"), ({}, 12, "name")):
            detail = checker.check(host, host["source_text"], drafts=drafts)["window"]["slot_details"][token]
            self.assertEqual(detail["width"], expected)
            self.assertEqual(detail["contributors"][0]["id"], winner)
            self.assertEqual(detail["dependencies"], ["name", "p2"])
        changed = self.checker(widths=[3 if b == 0 else 8 for b in range(256)])
        detail = changed.check(host, host["source_text"])["window"]["slot_details"][token]
        self.assertEqual(detail["width"], 16)

    def test_multiple_reader_alternatives_keep_losing_dependencies(self):
        from gen3tl.check.fillsources import merge_results
        host, checker = self.host(), self.checker()
        token = "B_ATK_NAME_WITH_PREFIX"
        host["window"]["slot_details"][token] = merge_results([
            self.evaluator().result("prefix"), self.evaluator().result("name")])
        original = checker.check(host, host["source_text"])
        changed = checker.check(host, host["source_text"], drafts={"name": {"translation": "ABCDE", "state": "text"}})
        self.assertEqual(original["window"]["placeholders"][token], 20)
        self.assertEqual(changed["window"]["placeholders"][token], 30)
        self.assertEqual(changed["window"]["slot_details"][token]["contributors"][0]["id"], "name")
        host["window"]["slot_details"][token] = merge_results([self.evaluator().result("prefix")], 40)
        held = checker.check(host, host["source_text"])["window"]["slot_details"][token]
        self.assertEqual((held["width"], held["state"], held["computed"]), (40, "recorded", 20))
