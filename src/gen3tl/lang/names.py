"""English names of languages, for the text of release notes."""

from __future__ import annotations

import json
from functools import cache

from .langpack import TOOLKIT_ROOT


@cache
def _names() -> dict[str, str]:
    document = json.loads((TOOLKIT_ROOT / "assets/lang/names.json").read_text(encoding="utf-8"))
    return document["names"]


def language_name(code: str) -> str:
    """The English name of a language code; an unlisted code names itself."""
    return _names().get(code.split("-")[0].lower(), code)

