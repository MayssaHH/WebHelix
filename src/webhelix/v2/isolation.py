"""Bubblewrap launcher. Real search fails closed if its isolation probe fails."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from .contract import ROOT


def codex_binary():
    binary = os.environ.get("WEBHELIX_CODEX_BINARY") or shutil.which("codex")
    if not binary:
        raise RuntimeError("Codex CLI missing; install it or set WEBHELIX_CODEX_BINARY")
    return str(Path(binary).resolve())


def command(argv, workspace):
    workspace = Path(workspace).resolve()
    runtime = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
    if not shutil.which("bwrap"):
        raise RuntimeError("Bubblewrap is required for real attacker isolation")
    args = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--ro-bind",
        "/",
        "/",
        "--dev-bind",
        "/dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(workspace),
        str(workspace),
        "--bind",
        str(runtime),
        str(runtime),
    ]
    # The victim key lives only in the parent evaluator. Conceal its file, and
    # clear all inherited credentials except Codex's own established login route.
    for path in [ROOT / ".env"]:
        if path.exists():
            args += ["--ro-bind", "/dev/null", str(path)]
    args += ["--clearenv"]
    allowed = {
        "PATH",
        "HOME",
        "CODEX_HOME",
        "LANG",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "TERM",
    }
    for key, value in os.environ.items():
        if key in allowed or key.lower() in {
            "https_proxy",
            "http_proxy",
            "all_proxy",
            "no_proxy",
        }:
            args += ["--setenv", key, value]
    args += ["--setenv", "TMPDIR", "/tmp", "--chdir", str(workspace), "--", *argv]
    return args


def main():
    if sys.argv[1:2] == ["probe"]:
        workspace = Path(sys.argv[2]).resolve()
        protected = Path(sys.argv[3]).resolve()
        code = """from pathlib import Path
import os,sys
assert not os.environ.get("WEBHELIX_API_KEY") and not os.environ.get("OPENAI_API_KEY")
try:
    assert not Path(sys.argv[2]).read_text() if Path(sys.argv[2]).exists() else True
except PermissionError: pass
try: Path(sys.argv[1]).write_text("tampered")
except PermissionError: pass
except OSError as e: assert e.errno==30
else: raise AssertionError("protected write allowed")
Path("isolation-probe.txt").write_text("passed")
print("protected writes denied; victim credential absent; workspace writable")
"""
        result = subprocess.run(
            command(
                [sys.executable, "-c", code, str(protected), str(ROOT / ".env")],
                workspace,
            ),
            capture_output=True,
            text=True,
        )
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    os.execvp("bwrap", command([codex_binary(), *sys.argv[1:]], Path.cwd()))


if __name__ == "__main__":
    main()
