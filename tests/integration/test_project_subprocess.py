import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.cli.verify import _toolkit_bootstrap


class ProjectSubprocessTest(unittest.TestCase):
    def test_emulator_subprocess_imports_from_an_external_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            code = _toolkit_bootstrap() + "from gen3tl.profile.profile import TOOLKIT_ROOT; print(TOOLKIT_ROOT)"
            result = subprocess.run([sys.executable, "-c", code], cwd=tmp, env=env,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(Path(result.stdout.strip()), TOOLKIT_ROOT)
