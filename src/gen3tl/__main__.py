"""Command line entry point: the gen3tl command, or python3 -m gen3tl <group> <command>."""

import sys

from .cli.main import main


def run():
    """Exit with the reported command status."""
    sys.exit(main())


if __name__ == "__main__":
    run()
