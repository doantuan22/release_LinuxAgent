"""One TTY decision shared by confirmation and privilege authentication."""

from __future__ import annotations

import sys


def has_interactive_tty() -> bool:
    return sys.stdin.isatty()
