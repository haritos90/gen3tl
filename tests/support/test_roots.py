"""Filesystem roots for tests and support helpers."""
import os
from pathlib import Path

from gen3tl import TOOLKIT_ROOT

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(os.environ.get("GEN3TL_UNBOUND_PROJECT", REPOSITORY_ROOT))
TEST_ROOT = Path(__file__).resolve().parents[1]
SUPPORT_ROOT = TEST_ROOT / "support"
