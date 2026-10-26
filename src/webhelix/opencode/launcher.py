"""Host-only dispatcher from native AutoHelix to its prepared isolated session."""

import json
import os
from pathlib import Path
import sys


def main():
    manifest = Path.cwd() / ".autohelix/opencode-session.json"
    if not manifest.is_file():
        raise SystemExit(
            "OpenCode requires a prepared WebHelix session; use checkout experiment/demo"
        )
    command = Path(json.loads(manifest.read_text())["command"])
    if not command.is_absolute() or not command.is_file():
        raise SystemExit("Prepared OpenCode session launcher is unavailable")
    os.execv(str(command), [str(command), *sys.argv[1:]])


if __name__ == "__main__":
    main()
