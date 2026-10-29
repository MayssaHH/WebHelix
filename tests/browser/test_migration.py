"""Guard the single browser runtime and prevent reuse of incompatible evidence."""

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from webhelix.browser.manifest import runtime_identity


ROOT = Path(__file__).resolve().parents[2]


def test_locked_environment_has_one_browser_agent_runtime():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dependencies = project["project"]["dependencies"]
    assert "browser-use==0.13.14" in dependencies
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    names = {package["name"] for package in lock["package"]}
    assert not any(name.startswith("browsergym") for name in names)
    assert "gymnasium" not in names
    assert importlib.util.find_spec("browsergym") is None
    assert runtime_identity()["backend"] == "browser-use"


def test_supported_sources_do_not_import_removed_runtime():
    paths = list((ROOT / "src").rglob("*.py"))
    paths += list((ROOT / "scripts").glob("*.py"))
    paths += list((ROOT / "benchmarks").rglob("*.py"))
    stale = []
    for path in paths:
        if any(part in {".venv", "__pycache__"} for part in path.parts):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            if any(
                name.startswith(("browsergym", "webhelix.policy")) for name in modules
            ):
                stale.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not stale, stale


@pytest.mark.parametrize("command", [[], ["checkout"], ["reservation"], ["benchmark"]])
def test_supported_cli_imports_without_browsergym(command):
    result = subprocess.run(
        [sys.executable, "-m", "webhelix.cli", *command, "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_cache_key_changes_when_browser_runtime_changes(monkeypatch, tmp_path):
    from webhelix import bridge

    raw = b'{"candidate":"same bytes"}'
    monkeypatch.setattr(bridge, "runtime_identity", lambda: {"version": "before"})
    before = bridge.cache_key(tmp_path, raw)
    monkeypatch.setattr(bridge, "runtime_identity", lambda: {"version": "after"})
    assert bridge.cache_key(tmp_path, raw) != before
