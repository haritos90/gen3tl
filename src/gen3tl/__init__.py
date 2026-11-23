"""gen3tl: a translation toolkit for Gen 3 (GBA) Pokemon ROMs."""

from pathlib import Path

__version__ = "2.15.1"

_PACKAGE = Path(__file__).resolve().parent
# wheel: under share/; checkout: at the root
INSTALLED = (_PACKAGE / "share").is_dir()
TOOLKIT_ROOT = _PACKAGE / "share" if INSTALLED else _PACKAGE.parents[1]
