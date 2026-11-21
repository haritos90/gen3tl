from test_roots import TOOLKIT_ROOT
"""Profile schema notes checks and fixtures."""

import sys
from pathlib import Path
import json
import tomllib
import unittest
from copy import deepcopy

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.core import notes
from gen3tl.profile import check, schema
import profile_schema_support
from profile_schema_support import GENERATED, HEADER, OVERLAY


class ProfileNotesTest(profile_schema_support.ProfileNotesTest, unittest.TestCase):
    def test_bound_notes_satisfy_one_line_consumers_and_keep_refusals(self):
        from gen3tl.catalog.catalog import never_shown, observed_entries
        from gen3tl.core.fields import long_name_fields
        from gen3tl.discover.inventory import text_modes

        cases = [
            ({"id": "s0000100", "mode": "field", "refs": [{"at": 16, "kind": "pool"}],
              "evidence": "Legacy proof."}, lambda row: text_modes([row]), "one-line evidence"),
            ({"ids": ["s0000100"], "at": 16, "evidence": "Legacy proof."},
             lambda row: observed_entries([row]), "one-line evidence"),
            ({"ids": ["s0000100"], "evidence": "Legacy proof."},
             lambda row: never_shown(None, [row]), "one-line evidence"),
            ({"count": 2, "field_length": 8, "long_name_pointers": True,
              "long_name_fields": [{"index": 0, "length": 12, "evidence": "Legacy proof."}]},
             long_name_fields, "one-line consumer evidence"),
        ]
        for value, consume, message in cases:
            for status in notes.STATUSES:
                with self.subTest(consumer=message, status=status, value=value):
                    if status not in ("unreviewed", "accepted"):
                        with self.assertRaisesRegex(schema.ProfileError, "evidence needs a sentence"):
                            self.migrated(value, status)
                        continue
                    migrated = self.migrated(value, status)
                    consume(migrated)
                    legacy = json.loads(json.dumps(migrated))
                    with self.assertRaisesRegex(ValueError, message):
                        consume(legacy)

    def test_digest_ignores_only_prose_and_keeps_types_and_array_order(self):
        value = {"base": 256, "evidence": "Old proof.",
                 "windows": [{"width": 32, "length_evidence": "Measured."}], "flags": [1, 2]}
        equivalent = {"flags": [1, 2], "windows": [{"width": 32}], "base": 256}
        self.assertEqual(schema.value_digest(value), schema.value_digest(equivalent))
        for key, changed in (("base", 257), ("base", 256.0), ("flags", [2, 1]),
                             ("evidence", {"offset": 256}), ("windows", [{"width": 33}])):
            with self.subTest(key=key, changed=changed):
                self.assertNotEqual(schema.value_digest(value), schema.value_digest(dict(value, **{key: changed})))
        self.assertNotEqual(schema.value_digest(1), schema.value_digest(True))

    def test_consumers_refuse_empty_unbound_evidence(self):
        from gen3tl.build import codereplace, easychat, spritefont
        from gen3tl.check import coverage
        from gen3tl.discover import context, maps, reach
        from test_codereplace import ENTRY
        from test_easychat import entry, image
        from test_spritefont import ENTRY as FONT

        cases = [
            ({"base": 256, "stride": 4, "count": 2},
             lambda row: reach.declared_tables({"reach": {"table": [row]}})),
            ({"base": 256, "indexes": [0]},
             lambda row: reach.unused_entries({"reach": {"unused_entries": [row]}})),
            ({"name": "fill", "address": 257, "slot": 0, "base": 512, "stride": 4,
              "count": 1, "variants": [["s0000300"]]},
             lambda row: maps.native_fill_rules({"context": {"native_fill": [row]}})),
            ({"text": "line", "role": "speaker"},
             lambda row: context.participant_rules({"context": {"participants": [row]}}, {"line"})),
            ({"gender": "female"}, lambda row: context.context_identities(
                {}, {}, {"context": {"sprite_genders": {"7": row}}}, None)),
            ({"at": 16}, lambda row: coverage.unread_rules({"coverage": {"unread_reference": [row]}})),
            (ENTRY, codereplace.parse),
            (entry(image()), easychat.parse),
            (FONT, spritefont.parse),
            ({"where": "panel", "width": 111, "encounters": ["land"], "learnsets": ["level_up"]},
             lambda row: spritefont._pair(row, "panel")),
        ]
        for value, consume in cases:
            with self.subTest(value=value):
                value = dict(value, evidence="Measured reader.")
                consume(value)
                consume(self.migrated(value, "unreviewed"))
                for empty in ("", notes.BoundProse("")):
                    with self.assertRaises(ValueError):
                        consume(dict(value, evidence=empty))

    def test_grouping_merge_and_prose_survive_migration(self):
        (self.path / schema.GENERATED).write_text(GENERATED, encoding="utf-8")
        (self.path / schema.OVERLAY).write_text(OVERLAY, encoding="utf-8")
        before = schema.load(self.path)
        comparisons = check.run(self.path).comparisons
        raw = tomllib.loads(OVERLAY)
        records = raw.pop("fact")
        value = raw["keyboard"]
        prose = value.pop("evidence")
        record = self.record("keyboard", value)
        record["notes"][0]["facts"] = [prose]
        record["notes"][0]["evidence"] = [{"method": "measurement", "verification": "recorded",
                                            "measurements": [{"name": "rows", "value": 4, "unit": "count"}]}]
        self.write(raw, records + [record])
        content = (self.path / schema.OVERLAY).read_bytes()
        self.assertEqual(schema.load(self.path), before)
        self.assertEqual([(c.fact.id, c.fact.section, c.fact.value, c.outcome, c.fields) for c in comparisons],
                         [(c.fact.id, c.fact.section, c.fact.value, c.outcome, c.fields)
                          for c in check.run(self.path).comparisons])
        sources = schema.read(self.path)
        original = deepcopy(sources)
        first = schema.overlay_facts(sources)
        self.assertEqual(schema.overlay_facts(sources), first)
        self.assertEqual(sources, original)
        fact = next(f for f in first if f.id == "keyboard")
        self.assertEqual(fact.note_records, tuple(record["notes"]))
        self.assertEqual(fact.evidence.sentence, prose)
        self.assertNotIn("author", fact.note_records[0])
        self.assertNotIn("created", fact.note_records[0])
        self.assertEqual((self.path / schema.OVERLAY).read_bytes(), content)

    def test_named_annotation_retains_notes_without_redeclaring_generated_values(self):
        (self.path / schema.GENERATED).write_text(GENERATED, encoding="utf-8")
        value = {"name": "main", "glyphs": 0x100, "widths": 0x200, "count": 8}
        record = self.record("font[name=main]", value)
        self.write({"font": [{"name": "main"}]}, [record])
        sources = schema.read(self.path)
        original = deepcopy(sources)
        fact = schema.overlay_facts(sources)[0]
        self.assertEqual({k: v for k, v in fact.value.items() if k != "evidence"}, value)
        self.assertEqual(fact.note_records, tuple(record["notes"]))
        self.assertEqual(schema.load(self.path)["font"][0]["evidence"], "Measured reader.")
        self.assertEqual(sources, original)
        self.assertEqual(sources.overlay, {"font": [{"name": "main"}]})
        self.assertEqual(check.run(self.path).comparisons[0].outcome, "reproduced")

    def test_named_annotation_rejects_changed_or_missing_generated_values(self):
        value = {"name": "main", "glyphs": 0x100, "widths": 0x200, "count": 8}
        self.write({"font": [{"name": "main"}]}, [self.record("font[name=main]", value)])
        for generated in (HEADER, GENERATED.replace("count = 8", "count = 9")):
            with self.subTest(generated=generated):
                (self.path / schema.GENERATED).write_text(generated, encoding="utf-8")
                with self.assertRaisesRegex(schema.ProfileError, "stale note binding"):
                    schema.load(self.path)

    def test_nested_fields_order_and_all_metadata_remain_readable(self):
        value = {"name": "panel", "windows": [{"width": 48}], "odd/key~": {"length": 7}}
        record = self.record("table[name=panel]", value, "/windows/0/length_evidence")
        record["notes"][0].update(guidance=["Keep the bound."], reason=["The reader limits the buffer."],
                                 author="Contributor", created="2026-11-11",
                                 sources=[{"kind": "rom", "name": "Base image", "address": "sha1:" + "1" * 40,
                                           "verification": "recorded"}], related_notes=["other-proof"])
        record["notes"].append(self.note(record["id"], value, "/windows/0/length_evidence",
                                         id="second-proof", facts=["Second measurement."], status="accepted"))
        record["notes"].append(self.note(record["id"], value, "/odd~1key~0/note", id="escaped-proof"))
        self.write({"table": [value]}, [record])
        fact = schema.overlay_facts(schema.read(self.path))[0]
        self.assertEqual(fact.note_records, tuple(record["notes"]))
        self.assertEqual(schema.load(self.path)["table"][0]["windows"][0], {
            "width": 48, "length_evidence": "Keep the bound.\nMeasured reader.\n"
                                          "The reader limits the buffer.\nSecond measurement."})
        self.assertEqual(schema.load(self.path)["table"][0]["odd/key~"]["note"], "Measured reader.")

    def test_only_visible_notes_establish_evidence(self):
        value = {"base": 256, "width": 32}
        for status in notes.STATUSES:
            with self.subTest(status=status):
                record = self.record("keyboard", value, tier="crossref")
                record["notes"][0]["status"] = status
                self.write({"keyboard": value}, [record])
                if status not in ("unreviewed", "accepted"):
                    for path in (self.path, self.path / schema.OVERLAY):
                        with self.assertRaisesRegex(schema.ProfileError, "evidence needs a sentence"):
                            schema.load(path)
                    continue
                fact = schema.overlay_facts(schema.read(self.path))[0]
                self.assertEqual((fact.status, fact.tier), ("verified", "crossref"))
                self.assertEqual(fact.note_records[0]["status"], status)
                projected = schema.load(self.path)["keyboard"]
                self.assertEqual({k: v for k, v in projected.items() if k != "evidence"}, value)
                self.assertTrue(notes.has_prose(projected["evidence"]))
                self.assertEqual(projected["evidence"], "Measured reader.")

    def test_visible_note_carries_evidence_beside_hidden_notes(self):
        value = {"base": 256, "evidence": ""}
        for status in ("unreviewed", "accepted"):
            record = self.record("keyboard", value)
            record["notes"][0]["status"] = status
            for hidden in ("proposed", "needs_review", "superseded"):
                record["notes"].append(self.note("keyboard", value, id=hidden, status=hidden,
                                                facts=["Hidden assertion."]))
            self.write({"keyboard": value}, [record])
            fact = schema.overlay_facts(schema.read(self.path))[0]
            self.assertEqual(fact.evidence.sentence, "Measured reader.")
            self.assertEqual(len(fact.note_records), 4)
            self.assertEqual(schema.load(self.path)["keyboard"]["evidence"], "Measured reader.")

    def test_flat_and_directory_profiles_validate_and_project_the_same_notes(self):
        value = {"base": 256, "evidence": ""}
        record = self.record("keyboard", value)
        self.write({"keyboard": value}, [record])
        self.assertTrue(check.same(schema.load(self.path), schema.load(self.path / schema.OVERLAY)))
        record["notes"][0]["binding"]["value_sha256"] = "0" * 64
        self.write({"keyboard": value}, [record])
        for path in (self.path, self.path / schema.OVERLAY):
            with self.subTest(path=path), self.assertRaisesRegex(schema.ProfileError, "stale note binding"):
                schema.load(path)

    def test_notes_for_another_field_do_not_supply_required_evidence(self):
        from gen3tl.catalog.catalog import observed_entries

        value = {"ids": ["s0000100"], "at": 16, "evidence": "", "note": ""}
        fact_id = "reach.observed[at=0x10]"
        self.write({"reach": {"observed": [value]}}, [self.record(fact_id, value, "note")])
        with self.assertRaisesRegex(ValueError, "one-line evidence"):
            observed_entries(schema.load(self.path)["reach"]["observed"])

    def test_empty_note_collection_preserves_legacy_evidence(self):
        value = {"base": 256, "evidence": "Legacy proof."}
        self.write({"keyboard": value}, [self.record("keyboard", value, notes=[])])
        self.assertEqual(schema.load(self.path), {"keyboard": value})
        fact = schema.overlay_facts(schema.read(self.path))[0]
        self.assertEqual(fact.note_records, ())
        self.assertEqual(fact.evidence.sentence, value["evidence"])

    def test_scalar_notes_retain_existing_technical_evidence(self):
        record = self.record("rom.size", 4096, tier="shape",
                             evidence={"method": "header", "anchors": [256], "code_sites": [128],
                                       "counts": {"headers": 1}})
        self.write({"rom": {"size": 4096}}, [record])
        fact = schema.overlay_facts(schema.read(self.path))[0]
        self.assertEqual(fact.value, 4096)
        self.assertEqual(fact.evidence, schema.Evidence("Measured reader.", "header", (256,), (128,),
                                                       (("headers", 1),)))
        self.assertEqual(schema.load(self.path), {"rom": {"size": 4096}})

    def test_note_migration_keeps_injected_rom_bytes_identical(self):
        from gen3tl.build.inject import inject, verify
        from test_inject import COMPUTED_INV, GROUP, computed_image

        group = dict(GROUP[0], evidence="The reader adds a fixed displacement.")
        self.write({"strings": {"computed": [group]}}, [])
        legacy = schema.load(self.path)
        group_id = schema.facts({"strings": {"computed": [group]}})[0].id
        record = self.record(group_id, group)
        record["notes"][0]["facts"] = [group.pop("evidence")]
        self.write({"strings": {"computed": [group]}}, [record])
        migrated = schema.load(self.path)
        base = computed_image()
        texts = {"s0000140": b"\xBB" * 40 + b"\xFF"}
        outputs = []
        for data in (legacy, migrated):
            rom = base.copy()
            result = inject(rom, base, COMPUTED_INV, texts, {}, computed=data["strings"]["computed"])
            self.assertEqual(verify(rom, base, result, texts, set()), [])
            self.assertNotEqual(rom.data, base.data)
            outputs.append((bytes(rom.data), result.placements))
        self.assertEqual(outputs[0], outputs[1])

    def test_invalid_bindings_containers_and_duplicate_owners_are_rejected(self):
        value = {"base": 256, "windows": [{"width": 32}]}
        good = self.record("keyboard", value)
        cases = []
        for key, change in (("notes_schema", "0.0.0"), ("notes_schema", None), ("notes", {})):
            record = deepcopy(good)
            if change is None:
                record.pop(key)
            else:
                record[key] = change
            cases.append((key, value, [record]))
        for field in (None, "base", "/windows/0/width", "/missing/evidence", "/windows/1/evidence",
                      "/windows/00/evidence", "/bad~2/evidence"):
            record = deepcopy(good)
            if field is None:
                record["notes"][0]["binding"].pop("field")
            else:
                record["notes"][0]["binding"]["field"] = field
            cases.append((field, value, [record]))
        cases.append(("stale", dict(value, base=257), [good]))
        cases.append(("nested stale", dict(value, windows=[{"width": 33}]), [good]))
        cases.append(("inline", dict(value, evidence="Legacy proof."), [good]))
        cases.append(("record sentence", value, [dict(good, evidence={"sentence": "Legacy proof."})]))
        cases.append(("invalid evidence", value, [dict(good, evidence="Legacy proof.")]))
        nested = self.record("keyboard", value, "/windows/0/evidence")
        cases.append(("nested inline", dict(value, windows=[{"width": 32, "evidence": "Legacy proof."}]),
                      [nested]))
        cases.append(("duplicate", value, [dict(good, notes=good["notes"] * 2)]))
        for field, change in (("subject", {"kind": "profile_fact", "id": "rom.size"}),
                              ("scope", {"kind": "language", "language": "xx"})):
            record = deepcopy(good)
            record["notes"][0][field] = change
            cases.append((field, value, [record]))
        for label, data, records in cases:
            with self.subTest(case=label):
                self.write({"keyboard": data}, records)
                with self.assertRaises(schema.ProfileError):
                    schema.load(self.path)

    def test_ids_are_unique_across_facts_and_unattached_records_are_rejected(self):
        first = self.record("rom.size", 4096)
        second = self.record("rom.sha1", "1" * 40)
        self.write({"rom": {"size": 4096, "sha1": "1" * 40}}, [first, second])
        with self.assertRaisesRegex(schema.ProfileError, "duplicate note"):
            schema.load(self.path)
        self.write({"rom": {"size": 4096}}, [second])
        with self.assertRaisesRegex(schema.ProfileError, "records name no fact"):
            schema.load(self.path)

    def test_generated_and_family_records_do_not_accept_editable_notes(self):
        data = {"rom": {"size": 4096}}
        record = self.record("rom.size", 4096, source="fixture", tier="shape",
                             evidence={"sentence": "Measured."})
        self.write(data, [record])
        overlay = self.path / schema.OVERLAY
        (self.path / schema.GENERATED).write_text(overlay.read_text() + "\n" + HEADER, encoding="utf-8")
        overlay.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(schema.ProfileError, "belong in the overlay"):
            schema.load(self.path)
        with self.assertRaisesRegex(schema.ProfileError, "belong in the overlay"):
            schema.family_facts(data, [dict(record, scope="family")], self.path)
