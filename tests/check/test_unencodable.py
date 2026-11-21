import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gen3tl.cli import check as cli
from gen3tl.check import qa
from gen3tl.lang.langpack import LangPack
from gen3tl.check.layout import Measure
from gen3tl.catalog.po import PoEntry, write
from gen3tl.profile.profile import Profile
from gen3tl.core.rom import Rom
from editorcheck_support import INV, PROF, ROM


class UnencodableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        old_cwd = Path.cwd()
        self.addCleanup(os.chdir, old_cwd)
        os.chdir(self.root)
        self.profile = Profile(copy.deepcopy(PROF.data), Path('test.toml'))
        self.pack = LangPack.load('ru', 'unbound')
        self.texts = {'s0000100': 'Привет, {PLAYER}! 中', 'species:1': '中',
                      's0000200': 'Зелье', 's0000300': 'Сумка...'}
        self.entries = [PoEntry(e['id'], e['text'], self.texts[e['id']]) for e in INV['entries']]
        write(Path('po/test.po'), self.entries, {})

    def test_qa_reports_wrapped_and_fixed_fields_then_continues(self):
        gate = qa.run(self.profile, INV, Path('po'), self.pack.charmap(),
                      Measure(lambda b: 6, {'PLAYER': 42}), set())
        self.assertEqual(gate.checked, 4)
        bad = {f.id for f in gate.findings if f.message.startswith('not encodable:')}
        self.assertEqual(bad, {'s0000100', 'species:1'})
        self.assertTrue(all(f.severity == 'error' for f in gate.findings if f.id in bad))
        self.assertFalse(any(f.id == 's0000200' for f in gate.findings))
        self.assertTrue(any(f.id == 's0000300' and f.message == 'typography: three dots'
                            for f in gate.findings))

    def test_batch_command_reports_errors_and_keeps_catalog(self):
        Path('build').mkdir()
        Path('build/inventory.json').write_text(json.dumps(INV))
        batch = Path('translation/batches/test')
        batch.mkdir(parents=True)
        Path('translation/release.toml').write_text('version="0.1.0"\n')
        (batch / 'source.json').write_text(json.dumps([
            {'id': e['id'], 'source_text': e['text']} for e in INV['entries']]))
        (batch / 'translation.json').write_text(json.dumps(self.texts))
        before = Path('po/test.po').read_bytes()
        project = SimpleNamespace(batches=Path('translation/batches'), glossary=Path('translation/glossary.tsv'),
                                  build=Path('build'))
        args = SimpleNamespace(profile='test', rom=None, lang='ru', name='test',
                               file='translation.json', po_root='po', project=project)
        out = io.StringIO()
        with patch.object(Profile, 'by_name', return_value=self.profile), \
             patch.object(Profile, 'load_rom', return_value=Rom(ROM + bytes(65536))), \
             redirect_stdout(out):
            result = cli.batch_check(args)
        lines = out.getvalue()
        self.assertEqual(result, 1)
        self.assertIn("entry        s0000100\nerror        not encodable: character '中'", lines)
        self.assertIn("entry        species:1\nerror        not encodable: character '中'", lines)
        self.assertIn('entry        s0000300\nerror        typography: three dots', lines)
        self.assertNotIn('entry        s0000200', lines)
        self.assertEqual(Path('po/test.po').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
