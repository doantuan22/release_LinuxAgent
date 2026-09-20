"""Narrow root entry point for the allowlisted, backup-first config edit tool.

Only the new file content is read from stdin. The helper never reads a sudo
password; its parent invokes it with ``sudo -n`` after authentication.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    if os.geteuid() != 0 or len(sys.argv) != 2:
        return 2

    from agent.tools.tier2_actions import edit_config_file

    result = edit_config_file(sys.argv[1], sys.stdin.read())
    print(json.dumps({"ok": result.ok, "data": result.data, "error": result.error}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
