"""Controls for instance annotations on generated window measurements."""

from copy import deepcopy
import unittest

from gen3tl.profile import schema, tsv


class WindowAnnotationsTest(unittest.TestCase):
    def setUp(self):
        self.window = {'name': 'menu', 'width': 80, 'box': 'dialogue', 'lines': 2, 'style': 'small'}
        self.generated = {'layout': {'window': [self.window]}}
        self.annotation = {'name': 'menu', 'evidence': 'Instance observation remains attached.'}

    def expand(self, row, bindings):
        overlay = {'layout': {'window': [row]}}
        schema._expand_windows(overlay, {'window': ('windows.tsv', [row]),
            'window_strings': ('bindings.tsv', bindings)}, self.generated)
        return overlay

    def test_hand_binding_uses_generated_measures_and_instance_evidence(self):
        before = deepcopy(self.annotation)
        overlay = self.expand(self.annotation, [{'string': 'sample', 'window': 'menu'}])
        for field in ('width', 'box', 'lines', 'style'):
            self.assertEqual(overlay['layout']['window_' + field]['sample'], self.window[field])
        effective = schema.merge(self.generated, overlay)
        self.assertEqual(effective['layout']['window'], [{**self.window, **self.annotation}])
        self.assertEqual(self.annotation, before)

    def test_annotation_survives_after_last_hand_binding_retires(self):
        overlay = self.expand(self.annotation, [])
        self.assertEqual(overlay['layout']['window'], [self.annotation])

    def test_generated_behavior_binding_keeps_instance_annotation(self):
        self.window['instant_print'] = True
        self.generated['layout']['window_binding'] = {'sample': dict(self.window)}
        overlay = self.expand(self.annotation, [])
        self.assertEqual(overlay['layout']['window_binding']['sample'],
                         {**self.window, **self.annotation})

    def test_orphan_annotation_and_unused_measure_are_refused(self):
        for row in ({'name': 'missing', 'evidence': 'Unmatched observation.'},
                    {**self.annotation, 'width': 80}):
            with self.subTest(row=row), self.assertRaises(tsv.TSVError):
                self.expand(row, [])

    def test_instance_measure_still_overrides_generated_default(self):
        overlay = self.expand({**self.annotation, 'width': 72}, [{'string': 'sample', 'window': 'menu'}])
        self.assertEqual(overlay['layout']['window_width']['sample'], 72)


if __name__ == '__main__':
    unittest.main()
