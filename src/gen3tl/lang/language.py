"""Selected language behavior."""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
import tomllib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache, wraps
from pathlib import Path

from .. import TOOLKIT_ROOT

# the browser check bundle holds the packs beside the package
ROOT = next((p for p in (TOOLKIT_ROOT, *Path(__file__).resolve().parents) if (p / "assets/lang").is_dir()),
            TOOLKIT_ROOT)
ACTIVE = ContextVar("language_behavior", default=None)


def save_link_keys(code: str | None = None) -> tuple[str, ...]:
    """Return the shared save-link policy fields."""
    return ("save_link", "save_link_defaults", "save_link_letters")


@dataclass(frozen=True)
class Language:
    code: str
    data: dict
    root: Path

    def normalize(self, text: str) -> str:
        for before, after in self.data.get("fold", {}).items():
            text = text.replace(before, after)
        return text

    def stand_ins(self) -> dict[str, str]:
        name = self.data.get("stand_ins")
        if name is None:
            return {}
        path = self.root / name
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(
                not isinstance(k, str) or len(k) != 1 or not isinstance(v, str) or len(v) != 1
                for k, v in data.items()):
            raise ValueError(f"invalid language stand-ins: {path}")
        return data

    def pattern(self, text: str) -> str:
        pattern = re.escape(text)
        groups = {}
        for before, after in self.data.get("fold", {}).items():
            groups.setdefault(after, set()).update((before, after))
        for after, chars in groups.items():
            pattern = pattern.replace(re.escape(after), "[" + "".join(re.escape(c) for c in sorted(chars)) + "]")
        return pattern

    def provider(self, key="provider"):
        name = self.data.get(key, "manual")
        return None if name == "manual" else _provider(self.root, name)

    def agreement_provider(self):
        return self.provider("agreement")

    def analyzer(self):
        provider = self.provider()
        return provider.morph() if provider else None

    def generate_forms(self, term) -> dict[str, str]:
        provider = self.provider()
        if provider:
            return provider.generate_forms(term)
        return term.all_forms()

    def grammar(self, forms, gender="", declension="", analyzer=None):
        provider = self.agreement_provider()
        if provider:
            return provider.grammar(forms, gender, declension, analyzer)
        return {"gender": gender, "number": "", "declension": declension, "animacy": ""}


@lru_cache(maxsize=None)
def _provider(root: Path, name: str):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix != ".py" or not path.is_file():
        raise ValueError(f"invalid language provider {name!r}")
    module_name = "gen3tl_language_" + str(abs(hash(str(path))))
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def load(code: str, root: Path | None = None) -> Language:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", code):
        raise ValueError("invalid language code")
    base = (root or ROOT / "assets/lang") / code
    path = base / "language.json"
    # Existing empty fixture packs have neutral behavior.
    if not path.exists():
        return Language(code, {}, base)
    data = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"schema", "code", "locale", "provider", "agreement", "fold", "wrapping", "mixed_scripts",
               "save", "preview_lines", "pseudo", "font_pairs", "stand_ins", "easy_chat_alphabet"}
    if not isinstance(data, dict) or set(data) - allowed or data.get("schema") != "2.0.0" or data.get("code") != code:
        raise ValueError(f"invalid language metadata: {path}")
    if "locale" in data and (not isinstance(data["locale"], str)
                             or not re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}", data["locale"])):
        raise ValueError(f"invalid language locale: {path}")
    for field in ("fold", "wrapping", "mixed_scripts", "save", "pseudo"):
        if field in data and not isinstance(data[field], dict):
            raise ValueError(f"invalid language {field}: {path}")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.get("fold", {}).items()):
        raise ValueError(f"invalid language fold: {path}")
    if any(len(k) != 1 or len(v) != 1 for k, v in data.get("fold", {}).items()):
        raise ValueError(f"language normalization must preserve positions: {path}")
    if not isinstance(data.get("preview_lines", []), list) or any(
            not isinstance(line, str) for line in data.get("preview_lines", [])):
        raise ValueError(f"invalid language preview lines: {path}")
    save = data.get("save", {})
    pattern = save.get("letter_pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise ValueError(f"invalid save letter pattern: {path}")
        re.compile(pattern)
    table = save.get("transliteration", {})
    if not isinstance(table, dict) or any(
            not isinstance(k, str) or len(k) != 1 or not isinstance(v, str)
            for k, v in table.items()):
        raise ValueError(f"invalid save transliteration: {path}")
    pseudo = data.get("pseudo", {})
    if any(not isinstance(v, str) for v in pseudo.values()) or (
            len(pseudo.get("source", "")) != len(pseudo.get("target", ""))):
        raise ValueError(f"invalid pseudo alphabet: {path}")
    buttons = data.get("easy_chat_alphabet", [])
    if not isinstance(buttons, list) or not all(isinstance(b, str) and b for b in buttons):
        raise ValueError(f"invalid easy-chat alphabet buttons: {path}")
    for key in ("abbreviation_left", "abbreviation_right"):
        if key in data.get("wrapping", {}):
            re.compile(data["wrapping"][key])
    if data.get("mixed_scripts"):
        re.compile(data["mixed_scripts"]["pattern"])
        if not isinstance(data["mixed_scripts"].get("message"), str):
            raise ValueError(f"invalid mixed-script message: {path}")
    for field, suffix in (("provider", ".py"), ("agreement", ".py"), ("stand_ins", ".json")):
        if field not in data or field != "stand_ins" and data[field] == "manual":
            continue
        name = data[field]
        if not isinstance(name, str) or (
                not (base / name).resolve().is_relative_to(base.resolve())
                or (base / name).suffix != suffix or not (base / name).is_file()):
            raise ValueError(f"invalid language {field}: {path}")
    return Language(code, data, base)


def default() -> Language:
    """The default language of the registry at or above the working directory."""
    from ..project import REGISTRY, find_registry
    return registry_default(find_registry() / REGISTRY)


@lru_cache(maxsize=None)
def registry_default(registry: Path) -> Language:
    if not registry.is_file():
        return Language("", {}, ROOT)
    with registry.open("rb") as stream:
        code = tomllib.load(stream).get("defaults", {}).get("language")
    return load(code) if code else Language("", {}, ROOT)


def current() -> Language:
    return ACTIVE.get() or default()


@contextmanager
def use(selected: str | Language):
    token = ACTIVE.set(load(selected) if isinstance(selected, str) else selected)
    try:
        yield ACTIVE.get()
    finally:
        ACTIVE.reset(token)


def selected(method):
    """Scope checker calls without leaking between documents."""
    @wraps(method)
    def call(self, *args, **kwargs):
        with use(self.language):
            return method(self, *args, **kwargs)
    return call


def profile_selected(function):
    """Honor an effective profile outside CLI activation."""
    parameter = next(iter(inspect.signature(function).parameters))
    @wraps(function)
    def call(*args, **kwargs):
        profile = args[0] if args else kwargs[parameter]
        with use(getattr(profile, "language", None) or current()):
            return function(*args, **kwargs)
    return call


def code_selected(function):
    """Honor a builder's explicit language argument."""
    parameter = next(iter(inspect.signature(function).parameters))
    @wraps(function)
    def call(*args, **kwargs):
        code = args[0] if args else kwargs[parameter]
        with use(code):
            return function(*args, **kwargs)
    return call
