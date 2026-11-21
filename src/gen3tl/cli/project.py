"""Translation project layout and status."""

from __future__ import annotations

from pathlib import Path

from ..profile.profile import Profile
from .common import add, command, line


@command("project init")
def project_init(args):
    """Lay out the game project of a translation-stage profile: project.toml, source/ and the folders."""
    from ..catalog.layout import init
    from ..project import ProjectError, Registry
    registry = Registry.load(Path(args.project_root) if args.project_root else None)
    prof = Profile.by_name(args.profile)
    root = Path(args.root) if args.root else registry.root
    try:
        rom = None if args.no_rom else prof.load_rom(args.rom)
        laid = init(prof, root, registry, rom)
    except (ProjectError, OSError, ValueError) as exc:
        line("error", exc)
        return 1
    for path in laid.written:
        line("wrote", path)
    for path in laid.kept:
        line("kept", path)
    for path in laid.profile_files:
        line("profile", path)
    line("notes", f"{laid.notes} notes moved")
    line("graphics", f"{laid.graphics} graphics moved")
    if args.no_rom:
        line("catalog", "skipped without the ROM")
    else:
        line("entries", f"{laid.entries} entries")
    return 0


def register(sub) -> None:
    pi = add(sub, "init", project_init)
    pi.add_argument("--root", default=None, help="project root (default: the directory of projects.toml)")
    pi.add_argument("--no-rom", action="store_true", help="keep the existing build/catalog.pot")
