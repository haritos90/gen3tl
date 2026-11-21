"""Dataset tree copying."""

import tempfile
import unittest
from pathlib import Path

from gen3tl.web import editordata


class SyncTreeTest(unittest.TestCase):
    def test_skips_hidden_files_and_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "source", Path(tmp) / "destination"
            visible = ("sprite.png", "icons/1.shiny.png")
            hidden = (".DS_Store", "icons/.DS_Store", ".cache/sprite.png", "icons/.cache/sprite.png")
            for name in (*visible, *hidden):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode("utf-8"))

            self.assertEqual(editordata.sync_tree(source, destination), len(visible))
            copied = sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*")
                            if path.is_file())
            self.assertEqual(copied, sorted(visible))
            for name in visible:
                self.assertEqual((destination / name).read_bytes(), name.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
